#!/usr/bin/env python3
"""Step 4: Deterministic cross-zone citation renumbering, claim-level insertion, and bibliography formatter."""

from __future__ import annotations

import argparse
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    has_cjk,
    load_json_safe,
    load_resource_json,
    parse_author_name,
    save_json,
)


def compress_numeric_list(nums: List[int], style: str) -> str:
    """Sort, deduplicate, and range-compress integer citation numbers."""
    uniq = sorted(set(nums))
    if not uniq:
        return ""
    if style == "ieee":
        # IEEE formats ranges as [1]-[3] or [1], [2]
        ranges: List[str] = []
        i = 0
        while i < len(uniq):
            j = i
            while j + 1 < len(uniq) and uniq[j + 1] == uniq[j] + 1:
                j += 1
            if j - i >= 2:
                ranges.append(f"[{uniq[i]}]–[{uniq[j]}]")
            elif j - i == 1:
                ranges.extend([f"[{uniq[i]}]", f"[{uniq[j]}]"])
            else:
                ranges.append(f"[{uniq[i]}]")
            i = j + 1
        return ", ".join(ranges)

    sep = "–" if style == "nature" else "-"
    parts: List[str] = []
    i = 0
    while i < len(uniq):
        j = i
        while j + 1 < len(uniq) and uniq[j + 1] == uniq[j] + 1:
            j += 1
        if j - i >= 2:
            parts.append(f"{uniq[i]}{sep}{uniq[j]}")
        elif j - i == 1:
            parts.extend([str(uniq[i]), str(uniq[j])])
        else:
            parts.append(str(uniq[i]))
        i = j + 1
    return f"[{','.join(parts)}]"


def format_authors_gbt(authors: List[str], is_zh: bool) -> str:
    if not authors:
        return "佚名" if is_zh else "Anon"
    fmt: List[str] = []
    for a in authors[:3]:
        p = parse_author_name(a)
        if p["is_cjk"]:
            fmt.append(p["family"])
        else:
            ini = p["initials"]
            fmt.append(f"{p['family'].upper()} {ini}".strip() if ini else p["family"].upper())
    if len(authors) > 3:
        fmt.append("等" if is_zh else "et al")
    return ", ".join(fmt)


def format_authors_apa(authors: List[str]) -> str:
    if not authors:
        return "Anonymous"
    fmt: List[str] = []
    for a in authors[:20]:
        p = parse_author_name(a)
        if p["is_cjk"]:
            fmt.append(p["family"])
        else:
            ini = ". ".join(p["initials"].split()) + "." if p["initials"] else ""
            fmt.append(f"{p['family']}, {ini}".strip(", "))
    if len(fmt) == 1:
        return fmt[0]
    if len(authors) <= 20:
        return ", ".join(fmt[:-1]) + ", & " + fmt[-1]
    return ", ".join(fmt[:19]) + ", ... " + fmt[-1]


def format_authors_ieee(authors: List[str]) -> str:
    if not authors:
        return "Anonymous"
    if len(authors) > 6:
        p = parse_author_name(authors[0])
        if p["is_cjk"]:
            return f"{p['family']} et al."
        ini = ". ".join(p["initials"].split()) + "." if p["initials"] else ""
        return f"{ini} {p['family']} et al.".strip()
    fmt: List[str] = []
    for a in authors:
        p = parse_author_name(a)
        if p["is_cjk"]:
            fmt.append(p["family"])
        else:
            ini = ". ".join(p["initials"].split()) + "." if p["initials"] else ""
            fmt.append(f"{ini} {p['family']}".strip())
    if len(fmt) == 1:
        return fmt[0]
    if len(fmt) == 2:
        return f"{fmt[0]} and {fmt[1]}"
    return ", ".join(fmt[:-1]) + ", and " + fmt[-1]


def format_authors_vancouver(authors: List[str]) -> str:
    if not authors:
        return "Anonymous"
    fmt: List[str] = []
    for a in authors[:6]:
        p = parse_author_name(a)
        if p["is_cjk"]:
            fmt.append(p["family"])
        else:
            fmt.append(f"{p['family']} {p.get('initials_compact', '')}".strip())
    if len(authors) > 6:
        fmt.append("et al")
    return ", ".join(fmt)


def format_bibliography_entry(entry: Dict[str, Any], style: str, index_num: Optional[int] = None, year_suffix: str = "") -> str:
    """Format a canonical entry cleanly in target style without emitting empty/None artifacts."""
    authors = entry.get("authors") or []
    title = (entry.get("title") or "").strip().rstrip(".")
    year = (entry.get("year") or "").strip()
    yr_disp = f"{year}{year_suffix}" if year else ("n.d." if style == "apa7" else "")
    venue = (entry.get("venue") or "").strip()
    vol = (entry.get("volume") or "").strip()
    iss = (entry.get("issue") or "").strip()
    pages = (entry.get("pages") or "").strip()
    doi = (entry.get("doi") or "").strip()
    url = (entry.get("url") or "").strip()
    etype = entry.get("entry_type", "misc")
    is_zh = has_cjk(title) or any(has_cjk(a) for a in authors)

    type_map = {
        "journal_article": "[J]",
        "conference_paper": "[C]",
        "book": "[M]",
        "book_chapter": "[M]",
        "thesis": "[D]",
        "report": "[R]",
        "standard": "[S]",
        "patent": "[P]",
        "preprint": "[EB/OL]",
        "web": "[EB/OL]",
        "misc": "[Z]",
    }

    if style in ("gbt7714_numeric", "gbt7714_author_year"):
        auth_s = format_authors_gbt(authors, is_zh)
        tcode = type_map.get(etype, "[Z]")
        if etype == "preprint" and not tcode:
            tcode = "[EB/OL]"
        prefix = f"[{index_num}] " if (style == "gbt7714_numeric" and index_num is not None) else ""
        head = f"{prefix}{auth_s}. {title}{tcode}"
        if etype == "conference_paper" and venue:
            venue_part = f"// {venue}"
        else:
            venue_part = venue
        tail_bits: List[str] = []
        if style == "gbt7714_author_year":
            head = f"{auth_s}, {yr_disp}. {title}{tcode}" if yr_disp else f"{auth_s}. {title}{tcode}"
        if venue_part:
            tail_bits.append(venue_part)
        # Year, Vol(Issue): Pages
        pub_seg = ""
        if style == "gbt7714_numeric" and yr_disp:
            pub_seg += yr_disp
        if vol:
            pub_seg += f", {vol}" if pub_seg else vol
        if iss:
            pub_seg += f"({iss})"
        if pages:
            pub_seg += f": {pages}" if pub_seg else pages
        if pub_seg:
            tail_bits.append(pub_seg)
        body = ". ".join(x for x in [head, ", ".join(tail_bits)] if x).rstrip(".") + "."
        if doi:
            body += f" DOI:{doi}."
        elif url and etype in ("preprint", "web"):
            body += f" {url}."
        return body

    if style == "apa7":
        auth_s = format_authors_apa(authors)
        parts = [f"{auth_s} ({yr_disp})." if yr_disp else f"{auth_s}."]
        if title:
            parts.append(f"{title}.")
        if venue:
            v_str = f"*{venue}*"
            if vol:
                v_str += f", *{vol}*"
                if iss:
                    v_str += f"({iss})"
            if pages:
                v_str += f", {pages}"
            parts.append(v_str + ".")
        if doi:
            parts.append(f"https://doi.org/{doi}")
        elif url:
            parts.append(url)
        return " ".join(parts)

    if style == "ieee":
        auth_s = format_authors_ieee(authors)
        prefix = f"[{index_num}] " if index_num is not None else ""
        segs = [auth_s]
        if title:
            segs.append(f'"{title},"')
        if venue:
            segs.append(f"*{venue}*")
        if vol:
            segs.append(f"vol. {vol}")
        if iss:
            segs.append(f"no. {iss}")
        if pages:
            segs.append(f"pp. {pages.replace('-', '–')}")
        if yr_disp:
            segs.append(yr_disp)
        out = prefix + ", ".join(segs) + "."
        if doi:
            out += f" doi: {doi}."
        return out

    if style == "nature":
        auth_s = format_authors_apa(authors[:5]) + (" et al." if len(authors) > 5 else "")
        prefix = f"{index_num}. " if index_num is not None else ""
        v_str = f"*{venue}*" if venue else ""
        if vol:
            v_str += f" **{vol}**"
        if pages:
            v_str += f", {pages}"
        yr_p = f"({yr_disp})" if yr_disp else ""
        body = " ".join(x for x in [f"{auth_s}", f"{title}.", v_str, yr_p] if x).strip()
        if not body.endswith("."):
            body += "."
        return prefix + body

    if style == "vancouver":
        auth_s = format_authors_vancouver(authors)
        prefix = f"{index_num}. " if index_num is not None else ""
        pub = yr_disp
        if vol:
            pub += f";{vol}"
        if iss:
            pub += f"({iss})"
        if pages:
            pub += f":{pages}"
        body = ". ".join(x for x in [auth_s, title, venue, pub] if x).rstrip(".") + "."
        if doi:
            body += f" doi:{doi}"
        return prefix + body

    # chicago_author_year fallback
    auth_s = format_authors_apa(authors)
    parts = [f"{auth_s}.", f"{yr_disp}." if yr_disp else "", f'"{title}."' if title else "", f"*{venue}*" if venue else ""]
    if vol:
        parts.append(f"{vol}" + (f" ({iss})" if iss else "") + (f": {pages}" if pages else ""))
    out = " ".join(x for x in parts if x).strip()
    if doi:
        out += f". https://doi.org/{doi}"
    return out + ("." if not out.endswith(".") else "")


def format_bibtex_entry(entry: Dict[str, Any], key: str) -> str:
    """Export clean BibTeX entry from canonical record."""
    btype = {"journal_article": "article", "conference_paper": "inproceedings", "book": "book", "book_chapter": "incollection", "thesis": "phdthesis", "report": "techreport"}.get(entry.get("entry_type", ""), "misc")
    lines = [f"@{btype}{{{key},"]
    if entry.get("authors"):
        lines.append(f"  author = {{{' and '.join(entry['authors'])}}},")
    if entry.get("title"):
        lines.append(f"  title = {{{entry['title']}}},")
    if entry.get("venue"):
        vkey = "booktitle" if btype in ("inproceedings", "incollection") else "journal"
        lines.append(f"  {vkey} = {{{entry['venue']}}},")
    if entry.get("year"):
        lines.append(f"  year = {{{entry['year']}}},")
    if entry.get("volume"):
        lines.append(f"  volume = {{{entry['volume']}}},")
    if entry.get("issue"):
        lines.append(f"  number = {{{entry['issue']}}},")
    if entry.get("pages"):
        lines.append(f"  pages = {{{entry['pages']}}},")
    if entry.get("doi"):
        lines.append(f"  doi = {{{entry['doi']}}},")
    if entry.get("url"):
        lines.append(f"  url = {{{entry['url']}}},")
    lines.append("}")
    return "\n".join(lines)


def format_ris_entry(entry: Dict[str, Any]) -> str:
    """Export clean RIS entry from canonical record."""
    ty = {"journal_article": "JOUR", "conference_paper": "CONF", "book": "BOOK", "book_chapter": "CHAP", "thesis": "THES", "report": "RPRT", "preprint": "ELEC"}.get(entry.get("entry_type", ""), "GEN")
    lines = [f"TY  - {ty}"]
    for a in entry.get("authors", []):
        lines.append(f"AU  - {a}")
    if entry.get("title"):
        lines.append(f"TI  - {entry['title']}")
    if entry.get("venue"):
        lines.append(f"JO  - {entry['venue']}")
    if entry.get("year"):
        lines.append(f"PY  - {entry['year']}")
    if entry.get("volume"):
        lines.append(f"VL  - {entry['volume']}")
    if entry.get("issue"):
        lines.append(f"IS  - {entry['issue']}")
    if entry.get("pages"):
        parts = entry["pages"].split("-", 1)
        lines.append(f"SP  - {parts[0]}")
        if len(parts) > 1:
            lines.append(f"EP  - {parts[1]}")
    if entry.get("doi"):
        lines.append(f"DO  - {entry['doi']}")
    if entry.get("url"):
        lines.append(f"UR  - {entry['url']}")
    lines.append("ER  - ")
    return "\n".join(lines)


def format_in_text_author_year(ref_ids: List[str], ref_map: Dict[str, Dict[str, Any]], suffix_map: Dict[str, str], style: str) -> str:
    """Format in-text author-year citation group."""
    items: List[str] = []
    for rid in ref_ids:
        e = ref_map[rid]
        authors = e.get("authors") or []
        yr = f"{e.get('year') or 'n.d.'}{suffix_map.get(rid, '')}"
        if not authors:
            a_lbl = "Anon"
        else:
            p0 = parse_author_name(authors[0])
            a0 = p0["family"]
            if len(authors) == 1:
                a_lbl = a0
            elif len(authors) == 2:
                a1 = parse_author_name(authors[1])["family"]
                sep = " & " if style == "apa7" else (" and " if style == "chicago_author_year" else ", ")
                a_lbl = f"{a0}{sep}{a1}"
            else:
                et = "等" if has_cjk(a0) and style == "gbt7714_author_year" else " et al."
                a_lbl = f"{a0}{et}"
        items.append(f"{a_lbl} {yr}" if style == "chicago_author_year" else f"{a_lbl}, {yr}")
    return f"({'; '.join(items)})"


def apply_anchor_insertion(text: str, anchor_phrase: str, placement_mode: str, marker_tok: str) -> Tuple[str, str]:
    """Insert marker token at exact claim phrase rather than dumping at paragraph end."""
    if anchor_phrase and anchor_phrase in text:
        pos = text.find(anchor_phrase) + len(anchor_phrase)
        if placement_mode == "before_clause_punct":
            m_punct = re.search(r"[,.;:?!，。；：？！]", text[pos:])
            if m_punct:
                ins = pos + m_punct.start()
                return text[:ins] + " " + marker_tok + text[ins:], "exact_before_clause_punct"
        return text[:pos] + " " + marker_tok + text[pos:], "exact_after_phrase"

    # Fallback: just before terminal sentence punctuation
    m_end = re.search(r"([.!?。！？])\s*$", text)
    if m_end:
        ins = m_end.start()
        return text[:ins] + " " + marker_tok + text[ins:], "fallback_sentence_end"
    return text + " " + marker_tok, "fallback_sentence_end"


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize citations across zones and format bibliography.")
    parser.add_argument("--case-plan", required=True)
    parser.add_argument("--library", required=True)
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--out", default="/app/work/synced_package.json")
    args = parser.parse_args()

    case_plan, _ = load_json_safe(Path(args.case_plan), default={})
    lib_doc, _ = load_json_safe(Path(args.library), default={})
    analysis, _ = load_json_safe(Path(args.analysis), default={})
    decisions, _ = load_json_safe(Path(args.decisions), default={})

    style = decisions.get("target_style") or case_plan.get("signals", {}).get("target_style", "gbt7714_numeric")
    styles_cfg = load_resource_json("citation_styles.json").get("styles", {})
    system = styles_cfg.get(style, {}).get("system", "numeric")
    retain_uncited = bool(decisions.get("retain_uncited_library", case_plan.get("signals", {}).get("retain_uncited_library", False)))

    entries: List[Dict[str, Any]] = [dict(e) for e in lib_doc.get("entries", [])]
    ref_map: Dict[str, Dict[str, Any]] = {e["ref_id"]: e for e in entries}

    # Apply explicit dedup_overrides (merge RAW/REF pairs when user/agent confirms merge)
    raw_to_ref: Dict[str, str] = {}
    for e in entries:
        for rid_raw in e.get("merged_from", []):
            raw_to_ref[rid_raw] = e["ref_id"]
    alias_ref_map: Dict[str, str] = {}
    for ov in decisions.get("dedup_overrides", []):
        if ov.get("decision") == "merge" and len(ov.get("candidate_pair", [])) >= 2:
            p0, p1 = ov["candidate_pair"][0], ov["candidate_pair"][1]
            r_keep = raw_to_ref.get(p0, p0)
            r_drop = raw_to_ref.get(p1, p1)
            if r_keep in ref_map and r_drop in ref_map and r_keep != r_drop:
                alias_ref_map[r_drop] = r_keep
                for mf in ref_map[r_drop].get("merged_from", []):
                    if mf not in ref_map[r_keep].get("merged_from", []):
                        ref_map[r_keep].setdefault("merged_from", []).append(mf)
                entries = [e for e in entries if e["ref_id"] != r_drop]
                ref_map.pop(r_drop, None)

    # Apply explicitly sourced metadata corrections
    for corr in decisions.get("metadata_corrections", []):
        rid = alias_ref_map.get(corr.get("ref_id"), corr.get("ref_id"))
        fld = corr.get("field")
        if rid in ref_map and fld and corr.get("evidence_source"):
            ref_map[rid][fld] = corr["new_value"]

    sentences: List[Dict[str, Any]] = [dict(s) for s in analysis.get("sentences", [])]
    cps_by_sid: Dict[str, List[Dict[str, Any]]] = {}
    for cp in analysis.get("citation_points", []):
        cps_by_sid.setdefault(cp["sentence_id"], []).append(cp)

    audit_by_cp: Dict[str, Dict[str, Any]] = {
        a["citation_point_id"]: a for a in decisions.get("existing_citation_audits", []) if a.get("citation_point_id")
    }
    insertions_by_sid: Dict[str, List[Dict[str, Any]]] = {}
    for ins in decisions.get("claim_insertions", []):
        insertions_by_sid.setdefault(ins["sentence_id"], []).append(ins)

    # Optional section reordering
    sec_order = decisions.get("section_reorder") or []
    if sec_order:
        order_idx = {sec.lower(): i for i, sec in enumerate(sec_order)}
        sentences.sort(key=lambda s: order_idx.get(s["section"].lower(), 999))

    # Stage 1: Replace existing citation markers & insert new claim placeholders per sentence
    placement_notes: List[Dict[str, Any]] = []
    staged_sentences: List[Dict[str, Any]] = []
    for sent in sentences:
        sid = sent["sentence_id"]
        txt = sent["text"]
        cps = sorted(cps_by_sid.get(sid, []), key=lambda c: c["marker"]["start"], reverse=True)
        for cp in cps:
            mk = cp["marker"]
            aud = audit_by_cp.get(cp["citation_point_id"], {})
            act = aud.get("action", "keep" if cp["resolved_ref_ids"] else "flag_unresolved")
            rids = aud.get("resolved_ref_ids") if "resolved_ref_ids" in aud else cp["resolved_ref_ids"]
            rids = [r for r in (rids or []) if r in ref_map]
            if act == "remove":
                repl = ""
            elif act == "flag_unresolved" or not rids:
                repl = f"<<UNRES:{cp['citation_point_id']}:{mk['raw']}>>"
            else:
                repl = f"<<CITE:{','.join(rids)}|{cp['citation_point_id']}|{mk['raw']}>>"
            txt = txt[: mk["start"]] + repl + txt[mk["end"] :]

        for ins in insertions_by_sid.get(sid, []):
            rids = [r for r in ins.get("ref_ids", []) if r in ref_map]
            if not rids:
                tok = f"<<UNRES:INS:{ins.get('anchor_phrase','')}>>"
            else:
                tok = f"<<CITE:{','.join(rids)}|NEW|>>"
            txt, mode_used = apply_anchor_insertion(txt, ins.get("anchor_phrase", ""), ins.get("placement_mode", "after_phrase"), tok)
            placement_notes.append({"sentence_id": sid, "anchor_phrase": ins.get("anchor_phrase", ""), "mode_used": mode_used, "ref_ids": rids})

        # Clean stray double spaces left by removals
        txt = re.sub(r" {2,}", " ", txt).strip()
        s_copy = dict(sent)
        s_copy["staged_text"] = txt
        staged_sentences.append(s_copy)

    # Stage 2: Traverse reading order across ALL zones and assign final numbers / author-year disambiguation
    cited_order: List[str] = []
    for s in staged_sentences:
        for m in re.finditer(r"<<CITE:([^|>]+)\|([^|]*)\|([^>]*)>>", s["staged_text"]):
            for rid in m.group(1).split(","):
                if rid in ref_map and rid not in cited_order:
                    cited_order.append(rid)

    bib_ref_ids = list(cited_order)
    if retain_uncited:
        for e in entries:
            if e["ref_id"] not in bib_ref_ids:
                bib_ref_ids.append(e["ref_id"])

    num_map: Dict[str, int] = {}
    suffix_map: Dict[str, str] = {}
    if system == "numeric":
        for idx, rid in enumerate(bib_ref_ids, start=1):
            num_map[rid] = idx
    else:
        # Sort bibliography alphabetically by first author surname, year, title
        bib_ref_ids.sort(
            key=lambda rid: (
                (ref_map[rid].get("authors") or ["zzz"])[0].lower(),
                ref_map[rid].get("year") or "9999",
                ref_map[rid].get("title", "").lower(),
            )
        )
        # Assign a/b/c disambiguation for identical (first_author_surname, year)
        ay_buckets: Dict[Tuple[str, str], List[str]] = {}
        for rid in bib_ref_ids:
            auths = ref_map[rid].get("authors") or []
            surn = parse_author_name(auths[0])["family"].lower() if auths else "anon"
            yr = ref_map[rid].get("year") or "n.d."
            ay_buckets.setdefault((surn, yr), []).append(rid)
        for (_, _), grp in ay_buckets.items():
            if len(grp) > 1:
                for i, rid in enumerate(grp):
                    suffix_map[rid] = chr(ord("a") + i)

    # Stage 3: Render final in-text markers & record renumbering_map
    renumbering_map: List[Dict[str, Any]] = []
    final_sentences: List[Dict[str, Any]] = []
    for s in staged_sentences:
        def _replace_tok(match: re.Match[str]) -> str:
            rids = [r for r in match.group(1).split(",") if r in ref_map]
            cp_lbl = match.group(2)
            old_mk = match.group(3)
            if system == "numeric":
                nums = [num_map[r] for r in rids]
                new_mk = compress_numeric_list(nums, style)
            else:
                new_mk = format_in_text_author_year(rids, ref_map, suffix_map, style)
            renumbering_map.append(
                {
                    "sentence_id": s["sentence_id"],
                    "zone": s["zone"],
                    "citation_point_id": cp_lbl,
                    "old_marker": old_mk or "(inserted)",
                    "canonical_ref_ids": rids,
                    "new_marker": new_mk,
                }
            )
            return new_mk

        rendered = re.sub(r"<<CITE:([^|>]+)\|([^|]*)\|([^>]*)>>", _replace_tok, s["staged_text"])
        rendered = re.sub(
            r"<<UNRES:([^:>]+):?([^>]*)>>",
            lambda m: f"[UNVERIFIED_CITE:{m.group(2) or m.group(1)}]",
            rendered,
        )
        rendered = re.sub(r"\s+([,.;:?!，。；：？！])", r"\1", rendered)
        s_out = dict(s)
        s_out["final_text"] = rendered
        final_sentences.append(s_out)

    # Stage 4: Format bibliography + BibTeX + RIS
    formatted_bib: List[Dict[str, Any]] = []
    bib_entries_str: List[str] = []
    ris_entries_str: List[str] = []
    for idx, rid in enumerate(bib_ref_ids, start=1):
        e = ref_map[rid]
        num = num_map.get(rid)
        formatted = format_bibliography_entry(e, style, index_num=num, year_suffix=suffix_map.get(rid, ""))
        bkey = (e.get("all_cite_keys") or [rid.lower().replace("-", "_")])[0]
        formatted_bib.append(
            {
                "ref_id": rid,
                "assigned_number": num,
                "year_suffix": suffix_map.get(rid, ""),
                "cited_in_text": rid in cited_order,
                "formatted_entry": formatted,
                "missing_fields": e.get("missing_fields", []),
                "merged_from": e.get("merged_from", []),
            }
        )
        bib_entries_str.append(format_bibtex_entry(e, bkey))
        ris_entries_str.append(format_ris_entry(e))

    # Stage 5: Reconstruct full manuscript preserving paragraph breaks
    by_para: "OrderedDict[str, List[str]]" = OrderedDict()
    for s in final_sentences:
        by_para.setdefault(s["paragraph_id"], []).append(s["final_text"])
    body_text = "\n\n".join(" ".join(sents) for sents in by_para.values()).strip()

    ref_heading = analysis.get("ref_heading") or "## References"
    if not ref_heading.startswith("#"):
        ref_heading = f"## {ref_heading}"
    bib_block = "\n".join(fb["formatted_entry"] for fb in formatted_bib)
    updated_manuscript = f"{body_text}\n\n{ref_heading}\n\n{bib_block}\n"

    synced = {
        "target_style": style,
        "citation_system": system,
        "retain_uncited_library": retain_uncited,
        "cited_ref_ids": cited_order,
        "bibliography_ref_ids": bib_ref_ids,
        "final_sentences": final_sentences,
        "renumbering_map": renumbering_map,
        "placement_notes": placement_notes,
        "formatted_bibliography": formatted_bib,
        "updated_manuscript": updated_manuscript,
        "bibtex_export": "\n\n".join(bib_entries_str) + ("\n" if bib_entries_str else ""),
        "ris_export": "\n\n".join(ris_entries_str) + ("\n" if ris_entries_str else ""),
    }
    save_json(Path(args.out), synced)


if __name__ == "__main__":
    main()
