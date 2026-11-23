#!/usr/bin/env python3
"""Deterministic citation synchronization, renumbering, and style conversion engine."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import load_json_safe, read_text_safe, save_json
from style_formatters import (
    compute_author_year_suffixes,
    export_bibtex,
    export_csl_json,
    export_csv,
    export_ris,
    format_in_text_citation,
    format_reference_entry,
    sort_records_for_style,
)

CITE_TOKEN_RE = re.compile(r"\[\[(CITE|CITE_NARR):([^\]]+)\]\]")


def _apply_dedup_decisions(
    lib: Dict[str, Any],
    decisions: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    records = [dict(r) for r in lib.get("canonical_records", [])]
    by_id = {r["ref_id"]: r for r in records}
    raw_to_canon = dict(lib.get("raw_to_canonical_map", {}))
    alias_ref_map: Dict[str, str] = {}
    extra_merges: List[Dict[str, Any]] = []
    change_records: List[Dict[str, Any]] = []

    for dec in decisions:
        if not isinstance(dec, dict):
            continue
        action = dec.get("decision")
        rids = dec.get("record_ids") or []
        if action != "merge" or len(rids) < 2:
            continue
        canon_ids: List[str] = []
        for x in rids:
            cid = x if x in by_id else raw_to_canon.get(x)
            if cid and cid not in canon_ids:
                canon_ids.append(cid)
        if len(canon_ids) < 2:
            continue
        primary = dec.get("canonical_primary")
        if primary not in canon_ids:
            primary = canon_ids[0]
        prim_rec = by_id[primary]
        for sec_id in canon_ids:
            if sec_id == primary or sec_id not in by_id:
                continue
            sec_rec = by_id.pop(sec_id)
            alias_ref_map[sec_id] = primary
            prim_rec["member_raw_ids"] = list(dict.fromkeys(prim_rec.get("member_raw_ids", []) + sec_rec.get("member_raw_ids", [])))
            prim_rec["source_keys"] = list(dict.fromkeys(prim_rec.get("source_keys", []) + sec_rec.get("source_keys", [])))
            prim_rec["original_list_numbers"] = list(dict.fromkeys(prim_rec.get("original_list_numbers", []) + sec_rec.get("original_list_numbers", [])))
            for fld in ("doi", "arxiv_id", "volume", "issue", "pages", "url", "publisher", "city", "abstract", "title_translated"):
                if not prim_rec.get(fld) and sec_rec.get(fld):
                    prim_rec[fld] = sec_rec[fld]
                    fp = prim_rec.setdefault("field_provenance", {})
                    sec_fp = (sec_rec.get("field_provenance") or {}).get(fld)
                    if sec_fp:
                        fp[fld] = sec_fp
            extra_merges.append({
                "canonical_id": primary,
                "merged_secondary_ref_id": sec_id,
                "rationale": dec.get("rationale", "agent_confirmed_tier3_merge"),
            })
            change_records.append({
                "change_id": f"CHG_DEDUP_{len(change_records) + 1:03d}",
                "target": f"{primary} <- {sec_id}",
                "action": "merge_ambiguous_duplicate",
                "before": f"Separate records {primary} & {sec_id}",
                "after": f"Merged into {primary}",
                "basis": dec.get("rationale", "tier3_agent_decision"),
            })

    return list(by_id.values()), alias_ref_map, extra_merges, change_records


def _apply_metadata_resolutions(
    records: List[Dict[str, Any]],
    resolutions: List[Dict[str, Any]],
    style_rules: Dict[str, Any],
) -> List[Dict[str, Any]]:
    by_id = {r["ref_id"]: r for r in records}
    changes: List[Dict[str, Any]] = []
    for res in resolutions:
        if not isinstance(res, dict):
            continue
        rid = res.get("ref_id")
        fld = res.get("field")
        val = res.get("value")
        if not rid or rid not in by_id or not fld or val in (None, ""):
            continue
        rec = by_id[rid]
        old_val = rec.get(fld)
        rec[fld] = val
        fp = rec.setdefault("field_provenance", {})
        fp[fld] = {
            "value": val,
            "source_record_id": "agent_evidence_resolution",
            "source_file": str(res.get("source_locator", "")).split(":")[0],
            "source_locator": str(res.get("source_locator", "")),
            "status": "resolved_from_evidence",
            "alternate_values": fp.get(fld, {}).get("alternate_values", []),
        }
        changes.append({
            "change_id": f"CHG_META_{len(changes) + 1:03d}",
            "target": f"{rid}.{fld}",
            "action": "resolve_or_supplement_field",
            "before": str(old_val),
            "after": str(val),
            "basis": f"{res.get('source_locator', '')} ({res.get('rationale', '')})",
        })

    req_map = style_rules.get("required_fields_by_doc_type", {})
    for rec in records:
        dtype = rec.get("doc_type", "other")
        req = req_map.get(dtype, ["authors", "title", "year"])
        rec["missing_required_fields"] = [f for f in req if not rec.get(f)]
    return changes


def _auto_tokenize_unmodified_sentence(
    sent: Dict[str, Any],
    num_map: Dict[str, str],
    key_map: Dict[str, str],
    ay_lookup: Dict[str, List[str]],
    alias_ref_map: Dict[str, str],
    lang: str,
) -> str:
    text = sent["text"]
    cites = sent.get("citations") or []
    if not cites:
        return text

    out = text
    for cite in sorted(cites, key=lambda c: c["span"][0], reverse=True):
        a, b = cite["span"]
        kind = cite["kind"]
        if kind == "placeholder":
            tag = "[证据不足/待人工核验]" if lang == "zh" else "[INSUFFICIENT_EVIDENCE]"
            out = out[:a] + tag + out[b:]
            continue

        resolved: List[str] = []
        unresolved: List[str] = []
        if kind in ("numeric_bracket", "numeric_superscript_html", "numeric_superscript_md", "numeric_superscript_unicode"):
            for n in cite.get("nums", []):
                cid = num_map.get(str(n)) or key_map.get(str(n))
                if cid:
                    cid = alias_ref_map.get(cid, cid)
                    resolved.append(cid)
                else:
                    unresolved.append(f"[{n}]")
        elif kind == "latex_cite":
            for k in cite.get("keys", []):
                cid = key_map.get(k)
                if not cid:
                    for km, rv in key_map.items():
                        if km.lower() == k.lower():
                            cid = rv
                            break
                if cid:
                    resolved.append(alias_ref_map.get(cid, cid))
                else:
                    unresolved.append(k)
        elif kind in ("author_year_paren", "author_year_narrative"):
            for ay in cite.get("author_years", []):
                cand = ay_lookup.get(ay)
                if cand:
                    resolved.append(alias_ref_map.get(cand[0], cand[0]))
                else:
                    unresolved.append(ay)

        uniq = list(dict.fromkeys(resolved))
        if uniq and not unresolved:
            tok_kind = "CITE_NARR" if kind == "author_year_narrative" else "CITE"
            repl = f"[[{tok_kind}:{','.join(uniq)}]]"
        elif uniq and unresolved:
            un_tag = f"[待核验引用:{','.join(unresolved)}]" if lang == "zh" else f"[UNVERIFIED_CITE:{','.join(unresolved)}]"
            repl = f"[[CITE:{','.join(uniq)}]]{un_tag}"
        else:
            repl = f"[待核验引用:{cite['raw']}]" if lang == "zh" else f"[UNVERIFIED_CITE:{cite['raw']}]"
        out = out[:a] + repl + out[b:]
    return out


def _reorder_blocks(blocks: List[Dict[str, Any]], section_order_override: Optional[List[str]]) -> List[Dict[str, Any]]:
    if not section_order_override:
        return blocks
    by_sec: Dict[str, List[Dict[str, Any]]] = {}
    orig_order: List[str] = []
    for blk in blocks:
        sec = blk.get("section", "Preamble")
        if sec not in by_sec:
            by_sec[sec] = []
            orig_order.append(sec)
        by_sec[sec].append(blk)

    ordered_secs: List[str] = []
    if "Preamble" in by_sec and "Preamble" not in section_order_override:
        ordered_secs.append("Preamble")
    for s in section_order_override:
        matched = [os for os in orig_order if os == s or s.lower() in os.lower()]
        for m in matched:
            if m not in ordered_secs:
                ordered_secs.append(m)
    for os in orig_order:
        if os not in ordered_secs:
            ordered_secs.append(os)

    out: List[Dict[str, Any]] = []
    for s in ordered_secs:
        out.extend(by_sec[s])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize citations, renumber deterministically, and convert formats.")
    parser.add_argument("--case-plan", default="/app/work/case_plan.json")
    parser.add_argument("--library", default="/app/work/library/library_canonical.json")
    parser.add_argument("--manuscript", default="/app/work/manuscript/manuscript_parsed.json")
    parser.add_argument("--pre-audit", default="/app/work/verify/pre_audit.json")
    parser.add_argument("--plan", default="/app/work/citation_plan.json")
    parser.add_argument("--style-rules", required=True)
    parser.add_argument("--out-dir", default="/app/work/synced")
    args = parser.parse_args()

    case_plan = load_json_safe(Path(args.case_plan), {})
    lib = load_json_safe(Path(args.library), {})
    ms = load_json_safe(Path(args.manuscript), {})
    pre_audit = load_json_safe(Path(args.pre_audit), {})
    plan = load_json_safe(Path(args.plan), {}) if Path(args.plan).exists() else {}
    style_rules = load_json_safe(Path(args.style_rules), {})
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    target_style = plan.get("target_style") or case_plan.get("signals", {}).get("target_style") or "gbt7714_2015_numeric"
    style_cfg = style_rules.get("supported_styles", {}).get(target_style, {})
    in_text_format = plan.get("in_text_format") or case_plan.get("signals", {}).get("in_text_format") or style_cfg.get("default_in_text_format", "bracket")
    lang = case_plan.get("signals", {}).get("language", "zh")

    records, alias_ref_map, extra_merges, dedup_changes = _apply_dedup_decisions(
        lib, plan.get("dedup_decisions") or []
    )
    meta_changes = _apply_metadata_resolutions(
        records, plan.get("metadata_resolutions") or [], style_rules
    )
    rec_by_id = {r["ref_id"]: r for r in records}

    num_map = dict(lib.get("inline_num_to_canonical_map", {}))
    key_map = dict(lib.get("key_to_canonical_map", {}))
    ay_lookup: Dict[str, List[str]] = {}
    for sl in pre_audit.get("sentence_links", []):
        sid = sl["sentence_id"]
        orig_sent = next((s for s in ms.get("sentences", []) if s["sentence_id"] == sid), None)
        if orig_sent:
            for c in orig_sent.get("citations", []):
                if c["kind"] in ("author_year_paren", "author_year_narrative") and len(c.get("author_years", [])) == 1 and len(sl.get("resolved_ref_ids", [])) >= 1:
                    ay_lookup[c["author_years"][0]] = sl["resolved_ref_ids"]

    edits_by_sid: Dict[str, Dict[str, Any]] = {}
    for ed in plan.get("manuscript_edits") or []:
        if isinstance(ed, dict) and ed.get("sentence_id"):
            edits_by_sid[ed["sentence_id"]] = ed

    blocks = _reorder_blocks(ms.get("blocks", []), plan.get("section_order_override"))
    sent_by_id = {s["sentence_id"]: s for s in ms.get("sentences", [])}

    tokenized_blocks: List[Dict[str, Any]] = []
    first_appearance_ids: List[str] = []
    sent_changes: List[Dict[str, Any]] = []

    def _register_tokens(tok_text: str) -> str:
        def _norm_tok(m: re.Match) -> str:
            kind = m.group(1)
            raw_ids = [x.strip() for x in m.group(2).split(",") if x.strip()]
            mapped: List[str] = []
            for rid in raw_ids:
                canon = alias_ref_map.get(rid, rid)
                if canon in rec_by_id:
                    mapped.append(canon)
                    if canon not in first_appearance_ids:
                        first_appearance_ids.append(canon)
            if not mapped:
                tag = f"[待核验引用:{','.join(raw_ids)}]" if lang == "zh" else f"[UNVERIFIED_CITE:{','.join(raw_ids)}]"
                return tag
            return f"[[{kind}:{','.join(mapped)}]]"
        return CITE_TOKEN_RE.sub(_norm_tok, tok_text)

    for blk in blocks:
        if blk["block_type"] == "heading":
            tokenized_blocks.append({"block": blk, "tokenized_sentences": [], "rendered_text": blk["text"]})
            continue
        tok_sents: List[Dict[str, Any]] = []
        for sid in blk.get("sentence_ids", []):
            s_obj = sent_by_id.get(sid)
            if not s_obj:
                continue
            ed = edits_by_sid.get(sid)
            if ed and ed.get("revised_sentence_with_tokens"):
                tok_str = _register_tokens(str(ed["revised_sentence_with_tokens"]))
                if tok_str != s_obj["text"]:
                    sent_changes.append({
                        "change_id": f"CHG_EDIT_{len(sent_changes) + 1:03d}",
                        "target": sid,
                        "action": ed.get("action", "edit_citation"),
                        "before": s_obj["text"],
                        "after": tok_str,
                        "basis": ed.get("anchor_rationale", ""),
                    })
            else:
                tok_str = _auto_tokenize_unmodified_sentence(s_obj, num_map, key_map, ay_lookup, alias_ref_map, lang)
                tok_str = _register_tokens(tok_str)
            tok_sents.append({"sentence": s_obj, "tokenized_text": tok_str})
        tokenized_blocks.append({"block": blk, "tokenized_sentences": tok_sents})

    sorted_cited = sort_records_for_style(records, target_style, first_appearance_ids)
    year_suffixes = compute_author_year_suffixes(sorted_cited)
    assigned_numbers: Dict[str, int] = {r["ref_id"]: idx for idx, r in enumerate(sorted_cited, start=1)}

    in_text_occurrences: List[Dict[str, Any]] = []
    first_seen_meta: Dict[str, Dict[str, Any]] = {}
    rendered_blocks: List[Dict[str, Any]] = []

    for tb in tokenized_blocks:
        blk = tb["block"]
        if blk["block_type"] == "heading":
            rendered_blocks.append({"block_type": "heading", "section": blk["section"], "text": blk["text"]})
            continue
        rendered_sents: List[str] = []
        for item in tb["tokenized_sentences"]:
            s_obj = item["sentence"]
            tok_str = item["tokenized_text"]

            def _render_match(m: re.Match) -> str:
                kind = m.group(1)
                rids = [x.strip() for x in m.group(2).split(",") if x.strip() and x.strip() in rec_by_id]
                entries = [(rec_by_id[rid], assigned_numbers[rid], year_suffixes.get(rid, "")) for rid in rids]
                rendered = format_in_text_citation(entries, target_style, in_text_format, narrative=(kind == "CITE_NARR"))
                in_text_occurrences.append({
                    "sentence_id": s_obj["sentence_id"],
                    "block_type": s_obj["block_type"],
                    "section": s_obj["section"],
                    "ref_ids": rids,
                    "assigned_numbers": [assigned_numbers[r] for r in rids],
                    "rendered_marker": rendered,
                })
                for rid in rids:
                    if rid not in first_seen_meta:
                        first_seen_meta[rid] = {
                            "sentence_id": s_obj["sentence_id"],
                            "section": s_obj["section"],
                            "block_type": s_obj["block_type"],
                        }
                return rendered

            r_sent = CITE_TOKEN_RE.sub(_render_match, tok_str)
            r_sent = re.sub(r"\s+([。，；：！？])", r"\1", r_sent)
            rendered_sents.append(r_sent)

        joined_para = "\n".join(rendered_sents) if blk["block_type"] == "table_cell" else " ".join(rendered_sents)
        if lang == "zh" and blk["block_type"] != "table_cell":
            joined_para = re.sub(r"(?<=[。！？；：”’>）\]])\s+(?=[\u4e00-\u9fff])", "", joined_para)
        rendered_blocks.append({
            "block_type": blk["block_type"],
            "section": blk["section"],
            "text": joined_para,
        })

    formatted_refs: List[Dict[str, Any]] = []
    for rec in sorted_cited:
        rid = rec["ref_id"]
        num = assigned_numbers[rid]
        ys = year_suffixes.get(rid, "")
        txt_ref = format_reference_entry(rec, target_style, number=num, year_suffix=ys)
        formatted_refs.append({
            "ref_id": rid,
            "assigned_number": num,
            "year_suffix": ys,
            "formatted_entry": txt_ref,
        })

    renumbering_map: List[Dict[str, Any]] = []
    for rec in sorted_cited:
        rid = rec["ref_id"]
        num = assigned_numbers[rid]
        ys = year_suffixes.get(rid, "")
        example_marker = format_in_text_citation([(rec, num, ys)], target_style, in_text_format)
        fsm = first_seen_meta.get(rid, {})
        renumbering_map.append({
            "ref_id": rid,
            "old_inline_numbers": rec.get("original_list_numbers", []),
            "old_source_keys": rec.get("source_keys", []),
            "new_number": num,
            "year_suffix": ys,
            "new_in_text_marker": example_marker,
            "first_appeared_sentence": fsm.get("sentence_id"),
            "first_appeared_section": fsm.get("section"),
            "first_appeared_block_type": fsm.get("block_type"),
            "title": rec.get("title", ""),
        })

    cited_set = set(assigned_numbers.keys())
    uncited_entries = [
        {
            "ref_id": r["ref_id"],
            "title": r.get("title", ""),
            "authors": r.get("authors", [])[:3],
            "year": r.get("year"),
            "source_keys": r.get("source_keys", []),
            "reason": "not_cited_in_revised_manuscript_excluded_from_bibliography",
        }
        for r in records
        if r["ref_id"] not in cited_set
    ]

    body_lines: List[str] = []
    footnotes_out: List[str] = []
    for rb in rendered_blocks:
        if rb["block_type"] == "footnote":
            footnotes_out.append(rb["text"])
        else:
            body_lines.append(rb["text"])
            body_lines.append("")

    ref_heading = "## 参考文献" if lang == "zh" else "## References"
    bib_lines = [ref_heading, ""] + [fr["formatted_entry"] for fr in formatted_refs]
    if not formatted_refs:
        bib_lines.append("*No verified references cited.*")
    if footnotes_out:
        fn_heading = "## 脚注" if lang == "zh" else "## Footnotes"
        full_md = "\n".join(body_lines + [fn_heading, ""] + footnotes_out + [""] + bib_lines).strip() + "\n"
    else:
        full_md = "\n".join(body_lines + bib_lines).strip() + "\n"

    (out_dir / "revised_manuscript.md").write_text(full_md, encoding="utf-8")

    req_fmts = case_plan.get("signals", {}).get("requested_output_formats", [])
    if ms.get("primary_format") == "latex" or "tex" in req_fmts:
        input_dir = Path(case_plan.get("input_dir", "/input"))
        tex_files = [f for f in case_plan.get("files", []) if f.get("role") == "manuscript" and f.get("ext") == ".tex"]
        orig_tex = ""
        if tex_files:
            orig_tex, _, _ = read_text_safe(input_dir / tex_files[0]["rel_path"])
        cleaned_tex = re.sub(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", "", orig_tex, flags=re.S)
        tex_body = []
        for rb in rendered_blocks:
            if rb["block_type"] == "heading":
                ht = rb["text"].lstrip("#").strip()
                if not ht.startswith("\\"):
                    tex_body.append(f"\\section{{{ht}}}")
                else:
                    tex_body.append(ht)
            else:
                tex_body.append(rb["text"])
            tex_body.append("")
        bib_tex = ["\\begin{thebibliography}{99}"]
        for fr in formatted_refs:
            rec = rec_by_id[fr["ref_id"]]
            k = (rec.get("source_keys") or [fr["ref_id"]])[0]
            bib_tex.append(f"\\bibitem{{{k}}} {fr['formatted_entry']}")
        bib_tex.append("\\end{thebibliography}")
        if "\\begin{document}" in cleaned_tex:
            preamble = cleaned_tex[: cleaned_tex.find("\\begin{document}") + len("\\begin{document}")]
            full_tex = preamble + "\n\n" + "\n".join(tex_body + bib_tex) + "\n\n\\end{document}\n"
        else:
            full_tex = "\\documentclass{article}\n\\usepackage[utf8]{inputenc}\n\\begin{document}\n\n" + "\n".join(tex_body + bib_tex) + "\n\n\\end{document}\n"
        (out_dir / "revised_manuscript.tex").write_text(full_tex, encoding="utf-8")

    ref_md_lines = [ref_heading, ""] + [fr["formatted_entry"] for fr in formatted_refs]
    (out_dir / "references_formatted.md").write_text("\n".join(ref_md_lines) + "\n", encoding="utf-8")
    (out_dir / "references_clean.bib").write_text(export_bibtex(sorted_cited), encoding="utf-8")
    (out_dir / "references_clean.ris").write_text(export_ris(sorted_cited), encoding="utf-8")
    (out_dir / "references_clean.csl.json").write_text(export_csl_json(sorted_cited), encoding="utf-8")
    (out_dir / "references_table.csv").write_text(export_csv(sorted_cited, assigned_numbers), encoding="utf-8-sig")

    sync_result = {
        "case_id": case_plan.get("case_id", ""),
        "target_style": target_style,
        "in_text_format": in_text_format,
        "updated_canonical_library": records,
        "cited_canonical_records": sorted_cited,
        "formatted_references": formatted_refs,
        "renumbering_map": renumbering_map,
        "in_text_occurrences": in_text_occurrences,
        "uncited_library_entries": uncited_entries,
        "extra_tier3_merges": extra_merges,
        "change_records": dedup_changes + meta_changes + sent_changes,
        "stats": {
            "cited_ref_count": len(sorted_cited),
            "in_text_occurrence_count": len(in_text_occurrences),
            "uncited_library_count": len(uncited_entries),
            "total_changes": len(dedup_changes) + len(meta_changes) + len(sent_changes),
        },
    }
    save_json(out_dir / "sync_result.json", sync_result)
    print(json.dumps(sync_result["stats"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
