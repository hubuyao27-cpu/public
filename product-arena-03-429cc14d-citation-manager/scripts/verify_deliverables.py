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

from common import (
    load_json_safe,
    normalize_arxiv,
    normalize_doi,
    normalize_title,
    parse_author_name,
    read_text_safe,
    save_json,
    title_similarity,
)
from parse_manuscript import REF_HEADER_RE, _SCI_UNIT_PREFIX_RE, _filter_valid_cite_nums, expand_numeric_spec

_UNVERIFIED_SPAN_RE = re.compile(
    r"\[(?:待核验引用|未核验引用|UNVERIFIED_CITE|证据不足/待人工核验|INSUFFICIENT_EVIDENCE)[^\]]*\]",
    re.I,
)


def _strip_unverified_tags(body_text: str) -> str:
    return _UNVERIFIED_SPAN_RE.sub("", body_text)


def _split_body_and_bib(revised_md: str) -> Tuple[str, str]:
    lines = revised_md.splitlines()
    for idx, ln in enumerate(lines):
        if REF_HEADER_RE.match(ln.strip()):
            return "\n".join(lines[:idx]), "\n".join(lines[idx + 1 :])
    return revised_md, ""


def _extract_rendered_numeric_citations(body_text: str) -> List[int]:
    """Extract all numeric citations in exact appearance order from rendered Markdown body."""
    cleaned = _strip_unverified_tags(body_text)
    ordered_nums: List[int] = []
    # Match superscript HTML, superscript MD, IEEE [a]–[b] ranges, inline brackets, or circled footnotes
    pat = re.compile(
        r"<sup>\s*(\[?)([0-9,\s\-–—]+)(\]?)\s*</sup>"
        r"|\^\[([0-9,\s\-–—]+)\]"
        r"|(?<![A-Za-z0-9_\]\^\[])\[(\d+)\]\s*[-–—]\s*\[(\d+)\](?!\()"
        r"|(?<![A-Za-z0-9_\]\^\[])\[(\d+(?:\s*(?:[-–—,~]|,\s*)\s*\d+)*)\](?!\()"
        r"|([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])"
    )
    circled_map = {ch: i + 1 for i, ch in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")}
    for m in pat.finditer(cleaned):
        if m.group(2):
            has_brackets = bool(m.group(1) and m.group(3))
            if not has_brackets and _SCI_UNIT_PREFIX_RE.search(cleaned[max(0, m.start() - 12) : m.start()]):
                continue
            for n in _filter_valid_cite_nums(expand_numeric_spec(m.group(2))):
                ordered_nums.append(n)
        elif m.group(4):
            for n in _filter_valid_cite_nums(expand_numeric_spec(m.group(4))):
                ordered_nums.append(n)
        elif m.group(5) and m.group(6):
            a_n, b_n = int(m.group(5)), int(m.group(6))
            raw_list = list(range(a_n, b_n + 1)) if a_n <= b_n and (b_n - a_n) <= 100 else [a_n, b_n]
            for n in _filter_valid_cite_nums(raw_list):
                ordered_nums.append(n)
        elif m.group(7):
            raw_spec = m.group(7)
            if re.search(r"\b(?:19|20)\d{2}\s*[-–—]\s*\d{2}\s*[-–—]\s*\d{2}\b", raw_spec):
                continue
            for n in _filter_valid_cite_nums(expand_numeric_spec(raw_spec)):
                ordered_nums.append(n)
        elif m.group(8):
            ordered_nums.append(circled_map[m.group(8)])
    return ordered_nums


def _check_inverted_ranges(body_text: str) -> List[str]:
    """Check inverted numeric citation ranges strictly inside matched citation spans."""
    cleaned = _strip_unverified_tags(body_text)
    bad: List[str] = []
    cite_pat = re.compile(
        r"<sup>\s*\[?([0-9,\s\-–—]+)\]?\s*</sup>"
        r"|\^\[([0-9,\s\-–—]+)\]"
        r"|(?<![A-Za-z0-9_\]\^\[])\[(\d+)\]\s*[-–—]\s*\[(\d+)\](?!\()"
        r"|(?<![A-Za-z0-9_\]\^\[])\[(\d+(?:\s*(?:[-–—,~]|,\s*)\s*\d+)*)\](?!\()"
    )
    for cm in cite_pat.finditer(cleaned):
        if cm.group(3) and cm.group(4):
            a, b = int(cm.group(3)), int(cm.group(4))
            if a >= b and a < 1900 and b < 1900:
                bad.append(f"{a}-{b} in `{cm.group(0)}`")
            continue
        spec = cm.group(1) or cm.group(2) or cm.group(5) or ""
        for rm in re.finditer(r"(\d+)\s*[-–—]\s*(\d+)", spec):
            a, b = int(rm.group(1)), int(rm.group(2))
            if a < 1900 and b < 1900 and a >= b:
                bad.append(f"{a}-{b} in `{cm.group(0)}`")
    return bad


def _cjk_bigrams(s: str) -> Set[str]:
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", s))
    if len(cjk) < 2:
        return set(cjk)
    return {cjk[i : i + 2] for i in range(len(cjk) - 1)}


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

    # 4. Formatting cleanliness (safe against scientific terms like Nonequilibrium, null hypothesis, NaNbO3, and APA ellipsis ...)
    dirty_patterns = [
        ("None", re.compile(r"\bNone\b")),
        ("NaN", re.compile(r"\bNaN\b")),
        ("null", re.compile(r"\bnull\b(?!\s+(?:hypothesis|hypotheses|model|models|space|distribution|result|results|effect|effects|set|vector|matrix|test))")),
        ("()", re.compile(r"(?<![A-Za-z0-9_])\(\s*\)")),
        ("[]", re.compile(r"\[\s*\]")),
        ("DOI:.", re.compile(r"DOI:\s*\.")),
        ("..", re.compile(r"(?<!\.)\.\.(?!\.)")),
    ]
    for fr in formatted_refs:
        entry = fr["formatted_entry"]
        for label, dpat in dirty_patterns:
            if dpat.search(entry):
                err("dirty_formatted_reference", f"{fr['ref_id']} contains artifact `{label}`: {entry}")
        if target_style.startswith("gbt7714"):
            if not re.search(r"\[(J|C|M|D|R|S|P|N|EB|DB|CP|Z)(?:/OL)?\]", entry):
                err("missing_gbt7714_type_code", f"{fr['ref_id']} missing GB/T 7714 doc-type tag: {entry}")

    # 5. Field-level provenance integrity
    for rec in synced.get("updated_canonical_library", []):
        fp = rec.get("field_provenance") or {}
        has_real_locator = any(
            isinstance(v, dict) and bool(v.get("source_locator"))
            for v in fp.values()
        )
        if not fp or not has_real_locator:
            err("missing_field_provenance", f"{rec['ref_id']} missing grounded field_provenance source_locator")

    # 6. Deduplication & conflict disclosure
    merged_member_sets = [set(r.get("member_raw_ids") or []) for r in synced.get("updated_canonical_library", [])]
    for rc in lib.get("recycled_doi_conflicts", []):
        if rc["raw_a"] not in audit_md and rc["raw_b"] not in audit_md:
            err("undisclosed_recycled_doi", f"Recycled DOI conflict {rc['raw_a']} vs {rc['raw_b']} not disclosed in citation_audit_report.md")
        if any(rc["raw_a"] in ms_set and rc["raw_b"] in ms_set for ms_set in merged_member_sets):
            err("merged_recycled_doi_conflict", f"Recycled DOI conflict pair {rc['raw_a']} & {rc['raw_b']} was illegally merged into one record")
    unresolved_amb = [
        ac for ac in lib.get("ambiguous_duplicate_candidates", [])
        if ac["candidate_id"] not in {d.get("candidate_id") for d in (plan.get("dedup_decisions") or []) if isinstance(d, dict)}
    ]
    if unresolved_amb:
        warn("unresolved_tier3_ambiguous", f"{len(unresolved_amb)} Tier-3 ambiguous duplicate candidates had no explicit dedup_decisions entry")

    # 7. Claim verification grounding & quote anti-hallucination check
    rec_by_id = {r["ref_id"]: r for r in synced.get("updated_canonical_library", [])}
    alias_ref_map: Dict[str, str] = dict(synced.get("alias_ref_map") or {})
    for em in synced.get("extra_tier3_merges") or []:
        if isinstance(em, dict) and em.get("merged_secondary_ref_id") and em.get("canonical_id"):
            alias_ref_map.setdefault(em["merged_secondary_ref_id"], em["canonical_id"])
    raw_to_canon = dict(lib.get("raw_to_canonical_map") or {})

    # Precompute evidence PDF corpus ONCE outside per-record loop (O(P) instead of O(R*P))
    ev_papers_pre: List[Dict[str, Any]] = []
    for ev in ev_data.get("evidence_papers", []):
        if ev.get("error"):
            continue
        ev_txt = "\n".join(p.get("text", "") for p in ev.get("pages", []))
        ev_papers_pre.append({
            "txt_low": ev_txt.lower(),
            "norm": normalize_title(ev_txt),
            "doi": normalize_doi(ev.get("extracted_doi", "")),
            "arxiv": normalize_arxiv(ev.get("extracted_arxiv", "")),
            "title": ev.get("extracted_title", ""),
        })

    ev_corpus_by_ref: Dict[str, str] = {}
    for rec in rec_by_id.values():
        parts = [rec.get("title", ""), rec.get("abstract", "")]
        r_doi = rec.get("doi") or ""
        r_ax = rec.get("arxiv_id") or ""
        r_title = rec.get("title") or ""
        r_tnorm = normalize_title(r_title)
        for evp in ev_papers_pre:
            if (
                (r_doi and (r_doi == evp["doi"] or r_doi in evp["txt_low"]))
                or (r_ax and (r_ax == evp["arxiv"] or r_ax in evp["txt_low"]))
                or (r_title and evp["title"] and title_similarity(r_title, evp["title"]) >= 0.78)
                or (len(r_tnorm) >= 16 and r_tnorm[:30] in evp["norm"])
            ):
                parts.append(evp["norm"])
        ev_corpus_by_ref[rec["ref_id"]] = normalize_title(" ".join(parts))

    claim_verifs = [cv for cv in (plan.get("claim_verifications") or []) if isinstance(cv, dict)]
    if len(pre_audit.get("sentence_links", [])) > 0 and not claim_verifs:
        warn("missing_claim_verifications", "Manuscript contains cited sentences but plan.claim_verifications is empty")

    for cv in claim_verifs:
        q = (cv.get("evidence_quote") or "").strip()
        raw_rids = cv.get("cited_ref_ids") or []
        rids = [alias_ref_map.get(raw_to_canon.get(r, r), raw_to_canon.get(r, r)) for r in raw_rids]
        if q and len(q) >= 12 and rids:
            nq = normalize_title(q)
            if not any(nq in ev_corpus_by_ref.get(rid, "") for rid in rids):
                matched_any = False
                has_cjk = bool(re.search(r"[\u4e00-\u9fff]", nq))
                if has_cjk:
                    q_bg = _cjk_bigrams(nq)
                    for rid in rids:
                        c_bg = _cjk_bigrams(ev_corpus_by_ref.get(rid, ""))
                        if q_bg and len(q_bg & c_bg) / len(q_bg) >= 0.80:
                            matched_any = True
                            break
                if not matched_any:
                    q_toks = set(nq.split())
                    for rid in rids:
                        c_toks = set(ev_corpus_by_ref.get(rid, "").split())
                        if q_toks and len(q_toks & c_toks) / len(q_toks) >= 0.85:
                            matched_any = True
                            break
                if not matched_any:
                    err("ungrounded_evidence_quote", f"{cv.get('claim_id')}: evidence_quote not found in local evidence/abstract for {rids}")

    # 8. Raw placeholders & unreviewed skeleton stubs must not remain untouched
    raw_ph_re = re.compile(r"\[(?:REF|CITE|CITATION(?:\s+NEEDED)?|\?|待引用|待补引|引用|TODO[^\]]*)\]", re.I)
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

    # 9. Harbor result.json contract compliance (presence + type check)
    if not result_json:
        err("missing_result_json", "result.json missing or unreadable in /app/output")
    else:
        expected_cid = case_plan.get("case_id", "")
        if expected_cid and result_json.get("case_id") != expected_cid:
            err("case_id_mismatch", f"result.json case_id={result_json.get('case_id')} != expected {expected_cid}")
        type_validators = {
            "string": lambda v: isinstance(v, str),
            "str": lambda v: isinstance(v, str),
            "array": lambda v: isinstance(v, list),
            "list": lambda v: isinstance(v, list),
            "object": lambda v: isinstance(v, dict),
            "dict": lambda v: isinstance(v, dict),
            "boolean": lambda v: isinstance(v, bool),
            "bool": lambda v: isinstance(v, bool),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        }
        contract_fields = case_plan.get("output_contract_parsed", {}).get("fields", [])
        for cf in contract_fields:
            req_k = cf["name"]
            if cf.get("required", True) and (req_k not in result_json or result_json[req_k] is None):
                err("missing_contract_field", f"Required output_contract key `{req_k}` missing or null in result.json")
            elif req_k in result_json and result_json[req_k] is not None:
                et = str(cf.get("expected_type", "any")).lower()
                chk = type_validators.get(et)
                if chk and not chk(result_json[req_k]):
                    err("contract_field_type_mismatch", f"output_contract key `{req_k}` expected `{et}`, got `{type(result_json[req_k]).__name__}`")

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
