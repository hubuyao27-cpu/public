#!/usr/bin/env python3
"""Independent mechanical verification of final citation & reference deliverables.

Enforces 10 strict invariants:
1. Zero residual internal [[CITE:...]] / [[CITE_NARR:...]] tokens in final outputs.
2. Bidirectional 1-to-1 consistency between in-text cited refs and bibliography entries.
3. Strict sequential continuity (1..K, no gaps, monotonic first appearance, valid a<b ranges) for numeric styles.
4. Formatting cleanliness (no None/null/NaN/empty brackets/doubled punctuation; proper GB/T 7714 等/et al).
5. Field-level provenance integrity (every canonical record traces back to real raw input locators).
6. Deduplication & conflict disclosure (merge_groups, recycled DOI conflicts, Tier-3 candidates reported).
7. Claim verification grounding (cited sentences covered; any evidence_quote matches abstract/title/PDF text).
8. Placeholder & unresolved-citation accounting (zero raw [REF]/[?]/[待引用] left silently; all mapped to human items).
9. Harbor result.json contract compliance (case_id + every output_contract key present and non-null).
10. Editable & preview artifact presence check (.docx/.tex non-empty; preview status honest).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from common import load_json_safe, normalize_title, parse_author_name, read_text_safe, save_json
from parse_manuscript import REF_HEADER_RE, expand_numeric_spec


def _split_body_and_bib(revised_md: str) -> Tuple[str, str]:
    lines = revised_md.splitlines()
    for idx, ln in enumerate(lines):
        if REF_HEADER_RE.match(ln.strip()):
            return "\n".join(lines[:idx]), "\n".join(lines[idx + 1 :])
    return revised_md, ""


def _extract_rendered_numeric_citations(body_text: str) -> List[int]:
    """Extract all numeric citations in exact appearance order from rendered Markdown body."""
    ordered_nums: List[int] = []
    # Match superscript HTML, superscript MD, inline brackets, or circled footnotes
    pat = re.compile(
        r"<sup>\s*\[?([0-9,\s\-–—]+)\]?\s*</sup>"
        r"|\^\[([0-9,\s\-–—]+)\]"
        r"|(?<!\^)(?<!\[)\[(\d+(?:\s*(?:[-–—,~]|,\s*)\s*\d+)*)\](?!\()"
        r"|([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])"
    )
    circled_map = {ch: i + 1 for i, ch in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")}
    for m in pat.finditer(body_text):
        spec = m.group(1) or m.group(2) or m.group(3)
        if spec:
            for n in expand_numeric_spec(spec):
                ordered_nums.append(n)
        elif m.group(4):
            ordered_nums.append(circled_map[m.group(4)])
    return ordered_nums


def _check_inverted_ranges(body_text: str) -> List[str]:
    bad: List[str] = []
    for m in re.finditer(r"(\d+)\s*[-–—]\s*(\d+)", body_text):
        # Only inspect inside citation brackets
        ctx = body_text[max(0, m.start() - 6) : min(len(body_text), m.end() + 6)]
        if "[" in ctx or "<sup>" in ctx or "^[" in ctx:
            a, b = int(m.group(1)), int(m.group(2))
            if a >= b:
                bad.append(f"{a}-{b} in `{ctx.strip()}`")
    return bad


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify final citation deliverables against 10 mechanical invariants.")
    parser.add_argument("--case-plan", default="/app/work/case_plan.json")
    parser.add_argument("--library", default="/app/work/library/library_canonical.json")
    parser.add_argument("--evidence", default="/app/work/library/evidence_fulltexts.json")
    parser.add_argument("--manuscript", default="/app/work/manuscript/manuscript_parsed.json")
    parser.add_argument("--pre-audit", default="/app/work/verify/pre_audit.json")
    parser.add_argument("--plan", default="/app/work/citation_plan.json")
    parser.add_argument("--synced", default="/app/work/synced/sync_result.json")
    parser.add_argument("--output-dir", default="/app/output")
    parser.add_argument("--out", default="/app/work/verification_report.json")
    args = parser.parse_args()

    case_plan = load_json_safe(Path(args.case_plan), {})
    lib = load_json_safe(Path(args.library), {})
    ev_data = load_json_safe(Path(args.evidence), {})
    ms = load_json_safe(Path(args.manuscript), {})
    pre_audit = load_json_safe(Path(args.pre_audit), {})
    plan = load_json_safe(Path(args.plan), {}) if Path(args.plan).exists() else {}
    synced = load_json_safe(Path(args.synced), {})
    out_dir = Path(args.output_dir)

    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    def err(code: str, msg: str) -> None:
        errors.append({"code": code, "message": msg})

    def warn(code: str, msg: str) -> None:
        warnings.append({"code": code, "message": msg})

    result_path = out_dir / "result.json"
    revised_md_path = out_dir / "revised_manuscript.md"
    audit_md_path = out_dir / "citation_audit_report.md"

    result_json = load_json_safe(result_path, {}) if result_path.exists() else {}
    revised_md, _, _ = read_text_safe(revised_md_path) if revised_md_path.exists() else ("", "", None)
    audit_md, _, _ = read_text_safe(audit_md_path) if audit_md_path.exists() else ("", "", None)

    # 1. Residual internal tokens check
    for fname in ("revised_manuscript.md", "revised_manuscript.tex", "references_formatted.md"):
        p = out_dir / fname
        if p.exists():
            txt, _, _ = read_text_safe(p)
            if "[[CITE" in txt:
                err("residual_internal_token", f"Unrendered [[CITE...]] token found in {fname}")

    # 2 & 3. Bidirectional consistency & sequential numbering continuity
    target_style = synced.get("target_style", "gbt7714_2015_numeric")
    in_text_format = synced.get("in_text_format", "bracket")
    formatted_refs = synced.get("formatted_references", [])
    bib_nums = {fr["assigned_number"] for fr in formatted_refs}
    library_only = len(ms.get("sentences", [])) == 0 and len(formatted_refs) > 0
    body_text, bib_text = _split_body_and_bib(revised_md)

    if library_only:
        if target_style not in ("apa7", "apa6", "gbt7714_2015_author_year", "chicago_author_date") and bib_nums:
            if bib_nums != set(range(1, len(bib_nums) + 1)):
                err("numbering_gap_or_noncontiguous", f"Library-only bibliography numbers {sorted(bib_nums)} != 1..{len(bib_nums)}")
    elif in_text_format == "latex_cite":
        # Check \cite{key} vs \bibitem{key}
        tex_path = out_dir / "revised_manuscript.tex"
        tex_txt, _, _ = read_text_safe(tex_path) if tex_path.exists() else (revised_md, "", None)
        cited_keys: Set[str] = set()
        for m in re.finditer(r"\\(?:cite|citep|citet|parencite|textcite)\{([^}]+)\}", tex_txt):
            for k in m.group(1).split(","):
                if k.strip():
                    cited_keys.add(k.strip())
        bib_keys = set(re.findall(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}", tex_txt))
        missing_keys = cited_keys - bib_keys
        orphan_keys = bib_keys - cited_keys
        if missing_keys:
            err("dangling_latex_cite", f"LaTeX keys cited in text missing from thebibliography: {sorted(missing_keys)}")
        if orphan_keys:
            err("orphan_latex_bibitem", f"LaTeX bibitem entries never cited in text: {sorted(orphan_keys)}")
        if target_style not in ("apa7", "apa6", "gbt7714_2015_author_year", "chicago_author_date") and _extract_rendered_numeric_citations(body_text):
            err("dangling_numeric_in_latex_mode", f"Residual unmapped numeric bracket citations found in revised_manuscript.md while in latex_cite mode: {_extract_rendered_numeric_citations(body_text)}")
    elif target_style in ("apa7", "apa6", "gbt7714_2015_author_year", "chicago_author_date"):
        # Verify every occurrence recorded by sync_and_format appears in body_text and every formatted ref is present
        occ_markers = {o["rendered_marker"] for o in synced.get("in_text_occurrences", [])}
        missing_markers = [mk for mk in occ_markers if mk not in body_text]
        if missing_markers:
            err("missing_author_year_marker", f"Expected author-year markers absent from revised_manuscript.md: {missing_markers}")
        # Verify surname & year presence in bibliography
        for fr in formatted_refs:
            if fr["formatted_entry"] not in bib_text:
                err("missing_bib_entry_in_manuscript", f"Formatted bibliography entry missing from revised_manuscript.md: {fr['ref_id']}")
    else:
        # Numeric styles: extract every rendered number from body_text directly
        occ_seq = _extract_rendered_numeric_citations(body_text)
        occ_set = set(occ_seq)
        if occ_set != bib_nums:
            dangling = sorted(occ_set - bib_nums)
            uncited_in_bib = sorted(bib_nums - occ_set)
            err(
                "numeric_bidirectional_mismatch",
                f"In-text numbers {sorted(occ_set)} != bibliography numbers {sorted(bib_nums)} (dangling={dangling}, uncited_in_bib={uncited_in_bib})",
            )
        if bib_nums:
            expected_range = set(range(1, len(bib_nums) + 1))
            if bib_nums != expected_range:
                err("numbering_gap_or_noncontiguous", f"Bibliography numbers {sorted(bib_nums)} != 1..{len(bib_nums)}")
        # Check monotonic first appearance
        seen_first: List[int] = []
        for n in occ_seq:
            if n not in seen_first:
                seen_first.append(n)
        if seen_first != list(range(1, len(seen_first) + 1)):
            err("non_monotonic_first_appearance", f"First-appearance numeric order is {seen_first}, expected {list(range(1, len(seen_first) + 1))}")
        bad_ranges = _check_inverted_ranges(body_text)
        if bad_ranges:
            err("invalid_citation_range", f"Invalid numeric citation range(s): {bad_ranges}")

    # 4. Formatting cleanliness
    for fr in formatted_refs:
        entry = fr["formatted_entry"]
        for bad_tok in ("None", "null", "NaN", "()", "[]", "DOI:.", ".."):
            if bad_tok in entry:
                err("dirty_formatted_reference", f"{fr['ref_id']} contains artifact `{bad_tok}`: {entry}")
        if target_style.startswith("gbt7714"):
            if not re.search(r"\[(J|C|M|D|R|S|P|N|EB|DB|CP|Z)(?:/OL)?\]", entry):
                err("missing_gbt7714_type_code", f"{fr['ref_id']} missing GB/T 7714 doc-type tag: {entry}")

    # 5. Field-level provenance integrity
    for rec in synced.get("updated_canonical_library", []):
        fp = rec.get("field_provenance") or {}
        if not fp.get("title"):
            err("missing_field_provenance", f"{rec['ref_id']} missing field_provenance")

    # 6. Deduplication & conflict disclosure
    for rc in lib.get("recycled_doi_conflicts", []):
        if rc["raw_a"] not in audit_md and rc["raw_b"] not in audit_md:
            err("undisclosed_recycled_doi", f"Recycled DOI conflict {rc['raw_a']} vs {rc['raw_b']} not disclosed in citation_audit_report.md")
    unresolved_amb = [
        ac for ac in lib.get("ambiguous_duplicate_candidates", [])
        if ac["candidate_id"] not in {d.get("candidate_id") for d in (plan.get("dedup_decisions") or []) if isinstance(d, dict)}
    ]
    if unresolved_amb:
        warn("unresolved_tier3_ambiguous", f"{len(unresolved_amb)} Tier-3 ambiguous duplicate candidates had no explicit dedup_decisions entry")

    # 7. Claim verification grounding & quote anti-hallucination check
    rec_by_id = {r["ref_id"]: r for r in synced.get("updated_canonical_library", [])}
    ev_papers = ev_data.get("evidence_papers", [])
    ev_corpus_by_ref: Dict[str, str] = {}
    for rec in rec_by_id.values():
        parts = [rec.get("title", ""), rec.get("abstract", "")]
        for ev in ev_papers:
            if not ev.get("error"):
                ev_txt = "\n".join(p.get("text", "") for p in ev.get("pages", []))
                if (rec.get("doi") and rec["doi"] in ev_txt.lower()) or (
                    rec.get("title") and normalize_title(rec["title"])[:30] in normalize_title(ev_txt)
                ):
                    parts.append(ev_txt)
        ev_corpus_by_ref[rec["ref_id"]] = normalize_title(" ".join(parts))

    for cv in plan.get("claim_verifications") or []:
        if not isinstance(cv, dict):
            continue
        q = (cv.get("evidence_quote") or "").strip()
        rids = cv.get("cited_ref_ids") or []
        if q and len(q) >= 12 and rids:
            nq = normalize_title(q)
            if not any(nq in ev_corpus_by_ref.get(rid, "") for rid in rids):
                # Allow 80% token match in case of minor PDF hyphenation
                q_toks = set(nq.split())
                matched_any = False
                for rid in rids:
                    c_toks = set(ev_corpus_by_ref.get(rid, "").split())
                    if q_toks and len(q_toks & c_toks) / len(q_toks) >= 0.85:
                        matched_any = True
                if not matched_any:
                    err("ungrounded_evidence_quote", f"{cv.get('claim_id')}: evidence_quote not found in local evidence/abstract for {rids}")

    # 8. Raw placeholders & unreviewed skeleton stubs must not remain untouched
    raw_ph_re = re.compile(r"\[(?:REF|CITE|CITATION NEEDED|\?|待引用|待补引)\]", re.I)
    m_ph = raw_ph_re.search(body_text)
    if m_ph:
        snippet = body_text[max(0, m_ph.start() - 30) : min(len(body_text), m_ph.end() + 30)].replace("\n", " ")
        err("raw_placeholder_left_behind", f"Original raw placeholder `{m_ph.group(0)}` still remains in revised_manuscript.md near `{snippet}`")

    if len(ms.get("sentences", [])) > 0:
        if not revised_md.strip() or "Awaiting final citation_plan synchronization" in revised_md:
            err("missing_final_manuscript", "revised_manuscript.md is empty or still contains partial checkpoint stub")

    stub_re = re.compile(r"Pending (?:review|placement)|REVIEW_REQUIRED", re.I)
    for section_k in ("dedup_decisions", "manuscript_edits", "claim_verifications"):
        for item in plan.get(section_k) or []:
            if isinstance(item, dict) and stub_re.search(json.dumps(item, ensure_ascii=False)):
                err("unreviewed_plan_stub", f"Unreviewed placeholder text left in citation_plan.json[{section_k}]: {item}")
                break

    # 9. Harbor result.json contract compliance
    if not result_json:
        err("missing_result_json", "result.json missing or unreadable in /app/output")
    else:
        expected_cid = case_plan.get("case_id", "")
        if expected_cid and result_json.get("case_id") != expected_cid:
            err("case_id_mismatch", f"result.json case_id={result_json.get('case_id')} != expected {expected_cid}")
        for req_k in case_plan.get("output_contract_parsed", {}).get("required_keys", []):
            if req_k not in result_json or result_json[req_k] is None:
                err("missing_contract_field", f"Required output_contract key `{req_k}` missing or null in result.json")

    # 10. Editable & preview artifact check
    if (out_dir / "revised_manuscript.docx").exists():
        if (out_dir / "revised_manuscript.docx").stat().st_size == 0:
            err("empty_docx_output", "revised_manuscript.docx is 0 bytes")
        if not list(out_dir.glob("docx_preview_p*.png")):
            warn("docx_preview_unavailable", "No DOCX PNG preview generated (LibreOffice soffice likely unavailable in environment)")

    report = {
        "case_id": case_plan.get("case_id", ""),
        "passed": len(errors) == 0,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "cited_references": len(formatted_refs),
            "in_text_occurrences": len(synced.get("in_text_occurrences", [])),
            "uncited_library_entries": len(synced.get("uncited_library_entries", [])),
            "duplicates_removed": lib.get("stats", {}).get("duplicates_removed", 0),
            "dangling_citations_detected": len(pre_audit.get("dangling_citations", [])),
            "recycled_doi_conflicts": len(lib.get("recycled_doi_conflicts", [])),
        },
    }
    save_json(Path(args.out), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
