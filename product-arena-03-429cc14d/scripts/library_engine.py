#!/usr/bin/env python3
"""Multi-format library parser, 3-tier explainable deduplication engine, and 8-style formatter."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from common import (
    CJK_RE,
    clean_latex_accents,
    detect_script_lang,
    is_valid_doi,
    normalize_arxiv,
    normalize_doi,
    normalize_title,
    normalize_url,
    parse_author_name,
    parse_page_range,
    parse_year,
    read_text_safe,
    split_authors_string,
    title_similarity,
)

PREPRINT_VENUES = {"arxiv", "biorxiv", "medrxiv", "chemrxiv", "ssrn", "chinaxiv", "preprint"}

_BIB_TYPE_MAP = {
    "article": "article",
    "inproceedings": "inproceedings",
    "conference": "inproceedings",
    "proceedings": "inproceedings",
    "book": "book",
    "incollection": "incollection",
    "inbook": "incollection",
    "phdthesis": "thesis",
    "mastersthesis": "thesis",
    "thesis": "thesis",
    "techreport": "techreport",
    "report": "techreport",
    "manual": "standard",
    "standard": "standard",
    "patent": "patent",
    "online": "online",
    "electronic": "online",
    "webpage": "online",
    "misc": "misc",
    "unpublished": "preprint",
}

_RIS_TYPE_MAP = {
    "JOUR": "article",
    "EJOUR": "article",
    "CONF": "inproceedings",
    "CPAPER": "inproceedings",
    "BOOK": "book",
    "CHAP": "incollection",
    "THES": "thesis",
    "RPRT": "techreport",
    "STAND": "standard",
    "PAT": "patent",
    "ELEC": "online",
    "NEWS": "newspaper",
    "GEN": "misc",
}


def _infer_doc_type(raw_type: str, venue: str, arxiv_id: str, url: str) -> str:
    rt = (raw_type or "").strip().lower()
    if rt in _BIB_TYPE_MAP:
        dtype = _BIB_TYPE_MAP[rt]
    elif rt.upper() in _RIS_TYPE_MAP:
        dtype = _RIS_TYPE_MAP[rt.upper()]
    elif rt in ("article", "inproceedings", "book", "incollection", "thesis", "techreport", "standard", "patent", "newspaper", "online", "preprint", "misc"):
        dtype = rt
    else:
        dtype = "other"

    vlow = (venue or "").lower()
    if arxiv_id or any(pv in vlow for pv in PREPRINT_VENUES) or "arxiv.org" in (url or "").lower():
        if dtype in ("other", "misc", "online", "article") and (arxiv_id or "arxiv" in vlow or "biorxiv" in vlow or "medrxiv" in vlow):
            return "preprint"
    if dtype == "other":
        if venue:
            return "inproceedings" if re.search(r"proc|conf|symp|workshop|icml|neurips|iclr|acl|emnlp|cvpr|aaai|会议|研讨", vlow) else "article"
        if url:
            return "online"
    return dtype


def _finalize_raw_record(raw: Dict[str, Any], idx: int) -> Dict[str, Any]:
    title = clean_latex_accents(raw.get("title") or "").strip()
    title_tr = clean_latex_accents(raw.get("title_translated") or "").strip()
    authors = [clean_latex_accents(a).strip() for a in (raw.get("authors") or []) if str(a).strip()]
    venue = clean_latex_accents(raw.get("venue") or "").strip()
    doi = normalize_doi(raw.get("doi") or "")
    arxiv_id = normalize_arxiv(raw.get("arxiv_id") or "")
    if not arxiv_id and doi.startswith("10.48550/arxiv."):
        arxiv_id = normalize_arxiv(doi.split("arxiv.", 1)[1])
    url = normalize_url(raw.get("url") or "")
    if not arxiv_id and "arxiv.org" in url.lower():
        arxiv_id = normalize_arxiv(url)
    year_int, year_flag = parse_year(raw.get("year"))
    pinfo = parse_page_range(raw.get("pages") or "")
    doc_type = _infer_doc_type(raw.get("doc_type") or "", venue, arxiv_id, url)
    lang = raw.get("language") or detect_script_lang(f"{title} {' '.join(authors)} {venue}")

    flags: List[str] = list(raw.get("integrity_flags") or [])
    if year_flag:
        flags.append(year_flag)
    if pinfo["inverted"]:
        flags.append(f"inverted_page_range:{pinfo['normalized']}")
    if doi and not is_valid_doi(doi):
        flags.append(f"invalid_doi_syntax:{doi}")
    if not venue and not doi and not arxiv_id and not raw.get("isbn") and not url and not raw.get("publisher"):
        flags.append("missing_venue_and_identifier")

    doi_status = "missing" if not doi else ("not_checked_offline" if is_valid_doi(doi) else "invalid_doi_syntax")

    return {
        "raw_id": f"RAW_{idx:03d}",
        "source_file": raw.get("source_file", ""),
        "line_start": raw.get("line_start", 1),
        "source_locator": f"{raw.get('source_file', 'unknown')}:{raw.get('line_start', 1)}",
        "source_format": raw.get("source_format", "unknown"),
        "source_key": str(raw.get("source_key") or "").strip(),
        "original_list_number": raw.get("original_list_number"),
        "doc_type": doc_type,
        "language": lang,
        "title": title,
        "title_translated": title_tr,
        "authors": authors,
        "year": year_int,
        "venue": venue,
        "volume": str(raw.get("volume") or "").strip(),
        "issue": str(raw.get("issue") or "").strip(),
        "pages": pinfo["normalized"],
        "pages_info": pinfo,
        "doi": doi,
        "doi_verification_status": doi_status,
        "arxiv_id": arxiv_id,
        "pmid": str(raw.get("pmid") or "").strip(),
        "isbn": str(raw.get("isbn") or "").strip(),
        "url": url,
        "publisher": clean_latex_accents(raw.get("publisher") or "").strip(),
        "city": clean_latex_accents(raw.get("city") or "").strip(),
        "abstract": clean_latex_accents(raw.get("abstract") or "").strip(),
        "patent_number": str(raw.get("patent_number") or "").strip(),
        "standard_number": str(raw.get("standard_number") or "").strip(),
        "access_date": str(raw.get("access_date") or "").strip(),
        "integrity_flags": list(dict.fromkeys(flags)),
    }


def _strip_outer_delim(val: str) -> str:
    s = val.strip()
    if len(s) >= 2 and ((s[0] == "{" and s[-1] == "}") or (s[0] == '"' and s[-1] == '"')):
        return s[1:-1].strip()
    return s


def parse_bibtex_text(text: str, source_file: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    warnings: List[str] = []
    string_macros: Dict[str, str] = {}

    # Parse @string macros first
    for m in re.finditer(r"@string\s*\{\s*([A-Za-z0-9_\-]+)\s*=\s*(\"[^\"]*\"|\{[^}]*\})\s*\}", text, re.I):
        string_macros[m.group(1).lower()] = _strip_outer_delim(m.group(2))

    entry_starts = [m for m in re.finditer(r"@([A-Za-z]+)\s*([{(])", text)]
    for ei, em in enumerate(entry_starts):
        etype = em.group(1).lower()
        if etype in ("comment", "preamble", "string"):
            continue
        open_ch = em.group(2)
        close_ch = "}" if open_ch == "{" else ")"
        pos = em.end()
        depth = 1
        next_start = entry_starts[ei + 1].start() if ei + 1 < len(entry_starts) else len(text)
        line_no = text[: em.start()].count("\n") + 1
        while pos < len(text) and depth > 0:
            ch = text[pos]
            if ch == "\\" and pos + 1 < len(text):
                pos += 2
                continue
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
            if pos >= next_start and depth > 0:
                warnings.append(f"{source_file}:{line_no}: malformed BibTeX entry `@{etype}` salvaged at next entry boundary.")
                break
            pos += 1
        body = text[em.end() : max(em.end(), pos - (1 if depth == 0 else 0))]
        if depth != 0 and pos == len(text):
            warnings.append(f"{source_file}:{line_no}: unclosed BibTeX brace in `@{etype}`, salvaged partial fields.")

        comma_idx = body.find(",")
        if comma_idx == -1:
            continue
        cite_key = body[:comma_idx].strip()
        fields_str = body[comma_idx + 1 :]
        fields: Dict[str, str] = {}
        i = 0
        n = len(fields_str)
        while i < n:
            while i < n and fields_str[i] in " \t\r\n,":
                i += 1
            if i >= n:
                break
            m_key = re.match(r"([A-Za-z0-9_\-]+)\s*=\s*", fields_str[i:])
            if not m_key:
                nl = fields_str.find("\n", i)
                if nl == -1:
                    break
                i = nl + 1
                continue
            fkey = m_key.group(1).lower()
            i += m_key.end()
            # Read value (may concatenate macros with #)
            val_parts: List[str] = []
            while i < n:
                while i < n and fields_str[i] in " \t\r\n":
                    i += 1
                if i >= n:
                    break
                ch = fields_str[i]
                if ch == "{":
                    d = 1
                    j = i + 1
                    while j < n and d > 0:
                        if fields_str[j] == "\\" and j + 1 < n:
                            j += 2
                            continue
                        if fields_str[j] == "{":
                            d += 1
                        elif fields_str[j] == "}":
                            d -= 1
                        j += 1
                    val_parts.append(fields_str[i + 1 : max(i + 1, j - (1 if d == 0 else 0))])
                    i = j
                elif ch == '"':
                    j = i + 1
                    d = 0
                    while j < n:
                        if fields_str[j] == "\\" and j + 1 < n:
                            j += 2
                            continue
                        if fields_str[j] == "{":
                            d += 1
                        elif fields_str[j] == "}" and d > 0:
                            d -= 1
                        elif fields_str[j] == '"' and d == 0:
                            break
                        j += 1
                    val_parts.append(fields_str[i + 1 : j])
                    i = j + 1
                else:
                    m_bare = re.match(r"([A-Za-z0-9_\-:.+/]+)", fields_str[i:])
                    if m_bare:
                        tok = m_bare.group(1)
                        val_parts.append(string_macros.get(tok.lower(), tok))
                        i += m_bare.end()
                    else:
                        break
                while i < n and fields_str[i] in " \t\r\n":
                    i += 1
                if i < n and fields_str[i] == "#":
                    i += 1
                    continue
                break
            fields[fkey] = " ".join(p.strip() for p in val_parts if p is not None).strip()

        raw_auth = fields.get("author") or fields.get("editor") or ""
        authors = split_authors_string(raw_auth)
        arxiv_id = fields.get("eprint") or fields.get("arxivid") or ""
        if not arxiv_id and "arxiv" in (fields.get("archiveprefix") or "").lower():
            arxiv_id = fields.get("eprint") or ""
        records.append({
            "source_file": source_file,
            "line_start": line_no,
            "source_format": "bibtex",
            "source_key": cite_key,
            "doc_type": etype,
            "title": fields.get("title", ""),
            "title_translated": fields.get("title_zh") or fields.get("title_en") or "",
            "authors": authors,
            "year": fields.get("year") or fields.get("date", ""),
            "venue": fields.get("journal") or fields.get("booktitle") or fields.get("series") or fields.get("school") or "",
            "volume": fields.get("volume", ""),
            "issue": fields.get("number") or fields.get("issue") or "",
            "pages": fields.get("pages", ""),
            "doi": fields.get("doi", ""),
            "arxiv_id": arxiv_id,
            "pmid": fields.get("pmid", ""),
            "isbn": fields.get("isbn", ""),
            "url": fields.get("url", ""),
            "publisher": fields.get("publisher") or fields.get("school") or fields.get("institution") or "",
            "city": fields.get("address") or fields.get("location") or "",
            "abstract": fields.get("abstract", ""),
            "access_date": fields.get("urldate", ""),
        })
    return records, warnings


def parse_ris_text(text: str, source_file: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    warnings: List[str] = []
    cur: Optional[Dict[str, Any]] = None
    last_tag = ""

    def _flush() -> None:
        nonlocal cur
        if cur:
            sp = cur.pop("_sp", "")
            ep = cur.pop("_ep", "")
            if sp and ep:
                cur["pages"] = f"{sp}-{ep}"
            elif sp:
                cur["pages"] = sp
            records.append(cur)
            cur = None

    for lno, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"^([A-Z0-9]{2})\s{1,2}-\s*(.*)$", line)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            last_tag = tag
            if tag == "TY":
                _flush()
                cur = {"source_file": source_file, "line_start": lno, "source_format": "ris", "doc_type": val, "authors": []}
            elif tag == "ER":
                _flush()
                last_tag = ""
            elif cur is not None:
                if tag in ("AU", "A1", "A2"):
                    cur["authors"].append(val)
                elif tag in ("TI", "T1"):
                    cur["title"] = val
                elif tag in ("TT",):
                    cur["title_translated"] = val
                elif tag in ("JO", "JF", "T2", "JA", "J2"):
                    if not cur.get("venue") or tag in ("JF", "JO"):
                        cur["venue"] = val
                elif tag in ("PY", "Y1"):
                    if not cur.get("year"):
                        cur["year"] = val
                elif tag == "VL":
                    cur["volume"] = val
                elif tag == "IS":
                    cur["issue"] = val
                elif tag == "SP":
                    cur["_sp"] = val
                elif tag == "EP":
                    cur["_ep"] = val
                elif tag == "DO":
                    cur["doi"] = val
                elif tag in ("UR", "L1"):
                    if not cur.get("url"):
                        cur["url"] = val
                elif tag == "PB":
                    cur["publisher"] = val
                elif tag == "CY":
                    cur["city"] = val
                elif tag in ("AB", "N2"):
                    cur["abstract"] = val
                elif tag == "SN":
                    cur["isbn"] = val
                elif tag == "ID":
                    cur["source_key"] = val
                elif tag == "Y2":
                    cur["access_date"] = val
        elif cur is not None and last_tag in ("TI", "T1", "AB", "N2") and line.strip():
            fld = "title" if last_tag in ("TI", "T1") else "abstract"
            cur[fld] = (cur.get(fld, "") + " " + line.strip()).strip()
    _flush()
    return records, warnings


def parse_enw_text(text: str, source_file: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for lno, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s:
            if cur:
                records.append(cur)
                cur = None
            continue
        m = re.match(r"^%([A-Za-z0-9])\s+(.*)$", s)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "0":
            if cur:
                records.append(cur)
            cur = {"source_file": source_file, "line_start": lno, "source_format": "enw", "doc_type": val, "authors": []}
            continue
        if cur is None:
            cur = {"source_file": source_file, "line_start": lno, "source_format": "enw", "doc_type": "article", "authors": []}
        if tag in ("A", "E"):
            cur["authors"].append(val)
        elif tag == "T":
            cur["title"] = val
        elif tag in ("J", "B"):
            cur["venue"] = val
        elif tag == "D":
            cur["year"] = val
        elif tag == "V":
            cur["volume"] = val
        elif tag == "N":
            cur["issue"] = val
        elif tag == "P":
            cur["pages"] = val
        elif tag == "R":
            cur["doi"] = val
        elif tag == "U":
            cur["url"] = val
        elif tag == "I":
            cur["publisher"] = val
        elif tag == "C":
            cur["city"] = val
        elif tag == "X":
            cur["abstract"] = val
        elif tag == "F":
            cur["source_key"] = val
    if cur:
        records.append(cur)
    return records, []


def parse_endnote_xml(text: str, source_file: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    warnings: List[str] = []
    try:
        root = ET.fromstring(text)
    except Exception as exc:
        return [], [f"{source_file}:1: EndNote XML parse error: {exc}"]
    for idx, rec in enumerate(root.findall(".//record"), start=1):
        def _t(xpath: str) -> str:
            el = rec.find(xpath)
            return "".join(el.itertext()).strip() if el is not None else ""
        rt_el = rec.find(".//ref-type")
        rtype = rt_el.attrib.get("name", "article") if rt_el is not None else "article"
        authors = ["".join(a.itertext()).strip() for a in rec.findall(".//contributors/authors/author") if "".join(a.itertext()).strip()]
        records.append({
            "source_file": source_file,
            "line_start": idx,
            "source_format": "endnote_xml",
            "source_key": _t(".//label") or _t(".//rec-number"),
            "doc_type": rtype,
            "title": _t(".//titles/title"),
            "venue": _t(".//periodical/full-title") or _t(".//titles/secondary-title"),
            "authors": authors,
            "year": _t(".//dates/year"),
            "volume": _t(".//volume"),
            "issue": _t(".//number"),
            "pages": _t(".//pages"),
            "doi": _t(".//electronic-resource-num"),
            "url": _t(".//urls/related-urls/url"),
            "publisher": _t(".//publisher"),
            "city": _t(".//pub-location"),
            "abstract": _t(".//abstract"),
        })
    return records, warnings


def parse_cnki_text(text: str, source_file: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    blocks = re.split(r"(?=^(?:SrcDatabase-来源库|RT\s+|Title-题名)\s*[:：])", text, flags=re.M)
    for blk in blocks:
        blk = blk.strip()
        if not blk or ("Title-题名" not in blk and "题名" not in blk):
            continue
        line_no = text[: text.find(blk[:30])].count("\n") + 1 if blk[:30] in text else 1
        kv: Dict[str, str] = {}
        for ln in blk.splitlines():
            m = re.match(r"^([A-Za-z0-9\-\u4e00-\u9fff]+)\s*[:：]\s*(.*)$", ln.strip())
            if m:
                kv[m.group(1).strip().lower()] = m.group(2).strip()
        title = kv.get("title-题名") or kv.get("题名") or kv.get("title") or ""
        if not title:
            continue
        auth_raw = kv.get("author-作者") or kv.get("作者") or kv.get("author") or ""
        src_db = (kv.get("srcdatabase-来源库") or "").lower()
        dtype = "thesis" if ("学位" in src_db or "硕士" in src_db or "博士" in src_db) else ("inproceedings" if "会议" in src_db else "article")
        yr = kv.get("year-年") or kv.get("pubtime-发表时间") or kv.get("年") or ""
        records.append({
            "source_file": source_file,
            "line_start": line_no,
            "source_format": "cnki",
            "doc_type": dtype,
            "title": title,
            "authors": split_authors_string(auth_raw),
            "year": yr,
            "venue": kv.get("source-文献来源") or kv.get("文献来源") or kv.get("刊名") or "",
            "volume": kv.get("volume-卷") or kv.get("卷") or "",
            "issue": kv.get("period-期") or kv.get("期") or "",
            "pages": kv.get("page-页码") or kv.get("页码") or "",
            "doi": kv.get("doi") or "",
            "url": kv.get("link-链接") or kv.get("url") or "",
            "abstract": kv.get("summary-摘要") or kv.get("摘要") or "",
        })
    return records, []


def parse_csl_or_json_records(data: Any, source_file: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    items: List[Dict[str, Any]] = []
    if isinstance(data, list):
        items = [x for x in data if isinstance(x, dict)]
    elif isinstance(data, dict):
        for k in ("references", "items", "entries", "records", "bibliography", "citations", "library"):
            if isinstance(data.get(k), list):
                items = [x for x in data[k] if isinstance(x, dict)]
                break
        if not items and (data.get("title") or data.get("DOI") or data.get("doi")):
            items = [data]

    records: List[Dict[str, Any]] = []
    for idx, it in enumerate(items, start=1):
        # CSL-JSON authors: [{"family": ..., "given": ...}] or list[str]
        authors: List[str] = []
        raw_auth = it.get("author") or it.get("authors") or []
        if isinstance(raw_auth, str):
            authors = split_authors_string(raw_auth)
        elif isinstance(raw_auth, list):
            for a in raw_auth:
                if isinstance(a, dict):
                    if a.get("literal"):
                        authors.append(str(a["literal"]))
                    else:
                        fam = str(a.get("family") or "").strip()
                        giv = str(a.get("given") or "").strip()
                        authors.append(f"{fam}, {giv}".strip(", "))
                elif isinstance(a, str):
                    authors.append(a)
        yr = it.get("year")
        if not yr and isinstance(it.get("issued"), dict):
            dp = it["issued"].get("date-parts") or []
            if dp and isinstance(dp[0], list) and dp[0]:
                yr = dp[0][0]
        raw_t = it.get("type") or it.get("doc_type") or "article"
        csl_type_map = {"article-journal": "article", "paper-conference": "inproceedings", "chapter": "incollection", "webpage": "online", "report": "techreport"}
        records.append({
            "source_file": source_file,
            "line_start": idx,
            "source_format": "csl_json",
            "source_key": str(it.get("id") or it.get("key") or it.get("citation_key") or ""),
            "doc_type": csl_type_map.get(str(raw_t).lower(), str(raw_t)),
            "title": str(it.get("title") or ""),
            "title_translated": str(it.get("title_translated") or it.get("title-short") or ""),
            "authors": authors,
            "year": yr,
            "venue": str(it.get("container-title") or it.get("journal") or it.get("booktitle") or it.get("venue") or ""),
            "volume": str(it.get("volume") or ""),
            "issue": str(it.get("issue") or it.get("number") or ""),
            "pages": str(it.get("page") or it.get("pages") or ""),
            "doi": str(it.get("DOI") or it.get("doi") or ""),
            "arxiv_id": str(it.get("arxiv_id") or it.get("eprint") or ""),
            "url": str(it.get("URL") or it.get("url") or ""),
            "publisher": str(it.get("publisher") or ""),
            "city": str(it.get("publisher-place") or it.get("city") or ""),
            "abstract": str(it.get("abstract") or ""),
        })
    return records, []


def parse_csv_text(text: str, source_file: str, delimiter: str = ",") -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    for lno, row in enumerate(reader, start=2):
        norm = {str(k or "").strip().lower(): str(v or "").strip() for k, v in row.items()}
        title = norm.get("title") or norm.get("题名") or norm.get("document title") or ""
        if not title:
            continue
        auth = norm.get("authors") or norm.get("author") or norm.get("作者") or ""
        records.append({
            "source_file": source_file,
            "line_start": lno,
            "source_format": "csv",
            "source_key": norm.get("key") or norm.get("id") or norm.get("bibtex_key") or "",
            "doc_type": norm.get("type") or norm.get("doc_type") or norm.get("item type") or "article",
            "title": title,
            "authors": split_authors_string(auth),
            "year": norm.get("year") or norm.get("publication year") or norm.get("年份") or "",
            "venue": norm.get("venue") or norm.get("journal") or norm.get("publication title") or norm.get("booktitle") or norm.get("来源") or "",
            "volume": norm.get("volume") or norm.get("卷") or "",
            "issue": norm.get("issue") or norm.get("number") or norm.get("期") or "",
            "pages": norm.get("pages") or norm.get("页码") or "",
            "doi": norm.get("doi") or "",
            "arxiv_id": norm.get("arxiv") or norm.get("arxiv_id") or "",
            "url": norm.get("url") or norm.get("link") or "",
            "publisher": norm.get("publisher") or "",
            "abstract": norm.get("abstract") or norm.get("abstract note") or norm.get("摘要") or "",
        })
    return records, []


def parse_embedded_references(lines: List[str], source_file: str, start_line: int = 1) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parse numbered or unnumbered plain-text reference list lines into structured raw records."""
    records: List[Dict[str, Any]] = []
    warnings: List[str] = []
    # Merge wrapped lines
    merged: List[Tuple[int, str]] = []
    for idx, ln in enumerate(lines, start=start_line):
        s = ln.strip()
        if not s:
            continue
        if re.match(r"^(?:\[\d+\]|\d+[.)、]|-\s+|\*\s+|\\bibitem)", s) or not merged:
            merged.append((idx, s))
        else:
            prev_ln, prev_txt = merged[-1]
            merged[-1] = (prev_ln, prev_txt + " " + s)

    for lno, entry in merged:
        raw_entry = entry
        orig_num: Optional[int] = None
        m_num = re.match(r"^(?:\[(\d+)\]|(\d+)[.)、])\s*(.+)$", entry)
        if m_num:
            orig_num = int(m_num.group(1) or m_num.group(2))
            entry = m_num.group(3).strip()
        elif entry.startswith("\\bibitem"):
            m_bi = re.match(r"^\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}\s*(.+)$", entry)
            if m_bi:
                entry = m_bi.group(2).strip()

        doi_m = re.search(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/[^\s,;\"'<>]+)", entry, re.I)
        doi = normalize_doi(doi_m.group(1)) if doi_m else ""
        arxiv_m = re.search(r"arXiv:\s*(\d{4}\.\d{4,5}(?:v\d+)?)", entry, re.I)
        arxiv_id = normalize_arxiv(arxiv_m.group(1)) if arxiv_m else ""
        url_m = re.search(r"(https?://[^\s\])>\"']+)", entry)
        url = normalize_url(url_m.group(1)) if url_m else ""
        yr_m = re.search(r"\b((?:19|20)\d{2})\b", entry)
        year = yr_m.group(1) if yr_m else ""

        # GB/T 7714 pattern: Authors. Title[J]. Venue, Year, Vol(Issue): Pages.
        m_gbt = re.match(r"^(.+?)\.\s*(.+?)\[([A-Z]{1,2})(?:/OL)?\]\.\s*(.*)$", entry)
        if m_gbt:
            auth_s, title_s, tag_code, tail = m_gbt.group(1), m_gbt.group(2), m_gbt.group(3), m_gbt.group(4)
            code_to_type = {"J": "article", "C": "inproceedings", "M": "book", "D": "thesis", "R": "techreport", "S": "standard", "P": "patent", "EB": "online", "N": "newspaper"}
            dtype = code_to_type.get(tag_code, "misc")
            tail_clean = re.sub(r"(?:https?://\S+|doi:\s*\S+)", "", tail, flags=re.I).strip().rstrip(".")
            parts = [p.strip() for p in tail_clean.split(",") if p.strip()]
            venue = parts[0] if parts else ""
            vol, iss, pages = "", "", ""
            if len(parts) >= 2:
                rest = ", ".join(parts[1:])
                m_vip = re.search(r"(?:19|20)\d{2}\s*[,，]\s*(\d+)?(?:\(([^)]+)\))?\s*[:：]\s*([0-9eE\-–—]+)?", rest)
                if m_vip:
                    vol = m_vip.group(1) or ""
                    iss = m_vip.group(2) or ""
                    pages = m_vip.group(3) or ""
                else:
                    m_p = re.search(r"[:：]\s*([0-9eE]+[-–—][0-9eE]+)", rest)
                    if m_p:
                        pages = m_p.group(1)
            records.append({
                "source_file": source_file,
                "line_start": lno,
                "source_format": "inline_bib",
                "original_list_number": orig_num,
                "doc_type": dtype,
                "authors": split_authors_string(auth_s),
                "title": title_s.strip(),
                "year": year,
                "venue": venue,
                "volume": vol,
                "issue": iss,
                "pages": pages,
                "doi": doi,
                "arxiv_id": arxiv_id,
                "url": url,
            })
            continue

        # Generic APA / IEEE / English reference fallback
        m_apa = re.match(r"^(.+?)\s*\(((?:19|20)\d{2}[a-z]?)\)\.\s*(.+?)\.\s*(.*)$", entry)
        if m_apa:
            auth_s, yr_s, title_s, tail = m_apa.group(1), m_apa.group(2), m_apa.group(3), m_apa.group(4)
            tail_segs = [s.strip() for s in tail.split(",") if s.strip()]
            venue = tail_segs[0].strip("*") if tail_segs else ""
            records.append({
                "source_file": source_file,
                "line_start": lno,
                "source_format": "inline_bib",
                "original_list_number": orig_num,
                "doc_type": "article",
                "authors": split_authors_string(auth_s),
                "title": title_s.strip().strip('"'),
                "year": yr_s,
                "venue": venue,
                "doi": doi,
                "arxiv_id": arxiv_id,
                "url": url,
            })
            continue

        # Quoted title (IEEE) or sentence-separated fallback
        m_quote = re.match(r'^(.+?),\s*[“"](.+?)[”"],\s*(.+)$', entry)
        if m_quote:
            auth_s, title_s, tail = m_quote.group(1), m_quote.group(2), m_quote.group(3)
            venue = tail.split(",")[0].strip().strip("*")
            records.append({
                "source_file": source_file,
                "line_start": lno,
                "source_format": "inline_bib",
                "original_list_number": orig_num,
                "doc_type": "article",
                "authors": split_authors_string(auth_s),
                "title": title_s.strip(),
                "year": year,
                "venue": venue,
                "doi": doi,
                "arxiv_id": arxiv_id,
                "url": url,
            })
            continue

        segs = [s.strip() for s in re.split(r"\.\s+", entry) if s.strip()]
        if len(segs) >= 2:
            records.append({
                "source_file": source_file,
                "line_start": lno,
                "source_format": "inline_bib",
                "original_list_number": orig_num,
                "doc_type": "misc",
                "authors": split_authors_string(segs[0]),
                "title": segs[1].strip('"'),
                "year": year,
                "venue": segs[2] if len(segs) >= 3 else "",
                "doi": doi,
                "arxiv_id": arxiv_id,
                "url": url,
            })
        else:
            warnings.append(f"{source_file}:{lno}: could not parse inline reference cleanly: {raw_entry[:80]}")
    return records, warnings


def parse_library_file(path: Path, rel_path: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    ext = path.suffix.lower()
    txt, _, dec_warn = read_text_safe(path)
    warns: List[str] = [dec_warn] if dec_warn else []
    if not txt.strip():
        return [], warns + [f"{rel_path}: empty library file"]

    if ext == ".bib":
        recs, w = parse_bibtex_text(txt, rel_path)
        return recs, warns + w
    if ext == ".ris":
        recs, w = parse_ris_text(txt, rel_path)
        return recs, warns + w
    if ext == ".enw":
        recs, w = parse_enw_text(txt, rel_path)
        return recs, warns + w
    if ext == ".xml" and "<record" in txt.lower():
        recs, w = parse_endnote_xml(txt, rel_path)
        return recs, warns + w
    if ext in (".csv", ".tsv"):
        recs, w = parse_csv_text(txt, rel_path, delimiter="\t" if ext == ".tsv" else ",")
        return recs, warns + w
    if ext == ".jsonl":
        items = []
        for lno, ln in enumerate(txt.splitlines(), start=1):
            if ln.strip():
                try:
                    items.append(json.loads(ln))
                except Exception as exc:
                    warns.append(f"{rel_path}:{lno}: invalid JSONL line: {exc}")
        recs, w = parse_csl_or_json_records(items, rel_path)
        return recs, warns + w
    if ext in (".json", ".csl"):
        try:
            data = json.loads(txt)
            recs, w = parse_csl_or_json_records(data, rel_path)
            return recs, warns + w
        except Exception as exc:
            return [], warns + [f"{rel_path}: JSON parse error: {exc}"]

    # .txt / .md auto-sniff
    if re.search(r"^TY\s{1,2}-", txt, re.M):
        recs, w = parse_ris_text(txt, rel_path)
        return recs, warns + w
    if re.search(r"^%0\s+", txt, re.M):
        recs, w = parse_enw_text(txt, rel_path)
        return recs, warns + w
    if "Title-题名" in txt or "SrcDatabase-来源库" in txt:
        recs, w = parse_cnki_text(txt, rel_path)
        return recs, warns + w
    if re.search(r"@\w+\s*\{", txt):
        recs, w = parse_bibtex_text(txt, rel_path)
        return recs, warns + w
    recs, w = parse_embedded_references(txt.splitlines(), rel_path)
    return recs, warns + w


def _author_surnames(rec: Dict[str, Any]) -> List[str]:
    surnames: List[str] = []
    for a in rec.get("authors") or []:
        pa = parse_author_name(a)
        fam = re.sub(r"[^\w\u4e00-\u9fff]", "", (pa["family"] or "").lower())
        if fam:
            surnames.append(fam)
    return surnames


def _author_overlap(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    sa, sb = set(_author_surnames(a)), set(_author_surnames(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, min(len(sa), len(sb)))


def _has_series_part_conflict(ta: str, tb: str) -> bool:
    pat = re.compile(r"\b(part\s*(?:i|ii|iii|iv|1|2|3|4)|(?:i|ii|iii):)\b|(上篇|下篇|第一部分|第二部分|（一）|（二）)", re.I)
    ma = [m.group(0).lower() for m in pat.finditer(ta or "")]
    mb = [m.group(0).lower() for m in pat.finditer(tb or "")]
    return bool(ma and mb and ma != mb)


def _is_preprint_rec(rec: Dict[str, Any]) -> bool:
    if rec.get("doc_type") == "preprint" or bool(rec.get("arxiv_id")):
        return True
    v = (rec.get("venue") or "").lower()
    return any(pv in v for pv in PREPRINT_VENUES)


def _record_priority_score(rec: Dict[str, Any], dedup_rules: Dict[str, Any]) -> Tuple[int, int, int]:
    prec = dedup_rules.get("primary_record_precedence", ["article", "inproceedings", "book", "incollection", "thesis", "standard", "patent", "techreport", "preprint", "online", "misc", "other"])
    fmt_rank = dedup_rules.get("source_format_richness_rank", ["csl_json", "bibtex", "endnote_xml", "ris", "cnki", "enw", "csv", "inline_bib"])
    p_idx = prec.index(rec.get("doc_type", "other")) if rec.get("doc_type", "other") in prec else len(prec)
    f_idx = fmt_rank.index(rec.get("source_format", "inline_bib")) if rec.get("source_format", "inline_bib") in fmt_rank else len(fmt_rank)
    richness = sum(1 for k in ("title", "authors", "year", "venue", "volume", "issue", "pages", "doi", "abstract") if rec.get(k))
    return (p_idx, f_idx, -richness)


def deduplicate_records(
    raw_records_in: List[Dict[str, Any]],
    dedup_rules: Dict[str, Any],
    style_rules: Dict[str, Any],
) -> Dict[str, Any]:
    raw_records = [_finalize_raw_record(r, i + 1) for i, r in enumerate(raw_records_in)]
    n = len(raw_records)
    parent = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(x: int, y: int) -> None:
        rx, ry = _find(x), _find(y)
        if rx != ry:
            parent[ry] = rx

    merge_rationales: Dict[Tuple[int, int], Dict[str, str]] = {}
    protected_non_duplicates: List[Dict[str, Any]] = []
    recycled_doi_conflicts: List[Dict[str, Any]] = []
    ambiguous_candidates: List[Dict[str, Any]] = []

    for i in range(n):
        for j in range(i + 1, n):
            ra, rb = raw_records[i], raw_records[j]
            same_script = ra["language"] == rb["language"]
            tsim = title_similarity(ra["title"], rb["title"])
            aov = _author_overlap(ra, rb)
            ya, yb = ra.get("year"), rb.get("year")
            yr_gap = abs(ya - yb) if (isinstance(ya, int) and isinstance(yb, int)) else 0

            # 1. Exact ID check + Recycled DOI Guardrail
            shared_id = ""
            id_kind = ""
            if ra["doi"] and rb["doi"] and ra["doi"] == rb["doi"]:
                shared_id, id_kind = ra["doi"], "doi"
            elif ra["arxiv_id"] and rb["arxiv_id"] and ra["arxiv_id"] == rb["arxiv_id"]:
                shared_id, id_kind = ra["arxiv_id"], "arxiv_id"
            elif ra["pmid"] and rb["pmid"] and ra["pmid"] == rb["pmid"]:
                shared_id, id_kind = ra["pmid"], "pmid"

            if shared_id:
                if same_script and tsim < 0.30 and aov == 0.0:
                    recycled_doi_conflicts.append({
                        "raw_a": ra["raw_id"],
                        "raw_b": rb["raw_id"],
                        "title_a": ra["title"],
                        "title_b": rb["title"],
                        "rule": "recycled_or_conflicting_identifier_guard",
                        "metrics": {"shared_id": shared_id, "id_kind": id_kind, "title_similarity": round(tsim, 3), "author_overlap": round(aov, 3)},
                    })
                    continue
                _union(i, j)
                merge_rationales[(i, j)] = {"tier": "tier_1_exact_id", "rule": f"Shared normalized {id_kind}: {shared_id}"}
                continue

            # 2. Anti-False-Merge Guardrails
            if _has_series_part_conflict(ra["title"], rb["title"]):
                protected_non_duplicates.append({"raw_a": ra["raw_id"], "raw_b": rb["raw_id"], "title_a": ra["title"], "title_b": rb["title"], "rule": "distinct_series_part_markers"})
                continue
            if ra["doi"] and rb["doi"] and ra["doi"] != rb["doi"] and not (_is_preprint_rec(ra) or _is_preprint_rec(rb)):
                if aov >= 0.5 or tsim >= 0.5:
                    protected_non_duplicates.append({"raw_a": ra["raw_id"], "raw_b": rb["raw_id"], "title_a": ra["title"], "title_b": rb["title"], "rule": "distinct_valid_non_preprint_dois"})
                    continue
            pa, pb = ra["pages_info"], rb["pages_info"]
            if (
                ya and yb and ya == yb
                and ra["volume"] and rb["volume"] and ra["volume"] == rb["volume"]
                and pa["start"] and pb["start"] and pa["end"] and pb["end"]
                and (pa["end"] < pb["start"] or pb["end"] < pa["start"])
            ):
                protected_non_duplicates.append({"raw_a": ra["raw_id"], "raw_b": rb["raw_id"], "title_a": ra["title"], "title_b": rb["title"], "rule": "disjoint_pages_same_volume_year"})
                continue

            # 3. Tier 2 Canonical Match (Same script exact/preprint or cross-script translated title)
            if same_script:
                if tsim >= 0.94 and (aov >= 0.50 or not _author_surnames(ra) or not _author_surnames(rb)) and yr_gap <= 2:
                    _union(i, j)
                    merge_rationales[(i, j)] = {"tier": "tier_2_title_author", "rule": f"Title sim={tsim:.2f}, author_overlap={aov:.2f}, year_gap={yr_gap}"}
                    continue
                if (_is_preprint_rec(ra) != _is_preprint_rec(rb)) and tsim >= 0.88 and aov >= 0.50 and yr_gap <= 4:
                    _union(i, j)
                    merge_rationales[(i, j)] = {"tier": "tier_2_preprint_to_published", "rule": f"Preprint/published upgrade (title_sim={tsim:.2f}, author_overlap={aov:.2f})"}
                    continue
                if tsim < 0.65:
                    if aov >= 0.5 and yr_gap <= 1:
                        protected_non_duplicates.append({"raw_a": ra["raw_id"], "raw_b": rb["raw_id"], "title_a": ra["title"], "title_b": rb["title"], "rule": f"same_author_distinct_title (sim={tsim:.2f})"})
                    continue
                # Tier 3 ambiguous
                if 0.65 <= tsim < 0.94 and (aov >= 0.40 or yr_gap <= 2):
                    ambiguous_candidates.append({
                        "candidate_id": f"AMB_{len(ambiguous_candidates) + 1:03d}",
                        "raw_ids": [ra["raw_id"], rb["raw_id"]],
                        "titles": [ra["title"], rb["title"]],
                        "years": [ya, yb],
                        "title_similarity": round(tsim, 3),
                        "author_overlap": round(aov, 3),
                        "reason": "Moderate title similarity / possible extended conference-to-journal or updated version",
                    })
            else:
                # Cross-script bilingual match
                tr_sim = max(
                    title_similarity(ra.get("title_translated", ""), rb["title"]),
                    title_similarity(rb.get("title_translated", ""), ra["title"]),
                )
                if tr_sim >= 0.90:
                    _union(i, j)
                    merge_rationales[(i, j)] = {"tier": "tier_2_bilingual_translated_title", "rule": f"Explicit translated title similarity={tr_sim:.2f}"}
                elif ya and yb and ya == yb and ra["volume"] and rb["volume"] and ra["volume"] == rb["volume"] and ra["pages"] and ra["pages"] == rb["pages"]:
                    _union(i, j)
                    merge_rationales[(i, j)] = {"tier": "tier_2_bilingual_bibliographic_coords", "rule": f"Matched cross-script year={ya}, vol={ra['volume']}, pages={ra['pages']}"}
                elif ya and yb and yr_gap <= 1 and aov >= 0.50:
                    ambiguous_candidates.append({
                        "candidate_id": f"AMB_{len(ambiguous_candidates) + 1:03d}",
                        "raw_ids": [ra["raw_id"], rb["raw_id"]],
                        "titles": [ra["title"], rb["title"]],
                        "years": [ya, yb],
                        "title_similarity": round(tr_sim, 3),
                        "author_overlap": round(aov, 3),
                        "reason": "Cross-script bilingual records with shared year/author surname; verify if identical work",
                    })

    clusters: Dict[int, List[int]] = {}
    for idx in range(n):
        clusters.setdefault(_find(idx), []).append(idx)

    canonical_records: List[Dict[str, Any]] = []
    raw_to_canonical_map: Dict[str, str] = {}
    key_to_canonical_map: Dict[str, str] = {}
    inline_num_to_canonical_map: Dict[str, str] = {}
    merge_groups: List[Dict[str, Any]] = []
    req_by_type = style_rules.get("required_fields_by_doc_type", {})

    for c_idx, member_indices in enumerate(clusters.values(), start=1):
        ref_id = f"REF_{c_idx:03d}"
        members = [raw_records[i] for i in member_indices]
        members_sorted = sorted(members, key=lambda r: _record_priority_score(r, dedup_rules))
        prim = dict(members_sorted[0])
        prim["ref_id"] = ref_id
        prim["member_raw_ids"] = [m["raw_id"] for m in members]
        prim["source_keys"] = list(dict.fromkeys(m["source_key"] for m in members if m.get("source_key")))
        prim["original_list_numbers"] = list(dict.fromkeys(m["original_list_number"] for m in members if m.get("original_list_number") is not None))

        # Field-level provenance & enrichment from secondary members
        fp: Dict[str, Any] = {}
        tracked_fields = ["title", "title_translated", "authors", "year", "venue", "volume", "issue", "pages", "doi", "arxiv_id", "pmid", "isbn", "url", "publisher", "city", "abstract"]
        for fld in tracked_fields:
            chosen = prim.get(fld)
            chosen_src = prim["raw_id"]
            chosen_loc = prim["source_locator"]
            alternates: List[Dict[str, Any]] = []
            for sec in members_sorted[1:]:
                sval = sec.get(fld)
                if not chosen and sval:
                    prim[fld] = sval
                    chosen = sval
                    chosen_src = sec["raw_id"]
                    chosen_loc = sec["source_locator"]
                    if fld == "pages":
                        prim["pages_info"] = sec["pages_info"]
                    if fld == "doi":
                        prim["doi_verification_status"] = sec["doi_verification_status"]
                elif sval and sval != chosen:
                    alternates.append({"value": sval, "raw_id": sec["raw_id"], "source_locator": sec["source_locator"]})
            if chosen:
                fp[fld] = {
                    "value": chosen,
                    "source_record_id": chosen_src,
                    "source_locator": chosen_loc,
                    "status": "primary" if chosen_src == prim["raw_id"] else "enriched_from_duplicate",
                    "alternate_values": alternates,
                }
        if not fp:
            fp["title"] = {"value": prim.get("title", ""), "source_record_id": prim["raw_id"], "source_locator": prim["source_locator"], "status": "primary", "alternate_values": []}
        prim["field_provenance"] = fp
        req_flds = req_by_type.get(prim["doc_type"], ["authors", "title", "year"])
        prim["missing_required_fields"] = [f for f in req_flds if not prim.get(f)]

        canonical_records.append(prim)
        for m in members:
            raw_to_canonical_map[m["raw_id"]] = ref_id
            if m.get("source_key"):
                key_to_canonical_map[m["source_key"]] = ref_id
            if m.get("original_list_number") is not None:
                inline_num_to_canonical_map[str(m["original_list_number"])] = ref_id

        if len(members) > 1:
            pair_rats = []
            for a_i in range(len(member_indices)):
                for b_i in range(a_i + 1, len(member_indices)):
                    key = (min(member_indices[a_i], member_indices[b_i]), max(member_indices[a_i], member_indices[b_i]))
                    if key in merge_rationales:
                        pair_rats.append(merge_rationales[key])
            merge_groups.append({
                "canonical_id": ref_id,
                "primary_raw_id": prim["raw_id"],
                "member_records": [{"raw_id": m["raw_id"], "source_locator": m["source_locator"], "title": m["title"]} for m in members],
                "pair_rationales": pair_rats,
            })

    # Filter ambiguous_candidates already merged transitively
    filtered_amb = []
    for ac in ambiguous_candidates:
        cids = {raw_to_canonical_map.get(rid) for rid in ac["raw_ids"]}
        if len(cids) > 1:
            ac["canonical_ids"] = [raw_to_canonical_map[rid] for rid in ac["raw_ids"]]
            filtered_amb.append(ac)

    return {
        "raw_records": raw_records,
        "canonical_records": canonical_records,
        "raw_to_canonical_map": raw_to_canonical_map,
        "key_to_canonical_map": key_to_canonical_map,
        "inline_num_to_canonical_map": inline_num_to_canonical_map,
        "merge_groups": merge_groups,
        "protected_non_duplicates": protected_non_duplicates,
        "recycled_doi_conflicts": recycled_doi_conflicts,
        "ambiguous_duplicate_candidates": filtered_amb,
        "stats": {
            "raw_record_count": n,
            "canonical_record_count": len(canonical_records),
            "duplicates_removed": n - len(canonical_records),
            "recycled_doi_conflicts_count": len(recycled_doi_conflicts),
            "ambiguous_candidates_count": len(filtered_amb),
        },
    }


# ==================== STYLE FORMATTING & EXPORTS ====================

def _format_authors_gbt(authors: List[str], lang: str) -> str:
    if not authors:
        return ""
    formatted = []
    for a in authors[:3]:
        pa = parse_author_name(a)
        if pa["is_cjk"] or pa["is_org"]:
            formatted.append(pa["raw"])
        else:
            inits = pa["initials"].replace(" ", "")
            formatted.append(f"{pa['family'].upper()} {inits}".strip())
    joined = ", ".join(formatted)
    if len(authors) > 3:
        joined += ", 等" if lang == "zh" else ", et al"
    return joined


def _format_authors_apa(authors: List[str], max_authors: int = 20) -> str:
    if not authors:
        return ""
    fmt: List[str] = []
    for a in authors:
        pa = parse_author_name(a)
        if pa["is_cjk"] or pa["is_org"]:
            fmt.append(pa["raw"])
        else:
            inits = " ".join(f"{c}." for c in pa["initials"].split())
            fmt.append(f"{pa['family']}, {inits}".strip(", "))
    if len(fmt) == 1:
        return fmt[0]
    if len(fmt) <= max_authors:
        return ", ".join(fmt[:-1]) + f", & {fmt[-1]}"
    return ", ".join(fmt[: max_authors - 1]) + f", ... {fmt[-1]}"


def _format_authors_ieee(authors: List[str]) -> str:
    if not authors:
        return ""
    fmt: List[str] = []
    for a in authors[:6]:
        pa = parse_author_name(a)
        if pa["is_cjk"] or pa["is_org"]:
            fmt.append(pa["raw"])
        else:
            inits = " ".join(f"{c}." for c in pa["initials"].split())
            fmt.append(f"{inits} {pa['family']}".strip())
    if len(authors) > 6:
        return ", ".join(fmt[:1]) + " et al."
    if len(fmt) == 1:
        return fmt[0]
    if len(fmt) == 2:
        return f"{fmt[0]} and {fmt[1]}"
    return ", ".join(fmt[:-1]) + f", and {fmt[-1]}"


def _format_authors_vancouver(authors: List[str]) -> str:
    if not authors:
        return ""
    fmt = []
    for a in authors[:6]:
        pa = parse_author_name(a)
        if pa["is_cjk"] or pa["is_org"]:
            fmt.append(pa["raw"])
        else:
            fmt.append(f"{pa['family']} {pa['initials'].replace(' ', '')}".strip())
    res = ", ".join(fmt)
    if len(authors) > 6:
        res += ", et al"
    return res


def _format_authors_chicago(authors: List[str]) -> str:
    if not authors:
        return ""
    first_pa = parse_author_name(authors[0])
    first = first_pa["raw"] if (first_pa["is_cjk"] or first_pa["is_org"]) else f"{first_pa['family']}, {first_pa['given']}".strip(", ")
    if len(authors) == 1:
        return first
    others = []
    for a in authors[1:10]:
        pa = parse_author_name(a)
        others.append(pa["raw"] if (pa["is_cjk"] or pa["is_org"]) else f"{pa['given']} {pa['family']}".strip())
    if len(authors) > 10:
        return f"{first}, " + ", ".join(others[:7]) + ", et al."
    if len(others) == 1:
        return f"{first}, and {others[0]}"
    return f"{first}, " + ", ".join(others[:-1]) + f", and {others[-1]}"


def _author_sort_key(rec: Dict[str, Any]) -> Tuple[int, str, int, str]:
    auths = rec.get("authors") or []
    lang_rank = 0 if rec.get("language") == "zh" else 1
    first_fam = parse_author_name(auths[0])["family"].lower() if auths else "zzz"
    yr = rec.get("year") if isinstance(rec.get("year"), int) else 9999
    return (lang_rank, first_fam, yr, normalize_title(rec.get("title", "")))


def sort_records_for_style(
    records: List[Dict[str, Any]],
    target_style: str,
    cited_order_ids: List[str],
) -> List[Dict[str, Any]]:
    by_id = {r["ref_id"]: r for r in records}
    cited = [by_id[rid] for rid in cited_order_ids if rid in by_id]
    if target_style in ("apa7", "apa6", "gbt7714_2015_author_year", "chicago_author_date"):
        return sorted(cited, key=_author_sort_key)
    return cited


def compute_author_year_suffixes(sorted_records: List[Dict[str, Any]]) -> Dict[str, str]:
    groups: Dict[Tuple[str, Any], List[str]] = {}
    for rec in sorted_records:
        auths = rec.get("authors") or []
        fam = parse_author_name(auths[0])["family"].lower() if auths else "anon"
        yr = rec.get("year") or "n.d."
        groups.setdefault((fam, yr), []).append(rec["ref_id"])
    suffixes: Dict[str, str] = {}
    for rids in groups.values():
        if len(rids) > 1:
            for idx, rid in enumerate(rids):
                suffixes[rid] = chr(ord("a") + idx)
    return suffixes


def compute_canonical_bibtex_keys(records: List[Dict[str, Any]]) -> Dict[str, str]:
    used: set[str] = set()
    keys: Dict[str, str] = {}
    for rec in records:
        auths = rec.get("authors") or []
        fam = re.sub(r"[^A-Za-z0-9]", "", parse_author_name(auths[0])["family"]) if auths else "ref"
        if not fam:
            fam = "ref"
        yr = str(rec.get("year") or "nd")
        first_w = next((w for w in re.findall(r"[A-Za-z0-9]+", rec.get("title", "")) if len(w) >= 3), "work").lower()
        base = f"{fam.lower()}{yr}{first_w}"
        cand = base
        suf = 0
        while cand in used:
            cand = f"{base}{chr(ord('a') + suf)}"
            suf += 1
        used.add(cand)
        keys[rec["ref_id"]] = cand
    return keys


def _compress_nums(nums: List[int], dash: str = "-") -> List[str]:
    if not nums:
        return []
    s = sorted(set(nums))
    out: List[str] = []
    start = prev = s[0]
    for n in s[1:]:
        if n == prev + 1:
            prev = n
        else:
            if prev - start >= 2:
                out.append(f"{start}{dash}{prev}")
            elif prev > start:
                out.append(f"{start},{prev}")
            else:
                out.append(str(start))
            start = prev = n
    if prev - start >= 2:
        out.append(f"{start}{dash}{prev}")
    elif prev > start:
        out.append(f"{start},{prev}")
    else:
        out.append(str(start))
    return out


def format_in_text_citation(
    entries: List[Tuple[Dict[str, Any], int, str]],
    target_style: str,
    in_text_format: str,
    narrative: bool = False,
    bibtex_keys: Optional[Dict[str, str]] = None,
) -> str:
    if not entries:
        return ""
    if in_text_format == "latex_cite":
        ks = [bibtex_keys.get(r["ref_id"], r["ref_id"].lower()) if bibtex_keys else r["ref_id"].lower() for r, _, _ in entries]
        if target_style in ("apa7", "apa6", "gbt7714_2015_author_year", "chicago_author_date"):
            cmd = "\\citet" if narrative else "\\citep"
        else:
            cmd = "\\cite"
        return f"{cmd}{{{','.join(ks)}}}"

    if target_style in ("apa7", "apa6", "gbt7714_2015_author_year", "chicago_author_date") or in_text_format == "author_year":
        et_al_thresh = 3 if target_style in ("apa7", "gbt7714_2015_author_year") else (4 if target_style == "chicago_author_date" else 6)
        conj_paren = " & " if target_style in ("apa7", "apa6") else (" and " if target_style == "chicago_author_date" else "等")
        parts: List[str] = []
        for rec, _, suf in entries:
            auths = rec.get("authors") or []
            yr_str = f"{rec.get('year') or 'n.d.'}{suf}"
            is_zh = rec.get("language") == "zh"
            if not auths:
                a_txt = (rec.get("title") or "Anonymous")[:24]
            elif len(auths) == 1:
                pa = parse_author_name(auths[0])
                a_txt = pa["raw"] if (pa["is_cjk"] or pa["is_org"]) else pa["family"]
            elif len(auths) == 2:
                p1, p2 = parse_author_name(auths[0]), parse_author_name(auths[1])
                n1 = p1["raw"] if (p1["is_cjk"] or p1["is_org"]) else p1["family"]
                n2 = p2["raw"] if (p2["is_cjk"] or p2["is_org"]) else p2["family"]
                c_word = "和" if is_zh else (" and " if narrative else conj_paren)
                a_txt = f"{n1}{c_word}{n2}" if is_zh else f"{n1}{c_word}{n2}"
            else:
                if len(auths) >= et_al_thresh:
                    p1 = parse_author_name(auths[0])
                    n1 = p1["raw"] if (p1["is_cjk"] or p1["is_org"]) else p1["family"]
                    a_txt = f"{n1}等" if is_zh else f"{n1} et al."
                else:
                    names = [parse_author_name(a)["raw" if (parse_author_name(a)["is_cjk"] or parse_author_name(a)["is_org"]) else "family"] for a in auths]
                    a_txt = "、".join(names) if is_zh else (", ".join(names[:-1]) + f", {'and' if narrative else conj_paren.strip()} {names[-1]}")
            if narrative:
                parts.append(f"{a_txt} ({yr_str})")
            else:
                sep = " " if target_style == "chicago_author_date" else ", "
                parts.append(f"{a_txt}{sep}{yr_str}")
        if narrative:
            return "; ".join(parts)
        return "(" + "; ".join(parts) + ")"

    nums = [n for _, n, _ in entries]
    if in_text_format == "bracket_separate":
        # IEEE style: [1]–[3], [5]
        s = sorted(set(nums))
        segs: List[str] = []
        start = prev = s[0]
        for n in s[1:]:
            if n == prev + 1:
                prev = n
            else:
                if prev - start >= 2:
                    segs.append(f"[{start}]–[{prev}]")
                elif prev > start:
                    segs.extend([f"[{start}]", f"[{prev}]"])
                else:
                    segs.append(f"[{start}]")
                start = prev = n
        if prev - start >= 2:
            segs.append(f"[{start}]–[{prev}]")
        elif prev > start:
            segs.extend([f"[{start}]", f"[{prev}]"])
        else:
            segs.append(f"[{start}]")
        return ", ".join(segs)

    compressed = ",".join(_compress_nums(nums, dash="–" if target_style == "nature" else "-"))
    if in_text_format == "superscript_bracket":
        return f"<sup>[{compressed}]</sup>"
    if in_text_format == "superscript_html":
        return f"<sup>{compressed}</sup>"
    if in_text_format == "superscript_md":
        return f"^[{compressed}]"
    if in_text_format == "footnote":
        return "".join(f"[^{n}]" for n in sorted(set(nums)))
    return f"[{compressed}]"


def format_reference_entry(
    rec: Dict[str, Any],
    target_style: str,
    assigned_number: int,
    year_suffix: str = "",
    style_rules: Optional[Dict[str, Any]] = None,
) -> str:
    lang = rec.get("language") or "en"
    title = (rec.get("title") or "").strip().rstrip(".")
    venue = (rec.get("venue") or "").strip().rstrip(".")
    year = str(rec.get("year") or "")
    yr_suf = f"{year}{year_suffix}" if year else (f"n.d.{year_suffix}" if year_suffix else "")
    vol = (rec.get("volume") or "").strip()
    iss = (rec.get("issue") or "").strip()
    pages = (rec.get("pages") or "").strip()
    doi = (rec.get("doi") or "").strip()
    url = (rec.get("url") or "").strip()
    pub = (rec.get("publisher") or "").strip()
    city = (rec.get("city") or "").strip()
    arxiv_id = (rec.get("arxiv_id") or "").strip()

    if target_style in ("gbt7714_2015_numeric", "gbt7714_2015_author_year"):
        doc_codes = (style_rules or {}).get("supported_styles", {}).get("gbt7714_2015_numeric", {}).get("doc_type_codes", {})
        code = doc_codes.get(rec.get("doc_type", "other"), "J" if venue else "Z")
        ol = "/OL" if (url or doi) and code == "EB" else ""
        auth_s = _format_authors_gbt(rec.get("authors") or [], lang)
        head = f"[{assigned_number}] " if target_style == "gbt7714_2015_numeric" else ""
        yr_block = f"({yr_suf}). " if (target_style == "gbt7714_2015_author_year" and yr_suf) else ""
        prefix = f"{head}{auth_s}. {yr_block}".strip()
        if not auth_s:
            prefix = f"{head}".strip()
        title_part = f"{title}[{code}{ol}]" if title else f"[Untitled][{code}{ol}]"
        tail_parts: List[str] = []
        if venue:
            tail_parts.append(venue)
        elif pub:
            pub_loc = f"{city}: {pub}" if city else pub
            tail_parts.append(pub_loc)
        elif arxiv_id:
            tail_parts.append(f"arXiv:{arxiv_id}")
        vip = ""
        if target_style == "gbt7714_2015_numeric" and yr_suf:
            vip = yr_suf
        if vol and iss:
            vip = f"{vip}, {vol}({iss})" if vip else f"{vol}({iss})"
        elif vol:
            vip = f"{vip}, {vol}" if vip else vol
        elif iss:
            vip = f"{vip}({iss})" if vip else f"({iss})"
        if pages:
            vip = f"{vip}: {pages}" if vip else pages
        if vip:
            tail_parts.append(vip)
        body = f"{prefix} {title_part}".strip() if prefix else title_part
        if tail_parts:
            body += ". " + ", ".join(tail_parts)
        body = body.rstrip(".") + "."
        if doi:
            body += f" DOI:{doi}."
        elif url and code == "EB":
            body += f" {url}."
        return re.sub(r"\s+", " ", body).strip()

    if target_style in ("apa7", "apa6"):
        max_a = 20 if target_style == "apa7" else 7
        auth_s = _format_authors_apa(rec.get("authors") or [], max_authors=max_a)
        yr_part = f"({yr_suf or 'n.d.'})."
        parts = [p for p in (auth_s, yr_part, f"{title}." if title else "") if p]
        v_str = ""
        if venue:
            v_str = f"*{venue}*"
            if vol and iss:
                v_str += f", *{vol}*({iss})"
            elif vol:
                v_str += f", *{vol}*"
            if pages:
                v_str += f", {pages}"
            v_str += "."
        elif pub:
            v_str = f"{pub}."
        if v_str:
            parts.append(v_str)
        if doi:
            parts.append(f"https://doi.org/{doi}")
        elif url:
            parts.append(url)
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    if target_style == "ieee":
        auth_s = _format_authors_ieee(rec.get("authors") or [])
        t_part = f'"{title},"' if title else ""
        segs = [s for s in (auth_s, t_part) if s]
        tail = []
        if venue:
            tail.append(f"*{venue}*")
        elif pub:
            tail.append(pub)
        if vol:
            tail.append(f"vol. {vol}")
        if iss:
            tail.append(f"no. {iss}")
        if pages:
            tail.append(f"pp. {pages}")
        if yr_suf:
            tail.append(yr_suf)
        line = f"[{assigned_number}] " + ", ".join(segs + tail).rstrip(",.") + "."
        if doi:
            line += f" doi: {doi}."
        return re.sub(r"\s+", " ", line).strip()

    if target_style == "nature":
        auth_s = _format_authors_apa(rec.get("authors") or [], max_authors=5).replace(", & ", ", ")
        segs = [s for s in (auth_s, f"{title}." if title else "") if s]
        v_str = f"*{venue}*" if venue else (pub or "")
        if vol:
            v_str += f" **{vol}**"
        if pages:
            v_str += f", {pages}"
        if yr_suf:
            v_str += f" ({yr_suf})"
        line = f"{assigned_number}. " + " ".join(segs)
        if v_str.strip():
            line += f" {v_str.strip()}."
        if doi:
            line += f" https://doi.org/{doi}"
        return re.sub(r"\s+", " ", line).strip()

    if target_style == "vancouver":
        auth_s = _format_authors_vancouver(rec.get("authors") or [])
        segs = [s for s in (f"{auth_s}." if auth_s else "", f"{title}." if title else "") if s]
        tail = venue or pub or ""
        vip = yr_suf
        if vol and iss:
            vip += f";{vol}({iss})"
        elif vol:
            vip += f";{vol}"
        if pages:
            vip += f":{pages}"
        line = f"{assigned_number}. " + " ".join(segs)
        if tail:
            line += f" {tail}."
        if vip.strip(";"):
            line += f" {vip.strip(';')}."
        if doi:
            line += f" doi:{doi}."
        return re.sub(r"\s+", " ", line).strip()

    # chicago_author_date
    auth_s = _format_authors_chicago(rec.get("authors") or [])
    yr_part = f"{yr_suf or 'n.d.'}."
    segs = [s for s in (f"{auth_s}." if auth_s else "", yr_part, f'"{title}."' if title else "") if s]
    v_str = f"*{venue}*" if venue else (pub or "")
    if vol:
        v_str += f" {vol}"
        if iss:
            v_str += f", no. {iss}"
    if pages:
        v_str += f": {pages}"
    if v_str.strip():
        segs.append(f"{v_str.strip()}.")
    if doi:
        segs.append(f"https://doi.org/{doi}.")
    return re.sub(r"\s+", " ", " ".join(segs)).strip()


def export_bibtex(records: List[Dict[str, Any]], keys: Dict[str, str]) -> str:
    blocks: List[str] = []
    for r in records:
        k = keys.get(r["ref_id"], r["ref_id"].lower())
        btype = r.get("doc_type", "misc")
        if btype == "preprint":
            btype = "misc"
        elif btype == "other":
            btype = "misc"
        lines = [f"@{btype}{{{k},"]
        if r.get("title"):
            lines.append(f"  title = {{{r['title']}}},")
        if r.get("authors"):
            lines.append(f"  author = {{{' and '.join(r['authors'])}}},")
        if r.get("year"):
            lines.append(f"  year = {{{r['year']}}},")
        if r.get("venue"):
            jfld = "booktitle" if btype == "inproceedings" else "journal"
            lines.append(f"  {jfld} = {{{r['venue']}}},")
        if r.get("volume"):
            lines.append(f"  volume = {{{r['volume']}}},")
        if r.get("issue"):
            lines.append(f"  number = {{{r['issue']}}},")
        if r.get("pages"):
            lines.append(f"  pages = {{{r['pages']}}},")
        if r.get("doi"):
            lines.append(f"  doi = {{{r['doi']}}},")
        if r.get("arxiv_id"):
            lines.append(f"  eprint = {{{r['arxiv_id']}}},")
            lines.append("  archivePrefix = {arXiv},")
        if r.get("publisher"):
            lines.append(f"  publisher = {{{r['publisher']}}},")
        if r.get("url"):
            lines.append(f"  url = {{{r['url']}}},")
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def export_ris(records: List[Dict[str, Any]]) -> str:
    rev_map = {v: k for k, v in _RIS_TYPE_MAP.items()}
    lines: List[str] = []
    for r in records:
        lines.append(f"TY  - {rev_map.get(r.get('doc_type', 'misc'), 'GEN')}")
        for a in r.get("authors") or []:
            lines.append(f"AU  - {a}")
        if r.get("title"):
            lines.append(f"TI  - {r['title']}")
        if r.get("venue"):
            lines.append(f"JO  - {r['venue']}")
        if r.get("year"):
            lines.append(f"PY  - {r['year']}")
        if r.get("volume"):
            lines.append(f"VL  - {r['volume']}")
        if r.get("issue"):
            lines.append(f"IS  - {r['issue']}")
        pinfo = r.get("pages_info") or {}
        if pinfo.get("start") and pinfo.get("end"):
            lines.append(f"SP  - {pinfo['start']}")
            lines.append(f"EP  - {pinfo['end']}")
        elif r.get("pages"):
            lines.append(f"SP  - {r['pages']}")
        if r.get("doi"):
            lines.append(f"DO  - {r['doi']}")
        if r.get("url"):
            lines.append(f"UR  - {r['url']}")
        if r.get("publisher"):
            lines.append(f"PB  - {r['publisher']}")
        lines.append("ER  - ")
        lines.append("")
    return "\n".join(lines)


def export_csl_json(records: List[Dict[str, Any]], keys: Dict[str, str]) -> str:
    out: List[Dict[str, Any]] = []
    csl_t = {"article": "article-journal", "inproceedings": "paper-conference", "incollection": "chapter", "online": "webpage", "techreport": "report"}
    for r in records:
        item: Dict[str, Any] = {
            "id": keys.get(r["ref_id"], r["ref_id"]),
            "type": csl_t.get(r.get("doc_type", "misc"), "article"),
            "title": r.get("title", ""),
        }
        if r.get("authors"):
            item["author"] = [{"family": parse_author_name(a)["family"], "given": parse_author_name(a)["given"]} for a in r["authors"]]
        if r.get("year"):
            item["issued"] = {"date-parts": [[r["year"]]]}
        for k_src, k_csl in (("venue", "container-title"), ("volume", "volume"), ("issue", "issue"), ("pages", "page"), ("doi", "DOI"), ("url", "URL"), ("publisher", "publisher")):
            if r.get(k_src):
                item[k_csl] = r[k_src]
        out.append(item)
    return json.dumps(out, ensure_ascii=False, indent=2) + "\n"


def export_csv(records: List[Dict[str, Any]], assigned_numbers: Dict[str, int]) -> str:
    buf = io.StringIO()
    cols = ["assigned_number", "ref_id", "doc_type", "authors", "year", "title", "venue", "volume", "issue", "pages", "doi", "arxiv_id", "url", "doi_verification_status", "missing_required_fields", "source_locators"]
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    for r in records:
        writer.writerow({
            "assigned_number": assigned_numbers.get(r["ref_id"], ""),
            "ref_id": r["ref_id"],
            "doc_type": r.get("doc_type", ""),
            "authors": "; ".join(r.get("authors") or []),
            "year": r.get("year") or "",
            "title": r.get("title", ""),
            "venue": r.get("venue", ""),
            "volume": r.get("volume", ""),
            "issue": r.get("issue", ""),
            "pages": r.get("pages", ""),
            "doi": r.get("doi", ""),
            "arxiv_id": r.get("arxiv_id", ""),
            "url": r.get("url", ""),
            "doi_verification_status": r.get("doi_verification_status", ""),
            "missing_required_fields": "; ".join(r.get("missing_required_fields") or []),
            "source_locators": "; ".join(r.get("member_raw_ids") or []),
        })
    return buf.getvalue()
