#!/usr/bin/env python3
"""Deterministic manuscript synchronization, renumbering, and multi-format export."""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    is_valid_doi,
    load_json_safe,
    normalize_arxiv,
    normalize_doi,
    normalize_url,
    parse_page_range,
    parse_year,
    save_json,
    save_text,
    split_authors_string,
)
from library_engine import (
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
from prepare_case import _match_author_year

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


def format_unverified_tag(tokens: List[str], lang: str) -> str:
    cleaned = [re.sub(r"[\[\]]", "", str(t)).strip() for t in tokens if str(t).strip()]
    joined = ", ".join(cleaned) or "unknown"
    return f"[待核验引用: 原标记 {joined}]" if lang == "zh" else f"[UNVERIFIED_CITE: ref-{joined}]"


def _apply_dedup_decisions(
    lib: Dict[str, Any],
    decisions: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    records = [dict(r) for r in lib.get("canonical_records", [])]
    by_id = {r["ref_id"]: r for r in records}
    raw_to_canon = dict(lib.get("raw_to_canonical_map", {}))
    recycled_pairs = {
        frozenset((rc["raw_a"], rc["raw_b"]))
        for rc in (lib.get("recycled_doi_conflicts") or [])
        if isinstance(rc, dict)
    }

    alias_ref_map: Dict[str, str] = {}
    extra_merges: List[Dict[str, Any]] = []
    changes: List[Dict[str, Any]] = []

    for dec in decisions:
        if not isinstance(dec, dict) or dec.get("decision") != "merge":
            continue
        rids = dec.get("record_ids") or []
        canon_ids: List[str] = []
        for x in rids:
            cid = x if x in by_id else raw_to_canon.get(x)
            if cid and cid not in canon_ids:
                canon_ids.append(cid)
        if len(canon_ids) < 2:
            continue

        # Block illegal merge of recycled DOI conflict pair
        member_sets = {cid: set(by_id[cid].get("member_raw_ids") or []) for cid in canon_ids if cid in by_id}
        all_members = set().union(*member_sets.values()) if member_sets else set()
        if any(pair.issubset(all_members) for pair in recycled_pairs):
            continue

        raw_prim = dec.get("canonical_primary")
        primary = raw_prim if raw_prim in canon_ids else raw_to_canon.get(str(raw_prim or ""))
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
                    sec_fp = (sec_rec.get("field_provenance") or {}).get(fld)
                    if sec_fp:
                        prim_rec.setdefault("field_provenance", {})[fld] = sec_fp
            extra_merges.append({"canonical_id": primary, "merged_secondary_ref_id": sec_id, "rationale": dec.get("rationale", "")})
            changes.append({"change_id": f"CHG_DEDUP_{len(changes) + 1:03d}", "target": f"{primary} <- {sec_id}", "action": "merge_tier3_duplicate", "before": f"{primary}, {sec_id}", "after": primary, "basis": dec.get("rationale", "")})

    # Flatten alias chains
    for k in list(alias_ref_map.keys()):
        seen = {k}
        cur = alias_ref_map[k]
        while cur in alias_ref_map and cur not in seen:
            seen.add(cur)
            cur = alias_ref_map[cur]
        alias_ref_map[k] = cur

    return list(by_id.values()), alias_ref_map, extra_merges, changes


def _apply_metadata_resolutions(
    records: List[Dict[str, Any]],
    resolutions: List[Dict[str, Any]],
    style_rules: Dict[str, Any],
    alias_ref_map: Dict[str, str],
    raw_to_canon: Dict[str, str],
) -> List[Dict[str, Any]]:
    by_id = {r["ref_id"]: r for r in records}
    changes: List[Dict[str, Any]] = []
    req_map = style_rules.get("required_fields_by_doc_type", {})

    for res in resolutions:
        if not isinstance(res, dict):
            continue
        rid = str(res.get("ref_id") or "").strip()
        rid = raw_to_canon.get(rid, rid)
        rid = alias_ref_map.get(rid, rid)
        raw_fld = str(res.get("field") or "").strip().lower()
        fld = _META_FIELD_ALIASES.get(raw_fld, raw_fld)
        val = res.get("value")
        if not rid or rid not in by_id or not fld or val in (None, ""):
            continue
        rec = by_id[rid]
        if fld == "authors":
            val = split_authors_string(val) if isinstance(val, str) else [str(a).strip() for a in val if str(a).strip()]
        elif fld == "year":
            yr_i, _ = parse_year(val)
            val = yr_i if yr_i is not None else val
        elif fld == "doi":
            val = normalize_doi(str(val))
            rec["doi_verification_status"] = "not_checked_offline" if is_valid_doi(val) else "invalid_doi_syntax"
        elif fld == "pages":
            pi = parse_page_range(val)
            rec["pages_info"] = pi
            val = pi["normalized"] or str(val).strip()
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
            "source_locator": str(res.get("source_locator", "")),
            "status": "resolved_from_evidence",
            "alternate_values": fp.get(fld, {}).get("alternate_values", []),
        }
        changes.append({
            "change_id": f"CHG_META_{len(changes) + 1:03d}",
            "target": f"{rid}.{fld}",
            "action": "resolve_metadata",
            "before": str(old_val),
            "after": str(val),
            "basis": f"{res.get('source_locator', '')} ({res.get('rationale', '')})",
        })

    for rec in records:
        req = req_map.get(rec.get("doc_type", "other"), ["authors", "title", "year"])
        rec["missing_required_fields"] = [f for f in req if not rec.get(f)]
    return changes


def _auto_tokenize_sentence(
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
                    resolved.append(alias_ref_map.get(cid, cid))
                else:
                    unresolved.append(str(n))
        elif kind == "latex_cite":
            for k in cite.get("keys", []):
                cid = key_map.get(k) or next((rv for km, rv in key_map.items() if km.lower() == k.lower()), None)
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
            repl = f"[[CITE:{','.join(uniq)}]]" + format_unverified_tag(unresolved, lang)
        else:
            if kind == "author_year_narrative" and cite.get("is_bare_narrative"):
                continue
            repl = format_unverified_tag(unresolved or [cite["raw"]], lang)
        out = out[:a] + repl + out[b:]
    return out


def _reorder_blocks(blocks: List[Dict[str, Any]], section_order_override: Optional[List[str]]) -> List[Dict[str, Any]]:
    if not section_order_override:
        return blocks
    override_low = [s.lower() for s in section_order_override]
    by_group: Dict[str, List[Dict[str, Any]]] = {}
    group_order: List[str] = []
    cur_group = "Preamble"
    cur_level = 0
    for blk in blocks:
        if blk.get("block_type") == "heading":
            sec = blk.get("section", "Preamble")
            lvl = int(blk.get("heading_level") or 2)
            explicit = any(o == sec.lower() or o in sec.lower() for o in override_low)
            if cur_level == 0 or lvl <= cur_level or explicit:
                cur_group = sec
                cur_level = lvl
        elif cur_level == 0:
            cur_group = blk.get("section", "Preamble")
        if cur_group not in by_group:
            by_group[cur_group] = []
            group_order.append(cur_group)
        by_group[cur_group].append(blk)

    ordered: List[str] = []
    if "Preamble" in by_group and "Preamble" not in section_order_override:
        ordered.append("Preamble")
    for s in section_order_override:
        for og in group_order:
            if (og == s or s.lower() in og.lower()) and og not in ordered:
                ordered.append(og)
    for og in group_order:
        if og not in ordered:
            ordered.append(og)
    out: List[Dict[str, Any]] = []
    for g in ordered:
        out.extend(by_group[g])
    return out


def _md_ref_to_latex(entry: str) -> str:
    bolds: List[str] = []
    italics: List[str] = []
    s = re.sub(r"\*\*([^*]+)\*\*", lambda m: (bolds.append(m.group(1)) or f"@@B{len(bolds)-1}@@"), entry)
    s = re.sub(r"\*([^*]+)\*", lambda m: (italics.append(m.group(1)) or f"@@I{len(italics)-1}@@"), s)

    def _esc(t: str) -> str:
        return t.replace("&", r"\&").replace("%", r"\%").replace("_", r"\_").replace("#", r"\#")

    s = _esc(s)
    for i, b in enumerate(bolds):
        s = s.replace(f"@@B{i}@@", f"\\textbf{{{_esc(b)}}}")
    for i, it in enumerate(italics):
        s = s.replace(f"@@I{i}@@", f"\\emph{{{_esc(it)}}}")
    return s


def run_sync(work_dir: Path, plan_path: Path) -> Dict[str, Any]:
    rdir = SCRIPT_DIR.parent / "resources"
    case_plan = load_json_safe(work_dir / "case_plan.json", {})
    lib = load_json_safe(work_dir / "library_canonical.json", {})
    ms = load_json_safe(work_dir / "manuscript_parsed.json", {})
    plan = load_json_safe(plan_path, {}) if plan_path.exists() else {}
    style_rules = load_json_safe(rdir / "style_rules.json", {})

    signals = case_plan.get("signals", {})
    target_style = plan.get("target_style") or signals.get("target_style") or "gbt7714_2015_numeric"
    st_cfg = style_rules.get("supported_styles", {}).get(target_style, {})
    in_text_format = plan.get("in_text_format") or signals.get("in_text_format") or st_cfg.get("default_in_text_format", "bracket")
    lang = signals.get("language", "zh")

    raw_to_canon = dict(lib.get("raw_to_canonical_map", {}))
    records, alias_ref_map, extra_merges, dedup_changes = _apply_dedup_decisions(lib, plan.get("dedup_decisions") or [])
    meta_changes = _apply_metadata_resolutions(records, plan.get("metadata_resolutions") or [], style_rules, alias_ref_map, raw_to_canon)
    rec_by_id = {r["ref_id"]: r for r in records}

    num_map = dict(lib.get("inline_num_to_canonical_map", {}))
    key_map = dict(lib.get("key_to_canonical_map", {}))
    ay_lookup: Dict[str, List[str]] = {}
    for orig_s in ms.get("sentences", []):
        for c in orig_s.get("citations", []):
            if c["kind"] in ("author_year_paren", "author_year_narrative"):
                for ay in c.get("author_years", []):
                    if ay not in ay_lookup:
                        m_ids, status_ay = _match_author_year(ay, records)
                        if status_ay == "matched":
                            ay_lookup[ay] = m_ids

    edits_list = plan.get("manuscript_edits") or plan.get("edits") or []
    edits_by_sid = {ed["sentence_id"]: ed for ed in edits_list if isinstance(ed, dict) and ed.get("sentence_id")}

    blocks = _reorder_blocks(ms.get("blocks", []), plan.get("section_order_override"))
    # Move footnotes to end before numbering so first-appearance order matches final Markdown
    blocks = [b for b in blocks if b.get("block_type") != "footnote"] + [b for b in blocks if b.get("block_type") == "footnote"]
    sent_by_id = {s["sentence_id"]: s for s in ms.get("sentences", [])}

    first_appearance_ids: List[str] = []
    sent_changes: List[Dict[str, Any]] = []

    def _register_tokens(tok_text: str) -> str:
        # Normalize shorthand [REF_1] / [REF_001, REF_2] before canonical [[CITE:...]] registration
        shorthand_re = re.compile(r"(?<!\[)\[\s*(REF_\d{1,4}(?:\s*[,;]\s*REF_\d{1,4})*)\s*\](?!\])", re.I)
        tok_text = shorthand_re.sub(lambda sm: "[[CITE:" + ",".join(p.strip() for p in re.split(r"[,;]", sm.group(1)) if p.strip()) + "]]", tok_text)

        def _norm(m: re.Match) -> str:
            kind = m.group(1)
            raw_ids = [x.strip() for x in m.group(2).split(",") if x.strip()]
            mapped: List[str] = []
            invalid: List[str] = []
            for rid in raw_ids:
                pad_m = re.match(r"^REF_(\d{1,4})$", rid, re.I)
                rid_norm = f"REF_{int(pad_m.group(1)):03d}" if pad_m else rid
                cand = alias_ref_map.get(raw_to_canon.get(rid_norm, rid_norm), raw_to_canon.get(rid_norm, rid_norm))
                if cand not in rec_by_id and rid_norm in key_map:
                    cand = alias_ref_map.get(key_map[rid_norm], key_map[rid_norm])
                elif cand not in rec_by_id and rid in key_map:
                    cand = alias_ref_map.get(key_map[rid], key_map[rid])
                if cand in rec_by_id:
                    if cand not in mapped:
                        mapped.append(cand)
                    if cand not in first_appearance_ids:
                        first_appearance_ids.append(cand)
                else:
                    invalid.append(rid)
            if not mapped:
                return format_unverified_tag(invalid or raw_ids, lang)
            res_tok = f"[[{kind}:{','.join(mapped)}]]"
            if invalid:
                res_tok += format_unverified_tag(invalid, lang)
            return res_tok
        return CITE_TOKEN_RE.sub(_norm, tok_text)

    shorthand_check_re = re.compile(r"(?<!\[)\[\s*REF_\d{1,4}(?:\s*[,;]\s*REF_\d{1,4})*\s*\](?!\])", re.I)

    tokenized_blocks: List[Dict[str, Any]] = []
    for blk in blocks:
        btype = blk.get("block_type", "body")
        if btype in ("heading", "code_block", "math_block", "tex_meta"):
            tokenized_blocks.append({"block": blk, "raw_mode": True, "text": blk["text"]})
            continue
        if btype == "table":
            tok_rows: List[Dict[str, Any]] = []
            for r_obj in blk.get("table_rows", []):
                sids = r_obj.get("sentence_ids") or []
                if not sids:
                    tok_rows.append({"raw_line": r_obj["raw"], "is_sep": True})
                    continue
                sid = sids[0]
                s_obj = sent_by_id[sid]
                ed = edits_by_sid.get(sid)
                ed_rev = str(ed.get("revised_sentence_with_tokens") or "") if ed else ""
                ed_act = str(ed.get("action") or "").lower() if ed else ""
                if ed and ed_act == "delete":
                    continue
                if ed and ed_rev and (CITE_TOKEN_RE.search(ed_rev) or shorthand_check_re.search(ed_rev) or (ed_rev != s_obj["text"] and ed_act != "keep")):
                    tok_str = _register_tokens(ed_rev)
                else:
                    tok_str = _register_tokens(_auto_tokenize_sentence(s_obj, num_map, key_map, ay_lookup, alias_ref_map, lang))
                tok_rows.append({"sentence": s_obj, "tokenized_text": tok_str, "is_sep": False})
            tokenized_blocks.append({"block": blk, "table_mode": True, "rows": tok_rows})
            continue

        tok_sents: List[Dict[str, Any]] = []
        for sid in blk.get("sentence_ids", []):
            s_obj = sent_by_id.get(sid)
            if not s_obj:
                continue
            ed = edits_by_sid.get(sid)
            ed_act = str(ed.get("action") or "").strip().lower() if ed else ""
            if ed and ed_act == "delete":
                sent_changes.append({"change_id": f"CHG_EDIT_{len(sent_changes)+1:03d}", "target": sid, "action": "delete", "before": s_obj["text"], "after": "", "basis": ed.get("anchor_rationale", "")})
                continue
            ed_rev = str(ed.get("revised_sentence_with_tokens") or "") if ed else ""
            use_manual = bool(ed and ed_rev and (CITE_TOKEN_RE.search(ed_rev) or shorthand_check_re.search(ed_rev) or (ed_rev != s_obj["text"] and ed_act != "keep")))
            if use_manual:
                tok_str = _register_tokens(ed_rev)
                if tok_str != s_obj["text"]:
                    sent_changes.append({"change_id": f"CHG_EDIT_{len(sent_changes)+1:03d}", "target": sid, "action": ed.get("action", "edit"), "before": s_obj["text"], "after": tok_str, "basis": ed.get("anchor_rationale", "")})
            else:
                tok_str = _register_tokens(_auto_tokenize_sentence(s_obj, num_map, key_map, ay_lookup, alias_ref_map, lang))
            tok_sents.append({"sentence": s_obj, "tokenized_text": tok_str})
        tokenized_blocks.append({"block": blk, "raw_mode": False, "table_mode": False, "tok_sents": tok_sents})

    library_only = (len(ms.get("sentences", [])) == 0 and len(records) > 0)
    if library_only:
        sorted_cited = sort_records_for_style(records, target_style, [r["ref_id"] for r in records])
    else:
        sorted_cited = sort_records_for_style(records, target_style, first_appearance_ids)

    year_suffixes = compute_author_year_suffixes(sorted_cited)
    bibtex_keys = compute_canonical_bibtex_keys(sorted_cited)
    assigned_numbers = {r["ref_id"]: idx for idx, r in enumerate(sorted_cited, start=1)}

    in_text_occurrences: List[Dict[str, Any]] = []
    first_seen_meta: Dict[str, Dict[str, Any]] = {}
    rendered_parts: List[str] = []
    tex_parts: List[str] = []
    before_after_diffs: List[Dict[str, Any]] = []

    req_fmts = list(dict.fromkeys((signals.get("requested_output_formats") or []) + (plan.get("requested_output_formats") or [])))
    want_tex = (ms.get("primary_format") == "latex" or "tex" in req_fmts or in_text_format == "latex_cite")

    def _render_tok_str(tok_str: str, s_obj: Dict[str, Any], force_latex: bool = False) -> str:
        fmt_mode = "latex_cite" if force_latex else in_text_format

        def _sub(m: re.Match) -> str:
            kind = m.group(1)
            rids = [x.strip() for x in m.group(2).split(",") if x.strip() and x.strip() in rec_by_id]
            entries = [(rec_by_id[rid], assigned_numbers[rid], year_suffixes.get(rid, "")) for rid in rids]
            rendered = format_in_text_citation(entries, target_style, fmt_mode, narrative=(kind == "CITE_NARR"), bibtex_keys=bibtex_keys)
            if not force_latex:
                in_text_occurrences.append({"sentence_id": s_obj["sentence_id"], "block_type": s_obj["block_type"], "ref_ids": rids, "rendered_marker": rendered})
                for rid in rids:
                    if rid not in first_seen_meta:
                        first_seen_meta[rid] = {"sentence_id": s_obj["sentence_id"], "block_type": s_obj["block_type"], "marker": rendered}
            return rendered

        return CITE_TOKEN_RE.sub(_sub, tok_str)

    for tb in tokenized_blocks:
        blk = tb["block"]
        btype = blk.get("block_type", "body")
        if tb.get("raw_mode"):
            if btype != "tex_meta":
                rendered_parts.append(tb["text"])
            if want_tex:
                tex_parts.append(tb["text"])
            continue
        if tb.get("table_mode"):
            md_rows: List[str] = []
            for r in tb["rows"]:
                if r.get("is_sep"):
                    md_rows.append(r["raw_line"])
                else:
                    s_obj = r["sentence"]
                    md_line = _render_tok_str(r["tokenized_text"], s_obj, force_latex=False)
                    md_rows.append(md_line)
                    if md_line != s_obj["text"]:
                        before_after_diffs.append({"sentence_id": s_obj["sentence_id"], "before": s_obj["text"], "after": md_line})
            rendered_parts.append("\n".join(md_rows))
            if want_tex:
                tex_parts.append("\n".join(md_rows))
            continue

        md_sents: List[str] = []
        tex_sents: List[str] = []
        for item in tb["tok_sents"]:
            s_obj = item["sentence"]
            md_s = _render_tok_str(item["tokenized_text"], s_obj, force_latex=False)
            md_sents.append(md_s)
            if md_s != s_obj["text"]:
                before_after_diffs.append({"sentence_id": s_obj["sentence_id"], "before": s_obj["text"], "after": md_s})
            if want_tex:
                tex_sents.append(_render_tok_str(item["tokenized_text"], s_obj, force_latex=True))

        if not md_sents:
            continue
        sep = "" if lang == "zh" else " "
        joined_md = sep.join(md_sents)
        if btype == "list_item":
            joined_md = f"{blk.get('list_prefix', '- ')}{joined_md}"
        elif btype == "footnote" and blk.get("fn_prefix"):
            joined_md = f"{blk['fn_prefix']}{joined_md}"
        rendered_parts.append(joined_md)
        if want_tex:
            joined_tx = sep.join(tex_sents)
            if btype == "list_item":
                joined_tx = f"\\item {joined_tx}"
            tex_parts.append(joined_tx)

    # Render bibliography
    formatted_refs: List[Dict[str, Any]] = []
    for rec in sorted_cited:
        rid = rec["ref_id"]
        num = assigned_numbers[rid]
        suf = year_suffixes.get(rid, "")
        entry = format_reference_entry(rec, target_style, num, year_suffix=suf, style_rules=style_rules)
        formatted_refs.append({
            "ref_id": rid,
            "assigned_number": num,
            "year_suffix": suf,
            "bibtex_key": bibtex_keys[rid],
            "formatted_entry": entry,
        })

    ref_heading = "## 参考文献" if lang == "zh" else "## References"
    bib_lines = [fr["formatted_entry"] for fr in formatted_refs]
    bib_block = "\n\n".join(bib_lines) if bib_lines else ("（无正文引用文献）" if lang == "zh" else "(No cited references.)")

    revised_md = ("\n\n".join(rendered_parts).strip() + "\n\n" if rendered_parts else "") + f"{ref_heading}\n\n{bib_block}\n"
    refs_only_md = f"{ref_heading}\n\n{bib_block}\n"

    # Optional LaTeX output
    revised_tex = ""
    if want_tex:
        tex_body_items = []
        end_doc = False
        for tp in tex_parts:
            if tp.strip() == "\\end{document}":
                end_doc = True
                continue
            tex_body_items.append(tp)
        bibitem_lines = ["\\begin{thebibliography}{99}"]
        for fr in formatted_refs:
            rec = rec_by_id[fr["ref_id"]]
            if target_style in ("apa7", "apa6", "gbt7714_2015_author_year", "chicago_author_date"):
                auth0 = (rec.get("authors") or ["Anon"])[0]
                lbl = f"{auth0}({rec.get('year') or 'n.d.'}{fr['year_suffix']})"
                bibitem_lines.append(f"\\bibitem[{lbl}]{{{fr['bibtex_key']}}} {_md_ref_to_latex(fr['formatted_entry'])}")
            else:
                bibitem_lines.append(f"\\bibitem{{{fr['bibtex_key']}}} {_md_ref_to_latex(fr['formatted_entry'])}")
        bibitem_lines.append("\\end{thebibliography}")
        tex_bib_str = "\n".join(bibitem_lines)
        if ms.get("primary_format") == "latex":
            revised_tex = "\n\n".join(tex_body_items) + "\n\n" + tex_bib_str + ("\n\n\\end{document}\n" if end_doc or "\\begin{document}" in "\n".join(tex_body_items) else "\n")
        else:
            doc_cls = "\\documentclass[UTF8]{ctexart}" if lang == "zh" else "\\documentclass{article}"
            natbib = "\\usepackage{natbib}\n" if target_style in ("apa7", "apa6", "gbt7714_2015_author_year", "chicago_author_date") else ""
            revised_tex = f"{doc_cls}\n{natbib}\\begin{{document}}\n\n" + "\n\n".join(tex_body_items) + f"\n\n{tex_bib_str}\n\n\\end{{document}}\n"

    cited_set = set(assigned_numbers.keys())
    uncited = [{"ref_id": r["ref_id"], "title": r.get("title", ""), "year": r.get("year"), "source_keys": r.get("source_keys", [])} for r in records if r["ref_id"] not in cited_set]

    renumbering_map: List[Dict[str, Any]] = []
    for fr in formatted_refs:
        rid = fr["ref_id"]
        rec = rec_by_id[rid]
        fsm = first_seen_meta.get(rid, {})
        renumbering_map.append({
            "ref_id": rid,
            "new_number": fr["assigned_number"],
            "year_suffix": fr["year_suffix"],
            "bibtex_key": fr["bibtex_key"],
            "old_inline_numbers": rec.get("original_list_numbers", []),
            "old_source_keys": rec.get("source_keys", []),
            "new_in_text_marker": fsm.get("marker", f"[{fr['assigned_number']}]"),
            "first_appeared_sentence": fsm.get("sentence_id", "library_only"),
            "first_appeared_block_type": fsm.get("block_type", "library"),
            "title": rec.get("title", ""),
        })

    sync_dir = work_dir / "synced"
    sync_dir.mkdir(parents=True, exist_ok=True)
    save_text(sync_dir / "revised_manuscript.md", revised_md)
    save_text(sync_dir / "references_formatted.md", refs_only_md)
    save_text(sync_dir / "references_clean.bib", export_bibtex(sorted_cited or records, bibtex_keys if sorted_cited else compute_canonical_bibtex_keys(records)))
    save_text(sync_dir / "references_clean.ris", export_ris(sorted_cited or records))
    save_text(sync_dir / "references_clean.csl.json", export_csl_json(sorted_cited or records, bibtex_keys if sorted_cited else compute_canonical_bibtex_keys(records)))
    save_text(sync_dir / "references_table.csv", export_csv(sorted_cited or records, assigned_numbers))
    if revised_tex:
        save_text(sync_dir / "revised_manuscript.tex", revised_tex)

    sync_result = {
        "case_id": case_plan.get("case_id", ""),
        "target_style": target_style,
        "in_text_format": in_text_format,
        "library_only": library_only,
        "alias_ref_map": alias_ref_map,
        "extra_tier3_merges": extra_merges,
        "updated_canonical_library": records,
        "formatted_references": formatted_refs,
        "renumbering_map": renumbering_map,
        "in_text_occurrences": in_text_occurrences,
        "uncited_library_entries": uncited,
        "before_after_diffs": before_after_diffs,
        "change_records": dedup_changes + meta_changes + sent_changes,
        "stats": {
            "cited_ref_count": len(formatted_refs),
            "uncited_library_count": len(uncited),
            "in_text_occurrence_count": len(in_text_occurrences),
            "change_record_count": len(dedup_changes) + len(meta_changes) + len(sent_changes),
        },
    }
    save_json(sync_dir / "sync_result.json", sync_result)
    return sync_result


def main() -> None:
    ap = argparse.ArgumentParser(description="Synchronize manuscript citations, renumbering, and style exports.")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--plan", default="/app/work/citation_plan.json")
    args = ap.parse_args()
    res = run_sync(Path(args.work_dir), Path(args.plan))
    print(f"Synced {res['stats']['cited_ref_count']} cited refs ({res['stats']['uncited_library_count']} uncited) in style={res['target_style']}.")


if __name__ == "__main__":
    main()
