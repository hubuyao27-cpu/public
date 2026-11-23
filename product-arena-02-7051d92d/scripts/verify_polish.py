#!/usr/bin/env python3
"""Step 5: 8-axis mechanical verification of /app/work/polished_payload.json.

Verifies:
1. Sentence coverage & verbatim original fidelity
2. Protected-span conservation (numbers/units, LaTeX math, citations, cross-refs, formulas)
3. Epistemic hedge tier conservation
4. Causality vs correlation, negation polarity, and conditional/quantifier conservation
5. Glossary & abbreviation consistency
6. Change-record auditability (changed flag, controlled edit_types, non-empty rationale)
7. Syntax/delimiter balance, minimal-edit discipline, and residual register alerts
8. Honest disclosure of glossary conflicts & uncertainties
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set

from analyze_manuscript import analyze_epistemic_and_polarity, diagnose_sentence_issues, extract_protected_spans
from common import (
    EDIT_TYPES,
    load_json_safe,
    load_resource_json,
    normalized_edit_distance_ratio,
    save_json,
)

GENERIC_RATIONALES = {"polished", "improved", "edited", "fixed", "updated", "润色", "修改", "优化", "改写"}


def _norm_token(tok: str) -> str:
    """Normalize benign spacing, comma-thousands, and dash variants for span comparison."""
    s = tok.strip()
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)
    s = re.sub(r"\s+", "", s)
    return s


def _check_balanced_delimiters(text: str) -> List[str]:
    """Check balanced {}, (), [], and $ delimiters (ignoring escaped \\{ \\} \\$)."""
    cleaned = re.sub(r"\\[{}$()\[\]]", "", text)
    issues: List[str] = []
    # Check curly braces
    depth = 0
    for ch in cleaned:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                issues.append("Unmatched closing '}'")
                break
    if depth > 0:
        issues.append("Unclosed '{'")

    # Check inline $ count (after stripping $$)
    no_disp = cleaned.replace("$$", "")
    if no_disp.count("$") % 2 != 0:
        issues.append("Unbalanced inline '$'")

    if cleaned.count("(") != cleaned.count(")") and not re.search(r"^\s*\d+\)", cleaned):
        issues.append("Unbalanced parentheses '()'")
    if cleaned.count("[") != cleaned.count("]"):
        issues.append("Unbalanced square brackets '[]'")
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify polished payload against source segments and rules.")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--segments", required=True)
    parser.add_argument("--glossary", required=True)
    parser.add_argument("--case-plan", required=True)
    parser.add_argument("--out", default="/app/work/verification_report.json")
    args = parser.parse_args()

    payload, err_p = load_json_safe(Path(args.payload), default={})
    seg_doc, err_s = load_json_safe(Path(args.segments), default={})
    glossary_doc, err_g = load_json_safe(Path(args.glossary), default={})
    case_plan, err_c = load_json_safe(Path(args.case_plan), default={})

    for label, err in (("payload", err_p), ("segments", err_s), ("glossary", err_g), ("case_plan", err_c)):
        if err and not isinstance(locals().get(label if label != "segments" else "seg_doc"), dict):
            raise SystemExit(f"Failed to load {label}: {err}")

    lexicon = load_resource_json("hedge_and_causality_lexicon.json")
    rules = load_resource_json("chinglish_and_register_rules.json")

    src_segments: List[Dict[str, Any]] = seg_doc.get("segments", [])
    src_by_id: Dict[str, Dict[str, Any]] = {s["sentence_id"]: s for s in src_segments}
    pol_sentences: List[Dict[str, Any]] = payload.get("sentences", [])
    signals = case_plan.get("signals", {})
    edit_intensity = payload.get("edit_intensity") or signals.get("edit_intensity", "standard")
    target_lang = payload.get("language") or signals.get("language", "en")

    findings: List[Dict[str, Any]] = []

    def add_finding(severity: str, axis: str, sid: str, message: str) -> None:
        findings.append({"severity": severity, "axis": axis, "sentence_id": sid, "message": message})

    # Axis 1: Coverage & order
    seen_ids: List[str] = []
    pol_by_id: Dict[str, Dict[str, Any]] = {}
    for item in pol_sentences:
        sid = str(item.get("sentence_id", ""))
        if not sid:
            add_finding("ERROR", "coverage", "", "Sentence entry missing sentence_id.")
            continue
        if sid in pol_by_id:
            add_finding("ERROR", "coverage", sid, f"Duplicate sentence_id in payload: {sid}")
            continue
        seen_ids.append(sid)
        pol_by_id[sid] = item
        if sid not in src_by_id:
            add_finding("ERROR", "coverage", sid, f"Unknown sentence_id not found in source: {sid}")

    expected_ids = [s["sentence_id"] for s in src_segments]
    for sid in expected_ids:
        if sid not in pol_by_id:
            add_finding("ERROR", "coverage", sid, f"Missing source sentence_id in payload: {sid}")

    if seen_ids == expected_ids:
        pass
    elif set(seen_ids) == set(expected_ids):
        add_finding("ERROR", "coverage", "", "Sentence order in payload does not match source segment order.")

    # Counters for metrics
    span_checks_total = 0
    span_checks_passed = 0
    hedge_checks_total = 0
    hedge_checks_passed = 0
    glossary_checks_total = 0
    glossary_checks_passed = 0
    changed_count = 0
    edit_ratios: List[float] = []

    # Prepare non-conflicting glossary constraints
    conflict_keys: Set[str] = {str(c.get("key", "")).lower() for c in glossary_doc.get("glossary_conflicts", [])}
    locked_terms: List[Dict[str, Any]] = []
    for t in glossary_doc.get("user_terms", []):
        en_k = str(t.get("canonical_en", "")).lower()
        zh_k = str(t.get("canonical_zh", ""))
        if (en_k and en_k in conflict_keys) or (zh_k and zh_k in conflict_keys):
            continue
        locked_terms.append(t)

    # Per-sentence checks (Axes 2-7)
    for sid in expected_ids:
        if sid not in pol_by_id:
            continue
        src = src_by_id[sid]
        pol = pol_by_id[sid]
        orig = str(pol.get("original", ""))
        polished = str(pol.get("polished", ""))

        if orig != src["original"]:
            add_finding("ERROR", "coverage", sid, "Payload 'original' does not match source segment verbatim.")
        if not polished.strip():
            add_finding("ERROR", "coverage", sid, "Payload 'polished' text is empty.")
            continue

        # Axis 6: Auditability of change records
        changed = pol.get("changed")
        edit_types = pol.get("edit_types") or []
        rationale = str(pol.get("rationale", "")).strip()

        if orig == polished:
            if changed is True:
                add_finding("ERROR", "auditability", sid, "Marked changed=true but polished == original.")
        else:
            changed_count += 1
            if changed is not True:
                add_finding("ERROR", "auditability", sid, "Text was modified but changed != true.")
            if not edit_types or edit_types == ["unchanged"]:
                add_finding("ERROR", "auditability", sid, "Modified sentence lacks valid non-unchanged edit_types.")
            for et in edit_types:
                if et not in EDIT_TYPES:
                    add_finding("ERROR", "auditability", sid, f"Invalid edit_type '{et}'.")
            if len(rationale) < 8 or rationale.lower() in GENERIC_RATIONALES:
                add_finding("ERROR", "auditability", sid, "Modified sentence rationale is missing or too generic.")

        # Axis 2: Protected-span conservation
        src_spans = src["protected_spans"]
        pol_spans = extract_protected_spans(polished)
        for span_cat in ("numbers_units", "math_latex", "citations", "cross_refs", "chemical_symbols"):
            src_list = src_spans.get(span_cat, [])
            if not src_list:
                continue
            pol_norm_set = {_norm_token(x) for x in pol_spans.get(span_cat, [])}
            pol_raw_norm = _norm_token(polished)
            for tok in src_list:
                span_checks_total += 1
                nt = _norm_token(tok)
                if nt in pol_norm_set or nt in pol_raw_norm:
                    span_checks_passed += 1
                else:
                    add_finding(
                        "ERROR",
                        "protected_spans",
                        sid,
                        f"Protected {span_cat} token '{tok}' from original missing or altered in polished text.",
                    )

        # Axis 3: Epistemic hedge conservation
        src_ep = src["epistemic"]
        pol_ep = analyze_epistemic_and_polarity(polished, lexicon)
        if src_ep["hedge_tier"] is not None or pol_ep["max_hedge_tier"] == 5:
            hedge_checks_total += 1
            src_t = src_ep["hedge_tier"]
            pol_min = pol_ep["hedge_tier"]
            pol_max = pol_ep["max_hedge_tier"]
            hedge_ok = True
            if src_t is not None and src_t <= 2:
                if pol_min is None:
                    hedge_ok = False
                    add_finding(
                        "ERROR",
                        "epistemic_hedging",
                        sid,
                        f"Epistemic hedge removed: original tier {src_t} ({src_ep['hedges_found']}), polished has none.",
                    )
                elif pol_max is not None and (pol_max - src_t) >= 2:
                    hedge_ok = False
                    add_finding(
                        "ERROR",
                        "epistemic_hedging",
                        sid,
                        f"Epistemic claim strengthened without basis: original tier {src_t} ({src_ep['hedges_found']}) -> polished tier {pol_max} ({pol_ep['hedges_found']}).",
                    )
            elif pol_max == 5 and (src_ep["max_hedge_tier"] or 3) < 5:
                hedge_ok = False
                add_finding(
                    "ERROR",
                    "epistemic_hedging",
                    sid,
                    f"Unauthorized tier-5 absolute wording introduced: {pol_ep['hedges_found']}.",
                )
            if hedge_ok:
                hedge_checks_passed += 1

        # Axis 4: Causality & negation/conditional conservation
        if src_ep["correlational_cues"] and not src_ep["causal_cues"] and pol_ep["causal_cues"]:
            add_finding(
                "ERROR",
                "causality_polarity",
                sid,
                f"Correlation upgraded to causation: original {src_ep['correlational_cues']} -> polished causal {pol_ep['causal_cues']}.",
            )
        has_neg_src = bool(src_ep["negation_cues"])
        has_neg_pol = bool(pol_ep["negation_cues"])
        if has_neg_src != has_neg_pol:
            add_finding(
                "ERROR",
                "causality_polarity",
                sid,
                f"Negation polarity mismatch: original negations {src_ep['negation_cues']} vs polished {pol_ep['negation_cues']}.",
            )
        if src_ep["conditional_cues"] and not pol_ep["conditional_cues"]:
            add_finding(
                "WARNING",
                "causality_polarity",
                sid,
                f"Conditional/concessive cue dropped: {src_ep['conditional_cues']}. Verify scope preservation.",
            )
        if src_ep["quantifier_cues"] and not pol_ep["quantifier_cues"]:
            add_finding(
                "WARNING",
                "causality_polarity",
                sid,
                f"Quantifier cue dropped: {src_ep['quantifier_cues']}. Verify scope preservation.",
            )

        # Axis 5: Terminology & glossary compliance
        pol_low = polished.lower()
        orig_low = orig.lower()
        for term in locked_terms:
            c_en = str(term.get("canonical_en", "")).strip()
            c_zh = str(term.get("canonical_zh", "")).strip()
            avoid_list = [str(a).strip() for a in term.get("avoid", []) if str(a).strip()]
            for av in avoid_list:
                if re.search(rf"(?<!\w){re.escape(av)}(?!\w)", polished, flags=re.IGNORECASE):
                    glossary_checks_total += 1
                    add_finding("ERROR", "terminology", sid, f"Disallowed/avoided variant '{av}' used in polished text; expected '{c_en or c_zh}'.")
            if target_lang in ("en", "bilingual") and c_en:
                # If canonical EN or any avoided variant appeared in original, canonical EN (or acronym) should remain in polished
                triggers = [c_en.lower()] + [a.lower() for a in avoid_list]
                if any(t and t in orig_low for t in triggers):
                    glossary_checks_total += 1
                    acr = str(term.get("acronym", "")).strip()
                    if (c_en.lower() in pol_low) or (acr and acr in polished):
                        glossary_checks_passed += 1
                    else:
                        add_finding("ERROR", "terminology", sid, f"Canonical EN term '{c_en}' missing after polishing.")
            if target_lang == "zh" and c_zh:
                triggers_zh = [c_zh] + avoid_list
                if any(t and t in orig for t in triggers_zh):
                    glossary_checks_total += 1
                    if c_zh in polished:
                        glossary_checks_passed += 1
                    else:
                        add_finding("ERROR", "terminology", sid, f"Canonical ZH term '{c_zh}' missing after polishing.")

        # Axis 7: Delimiter balance, minimal-edit ratio, and residual register flags
        for delim_err in _check_balanced_delimiters(polished):
            add_finding("ERROR", "syntax_integrity", sid, f"Delimiter imbalance in polished text: {delim_err}")

        ed_ratio = normalized_edit_distance_ratio(orig, polished)
        edit_ratios.append(ed_ratio)
        if edit_intensity == "minimal" and ed_ratio > 0.60:
            allowed_restructure = {"chinglish_translationese", "sentence_splitting_flow", "bilingual_alignment"}
            if not (set(edit_types) & allowed_restructure):
                add_finding(
                    "WARNING",
                    "minimal_edit",
                    sid,
                    f"High edit ratio ({ed_ratio:.2f}) in minimal-edit mode; confirm no unnecessary wholesale rewrite.",
                )

        rem_diags = diagnose_sentence_issues(polished, rules)
        for rd in rem_diags:
            if rd["category"] in ("academic_register", "chinglish_translationese"):
                add_finding("WARNING", "register_residual", sid, f"Residual diagnostic after polish: {rd['detail']}")

    # Axis 8: Conflict & uncertainty honesty
    conflicts = glossary_doc.get("glossary_conflicts", [])
    if conflicts:
        disclosed_blob = json.dumps(
            {
                "uncertainty": payload.get("uncertainty", []),
                "author_confirmation_items": payload.get("author_confirmation_items", []),
            },
            ensure_ascii=False,
        ).lower()
        for gc in conflicts:
            cid = str(gc.get("conflict_id", "")).lower()
            ckey = str(gc.get("key", "")).lower()
            if cid not in disclosed_blob and ckey not in disclosed_blob:
                add_finding(
                    "ERROR",
                    "conflict_disclosure",
                    "",
                    f"Glossary conflict {gc.get('conflict_id')} ('{gc.get('key')}') not disclosed in uncertainty or author_confirmation_items.",
                )

    errors = [f for f in findings if f["severity"] == "ERROR"]
    warnings = [f for f in findings if f["severity"] == "WARNING"]

    metrics = {
        "sentences_total": len(expected_ids),
        "sentences_changed": changed_count,
        "mean_edit_distance_ratio": round(sum(edit_ratios) / max(1, len(edit_ratios)), 4),
        "protected_span_checks": span_checks_total,
        "protected_span_retention_rate": round(span_checks_passed / span_checks_total, 4) if span_checks_total else 1.0,
        "hedge_checks": hedge_checks_total,
        "hedge_conservation_rate": round(hedge_checks_passed / hedge_checks_total, 4) if hedge_checks_total else 1.0,
        "glossary_checks": glossary_checks_total,
        "glossary_compliance_rate": round(glossary_checks_passed / glossary_checks_total, 4) if glossary_checks_total else 1.0,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "passed": len(errors) == 0,
    }

    report = {
        "passed": len(errors) == 0,
        "metrics": metrics,
        "findings": findings,
    }
    save_json(Path(args.out), report)
    if len(errors) > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
