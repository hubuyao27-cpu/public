#!/usr/bin/env python3
"""Deterministic pre-verification of reference records, local evidence PDFs, and manuscript claims.

Checks:
1. Reference record integrity (DOI syntax, arXiv ID, year range, page ranges, missing fields, suspect ghost entries).
2. Local evidence PDF cross-matching (verifies title, year, DOI, and quantitative figures against actual PDF pages).
3. Manuscript <-> Library link check (resolves numeric, BibTeX key, and Author-Year markers; reports dangling refs,
   uncited library entries, numbering gaps, and out-of-order first appearances).
4. Sentence <-> Reference lexical & quantitative overlap audit (flags low-overlap suspect mismatches and unsupported numbers).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    extract_numbers,
    is_valid_doi,
    load_json_safe,
    normalize_arxiv,
    normalize_doi,
    normalize_title,
    parse_author_name,
    save_json,
    title_similarity,
    tokenize_for_similarity,
)


def _match_author_year(ay_str: str, canonical_records: List[Dict[str, Any]]) -> Tuple[List[str], str]:
    """Match an author-year citation expression like 'Smith et al., 2020' or '张伟, 2021a' to REF_IDs."""
    m_yr = re.search(r"((?:19|20)\d{2})([a-z]?)", ay_str)
    if not m_yr:
        return [], "no_year_in_marker"
    yr = int(m_yr.group(1))
    auth_part = ay_str[: m_yr.start()].strip(" ,，&＆")
    auth_part_clean = re.sub(r"\b(?:et\s+al\.?|and)\b|等|和", " ", auth_part, flags=re.I).strip()
    tokens = [t.strip(".,，") for t in re.split(r"[\s,，&＆]+", auth_part_clean) if t.strip(".,，")]
    if not tokens:
        return [], "no_author_in_marker"
    first_tok = tokens[0].lower()

    matched: List[str] = []
    for rec in canonical_records:
        if rec.get("year") != yr:
            continue
        authors = rec.get("authors") or []
        if not authors:
            continue
        p0 = parse_author_name(authors[0])
        fam0 = p0["family"].lower()
        skey0 = p0["surname_key"]
        raw0 = p0["raw"].lower()
        if first_tok == fam0 or first_tok == skey0 or first_tok in raw0 or (len(first_tok) >= 1 and raw0.startswith(first_tok)):
            # If two explicit surnames given (e.g. Smith & Jones), check second surname too if available
            if len(tokens) >= 2 and len(authors) >= 2 and "et al" not in ay_str.lower() and "等" not in ay_str:
                second_tok = tokens[-1].lower()
                surnames_all = {parse_author_name(a)["family"].lower() for a in authors} | {parse_author_name(a)["surname_key"] for a in authors}
                if any(second_tok == sn or second_tok in sn for sn in surnames_all):
                    matched.append(rec["ref_id"])
            else:
                matched.append(rec["ref_id"])
    return matched, "matched" if matched else "unmatched_author_year"


def _match_evidence_pdf(rec: Dict[str, Any], ev_papers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for ev in ev_papers:
        if ev.get("error"):
            continue
        ev_doi = normalize_doi(ev.get("extracted_doi", ""))
        ev_arxiv = normalize_arxiv(ev.get("extracted_arxiv", ""))
        if rec.get("doi") and ev_doi and rec["doi"] == ev_doi:
            return ev
        if rec.get("arxiv_id") and ev_arxiv and rec["arxiv_id"] == ev_arxiv:
            return ev
        if rec.get("title") and ev.get("extracted_title") and title_similarity(rec["title"], ev["extracted_title"]) >= 0.78:
            return ev
        # Search full page 1 text for title or DOI substring
        if ev.get("pages"):
            p1_txt = ev["pages"][0].get("text", "")
            if rec.get("doi") and rec["doi"] in p1_txt.lower():
                return ev
            if rec.get("title") and len(rec["title"]) >= 16 and normalize_title(rec["title"])[:40] in normalize_title(p1_txt):
                return ev
    return None


def _evidence_text_for_record(rec: Dict[str, Any], matched_ev: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """Returns (combined_evidence_text, evidence_tier)."""
    if matched_ev and matched_ev.get("pages"):
        full = "\n".join(f"[p.{p['page_num']}] {p['text']}" for p in matched_ev["pages"])
        return full, "local_pdf_fulltext"
    if rec.get("abstract"):
        return f"{rec.get('title', '')}. {rec.get('abstract', '')}", "library_abstract_and_title"
    if rec.get("title"):
        return rec["title"], "title_metadata_only"
    return "", "none"


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-audit reference library, local PDFs, and manuscript links.")
    parser.add_argument("--case-plan", default="/app/work/case_plan.json")
    parser.add_argument("--library", default="/app/work/library/library_canonical.json")
    parser.add_argument("--evidence", default="/app/work/library/evidence_fulltexts.json")
    parser.add_argument("--manuscript", default="/app/work/manuscript/manuscript_parsed.json")
    parser.add_argument("--out-dir", default="/app/work/verify")
    parser.add_argument("--out-skeleton", default=None, help="Optional path for pre-populated citation_plan_skeleton.json")
    args = parser.parse_args()

    case_plan = load_json_safe(Path(args.case_plan), {})
    lib = load_json_safe(Path(args.library), {})
    ev_data = load_json_safe(Path(args.evidence), {})
    ms = load_json_safe(Path(args.manuscript), {})
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    canonical_records = lib.get("canonical_records", [])
    rec_by_id = {r["ref_id"]: r for r in canonical_records}
    key_map = lib.get("key_to_canonical_map", {})
    num_map = lib.get("inline_num_to_canonical_map", {})
    ev_papers = ev_data.get("evidence_papers", [])

    # 1. Per-reference integrity + local PDF cross-check
    ref_audits: Dict[str, Any] = {}
    evidence_by_ref: Dict[str, Tuple[str, str, Optional[str]]] = {}
    for rec in canonical_records:
        rid = rec["ref_id"]
        matched_ev = _match_evidence_pdf(rec, ev_papers)
        ev_txt, ev_tier = _evidence_text_for_record(rec, matched_ev)
        ev_file = matched_ev["source_file"] if matched_ev else None
        evidence_by_ref[rid] = (ev_txt, ev_tier, ev_file)

        pdf_checks: List[str] = []
        if matched_ev:
            ev_txt_low = ev_txt.lower()
            if rec.get("year") and str(rec["year"]) not in ev_txt_low:
                pdf_checks.append(f"year_mismatch_with_pdf:library={rec['year']}")
            if rec.get("title") and matched_ev.get("extracted_title"):
                tsim = title_similarity(rec["title"], matched_ev["extracted_title"])
                if tsim < 0.75:
                    pdf_checks.append(f"title_partial_mismatch_with_pdf:sim={tsim}")
            if rec.get("doi") and matched_ev.get("extracted_doi") and rec["doi"] != normalize_doi(matched_ev["extracted_doi"]):
                pdf_checks.append(f"doi_mismatch_with_pdf:lib={rec['doi']} vs pdf={matched_ev['extracted_doi']}")

        ref_audits[rid] = {
            "ref_id": rid,
            "doi_verification_status": rec.get("doi_verification_status"),
            "evidence_tier": ev_tier,
            "matched_evidence_file": ev_file,
            "missing_required_fields": rec.get("missing_required_fields", []),
            "integrity_flags": rec.get("integrity_flags", []) + pdf_checks,
        }

    # 2. Resolve every in-text citation in manuscript
    dangling_citations: List[Dict[str, Any]] = []
    ambiguous_marker_links: List[Dict[str, Any]] = []
    sentence_links: List[Dict[str, Any]] = []
    cited_ref_ids_order: List[str] = []
    seen_orig_nums: List[int] = []
    out_of_order_events: List[Dict[str, Any]] = []

    for sent in ms.get("sentences", []):
        sid = sent["sentence_id"]
        resolved_ids: List[str] = []
        unresolved_tokens: List[str] = []

        for cite in sent.get("citations", []):
            kind = cite["kind"]
            if kind == "placeholder":
                continue

            if kind in ("numeric_bracket", "numeric_superscript_html", "numeric_superscript_md", "numeric_superscript_unicode"):
                for num in cite.get("nums", []):
                    seen_orig_nums.append(num)
                    cid = num_map.get(str(num)) or key_map.get(str(num))
                    if cid:
                        resolved_ids.append(cid)
                        if cid not in cited_ref_ids_order:
                            # Check monotonic first appearance
                            if len(cited_ref_ids_order) > 0:
                                expected_next = len(cited_ref_ids_order) + 1
                                if num != expected_next:
                                    out_of_order_events.append({
                                        "sentence_id": sid,
                                        "cited_num": num,
                                        "expected_sequential_num": expected_next,
                                        "ref_id": cid,
                                    })
                            elif num != 1:
                                out_of_order_events.append({
                                    "sentence_id": sid,
                                    "cited_num": num,
                                    "expected_sequential_num": 1,
                                    "ref_id": cid,
                                })
                            cited_ref_ids_order.append(cid)
                    else:
                        unresolved_tokens.append(f"[{num}]")
                        dangling_citations.append({"sentence_id": sid, "raw_marker": cite["raw"], "missing_token": f"[{num}]", "reason": "number_not_in_reference_list"})

            elif kind == "latex_cite":
                for k in cite.get("keys", []):
                    cid = key_map.get(k)
                    if not cid:
                        # Case-insensitive fallback
                        for km, rv in key_map.items():
                            if km.lower() == k.lower():
                                cid = rv
                                break
                    if cid:
                        resolved_ids.append(cid)
                        if cid not in cited_ref_ids_order:
                            cited_ref_ids_order.append(cid)
                    else:
                        unresolved_tokens.append(k)
                        dangling_citations.append({"sentence_id": sid, "raw_marker": cite["raw"], "missing_token": k, "reason": "bibtex_key_not_found"})

            elif kind in ("author_year_paren", "author_year_narrative"):
                for ay in cite.get("author_years", []):
                    matches, reason = _match_author_year(ay, canonical_records)
                    if len(matches) == 1:
                        cid = matches[0]
                        resolved_ids.append(cid)
                        if cid not in cited_ref_ids_order:
                            cited_ref_ids_order.append(cid)
                    elif len(matches) > 1:
                        # Check suffix letter a/b or disambiguate
                        ambiguous_marker_links.append({
                            "sentence_id": sid,
                            "raw_marker": ay,
                            "candidate_ref_ids": matches,
                            "reason": "multiple_same_author_year_records",
                        })
                        # Keep first provisionally but flag for citation_plan disambiguation
                        resolved_ids.extend(matches)
                    else:
                        unresolved_tokens.append(ay)
                        dangling_citations.append({"sentence_id": sid, "raw_marker": cite["raw"], "missing_token": ay, "reason": reason})

        # Deduplicate resolved_ids preserving order
        uniq_ids: List[str] = []
        for x in resolved_ids:
            if x not in uniq_ids:
                uniq_ids.append(x)

        # Lexical & numeric pre-audit against cited references
        if uniq_ids or unresolved_tokens:
            sent_tokens = set(tokenize_for_similarity(sent["text"]))
            sent_nums = [n for n in extract_numbers(sent["text"]) if len(n) >= 2 or "%" in n or "." in n]
            # Remove bracket citation numbers themselves from sent_nums heuristic
            cite_raw_nums = {str(n) for c in sent.get("citations", []) for n in c.get("nums", [])}
            claim_nums = [n for n in sent_nums if n not in cite_raw_nums]

            per_ref_checks = []
            for cid in uniq_ids:
                ev_txt, ev_tier, ev_file = evidence_by_ref.get(cid, ("", "none", None))
                ev_tokens = set(tokenize_for_similarity(ev_txt))
                overlap_cnt = len(sent_tokens & ev_tokens)
                overlap_ratio = round(overlap_cnt / max(1, len(sent_tokens)), 3) if sent_tokens else 0.0
                missing_nums = [n for n in claim_nums if n.rstrip("%") not in ev_txt]

                pre_status = "needs_agent_judgment"
                if ev_tier == "title_metadata_only":
                    pre_status = "title_metadata_only_limited_evidence"
                elif ev_tier in ("local_pdf_fulltext", "library_abstract_and_title"):
                    if overlap_cnt == 0 and len(sent_tokens) >= 4:
                        pre_status = "low_overlap_suspect_mismatch"
                    elif missing_nums and ev_tier == "local_pdf_fulltext":
                        pre_status = "numeric_not_found_in_local_pdf"
                    else:
                        pre_status = "plausible_evidence_overlap"

                per_ref_checks.append({
                    "ref_id": cid,
                    "evidence_tier": ev_tier,
                    "evidence_file": ev_file,
                    "lexical_overlap_count": overlap_cnt,
                    "lexical_overlap_ratio": overlap_ratio,
                    "claim_numbers": claim_nums,
                    "numbers_missing_in_evidence": missing_nums,
                    "pre_status": pre_status,
                })

            sentence_links.append({
                "sentence_id": sid,
                "section": sent["section"],
                "block_type": sent["block_type"],
                "text": sent["text"],
                "resolved_ref_ids": uniq_ids,
                "unresolved_tokens": unresolved_tokens,
                "per_ref_checks": per_ref_checks,
            })

    # Detect numbering gaps in original inline reference list
    orig_nums_set = {int(k) for k in num_map.keys() if k.isdigit()}
    numbering_gaps = []
    if orig_nums_set:
        mx = max(orig_nums_set)
        numbering_gaps = [n for n in range(1, mx + 1) if n not in orig_nums_set]

    cited_set = set(cited_ref_ids_order)
    uncited_entries = [
        {"ref_id": r["ref_id"], "title": r["title"], "authors": r["authors"][:2], "year": r["year"]}
        for r in canonical_records
        if r["ref_id"] not in cited_set
    ]

    pre_audit = {
        "case_id": case_plan.get("case_id", ""),
        "ref_audits": ref_audits,
        "sentence_links": sentence_links,
        "dangling_citations": dangling_citations,
        "ambiguous_marker_links": ambiguous_marker_links,
        "uncited_library_entries": uncited_entries,
        "out_of_order_numbering": out_of_order_events,
        "numbering_gaps_in_original_list": numbering_gaps,
        "stats": {
            "canonical_refs": len(canonical_records),
            "cited_canonical_refs": len(cited_set),
            "uncited_library_entries": len(uncited_entries),
            "dangling_citations": len(dangling_citations),
            "ambiguous_marker_links": len(ambiguous_marker_links),
            "out_of_order_numbering_events": len(out_of_order_events),
            "numbering_gaps": len(numbering_gaps),
            "local_pdf_matched_refs": sum(1 for a in ref_audits.values() if a["evidence_tier"] == "local_pdf_fulltext"),
            "suspect_mismatches": sum(
                1 for sl in sentence_links for pc in sl["per_ref_checks"]
                if pc["pre_status"] in ("low_overlap_suspect_mismatch", "numeric_not_found_in_local_pdf")
            ),
        },
    }
    save_json(out_dir / "pre_audit.json", pre_audit)

    # Compact worklist Markdown for Step 4
    wlines = [
        f"# Verification & Citation Action Worklist (`{pre_audit['case_id']}`)",
        "",
        f"- **Canonical Refs**: {pre_audit['stats']['canonical_refs']} | **Currently Cited**: {pre_audit['stats']['cited_canonical_refs']} | **Uncited**: {pre_audit['stats']['uncited_library_entries']}",
        f"- **Dangling Citations**: {pre_audit['stats']['dangling_citations']} | **Out-of-order events**: {pre_audit['stats']['out_of_order_numbering_events']} | **Local PDFs matched**: {pre_audit['stats']['local_pdf_matched_refs']} | **Suspect claim mismatches**: {pre_audit['stats']['suspect_mismatches']}",
        "",
    ]
    if dangling_citations:
        wlines.append("## 1. Dangling / Unresolved Citations")
        for dc in dangling_citations:
            wlines.append(f"- `{dc['sentence_id']}`: `{dc['missing_token']}` ({dc['reason']})")
        wlines.append("")
    if ambiguous_marker_links:
        wlines.append("## 2. Ambiguous Author-Year Marker Links (Must Disambiguate)")
        for am in ambiguous_marker_links:
            wlines.append(f"- `{am['sentence_id']}` (`{am['raw_marker']}`): candidates {am['candidate_ref_ids']}")
        wlines.append("")
    if out_of_order_events:
        wlines.append("## 3. Out-of-Order Numbering Detected")
        for oo in out_of_order_events:
            wlines.append(f"- `{oo['sentence_id']}`: cited `[{oo['cited_num']}]` (`{oo['ref_id']}`), expected `[{oo['expected_sequential_num']}]`")
        wlines.append("")
    if uncited_entries:
        wlines.append("## 4. Uncited Library Entries (Check if needed for placeholders or paragraph-end splits)")
        for ue in uncited_entries:
            wlines.append(f"- `{ue['ref_id']}` ({ue['year']}): {ue['title']}")
        wlines.append("")
    wlines.append("## 5. Sentence-to-Reference Pre-Audit")
    for sl in sentence_links:
        wlines.append(f"- **`{sl['sentence_id']}`** ({sl['section']} | `{sl['block_type']}`): {sl['text']}")
        for pc in sl["per_ref_checks"]:
            wlines.append(
                f"  - `{pc['ref_id']}` -> `{pc['pre_status']}` (evidence={pc['evidence_tier']}, overlap={pc['lexical_overlap_count']}, missing_nums={pc['numbers_missing_in_evidence']})"
            )

    (out_dir / "verification_worklist.md").write_text("\n".join(wlines) + "\n", encoding="utf-8")

    # Auto-emit ready-to-edit citation_plan_skeleton.json to minimize LLM JSON boilerplate
    out_skel = Path(args.out_skeleton) if args.out_skeleton else (out_dir.parent / "citation_plan_skeleton.json")
    sigs = case_plan.get("signals") or {}
    amb_cands = lib.get("ambiguous_duplicate_candidates") or []
    dedup_stubs = [
        {
            "candidate_id": ac["candidate_id"],
            "record_ids": [ac["raw_a"]["raw_id"], ac["raw_b"]["raw_id"]],
            "decision": "keep_separate",
            "rationale": f"Pending review of Tier-3 candidate ({ac.get('rule', '')})",
        }
        for ac in amb_cands
    ]

    flagged_sids: set[str] = set()
    for ph in (ms.get("signals") or {}).get("placeholders") or []:
        flagged_sids.add(ph["sentence_id"])
    for uc in (ms.get("signals") or {}).get("unreferenced_claim_candidates") or []:
        flagged_sids.add(uc["sentence_id"])
    for pc in (ms.get("signals") or {}).get("paragraph_end_clusters") or []:
        flagged_sids.add(pc["last_sentence_id"])
        flagged_sids.update(pc.get("earlier_uncited_claim_sentences") or [])
    for dc in dangling_citations:
        flagged_sids.add(dc["sentence_id"])

    edit_stubs = []
    for sent in ms.get("sentences", []):
        if sent["sentence_id"] in flagged_sids:
            edit_stubs.append({
                "sentence_id": sent["sentence_id"],
                "action": "keep",
                "revised_sentence_with_tokens": sent["text"],
                "anchor_rationale": "Pending placement/placeholder review",
            })

    claim_stubs = []
    for ci, sl in enumerate(sentence_links, start=1):
        rids = sl.get("resolved_ref_ids") or []
        checks = sl.get("per_ref_checks") or []
        verdict = "title_metadata_only_unverified_claim"
        ev_loc = "title_only"
        if sl.get("unresolved_tokens"):
            verdict = "fabricated_or_dangling_ref"
            ev_loc = "unresolved_in_library"
        elif checks:
            tiers = [c.get("evidence_tier") for c in checks]
            if any(c.get("pre_status") == "low_overlap_suspect_mismatch" for c in checks):
                verdict = "claim_mismatch"
            elif "local_pdf_fulltext" in tiers:
                verdict = "supported_by_local_fulltext"
            elif "library_abstract_and_title" in tiers:
                verdict = "supported_by_abstract"
            ev_loc = checks[0].get("evidence_file") or checks[0].get("evidence_tier") or "library_canonical.json"
        claim_stubs.append({
            "claim_id": f"CLM_{ci:03d}",
            "sentence_id": sl["sentence_id"],
            "section": sl["section"],
            "claim_excerpt": sl["text"][:180],
            "cited_ref_ids": rids,
            "verdict": verdict,
            "evidence_locator": ev_loc,
            "evidence_quote": "",
            "mismatch_detail": "",
            "needs_human_confirmation": bool(sl.get("unresolved_tokens")) or verdict == "claim_mismatch",
        })

    skeleton = {
        "case_id": case_plan.get("case_id", ""),
        "target_style": sigs.get("target_style", "gbt7714_2015_numeric"),
        "in_text_format": sigs.get("in_text_format", "bracket"),
        "actions_performed": sigs.get("requested_actions") or [],
        "dedup_decisions": dedup_stubs,
        "metadata_resolutions": [],
        "manuscript_edits": edit_stubs,
        "claim_verifications": claim_stubs,
        "uncertainty": [],
        "human_confirmation_items": [],
        "contract_overrides": {},
    }
    save_json(out_skel, skeleton)
    print(json.dumps({**pre_audit["stats"], "skeleton": str(out_skel)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
