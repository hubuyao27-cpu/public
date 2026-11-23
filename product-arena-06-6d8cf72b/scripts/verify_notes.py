#!/usr/bin/env python3
"""Step 4: Independent mechanical verifier for structured literature notes.

Audits anchor validity, page consistency, quote grounding, quantitative numeric
grounding, original section-skeleton alignment, knowledge deduplication & alias
conflicts, boundary honesty, and Harbor output_contract coverage.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    extract_quantitative_numbers,
    load_json,
    normalize_anchor_id,
    normalize_for_match,
    parse_number_variants,
    quote_matches_text,
    save_json,
)


REQUIRED_PAPER_CARD_KEYS = (
    "research_question",
    "data_or_samples",
    "methodology_and_conditions",
    "key_findings",
    "preconditions",
    "conclusions",
    "limitations",
)

SEMANTIC_ROLE_SOURCE_MAP = {
    "case_id": ("case_id",),
    "status": ("status",),
    "task_responses": ("task_responses",),
    "paper_card": ("paper_card",),
    "section_summaries": ("section_summaries",),
    "knowledge_items": ("knowledge_items",),
    "evidence_index": ("paper_card", "section_summaries", "knowledge_items", "task_responses"),
    "sources": ("sources",),
    "uncertainty_and_conflicts": ("not_reported_and_conflicts",),
    "limitations_and_preconditions": ("paper_card", "limitations_note"),
    "executive_summary": ("executive_summary", "section_summaries"),
    "key_findings": ("paper_card",),
    "methodology": ("paper_card",),
    "data_and_samples": ("paper_card",),
}


def resolve_anchor_target(
    anchor_id: str,
    segments: Dict[str, Dict[str, Any]],
    artifacts: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    norm = normalize_anchor_id(anchor_id)
    if norm in segments:
        return segments[norm]
    if norm in artifacts:
        return artifacts[norm]
    # Also try matching case-insensitively on artifact IDs
    low = norm.lower()
    for k, v in artifacts.items():
        if k.lower() == low:
            return v
    return None


def find_adjacent_segment_match(
    quote: str,
    anchor_id: str,
    segment_order: List[str],
    segments: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    norm = normalize_anchor_id(anchor_id)
    if norm not in segments:
        return None
    try:
        idx = segment_order.index(norm)
    except ValueError:
        return None
    for delta in (-1, 1):
        ni = idx + delta
        if 0 <= ni < len(segment_order):
            cand_id = segment_order[ni]
            matched, _, _ = quote_matches_text(quote, segments[cand_id].get("text", ""))
            if matched:
                return cand_id
    return None


def is_value_present(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, str):
        return bool(val.strip())
    if isinstance(val, (list, dict)):
        return len(val) > 0
    return True


def collect_claim_records(notes: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten all evidence-bearing records across task_responses, paper_card, sections, knowledge_items."""
    records: List[Dict[str, Any]] = []

    for idx, tr in enumerate(notes.get("task_responses", [])):
        if not isinstance(tr, dict):
            continue
        ans = str(tr.get("answer") or "")
        st = str(tr.get("status") or "").lower()
        records.append(
            {
                "path": f"task_responses[{idx}]",
                "text": ans,
                "evidence": tr.get("evidence", []),
                "allow_no_evidence": st in ("not_reported", "unable_to_determine"),
                "verification_mode": tr.get("verification_mode"),
                "require_numeric_grounding": True,
            }
        )

    card = notes.get("paper_card", {})
    if isinstance(card, dict):
        for field in ("research_question", "data_or_samples", "methodology_and_conditions", "conclusions"):
            val = card.get(field)
            if isinstance(val, dict):
                txt = str(val.get("summary") or val.get("value") or val.get("text") or "")
                st = str(val.get("status") or "").lower()
                records.append(
                    {
                        "path": f"paper_card.{field}",
                        "text": txt,
                        "evidence": val.get("evidence", []),
                        "allow_no_evidence": st == "not_reported" or txt.strip().lower() == "not_reported",
                        "verification_mode": val.get("verification_mode"),
                        "require_numeric_grounding": True,
                    }
                )
        for list_field in ("key_findings", "preconditions", "limitations"):
            arr = card.get(list_field, [])
            if isinstance(arr, list):
                for idx, entry in enumerate(arr):
                    if isinstance(entry, dict):
                        txt = str(entry.get("statement") or entry.get("condition") or entry.get("limitation") or entry.get("text") or "")
                        st = str(entry.get("status") or "").lower()
                        records.append(
                            {
                                "path": f"paper_card.{list_field}[{idx}]",
                                "text": txt,
                                "evidence": entry.get("evidence", []),
                                "allow_no_evidence": st == "not_reported" or txt.strip().lower() == "not_reported",
                                "verification_mode": entry.get("verification_mode"),
                                "require_numeric_grounding": True,
                            }
                        )

    for idx, sec in enumerate(notes.get("section_summaries", [])):
        if not isinstance(sec, dict):
            continue
        sec_text_parts = [
            str(sec.get(k) or "")
            for k in ("purpose", "methods", "results", "conclusion", "summary")
        ]
        # Section summary top-level evidence
        if sec.get("evidence"):
            records.append(
                {
                    "path": f"section_summaries[{idx}]",
                    "text": " ".join(sec_text_parts),
                    "evidence": sec.get("evidence", []),
                    "allow_no_evidence": False,
                    "verification_mode": sec.get("verification_mode"),
                    "require_numeric_grounding": False,  # Whole-section summary aggregates across paras; key_points check exact nums
                }
            )
        for kidx, kp in enumerate(sec.get("key_points", [])):
            if isinstance(kp, dict):
                records.append(
                    {
                        "path": f"section_summaries[{idx}].key_points[{kidx}]",
                        "text": str(kp.get("claim") or kp.get("statement") or kp.get("text") or ""),
                        "evidence": kp.get("evidence", []),
                        "allow_no_evidence": str(kp.get("status", "")).lower() == "not_reported",
                        "verification_mode": kp.get("verification_mode"),
                        "require_numeric_grounding": True,
                    }
                )

    for idx, ki in enumerate(notes.get("knowledge_items", [])):
        if not isinstance(ki, dict):
            continue
        txt = str(ki.get("statement") or ki.get("definition") or "")
        records.append(
            {
                "path": f"knowledge_items[{idx}]",
                "text": txt,
                "evidence": ki.get("evidence", []),
                "allow_no_evidence": str(ki.get("status", "")).lower() == "not_reported",
                "verification_mode": ki.get("verification_mode"),
                "require_numeric_grounding": True,
            }
        )

    return records


def verify_all(
    source_index: Dict[str, Any],
    outline: Dict[str, Any],
    notes: Dict[str, Any],
    case_plan: Optional[Dict[str, Any]] = None,
    extraction_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    segments: Dict[str, Dict[str, Any]] = source_index.get("segments", {})
    segment_order: List[str] = source_index.get("segment_order", list(segments.keys()))
    artifacts: Dict[str, Dict[str, Any]] = source_index.get("artifacts", {})

    # Pre-build per-page text pools for secondary numeric fallback check (PDFs only)
    page_text_pool: Dict[int, str] = defaultdict(str)
    full_corpus_norm = normalize_for_match(" ".join(s.get("text", "") for s in segments.values()))
    for s in segments.values():
        raw_p = s.get("page")
        if raw_p is not None:
            page_text_pool[int(raw_p)] += " " + s.get("text", "")

    issues: List[Dict[str, Any]] = []

    def add_issue(severity: str, category: str, path: str, message: str, suggestion: str = "") -> None:
        issues.append(
            {
                "severity": severity,
                "category": category,
                "path": path,
                "message": message,
                "suggestion": suggestion,
            }
        )

    # 1. Paper Card completeness check
    card = notes.get("paper_card", {})
    card_present = 0
    for key in REQUIRED_PAPER_CARD_KEYS:
        val = card.get(key) if isinstance(card, dict) else None
        if is_value_present(val):
            card_present += 1
        else:
            add_issue(
                "ERROR",
                "paper_card_completeness",
                f"paper_card.{key}",
                f"Required Paper Card dimension '{key}' is missing or empty.",
                "Populate from source text with anchor(s), or explicitly record 'not_reported' with boundary reason.",
            )
    card_completeness_rate = card_present / len(REQUIRED_PAPER_CARD_KEYS)

    # 2. Task questions coverage check (if case_plan specifies explicit questions)
    questions = (case_plan or {}).get("questions_detected", [])
    task_responses = notes.get("task_responses", [])
    if questions and not task_responses:
        add_issue(
            "ERROR",
            "task_question_coverage",
            "task_responses",
            f"Case specifies {len(questions)} explicit question(s), but 'task_responses' is empty.",
            "Answer every detected question in task_responses with grounded evidence anchors.",
        )

    # 3. Anchor, Page, Quote, and Numeric Grounding checks across all claim records
    records = collect_claim_records(notes)
    anchor_checks = 0
    anchor_valid = 0
    quote_checks = 0
    quote_matched = 0
    numeric_checks = 0
    numeric_grounded = 0
    claims_with_evidence = 0
    claims_needing_evidence = 0
    missing_vmode_count = 0

    for rec in records:
        rpath = rec["path"]
        evs = rec.get("evidence") or []
        if not rec.get("verification_mode"):
            missing_vmode_count += 1

        if not rec["allow_no_evidence"]:
            claims_needing_evidence += 1
            if evs:
                claims_with_evidence += 1
            else:
                add_issue(
                    "ERROR",
                    "missing_evidence",
                    rpath,
                    "Substantive claim/answer has no attached evidence anchor.",
                    "Attach at least one {anchor_id, page, section, quote} object, or mark status='not_reported'.",
                )

        bound_texts: List[str] = []
        bound_pages: set[int] = set()

        for eidx, ev in enumerate(evs):
            if not isinstance(ev, dict):
                add_issue("ERROR", "invalid_evidence_format", f"{rpath}.evidence[{eidx}]", "Evidence entry must be a JSON object.")
                continue
            anchor_checks += 1
            raw_aid = str(ev.get("anchor_id") or "")
            aid = normalize_anchor_id(raw_aid)
            target = resolve_anchor_target(aid, segments, artifacts)
            if not target:
                add_issue(
                    "ERROR",
                    "missing_anchor",
                    f"{rpath}.evidence[{eidx}]",
                    f"Anchor ID '{raw_aid}' does not exist in source_index.json.",
                    "Use an exact ID from anchored_paper.md (e.g., P012, FIG:Fig. 1, TAB:Table 2).",
                )
                continue

            anchor_valid += 1
            raw_t_page = target.get("page")
            t_page: Optional[int] = int(raw_t_page) if raw_t_page is not None else None
            if t_page is not None:
                bound_pages.add(t_page)
            bound_texts.append(target.get("text", ""))

            # Page consistency check (PDF vs non-PDF without page numbers)
            if ev.get("page") is not None:
                if t_page is None:
                    add_issue(
                        "WARNING",
                        "synthetic_page_on_non_pdf",
                        f"{rpath}.evidence[{eidx}].page",
                        f"Anchor '{aid}' comes from a non-PDF source without page numbers; omit 'page' or set null instead of {ev['page']}.",
                        "Set page to null for non-PDF sources.",
                    )
                else:
                    try:
                        if int(ev["page"]) != t_page:
                            add_issue(
                                "ERROR",
                                "page_mismatch",
                                f"{rpath}.evidence[{eidx}].page",
                                f"Anchor '{aid}' is on page {t_page}, but evidence declares page {ev['page']}.",
                                f"Update page to {t_page}.",
                            )
                    except (TypeError, ValueError):
                        add_issue("WARNING", "invalid_page_number", f"{rpath}.evidence[{eidx}].page", f"Non-integer page '{ev['page']}'.")

            # Quote grounding check
            quote = str(ev.get("quote") or "").strip()
            if len(quote) >= 6:
                quote_checks += 1
                matched, overlap, mode = quote_matches_text(quote, target.get("text", ""))
                if matched:
                    quote_matched += 1
                else:
                    adj = find_adjacent_segment_match(quote, aid, segment_order, segments)
                    if adj:
                        add_issue(
                            "WARNING",
                            "quote_in_adjacent_paragraph",
                            f"{rpath}.evidence[{eidx}]",
                            f"Quote not in '{aid}' (overlap={overlap:.2f}), but found verbatim in adjacent '{adj}'.",
                            f"Change anchor_id from '{aid}' to '{adj}'.",
                        )
                    else:
                        add_issue(
                            "ERROR",
                            "quote_not_found_in_anchor",
                            f"{rpath}.evidence[{eidx}]",
                            f"Quote '{quote[:60]}...' is not grounded in '{aid}' (overlap={overlap:.2f}, mode={mode}).",
                            "Copy an exact short excerpt from the cited segment in anchored_paper.md.",
                        )

        # Numeric grounding check
        if rec["require_numeric_grounding"] and bound_texts:
            q_nums = extract_quantitative_numbers(rec["text"])
            if q_nums:
                anchor_norm = normalize_for_match(" ".join(bound_texts))
                page_norm = normalize_for_match(" ".join(page_text_pool[p] for p in bound_pages)) if bound_pages else ""
                for num in q_nums:
                    numeric_checks += 1
                    variants = [v.lower() for v in parse_number_variants(num)]
                    if any(v in anchor_norm for v in variants):
                        numeric_grounded += 1
                    elif page_norm and any(v in page_norm for v in variants):
                        numeric_grounded += 1
                        add_issue(
                            "WARNING",
                            "numeric_on_same_page_other_segment",
                            rpath,
                            f"Numeric value '{num}' appears on the same page ({sorted(bound_pages)}) but not in the cited anchor paragraph.",
                            "Also cite the exact paragraph/table/figure anchor containing this value.",
                        )
                    elif any(v in full_corpus_norm for v in variants):
                        numeric_grounded += 1
                        add_issue(
                            "WARNING",
                            "numeric_elsewhere_in_paper",
                            rpath,
                            f"Numeric value '{num}' was not found in bound anchor(s), though present elsewhere in paper.",
                            "Add the exact source anchor where this number is reported.",
                        )
                    else:
                        add_issue(
                            "ERROR",
                            "ungrounded_numeric_claim",
                            rpath,
                            f"Quantitative value '{num}' in note is NOT found anywhere in extracted source text.",
                            "Verify against original document or fix hallucinated/miscalculated number.",
                        )

    # 4. Section skeleton alignment & sequential order check
    body_sec_ids: List[str] = outline.get("body_section_ids", [])
    sec_summaries = notes.get("section_summaries", [])
    covered_sec_ids: List[str] = []
    sec_rank = {sid: idx for idx, sid in enumerate(body_sec_ids)}

    noted_order_ranks: List[int] = []
    for idx, s in enumerate(sec_summaries):
        if not isinstance(s, dict):
            continue
        sid = str(s.get("section_id") or "")
        if sid in sec_rank:
            covered_sec_ids.append(sid)
            noted_order_ranks.append(sec_rank[sid])
        else:
            # Allow parent summary covering children or S00
            matched_children = [bs for bs in body_sec_ids if bs.startswith(sid + ".")]
            if matched_children:
                covered_sec_ids.extend(matched_children)
                noted_order_ranks.append(sec_rank[matched_children[0]])
        if idx > 0 and not str(s.get("transition_from_prev") or "").strip():
            add_issue(
                "WARNING",
                "missing_section_transition",
                f"section_summaries[{idx}].transition_from_prev",
                f"Section '{sid}' lacks 'transition_from_prev' explaining how it connects to the previous section.",
                "Add a concise 1-sentence logical transition between sections.",
            )

    covered_unique = set(covered_sec_ids)
    missing_sections = [sid for sid in body_sec_ids if sid not in covered_unique]
    for msid in missing_sections:
        add_issue(
            "WARNING",
            "missing_section_summary",
            "section_summaries",
            f"Original paper section '{msid}' is missing from section_summaries.",
            "Add a section summary entry for this section or document why it was unreadable/skipped.",
        )

    section_coverage_rate = (len(covered_unique) / len(body_sec_ids)) if body_sec_ids else 1.0
    inversions = sum(
        1 for i in range(1, len(noted_order_ranks)) if noted_order_ranks[i] < noted_order_ranks[i - 1]
    )
    section_order_monotone_rate = (
        1.0 - (inversions / max(1, len(noted_order_ranks) - 1))
        if len(noted_order_ranks) > 1
        else 1.0
    )
    if inversions > 0:
        add_issue(
            "ERROR",
            "section_order_mismatch",
            "section_summaries",
            f"Section summaries deviate from original paper order ({inversions} order inversion(s)).",
            "Reorder section_summaries to strictly follow section_outline.json.",
        )

    # 5. Knowledge items deduplication & alias conflict check
    kitems = notes.get("knowledge_items", [])
    seen_term_keys: Dict[str, int] = {}
    alias_owners: Dict[str, List[str]] = defaultdict(list)
    for idx, ki in enumerate(kitems):
        if not isinstance(ki, dict):
            continue
        term = str(ki.get("canonical_term") or "").strip()
        kind = str(ki.get("kind") or "concept").lower()
        tkey = f"{kind}::{normalize_for_match(term)}"
        if tkey in seen_term_keys:
            add_issue(
                "ERROR",
                "duplicate_knowledge_term",
                f"knowledge_items[{idx}]",
                f"Duplicate knowledge item '{term}' (kind={kind}) already appears at index {seen_term_keys[tkey]}.",
                "Merge duplicates via merge_notes.py or disambiguate distinct concepts.",
            )
        else:
            seen_term_keys[tkey] = idx
        for alias in ki.get("aliases", []):
            na = normalize_for_match(str(alias))
            if na and term and term not in alias_owners[na]:
                alias_owners[na].append(term)

    for alias_norm, owners in alias_owners.items():
        if len(owners) > 1:
            add_issue(
                "WARNING",
                "ambiguous_alias",
                "knowledge_items",
                f"Alias/abbreviation '{alias_norm}' maps to multiple terms: {owners}.",
                "Disambiguate acronym sense in notes or merge synonymous entries.",
            )

    # 6. Verification mode & boundary honesty checks
    if missing_vmode_count > 0:
        add_issue(
            "WARNING",
            "missing_verification_mode",
            "notes_payload",
            f"{missing_vmode_count} structured entry/entries lack explicit 'verification_mode' ('machine_verifiable' | 'human_review_required').",
            "Label each major claim/entry with verification_mode for downstream audit clarity.",
        )

    if extraction_report:
        if extraction_report.get("any_unreadable_pages"):
            nrc = notes.get("not_reported_and_conflicts", {})
            if not (nrc.get("unable_to_determine") or notes.get("manual_takeover_points")):
                add_issue(
                    "ERROR",
                    "unreported_unreadable_pages",
                    "not_reported_and_conflicts",
                    "Source contains unreadable/scanned pages, but notes do not record them in unable_to_determine or manual_takeover_points.",
                    "Explicitly document unreadable pages and human-takeover locations instead of implying complete coverage.",
                )

    # 7. output_contract check (against contract_overrides or semantic role mapping)
    contract_fields = (case_plan or {}).get("output_contract_parsed", {}).get("fields", [])
    overrides = notes.get("contract_overrides", {}) if isinstance(notes.get("contract_overrides"), dict) else {}
    contract_covered = 0
    for cf in contract_fields:
        cname = cf["name"]
        role = cf.get("semantic_role", "custom")
        if cname in overrides and is_value_present(overrides[cname]):
            contract_covered += 1
            continue
        mapped_sources = SEMANTIC_ROLE_SOURCE_MAP.get(role, ())
        if any(is_value_present(notes.get(src)) for src in mapped_sources):
            contract_covered += 1
        elif role in ("case_id", "status", "sources", "audit_metrics", "change_records", "manual_takeover_points"):
            contract_covered += 1
        else:
            msg = f"output_contract field '{cname}' (role={role}) has no explicit value in contract_overrides or standard payload."
            add_issue(
                "ERROR" if cf.get("required") else "WARNING",
                "contract_field_unmapped",
                f"contract_overrides.{cname}",
                msg,
                f"Provide explicit value in notes_payload.contract_overrides['{cname}'].",
            )
    contract_coverage_rate = (contract_covered / len(contract_fields)) if contract_fields else 1.0

    error_count = sum(1 for i in issues if i["severity"] == "ERROR")
    warning_count = sum(1 for i in issues if i["severity"] == "WARNING")

    metrics = {
        "paper_card_completeness_rate": round(card_completeness_rate, 4),
        "evidence_attachment_rate": round(claims_with_evidence / claims_needing_evidence, 4) if claims_needing_evidence else 1.0,
        "anchor_checks": anchor_checks,
        "anchor_validity_rate": round(anchor_valid / anchor_checks, 4) if anchor_checks else 1.0,
        "quote_checks": quote_checks,
        "quote_match_rate": round(quote_matched / quote_checks, 4) if quote_checks else 1.0,
        "numeric_checks": numeric_checks,
        "numeric_grounding_rate": round(numeric_grounded / numeric_checks, 4) if numeric_checks else 1.0,
        "body_sections_total": len(body_sec_ids),
        "section_coverage_rate": round(section_coverage_rate, 4),
        "section_order_monotone_rate": round(section_order_monotone_rate, 4),
        "knowledge_items_count": len(kitems),
        "contract_coverage_rate": round(contract_coverage_rate, 4),
        "error_count": error_count,
        "warning_count": warning_count,
    }

    return {
        "passed": error_count == 0,
        "metrics": metrics,
        "missing_sections": missing_sections,
        "issues": issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Mechanically audit structured notes against source_index and outline.")
    parser.add_argument("--source", required=True, help="Path to source_index.json")
    parser.add_argument("--outline", required=True, help="Path to section_outline.json")
    parser.add_argument("--notes", required=True, help="Path to notes_payload.json")
    parser.add_argument("--case-plan", default=None, help="Optional path to case_plan.json")
    parser.add_argument("--extraction", default=None, help="Optional path to extraction_report.json")
    parser.add_argument("--out", default="/app/work/verification_report.json", help="Output path for verification report.")
    args = parser.parse_args()

    source_index = load_json(args.source, {})
    outline = load_json(args.outline, {})
    notes = load_json(args.notes, {})
    case_plan = load_json(args.case_plan, None) if args.case_plan else None
    extraction = load_json(args.extraction, None) if args.extraction else None

    report = verify_all(source_index, outline, notes, case_plan, extraction)
    save_json(args.out, report)
    m = report["metrics"]
    print(
        f"Verification {'PASSED' if report['passed'] else 'FAILED'}: "
        f"anchors={m['anchor_validity_rate']:.1%}, quotes={m['quote_match_rate']:.1%}, "
        f"numbers={m['numeric_grounding_rate']:.1%}, sections={m['section_coverage_rate']:.1%}, "
        f"errors={m['error_count']}, warnings={m['warning_count']} -> {args.out}"
    )


if __name__ == "__main__":
    main()
