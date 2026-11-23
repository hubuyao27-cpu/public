#!/usr/bin/env python3
"""Step 5: 8-axis mechanical verification of synchronized citations and bibliography."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Set

from common import load_json_safe, normalize_title, save_json

ALLOWED_VERDICTS = {
    "supported",
    "partially_supported",
    "mismatch_or_unsupported",
    "insufficient_source_text",
    "unresolved_ref",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify synchronized citations across 8 axes.")
    parser.add_argument("--synced", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--case-plan", required=True)
    parser.add_argument("--out", default="/app/work/verification_report.json")
    args = parser.parse_args()

    synced, _ = load_json_safe(Path(args.synced), default={})
    snapshot, _ = load_json_safe(Path(args.snapshot), default={})
    decisions, _ = load_json_safe(Path(args.decisions), default={})
    case_plan, _ = load_json_safe(Path(args.case_plan), default={})

    style = synced.get("target_style", "gbt7714_numeric")
    system = synced.get("citation_system", "numeric")
    retain_uncited = bool(synced.get("retain_uncited_library", False))
    cited_ids: List[str] = synced.get("cited_ref_ids", [])
    bib_items: List[Dict[str, Any]] = synced.get("formatted_bibliography", [])
    bib_ids: List[str] = [b["ref_id"] for b in bib_items]
    renum_map: List[Dict[str, Any]] = synced.get("renumbering_map", [])
    final_sents: List[Dict[str, Any]] = synced.get("final_sentences", [])
    raw_ids: Set[str] = {r["raw_id"] for r in snapshot.get("raw_records", [])}

    findings: List[Dict[str, str]] = []

    def add_finding(sev: str, axis: str, loc: str, msg: str) -> None:
        findings.append({"severity": sev, "axis": axis, "location": loc, "message": msg})

    # Axis 1: Bidirectional parity
    cited_set = set(cited_ids)
    bib_set = set(bib_ids)
    dangling = cited_set - bib_set
    orphans = bib_set - cited_set
    if dangling:
        add_finding("ERROR", "bidirectional_parity", "bibliography", f"Cited in text but missing from bibliography: {sorted(dangling)}")
    if orphans and not retain_uncited:
        add_finding("ERROR", "bidirectional_parity", "bibliography", f"Uncited orphan entries in bibliography: {sorted(orphans)}")

    # Axis 2: Sequential continuity & bracket formatting
    if system == "numeric":
        assigned_nums = [b["assigned_number"] for b in bib_items]
        expected_nums = list(range(1, len(bib_items) + 1))
        if assigned_nums != expected_nums:
            add_finding("ERROR", "sequential_continuity", "bibliography", f"Bibliography numbers {assigned_nums} not continuous 1..{len(bib_items)}.")
        # First appearance strictly non-decreasing
        seen_first: List[int] = []
        for rm in renum_map:
            for num in [next((b["assigned_number"] for b in bib_items if b["ref_id"] == rid), None) for rid in rm["canonical_ref_ids"]]:
                if num is not None and num not in seen_first:
                    seen_first.append(num)
        if seen_first != list(range(1, len(seen_first) + 1)):
            add_finding("ERROR", "sequential_continuity", "in_text", f"In-text first-appearance sequence {seen_first} is out of order.")

    # Axis 3: Zero residual duplicates in final bibliography
    seen_titles: Dict[str, str] = {}
    for b in bib_items:
        norm_t = normalize_title(b["formatted_entry"])
        # Only check title portion roughly or merged_from
        for mid in b.get("merged_from", []):
            if mid not in raw_ids:
                add_finding("ERROR", "provenance", b["ref_id"], f"Bibliography ref traces to unknown raw_id '{mid}'.")
        if not b.get("merged_from"):
            add_finding("ERROR", "provenance", b["ref_id"], "Bibliography entry has empty merged_from provenance (possible fabrication).")

    # Check raw title duplicates across formatted entries if title available via decisions/snapshot
    snap_by_raw = {r["raw_id"]: r for r in snapshot.get("raw_records", [])}
    seen_dois: Dict[str, str] = {}
    for b in bib_items:
        r0 = snap_by_raw.get((b.get("merged_from") or [""])[0], {})
        doi = r0.get("doi", "")
        if doi:
            if doi in seen_dois:
                add_finding("ERROR", "deduplication", b["ref_id"], f"Residual duplicate DOI '{doi}' with {seen_dois[doi]}.")
            seen_dois[doi] = b["ref_id"]
        nt = normalize_title(r0.get("title", ""))
        if nt:
            if nt in seen_titles:
                add_finding("ERROR", "deduplication", b["ref_id"], f"Residual duplicate title '{nt}' with {seen_titles[nt]}.")
            seen_titles[nt] = b["ref_id"]

    # Axis 4: Zero literal None/null/empty artifacts
    for b in bib_items:
        txt = b["formatted_entry"]
        if re.search(r"\b(?:None|null|undefined|NaN)\b|\(\)", txt):
            add_finding("ERROR", "formatting_cleanliness", b["ref_id"], f"Artifact/empty placeholder in formatted entry: {txt}")

    # Axis 5: Claim-level placement & match_verdict coverage
    for ins in decisions.get("claim_insertions", []):
        v = ins.get("match_verdict", "")
        if v not in ALLOWED_VERDICTS:
            add_finding("ERROR", "claim_verification", ins.get("sentence_id", ""), f"Invalid or missing match_verdict '{v}'.")
        if not str(ins.get("rationale", "")).strip():
            add_finding("ERROR", "claim_verification", ins.get("sentence_id", ""), "Inserted citation missing rationale.")
    for pn in synced.get("placement_notes", []):
        if pn.get("mode_used") == "fallback_sentence_end":
            add_finding("WARNING", "claim_placement", pn["sentence_id"], f"Anchor phrase '{pn['anchor_phrase']}' not found verbatim; inserted at sentence end.")

    for aud in decisions.get("existing_citation_audits", []):
        v = aud.get("match_verdict", "")
        if v not in ALLOWED_VERDICTS:
            add_finding("ERROR", "claim_verification", aud.get("citation_point_id", ""), f"Invalid match_verdict '{v}'.")
        if v == "mismatch_or_unsupported" and aud.get("action") == "keep":
            add_finding("ERROR", "claim_verification", aud.get("citation_point_id", ""), "Mismatched citation marked 'keep' without replacement, removal, or flag_unresolved.")

    # Axis 6: Target style structural conformance
    for b in bib_items:
        fe = b["formatted_entry"]
        if style == "gbt7714_numeric" and not re.search(r"\[(J|C|M|D|R|S|P|Z|EB/OL)\]", fe):
            add_finding("ERROR", "style_conformance", b["ref_id"], f"GB/T 7714 entry missing document type tag [J]/[C]/[M]/...: {fe}")
        if style == "apa7" and not re.search(r"\(\d{4}[a-z]?|n\.d\.\)", fe):
            add_finding("ERROR", "style_conformance", b["ref_id"], f"APA 7 entry missing parenthetical year: {fe}")
        if style == "ieee" and not re.match(r"^\[\d+\]\s+", fe):
            add_finding("ERROR", "style_conformance", b["ref_id"], f"IEEE entry missing leading '[n] ': {fe}")

    # Axis 7: Cross-zone sync & stale markers
    for s in final_sents:
        ft = s.get("final_text", "")
        if "<<CITE:" in ft or "<<UNRES:" in ft:
            add_finding("ERROR", "cross_zone_sync", s["sentence_id"], f"Unresolved staging token in final text: {ft}")
        if re.search(r"\[(?:REF|\?|CITE|待引用)\]", ft, flags=re.IGNORECASE):
            add_finding("ERROR", "cross_zone_sync", s["sentence_id"], f"Stale placeholder citation remains in {s['zone']}: {ft}")

    # Axis 8: Honest disclosure of missing fields & unresolved markers
    has_unverified = any("[UNVERIFIED_CITE:" in s.get("final_text", "") for s in final_sents)
    if has_unverified and not decisions.get("uncertainty") and not decisions.get("author_confirmation_items"):
        add_finding("ERROR", "uncertainty_disclosure", "", "Unverified citations exist in manuscript but uncertainty/author_confirmation_items are empty.")

    errors = [f for f in findings if f["severity"] == "ERROR"]
    warnings = [f for f in findings if f["severity"] == "WARNING"]

    metrics = {
        "cited_references_count": len(cited_ids),
        "bibliography_entries_count": len(bib_items),
        "renumbered_points_count": len(renum_map),
        "bidirectional_parity_ok": (not dangling) and (not orphans or retain_uncited),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "passed": len(errors) == 0,
    }
    report = {"passed": len(errors) == 0, "metrics": metrics, "findings": findings}
    save_json(Path(args.out), report)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
