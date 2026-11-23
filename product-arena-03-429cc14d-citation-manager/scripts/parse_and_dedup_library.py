#!/usr/bin/env python3
"""Parse heterogeneous reference libraries & run explainable 3-tier deduplication."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    author_overlap,
    bilingual_surname_compatible,
    detect_script_lang,
    extract_pdf_text_safe,
    is_valid_doi,
    load_json_safe,
    normalize_arxiv,
    normalize_doi,
    normalize_title,
    normalize_url,
    page_ranges_conflict,
    parse_page_range,
    parse_year,
    read_text_safe,
    save_json,
    split_authors_string,
    strip_latex_accents_and_braces,
    title_similarity,
)


def _infer_source_tier(file_rel: str, fmt: str, venue: str, arxiv_id: str, doi: str) -> Tuple[str, int]:
    low = f"{file_rel} {venue}".lower()
    if fmt == "manuscript_inline_ref":
        return "manuscript_inline_ref", 30
    if any(p in low for p in ("arxiv", "biorxiv", "medrxiv", "chemrxiv", "ssrn", "preprint", "corr", "chinaxiv")):
        return "preprint_arxiv_biorxiv", 50
    if arxiv_id and not venue:
        return "preprint_arxiv_biorxiv", 50
    if fmt == "evidence_pdf":
        return "local_pdf_metadata", 70
    if any(p in low for p in ("wos", "web_of_science", "scopus", "crossref", "pubmed")):
        return "wos_scopus_crossref", 90
    if fmt in ("cnki_txt", "cnki_refworks", "endnote_enw", "endnote_xml"):
        return "cnki_endnote", 75
    if venue and (doi or fmt in ("bibtex", "ris", "csl_json")):
        return "published_peer_reviewed", 100
    return "bibtex_ris_csl", 80


def _map_doc_type(raw_type: str, fmt_family: str, style_rules: Dict[str, Any]) -> str:
    dmap = style_rules.get("doc_type_map", {}).get(fmt_family, {})
    rt = (raw_type or "").strip()
    if rt in dmap:
        return dmap[rt]
    low = rt.lower()
    for k, v in dmap.items():
        if k.lower() == low:
            return v
    if any(w in low for w in ("jour", "article", "期刊")):
        return "journal"
    if any(w in low for w in ("conf", "proc", "meeting", "会议")):
        return "conference"
    if any(w in low for w in ("thesis", "dissert", "学位", "硕士", "博士")):
        return "thesis"
    if any(w in low for w in ("book", "mono", "图书")):
        return "book"
    if any(w in low for w in ("patent", "专利")):
        return "patent"
    if any(w in low for w in ("stand", "标准")):
        return "standard"
    if any(w in low for w in ("preprint", "arxiv", "biorxiv", "unpub")):
        return "preprint"
    if any(w in low for w in ("web", "elec", "online", "url")):
        return "electronic"
    return "journal" if fmt_family == "numbered_list" else "other"


def _make_record(
    raw_id: str,
    src_file: str,
    locator: str,
    fmt: str,
    raw_fields: Dict[str, Any],
    style_rules: Dict[str, Any],
) -> Dict[str, Any]:
    title = strip_latex_accents_and_braces(str(raw_fields.get("title") or "")).strip()
    title_translated = strip_latex_accents_and_braces(str(raw_fields.get("title_translated") or "")).strip()
    authors = split_authors_string(raw_fields.get("authors"))
    year, year_warn = parse_year(raw_fields.get("year"))
    venue = strip_latex_accents_and_braces(str(raw_fields.get("venue") or "")).strip()
    volume = str(raw_fields.get("volume") or "").strip()
    issue = str(raw_fields.get("issue") or "").strip()
    pages_info = parse_page_range(raw_fields.get("pages"))
    doi_raw = str(raw_fields.get("doi") or "").strip()
    doi = normalize_doi(doi_raw)
    url = normalize_url(raw_fields.get("url"))
    arxiv_id = normalize_arxiv(str(raw_fields.get("arxiv_id") or "") + " " + url + " " + venue + " " + doi_raw)
    if not doi and "doi.org/" in url.lower():
        doi = normalize_doi(url)

    doc_type = raw_fields.get("doc_type") or _map_doc_type(str(raw_fields.get("raw_type") or ""), raw_fields.get("type_family", "bibtex"), style_rules)
    if any(p in venue.lower() for p in ("arxiv", "biorxiv", "medrxiv", "chemrxiv", "ssrn", "preprint")) and doc_type in ("other", "journal", "electronic_or_preprint"):
        doc_type = "preprint"

    lang = detect_script_lang(title or venue)
    tier_name, tier_score = _infer_source_tier(src_file, fmt, venue, arxiv_id, doi)

    warnings = []
    if year_warn:
        warnings.append(year_warn)
    if doi_raw and not is_valid_doi(doi):
        warnings.append(f"invalid_doi_syntax:{doi_raw}")
    if pages_info["raw"] and not pages_info["valid"]:
        warnings.append(f"inverted_page_range:{pages_info['raw']}")

    return {
        "raw_id": raw_id,
        "source_file": src_file,
        "source_locator": locator,
        "source_format": fmt,
        "source_tier": tier_name,
        "source_precedence": tier_score,
        "source_key": str(raw_fields.get("source_key") or "").strip(),
        "original_list_number": raw_fields.get("original_list_number"),
        "doc_type": doc_type,
        "language": lang,
        "title": title,
        "title_translated": title_translated,
        "authors": authors,
        "year": year,
        "venue": venue,
        "volume": volume,
        "issue": issue,
        "pages": pages_info["normalized"],
        "pages_info": pages_info,
        "doi": doi if is_valid_doi(doi) else "",
        "doi_raw": doi_raw,
        "arxiv_id": arxiv_id,
        "pmid": str(raw_fields.get("pmid") or "").strip(),
        "url": url,
        "publisher": strip_latex_accents_and_braces(str(raw_fields.get("publisher") or "")).strip(),
        "city": strip_latex_accents_and_braces(str(raw_fields.get("city") or "")).strip(),
        "abstract": strip_latex_accents_and_braces(str(raw_fields.get("abstract") or "")).strip(),
        "patent_number": str(raw_fields.get("patent_number") or "").strip(),
        "standard_number": str(raw_fields.get("standard_number") or "").strip(),
        "access_date": str(raw_fields.get("access_date") or "").strip(),
        "parse_warnings": warnings,
    }


def parse_bibtex(text: str, src_rel: str, style_rules: Dict[str, Any], start_idx: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    warnings: List[str] = []
    strings: Dict[str, str] = {}

    i = 0
    n = len(text)
    line_starts = [0]
    for idx, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(idx + 1)

    def pos_to_line(pos: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if line_starts[mid] <= pos:
                lo = mid + 1
            else:
                hi = mid - 1
        return hi + 1

    while i < n:
        at = text.find("@", i)
        if at == -1:
            break
        m = re.match(r"@([A-Za-z]+)\s*([({])", text[at:])
        if not m:
            i = at + 1
            continue
        etype = m.group(1).lower()
        open_ch = m.group(2)
        close_ch = "}" if open_ch == "{" else ")"
        start_line = pos_to_line(at)

        j = at + m.end()
        depth = 1
        in_quote = False
        while j < n and depth > 0:
            ch = text[j]
            if ch == '"' and (j == 0 or text[j - 1] != "\\"):
                in_quote = not in_quote
            elif not in_quote:
                if ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
            j += 1

        if depth != 0:
            warnings.append(f"{src_rel}:L{start_line}: unclosed BibTeX entry `@{etype}` salvaged to EOF/next entry")
            body = text[at + m.end() : min(n, at + m.end() + 2000)]
            end_line = pos_to_line(min(n - 1, at + m.end() + len(body)))
            i = n
        else:
            body = text[at + m.end() : j - 1]
            end_line = pos_to_line(j - 1)
            i = j

        if etype in ("comment", "preamble"):
            continue
        if etype == "string":
            ms = re.match(r"\s*([A-Za-z0-9_\-]+)\s*=\s*[\{\"](.*)[\}\"]\s*$", body.strip(), re.S)
            if ms:
                strings[ms.group(1).lower()] = ms.group(2).strip()
            continue

        comma_idx = -1
        bdepth = 0
        for idx, ch in enumerate(body):
            if ch == "{":
                bdepth += 1
            elif ch == "}":
                bdepth = max(0, bdepth - 1)
            elif ch == "," and bdepth == 0:
                comma_idx = idx
                break
        if comma_idx == -1:
            warnings.append(f"{src_rel}:L{start_line}-L{end_line}: malformed BibTeX entry missing key comma")
            continue
        citekey = body[:comma_idx].strip()
        rest = body[comma_idx + 1 :]

        fields: Dict[str, str] = {}
        k = 0
        rn = len(rest)
        while k < rn:
            while k < rn and rest[k] in " \t\r\n,":
                k += 1
            if k >= rn:
                break
            mf = re.match(r"([A-Za-z0-9_\-]+)\s*=\s*", rest[k:])
            if not mf:
                nxt = rest.find("\n", k)
                k = nxt + 1 if nxt != -1 else rn
                continue
            fname = mf.group(1).lower()
            k += mf.end()
            val_parts: List[str] = []
            while k < rn:
                ch = rest[k]
                if ch == "{":
                    d = 1
                    k += 1
                    vstart = k
                    while k < rn and d > 0:
                        if rest[k] == "{":
                            d += 1
                        elif rest[k] == "}":
                            d -= 1
                        k += 1
                    val_parts.append(rest[vstart : k - 1 if d == 0 else k])
                elif ch == '"':
                    k += 1
                    vstart = k
                    while k < rn:
                        if rest[k] == '"' and rest[k - 1] != "\\":
                            break
                        k += 1
                    val_parts.append(rest[vstart:k])
                    if k < rn and rest[k] == '"':
                        k += 1
                else:
                    m_tok = re.match(r"([A-Za-z0-9_\-:.]+)", rest[k:])
                    if m_tok:
                        tok = m_tok.group(1)
                        val_parts.append(strings.get(tok.lower(), tok))
                        k += m_tok.end()
                    else:
                        break
                while k < rn and rest[k] in " \t\r\n":
                    k += 1
                if k < rn and rest[k] == "#":
                    k += 1
                    while k < rn and rest[k] in " \t\r\n":
                        k += 1
                else:
                    break
            fields[fname] = "".join(val_parts).strip()

        if not fields.get("title") and not fields.get("author"):
            warnings.append(f"{src_rel}:L{start_line}-L{end_line}: BibTeX entry `{citekey}` missing both title and author")

        raw_fields = {
            "source_key": citekey,
            "raw_type": etype,
            "type_family": "bibtex",
            "title": fields.get("title", ""),
            "title_translated": fields.get("title_en") or fields.get("translated_title") or "",
            "authors": fields.get("author") or fields.get("editor") or "",
            "year": fields.get("year") or fields.get("date") or "",
            "venue": fields.get("journal") or fields.get("booktitle") or fields.get("howpublished") or fields.get("school") or fields.get("institution") or "",
            "volume": fields.get("volume", ""),
            "issue": fields.get("number") or fields.get("issue") or "",
            "pages": fields.get("pages", ""),
            "doi": fields.get("doi", ""),
            "arxiv_id": fields.get("eprint") or fields.get("archiveprefix") or "",
            "pmid": fields.get("pmid", ""),
            "url": fields.get("url", ""),
            "publisher": fields.get("publisher") or fields.get("school") or fields.get("institution") or "",
            "city": fields.get("address") or fields.get("location") or "",
            "abstract": fields.get("abstract", ""),
            "access_date": fields.get("urldate", ""),
        }
        rid = f"RAW_{start_idx + len(records) + 1:03d}"
        records.append(_make_record(rid, src_rel, f"{src_rel}:L{start_line}-L{end_line}", "bibtex", raw_fields, style_rules))
    return records, warnings


def parse_ris(text: str, src_rel: str, style_rules: Dict[str, Any], start_idx: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    cur: Dict[str, List[str]] = {}
    rec_start = 1
    last_tag = ""

    def flush(end_line: int) -> None:
        if not cur:
            return
        sp = (cur.get("SP") or [""])[0]
        ep = (cur.get("EP") or [""])[0]
        pages = f"{sp}-{ep}" if sp and ep and ep != sp else sp
        raw_fields = {
            "source_key": (cur.get("ID") or [""])[0],
            "raw_type": (cur.get("TY") or ["JOUR"])[0],
            "type_family": "ris",
            "title": (cur.get("TI") or cur.get("T1") or [""])[0],
            "title_translated": (cur.get("TT") or [""])[0],
            "authors": cur.get("AU") or cur.get("A1") or [],
            "year": (cur.get("PY") or cur.get("Y1") or cur.get("DA") or [""])[0],
            "venue": (cur.get("JO") or cur.get("JF") or cur.get("T2") or cur.get("BT") or [""])[0],
            "volume": (cur.get("VL") or [""])[0],
            "issue": (cur.get("IS") or [""])[0],
            "pages": pages,
            "doi": (cur.get("DO") or [""])[0],
            "url": (cur.get("UR") or cur.get("L2") or [""])[0],
            "publisher": (cur.get("PB") or [""])[0],
            "city": (cur.get("CY") or cur.get("PP") or [""])[0],
            "abstract": " ".join(cur.get("AB") or cur.get("N2") or []),
            "access_date": (cur.get("Y2") or [""])[0],
        }
        rid = f"RAW_{start_idx + len(records) + 1:03d}"
        records.append(_make_record(rid, src_rel, f"{src_rel}:L{rec_start}-L{end_line}", "ris", raw_fields, style_rules))
        cur.clear()

    for idx, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"^([A-Z0-9]{2})\s{1,3}-\s*(.*)$", line)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            if tag == "TY":
                if cur:
                    flush(idx - 1)
                rec_start = idx
                cur = {"TY": [val]}
                last_tag = "TY"
            elif tag == "ER":
                flush(idx)
                last_tag = ""
            else:
                cur.setdefault(tag, []).append(val)
                last_tag = tag
        elif line.strip() and last_tag and cur:
            cur[last_tag][-1] = (cur[last_tag][-1] + " " + line.strip()).strip()
    if cur:
        flush(len(text.splitlines()))
    return records, []


def parse_endnote_tagged(text: str, src_rel: str, style_rules: Dict[str, Any], start_idx: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    cur: Dict[str, List[str]] = {}
    rec_start = 1

    def flush(end_line: int) -> None:
        if not cur:
            return
        raw_fields = {
            "raw_type": (cur.get("%0") or ["Journal Article"])[0],
            "type_family": "endnote_tag",
            "title": (cur.get("%T") or [""])[0],
            "authors": cur.get("%A") or [],
            "year": (cur.get("%D") or [""])[0],
            "venue": (cur.get("%J") or cur.get("%B") or [""])[0],
            "volume": (cur.get("%V") or [""])[0],
            "issue": (cur.get("%N") or [""])[0],
            "pages": (cur.get("%P") or [""])[0],
            "doi": (cur.get("%R") or [""])[0],
            "url": (cur.get("%U") or [""])[0],
            "publisher": (cur.get("%I") or [""])[0],
            "city": (cur.get("%C") or [""])[0],
            "abstract": " ".join(cur.get("%X") or []),
            "source_key": (cur.get("%F") or cur.get("%L") or [""])[0],
        }
        rid = f"RAW_{start_idx + len(records) + 1:03d}"
        records.append(_make_record(rid, src_rel, f"{src_rel}:L{rec_start}-L{end_line}", "endnote_enw", raw_fields, style_rules))
        cur.clear()

    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        s = line.strip()
        if not s:
            flush(idx - 1)
            rec_start = idx + 1
            continue
        m = re.match(r"^(%[A-Za-z0-9])\s+(.*)$", s)
        if m:
            if not cur:
                rec_start = idx
            cur.setdefault(m.group(1), []).append(m.group(2).strip())
    flush(len(lines))
    return records, []


def parse_endnote_xml(text: str, src_rel: str, style_rules: Dict[str, Any], start_idx: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(text)
    except Exception as exc:
        return [], [f"{src_rel}: invalid XML ({exc})"]
    for idx, rec in enumerate(root.findall(".//record"), start=1):
        def _txt(xpath: str) -> str:
            el = rec.find(xpath)
            return "".join(el.itertext()).strip() if el is not None else ""
        authors = ["".join(a.itertext()).strip() for a in rec.findall(".//contributors/authors/author") if "".join(a.itertext()).strip()]
        ref_type_el = rec.find(".//ref-type")
        raw_type = ref_type_el.attrib.get("name", "Journal Article") if ref_type_el is not None else "Journal Article"
        raw_fields = {
            "raw_type": raw_type,
            "type_family": "endnote_tag",
            "title": _txt(".//titles/title"),
            "authors": authors,
            "year": _txt(".//dates/year"),
            "venue": _txt(".//periodical/full-title") or _txt(".//titles/secondary-title"),
            "volume": _txt(".//volume"),
            "issue": _txt(".//number"),
            "pages": _txt(".//pages"),
            "doi": _txt(".//electronic-resource-num"),
            "url": _txt(".//urls/related-urls/url"),
            "publisher": _txt(".//publisher"),
            "city": _txt(".//pub-location"),
            "abstract": _txt(".//abstract"),
        }
        rid = f"RAW_{start_idx + len(records) + 1:03d}"
        records.append(_make_record(rid, src_rel, f"{src_rel}:record[{idx}]", "endnote_xml", raw_fields, style_rules))
    return records, []


def parse_cnki_txt(text: str, src_rel: str, style_rules: Dict[str, Any], start_idx: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    lines = text.splitlines()

    if "Title-题名:" in text or "Author-作者:" in text or "SrcDatabase-来源库:" in text:
        blocks = re.split(r"\n\s*\n+", text.strip())
        cur_line = 1
        for blk in blocks:
            kv: Dict[str, str] = {}
            blines = blk.splitlines()
            for bl in blines:
                m = re.match(r"^([A-Za-z\u4e00-\u9fff\-]+)\s*:\s*(.*)$", bl.strip())
                if m:
                    kv[m.group(1).strip()] = m.group(2).strip()
            if kv:
                raw_fields = {
                    "raw_type": kv.get("SrcDatabase-来源库", "Journal Article"),
                    "type_family": "cnki_rt",
                    "title": kv.get("Title-题名") or kv.get("题名") or "",
                    "authors": kv.get("Author-作者") or kv.get("作者") or "",
                    "venue": kv.get("Source-文献来源") or kv.get("文献来源") or "",
                    "year": kv.get("PubTime-发表时间") or kv.get("Year-年") or "",
                    "volume": kv.get("Volume-卷") or "",
                    "issue": kv.get("Period-期") or kv.get("Issue-期") or "",
                    "pages": kv.get("PageCount-页码") or kv.get("Page-页码") or "",
                    "doi": kv.get("DOI") or "",
                    "abstract": kv.get("Summary-摘要") or kv.get("摘要") or "",
                }
                rid = f"RAW_{start_idx + len(records) + 1:03d}"
                records.append(_make_record(rid, src_rel, f"{src_rel}:L{cur_line}-L{cur_line + len(blines) - 1}", "cnki_txt", raw_fields, style_rules))
            cur_line += len(blines) + 1
        return records, []

    cur: Dict[str, List[str]] = {}
    rec_start = 1

    def flush(end_line: int) -> None:
        if not cur:
            return
        sp = (cur.get("SP") or [""])[0]
        ep = (cur.get("EP") or [""])[0]
        pages = (cur.get("OP") or [""])[0] or (f"{sp}-{ep}" if sp and ep else sp)
        raw_fields = {
            "raw_type": (cur.get("RT") or ["Journal Article"])[0],
            "type_family": "cnki_rt",
            "title": (cur.get("T1") or cur.get("TI") or [""])[0],
            "title_translated": (cur.get("TT") or [""])[0],
            "authors": cur.get("A1") or cur.get("AU") or [],
            "year": (cur.get("YR") or cur.get("PY") or [""])[0],
            "venue": (cur.get("JF") or cur.get("JO") or cur.get("T2") or [""])[0],
            "volume": (cur.get("VO") or cur.get("VL") or [""])[0],
            "issue": (cur.get("IS") or [""])[0],
            "pages": pages,
            "doi": (cur.get("DO") or [""])[0],
            "url": (cur.get("UL") or cur.get("UR") or [""])[0],
            "publisher": (cur.get("PB") or [""])[0],
            "abstract": " ".join(cur.get("AB") or []),
            "source_key": (cur.get("ID") or [""])[0],
        }
        rid = f"RAW_{start_idx + len(records) + 1:03d}"
        records.append(_make_record(rid, src_rel, f"{src_rel}:L{rec_start}-L{end_line}", "cnki_refworks", raw_fields, style_rules))
        cur.clear()

    for idx, line in enumerate(lines, start=1):
        s = line.strip()
        if not s:
            flush(idx - 1)
            rec_start = idx + 1
            continue
        m = re.match(r"^([A-Z0-9]{2})\s+(.*)$", s)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            if tag == "RT" and cur:
                flush(idx - 1)
                rec_start = idx
            if not cur:
                rec_start = idx
            cur.setdefault(tag, []).append(val)
    flush(len(lines))
    return records, []


def parse_csl_or_json(text: str, src_rel: str, style_rules: Dict[str, Any], start_idx: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    warnings: List[str] = []
    items: List[Dict[str, Any]] = []
    stripped = text.strip()
    if stripped.startswith("{") and "\n{" in stripped:
        for idx, ln in enumerate(stripped.splitlines(), start=1):
            if ln.strip():
                try:
                    items.append(json.loads(ln))
                except Exception as exc:
                    warnings.append(f"{src_rel}:L{idx}: invalid jsonl line ({exc})")
    else:
        try:
            obj = json.loads(stripped)
            if isinstance(obj, list):
                items = [x for x in obj if isinstance(x, dict)]
            elif isinstance(obj, dict):
                items = obj.get("references") or obj.get("items") or obj.get("records") or [obj]
        except Exception as exc:
            return [], [f"{src_rel}: invalid JSON ({exc})"]

    for idx, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            continue
        year = it.get("year")
        if year is None and isinstance(it.get("issued"), dict):
            dp = it["issued"].get("date-parts") or [[]]
            if dp and dp[0]:
                year = dp[0][0]
        raw_fields = {
            "source_key": it.get("id") or it.get("key") or "",
            "raw_type": it.get("type") or it.get("doc_type") or "article-journal",
            "type_family": "bibtex",
            "title": it.get("title") or "",
            "title_translated": it.get("title_translated") or it.get("title-short") or "",
            "authors": it.get("author") or it.get("authors") or [],
            "year": year,
            "venue": it.get("container-title") or it.get("journal") or it.get("venue") or "",
            "volume": it.get("volume") or "",
            "issue": it.get("issue") or "",
            "pages": it.get("page") or it.get("pages") or "",
            "doi": it.get("DOI") or it.get("doi") or "",
            "url": it.get("URL") or it.get("url") or "",
            "publisher": it.get("publisher") or "",
            "city": it.get("publisher-place") or it.get("city") or "",
            "abstract": it.get("abstract") or "",
        }
        rid = f"RAW_{start_idx + len(records) + 1:03d}"
        records.append(_make_record(rid, src_rel, f"{src_rel}:item[{idx}]", "csl_json", raw_fields, style_rules))
    return records, warnings


def parse_csv_tsv(text: str, src_rel: str, is_tsv: bool, style_rules: Dict[str, Any], start_idx: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    delimiter = "\t" if is_tsv else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    for row_idx, row in enumerate(reader, start=2):
        norm_row = {str(k).strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
        def pick(*keys: str) -> str:
            for k in keys:
                if norm_row.get(k):
                    return norm_row[k]
            return ""
        raw_fields = {
            "source_key": pick("id", "key", "citekey", "bibkey", "编号"),
            "raw_type": pick("type", "doc_type", "文献类型", "类型") or "article",
            "type_family": "bibtex",
            "title": pick("title", "题名", "标题", "article title"),
            "title_translated": pick("title_en", "translated_title", "英文题名"),
            "authors": pick("authors", "author", "作者"),
            "year": pick("year", "pubyear", "年份", "出版年"),
            "venue": pick("journal", "venue", "source", "container_title", "期刊", "来源"),
            "volume": pick("volume", "vol", "卷"),
            "issue": pick("issue", "number", "no", "期"),
            "pages": pick("pages", "page", "页码"),
            "doi": pick("doi"),
            "url": pick("url", "link"),
            "publisher": pick("publisher", "出版社"),
            "abstract": pick("abstract", "summary", "摘要"),
        }
        if raw_fields["title"] or raw_fields["authors"]:
            rid = f"RAW_{start_idx + len(records) + 1:03d}"
            records.append(_make_record(rid, src_rel, f"{src_rel}:L{row_idx}", "csv_tsv", raw_fields, style_rules))
    return records, []


def parse_numbered_ref_lines(lines_with_src: List[Dict[str, str]], style_rules: Dict[str, Any], start_idx: int, fmt_label: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    gbt_code_to_type = {v: k for k, v in style_rules.get("gbt7714_type_codes", {}).items()}

    for idx, item in enumerate(lines_with_src, start=1):
        raw = item["raw"].strip()
        src_rel = item["source_file"]
        if not raw:
            continue
        num = None
        body = raw
        m_num = re.match(r"^(?:\[(\d+)\]|(\d+)[.)])\s*(.*)$", raw)
        if m_num:
            num = int(m_num.group(1) or m_num.group(2))
            body = m_num.group(3).strip()
        elif raw.startswith("\\bibitem"):
            m_bib = re.match(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}\s*(.*)", raw)
            if m_bib:
                body = m_bib.group(2).strip()

        doi = ""
        m_doi = re.search(r"(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)(10\.\d{4,9}/[^\s,;]+)", body, flags=re.I)
        if m_doi:
            doi = m_doi.group(1).rstrip(".,;")
        url = ""
        m_url = re.search(r"(https?://[^\s,]+)", body)
        if m_url and "doi.org" not in m_url.group(1).lower():
            url = m_url.group(1).rstrip(".,;")

        doc_type = "journal"
        m_tag = re.search(r"\[(J|C|M|D|R|S|P|N|EB|DB|CP|Z)(?:/OL)?\]", body)
        title = ""
        authors = ""
        venue = ""
        year = ""
        volume = ""
        issue = ""
        pages = ""

        if m_tag:
            doc_type = gbt_code_to_type.get(m_tag.group(1), "journal")
            before_tag = body[: m_tag.start()].strip()
            after_tag = body[m_tag.end() :].lstrip(". ").strip()
            if ". " in before_tag:
                authors, title = before_tag.split(". ", 1)
            elif "．" in before_tag:
                authors, title = before_tag.split("．", 1)
            else:
                title = before_tag
            m_tail = re.search(r"((?:19|20)\d{2})\s*[,，]\s*(\d+)?(?:\((\d+)\))?\s*[:：]?\s*([A-Za-z0-9\-–—]+)?", after_tag)
            if m_tail:
                venue = after_tag[: m_tail.start()].strip(" .,/：:")
                year = m_tail.group(1) or ""
                volume = m_tail.group(2) or ""
                issue = m_tail.group(3) or ""
                pages = m_tail.group(4) or ""
            else:
                m_yr_only = re.search(r"\b((?:19|20)\d{2})\b", after_tag)
                if m_yr_only:
                    year = m_yr_only.group(1)
                    venue = after_tag[: m_yr_only.start()].strip(" .,/：:")
                else:
                    venue = after_tag.rstrip(".").strip()
            arxiv_body = normalize_arxiv(body)
            if arxiv_body and doc_type in ("journal", "other"):
                doc_type = "preprint"
        else:
            m_yr = re.search(r"\b((?:19|20)\d{2})\b", body)
            if m_yr:
                year = m_yr.group(1)
                authors = body[: m_yr.start()].strip(" .(,")
                after_yr = body[m_yr.end() :].strip(" ).,:")
                parts = [p.strip() for p in re.split(r"\.\s+", after_yr) if p.strip()]
                if parts:
                    title = parts[0]
                if len(parts) > 1:
                    venue = parts[1].split(",")[0].strip()
            else:
                title = body

        raw_fields = {
            "original_list_number": num,
            "source_key": str(num) if num is not None else "",
            "doc_type": doc_type,
            "title": title,
            "authors": authors,
            "year": year,
            "venue": venue,
            "volume": volume,
            "issue": issue,
            "pages": pages,
            "doi": doi,
            "arxiv_id": normalize_arxiv(body),
            "url": url,
        }
        rid = f"RAW_{start_idx + len(records) + 1:03d}"
        records.append(_make_record(rid, src_rel, f"{src_rel}:ref#{num or idx}", fmt_label, raw_fields, style_rules))
    return records


def parse_evidence_pdf(path: Path, src_rel: str, style_rules: Dict[str, Any], start_idx: int) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], List[str]]:
    pdf_data = extract_pdf_text_safe(path)
    warnings: List[str] = []
    if pdf_data.get("error"):
        warnings.append(f"{src_rel}: {pdf_data['error']}")
        return None, {"source_file": src_rel, "error": pdf_data["error"], "pages": []}, warnings
    if pdf_data.get("is_scanned_empty"):
        warnings.append(f"{src_rel}: scanned_or_empty_pdf_text")
        return None, {"source_file": src_rel, "error": "scanned_or_empty_pdf_text", "pages": pdf_data.get("pages", [])}, warnings

    p1 = pdf_data["pages"][0]["text"] if pdf_data["pages"] else ""
    lines1 = [ln.strip() for ln in p1.splitlines() if ln.strip()]
    doi_m = re.search(r"(10\.\d{4,9}/[^\s,;]+)", pdf_data["full_text"][:4000])
    arxiv_id = normalize_arxiv(pdf_data["full_text"][:4000])
    yr_m = re.search(r"\b(20[0-2]\d|19[89]\d)\b", "\n".join(lines1[:15]))

    title = ""
    for ln in lines1[:8]:
        if len(ln) >= 12 and not ln.lower().startswith(("doi:", "arxiv:", "http", "received", "accepted", "published")):
            title = ln
            break
    abs_m = re.search(r"(?:abstract|摘要)[:.\s\n]+(.{40,1200}?)(?:\n\s*(?:keywords|关键词|1\.|introduction|引言))", pdf_data["full_text"][:6000], flags=re.I | re.S)
    abstract = re.sub(r"\s+", " ", abs_m.group(1)).strip() if abs_m else re.sub(r"\s+", " ", p1[:1200]).strip()

    ev_rec = None
    if title or doi_m or arxiv_id:
        rid = f"RAW_{start_idx + 1:03d}"
        raw_fields = {
            "doc_type": "preprint" if arxiv_id and not doi_m else "journal",
            "title": title,
            "authors": "",
            "year": yr_m.group(1) if yr_m else "",
            "venue": "arXiv" if arxiv_id else "",
            "doi": doi_m.group(1).rstrip(".,;)") if doi_m else "",
            "arxiv_id": arxiv_id,
            "abstract": abstract,
        }
        ev_rec = _make_record(rid, src_rel, f"{src_rel}:p1", "evidence_pdf", raw_fields, style_rules)

    ev_full = {
        "source_file": src_rel,
        "page_count": pdf_data["page_count"],
        "pages": pdf_data["pages"],
        "extracted_doi": doi_m.group(1).rstrip(".,;)") if doi_m else "",
        "extracted_arxiv": arxiv_id,
        "extracted_title": title,
    }
    return ev_rec, ev_full, warnings


def _is_preprint(r: Dict[str, Any]) -> bool:
    vlow = (r.get("venue") or "").lower()
    return (
        r.get("source_tier") == "preprint_arxiv_biorxiv"
        or r.get("doc_type") == "preprint"
        or bool(r.get("arxiv_id"))
        or any(p in vlow for p in ("arxiv", "biorxiv", "medrxiv", "chemrxiv", "ssrn", "preprint"))
    )


def _are_preprint_vs_published(r1: Dict[str, Any], r2: Dict[str, Any]) -> bool:
    return _is_preprint(r1) != _is_preprint(r2)


def evaluate_pair(r1: Dict[str, Any], r2: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    thr = rules.get("thresholds", {})
    tsim = title_similarity(r1["title"], r2["title"])
    if r1.get("title_translated"):
        tsim = max(tsim, title_similarity(r1["title_translated"], r2["title"]))
    if r2.get("title_translated"):
        tsim = max(tsim, title_similarity(r1["title"], r2["title_translated"]))

    aov = author_overlap(r1["authors"], r2["authors"])
    y1, y2 = r1.get("year"), r2.get("year")
    ydiff = abs(y1 - y2) if (y1 and y2) else None
    same_script = r1["language"] == r2["language"]
    preprint_pair = _are_preprint_vs_published(r1, r2)

    shared_doi = bool(r1["doi"] and r2["doi"] and r1["doi"] == r2["doi"])
    shared_arxiv = bool(r1["arxiv_id"] and r2["arxiv_id"] and r1["arxiv_id"] == r2["arxiv_id"])
    shared_pmid = bool(r1["pmid"] and r2["pmid"] and r1["pmid"] == r2["pmid"])

    if shared_doi or shared_arxiv or shared_pmid:
        id_name = "doi" if shared_doi else ("arxiv_id" if shared_arxiv else "pmid")
        id_val = r1[id_name]
        if same_script and tsim < thr.get("tier1_identifier_title_coherence_min", 0.30) and aov < 0.30:
            return {
                "verdict": "recycled_doi_conflict",
                "tier": "conflict",
                "rule": f"shared_{id_name}_but_unrelated_titles_and_authors",
                "metrics": {"title_sim": tsim, "author_overlap": aov, "shared_id": f"{id_name}:{id_val}"},
            }
        return {
            "verdict": "auto_merge",
            "tier": "tier1_exact_identifier",
            "rule": f"identical_{id_name}:{id_val}",
            "metrics": {"title_sim": tsim, "author_overlap": aov, "year_diff": ydiff},
        }

    if r1["doi"] and r2["doi"] and r1["doi"] != r2["doi"]:
        is_arxiv_doi_pair = ("arxiv" in r1["doi"] or "arxiv" in r2["doi"] or preprint_pair)
        if not is_arxiv_doi_pair:
            if aov >= 0.50:
                return {
                    "verdict": "protected_non_duplicate",
                    "tier": "anti_false_merge",
                    "rule": "overlapping_authors_with_distinct_valid_dois",
                    "metrics": {"title_sim": tsim, "author_overlap": aov, "doi_1": r1["doi"], "doi_2": r2["doi"]},
                }
            return {"verdict": "distinct", "tier": "none", "rule": "distinct_dois", "metrics": {}}

    if (
        r1["volume"] and r2["volume"] and r1["volume"] == r2["volume"]
        and r1["pages"] and r2["pages"]
        and page_ranges_conflict(r1["pages_info"], r2["pages_info"])
    ):
        if aov >= 0.50 or tsim >= 0.50:
            return {
                "verdict": "protected_non_duplicate",
                "tier": "anti_false_merge",
                "rule": "same_volume_disjoint_pages",
                "metrics": {"title_sim": tsim, "author_overlap": aov, "pages_1": r1["pages"], "pages_2": r2["pages"]},
            }
        return {"verdict": "distinct", "tier": "none", "rule": "disjoint_pages", "metrics": {}}

    if not same_script:
        if r1.get("title_translated") and title_similarity(r1["title_translated"], r2["title"]) >= 0.88:
            return {
                "verdict": "auto_merge",
                "tier": "tier2_bilingual_with_doi_or_locator",
                "rule": "explicit_translated_title_match",
                "metrics": {"title_sim": tsim, "author_overlap": aov, "year_diff": ydiff},
            }
        if r2.get("title_translated") and title_similarity(r1["title"], r2["title_translated"]) >= 0.88:
            return {
                "verdict": "auto_merge",
                "tier": "tier2_bilingual_with_doi_or_locator",
                "rule": "explicit_translated_title_match",
                "metrics": {"title_sim": tsim, "author_overlap": aov, "year_diff": ydiff},
            }
        p1s = r1["pages_info"].get("start")
        p2s = r2["pages_info"].get("start")
        if y1 and y2 and y1 == y2 and r1["volume"] and r1["volume"] == r2["volume"] and p1s and p1s == p2s:
            return {
                "verdict": "auto_merge",
                "tier": "tier2_bilingual_with_doi_or_locator",
                "rule": "bilingual_shared_year_volume_start_page",
                "metrics": {"year": y1, "volume": r1["volume"], "start_page": p1s},
            }
        if (ydiff is None or ydiff <= thr.get("tier3_bilingual_max_year_gap", 1)) and (
            bilingual_surname_compatible(r1["authors"], r2["authors"])
            or (r1["volume"] and r1["volume"] == r2["volume"] and len(r1["authors"]) == len(r2["authors"]))
        ):
            return {
                "verdict": "ambiguous_candidate",
                "tier": "tier3_suspected_bilingual_pair",
                "rule": "cross_script_same_year_and_compatible_metadata_needs_semantic_check",
                "metrics": {"year_1": y1, "year_2": y2, "volume_1": r1["volume"], "volume_2": r2["volume"]},
            }

    if preprint_pair and tsim >= thr.get("tier2_preprint_published_title_sim_min", 0.88) and (
        aov >= thr.get("tier2_preprint_published_author_overlap_min", 0.50) or (not r1["authors"] or not r2["authors"])
    ) and (ydiff is None or ydiff <= thr.get("tier2_preprint_published_max_year_gap", 3)):
        return {
            "verdict": "auto_merge",
            "tier": "tier2_preprint_to_published",
            "rule": f"preprint_published_title_sim={tsim:.2f}_author_overlap={aov:.2f}",
            "metrics": {"title_sim": tsim, "author_overlap": aov, "year_diff": ydiff},
        }

    if tsim >= thr.get("tier2_cross_db_title_sim_min", 0.90) and (
        aov >= thr.get("tier2_cross_db_author_overlap_min", 0.50) or (not r1["authors"] or not r2["authors"])
    ) and (ydiff is None or ydiff <= thr.get("tier2_cross_db_max_year_gap", 1)):
        return {
            "verdict": "auto_merge",
            "tier": "tier2_cross_db_strong_metadata",
            "rule": f"cross_db_title_sim={tsim:.2f}_author_overlap={aov:.2f}",
            "metrics": {"title_sim": tsim, "author_overlap": aov, "year_diff": ydiff},
        }

    if (
        thr.get("tier3_conference_journal_title_sim_min", 0.72) <= tsim < thr.get("tier3_conference_journal_title_sim_max", 0.90)
        and aov >= 0.50
        and (ydiff is None or ydiff <= 3)
    ):
        return {
            "verdict": "ambiguous_candidate",
            "tier": "tier3_conference_vs_journal_extension",
            "rule": f"moderate_title_sim={tsim:.2f}_shared_authors={aov:.2f}",
            "metrics": {"title_sim": tsim, "author_overlap": aov, "year_diff": ydiff},
        }

    if aov >= 0.50 and (ydiff is not None and ydiff <= 1) and same_script and tsim < thr.get("anti_false_merge_title_sim_floor", 0.70):
        return {
            "verdict": "protected_non_duplicate",
            "tier": "anti_false_merge",
            "rule": f"same_author_close_year_but_distinct_title_sim={tsim:.2f}",
            "metrics": {"title_sim": tsim, "author_overlap": aov, "year_diff": ydiff},
        }

    return {"verdict": "distinct", "tier": "none", "rule": "distinct", "metrics": {}}


def merge_cluster(
    cluster: List[Dict[str, Any]],
    canon_id: str,
    pair_reasons: List[Dict[str, Any]],
    style_rules: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    def richness(r: Dict[str, Any]) -> Tuple[int, int]:
        filled = sum(1 for k in ("title", "authors", "year", "venue", "volume", "issue", "pages", "doi", "abstract") if r.get(k))
        return (int(r.get("source_precedence", 0)), filled)

    ordered = sorted(cluster, key=richness, reverse=True)
    primary = ordered[0]

    tracked_fields = [
        "doc_type", "title", "title_translated", "authors", "year", "venue",
        "volume", "issue", "pages", "doi", "arxiv_id", "pmid", "url",
        "publisher", "city", "abstract", "patent_number", "standard_number",
    ]
    merged_fields: Dict[str, Any] = {}
    field_provenance: Dict[str, Any] = {}
    field_conflicts: List[Dict[str, Any]] = []

    for fld in tracked_fields:
        candidates: List[Tuple[Any, Dict[str, Any]]] = []
        for rec in ordered:
            val = rec.get(fld)
            if val not in (None, "", []):
                candidates.append((val, rec))
        if not candidates:
            merged_fields[fld] = [] if fld == "authors" else (None if fld == "year" else "")
            field_provenance[fld] = {
                "value": merged_fields[fld],
                "source_record_id": None,
                "source_file": None,
                "source_locator": None,
                "status": "missing",
                "alternate_values": [],
            }
            continue

        chosen_val, chosen_rec = candidates[0]
        alternates = []
        has_conflict = False
        for alt_val, alt_rec in candidates[1:]:
            if alt_val != chosen_val:
                alternates.append({
                    "value": alt_val,
                    "source_record_id": alt_rec["raw_id"],
                    "source_locator": alt_rec["source_locator"],
                    "source_tier": alt_rec["source_tier"],
                })
                if fld in ("year", "volume", "issue", "pages", "doi"):
                    has_conflict = True
                elif fld == "venue" and normalize_title(str(alt_val)) != normalize_title(str(chosen_val)):
                    if alt_rec["source_tier"] == chosen_rec["source_tier"]:
                        has_conflict = True
                elif fld == "title" and title_similarity(str(alt_val), str(chosen_val)) < 0.92 and alt_rec["language"] == chosen_rec["language"]:
                    has_conflict = True

        if has_conflict:
            field_conflicts.append({
                "field": fld,
                "adopted": {"value": chosen_val, "source_locator": chosen_rec["source_locator"], "source_tier": chosen_rec["source_tier"]},
                "alternates": alternates,
            })

        merged_fields[fld] = chosen_val
        field_provenance[fld] = {
            "value": chosen_val,
            "source_record_id": chosen_rec["raw_id"],
            "source_file": chosen_rec["source_file"],
            "source_locator": chosen_rec["source_locator"],
            "status": "conflict_resolved_by_precedence" if has_conflict else (
                "supplemented_from_secondary" if chosen_rec["raw_id"] != primary["raw_id"] else "verified_from_primary"
            ),
            "alternate_values": alternates,
        }

    zh_titles = [r["title"] for r in ordered if r["language"] == "zh" and r["title"]]
    en_titles = [r["title"] for r in ordered if r["language"] == "en" and r["title"]]
    if zh_titles and en_titles and not merged_fields.get("title_translated"):
        alt_t = en_titles[0] if detect_script_lang(merged_fields["title"]) == "zh" else zh_titles[0]
        merged_fields["title_translated"] = alt_t

    doc_type = merged_fields.get("doc_type") or "other"
    req_fields = style_rules.get("required_fields_by_doc_type", {}).get(doc_type, ["authors", "title", "year"])
    missing_req = [rf for rf in req_fields if not merged_fields.get(rf)]

    integrity_flags: List[str] = []
    if not merged_fields.get("venue") and not merged_fields.get("doi") and not merged_fields.get("arxiv_id") and not merged_fields.get("url") and not merged_fields.get("publisher"):
        integrity_flags.append("missing_venue_and_identifier_suspect")
    for r in ordered:
        integrity_flags.extend(r.get("parse_warnings", []))

    doi_status = "not_checked_offline" if merged_fields.get("doi") else "missing_doi"
    for r in ordered:
        if r.get("doi_raw") and not r.get("doi"):
            doi_status = "invalid_doi_syntax"

    canon_rec = {
        "ref_id": canon_id,
        "primary_raw_id": primary["raw_id"],
        "member_raw_ids": [r["raw_id"] for r in ordered],
        "source_keys": [r["source_key"] for r in ordered if r.get("source_key")],
        "original_list_numbers": [r["original_list_number"] for r in ordered if r.get("original_list_number") is not None],
        "language": detect_script_lang(merged_fields.get("title") or primary.get("venue") or ""),
        **merged_fields,
        "doi_verification_status": doi_status,
        "missing_required_fields": missing_req,
        "integrity_flags": sorted(set(integrity_flags)),
        "field_provenance": field_provenance,
    }

    merge_info = {
        "canonical_id": canon_id,
        "merged_count": len(ordered),
        "primary_raw_id": primary["raw_id"],
        "primary_source_locator": primary["source_locator"],
        "primary_tier": primary["source_tier"],
        "member_records": [
            {"raw_id": r["raw_id"], "source_locator": r["source_locator"], "source_tier": r["source_tier"], "title": r["title"]}
            for r in ordered
        ],
        "pair_rationales": pair_reasons,
        "field_conflicts": field_conflicts,
    }
    return canon_rec, merge_info


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse reference libraries and perform explainable deduplication.")
    parser.add_argument("--case-plan", default="/app/work/case_plan.json")
    parser.add_argument("--manuscript", default="/app/work/manuscript/manuscript_parsed.json")
    parser.add_argument("--rules", required=True)
    parser.add_argument("--style-rules", required=True)
    parser.add_argument("--out-dir", default="/app/work/library")
    args = parser.parse_args()

    case_plan = load_json_safe(Path(args.case_plan), {})
    dedup_rules = load_json_safe(Path(args.rules), {})
    style_rules = load_json_safe(Path(args.style_rules), {})
    input_dir = Path(case_plan.get("input_dir", "/input"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_records: List[Dict[str, Any]] = []
    parse_warnings: List[str] = []
    evidence_fulltexts: List[Dict[str, Any]] = []

    for finfo in case_plan.get("files", []):
        role = finfo.get("role")
        rel = finfo.get("rel_path", "")
        p = input_dir / rel
        ext = finfo.get("ext", "").lower()

        if role == "reference_library":
            txt, enc, warn = read_text_safe(p)
            if warn:
                parse_warnings.append(f"{rel}: {warn}")
            if not txt.strip():
                continue
            if ext == ".bib" or "@article" in txt[:2000].lower() or "@inproceedings" in txt[:2000].lower():
                recs, warns = parse_bibtex(txt, rel, style_rules, len(raw_records))
            elif ext == ".ris" or ("TY  -" in txt[:3000] and "ER  -" in txt):
                recs, warns = parse_ris(txt, rel, style_rules, len(raw_records))
            elif ext == ".enw" or ("%0 " in txt[:2000] and "%T " in txt[:2000]):
                recs, warns = parse_endnote_tagged(txt, rel, style_rules, len(raw_records))
            elif ext == ".xml":
                recs, warns = parse_endnote_xml(txt, rel, style_rules, len(raw_records))
            elif ext in (".json", ".jsonl", ".csl"):
                recs, warns = parse_csl_or_json(txt, rel, style_rules, len(raw_records))
            elif ext in (".csv", ".tsv"):
                recs, warns = parse_csv_tsv(txt, rel, ext == ".tsv", style_rules, len(raw_records))
            elif any(k in txt[:2000] for k in ("RT ", "Title-题名:", "SrcDatabase-")):
                recs, warns = parse_cnki_txt(txt, rel, style_rules, len(raw_records))
            else:
                lines_src = [{"source_file": rel, "raw": ln} for ln in txt.splitlines() if ln.strip()]
                recs = parse_numbered_ref_lines(lines_src, style_rules, len(raw_records), "numbered_list")
                warns = []
            raw_records.extend(recs)
            parse_warnings.extend(warns)

        elif role == "evidence_paper":
            if ext == ".pdf":
                ev_rec, ev_full, warns = parse_evidence_pdf(p, rel, style_rules, len(raw_records))
                parse_warnings.extend(warns)
                evidence_fulltexts.append(ev_full)
                if ev_rec:
                    raw_records.append(ev_rec)
            else:
                txt, enc, warn = read_text_safe(p)
                if warn:
                    parse_warnings.append(f"{rel}: {warn}")
                if txt.strip():
                    evidence_fulltexts.append({
                        "source_file": rel,
                        "page_count": 1,
                        "pages": [{"page_num": 1, "text": txt}],
                        "extracted_doi": "",
                        "extracted_arxiv": "",
                        "extracted_title": txt.splitlines()[0].strip()[:200],
                    })

    ms_path = Path(args.manuscript)
    if ms_path.exists():
        ms_parsed = load_json_safe(ms_path, {})
        inline_lines = ms_parsed.get("inline_references_raw") or []
        if inline_lines:
            irecs = parse_numbered_ref_lines(inline_lines, style_rules, len(raw_records), "manuscript_inline_ref")
            raw_records.extend(irecs)

    save_json(out_dir / "raw_records.json", {"raw_records": raw_records, "parse_warnings": parse_warnings})
    save_json(out_dir / "evidence_fulltexts.json", {"evidence_papers": evidence_fulltexts})

    n = len(raw_records)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    merge_edges: List[Dict[str, Any]] = []
    ambiguous_candidates: List[Dict[str, Any]] = []
    protected_non_duplicates: List[Dict[str, Any]] = []
    recycled_doi_conflicts: List[Dict[str, Any]] = []

    for i in range(n):
        for j in range(i + 1, n):
            res = evaluate_pair(raw_records[i], raw_records[j], dedup_rules)
            v = res["verdict"]
            if v == "auto_merge":
                union(i, j)
                merge_edges.append({
                    "raw_a": raw_records[i]["raw_id"],
                    "raw_b": raw_records[j]["raw_id"],
                    "tier": res["tier"],
                    "rule": res["rule"],
                    "metrics": res["metrics"],
                })
            elif v == "ambiguous_candidate":
                ambiguous_candidates.append({
                    "candidate_id": f"AMB_{len(ambiguous_candidates) + 1:03d}",
                    "raw_a": {"raw_id": raw_records[i]["raw_id"], "title": raw_records[i]["title"], "authors": raw_records[i]["authors"], "year": raw_records[i]["year"], "venue": raw_records[i]["venue"], "doi": raw_records[i]["doi"], "source_locator": raw_records[i]["source_locator"]},
                    "raw_b": {"raw_id": raw_records[j]["raw_id"], "title": raw_records[j]["title"], "authors": raw_records[j]["authors"], "year": raw_records[j]["year"], "venue": raw_records[j]["venue"], "doi": raw_records[j]["doi"], "source_locator": raw_records[j]["source_locator"]},
                    "tier": res["tier"],
                    "rule": res["rule"],
                    "metrics": res["metrics"],
                })
            elif v == "protected_non_duplicate":
                protected_non_duplicates.append({
                    "raw_a": raw_records[i]["raw_id"],
                    "raw_b": raw_records[j]["raw_id"],
                    "title_a": raw_records[i]["title"],
                    "title_b": raw_records[j]["title"],
                    "rule": res["rule"],
                    "metrics": res["metrics"],
                })
            elif v == "recycled_doi_conflict":
                recycled_doi_conflicts.append({
                    "raw_a": raw_records[i]["raw_id"],
                    "raw_b": raw_records[j]["raw_id"],
                    "title_a": raw_records[i]["title"],
                    "title_b": raw_records[j]["title"],
                    "rule": res["rule"],
                    "metrics": res["metrics"],
                })

    clusters: Dict[int, List[Dict[str, Any]]] = {}
    for idx in range(n):
        clusters.setdefault(find(idx), []).append(raw_records[idx])

    canonical_records: List[Dict[str, Any]] = []
    merge_groups: List[Dict[str, Any]] = []
    raw_to_canonical_map: Dict[str, str] = {}
    key_to_canonical_map: Dict[str, str] = {}
    inline_num_to_canonical_map: Dict[str, str] = {}
    all_field_conflicts: List[Dict[str, Any]] = []

    for c_idx, (_, members) in enumerate(sorted(clusters.items(), key=lambda kv: kv[0]), start=1):
        cid = f"REF_{c_idx:03d}"
        m_ids = {m["raw_id"] for m in members}
        rel_edges = [e for e in merge_edges if e["raw_a"] in m_ids and e["raw_b"] in m_ids]
        canon_rec, minfo = merge_cluster(members, cid, rel_edges, style_rules)
        canonical_records.append(canon_rec)
        if len(members) > 1:
            merge_groups.append(minfo)
        for fc in minfo.get("field_conflicts", []):
            all_field_conflicts.append({"ref_id": cid, **fc})
        for m in members:
            raw_to_canonical_map[m["raw_id"]] = cid
            if m.get("source_key"):
                key_to_canonical_map.setdefault(m["source_key"], cid)
            if m.get("original_list_number") is not None:
                inline_num_to_canonical_map[str(m["original_list_number"])] = cid

    lib_out = {
        "case_id": case_plan.get("case_id", ""),
        "canonical_records": canonical_records,
        "raw_to_canonical_map": raw_to_canonical_map,
        "key_to_canonical_map": key_to_canonical_map,
        "inline_num_to_canonical_map": inline_num_to_canonical_map,
        "merge_groups": merge_groups,
        "ambiguous_duplicate_candidates": ambiguous_candidates,
        "protected_non_duplicates": protected_non_duplicates,
        "recycled_doi_conflicts": recycled_doi_conflicts,
        "field_conflicts": all_field_conflicts,
        "parse_warnings": parse_warnings,
        "stats": {
            "raw_record_count": len(raw_records),
            "canonical_record_count": len(canonical_records),
            "auto_merged_groups": len(merge_groups),
            "duplicates_removed": len(raw_records) - len(canonical_records),
            "ambiguous_candidates": len(ambiguous_candidates),
            "protected_non_duplicates": len(protected_non_duplicates),
            "recycled_doi_conflicts": len(recycled_doi_conflicts),
            "field_conflicts": len(all_field_conflicts),
        },
    }
    save_json(out_dir / "library_canonical.json", lib_out)

    dlines = [
        f"# Reference Library & Deduplication Digest (`{lib_out['case_id']}`)",
        "",
        f"- **Raw records**: {lib_out['stats']['raw_record_count']} -> **Canonical records**: {lib_out['stats']['canonical_record_count']} (auto-merged {lib_out['stats']['duplicates_removed']} duplicates across {lib_out['stats']['auto_merged_groups']} groups)",
        f"- **Ambiguous candidates (review needed)**: {lib_out['stats']['ambiguous_candidates']} | **Protected non-duplicates**: {lib_out['stats']['protected_non_duplicates']} | **Recycled DOI conflicts**: {lib_out['stats']['recycled_doi_conflicts']} | **Field conflicts**: {lib_out['stats']['field_conflicts']}",
        "",
    ]
    if parse_warnings:
        dlines.append("## Parse Warnings")
        for w in parse_warnings:
            dlines.append(f"- {w}")
        dlines.append("")
    if merge_groups:
        dlines.append("## Auto-Merged Duplicate Groups")
        for mg in merge_groups:
            dlines.append(f"- **`{mg['canonical_id']}`** (merged {mg['merged_count']} records; primary: `{mg['primary_raw_id']}` @ `{mg['primary_source_locator']}`):")
            for r in mg["pair_rationales"]:
                dlines.append(f"  - Rule `{r['tier']}`: `{r['raw_a']}` + `{r['raw_b']}` ({r['rule']})")
        dlines.append("")
    if ambiguous_candidates:
        dlines.append("## Tier-3 Ambiguous Candidates (Decide merge vs keep_separate in citation_plan.json)")
        for ac in ambiguous_candidates:
            dlines.append(f"- **`{ac['candidate_id']}`** ({ac['tier']} | {ac['rule']}):")
            dlines.append(f"  - A (`{ac['raw_a']['raw_id']}` @ `{ac['raw_a']['source_locator']}`): {ac['raw_a']['title']} | {ac['raw_a']['authors']} | {ac['raw_a']['year']} | {ac['raw_a']['venue']}")
            dlines.append(f"  - B (`{ac['raw_b']['raw_id']}` @ `{ac['raw_b']['source_locator']}`): {ac['raw_b']['title']} | {ac['raw_b']['authors']} | {ac['raw_b']['year']} | {ac['raw_b']['venue']}")
        dlines.append("")
    if recycled_doi_conflicts:
        dlines.append("## CRITICAL: Recycled / Conflicting DOI Alerts (DO NOT MERGE)")
        for rc in recycled_doi_conflicts:
            dlines.append(f"- `{rc['raw_a']}` ({rc['title_a']}) vs `{rc['raw_b']}` ({rc['title_b']}) -> {rc['metrics']}")
        dlines.append("")
    if protected_non_duplicates:
        dlines.append("## Protected Non-Duplicates (Kept Separate)")
        for pn in protected_non_duplicates:
            dlines.append(f"- `{pn['raw_a']}` vs `{pn['raw_b']}` kept separate (`{pn['rule']}`)")
        dlines.append("")

    dlines.append("## Canonical Records")
    for rec in canonical_records:
        auth_preview = ", ".join(rec["authors"][:3]) + (" et al." if len(rec["authors"]) > 3 else "")
        dlines.append(
            f"- **`{rec['ref_id']}`** (`{rec['doc_type']}` | keys={rec['source_keys']} | orig#={rec['original_list_numbers']} | lang={rec['language']}): "
            f"{auth_preview} ({rec['year']}). **{rec['title']}**. *{rec['venue']}*"
            + (f", {rec['volume']}" if rec['volume'] else "")
            + (f"({rec['issue']})" if rec['issue'] else "")
            + (f": {rec['pages']}" if rec['pages'] else "")
            + (f" | DOI:{rec['doi']}" if rec['doi'] else "")
            + (f" | arXiv:{rec['arxiv_id']}" if rec['arxiv_id'] else "")
        )
        if rec["missing_required_fields"] or rec["integrity_flags"]:
            dlines.append(f"  - Missing required: {rec['missing_required_fields']} | Flags: {rec['integrity_flags']}")
        if rec.get("abstract"):
            dlines.append(f"  - Abstract: {rec['abstract'][:320]}")

    (out_dir / "library_digest.md").write_text("\n".join(dlines) + "\n", encoding="utf-8")
    print(json.dumps(lib_out["stats"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
