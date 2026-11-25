#!/usr/bin/env python3
"""Pure-Python academic reference & in-text citation formatting engine.

Supports:
- gbt7714_2015_numeric (GB/T 7714—2015 顺序编码制)
- gbt7714_2015_author_year (GB/T 7714—2015 著者—出版年制)
- apa7, apa6
- ieee
- nature
- vancouver
- chicago_author_date
Plus lossless BibTeX, RIS, CSL-JSON, and CSV exporters.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Dict, List, Tuple

from common import detect_script_lang, parse_author_name, parse_page_range


def compute_canonical_bibtex_keys(records: List[Dict[str, Any]]) -> Dict[str, str]:
    """Compute one deterministic BibTeX/LaTeX key per ref_id shared across \\cite, \\bibitem, and .bib."""
    keys: Dict[str, str] = {}
    used_keys: set[str] = set()
    for rec in records:
        rid = rec["ref_id"]
        raw_k = ""
        for sk in rec.get("source_keys") or []:
            if sk and not str(sk).isdigit():
                raw_k = str(sk)
                break
        if not raw_k:
            a0 = parse_author_name(rec["authors"][0])["surname_key"] if rec.get("authors") else "ref"
            raw_k = f"{a0 or 'ref'}{rec.get('year') or 'nd'}_{rid.lower()}"
        key = re.sub(r"[^A-Za-z0-9_:\-]", "", raw_k) or rid.lower()
        while key in used_keys:
            key += "_"
        used_keys.add(key)
        keys[rid] = key
    return keys


def _author_sort_key(record: Dict[str, Any]) -> Tuple[str, int, str]:
    authors = record.get("authors") or []
    if authors:
        p = parse_author_name(authors[0])
        a_key = (p["surname_key"] or p["raw"]).lower()
    else:
        a_key = "zzzz"
    yr = int(record.get("year") or 9999)
    t_key = (record.get("title") or "").lower()
    return (a_key, yr, t_key)


def _in_text_author_group_key(rec: Dict[str, Any], style: str = "apa7", style_rules: Dict[str, Any] | None = None) -> str:
    """Compute the rendered in-text author label key for accurate a/b suffix collision detection."""
    authors = rec.get("authors") or []
    if not authors:
        return (rec.get("title") or "unknown")[:20].lower()
    parsed = [parse_author_name(a) for a in authors]
    default_max = {"apa7": 2, "apa6": 5, "chicago_author_date": 3, "gbt7714_2015_author_year": 2}.get(style, 2)
    max_before_et_al = int(
        ((style_rules or {}).get("supported_styles", {}).get(style, {}) or {}).get("in_text_max_authors_before_et_al", default_max)
    )
    if len(parsed) <= max_before_et_al:
        return "|".join((p["surname_key"] or p["raw"]).lower() for p in parsed)
    return f"{(parsed[0]['surname_key'] or parsed[0]['raw']).lower()}|etal"


def compute_author_year_suffixes(
    records: List[Dict[str, Any]],
    style: str = "apa7",
    style_rules: Dict[str, Any] | None = None,
) -> Dict[str, str]:
    """Assign 'a', 'b', 'c' suffixes only when rendered in-text author label & year actually collide."""
    groups: Dict[Tuple[str, Any], List[Dict[str, Any]]] = {}
    for r in records:
        yr = r.get("year")
        if yr and (r.get("authors") or r.get("title")):
            akey = _in_text_author_group_key(r, style=style, style_rules=style_rules)
            groups.setdefault((akey, yr), []).append(r)
    suffixes: Dict[str, str] = {}
    for _, grp in groups.items():
        if len(grp) > 1:
            ordered = sorted(grp, key=lambda x: (x.get("title") or "").lower())
            for idx, r in enumerate(ordered):
                suffixes[r["ref_id"]] = chr(ord("a") + idx)
    return suffixes


def sort_records_for_style(records: List[Dict[str, Any]], style: str, first_appearance_ids: List[str]) -> List[Dict[str, Any]]:
    if style in ("gbt7714_2015_author_year", "apa7", "apa6", "chicago_author_date"):
        by_id = {r["ref_id"]: r for r in records}
        target = [by_id[rid] for rid in first_appearance_ids if rid in by_id] if first_appearance_ids else list(records)
        return sorted(target, key=_author_sort_key)
    by_id = {r["ref_id"]: r for r in records}
    return [by_id[rid] for rid in first_appearance_ids if rid in by_id]


def _fmt_gbt_authors(authors: List[str], lang: str) -> str:
    if not authors:
        return ""
    fmt_list: List[str] = []
    for a in authors[:3]:
        p = parse_author_name(a)
        if p["is_corporate"]:
            fmt_list.append(p["raw"])
        elif p["is_cjk"]:
            fmt_list.append(p["raw"])
        else:
            fam_up = p["family"].upper()
            inits = p["initials"]
            fmt_list.append(f"{fam_up} {inits}".strip() if inits else fam_up)
    joined = ", ".join(fmt_list)
    if len(authors) > 3:
        is_zh = lang == "zh" or any(parse_author_name(a)["is_cjk"] for a in authors[:3])
        joined += ", 等" if is_zh else ", et al"
    return joined


def _fmt_apa_authors(authors: List[str], max_authors: int = 20) -> str:
    if not authors:
        return ""
    formatted: List[str] = []
    for a in authors:
        p = parse_author_name(a)
        if p["is_corporate"] or p["is_cjk"]:
            formatted.append(p["raw"])
        else:
            inits = " ".join(f"{ch}." for ch in p["initials"].split() if ch)
            formatted.append(f"{p['family']}, {inits}".strip(", "))
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) <= max_authors:
        return ", ".join(formatted[:-1]) + f", & {formatted[-1]}"
    if max_authors == 20:
        return ", ".join(formatted[:19]) + f", ... {formatted[-1]}"
    return ", ".join(formatted[:6]) + f", ... {formatted[-1]}"


def _fmt_ieee_authors(authors: List[str]) -> str:
    if not authors:
        return ""
    if len(authors) > 6:
        p0 = parse_author_name(authors[0])
        inits = " ".join(f"{ch}." for ch in p0["initials"].split() if ch)
        return f"{inits} {p0['family']} et al.".strip()
    formatted: List[str] = []
    for a in authors:
        p = parse_author_name(a)
        if p["is_corporate"] or p["is_cjk"]:
            formatted.append(p["raw"])
        else:
            inits = " ".join(f"{ch}." for ch in p["initials"].split() if ch)
            formatted.append(f"{inits} {p['family']}".strip())
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]} and {formatted[1]}"
    return ", ".join(formatted[:-1]) + f", and {formatted[-1]}"


def _fmt_nature_authors(authors: List[str]) -> str:
    if not authors:
        return ""
    if len(authors) > 5:
        p0 = parse_author_name(authors[0])
        inits = " ".join(f"{ch}." for ch in p0["initials"].split() if ch)
        return f"{p0['family']}, {inits} et al.".strip()
    formatted: List[str] = []
    for a in authors:
        p = parse_author_name(a)
        if p["is_corporate"] or p["is_cjk"]:
            formatted.append(p["raw"])
        else:
            inits = " ".join(f"{ch}." for ch in p["initials"].split() if ch)
            formatted.append(f"{p['family']}, {inits}".strip(", "))
    if len(formatted) == 1:
        return formatted[0]
    return ", ".join(formatted[:-1]) + f" & {formatted[-1]}"


def _fmt_vancouver_authors(authors: List[str]) -> str:
    if not authors:
        return ""
    formatted: List[str] = []
    for a in authors[:6]:
        p = parse_author_name(a)
        if p["is_corporate"] or p["is_cjk"]:
            formatted.append(p["raw"])
        else:
            formatted.append(f"{p['family']} {p['initials_compact']}".strip())
    out = ", ".join(formatted)
    if len(authors) > 6:
        out += ", et al"
    return out


def format_reference_entry(
    rec: Dict[str, Any],
    style: str,
    number: int | None = None,
    year_suffix: str = "",
) -> str:
    doc_type = rec.get("doc_type") or "journal"
    authors = rec.get("authors") or []
    title = (rec.get("title") or "").strip().rstrip(".")
    venue = (rec.get("venue") or "").strip().rstrip(".")
    year = str(rec.get("year") or "").strip()
    yr_disp = f"{year}{year_suffix}" if year else ""
    vol = str(rec.get("volume") or "").strip()
    issue = str(rec.get("issue") or "").strip()
    pages = str(rec.get("pages") or "").strip()
    doi = str(rec.get("doi") or "").strip()
    arxiv_id = str(rec.get("arxiv_id") or "").strip()
    url = str(rec.get("url") or "").strip()
    publisher = str(rec.get("publisher") or "").strip()
    city = str(rec.get("city") or "").strip()
    patent_number = str(rec.get("patent_number") or "").strip()
    standard_number = str(rec.get("standard_number") or "").strip()
    access_date = str(rec.get("access_date") or "").strip()
    lang = rec.get("language") or detect_script_lang(title or venue)

    def _clean_double_dots(s: str) -> str:
        # Collapse unintended double periods (e.g. 'Doe, J..') while preserving APA ellipsis '...'
        return re.sub(r"(?<!\.)\.\.(?!\.)", ".", s)

    # --- GB/T 7714-2015 (numeric or author-year) ---
    if style in ("gbt7714_2015_numeric", "gbt7714_2015_author_year"):
        is_ay = style == "gbt7714_2015_author_year"
        type_codes = {
            "journal": "J", "conference": "C", "proceedings": "C", "book": "M",
            "book_chapter": "M", "thesis": "D", "report": "R", "standard": "S",
            "patent": "P", "newspaper": "N", "electronic": "EB", "preprint": "EB",
            "electronic_or_preprint": "EB", "other": "Z",
        }
        code = type_codes.get(doc_type, "Z")
        is_online = bool(url or (code in ("EB",))) and (code == "EB" or not (vol or pages))
        tag = f"[{code}/OL]" if is_online and not code.endswith("/OL") else f"[{code}]"

        auth_str = _fmt_gbt_authors(authors, lang)
        head_parts = []
        if auth_str:
            head_parts.append(auth_str.rstrip("."))
        if is_ay and yr_disp:
            head_parts.append(yr_disp)
        if title:
            if doc_type == "standard" and standard_number and standard_number not in title:
                head_parts.append(f"{title}: {standard_number}{tag}")
            elif doc_type == "patent" and patent_number and patent_number not in title:
                head_parts.append(f"{title}: {patent_number}{tag}")
            else:
                head_parts.append(f"{title}{tag}")
        else:
            head_parts.append(tag)
        head = ". ".join(head_parts)

        tail = ""
        tail_sep = ". "
        yr_in_tail = "" if is_ay else yr_disp
        if doc_type in ("journal", "preprint", "electronic", "electronic_or_preprint"):
            v_str = venue or ("arXiv preprint" if arxiv_id else "")
            # Never append arxiv_id onto a published journal venue
            if arxiv_id and (doc_type != "journal" or not venue) and arxiv_id.lower() not in v_str.lower():
                v_str = f"{v_str} {arxiv_id}".strip() if v_str else f"arXiv:{arxiv_id}"
            date_part = yr_in_tail
            vi_part = ""
            if vol and issue:
                vi_part = f"{vol}({issue})"
            elif vol:
                vi_part = vol
            elif issue:
                vi_part = f"({issue})"
            loc_str = ""
            if date_part and vi_part:
                loc_str = f"{date_part}, {vi_part}"
            elif date_part or vi_part:
                loc_str = date_part or vi_part
            if pages:
                loc_str = f"{loc_str}: {pages}" if loc_str else pages

            if v_str and loc_str:
                tail = f"{v_str}, {loc_str}"
            else:
                tail = v_str or loc_str
        elif doc_type in ("conference", "proceedings"):
            pub_loc = f"{city}: {publisher}" if (city and publisher) else (publisher or city)
            conf_parts = [p for p in [venue, pub_loc, yr_in_tail] if p]
            conf_str = ", ".join(conf_parts)
            if pages:
                conf_str = f"{conf_str}: {pages}" if conf_str else pages
            tail = conf_str
            tail_sep = "//"
        elif doc_type in ("book", "book_chapter", "thesis", "report"):
            pub_loc = f"{city}: {publisher}" if (city and publisher) else (publisher or venue or city)
            bp = [p for p in [pub_loc, yr_in_tail] if p]
            tail = ", ".join(bp)
            if pages:
                tail = f"{tail}: {pages}" if tail else pages
        elif doc_type == "patent":
            tail = ", ".join(p for p in [patent_number if patent_number and patent_number not in head else "", yr_in_tail] if p)
        elif doc_type == "standard":
            pub_loc = f"{city}: {publisher}" if (city and publisher) else (publisher or venue or "")
            tail = ", ".join(p for p in [pub_loc, yr_in_tail] if p)
        else:
            tail = ", ".join(p for p in [venue or publisher, yr_in_tail, pages] if p)

        entry = f"{head}{tail_sep}{tail}." if tail else f"{head}."
        if access_date and is_online:
            entry = entry.rstrip(".") + f"[{access_date}]."
        if doi:
            entry += f" DOI:{doi}."
        elif url and is_online:
            entry += f" {url}."
        entry = _clean_double_dots(entry).strip()
        if style == "gbt7714_2015_numeric" and number is not None:
            return f"[{number}] {entry}"
        return entry

    # --- APA 7th / APA 6th ---
    if style in ("apa7", "apa6"):
        max_a = 20 if style == "apa7" else 7
        auth_str = _fmt_apa_authors(authors, max_authors=max_a)
        yr_block = f"({yr_disp or 'n.d.'})."
        parts = [p for p in [auth_str, yr_block] if p]
        if title:
            parts.append(f"{title}.")
        if venue:
            v_str = f"*{venue}*"
            if vol:
                v_str += f", *{vol}*"
                if issue:
                    v_str += f"({issue})"
            if pages:
                v_str += f", {pages}"
            parts.append(f"{v_str}.")
        elif arxiv_id:
            parts.append(f"*arXiv preprint arXiv:{arxiv_id}*.")
        elif publisher:
            parts.append(f"{publisher}.")
        if doi:
            parts.append(f"https://doi.org/{doi}")
        elif url:
            parts.append(url)
        return _clean_double_dots(re.sub(r"\s+", " ", " ".join(parts))).strip()

    # --- IEEE ---
    if style == "ieee":
        auth_str = _fmt_ieee_authors(authors)
        segs = []
        if auth_str:
            segs.append(f"{auth_str},")
        if title:
            segs.append(f'"{title},"')
        if venue:
            segs.append(f"*{venue}*,")
        elif arxiv_id:
            segs.append(f"*arXiv preprint arXiv:{arxiv_id}*,")
        elif publisher:
            pub_loc = f"{city}: {publisher}" if city else publisher
            segs.append(f"{pub_loc},")
        if vol:
            segs.append(f"vol. {vol},")
        if issue:
            segs.append(f"no. {issue},")
        if pages:
            segs.append(f"pp. {pages.replace('-', '–')},")
        if yr_disp:
            segs.append(f"{yr_disp}.")
        body = " ".join(segs).rstrip(",")
        if body and not body.endswith("."):
            body += "."
        if doi:
            body += f" doi: {doi}."
        elif url:
            body += f" [Online]. Available: {url}"
        prefix = f"[{number}] " if number is not None else ""
        return _clean_double_dots(re.sub(r"\s+", " ", prefix + body)).strip()

    # --- Nature ---
    if style == "nature":
        auth_str = _fmt_nature_authors(authors)
        segs = [p for p in [auth_str, f"{title}." if title else ""] if p]
        v_str = f"*{venue}*" if venue else (f"Preprint at arXiv:{arxiv_id}" if arxiv_id else (f"({publisher})" if publisher else ""))
        if vol:
            v_str += f" **{vol}**"
        if pages:
            v_str += f", {pages.replace('-', '–')}"
        if yr_disp:
            v_str += f" ({yr_disp})"
        if v_str:
            segs.append(f"{v_str.strip()}.")
        if doi:
            segs.append(f"https://doi.org/{doi}")
        elif url:
            segs.append(url)
        prefix = f"{number}. " if number is not None else ""
        return _clean_double_dots(re.sub(r"\s+", " ", prefix + " ".join(segs))).strip()

    # --- Vancouver ---
    if style == "vancouver":
        auth_str = _fmt_vancouver_authors(authors)
        segs = [p for p in [f"{auth_str.rstrip('.')}." if auth_str else "", f"{title}." if title else ""] if p]
        if venue:
            loc = f"{venue}. {yr_disp}" if yr_disp else venue
            if vol:
                loc += f";{vol}"
                if issue:
                    loc += f"({issue})"
            if pages:
                loc += f":{pages}"
            segs.append(f"{loc}.")
        else:
            pub_loc = f"{city}: {publisher}" if (city and publisher) else (publisher or (f"arXiv:{arxiv_id}" if arxiv_id else ""))
            fallback_tail = "; ".join(p for p in [pub_loc, yr_disp] if p)
            if fallback_tail:
                segs.append(f"{fallback_tail}.")
        if doi:
            segs.append(f"doi:{doi}")
        elif url:
            segs.append(f"Available from: {url}")
        prefix = f"{number}. " if number is not None else ""
        return _clean_double_dots(re.sub(r"\s+", " ", prefix + " ".join(segs))).strip()

    # --- Chicago Author-Date ---
    auth_str = _fmt_apa_authors(authors, max_authors=10).replace(", & ", ", and ")
    segs = [p for p in [f"{auth_str.rstrip('.')}." if auth_str else "", f"{yr_disp}." if yr_disp else "", f'"{title}."' if title else ""] if p]
    if venue:
        v_str = f"*{venue}*"
        if vol:
            v_str += f" {vol}"
            if issue:
                v_str += f", no. {issue}"
        if pages:
            v_str += f": {pages}"
        segs.append(f"{v_str}.")
    elif arxiv_id:
        segs.append(f"*arXiv preprint arXiv:{arxiv_id}*.")
    elif publisher:
        pub_loc = f"{city}: {publisher}" if city else publisher
        segs.append(f"{pub_loc}.")
    if doi:
        segs.append(f"https://doi.org/{doi}.")
    elif url:
        segs.append(f"{url}.")
    return _clean_double_dots(re.sub(r"\s+", " ", " ".join(segs))).strip()


def _compress_numeric_ranges(nums: List[int], sep: str = "-") -> str:
    if not nums:
        return ""
    uniq = sorted(set(nums))
    ranges: List[str] = []
    start = prev = uniq[0]
    for n in uniq[1:]:
        if n == prev + 1:
            prev = n
        else:
            if prev - start >= 2:
                ranges.append(f"{start}{sep}{prev}")
            elif prev == start + 1:
                ranges.append(f"{start}, {prev}")
            else:
                ranges.append(str(start))
            start = prev = n
    if prev - start >= 2:
        ranges.append(f"{start}{sep}{prev}")
    elif prev == start + 1:
        ranges.append(f"{start}, {prev}")
    else:
        ranges.append(str(start))
    return ", ".join(ranges)


def _author_short_name(rec: Dict[str, Any], style: str, narrative: bool = False, style_rules: Dict[str, Any] | None = None) -> str:
    authors = rec.get("authors") or []
    if not authors:
        return (rec.get("title") or "Unknown")[:20]

    parsed = [parse_author_name(a) for a in authors]
    is_zh = parsed[0]["is_cjk"]
    default_max = {"apa7": 2, "apa6": 5, "chicago_author_date": 3, "gbt7714_2015_author_year": 2}.get(style, 2)
    max_before_et_al = int(
        ((style_rules or {}).get("supported_styles", {}).get(style, {}) or {}).get("in_text_max_authors_before_et_al", default_max)
    )
    if len(parsed) == 1:
        return parsed[0]["raw"] if is_zh else parsed[0]["family"]
    if len(parsed) <= max_before_et_al:
        names = [p["raw"] if p["is_cjk"] else p["family"] for p in parsed]
        if is_zh:
            return "和".join(names) if len(names) == 2 else "、".join(names[:-1]) + f"和{names[-1]}"
        amp = "and" if narrative or style == "chicago_author_date" else "&"
        if len(names) == 2:
            return f"{names[0]} {amp} {names[1]}"
        return ", ".join(names[:-1]) + f", {amp} {names[-1]}"
    if is_zh:
        return f"{parsed[0]['raw']}等"
    return f"{parsed[0]['family']} et al."


def _author_year_short_label(
    rec: Dict[str, Any],
    year_suffix: str,
    style: str,
    narrative: bool = False,
    style_rules: Dict[str, Any] | None = None,
) -> str:
    yr = str(rec.get("year") or "n.d.") + year_suffix
    name_part = _author_short_name(rec, style, narrative=narrative, style_rules=style_rules)
    if narrative:
        return f"{name_part} ({yr})"
    return f"{name_part}, {yr}"


def _compress_ieee_ranges(nums: List[int]) -> str:
    uniq = sorted(set(nums))
    if not uniq:
        return ""
    chunks: List[str] = []
    start = prev = uniq[0]
    for n in uniq[1:]:
        if n == prev + 1:
            prev = n
        else:
            if prev - start >= 2:
                chunks.append(f"[{start}]–[{prev}]")
            elif prev == start + 1:
                chunks.append(f"[{start}], [{prev}]")
            else:
                chunks.append(f"[{start}]")
            start = prev = n
    if prev - start >= 2:
        chunks.append(f"[{start}]–[{prev}]")
    elif prev == start + 1:
        chunks.append(f"[{start}], [{prev}]")
    else:
        chunks.append(f"[{start}]")
    return ", ".join(chunks)


def format_in_text_citation(
    entries: List[Tuple[Dict[str, Any], int, str]],
    style: str,
    in_text_format: str,
    narrative: bool = False,
    style_rules: Dict[str, Any] | None = None,
    bibtex_keys: Dict[str, str] | None = None,
) -> str:
    """entries is a list of (canonical_record, assigned_number, year_suffix)."""
    if not entries:
        return ""

    is_ay_style = style in ("gbt7714_2015_author_year", "apa7", "apa6", "chicago_author_date")

    if in_text_format == "latex_cite":
        keys = [
            (bibtex_keys or {}).get(rec["ref_id"])
            or next((str(k) for k in (rec.get("source_keys") or []) if k and not str(k).isdigit()), rec["ref_id"].lower())
            for rec, _, _ in entries
        ]
        if is_ay_style:
            cmd = "\\citet" if narrative else "\\citep"
            return f"{cmd}{{{', '.join(keys)}}}"
        return f"\\cite{{{', '.join(keys)}}}"

    if is_ay_style or in_text_format == "author_year":
        ordered = sorted(entries, key=lambda item: _author_sort_key(item[0]))
        if narrative and len(ordered) == 1:
            rec, _, ys = ordered[0]
            return _author_year_short_label(rec, ys, style, narrative=True, style_rules=style_rules)
        labels = [_author_year_short_label(rec, ys, style, narrative=False, style_rules=style_rules) for rec, _, ys in ordered]
        return "(" + "; ".join(labels) + ")"

    nums = [num for _, num, _ in entries]
    if style == "ieee" or in_text_format == "bracket_separate":
        marker = _compress_ieee_ranges(nums)
    elif in_text_format == "footnote":
        circled = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
        marker = "".join(circled[n - 1] if 1 <= n <= len(circled) else f"<sup>{n}</sup>" for n in sorted(set(nums)))
    else:
        sep = "–" if style == "nature" else "-"
        compressed = _compress_numeric_ranges(nums, sep=sep)
        if in_text_format == "superscript_bracket":
            marker = f"<sup>[{compressed}]</sup>"
        elif in_text_format == "superscript_md":
            marker = f"^[{compressed}]"
        elif in_text_format == "superscript_html":
            marker = f"<sup>{compressed}</sup>"
        else:
            marker = f"[{compressed}]"

    if narrative and len(entries) == 1:
        rec = entries[0][0]
        author_label = _author_short_name(rec, style, narrative=True, style_rules=style_rules)
        if in_text_format in ("superscript_bracket", "superscript_md", "superscript_html", "footnote"):
            return f"{author_label}{marker}"
        return f"{author_label} {marker}"
    return marker


def export_bibtex(records: List[Dict[str, Any]], bibtex_keys: Dict[str, str] | None = None) -> str:
    tmap = {
        "journal": "article", "conference": "inproceedings", "proceedings": "proceedings",
        "book": "book", "book_chapter": "incollection", "thesis": "phdthesis",
        "report": "techreport", "preprint": "misc", "electronic": "misc", "other": "misc",
    }
    key_map = bibtex_keys if bibtex_keys is not None else compute_canonical_bibtex_keys(records)
    blocks: List[str] = []
    for rec in records:
        key = key_map.get(rec["ref_id"], rec["ref_id"].lower())

        btype = tmap.get(rec.get("doc_type", "journal"), "misc")
        lines = [f"@{btype}{{{key},"]
        if rec.get("title"):
            lines.append(f"  title = {{{rec['title']}}},")
        if rec.get("authors"):
            lines.append(f"  author = {{{' and '.join(rec['authors'])}}},")
        if rec.get("year"):
            lines.append(f"  year = {{{rec['year']}}},")
        if rec.get("venue"):
            vfield = "booktitle" if btype == "inproceedings" else "journal"
            lines.append(f"  {vfield} = {{{rec['venue']}}},")
        if rec.get("volume"):
            lines.append(f"  volume = {{{rec['volume']}}},")
        if rec.get("issue"):
            lines.append(f"  number = {{{rec['issue']}}},")
        if rec.get("pages"):
            lines.append(f"  pages = {{{rec['pages'].replace('-', '--')}}},")
        if rec.get("publisher"):
            lines.append(f"  publisher = {{{rec['publisher']}}},")
        if rec.get("city"):
            lines.append(f"  address = {{{rec['city']}}},")
        if rec.get("doi"):
            lines.append(f"  doi = {{{rec['doi']}}},")
        if rec.get("arxiv_id"):
            lines.append(f"  eprint = {{{rec['arxiv_id']}}},")
            lines.append("  archivePrefix = {arXiv},")
        if rec.get("url"):
            lines.append(f"  url = {{{rec['url']}}},")
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def export_ris(records: List[Dict[str, Any]]) -> str:
    tmap = {
        "journal": "JOUR", "conference": "CONF", "book": "BOOK", "book_chapter": "CHAP",
        "thesis": "THES", "report": "RPRT", "patent": "PAT", "standard": "STAND",
        "electronic": "ELEC", "preprint": "UNPB", "other": "GEN",
    }
    blocks: List[str] = []
    for rec in records:
        lines = [f"TY  - {tmap.get(rec.get('doc_type', 'journal'), 'JOUR')}"]
        if rec.get("title"):
            lines.append(f"TI  - {rec['title']}")
        for a in rec.get("authors") or []:
            lines.append(f"AU  - {a}")
        if rec.get("year"):
            lines.append(f"PY  - {rec['year']}")
        if rec.get("venue"):
            lines.append(f"JO  - {rec['venue']}")
        if rec.get("volume"):
            lines.append(f"VL  - {rec['volume']}")
        if rec.get("issue"):
            lines.append(f"IS  - {rec['issue']}")
        pinfo = rec.get("pages_info") or {}
        if not pinfo.get("start") and rec.get("pages"):
            pinfo = parse_page_range(rec.get("pages"))
        if pinfo.get("start"):
            lines.append(f"SP  - {pinfo['start']}")
        if pinfo.get("end"):
            lines.append(f"EP  - {pinfo['end']}")
        if rec.get("publisher"):
            lines.append(f"PB  - {rec['publisher']}")
        if rec.get("doi"):
            lines.append(f"DO  - {rec['doi']}")
        if rec.get("url"):
            lines.append(f"UR  - {rec['url']}")
        lines.append("ER  - ")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def export_csl_json(records: List[Dict[str, Any]]) -> str:
    tmap = {"journal": "article-journal", "conference": "paper-conference", "book": "book", "thesis": "thesis", "report": "report", "preprint": "article", "electronic": "webpage"}
    items = []
    for rec in records:
        csl_auth = []
        for a in rec.get("authors") or []:
            p = parse_author_name(a)
            if p["is_corporate"]:
                csl_auth.append({"literal": p["raw"]})
            elif p["is_cjk"]:
                csl_auth.append({"family": p["family"], "given": p["given"]})
            else:
                csl_auth.append({"family": p["family"], "given": p["given"] or p["initials"]})
        item: Dict[str, Any] = {
            "id": (rec.get("source_keys") or [rec["ref_id"]])[0],
            "type": tmap.get(rec.get("doc_type", "journal"), "article"),
            "title": rec.get("title", ""),
        }
        if csl_auth:
            item["author"] = csl_auth
        if rec.get("year"):
            item["issued"] = {"date-parts": [[int(rec["year"])]]}
        if rec.get("venue"):
            item["container-title"] = rec["venue"]
        if rec.get("volume"):
            item["volume"] = rec["volume"]
        if rec.get("issue"):
            item["issue"] = rec["issue"]
        if rec.get("pages"):
            item["page"] = rec["pages"]
        if rec.get("doi"):
            item["DOI"] = rec["doi"]
        if rec.get("url"):
            item["URL"] = rec["url"]
        items.append(item)
    return json.dumps(items, ensure_ascii=False, indent=2) + "\n"


def export_csv(records: List[Dict[str, Any]], num_map: Dict[str, int] | None = None) -> str:
    cols = [
        "assigned_number", "ref_id", "doc_type", "authors", "year", "title", "venue",
        "volume", "issue", "pages", "doi", "arxiv_id", "doi_verification_status", "missing_required_fields",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    for rec in records:
        rid = rec["ref_id"]
        writer.writerow({
            "assigned_number": (num_map or {}).get(rid, ""),
            "ref_id": rid,
            "doc_type": rec.get("doc_type", ""),
            "authors": "; ".join(rec.get("authors") or []),
            "year": rec.get("year") or "",
            "title": rec.get("title", ""),
            "venue": rec.get("venue", ""),
            "volume": rec.get("volume", ""),
            "issue": rec.get("issue", ""),
            "pages": rec.get("pages", ""),
            "doi": rec.get("doi", ""),
            "arxiv_id": rec.get("arxiv_id", ""),
            "doi_verification_status": rec.get("doi_verification_status", ""),
            "missing_required_fields": ", ".join(rec.get("missing_required_fields") or []),
        })
    return buf.getvalue()
