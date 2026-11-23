#!/usr/bin/env python3
"""Step 2: Parse multi-format reference libraries and perform explainable 4-tier deduplication.

Supports BibTeX (.bib), RIS (.ris), EndNote (.enw / .xml), CNKI (NoteExpress / RefWorks / custom),
CSL-JSON / JSON, CSV / TSV, plain-text lists, and embedded manuscript reference sections.
Produces:
- library_snapshot.json (immutable RAW-001.. records with exact file/line provenance)
- canonical_library.json (deduplicated REF-001.. records with field_provenance & missing_fields)
- dedup_report.json (merge_groups, preserved_distinct_pairs, ambiguous_candidates)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    extract_arxiv_id,
    first_author_surname,
    has_cjk,
    load_json_safe,
    load_resource_json,
    normalize_doi,
    normalize_title,
    read_document_any,
    read_text_safe,
    save_json,
    split_authors_list,
    strip_latex_markup,
    title_similarity,
    validate_doi_syntax,
)


def _infer_type(raw_type: str, venue: str = "", extra: str = "") -> str:
    rt = (raw_type or "").lower().strip()
    comb = f"{rt} {venue} {extra}".lower()
    if "arxiv" in comb or "biorxiv" in comb or "medrxiv" in comb or "preprint" in comb:
        return "preprint"
    if rt in ("article", "jour", "journal", "journal article", "期刊论文", "j"):
        return "journal_article"
    if rt in ("inproceedings", "conference", "conf", "cpaper", "proceedings", "conference paper", "会议论文", "c"):
        return "conference_paper"
    if rt in ("incollection", "inbook", "chap", "book chapter"):
        return "book_chapter"
    if rt in ("book", "monograph", "m"):
        return "book"
    if rt in ("phdthesis", "mastersthesis", "thesis", "thes", "学位论文", "d"):
        return "thesis"
    if rt in ("techreport", "report", "rprt", "r"):
        return "report"
    if rt in ("patent", "pat", "p"):
        return "patent"
    if rt in ("standard", "s"):
        return "standard"
    if rt in ("misc", "online", "elec", "web", "eb/ol"):
        return "web" if "http" in comb else "misc"
    if "proceedings" in comb or "conference" in comb or "symposium" in comb:
        return "conference_paper"
    if venue:
        return "journal_article"
    return "misc"


def _clean_year(raw: str) -> str:
    m = re.search(r"\b(19\d{2}|20\d{2})\b", str(raw or ""))
    return m.group(1) if m else ""


def parse_bibtex(text: str, rel_path: str) -> List[Dict[str, Any]]:
    """Brace-depth-aware BibTeX parser supporting @string macros and line provenance."""
    strings: Dict[str, str] = {}
    records: List[Dict[str, Any]] = []

    # First pass for @string{k = "v"}
    for m in re.finditer(r"@string\s*\{\s*([A-Za-z0-9_-]+)\s*=\s*[\{\"]([^\}\"]+)[\}\"]\s*\}", text, flags=re.IGNORECASE):
        strings[m.group(1).lower()] = m.group(2)

    lines = text.splitlines()
    # Map character offset -> line number
    line_starts = [0]
    for ln in lines:
        line_starts.append(line_starts[-1] + len(ln) + 1)

    def _offset_to_line(pos: int) -> int:
        import bisect
        return bisect.bisect_right(line_starts, pos)

    idx = 0
    n = len(text)
    while idx < n:
        at = text.find("@", idx)
        if at == -1:
            break
        m_head = re.match(r"@([A-Za-z]+)\s*([\{\(])", text[at:])
        if not m_head:
            idx = at + 1
            continue
        etype = m_head.group(1).lower()
        open_ch = m_head.group(2)
        close_ch = "}" if open_ch == "{" else ")"
        start_pos = at + m_head.end()
        depth = 1
        pos = start_pos
        while pos < n and depth > 0:
            ch = text[pos]
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
            pos += 1
        body = text[start_pos : pos - 1]
        idx = pos
        if etype in ("string", "preamble", "comment"):
            continue

        comma_pos = body.find(",")
        if comma_pos == -1:
            continue
        cite_key = body[:comma_pos].strip()
        fields_str = body[comma_pos + 1 :]

        fields: Dict[str, str] = {}
        f_pos = 0
        flen = len(fields_str)
        while f_pos < flen:
            m_fk = re.match(r"[\s,]*([A-Za-z0-9_-]+)\s*=\s*", fields_str[f_pos:])
            if not m_fk:
                break
            fk = m_fk.group(1).lower()
            f_pos += m_fk.end()
            if f_pos >= flen:
                break
            val_parts: List[str] = []
            while f_pos < flen:
                c = fields_str[f_pos]
                if c == "{":
                    d = 1
                    v_start = f_pos + 1
                    f_pos += 1
                    while f_pos < flen and d > 0:
                        if fields_str[f_pos] == "{":
                            d += 1
                        elif fields_str[f_pos] == "}":
                            d -= 1
                        f_pos += 1
                    val_parts.append(fields_str[v_start : f_pos - 1])
                elif c == '"':
                    v_start = f_pos + 1
                    f_pos += 1
                    while f_pos < flen:
                        if fields_str[f_pos] == '"' and fields_str[f_pos - 1] != "\\":
                            break
                        f_pos += 1
                    val_parts.append(fields_str[v_start:f_pos])
                    f_pos += 1
                else:
                    m_tok = re.match(r"([^,#\s\}]+)", fields_str[f_pos:])
                    if m_tok:
                        tok = m_tok.group(1)
                        val_parts.append(strings.get(tok.lower(), tok))
                        f_pos += m_tok.end()
                    else:
                        break
                # Skip spaces; check concatenation '#'
                while f_pos < flen and fields_str[f_pos].isspace():
                    f_pos += 1
                if f_pos < flen and fields_str[f_pos] == "#":
                    f_pos += 1
                    while f_pos < flen and fields_str[f_pos].isspace():
                        f_pos += 1
                    continue
                break
            fields[fk] = re.sub(r"\s+", " ", " ".join(val_parts)).strip()

        venue = fields.get("journal") or fields.get("booktitle") or fields.get("publisher") or ""
        doi = normalize_doi(fields.get("doi", "") or fields.get("url", ""))
        arxiv = extract_arxiv_id(f"{fields.get('eprint','')} {fields.get('url','')} {fields.get('journal','')} {fields.get('note','')}")
        s_line = _offset_to_line(at)
        e_line = _offset_to_line(pos)
        records.append(
            {
                "cite_key": cite_key,
                "source_format": "bibtex",
                "source_location": f"{rel_path}:L{s_line}-L{e_line}",
                "entry_type": _infer_type(etype, venue, fields.get("archiveprefix", "")),
                "title": strip_latex_markup(fields.get("title", "")),
                "authors": split_authors_list(fields.get("author", "")),
                "year": _clean_year(fields.get("year", "") or fields.get("date", "")),
                "venue": strip_latex_markup(venue),
                "volume": fields.get("volume", ""),
                "issue": fields.get("number", "") or fields.get("issue", ""),
                "pages": fields.get("pages", "").replace("--", "-"),
                "doi": doi,
                "arxiv_id": arxiv,
                "pmid": fields.get("pmid", ""),
                "url": fields.get("url", ""),
                "publisher": strip_latex_markup(fields.get("publisher", "")),
                "abstract": strip_latex_markup(fields.get("abstract", "")),
                "keywords": fields.get("keywords", ""),
                "raw_fields": fields,
            }
        )
    return records


def parse_ris(text: str, rel_path: str) -> List[Dict[str, Any]]:
    """Parse RIS tagged bibliography."""
    records: List[Dict[str, Any]] = []
    cur: Dict[str, List[str]] = {}
    start_line = 1
    for lno, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"^([A-Z0-9]{2})\s{1,2}-\s?(.*)$", line.rstrip())
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur = {"TY": [val]}
            start_line = lno
        elif tag == "ER":
            if cur:
                records.append(_ris_dict_to_rec(cur, f"{rel_path}:L{start_line}-L{lno}"))
            cur = {}
        else:
            cur.setdefault(tag, []).append(val)
    if cur:
        records.append(_ris_dict_to_rec(cur, f"{rel_path}:L{start_line}-L{len(text.splitlines())}"))
    return records


def _ris_dict_to_rec(d: Dict[str, List[str]], loc: str) -> Dict[str, Any]:
    authors = d.get("AU") or d.get("A1") or []
    title = (d.get("TI") or d.get("T1") or [""])[0]
    venue = (d.get("JO") or d.get("JF") or d.get("T2") or d.get("JA") or [""])[0]
    year = _clean_year((d.get("PY") or d.get("Y1") or d.get("DA") or [""])[0])
    sp = (d.get("SP") or [""])[0]
    ep = (d.get("EP") or [""])[0]
    pages = f"{sp}-{ep}" if sp and ep else (sp or ep)
    doi = normalize_doi((d.get("DO") or [""])[0] or " ".join(d.get("UR", [])))
    arxiv = extract_arxiv_id(" ".join(d.get("UR", []) + d.get("N1", []) + [venue]))
    return {
        "cite_key": (d.get("ID") or [""])[0],
        "source_format": "ris",
        "source_location": loc,
        "entry_type": _infer_type((d.get("TY") or [""])[0], venue),
        "title": title,
        "authors": [a.strip() for a in authors if a.strip()],
        "year": year,
        "venue": venue,
        "volume": (d.get("VL") or [""])[0],
        "issue": (d.get("IS") or [""])[0],
        "pages": pages,
        "doi": doi,
        "arxiv_id": arxiv,
        "pmid": "",
        "url": (d.get("UR") or [""])[0],
        "publisher": (d.get("PB") or [""])[0],
        "abstract": " ".join(d.get("AB") or d.get("N2") or []),
        "keywords": "; ".join(d.get("KW", [])),
        "raw_fields": d,
    }


def parse_endnote_tagged(text: str, rel_path: str) -> List[Dict[str, Any]]:
    """Parse EndNote Tagged (%0, %A, %T, %J, %D, %V, %N, %P, %R, %X) format."""
    records: List[Dict[str, Any]] = []
    cur: Dict[str, List[str]] = {}
    start_line = 1
    lines = text.splitlines()

    def _flush(end_l: int) -> None:
        if not cur:
            return
        venue = (cur.get("%J") or cur.get("%B") or [""])[0]
        doi = normalize_doi((cur.get("%R") or [""])[0] or " ".join(cur.get("%U", [])))
        records.append(
            {
                "cite_key": (cur.get("%F") or [""])[0],
                "source_format": "endnote_tagged",
                "source_location": f"{rel_path}:L{start_line}-L{end_l}",
                "entry_type": _infer_type((cur.get("%0") or [""])[0], venue),
                "title": (cur.get("%T") or [""])[0],
                "authors": [a.strip() for a in cur.get("%A", []) if a.strip()],
                "year": _clean_year((cur.get("%D") or [""])[0]),
                "venue": venue,
                "volume": (cur.get("%V") or [""])[0],
                "issue": (cur.get("%N") or [""])[0],
                "pages": (cur.get("%P") or [""])[0],
                "doi": doi,
                "arxiv_id": extract_arxiv_id(" ".join(cur.get("%U", []) + cur.get("%Z", []))),
                "pmid": "",
                "url": (cur.get("%U") or [""])[0],
                "publisher": (cur.get("%I") or [""])[0],
                "abstract": " ".join(cur.get("%X", [])),
                "keywords": "; ".join(cur.get("%K", [])),
                "raw_fields": cur,
            }
        )

    for lno, line in enumerate(lines, start=1):
        s = line.strip()
        if not s:
            _flush(lno - 1)
            cur = {}
            continue
        m = re.match(r"^(%[A-Za-z0-9])\s+(.*)$", s)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            if tag == "%0" and cur:
                _flush(lno - 1)
                cur = {}
            if not cur:
                start_line = lno
            cur.setdefault(tag, []).append(val)
    _flush(len(lines))
    return records


def parse_endnote_xml(text: str, rel_path: str) -> List[Dict[str, Any]]:
    """Parse EndNote XML export."""
    records: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(text)
    except Exception:
        return records
    for idx, rec in enumerate(root.findall(".//record"), start=1):
        reftype = rec.findtext(".//ref-type", default="")
        title = "".join(rec.find(".//titles/title").itertext()).strip() if rec.find(".//titles/title") is not None else ""
        venue = "".join(rec.find(".//periodical/full-title").itertext()).strip() if rec.find(".//periodical/full-title") is not None else ""
        if not venue and rec.find(".//titles/secondary-title") is not None:
            venue = "".join(rec.find(".//titles/secondary-title").itertext()).strip()
        authors = ["".join(a.itertext()).strip() for a in rec.findall(".//contributors/authors/author") if "".join(a.itertext()).strip()]
        year = _clean_year(rec.findtext(".//dates/year", default=""))
        doi = normalize_doi(rec.findtext(".//electronic-resource-num", default=""))
        records.append(
            {
                "cite_key": f"enxml_{idx}",
                "source_format": "endnote_xml",
                "source_location": f"{rel_path}:record[{idx}]",
                "entry_type": _infer_type(reftype, venue),
                "title": title,
                "authors": authors,
                "year": year,
                "venue": venue,
                "volume": rec.findtext(".//volume", default="").strip(),
                "issue": rec.findtext(".//number", default="").strip(),
                "pages": rec.findtext(".//pages", default="").strip(),
                "doi": doi,
                "arxiv_id": extract_arxiv_id(rec.findtext(".//urls/related-urls/url", default="")),
                "pmid": "",
                "url": rec.findtext(".//urls/related-urls/url", default="").strip(),
                "publisher": rec.findtext(".//publisher", default="").strip(),
                "abstract": rec.findtext(".//abstract", default="").strip(),
                "keywords": "",
                "raw_fields": {},
            }
        )
    return records


def parse_cnki_formats(text: str, rel_path: str) -> List[Dict[str, Any]]:
    """Parse CNKI RefWorks (RT/A1/T1/JF/YR), NoteExpress ({Reference Type}:), and CNKI custom (Title-/Author-)."""
    records: List[Dict[str, Any]] = []
    # Split into blank-line-delimited blocks or {Reference Type}: blocks
    blocks = re.split(r"\n\s*\n+", text.strip())
    line_cursor = 1
    for blk in blocks:
        blk_lines = blk.splitlines()
        start_l = line_cursor
        end_l = line_cursor + len(blk_lines) - 1
        line_cursor = end_l + 2

        d: Dict[str, List[str]] = {}
        for ln in blk_lines:
            # NoteExpress: {Title}: xxx
            m_ne = re.match(r"^\{([^}]+)\}\s*:\s*(.*)$", ln.strip())
            # RefWorks: T1 xxx
            m_rw = re.match(r"^([A-Z][A-Z0-9])\s+(.*)$", ln.strip())
            # CNKI custom: Title-标题: xxx or Title-题名: xxx
            m_ck = re.match(r"^([A-Za-z]+)-[^:：]*[:：]\s*(.*)$", ln.strip())
            if m_ne:
                d.setdefault(m_ne.group(1).strip().lower(), []).append(m_ne.group(2).strip())
            elif m_ck:
                d.setdefault(m_ck.group(1).strip().lower(), []).append(m_ck.group(2).strip())
            elif m_rw:
                d.setdefault(m_rw.group(1).strip().upper(), []).append(m_rw.group(2).strip())
        if not d:
            continue

        title = (d.get("title") or d.get("T1") or d.get("TI") or [""])[0]
        if not title:
            continue
        raw_auths = d.get("author") or d.get("authors") or d.get("A1") or d.get("AU") or []
        authors: List[str] = []
        for ra in raw_auths:
            authors.extend(split_authors_list(ra))
        venue = (d.get("journal") or d.get("source") or d.get("JF") or d.get("JO") or [""])[0]
        year = _clean_year((d.get("year") or d.get("YR") or d.get("PY") or [""])[0])
        rtype = (d.get("reference type") or d.get("srcdatabase") or d.get("RT") or [""])[0]
        vol = (d.get("volume") or d.get("VO") or d.get("VL") or [""])[0]
        iss = (d.get("issue") or d.get("IS") or [""])[0]
        pages = (d.get("pages") or [""])[0]
        if not pages and d.get("OP"):
            pages = d["OP"][0]
        elif not pages and d.get("SP"):
            sp = d["SP"][0]
            ep = (d.get("EP") or [""])[0]
            pages = f"{sp}-{ep}" if ep else sp
        doi = normalize_doi((d.get("doi") or d.get("DO") or [""])[0] or " ".join(d.get("LK", []) + d.get("url", [])))
        records.append(
            {
                "cite_key": "",
                "source_format": "cnki",
                "source_location": f"{rel_path}:L{start_l}-L{end_l}",
                "entry_type": _infer_type(rtype, venue),
                "title": title,
                "authors": authors,
                "year": year,
                "venue": venue,
                "volume": vol,
                "issue": iss,
                "pages": pages,
                "doi": doi,
                "arxiv_id": extract_arxiv_id(" ".join(d.get("url", []) + d.get("LK", []))),
                "pmid": "",
                "url": (d.get("url") or d.get("LK") or [""])[0],
                "publisher": (d.get("publisher") or d.get("PB") or [""])[0],
                "abstract": " ".join(d.get("abstract") or d.get("summary") or d.get("AB") or []),
                "keywords": "; ".join(d.get("keywords") or d.get("K1") or []),
                "raw_fields": d,
            }
        )
    return records


def parse_json_or_csv(path: Path, rel_path: str) -> List[Dict[str, Any]]:
    """Parse CSL-JSON, generic JSON, CSV, or TSV libraries."""
    records: List[Dict[str, Any]] = []
    ext = path.suffix.lower()
    if ext == ".json":
        data, _ = load_json_safe(path, default=None)
        items = data.get("references", data.get("items", [])) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        for idx, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            auths = it.get("authors") or it.get("author") or []
            if isinstance(auths, str):
                auth_list = split_authors_list(auths)
            elif isinstance(auths, list):
                auth_list = []
                for a in auths:
                    if isinstance(a, str):
                        auth_list.append(a)
                    elif isinstance(a, dict):
                        fam = a.get("family", "")
                        giv = a.get("given", "")
                        auth_list.append(f"{fam}, {giv}".strip(", ") if fam else str(a.get("literal", "")))
            else:
                auth_list = []
            yr = it.get("year") or ""
            if not yr and isinstance(it.get("issued"), dict):
                dp = it["issued"].get("date-parts") or [[]]
                if dp and dp[0]:
                    yr = str(dp[0][0])
            venue = it.get("venue") or it.get("container-title") or it.get("journal") or it.get("booktitle") or ""
            doi = normalize_doi(str(it.get("doi") or it.get("DOI") or it.get("url") or it.get("URL") or ""))
            records.append(
                {
                    "cite_key": str(it.get("id") or it.get("cite_key") or ""),
                    "source_format": "json",
                    "source_location": f"{rel_path}:item[{idx}]",
                    "entry_type": _infer_type(str(it.get("type", "")), str(venue)),
                    "title": str(it.get("title", "")).strip(),
                    "authors": [a for a in auth_list if a],
                    "year": _clean_year(str(yr)),
                    "venue": str(venue).strip(),
                    "volume": str(it.get("volume", "")).strip(),
                    "issue": str(it.get("issue", "")).strip(),
                    "pages": str(it.get("pages") or it.get("page") or "").strip(),
                    "doi": doi,
                    "arxiv_id": extract_arxiv_id(f"{it.get('arxiv_id','')} {it.get('url','')} {venue}"),
                    "pmid": str(it.get("pmid") or it.get("PMID") or "").strip(),
                    "url": str(it.get("url") or it.get("URL") or "").strip(),
                    "publisher": str(it.get("publisher", "")).strip(),
                    "abstract": str(it.get("abstract", "")).strip(),
                    "keywords": str(it.get("keywords", "")).strip(),
                    "raw_fields": it,
                }
            )
    elif ext in (".csv", ".tsv"):
        txt, _, _ = read_text_safe(path)
        if txt:
            delim = "\t" if ext == ".tsv" else ","
            reader = csv.DictReader(txt.splitlines(), delimiter=delim)
            for r_i, row in enumerate(reader, start=2):
                norm = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
                venue = norm.get("journal") or norm.get("venue") or norm.get("booktitle") or ""
                doi = normalize_doi(norm.get("doi", "") or norm.get("url", ""))
                records.append(
                    {
                        "cite_key": norm.get("key") or norm.get("cite_key") or norm.get("id") or "",
                        "source_format": ext.lstrip("."),
                        "source_location": f"{rel_path}:L{r_i}",
                        "entry_type": _infer_type(norm.get("type", ""), venue),
                        "title": norm.get("title", ""),
                        "authors": split_authors_list(norm.get("authors") or norm.get("author") or ""),
                        "year": _clean_year(norm.get("year", "")),
                        "venue": venue,
                        "volume": norm.get("volume", ""),
                        "issue": norm.get("issue") or norm.get("number") or "",
                        "pages": norm.get("pages", ""),
                        "doi": doi,
                        "arxiv_id": extract_arxiv_id(f"{norm.get('arxiv','')} {norm.get('url','')} {venue}"),
                        "pmid": norm.get("pmid", ""),
                        "url": norm.get("url", ""),
                        "publisher": norm.get("publisher", ""),
                        "abstract": norm.get("abstract", ""),
                        "keywords": norm.get("keywords", ""),
                        "raw_fields": norm,
                    }
                )
    return records


def parse_plain_reference_lines(text: str, rel_path: str, start_line_offset: int = 1) -> List[Dict[str, Any]]:
    """Heuristic parser for numbered or bulleted plain-text bibliography entries."""
    records: List[Dict[str, Any]] = []
    for idx, line in enumerate(text.splitlines(), start=start_line_offset):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m_num = re.match(r"^(?:\[(\d+)\]|(\d+)\.)\s+(.+)$", s)
        orig_num = None
        body = s
        if m_num:
            orig_num = int(m_num.group(1) or m_num.group(2))
            body = m_num.group(3).strip()
        elif len(s) < 25:
            continue

        doi = normalize_doi(body)
        arxiv = extract_arxiv_id(body)
        year = _clean_year(body)
        # Extract title heuristically (quoted, or after year/author)
        title = ""
        m_q = re.search(r"[\"“]([^\"”]{8,})[\"”]", body)
        m_gbt = re.search(r"^([^.。]+)\.\s*([^\[.。]{6,})(?:\[[A-Z/]+\])?\.", body)
        m_apa = re.search(r"\(\d{4}[a-z]?\)\.\s*([^.]+)\.", body)
        if m_q:
            title = m_q.group(1).strip().rstrip(",.")
            auth_part = body[: m_q.start()].strip().rstrip(",")
        elif m_apa:
            title = m_apa.group(1).strip()
            auth_part = body[: m_apa.start()].strip()
        elif m_gbt:
            auth_part = m_gbt.group(1).strip()
            title = m_gbt.group(2).strip()
        else:
            parts = [p.strip() for p in re.split(r"[.。]", body) if p.strip()]
            auth_part = parts[0] if len(parts) >= 2 else ""
            title = parts[1] if len(parts) >= 2 else body

        # Extract venue after title
        venue = ""
        m_type = re.search(r"\[([JCMDRSPZ]|EB/OL)\]\.?\s*([^,，.。;；\d]+)", body)
        if m_type:
            venue = m_type.group(2).strip()
        records.append(
            {
                "cite_key": f"ref_{orig_num}" if orig_num is not None else "",
                "orig_list_num": orig_num,
                "source_format": "plain_text_list",
                "source_location": f"{rel_path}:L{idx}",
                "entry_type": _infer_type("", venue, body),
                "title": title,
                "authors": split_authors_list(auth_part),
                "year": year,
                "venue": venue,
                "volume": "",
                "issue": "",
                "pages": "",
                "doi": doi,
                "arxiv_id": arxiv,
                "pmid": "",
                "url": "",
                "publisher": "",
                "abstract": "",
                "keywords": "",
                "raw_text": s,
                "raw_fields": {"raw_line": s},
            }
        )
    return records


def extract_embedded_ref_section(manuscript_text: str, rel_path: str) -> List[Dict[str, Any]]:
    """Extract embedded References / 参考文献 section from manuscript if present."""
    lines = manuscript_text.splitlines()
    ref_start = None
    for i, ln in enumerate(lines):
        if re.match(r"^(?:#{1,4}\s*)?(?:references|bibliography|参考文献)\s*$", ln.strip(), flags=re.IGNORECASE):
            ref_start = i + 1
            break
    if ref_start is None:
        return []
    sub_lines: List[str] = []
    for ln in lines[ref_start:]:
        if re.match(r"^#{1,3}\s+", ln.strip()) or re.match(r"^(?:appendix|附录)\b", ln.strip(), flags=re.IGNORECASE):
            break
        sub_lines.append(ln)
    return parse_plain_reference_lines("\n".join(sub_lines), rel_path, start_line_offset=ref_start + 1)


def parse_single_library_file(path: Path, rel_path: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Dispatch file parsing by extension and content signatures."""
    warnings: List[str] = []
    ext = path.suffix.lower()
    if ext in (".json", ".csv", ".tsv"):
        return parse_json_or_csv(path, rel_path), warnings

    txt, _, warn = read_text_safe(path)
    if warn:
        warnings.append(warn)
    if not txt:
        return [], warnings

    if ext == ".bib" or ("@article" in txt.lower() or "@inproceedings" in txt.lower() or "@book" in txt.lower()):
        recs = parse_bibtex(txt, rel_path)
        if recs:
            return recs, warnings
    if ext == ".xml" or "<records>" in txt.lower():
        recs = parse_endnote_xml(txt, rel_path)
        if recs:
            return recs, warnings
    if ext == ".ris" or re.search(r"^TY\s{1,2}-", txt, flags=re.MULTILINE):
        recs = parse_ris(txt, rel_path)
        if recs:
            return recs, warnings
    if ext == ".enw" or re.search(r"^%0\s+", txt, flags=re.MULTILINE):
        recs = parse_endnote_tagged(txt, rel_path)
        if recs:
            return recs, warnings
    if any(sig in txt for sig in ("{Reference Type}:", "SrcDatabase-", "T1 ", "RT ")):
        recs = parse_cnki_formats(txt, rel_path)
        if recs:
            return recs, warnings
    return parse_plain_reference_lines(txt, rel_path), warnings


def choose_primary_record(group: List[Dict[str, Any]], precedence: List[str]) -> Dict[str, Any]:
    """Merge a duplicate group keeping formal published metadata primary with field provenance."""
    prec_rank = {t: i for i, t in enumerate(precedence)}

    def _score(r: Dict[str, Any]) -> Tuple[int, int]:
        rank = prec_rank.get(r.get("entry_type", "misc"), 99)
        completeness = sum(
            1 for k in ("title", "authors", "year", "venue", "volume", "pages", "doi", "abstract") if r.get(k)
        )
        return (rank, -completeness)

    ordered = sorted(group, key=_score)
    primary = dict(ordered[0])
    prov: Dict[str, str] = {}
    scalar_fields = ("title", "year", "venue", "volume", "issue", "pages", "doi", "arxiv_id", "pmid", "url", "publisher", "abstract", "keywords")
    for f in scalar_fields:
        if primary.get(f):
            prov[f] = primary["raw_id"]
        else:
            for other in ordered[1:]:
                if other.get(f):
                    primary[f] = other[f]
                    prov[f] = other["raw_id"]
                    break
    if not primary.get("authors"):
        for other in ordered[1:]:
            if other.get("authors"):
                primary["authors"] = other["authors"]
                prov["authors"] = other["raw_id"]
                break
    else:
        prov["authors"] = primary["raw_id"]

    primary["merged_from"] = [r["raw_id"] for r in ordered]
    primary["all_cite_keys"] = sorted({r["cite_key"] for r in ordered if r.get("cite_key")})
    primary["all_orig_nums"] = sorted({r["orig_list_num"] for r in ordered if r.get("orig_list_num") is not None})
    primary["field_provenance"] = prov
    return primary


def deduplicate_records(
    raw_records: List[Dict[str, Any]],
    rules: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run 4-tier explainable deduplication with anti-false-merge guard."""
    precedence = rules.get("source_type_precedence", ["journal_article", "conference_paper", "preprint", "misc"])
    n = len(raw_records)
    parent = list(range(n))
    merge_reasons: List[Dict[str, Any]] = []
    preserved_distinct: List[Dict[str, Any]] = []
    ambiguous_pairs: List[Dict[str, Any]] = []

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            ri, rj = raw_records[i], raw_records[j]
            # Tier 1: Persistent IDs
            if ri["doi"] and rj["doi"] and ri["doi"] == rj["doi"]:
                union(i, j)
                merge_reasons.append({"pair": [ri["raw_id"], rj["raw_id"]], "tier": "tier_1_exact_doi", "reason": f"Exact normalized DOI match: {ri['doi']}"})
                continue
            if ri["arxiv_id"] and rj["arxiv_id"] and ri["arxiv_id"] == rj["arxiv_id"]:
                union(i, j)
                merge_reasons.append({"pair": [ri["raw_id"], rj["raw_id"]], "tier": "tier_1_exact_arxiv", "reason": f"Exact arXiv ID match: {ri['arxiv_id']}"})
                continue
            if ri["pmid"] and rj["pmid"] and ri["pmid"] == rj["pmid"]:
                union(i, j)
                merge_reasons.append({"pair": [ri["raw_id"], rj["raw_id"]], "tier": "tier_1_exact_pmid", "reason": f"Exact PMID match: {ri['pmid']}"})
                continue

            sim = title_similarity(ri["title"], rj["title"])
            fa_i = first_author_surname(ri["authors"])
            fa_j = first_author_surname(rj["authors"])
            same_author = bool(fa_i and fa_j and (fa_i == fa_j or fa_i in fa_j or fa_j in fa_i))
            yr_i = int(ri["year"]) if ri["year"].isdigit() else None
            yr_j = int(rj["year"]) if rj["year"].isdigit() else None
            same_or_near_yr = (yr_i is not None and yr_j is not None and abs(yr_i - yr_j) <= 1)

            # Anti-false-merge guard
            if same_author and yr_i == yr_j and yr_i is not None and sim < 0.75:
                preserved_distinct.append(
                    {
                        "pair": [ri["raw_id"], rj["raw_id"]],
                        "reason": f"Anti-false-merge guard: same first author '{fa_i}' & year {yr_i}, but title similarity {sim} < 0.75.",
                    }
                )
                continue

            # Tier 2: High title similarity + author/year
            if sim >= 0.90 and (same_author or same_or_near_yr or (not fa_i or not fa_j)):
                union(i, j)
                merge_reasons.append(
                    {
                        "pair": [ri["raw_id"], rj["raw_id"]],
                        "tier": "tier_2_title_author_year",
                        "reason": f"Normalized title similarity {sim} >= 0.90 (same_author={same_author}, near_year={same_or_near_yr}).",
                    }
                )
                continue

            # Tier 3: Preprint vs formal edition or bilingual overlap
            is_preprint_pair = (ri["entry_type"] == "preprint") != (rj["entry_type"] == "preprint")
            if is_preprint_pair and sim >= 0.80 and same_author:
                union(i, j)
                merge_reasons.append(
                    {
                        "pair": [ri["raw_id"], rj["raw_id"]],
                        "tier": "tier_3_preprint_published",
                        "reason": f"Preprint vs formal record with same first author '{fa_i}' and title similarity {sim} >= 0.80.",
                    }
                )
                continue

            # Tier 4: Ambiguous candidate band
            if 0.75 <= sim < 0.90:
                ambiguous_pairs.append(
                    {
                        "pair": [ri["raw_id"], rj["raw_id"]],
                        "title_similarity": sim,
                        "titles": [ri["title"], rj["title"]],
                        "reason": f"Title similarity {sim} in ambiguous band [0.75, 0.90) without shared persistent ID.",
                    }
                )

    groups: Dict[int, List[Dict[str, Any]]] = {}
    for idx, rec in enumerate(raw_records):
        groups.setdefault(find(idx), []).append(rec)

    canonical: List[Dict[str, Any]] = []
    merge_groups: List[Dict[str, Any]] = []
    for g_idx, grp in enumerate(groups.values(), start=1):
        merged = choose_primary_record(grp, precedence)
        ref_id = f"REF-{g_idx:03d}"
        merged["ref_id"] = ref_id
        merged["doi_syntax_valid"] = validate_doi_syntax(merged["doi"]) if merged["doi"] else None
        missing_fields = [f for f in ("authors", "title", "year", "venue") if not merged.get(f)]
        if merged["entry_type"] == "journal_article":
            for jf in ("volume", "pages", "doi"):
                if not merged.get(jf):
                    missing_fields.append(jf)
        merged["missing_fields"] = missing_fields
        merged["has_abstract"] = bool(merged.get("abstract"))
        canonical.append(merged)
        if len(grp) > 1:
            rel_reasons = [m for m in merge_reasons if set(m["pair"]) <= set(merged["merged_from"])]
            merge_groups.append(
                {
                    "canonical_ref_id": ref_id,
                    "merged_raw_ids": merged["merged_from"],
                    "retained_title": merged["title"],
                    "retained_type": merged["entry_type"],
                    "evidence": rel_reasons,
                }
            )

    dedup_report = {
        "raw_count": len(raw_records),
        "canonical_count": len(canonical),
        "duplicates_merged": len(raw_records) - len(canonical),
        "merge_groups": merge_groups,
        "preserved_distinct_pairs": preserved_distinct,
        "ambiguous_candidates": ambiguous_pairs,
    }
    return canonical, dedup_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse and deduplicate multi-format reference libraries.")
    parser.add_argument("--case-plan", required=True)
    parser.add_argument("--out-dir", default="/app/work")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    case_plan, err = load_json_safe(Path(args.case_plan), default={})
    if err or not isinstance(case_plan, dict):
        raise SystemExit(f"Cannot load case_plan: {err}")

    rules = load_resource_json("dedup_and_matching_rules.json")
    raw_records: List[Dict[str, Any]] = []
    warnings: List[str] = list(case_plan.get("warnings", []))

    for lf in case_plan.get("library_files", []):
        recs, w = parse_single_library_file(Path(lf["path"]), lf["rel_path"])
        warnings.extend(w)
        raw_records.extend(recs)

    # Also extract embedded reference sections from manuscripts
    for mf in case_plan.get("manuscript_files", []):
        m_txt, _, w = read_document_any(Path(mf["path"]))
        if w:
            warnings.append(w)
        if m_txt:
            emb = extract_embedded_ref_section(m_txt, mf["rel_path"])
            for er in emb:
                er["from_embedded_manuscript"] = True
            raw_records.extend(emb)

    if case_plan.get("inline_library"):
        tmp_p = out_dir / "_inline_lib_tmp.json"
        save_json(tmp_p, case_plan["inline_library"])
        raw_records.extend(parse_json_or_csv(tmp_p, "case.json:inline_library"))
        tmp_p.unlink(missing_ok=True)

    for idx, rec in enumerate(raw_records, start=1):
        rec["raw_id"] = f"RAW-{idx:03d}"

    canonical, dedup_report = deduplicate_records(raw_records, rules)

    save_json(out_dir / "library_snapshot.json", {"raw_records": raw_records, "warnings": warnings})
    save_json(out_dir / "canonical_library.json", {"entries": canonical})
    save_json(out_dir / "dedup_report.json", dedup_report)


if __name__ == "__main__":
    main()
