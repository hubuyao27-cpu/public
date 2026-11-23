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

from common import detect_script_lang, parse_author_name


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


def compute_author_year_suffixes(records: List[Dict[str, Any]]) -> Dict[str, str]:
    """Assign 'a', 'b', 'c' suffixes when same first-author surname & year collide."""
    groups: Dict[Tuple[str, Any], List[Dict[str, Any]]] = {}
    for r in records:
        authors = r.get("authors") or []
        fam = parse_author_name(authors[0])["surname_key"] if authors else ""
        yr = r.get("year")
        if fam and yr:
            groups.setdefault((fam, yr), []).append(r)
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
    lang = rec.get("language") or detect_script_lang(title or venue)

    # --- GB/T 7714-2015 (numeric or author-year) ---
    if style in ("gbt7714_2015_numeric", "gbt7714_2015_author_year"):
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
            head_parts.append(auth_str)
        if title:
            head_parts.append(f"{title}{tag}")
        else:
            head_parts.append(tag)
        head = ". ".join(head_parts)

        tail = ""
        if doc_type in ("journal", "preprint", "electronic", "electronic_or_preprint"):
            v_str = venue or ("arXiv preprint" if arxiv_id else "")
            if arxiv_id and arxiv_id.lower() not in v_str.lower():
                v_str = f"{v_str} {arxiv_id}".strip() if v_str else f"arXiv:{arxiv_id}"
            date_part = yr_disp
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
        elif doc_type == "conference":
            pub_loc = f"{city}: {publisher}" if (city and publisher) else (publisher or city)
            conf_parts = [p for p in [venue, pub_loc, yr_disp] if p]
            conf_str = ", ".join(conf_parts)
            if pages:
                conf_str = f"{conf_str}: {pages}" if conf_str else pages
            tail = f"//{conf_str}" if conf_str else ""
        elif doc_type in ("book", "thesis", "report"):
            pub_loc = f"{city}: {publisher}" if (city and publisher) else (publisher or venue or city)
            bp = [p for p in [pub_loc, yr_disp] if p]
            tail = ", ".join(bp)
            if pages:
                tail = f"{tail}: {pages}" if tail else pages
        else:
            tail = ", ".join(p for p in [venue or publisher, yr_disp, pages] if p)

        entry = f"{head}. {tail}." if tail else f"{head}."
        if doi:
            entry += f" DOI:{doi}."
        elif url and is_online:
            entry += f" {url}."
        entry = re.sub(r"\.\.+", ".", entry).strip()
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
        elif publisher:
            parts.append(f"{publisher}.")
        if doi:
            parts.append(f"https://doi.org/{doi}")
        elif url:
            parts.append(url)
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    # --- IEEE ---
    if style == "ieee":
        auth_str = _fmt_ieee_authors(authors)
        segs = []
        if auth_str:
            segs.append(auth_str)
        if title:
            segs.append(f'"{title},"')
        if venue:
            segs.append(f"*{venue}*,")
        if vol:
            segs.append(f"vol. {vol},")
        if issue:
            segs.append(f"no. {issue},")
        if pages:
            segs.append(f"pp. {pages.replace('-', '–')},")
        if yr_disp:
            segs.append(f"{yr_disp}.")
        body = " ".join(segs).rstrip(",")
        if not body.endswith("."):
            body += "."
        if doi:
            body += f" doi: {doi}."
        prefix = f"[{number}] " if number is not None else ""
        return re.sub(r"\s+", " ", prefix + body).strip()

    # --- Nature ---
    if style == "nature":
        auth_str = _fmt_nature_authors(authors)
        segs = [p for p in [auth_str, f"{title}." if title else ""] if p]
        v_str = f"*{venue}*" if venue else ""
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
        prefix = f"{number}. " if number is not None else ""
        return re.sub(r"\s+", " ", prefix + " ".join(segs)).strip()

    # --- Vancouver ---
    if style == "vancouver":
        auth_str = _fmt_vancouver_authors(authors)
        segs = [p for p in [f"{auth_str}." if auth_str else "", f"{title}." if title else ""] if p]
        if venue:
            loc = f"{venue}. {yr_disp}" if yr_disp else venue
            if vol:
                loc += f";{vol}"
                if issue:
                    loc += f"({issue})"
            if pages:
                loc += f":{pages}"
            segs.append(f"{loc}.")
        if doi:
            segs.append(f"doi:{doi}")
        prefix = f"{number}. " if number is not None else ""
        return re.sub(r"\s+", " ", prefix + " ".join(segs)).strip()

    # --- Chicago Author-Date ---
    auth_str = _fmt_apa_authors(authors, max_authors=10).replace(", & ", ", and ")
    segs = [p for p in [f"{auth_str}." if auth_str else "", f"{yr_disp}." if yr_disp else "", f'"{title}."' if title else ""] if p]
    if venue:
        v_str = f"*{venue}*"
        if vol:
            v_str += f" {vol}"
            if issue:
                v_str += f", no. {issue}"
        if pages:
            v_str += f": {pages}"
        segs.append(f"{v_str}.")
    if doi:
        segs.append(f"https://doi.org/{doi}.")
    return re.sub(r"\s+", " ", " ".join(segs)).strip()


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


def _author_year_short_label(rec: Dict[str, Any], year_suffix: str, style: str, narrative: bool = False) -> str:
    authors = rec.get("authors") or []
    yr = str(rec.get("year") or "n.d.") + year_suffix
    if not authors:
        short_t = (rec.get("title") or "Unknown")[:20]
        return f"{short_t} ({yr})" if narrative else f"{short_t}, {yr}"

    parsed = [parse_author_name(a) for a in authors]
    is_zh = parsed[0]["is_cjk"]
    if len(parsed) == 1:
        name_part = parsed[0]["raw"] if is_zh else parsed[0]["family"]
    elif len(parsed) == 2:
        if is_zh:
            sep = "和"
            name_part = f"{parsed[0]['raw']}{sep}{parsed[1]['raw']}"
        else:
            amp = "and" if narrative or style == "chicago_author_date" else "&"
            name_part = f"{parsed[0]['family']} {amp} {parsed[1]['family']}"
    else:
        if is_zh:
            name_part = f"{parsed[0]['raw']}等"
        else:
            name_part = f"{parsed[0]['family']} et al."

    if narrative:
        return f"{name_part} ({yr})"
    return f"{name_part}, {yr}"


def format_in_text_citation(
    entries: List[Tuple[Dict[str, Any], int, str]],
    style: str,
    in_text_format: str,
    narrative: bool = False,
) -> str:
    """entries is a list of (canonical_record, assigned_number, year_suffix)."""
    if not entries:
        return ""

    if style in ("gbt7714_2015_author_year", "apa7", "apa6", "chicago_author_date") or in_text_format == "author_year":
        ordered = sorted(entries, key=lambda item: _author_sort_key(item[0]))
        if narrative and len(ordered) == 1:
            rec, _, ys = ordered[0]
            return _author_year_short_label(rec, ys, style, narrative=True)
        labels = [_author_year_short_label(rec, ys, style, narrative=False) for rec, _, ys in ordered]
        return "(" + "; ".join(labels) + ")"

    if in_text_format == "latex_cite":
        keys = [
            (rec.get("source_keys") or [rec["ref_id"]])[0]
            for rec, _, _ in entries
        ]
        return f"\\cite{{{', '.join(keys)}}}"

    nums = [num for _, num, _ in entries]
    if style == "ieee" or in_text_format == "bracket_separate":
        uniq = sorted(set(nums))
        if len(uniq) >= 3 and uniq == list(range(uniq[0], uniq[-1] + 1)):
            return f"[{uniq[0]}]–[{uniq[-1]}]"
        return ", ".join(f"[{n}]" for n in uniq)

    if in_text_format == "footnote":
        circled = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
        return "".join(circled[n - 1] if 1 <= n <= len(circled) else f"[{n}]" for n in sorted(set(nums)))

    sep = "–" if style == "nature" else "-"
    compressed = _compress_numeric_ranges(nums, sep=sep)
    if in_text_format == "superscript_bracket":
        return f"<sup>[{compressed}]</sup>"
    if in_text_format == "superscript_md":
        return f"^[{compressed}]"
    if in_text_format == "superscript_html":
        return f"<sup>{compressed}</sup>"
    return f"[{compressed}]"


def export_bibtex(records: List[Dict[str, Any]]) -> str:
    tmap = {
        "journal": "article", "conference": "inproceedings", "proceedings": "proceedings",
        "book": "book", "book_chapter": "incollection", "thesis": "phdthesis",
        "report": "techreport", "preprint": "misc", "electronic": "misc", "other": "misc",
    }
    blocks: List[str] = []
    used_keys: set[str] = set()
    for rec in records:
        raw_k = (rec.get("source_keys") or [""])[0]
        if not raw_k or raw_k.isdigit():
            a0 = parse_author_name(rec["authors"][0])["surname_key"] if rec.get("authors") else "ref"
            raw_k = f"{a0 or 'ref'}{rec.get('year') or 'nd'}_{rec['ref_id'].lower()}"
        key = re.sub(r"[^A-Za-z0-9_:\-]", "", raw_k) or rec["ref_id"].lower()
        while key in used_keys:
            key += "_"
        used_keys.add(key)

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
        if rec.get("doi"):
            lines.append(f"  doi = {{{rec['doi']}}},")
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
        if pinfo.get("start"):
            lines.append(f"SP  - {pinfo['start']}")
        if pinfo.get("end"):
            lines.append(f"EP  - {pinfo['end']}")
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
