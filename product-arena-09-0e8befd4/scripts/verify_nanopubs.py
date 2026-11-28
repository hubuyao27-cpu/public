#!/usr/bin/env python3
"""Step 2: Verify, normalize, deduplicate, and compute Trusty IDs for Nanopublications.

Runs 10 deterministic quality gates over extracted/candidate Nanopublications against
ingest/source_index.json and schema/predicate_vocab.json:
  1. ATOMICITY (word/CJK count, compound clause splits, multi-metric bundling)
  2. EVIDENCE_GROUNDING & MINIMAL_SPAN (verbatim span match, char offsets, minimal size)
  3. LOCATOR_INTEGRITY (no fake PDF pages, valid paragraph/sentence/page bounds, cross-modal flags)
  4. PREDICATE_AND_DIRECTION (controlled vocab mapping, passive-voice inversion check, entity grounding)
  5. NUMERIC_NEGATION_FIDELITY (numbers present in evidence, negation polarity agreement)
  6. EPISTEMIC_CONSISTENCY (cited prior work & hedging vs author_direct_finding + direct_empirical)
  7. PROVENANCE_AUTHENTICITY (reconciles metadata & sha256 against source_index)
  8. CONFLICT_DETECTION (opposite predicates or polarities for same subject/object pair)
  9. DEDUP_AND_CORROBORATION_MERGE (merges duplicate assertions while preserving all evidence spans)
 10. TRUSTY_ID_AND_CONFIDENCE_RUBRIC (deterministic confidence score & urn:nanopub:RA... Trusty ID)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    compute_dedup_fingerprint,
    compute_trusty_nanopub_id,
    extract_negation_hedging,
    extract_numeric_tokens,
    find_verbatim_span,
    load_config,
    load_json,
    load_predicate_vocab,
    norm_doi,
    norm_key,
    norm_nfkc,
    norm_ws,
    normalize_numeric_value,
    save_json,
    split_sentences,
    token_jaccard,
)


def _build_vocab_lookup(vocab: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    syn_map: Dict[str, str] = {}
    for entry in vocab.get("predicates") or []:
        pid = entry["id"]
        by_id[pid] = entry
        syn_map[norm_key(pid)] = pid
        for syn in entry.get("synonyms") or []:
            syn_map[norm_key(syn)] = pid
    return by_id, syn_map


def _index_source_docs(source_index: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    docs: Dict[str, Dict[str, Any]] = {}
    for d in source_index.get("documents") or []:
        p_map = {str(p["paragraph_id"]): p for p in d.get("paragraphs") or []}
        s_map = {str(s["sentence_id"]): s for s in d.get("sentences") or []}
        docs[d["source_id"]] = {
            "doc": d,
            "paragraphs": p_map,
            "sentences": s_map,
            "full_text": "\n".join(p["text"] for p in d.get("paragraphs") or []),
        }
    return docs


def _entity_in_text(entity: Dict[str, Any], text: str) -> bool:
    if not entity or not text:
        return False
    t_norm = norm_nfkc(text).lower()
    t_key = norm_key(text)
    candidates = [entity.get("label") or ""] + list(entity.get("aliases") or [])
    for cand in candidates:
        c = norm_ws(str(cand))
        if not c:
            continue
        if c.lower() in t_norm:
            return True
        ck = norm_key(c)
        if ck and ck in t_key:
            return True
        # Token overlap fallback for slight inflection/parenthetical abbreviation
        words = [w for w in re.findall(r"[a-z0-9\u4e00-\u9fff]+", c.lower()) if len(w) >= 3]
        if words and all(w in t_norm for w in words):
            return True
    return False


def _add_issue(
    issues: List[Dict[str, Any]],
    severity: str,
    gate: str,
    code: str,
    item_id: str,
    message: str,
    hint: str = "",
) -> None:
    issues.append({
        "severity": severity,
        "gate": gate,
        "code": code,
        "item_id": item_id,
        "message": message,
        "fix_hint": hint,
    })


def _normalize_raw_items(raw_data: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    if isinstance(raw_data, list):
        return raw_data, [], []
    if isinstance(raw_data, dict):
        items = (
            raw_data.get("nanopublications")
            or raw_data.get("assertions")
            or raw_data.get("claims")
            or raw_data.get("items")
            or []
        )
        conflicts = list(raw_data.get("conflicts") or [])
        uncertainty = list(raw_data.get("uncertainty") or [])
        return list(items), conflicts, uncertainty
    return [], [], []


def verify_nanopubs(
    raw_data: Any,
    source_index: Dict[str, Any],
    cfg: Optional[Dict[str, Any]] = None,
    vocab: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    cfg = cfg or load_config()
    vocab = vocab or load_predicate_vocab()
    by_id, syn_map = _build_vocab_lookup(vocab)
    docs_idx = _index_source_docs(source_index)

    raw_items, pre_conflicts, pre_uncertainty = _normalize_raw_items(raw_data)
    issues: List[Dict[str, Any]] = []
    change_records: List[Dict[str, Any]] = []
    audited_items: List[Dict[str, Any]] = []

    atom_cfg = cfg.get("atomicity") or {}
    span_cfg = cfg.get("evidence_minimality") or {}
    dedup_cfg = cfg.get("deduplication") or {}
    rubric = cfg.get("confidence_rubric") or {}
    ser_cfg = cfg.get("serialization") or {}

    max_words = int(atom_cfg.get("max_words_per_claim", 45))
    max_cjk = int(atom_cfg.get("max_cjk_chars_per_claim", 90))
    max_metrics = int(atom_cfg.get("max_independent_metrics_per_claim", 2))
    cues_en = atom_cfg.get("compound_split_cues_en") or []
    cues_zh = atom_cfg.get("compound_split_cues_zh") or []

    max_sents = int(span_cfg.get("max_sentences_without_justification", 2))
    max_chars = int(span_cfg.get("max_chars_without_justification", 450))
    max_cov = float(span_cfg.get("max_paragraph_coverage_ratio", 0.85))
    min_p_for_cov = int(span_cfg.get("min_paragraph_chars_for_ratio_check", 450))

    default_source_id = next(iter(docs_idx.keys()), None)

    for idx, raw in enumerate(raw_items, 1):
        temp_id = str(raw.get("temp_id") or raw.get("id") or raw.get("nanopub_id") or f"NP_{idx:03d}")
        # Support either flat dict or nested 4-graph dict
        assertion_in = dict(raw.get("assertion") if isinstance(raw.get("assertion"), dict) else raw)
        prov_in = dict(raw.get("provenance") if isinstance(raw.get("provenance"), dict) else {})

        claim_text = norm_ws(assertion_in.get("claim_text") or assertion_in.get("claim") or "")
        subj = assertion_in.get("subject")
        if isinstance(subj, str):
            subj = {"label": subj, "entity_type": "Material", "aliases": [], "identifier": None}
        elif not isinstance(subj, dict):
            subj = {"label": "", "entity_type": "Material", "aliases": [], "identifier": None}
        else:
            subj = {
                "label": norm_ws(subj.get("label") or subj.get("name") or ""),
                "entity_type": subj.get("entity_type") or "Material",
                "aliases": list(subj.get("aliases") or []),
                "identifier": subj.get("identifier"),
            }

        obj = assertion_in.get("object")
        if isinstance(obj, str):
            obj = {"label": obj, "entity_type": "Property_Or_Metric", "aliases": [], "identifier": None}
        elif not isinstance(obj, dict):
            obj = {"label": "", "entity_type": "Property_Or_Metric", "aliases": [], "identifier": None}
        else:
            obj = {
                "label": norm_ws(obj.get("label") or obj.get("name") or ""),
                "entity_type": obj.get("entity_type") or "Property_Or_Metric",
                "aliases": list(obj.get("aliases") or []),
                "identifier": obj.get("identifier"),
            }

        pred_raw = str(assertion_in.get("predicate") or "").strip()
        cond_in = assertion_in.get("conditions")
        if isinstance(cond_in, str):
            cond = {
                "experimental_system": None,
                "parameters": [cond_in] if norm_ws(cond_in) else [],
                "comparator": None,
                "scope_qualifiers": [],
            }
        elif isinstance(cond_in, dict):
            cond = {
                "experimental_system": cond_in.get("experimental_system"),
                "parameters": [norm_ws(str(x)) for x in (cond_in.get("parameters") or []) if norm_ws(str(x))],
                "comparator": cond_in.get("comparator"),
                "scope_qualifiers": [norm_ws(str(x)) for x in (cond_in.get("scope_qualifiers") or []) if norm_ws(str(x))],
            }
        else:
            cond = {"experimental_system": None, "parameters": [], "comparator": None, "scope_qualifiers": []}

        qvals = list(assertion_in.get("quantitative_values") or [])
        polarity = str(assertion_in.get("polarity") or "positive").strip()
        epistemic = str(assertion_in.get("epistemic_status") or "author_direct_finding").strip()

        # ---------------- GATE 1: ATOMICITY ----------------
        if not claim_text:
            _add_issue(issues, "ERROR", "ATOMICITY", "empty_claim_text", temp_id, "Missing assertion.claim_text.", "Provide one concise single-claim sentence.")
        else:
            words = re.findall(r"[A-Za-z0-9_\-]+", claim_text)
            cjk_count = len(re.findall(r"[\u4e00-\u9fff]", claim_text))
            if len(words) > max_words or cjk_count > max_cjk:
                _add_issue(
                    issues,
                    "ERROR",
                    "ATOMICITY",
                    "claim_too_long_or_bundled",
                    temp_id,
                    f"Claim length ({len(words)} words, {cjk_count} CJK chars) exceeds atomic threshold ({max_words} words / {max_cjk} CJK chars).",
                    "Split paragraph-level summary into separate atomic assertions.",
                )
            low_claim = claim_text.lower()
            hit_cues = [c for c in cues_en if c.lower() in low_claim] + [c for c in cues_zh if c in claim_text]
            if hit_cues:
                _add_issue(
                    issues,
                    "ERROR",
                    "ATOMICITY",
                    "compound_clause_detected",
                    temp_id,
                    f"Claim contains compound multi-assertion cue(s): {hit_cues}.",
                    "Split parallel/contrastive/additive clauses into independent Nanopublications.",
                )
            if len(qvals) > max_metrics:
                _add_issue(
                    issues,
                    "ERROR",
                    "ATOMICITY",
                    "multiple_metrics_bundled",
                    temp_id,
                    f"Assertion bundles {len(qvals)} quantitative values (max {max_metrics} per atomic claim).",
                    "Create separate assertions per independent outcome metric.",
                )

        if not subj["label"] or not obj["label"]:
            _add_issue(
                issues,
                "ERROR",
                "ATOMICITY",
                "missing_subject_or_object",
                temp_id,
                "Assertion requires non-empty subject.label and object.label.",
                "Specify exact semantic subject and object labels.",
            )

        # ---------------- GATE 4a: PREDICATE CONTROLLED VOCAB ----------------
        pred_norm = syn_map.get(norm_key(pred_raw))
        if not pred_norm:
            _add_issue(
                issues,
                "ERROR",
                "PREDICATE_AND_DIRECTION",
                "unknown_predicate",
                temp_id,
                f"Predicate `{pred_raw}` is not in controlled vocabulary (predicate_vocab.json).",
                f"Map `{pred_raw}` to one of the 24 canonical predicates.",
            )
            pred_canonical = pred_raw or "associated_with"
            pred_meta = {}
        else:
            pred_canonical = pred_norm
            pred_meta = by_id[pred_canonical]
            if pred_raw != pred_canonical:
                change_records.append({
                    "item_id": temp_id,
                    "field": "assertion.predicate",
                    "before": pred_raw,
                    "after": pred_canonical,
                    "reason": "Mapped synonym to canonical controlled predicate URI.",
                })

        if pred_meta.get("requires_conditions") and not (
            cond["experimental_system"] or cond["parameters"] or cond["comparator"] or cond["scope_qualifiers"]
        ):
            _add_issue(
                issues,
                "WARNING",
                "ATOMICITY",
                "missing_conditions_for_conditional_predicate",
                temp_id,
                f"Predicate `{pred_canonical}` usually requires experimental/scope conditions, but conditions are empty.",
                "Extract system, parameters, comparator, or scope qualifiers from evidence.",
            )

        # ---------------- GATE 2 & 3: EVIDENCE GROUNDING, MINIMAL SPAN & LOCATORS ----------------
        ev_raw = prov_in.get("evidence_spans") or raw.get("evidence_spans") or []
        if not ev_raw and (raw.get("quote") or prov_in.get("quote")):
            ev_raw = [{
                "source_id": raw.get("source_id") or default_source_id,
                "quote": raw.get("quote") or prov_in.get("quote"),
                "evidence_type": raw.get("evidence_type") or "direct_empirical",
                "locator": raw.get("locator") or prov_in.get("locator") or {},
            }]

        if not ev_raw:
            _add_issue(
                issues,
                "ERROR",
                "EVIDENCE_GROUNDING",
                "missing_evidence_spans",
                temp_id,
                "Nanopublication has zero evidence_spans.",
                "Bind at least one minimal verbatim evidence span with locator.",
            )

        verified_spans: List[Dict[str, Any]] = []
        matched_span_count = 0
        exact_match_count = 0
        combined_quote_parts: List[str] = []
        primary_sid = None

        for sp_idx, sp in enumerate(ev_raw, 1):
            sp_dict = dict(sp) if isinstance(sp, dict) else {}
            sid = str(sp_dict.get("source_id") or raw.get("source_id") or default_source_id or "")
            if not primary_sid and sid:
                primary_sid = sid
            quote = str(sp_dict.get("quote") or "").strip()
            ev_type = str(sp_dict.get("evidence_type") or "direct_empirical")
            loc = dict(sp_dict.get("locator") or {})
            justif = sp_dict.get("multi_sentence_justification")

            doc_bundle = docs_idx.get(sid)
            if not doc_bundle:
                _add_issue(
                    issues,
                    "ERROR",
                    "LOCATOR_INTEGRITY",
                    "unknown_source_id",
                    temp_id,
                    f"Evidence span #{sp_idx} cites unknown source_id `{sid}`.",
                    f"Use valid source_id from source_index.json: {list(docs_idx.keys())}.",
                )
                verified_spans.append(sp_dict)
                continue

            doc_obj = doc_bundle["doc"]
            has_pdf = bool(doc_obj.get("has_pdf_pages"))
            loc_page = loc.get("page")
            loc_type = loc.get("locator_type")

            # Gate 3: Zero fake PDF page numbers
            if not has_pdf and (loc_page is not None or loc_type == "pdf_page"):
                _add_issue(
                    issues,
                    "ERROR",
                    "LOCATOR_INTEGRITY",
                    "fake_pdf_page_number",
                    temp_id,
                    f"Evidence span #{sp_idx} specifies PDF page={loc_page} / locator_type={loc_type} on non-PDF source `{sid}` (has_pdf_pages=false).",
                    "Use authentic paragraph_id / sentence_id / line_range locators instead of synthetic page numbers.",
                )
            elif has_pdf and loc_page is not None:
                pc = doc_obj.get("page_count") or 10**6
                if not isinstance(loc_page, int) or loc_page < 1 or loc_page > pc:
                    _add_issue(
                        issues,
                        "ERROR",
                        "LOCATOR_INTEGRITY",
                        "page_out_of_bounds",
                        temp_id,
                        f"Evidence span #{sp_idx} cites page={loc_page}, outside [1, {pc}].",
                        "Fix page number against source_digest.md.",
                    )

            # Locate target paragraph / sentence text
            pid = loc.get("paragraph_id")
            sent_id = loc.get("sentence_id")
            haystack = ""
            target_para: Optional[Dict[str, Any]] = None

            if pid and str(pid) in doc_bundle["paragraphs"]:
                target_para = doc_bundle["paragraphs"][str(pid)]
                haystack = target_para["text"]
            elif sent_id and str(sent_id) in doc_bundle["sentences"]:
                srec = doc_bundle["sentences"][str(sent_id)]
                pid = srec["paragraph_id"]
                target_para = doc_bundle["paragraphs"].get(str(pid))
                haystack = target_para["text"] if target_para else srec["text"]
            else:
                # Search across all paragraphs of this source
                if pid:
                    _add_issue(
                        issues,
                        "WARNING",
                        "LOCATOR_INTEGRITY",
                        "paragraph_id_not_found_fallback_search",
                        temp_id,
                        f"paragraph_id `{pid}` not found in `{sid}`; searching full document.",
                        "Use exact paragraph_id from source_digest.md.",
                    )
                for p_cand in doc_obj.get("paragraphs") or []:
                    if has_pdf and loc_page is not None and p_cand.get("page") != loc_page:
                        continue
                    res_cand = find_verbatim_span(quote, p_cand["text"])
                    if res_cand["matched"]:
                        target_para = p_cand
                        haystack = p_cand["text"]
                        pid = p_cand["paragraph_id"]
                        break
                if not haystack:
                    haystack = doc_bundle["full_text"]

            # Gate 2: Verbatim match verification
            m_res = find_verbatim_span(quote, haystack)
            if not m_res["matched"]:
                # Also check across full source if paragraph scoping missed
                m_full = find_verbatim_span(quote, doc_bundle["full_text"]) if haystack != doc_bundle["full_text"] else m_res
                if m_full["matched"]:
                    _add_issue(
                        issues,
                        "ERROR",
                        "LOCATOR_INTEGRITY",
                        "quote_in_wrong_paragraph_locator",
                        temp_id,
                        f"Quote exists in `{sid}`, but NOT inside cited paragraph `{pid}`.",
                        "Update locator.paragraph_id / page to the paragraph where the quote actually appears.",
                    )
                else:
                    _add_issue(
                        issues,
                        "ERROR",
                        "EVIDENCE_GROUNDING",
                        "quote_not_grounded_in_source",
                        temp_id,
                        f"Evidence quote not found verbatim in `{sid}` (best token similarity={m_res['best_similarity']}). Quote: `{quote[:120]}`",
                        "Copy an exact verbatim excerpt directly from source_digest.md.",
                    )
            else:
                matched_span_count += 1
                if m_res["match_mode"] == "exact":
                    exact_match_count += 1
                combined_quote_parts.append(quote)
                # Populate precise char offsets & missing locator fields
                if m_res["char_start"] is not None:
                    loc["char_start"] = m_res["char_start"]
                    loc["char_end"] = m_res["char_end"]
                if target_para:
                    if not loc.get("paragraph_id"):
                        loc["paragraph_id"] = target_para["paragraph_id"]
                    if not loc.get("section"):
                        loc["section"] = target_para.get("section")
                    if not loc.get("locator_type"):
                        loc["locator_type"] = target_para.get("locator_type") or ("pdf_page" if has_pdf else "section_paragraph")
                    if has_pdf and loc.get("page") is None and target_para.get("page") is not None:
                        loc["page"] = target_para.get("page")
                    if not loc.get("sentence_id") and target_para.get("sentences"):
                        # Find first overlapping sentence
                        cs, ce = m_res["char_start"], m_res["char_end"]
                        for srec in target_para["sentences"]:
                            ss, se = srec.get("char_start"), srec.get("char_end")
                            if cs is not None and ss is not None and se is not None and not (ce <= ss or cs >= se):
                                loc["sentence_id"] = srec["sentence_id"]
                                break

            # Gate 2b: Minimal sufficient span check
            q_sents = split_sentences(quote)
            if not justif:
                if len(q_sents) > max_sents or len(quote) > max_chars:
                    _add_issue(
                        issues,
                        "ERROR",
                        "MINIMAL_SPAN",
                        "evidence_span_too_long",
                        temp_id,
                        f"Evidence span #{sp_idx} has {len(q_sents)} sentences / {len(quote)} chars (limits: {max_sents} sentences, {max_chars} chars without multi_sentence_justification).",
                        "Trim quote to the minimal 1-2 sentences supporting this assertion, or supply multi_sentence_justification.",
                    )
                elif target_para and len(target_para["text"]) >= min_p_for_cov:
                    ratio = len(quote) / max(1, len(target_para["text"]))
                    if ratio > max_cov:
                        _add_issue(
                            issues,
                            "WARNING",
                            "MINIMAL_SPAN",
                            "evidence_covers_nearly_full_paragraph",
                            temp_id,
                            f"Evidence span #{sp_idx} covers {ratio:.0%} of long paragraph `{pid}`.",
                            "Narrow quote to the minimal supporting sentence(s).",
                        )

            # Gate 3b: Cross-figure claim check
            if re.search(r"\b(?:Fig\.?|Figure)\s*\d+", claim_text, re.I) and ev_type != "table_or_caption_direct":
                if not re.search(r"\b(?:Fig\.?|Figure)\s*\d+", quote, re.I) and not target_para.get("figure_id") if target_para else True:
                    _add_issue(
                        issues,
                        "WARNING",
                        "LOCATOR_INTEGRITY",
                        "cross_figure_reference_check",
                        temp_id,
                        "Assertion references a Figure; ensure value comes from explicit text/caption/table rather than visual pixel estimation.",
                        "Cite exact caption/results sentence or mark uncertainty if reading curve values visually.",
                    )

            verified_spans.append({
                "source_id": sid,
                "quote": quote,
                "evidence_type": ev_type,
                "locator": loc,
                "multi_sentence_justification": justif,
                "ocr_confidence": sp_dict.get("ocr_confidence"),
            })

        combined_quote = " ".join(combined_quote_parts)

        # ---------------- GATE 4b: PASSIVE-VOICE DIRECTION & ENTITY GROUNDING ----------------
        if combined_quote:
            passive_markers = pred_meta.get("passive_markers") or []
            q_low = norm_nfkc(combined_quote).lower()
            subj_low = norm_nfkc(subj["label"]).lower()
            obj_low = norm_nfkc(obj["label"]).lower()
            for pm in passive_markers:
                pm_low = pm.lower()
                if pm_low in q_low and subj_low and obj_low:
                    # Check if quote reads "<subj> ... <passive_marker> ... <obj>" -> subject/object likely swapped!
                    s_pos = q_low.find(subj_low)
                    pm_pos = q_low.find(pm_low)
                    o_pos = q_low.find(obj_low)
                    if 0 <= s_pos < pm_pos < o_pos:
                        _add_issue(
                            issues,
                            "ERROR",
                            "PREDICATE_AND_DIRECTION",
                            "likely_subject_object_inversion",
                            temp_id,
                            f"Quote contains passive marker `{pm}` with `{subj['label']}` BEFORE `{obj['label']}`. In `{pred_canonical}`, grammatical passive subject is typically the semantic object!",
                            f"Verify causal/relation direction and swap subject/object if `{obj['label']}` actually acts on `{subj['label']}`.",
                        )
                        break

            if subj["label"] and not _entity_in_text(subj, combined_quote):
                _add_issue(
                    issues,
                    "WARNING",
                    "PREDICATE_AND_DIRECTION",
                    "subject_not_explicit_in_quote",
                    temp_id,
                    f"Subject `{subj['label']}` (and aliases) not literally matched inside bound quote.",
                    "Confirm pronoun/coreference resolution or include explicit alias/antecedent sentence.",
                )
            if obj["label"] and not _entity_in_text(obj, combined_quote):
                _add_issue(
                    issues,
                    "WARNING",
                    "PREDICATE_AND_DIRECTION",
                    "object_not_explicit_in_quote",
                    temp_id,
                    f"Object `{obj['label']}` (and aliases) not literally matched inside bound quote.",
                    "Confirm object wording matches bound evidence span.",
                )

        # ---------------- GATE 5: NUMERIC & NEGATION FIDELITY ----------------
        if combined_quote:
            quote_nums = {normalize_numeric_value(x) for x in extract_numeric_tokens(combined_quote)}
            # Check claim numbers & quantitative_values
            claim_nums = [normalize_numeric_value(x) for x in extract_numeric_tokens(claim_text)]
            for qv in qvals:
                if isinstance(qv, dict) and qv.get("value") is not None:
                    for x in extract_numeric_tokens(str(qv["value"])):
                        claim_nums.append(normalize_numeric_value(x))
            missing_nums = [n for n in dict.fromkeys(claim_nums) if n not in quote_nums]
            if missing_nums:
                _add_issue(
                    issues,
                    "ERROR",
                    "NUMERIC_NEGATION_FIDELITY",
                    "ungrounded_numeric_value",
                    temp_id,
                    f"Numeric value(s) {missing_nums} appear in assertion/quantitative_values but NOT in bound evidence quote.",
                    "Verify numbers against source text or include the quantitative sentence in evidence_spans.",
                )

            q_signals = extract_negation_hedging(combined_quote)
            c_signals = extract_negation_hedging(claim_text)
            if q_signals["has_negation"] and polarity == "positive" and not c_signals["has_negation"]:
                _add_issue(
                    issues,
                    "ERROR",
                    "NUMERIC_NEGATION_FIDELITY",
                    "negation_polarity_mismatch",
                    temp_id,
                    f"Evidence contains negation cue(s) {q_signals['negation_cues']}, but assertion has polarity='positive' with no negation.",
                    "Set polarity='negative' or 'null_result' and faithfully reflect the negative/null finding in claim_text.",
                )
            if c_signals["has_negation"] and polarity == "positive":
                _add_issue(
                    issues,
                    "WARNING",
                    "NUMERIC_NEGATION_FIDELITY",
                    "negative_claim_labeled_positive",
                    temp_id,
                    f"claim_text contains negation cue(s) {c_signals['negation_cues']} while polarity='positive'.",
                    "Set polarity='negative' or 'null_result'.",
                )

            # ---------------- GATE 6: EPISTEMIC CONSISTENCY ----------------
            primary_ev_types = {sp.get("evidence_type") for sp in verified_spans}
            if epistemic == "author_direct_finding" and "direct_empirical" in primary_ev_types:
                if q_signals["has_citation_markers"] and not re.search(r"\b(?:we|our|this\s+study|here)\b", combined_quote, re.I):
                    _add_issue(
                        issues,
                        "ERROR",
                        "EPISTEMIC_CONSISTENCY",
                        "cited_prior_work_mislabeled_direct_finding",
                        temp_id,
                        f"Evidence contains prior-work citation cue(s) {q_signals['citation_cues']} without author self-reference, but is labeled author_direct_finding + direct_empirical.",
                        "Relabel epistemic_status='cited_prior_work' and evidence_type='cited_prior_work'.",
                    )
                elif q_signals["has_hedging"]:
                    _add_issue(
                        issues,
                        "WARNING",
                        "EPISTEMIC_CONSISTENCY",
                        "hedged_statement_labeled_direct_finding",
                        temp_id,
                        f"Evidence uses hedging cue(s) {q_signals['hedging_cues']}; consider hypothesis_or_speculation or indirect_inference.",
                        "Align epistemic_status and evidence_type with hedged wording.",
                    )

        # ---------------- GATE 7: PROVENANCE AUTHENTICITY ----------------
        doc_meta_ref = (docs_idx.get(primary_sid or "") or {}).get("doc", {}).get("metadata") or {}
        raw_meta = prov_in.get("source_metadata") or {}
        if raw_meta.get("doi") and doc_meta_ref.get("doi") and norm_doi(raw_meta["doi"]) != norm_doi(doc_meta_ref["doi"]):
            _add_issue(
                issues,
                "ERROR",
                "PROVENANCE_AUTHENTICITY",
                "mismatched_source_doi",
                temp_id,
                f"Supplied DOI `{raw_meta['doi']}` contradicts source `{primary_sid}` DOI `{doc_meta_ref['doi']}`.",
                "Use exact bibliographic metadata from source_index.json.",
            )
        if raw_meta.get("year") and doc_meta_ref.get("year") and str(raw_meta["year"]) != str(doc_meta_ref["year"]):
            _add_issue(
                issues,
                "ERROR",
                "PROVENANCE_AUTHENTICITY",
                "mismatched_source_year",
                temp_id,
                f"Supplied year `{raw_meta['year']}` contradicts source `{primary_sid}` year `{doc_meta_ref['year']}`.",
                "Use exact publication year from source_index.json.",
            )

        verified_meta = {
            "source_id": primary_sid or "UNKNOWN",
            "title": doc_meta_ref.get("title") or raw_meta.get("title"),
            "authors": doc_meta_ref.get("authors") or raw_meta.get("authors") or [],
            "year": doc_meta_ref.get("year") or raw_meta.get("year"),
            "venue": doc_meta_ref.get("venue") or raw_meta.get("venue"),
            "version": doc_meta_ref.get("version") or raw_meta.get("version"),
            "doi": doc_meta_ref.get("doi") or raw_meta.get("doi"),
            "pmid": doc_meta_ref.get("pmid") or raw_meta.get("pmid"),
            "arxiv_id": doc_meta_ref.get("arxiv_id") or raw_meta.get("arxiv_id"),
            "url": doc_meta_ref.get("url") or raw_meta.get("url"),
            "source_file": doc_meta_ref.get("source_file") or raw_meta.get("source_file") or "UNKNOWN",
            "file_sha256": doc_meta_ref.get("file_sha256") or raw_meta.get("file_sha256") or "",
        }

        assertion_clean = {
            "claim_text": claim_text,
            "subject": subj,
            "predicate": pred_canonical,
            "predicate_uri": pred_meta.get("uri", ""),
            "object": obj,
            "conditions": cond,
            "quantitative_values": qvals,
            "polarity": polarity,
            "epistemic_status": epistemic,
        }

        audited_items.append({
            "temp_id": temp_id,
            "paper_group_id": (docs_idx.get(primary_sid or "") or {}).get("doc", {}).get("paper_group_id"),
            "assertion": assertion_clean,
            "evidence_spans": verified_spans,
            "source_metadata": verified_meta,
            "matched_span_count": matched_span_count,
            "exact_match_count": exact_match_count,
            "dedup_fingerprint": compute_dedup_fingerprint(assertion_clean),
        })

    # ---------------- GATE 9: DEDUPLICATION & CORROBORATION MERGE ----------------
    merge_enabled = bool(dedup_cfg.get("merge_same_fingerprint", True))
    jacc_thresh = float(dedup_cfg.get("token_jaccard_threshold", 0.82))
    merged_clusters: List[Dict[str, Any]] = []

    for item in audited_items:
        placed = False
        if merge_enabled:
            for cl in merged_clusters:
                same_fp = item["dedup_fingerprint"] == cl["dedup_fingerprint"]
                same_pred_pol = (
                    item["assertion"]["predicate"] == cl["assertion"]["predicate"]
                    and item["assertion"]["polarity"] == cl["assertion"]["polarity"]
                )
                high_sim = same_pred_pol and token_jaccard(item["assertion"]["claim_text"], cl["assertion"]["claim_text"]) >= jacc_thresh
                if same_fp or high_sim:
                    # Merge corroborating evidence spans without losing unique locations
                    existing_keys = {
                        (sp.get("source_id"), (sp.get("locator") or {}).get("paragraph_id"), norm_ws(sp.get("quote")))
                        for sp in cl["evidence_spans"]
                    }
                    for sp in item["evidence_spans"]:
                        k = (sp.get("source_id"), (sp.get("locator") or {}).get("paragraph_id"), norm_ws(sp.get("quote")))
                        if k not in existing_keys:
                            cl["evidence_spans"].append(sp)
                            existing_keys.add(k)
                    # Keep multi-source metadata list
                    if item["source_metadata"]["source_id"] not in {m["source_id"] for m in cl["source_metadata_list"]}:
                        cl["source_metadata_list"].append(item["source_metadata"])
                    cl["merged_from_temp_ids"].append(item["temp_id"])
                    cl["matched_span_count"] += item["matched_span_count"]
                    cl["exact_match_count"] += item["exact_match_count"]
                    change_records.append({
                        "item_id": item["temp_id"],
                        "field": "dedup_merge",
                        "before": item["temp_id"],
                        "after": cl["merged_from_temp_ids"][0],
                        "reason": f"Merged duplicate/corroborating assertion into canonical cluster (fingerprint={cl['dedup_fingerprint']}); preserved all unique evidence spans.",
                    })
                    placed = True
                    break
        if not placed:
            merged_clusters.append({
                "assertion": item["assertion"],
                "evidence_spans": list(item["evidence_spans"]),
                "source_metadata": item["source_metadata"],
                "source_metadata_list": [item["source_metadata"]],
                "merged_from_temp_ids": [item["temp_id"]],
                "matched_span_count": item["matched_span_count"],
                "exact_match_count": item["exact_match_count"],
                "dedup_fingerprint": item["dedup_fingerprint"],
            })

    # ---------------- GATE 10: TRUSTY ID & CONFIDENCE RUBRIC ----------------
    weights = rubric.get("weights") or {}
    ev_scores = rubric.get("evidence_type_scores") or {}
    lvl_thresh = rubric.get("level_thresholds") or {}
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    final_nanopubs: List[Dict[str, Any]] = []
    for cl in merged_clusters:
        spans = cl["evidence_spans"]
        n_spans = max(1, len(spans))
        ev_factor = max((float(ev_scores.get(sp.get("evidence_type"), 0.5)) for sp in spans), default=0.0)
        quote_factor = (cl["exact_match_count"] + 0.85 * max(0, cl["matched_span_count"] - cl["exact_match_count"])) / n_spans
        quote_factor = min(1.0, max(0.0, quote_factor))

        cond = cl["assertion"]["conditions"]
        cond_items = sum(
            1 for x in [cond.get("experimental_system"), cond.get("parameters"), cond.get("comparator"), cond.get("scope_qualifiers")]
            if x
        )
        cond_factor = min(1.0, cond_items / 2.0)

        # Numeric penalty if item had numeric errors
        item_ids = set(cl["merged_from_temp_ids"])
        item_errors = [i for i in issues if i["item_id"] in item_ids and i["severity"] == "ERROR"]
        num_err = any(i["gate"] == "NUMERIC_NEGATION_FIDELITY" for i in item_errors)
        num_factor = 0.0 if num_err else 1.0

        # Corroboration counts distinct paragraphs or distinct paper groups
        distinct_locs = {(sp.get("source_id"), (sp.get("locator") or {}).get("paragraph_id")) for sp in spans}
        corr_factor = min(1.0, (len(distinct_locs) - 1) * 0.5)

        score = (
            float(weights.get("evidence_directness", 0.30)) * ev_factor
            + float(weights.get("quote_match_exactness", 0.25)) * quote_factor
            + float(weights.get("condition_completeness", 0.20)) * cond_factor
            + float(weights.get("numeric_fidelity", 0.15)) * num_factor
            + float(weights.get("corroboration_bonus", 0.10)) * corr_factor
        )
        if cl["matched_span_count"] == 0:
            score = min(score, 0.20)
            level = "unverified"
        elif score >= float(lvl_thresh.get("high", 0.80)) and not item_errors:
            level = "high"
        elif score >= float(lvl_thresh.get("medium", 0.55)):
            level = "medium"
        else:
            level = "low"

        score = round(score, 3)
        rationale = (
            f"directness={ev_factor:.2f}, quote_match={quote_factor:.2f}, "
            f"conditions={cond_factor:.2f}, numeric_fidelity={num_factor:.2f}, "
            f"corroboration_locs={len(distinct_locs)}"
        )

        prov_core = {
            "evidence_spans": [
                {
                    "source_id": sp.get("source_id"),
                    "quote": sp.get("quote"),
                    "evidence_type": sp.get("evidence_type"),
                    "locator": sp.get("locator"),
                }
                for sp in spans
            ],
            "source_sha256": cl["source_metadata"].get("file_sha256"),
        }
        np_id = compute_trusty_nanopub_id(cl["assertion"], prov_core)
        base_iri = ser_cfg.get("base_iri", "https://w3id.org/np/arena/")
        short_hash = np_id.replace("urn:nanopub:", "")
        np_uri = f"{base_iri}{short_hash}"

        final_nanopubs.append({
            "nanopub_id": np_id,
            "head": {
                "nanopub_uri": np_uri,
                "assertion_graph": f"{np_uri}#assertion",
                "provenance_graph": f"{np_uri}#provenance",
                "pubinfo_graph": f"{np_uri}#pubinfo",
            },
            "assertion": cl["assertion"],
            "provenance": {
                "evidence_spans": spans,
                "source_metadata": cl["source_metadata"],
                "source_metadata_list": cl["source_metadata_list"],
                "confidence": {
                    "level": level,
                    "score": score,
                    "rationale": rationale,
                    "factors": {
                        "evidence_directness": round(ev_factor, 3),
                        "quote_match_exactness": round(quote_factor, 3),
                        "condition_completeness": round(cond_factor, 3),
                        "numeric_fidelity": round(num_factor, 3),
                        "corroboration_bonus": round(corr_factor, 3),
                    },
                },
            },
            "pubinfo": {
                "nanopub_id": np_id,
                "dedup_fingerprint": cl["dedup_fingerprint"],
                "created_at": now_iso,
                "created_by": ser_cfg.get("created_by", "nanopublication-extractor/1.0.0"),
                "schema_version": ser_cfg.get("schema_version", "nanopub-skill/1.0.0"),
                "license": ser_cfg.get("default_license", "https://creativecommons.org/licenses/by/4.0/"),
                "corroboration_count": len(distinct_locs),
                "merged_from_temp_ids": cl["merged_from_temp_ids"],
            },
        })

    # ---------------- GATE 8: CONFLICT DETECTION ----------------
    conflicts: List[Dict[str, Any]] = list(pre_conflicts)
    seen_pairs = {tuple(sorted(c.get("nanopub_ids") or [])) for c in conflicts}

    for i in range(len(final_nanopubs)):
        a1 = final_nanopubs[i]["assertion"]
        s1 = norm_key(a1["subject"]["label"])
        o1 = norm_key(a1["object"]["label"])
        p1 = a1["predicate"]
        opps1 = set((by_id.get(p1) or {}).get("opposite_predicates") or [])
        for j in range(i + 1, len(final_nanopubs)):
            a2 = final_nanopubs[j]["assertion"]
            s2 = norm_key(a2["subject"]["label"])
            o2 = norm_key(a2["object"]["label"])
            if s1 != s2 or o1 != o2:
                continue
            p2 = a2["predicate"]
            conflict_type = None
            if p2 in opps1:
                conflict_type = "opposite_predicate"
            elif p1 == p2 and {a1["polarity"], a2["polarity"]} == {"positive", "negative"}:
                conflict_type = "polarity_conflict"
            elif p1 == p2 and {a1["polarity"], a2["polarity"]} == {"positive", "null_result"}:
                conflict_type = "polarity_conflict"

            if conflict_type:
                pair = tuple(sorted([final_nanopubs[i]["nanopub_id"], final_nanopubs[j]["nanopub_id"]]))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    c1 = json.dumps(a1["conditions"], ensure_ascii=False, sort_keys=True)
                    c2 = json.dumps(a2["conditions"], ensure_ascii=False, sort_keys=True)
                    conflicts.append({
                        "conflict_id": f"CONF_{len(conflicts)+1:03d}",
                        "conflict_type": conflict_type,
                        "description": f"Conflict ({conflict_type}) between `{a1['claim_text']}` and `{a2['claim_text']}`.",
                        "nanopub_ids": [final_nanopubs[i]["nanopub_id"], final_nanopubs[j]["nanopub_id"]],
                        "condition_difference": None if c1 == c2 else f"Cond A: {c1} | Cond B: {c2}",
                        "evidence_spans": final_nanopubs[i]["provenance"]["evidence_spans"][:1] + final_nanopubs[j]["provenance"]["evidence_spans"][:1],
                    })

    err_count = sum(1 for i in issues if i["severity"] == "ERROR")
    warn_count = sum(1 for i in issues if i["severity"] == "WARNING")
    grounded_count = sum(
        1 for np in final_nanopubs
        if all(
            any(x["item_id"] in np["pubinfo"]["merged_from_temp_ids"] for x in [i for i in issues if i["code"] == "quote_not_grounded_in_source"]) is False
            for _ in [0]
        )
    )
    quote_grounding_rate = round(
        sum(1 for it in audited_items if it["matched_span_count"] > 0) / max(1, len(audited_items)),
        4,
    ) if audited_items else 1.0

    audited_bundle = {
        "case_id": source_index.get("case_id"),
        "nanopublications": final_nanopubs,
        "conflicts": conflicts,
        "uncertainty": pre_uncertainty,
        "change_records": change_records,
    }
    report = {
        "status": "PASSED" if err_count == 0 else "FAILED",
        "raw_items_count": len(raw_items),
        "audited_nanopubs_count": len(final_nanopubs),
        "merged_duplicates_count": len(raw_items) - len(final_nanopubs),
        "conflicts_detected_count": len(conflicts),
        "quote_grounding_rate": quote_grounding_rate,
        "error_count": err_count,
        "warning_count": warn_count,
        "issues": issues,
    }
    return audited_bundle, report


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify, normalize, and deduplicate Nanopublications against source_index.json.")
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--source-index", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--strict", action="store_true", help="Exit with code 2 if any ERROR-level gate fails.")
    ap.add_argument("--allow-partial", action="store_true", help="Record errors honestly in report but exit 0 for bounded partial export.")
    args = ap.parse_args()

    raw_data = load_json(args.input)
    source_index = load_json(args.source_index)
    audited, report = verify_nanopubs(raw_data, source_index)
    save_json(args.out, audited)
    save_json(args.report, report)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.strict and report["error_count"] > 0 and not args.allow_partial:
        sys.exit(2)


if __name__ == "__main__":
    main()
