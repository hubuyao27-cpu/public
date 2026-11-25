#!/usr/bin/env python3
"""Deterministic citation synchronization, renumbering, and style conversion engine."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    is_valid_doi,
    load_json_safe,
    normalize_arxiv,
    normalize_doi,
    normalize_url,
    parse_page_range,
    parse_year,
    read_text_safe,
    save_json,
    split_authors_string,
)
from style_formatters import (
    compute_author_year_suffixes,
    compute_canonical_bibtex_keys,
    export_bibtex,
    export_csl_json,
    export_csv,
    export_ris,
    format_in_text_citation,
    format_reference_entry,
    sort_records_for_style,
)

CITE_TOKEN_RE = re.compile(r"\[\[(CITE|CITE_NARR):([^\]]+)\]\]")

_META_FIELD_ALIASES = {
    "journal": "venue",
    "booktitle": "venue",
    "container-title": "venue",
    "source": "venue",
    "number": "issue",
    "page": "pages",
    "author": "authors",
    "address": "city",
    "location": "city",
}


def _format_unverified_tag(tokens: List[str], lang: str) -> str:
    """Format an honest unresolved citation tag WITHOUT nested numeric brackets like [9]."""
    cleaned = [re.sub(r"[\[\]]", "", str(t)).strip() for t in tokens if str(t).strip()]
    joined = ", ".join(cleaned) or "unknown"
    if lang == "zh":
        return f"[待核验引用: 原标记 {joined}]"
    return f"[UNVERIFIED_CITE: ref-{joined}]"


def _md_ref_to_latex(entry: str) -> str:
    """Escape LaTeX special characters in a Markdown-formatted reference entry while converting bold/italic."""
    # Temporarily replace Markdown bold/italic spans with placeholders
    bolds: List[str] = []
    italics: List[str] = []

    def _sub_bold(m: re.Match) -> str:
        bolds.append(m.group(1))
        return f"@@BOLD{len(bolds) - 1}@@"

    def _sub_ital(m: re.Match) -> str:
        italics.append(m.group(1))
        return f"@@ITAL{len(italics) - 1}@@"

    s = re.sub(r"\*\*([^*]+)\*\*", _sub_bold, entry)
    s = re.sub(r"\*([^*]+)\*", _sub_ital, s)

    def _esc(t: str) -> str:
        t = t.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_").replace("#", r"\#")
        return t

    s = _esc(s)
    for i, b in enumerate(bolds):
        s = s.replace(f"@@BOLD{i}@@", f"\\textbf{{{_esc(b)}}}")
    for i, it in enumerate(italics):
        s = s.replace(f"@@ITAL{i}@@", f"\\emph{{{_esc(it)}}}")
    return s


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
        raw_primary = dec.get("canonical_primary")
        primary = raw_primary if raw_primary in canon_ids else raw_to_canon.get(str(raw_primary or ""))
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
            for fld in ("doi", "arxiv_id", "volume", "issue", "pages", "url", "publisher", "city", "abstract", "title_translated", "access_date"):
                if not prim_rec.get(fld) and sec_rec.get(fld):
                    prim_rec[fld] = sec_rec[fld]
                    if fld == "pages":
                        prim_rec["pages_info"] = sec_rec.get("pages_info") or parse_page_range(sec_rec[fld])
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

    # Flatten transitive alias chains (e.g., REF_003 -> REF_002 -> REF_001)
    for k in list(alias_ref_map.keys()):
        seen_chain = {k}
        cur = alias_ref_map[k]
        while cur in alias_ref_map and cur not in seen_chain:
            seen_chain.add(cur)
            cur = alias_ref_map[cur]
        alias_ref_map[k] = cur

    return list(by_id.values()), alias_ref_map, extra_merges, change_records


def _apply_metadata_resolutions(
    records: List[Dict[str, Any]],
    resolutions: List[Dict[str, Any]],
    style_rules: Dict[str, Any],
    alias_ref_map: Optional[Dict[str, str]] = None,
    raw_to_canon: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    by_id = {r["ref_id"]: r for r in records}
    changes: List[Dict[str, Any]] = []
    for res in resolutions:
        if not isinstance(res, dict):
            continue
        rid = str(res.get("ref_id") or "").strip()
        if rid not in by_id and raw_to_canon and rid in raw_to_canon:
            rid = raw_to_canon[rid]
        if rid not in by_id and alias_ref_map and rid in alias_ref_map:
            rid = alias_ref_map[rid]
        raw_fld = str(res.get("field") or "").strip().lower()
        fld = _META_FIELD_ALIASES.get(raw_fld, raw_fld)
        val = res.get("value")
        if not rid or rid not in by_id or not fld or val in (None, ""):
            continue
        rec = by_id[rid]
        if fld == "authors":
            val = split_authors_string(val) if isinstance(val, str) else [str(a).strip() for a in val if str(a).strip()]
        elif fld == "year":
            yr_int, _ = parse_year(val)
            val = yr_int if yr_int is not None else val
        elif fld == "doi":
            val = normalize_doi(str(val))
            rec["doi_verification_status"] = "not_checked_offline" if is_valid_doi(val) else "invalid_doi_syntax"
        elif fld == "pages":
            pinfo = parse_page_range(val)
            rec["pages_info"] = pinfo
            val = pinfo["normalized"] or str(val).strip()
        elif fld == "url":
            val = normalize_url(str(val))
        elif fld == "arxiv_id":
            val = normalize_arxiv(str(val)) or str(val).strip()
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
                    unresolved.append(str(n))
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
                if cand and len(cand) == 1:
                    resolved.append(alias_ref_map.get(cand[0], cand[0]))
                else:
                    unresolved.append(ay)

        uniq = list(dict.fromkeys(resolved))
        if uniq and not unresolved:
            tok_kind = "CITE_NARR" if kind == "author_year_narrative" else "CITE"
            repl = f"[[{tok_kind}:{','.join(uniq)}]]"
        elif uniq and unresolved:
            un_tag = _format_unverified_tag(unresolved, lang)
            repl = f"[[CITE:{','.join(uniq)}]]{un_tag}"
        else:
            # Never silently corrupt unmatched bare narrative prose like 'ImageNet (2012)'
            if kind == "author_year_narrative" and cite.get("is_bare_narrative"):
                continue
            repl = _format_unverified_tag(unresolved or [cite["raw"]], lang)
        out = out[:a] + repl + out[b:]
    return out


def _reorder_blocks(blocks: List[Dict[str, Any]], section_order_override: Optional[List[str]]) -> List[Dict[str, Any]]:
    if not section_order_override:
        return blocks

    # Group child subsections under their parent section unless explicitly named in override
    override_low = [s.lower() for s in section_order_override]
    by_group: Dict[str, List[Dict[str, Any]]] = {}
    group_order: List[str] = []
    cur_group = "Preamble"
    cur_level = 0
    for blk in blocks:
        if blk.get("block_type") == "heading":
            sec = blk.get("section", "Preamble")
            lvl = int(blk.get("heading_level") or 2)
            explicitly_listed = any(o == sec.lower() or o in sec.lower() for o in override_low)
            if cur_level == 0 or lvl <= cur_level or explicitly_listed:
                cur_group = sec
                cur_level = lvl
        else:
            if cur_level == 0:
                cur_group = blk.get("section", "Preamble")
        if cur_group not in by_group:
            by_group[cur_group] = []
            group_order.append(cur_group)
        by_group[cur_group].append(blk)

    ordered_groups: List[str] = []
    if "Preamble" in by_group and "Preamble" not in section_order_override:
        ordered_groups.append("Preamble")
    for s in section_order_override:
        matched = [og for og in group_order if og == s or s.lower() in og.lower()]
        for m in matched:
            if m not in ordered_groups:
                ordered_groups.append(m)
    for og in group_order:
        if og not in ordered_groups:
            ordered_groups.append(og)

    out: List[Dict[str, Any]] = []
    for g in ordered_groups:
        out.extend(by_group[g])
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

    raw_to_canon = dict(lib.get("raw_to_canonical_map", {}))
    records, alias_ref_map, extra_merges, dedup_changes = _apply_dedup_decisions(
        lib, plan.get("dedup_decisions") or []
    )
    meta_changes = _apply_metadata_resolutions(
        records, plan.get("metadata_resolutions") or [], style_rules, alias_ref_map=alias_ref_map, raw_to_canon=raw_to_canon
    )
    rec_by_id = {r["ref_id"]: r for r in records}

    num_map = dict(lib.get("inline_num_to_canonical_map", {}))
    key_map = dict(lib.get("key_to_canonical_map", {}))
    ay_lookup: Dict[str, List[str]] = {}
    try:
        from verify_sources_pre import _match_author_year
    except Exception:
        _match_author_year = None
    # Resolve every author-year marker individually (never use sentence-level resolved_ref_ids shortcut)
    if _match_author_year is not None:
        for orig_sent in ms.get("sentences", []):
            for c in orig_sent.get("citations", []):
                if c["kind"] in ("author_year_paren", "author_year_narrative"):
                    for ay in c.get("author_years", []):
                        if ay not in ay_lookup:
                            m_ids, _ = _match_author_year(ay, records)
                            if len(m_ids) == 1:
                                ay_lookup[ay] = m_ids

    edits_by_sid: Dict[str, Dict[str, Any]] = {}
    for ed in plan.get("manuscript_edits") or []:
        if isinstance(ed, dict) and ed.get("sentence_id"):
            edits_by_sid[ed["sentence_id"]] = ed

    blocks = _reorder_blocks(ms.get("blocks", []), plan.get("section_order_override"))
    # Partition footnote blocks to the end BEFORE numbering so first-appearance order matches final rendered Markdown
    blocks = [b for b in blocks if b.get("block_type") != "footnote"] + [b for b in blocks if b.get("block_type") == "footnote"]
    sent_by_id = {s["sentence_id"]: s for s in ms.get("sentences", [])}

    tokenized_blocks: List[Dict[str, Any]] = []
    first_appearance_ids: List[str] = []
    sent_changes: List[Dict[str, Any]] = []

    def _register_tokens(tok_text: str) -> str:
        def _norm_tok(m: re.Match) -> str:
            kind = m.group(1)
            raw_ids = [x.strip() for x in m.group(2).split(",") if x.strip()]
            mapped: List[str] = []
            invalid: List[str] = []
            for rid in raw_ids:
                cand = raw_to_canon.get(rid, rid)
                cand = alias_ref_map.get(cand, cand)
                if cand not in rec_by_id and rid in key_map:
                    cand = alias_ref_map.get(key_map[rid], key_map[rid])
                if cand in rec_by_id:
                    if cand not in mapped:
                        mapped.append(cand)
                    if cand not in first_appearance_ids:
                        first_appearance_ids.append(cand)
                else:
                    invalid.append(rid)
            if not mapped:
                return _format_unverified_tag(invalid or raw_ids, lang)
            out_tok = f"[[{kind}:{','.join(mapped)}]]"
            if invalid:
                out_tok += _format_unverified_tag(invalid, lang)
            return out_tok
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
            ed_action = str(ed.get("action") or "").strip().lower() if ed else ""
            if ed and ed_action == "delete":
                sent_changes.append({
                    "change_id": f"CHG_EDIT_{len(sent_changes) + 1:03d}",
                    "target": sid,
                    "action": "delete",
                    "before": s_obj["text"],
                    "after": "",
                    "basis": ed.get("anchor_rationale", ""),
                })
                continue
            ed_rev = str(ed.get("revised_sentence_with_tokens") or "") if ed else ""
            # Use manual edit if it embeds [[CITE...]] tokens or actually modifies text;
            # guard against unmodified raw-citation "keep" copies bypassing auto-tokenization.
            use_manual_edit = bool(
                ed
                and ed_rev
                and (bool(CITE_TOKEN_RE.search(ed_rev)) or (ed_rev != s_obj["text"] and ed_action != "keep"))
            )
            if use_manual_edit:
                tok_str = _register_tokens(ed_rev)
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

    if not first_appearance_ids and len(ms.get("sentences", [])) == 0 and records:
        # Library-only formatting/dedup/conversion task (no manuscript sentences)
        library_order_ids = [r["ref_id"] for r in records]
        sorted_cited = sort_records_for_style(records, target_style, library_order_ids)
    else:
        sorted_cited = sort_records_for_style(records, target_style, first_appearance_ids)
    year_suffixes = compute_author_year_suffixes(sorted_cited, style=target_style, style_rules=style_rules)
    bibtex_keys = compute_canonical_bibtex_keys(sorted_cited)
    assigned_numbers: Dict[str, int] = {r["ref_id"]: idx for idx, r in enumerate(sorted_cited, start=1)}

    in_text_occurrences: List[Dict[str, Any]] = []
    first_seen_meta: Dict[str, Dict[str, Any]] = {}
    rendered_blocks: List[Dict[str, Any]] = []
    tex_rendered_blocks: List[Dict[str, Any]] = []
    req_fmts_early = list(
        dict.fromkeys(
            (case_plan.get("signals", {}).get("requested_output_formats") or [])
            + (plan.get("requested_output_formats") or [])
        )
    )
    want_tex_out = (ms.get("primary_format") == "latex" or "tex" in req_fmts_early)

    for tb in tokenized_blocks:
        blk = tb["block"]
        if blk["block_type"] == "heading":
            rendered_blocks.append({"block_type": "heading", "section": blk["section"], "text": blk["text"]})
            tex_rendered_blocks.append({"block_type": "heading", "section": blk["section"], "text": blk["text"]})
            continue
        rendered_sents: List[str] = []
        tex_sents: List[str] = []
        for item in tb["tokenized_sentences"]:
            s_obj = item["sentence"]
            tok_str = item["tokenized_text"]

            def _render_match(m: re.Match) -> str:
                kind = m.group(1)
                rids = [x.strip() for x in m.group(2).split(",") if x.strip() and x.strip() in rec_by_id]
                entries = [(rec_by_id[rid], assigned_numbers[rid], year_suffixes.get(rid, "")) for rid in rids]
                rendered = format_in_text_citation(
                    entries,
                    target_style,
                    in_text_format,
                    narrative=(kind == "CITE_NARR"),
                    style_rules=style_rules,
                    bibtex_keys=bibtex_keys,
                )
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

            if want_tex_out:
                def _render_tex_match(m: re.Match) -> str:
                    kind = m.group(1)
                    rids = [x.strip() for x in m.group(2).split(",") if x.strip() and x.strip() in rec_by_id]
                    entries = [(rec_by_id[rid], assigned_numbers[rid], year_suffixes.get(rid, "")) for rid in rids]
                    return format_in_text_citation(
                        entries,
                        target_style,
                        "latex_cite",
                        narrative=(kind == "CITE_NARR"),
                        style_rules=style_rules,
                        bibtex_keys=bibtex_keys,
                    )

                t_sent = CITE_TOKEN_RE.sub(_render_tex_match, tok_str)
                t_sent = re.sub(r"\s+([。，；：！？])", r"\1", t_sent)
                tex_sents.append(t_sent)

        preserve_lines = blk["block_type"] == "table_cell" or (
            len(rendered_sents) > 1 and all(re.match(r"^(?:[-*+]\s+|\d+[.)]\s+|%)", x.strip()) for x in rendered_sents if x.strip())
        )
        joined_para = "\n".join(rendered_sents) if preserve_lines else " ".join(rendered_sents)
        if lang == "zh" and not preserve_lines:
            joined_para = re.sub(r"(?<=[。！？；：”’>）\]])\s+(?=[\u4e00-\u9fff])", "", joined_para)
        rendered_blocks.append({
            "block_type": blk["block_type"],
            "section": blk["section"],
            "text": joined_para,
        })
        if want_tex_out:
            t_para = "\n".join(tex_sents) if preserve_lines else " ".join(tex_sents)
            if lang == "zh" and not preserve_lines:
                t_para = re.sub(r"(?<=[。！？；：”’>）\]])\s+(?=[\u4e00-\u9fff])", "", t_para)
            tex_rendered_blocks.append({"block_type": blk["block_type"], "section": blk["section"], "text": t_para})

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
        example_marker = format_in_text_citation([(rec, num, ys)], target_style, in_text_format, style_rules=style_rules, bibtex_keys=bibtex_keys)
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

    if want_tex_out:
        input_dir = Path(case_plan.get("input_dir", "/input"))
        tex_files = [f for f in case_plan.get("files", []) if f.get("role") == "manuscript" and f.get("ext") == ".tex"]
        orig_tex = ""
        if tex_files:
            orig_tex, _, _ = read_text_safe(input_dir / tex_files[0]["rel_path"])
        cleaned_tex = re.sub(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", "", orig_tex, flags=re.S)
        tex_body = []
        for rb in (tex_rendered_blocks or rendered_blocks):
            if rb["block_type"] == "heading":
                raw_h = rb["text"].strip()
                if raw_h.startswith("### "):
                    tex_body.append(f"\\subsection{{{raw_h[4:].strip()}}}")
                elif raw_h.startswith("## "):
                    tex_body.append(f"\\section{{{raw_h[3:].strip()}}}")
                elif raw_h.startswith("# "):
                    tex_body.append(f"\\section*{{{raw_h[2:].strip()}}}")
                elif raw_h.startswith("\\"):
                    tex_body.append(raw_h)
                else:
                    tex_body.append(f"\\section{{{raw_h}}}")
            else:
                tex_body.append(rb["text"])
            tex_body.append("")
        bib_tex = ["\\begin{thebibliography}{99}"]
        for fr in formatted_refs:
            k = bibtex_keys.get(fr["ref_id"], fr["ref_id"].lower())
            bib_tex.append(f"\\bibitem{{{k}}} {_md_ref_to_latex(fr['formatted_entry'])}")
        bib_tex.append("\\end{thebibliography}")
        if "\\begin{document}" in cleaned_tex:
            preamble = cleaned_tex[: cleaned_tex.find("\\begin{document}") + len("\\begin{document}")]
            full_tex = preamble + "\n\n" + "\n".join(tex_body + bib_tex) + "\n\n\\end{document}\n"
        else:
            joined_tex_text = "\n".join(tex_body + bib_tex)
            has_cjk = bool(re.search(r"[\u4e00-\u9fff]", joined_tex_text))
            doc_cls = "\\documentclass[UTF8]{ctexart}\n" if has_cjk else "\\documentclass{article}\n\\usepackage[utf8]{inputenc}\n"
            full_tex = doc_cls + "\\usepackage[margin=1in]{geometry}\n\\begin{document}\n\n" + joined_tex_text + "\n\n\\end{document}\n"
        (out_dir / "revised_manuscript.tex").write_text(full_tex, encoding="utf-8")

    ref_md_lines = [ref_heading, ""] + [fr["formatted_entry"] for fr in formatted_refs]
    (out_dir / "references_formatted.md").write_text("\n".join(ref_md_lines) + "\n", encoding="utf-8")
    (out_dir / "references_clean.bib").write_text(export_bibtex(sorted_cited, bibtex_keys=bibtex_keys), encoding="utf-8")
    (out_dir / "references_clean.ris").write_text(export_ris(sorted_cited), encoding="utf-8")
    (out_dir / "references_clean.csl.json").write_text(export_csl_json(sorted_cited), encoding="utf-8")
    (out_dir / "references_table.csv").write_text(export_csv(sorted_cited, assigned_numbers), encoding="utf-8-sig")

    sync_result = {
        "case_id": case_plan.get("case_id", ""),
        "target_style": target_style,
        "in_text_format": in_text_format,
        "alias_ref_map": alias_ref_map,
        "bibtex_keys": bibtex_keys,
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
