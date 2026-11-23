#!/usr/bin/env python3
"""Step 1: Ingest scientific sources, isolate per-paper metadata, deduplicate multi-format copies,
index paragraphs/sentences/tables/figures with authentic locators, and generate Agent-ready digests.

Pure Python 3.10+ standard library for JSON, XML/JATS, Markdown, TXT, HTML, and CSV/TSV.
Optional PDF/OCR backends (pymupdf, pdfplumber, pypdf, pdftotext, tesseract) are loaded lazily
only when PDF files exist.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    load_config,
    load_json,
    norm_doi,
    norm_key,
    norm_nfkc,
    norm_ws,
    save_json,
    set_jaccard,
    sha256_file,
    split_sentences,
    text_shingles,
)


CONTROL_FILES = {
    "case.json",
    "task.md",
    "corpus-manifest.json",
    "manifest.json",
    "config.json",
    "resources.yaml",
    "resources.yml",
    "rubric.md",
    "challenge.json",
}
DOC_EXTS = {".pdf", ".json", ".xml", ".nxml", ".md", ".markdown", ".txt", ".html", ".htm", ".csv", ".tsv", ".tex"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp", ".gif"}


def local_tag(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def extract_output_contract_fields(case_data: Dict[str, Any]) -> Tuple[Any, List[str]]:
    contract = case_data.get("output_contract")
    fields: List[str] = []
    if isinstance(contract, list):
        for item in contract:
            if isinstance(item, str) and item not in fields:
                fields.append(item)
            elif isinstance(item, dict) and item.get("name"):
                nm = str(item["name"])
                if nm not in fields:
                    fields.append(nm)
    elif isinstance(contract, dict):
        if isinstance(contract.get("required"), list):
            for x in contract["required"]:
                if str(x) not in fields:
                    fields.append(str(x))
        if isinstance(contract.get("properties"), dict):
            for k in contract["properties"]:
                if str(k) not in fields:
                    fields.append(str(k))
        if isinstance(contract.get("fields"), dict):
            for k in contract["fields"]:
                if str(k) not in fields:
                    fields.append(str(k))
        elif isinstance(contract.get("fields"), list):
            for item in contract["fields"]:
                if isinstance(item, str) and item not in fields:
                    fields.append(item)
                elif isinstance(item, dict) and item.get("name") and str(item["name"]) not in fields:
                    fields.append(str(item["name"]))
        if not fields:
            meta_keys = {"type", "description", "format", "schema_version", "notes", "title"}
            fields = [str(k) for k in contract.keys() if str(k) not in meta_keys]
    return contract, fields


def extract_questions_and_claims(case_data: Dict[str, Any], task_text: str) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    questions: List[Dict[str, str]] = []
    claims: List[Dict[str, str]] = []
    seen_q: set = set()
    seen_c: set = set()

    def add_q(qid: str, text: str, src: str) -> None:
        t = norm_ws(text)
        if t and t not in seen_q:
            seen_q.add(t)
            questions.append({"id": qid, "question": t, "source": src})

    def add_c(cid: str, text: str, src: str) -> None:
        t = norm_ws(text)
        if t and t not in seen_c:
            seen_c.add(t)
            claims.append({"id": cid, "claim": t, "source": src})

    for key in ("questions", "queries", "subquestions", "evaluation_queries", "required_queries"):
        val = case_data.get(key)
        if isinstance(val, list):
            for idx, item in enumerate(val, 1):
                if isinstance(item, str):
                    add_q(f"Q{len(questions)+1}", item, f"case.json:{key}")
                elif isinstance(item, dict):
                    qtxt = item.get("question") or item.get("query") or item.get("text") or item.get("prompt")
                    qid = str(item.get("id") or f"Q{len(questions)+1}")
                    if qtxt:
                        add_q(qid, str(qtxt), f"case.json:{key}")
        elif isinstance(val, dict):
            for k, v in val.items():
                add_q(str(k), str(v), f"case.json:{key}")

    for key in ("claims", "claims_to_verify", "candidate_claims", "assertions_to_check"):
        val = case_data.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    add_c(f"C{len(claims)+1}", item, f"case.json:{key}")
                elif isinstance(item, dict):
                    ctxt = item.get("claim") or item.get("assertion") or item.get("text") or item.get("statement")
                    cid = str(item.get("id") or f"C{len(claims)+1}")
                    if ctxt:
                        add_c(cid, str(ctxt), f"case.json:{key}")

    for line in (task_text or "").splitlines():
        s = line.strip()
        m = re.match(r"^(?:[-*]\s*)?(Q\d+|问题\s*\d+|查询\s*\d+)[:：.\s]+(.+)$", s, re.IGNORECASE)
        if m:
            add_q(norm_ws(m.group(1)).replace(" ", ""), m.group(2), "task.md")
        mc = re.match(r"^(?:[-*]\s*)?(?:Claim|Assertion|断言)\s*(\d+)[:：.\s]+(.+)$", s, re.IGNORECASE)
        if mc:
            add_c(f"C{mc.group(1)}", mc.group(2), "task.md")

    return questions, claims


def _normalize_authors(val: Any) -> List[str]:
    if not val:
        return []
    if isinstance(val, str):
        parts = [norm_ws(x) for x in re.split(r"[;；]|,(?=\s*[A-Z\u4e00-\u9fff])", val) if norm_ws(x)]
        return parts or ([norm_ws(val)] if norm_ws(val) else [])
    if isinstance(val, list):
        out: List[str] = []
        for item in val:
            if isinstance(item, str) and norm_ws(item):
                out.append(norm_ws(item))
            elif isinstance(item, dict):
                name = item.get("name") or item.get("full_name") or " ".join(
                    x for x in [item.get("given") or item.get("first_name"), item.get("surname") or item.get("last_name")] if x
                )
                if name and norm_ws(str(name)):
                    out.append(norm_ws(str(name)))
        return out
    return []


def _parse_year(val: Any) -> Optional[int]:
    if val is None:
        return None
    m = re.search(r"\b(19\d{2}|20\d{2})\b", str(val))
    return int(m.group(1)) if m else None


def _parse_manifest(manifest_path: Path) -> Dict[str, Dict[str, Any]]:
    """Map filename/path -> manifest metadata entry if corpus-manifest.json exists."""
    by_rel: Dict[str, Dict[str, Any]] = {}
    try:
        data = load_json(manifest_path)
    except Exception:
        return by_rel
    entries: List[Dict[str, Any]] = []
    if isinstance(data, list):
        entries = [x for x in data if isinstance(x, dict)]
    elif isinstance(data, dict):
        for k in ("documents", "papers", "sources", "corpus", "materials", "files", "items", "entries"):
            if isinstance(data.get(k), list):
                entries = [x for x in data[k] if isinstance(x, dict)]
                break
        if not entries and any(k in data for k in ("primary", "secondary")):
            for role, val in data.items():
                if isinstance(val, dict):
                    val_copy = dict(val)
                    val_copy.setdefault("role", role)
                    entries.append(val_copy)
    for e in entries:
        fp = e.get("file") or e.get("path") or e.get("filename") or e.get("source_file")
        if fp:
            by_rel[Path(str(fp)).name] = e
            by_rel[str(fp).replace("\\", "/")] = e
    return by_rel


def _xml_text_reconcile(elem: ET.Element, notes: List[str]) -> str:
    """Linearize XML element cleanly: preserve sub/sup/tex-math and deduplicate <alternatives>."""
    tag = local_tag(elem.tag)
    if tag == "alternatives":
        # Prefer tex-math if present, else first non-empty child
        tex = None
        for ch in elem:
            if local_tag(ch.tag) == "tex-math":
                tex = norm_ws("".join(ch.itertext()))
                break
        if tex:
            return f" ${tex}$ "
        for ch in elem:
            t = _xml_text_reconcile(ch, notes)
            if t.strip():
                return t
        return ""
    if tag == "tex-math":
        tex = norm_ws("".join(elem.itertext()))
        return f" ${tex}$ " if tex else ""
    if tag == "sub":
        inner = "".join([elem.text or ""] + [_xml_text_reconcile(c, notes) + (c.tail or "") for c in elem])
        return f"_{{{norm_ws(inner)}}}"
    if tag == "sup":
        inner = "".join([elem.text or ""] + [_xml_text_reconcile(c, notes) + (c.tail or "") for c in elem])
        return f"^{{{norm_ws(inner)}}}"
    if tag in {"xref"}:
        inner = norm_ws("".join(elem.itertext()))
        return inner

    parts: List[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(_xml_text_reconcile(child, notes))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _xml_table_to_markdown(tw: ET.Element, notes: List[str]) -> str:
    rows: List[List[str]] = []
    for tr in tw.iter():
        if local_tag(tr.tag) == "tr":
            cells: List[str] = []
            for cell in tr:
                if local_tag(cell.tag) in {"th", "td"}:
                    cells.append(norm_ws(_xml_text_reconcile(cell, notes)).replace("|", "\\|"))
            if cells:
                rows.append(cells)
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    norm_rows = [r + [""] * (ncols - len(r)) for r in rows]
    lines = ["| " + " | ".join(norm_rows[0]) + " |", "| " + " | ".join(["---"] * ncols) + " |"]
    for r in norm_rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def parse_xml_document(path: Path, source_id: str) -> Dict[str, Any]:
    notes: List[str] = []
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8", errors="replace"))
    except ET.ParseError:
        # Handle publisher XML fragments with undeclared namespace prefixes (e.g. mml:math)
        raw_xml = path.read_text(encoding="utf-8", errors="replace")
        raw_xml = re.sub(r"<(/?)[A-Za-z_][\w-]*:([A-Za-z_][\w-]*)", r"<\1\2", raw_xml)
        root = ET.fromstring(raw_xml)
    # Extract metadata from article-meta / front / teiHeader
    meta: Dict[str, Any] = {
        "title": None,
        "authors": [],
        "year": None,
        "venue": None,
        "version": None,
        "doi": None,
        "pmid": None,
        "arxiv_id": None,
        "url": None,
    }
    for elem in root.iter():
        tg = local_tag(elem.tag)
        if tg in {"article-title", "title"} and not meta["title"]:
            t = norm_ws("".join(elem.itertext()))
            if t and len(t) > 5:
                meta["title"] = t
        elif tg == "article-id":
            id_type = (elem.attrib.get("pub-id-type") or "").lower()
            val = norm_ws("".join(elem.itertext()))
            if id_type == "doi" and val and not meta["doi"]:
                meta["doi"] = norm_doi(val)
            elif id_type == "pmid" and val and not meta["pmid"]:
                meta["pmid"] = val
            elif id_type == "arxiv" and val and not meta["arxiv_id"]:
                meta["arxiv_id"] = val
        elif tg in {"journal-title", "abbrev-journal-title"} and not meta["venue"]:
            val = norm_ws("".join(elem.itertext()))
            if val:
                meta["venue"] = val
        elif tg == "pub-date" and not meta["year"]:
            for ch in elem:
                if local_tag(ch.tag) == "year":
                    y = _parse_year("".join(ch.itertext()))
                    if y:
                        meta["year"] = y
                        break
        elif tg == "contrib" and elem.attrib.get("contrib-type", "author") == "author":
            surname = ""
            given = ""
            for ch in elem.iter():
                if local_tag(ch.tag) == "surname":
                    surname = norm_ws("".join(ch.itertext()))
                elif local_tag(ch.tag) in {"given-names", "given"}:
                    given = norm_ws("".join(ch.itertext()))
            full = norm_ws(f"{given} {surname}")
            if full:
                meta["authors"].append(full)

    paragraphs: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    figures: List[Dict[str, Any]] = []
    p_counter = 0

    def walk_sec(node: ET.Element, sec_title: str) -> None:
        nonlocal p_counter
        cur_sec = sec_title
        for ch in node:
            tg = local_tag(ch.tag)
            if tg == "title" and local_tag(node.tag) in {"sec", "abstract"}:
                t = norm_ws("".join(ch.itertext()))
                if t:
                    cur_sec = t
            elif tg == "abstract":
                walk_sec(ch, "Abstract")
            elif tg == "sec":
                sub_title = cur_sec
                for sub in ch:
                    if local_tag(sub.tag) == "title":
                        st = norm_ws("".join(sub.itertext()))
                        if st:
                            sub_title = st
                        break
                walk_sec(ch, sub_title)
            elif tg == "p":
                p_counter += 1
                pid = ch.attrib.get("id") or ch.attrib.get("{http://www.w3.org/XML/1998/namespace}id") or f"P{p_counter:03d}"
                txt = norm_ws(_xml_text_reconcile(ch, notes))
                if txt:
                    paragraphs.append({
                        "paragraph_id": str(pid),
                        "section": cur_sec or "Body",
                        "page": None,
                        "printed_page": None,
                        "line_start": None,
                        "line_end": None,
                        "locator_type": "jats_paragraph_id",
                        "text": txt,
                    })
            elif tg == "table-wrap":
                tid = ch.attrib.get("id") or f"Table_{len(tables)+1}"
                cap = ""
                for sub in ch:
                    if local_tag(sub.tag) == "caption":
                        cap = norm_ws(_xml_text_reconcile(sub, notes))
                md = _xml_table_to_markdown(ch, notes)
                tables.append({
                    "table_id": str(tid),
                    "section": cur_sec or "Body",
                    "page": None,
                    "caption": cap,
                    "markdown": md,
                })
                if cap or md:
                    p_counter += 1
                    paragraphs.append({
                        "paragraph_id": f"{tid}_block",
                        "section": cur_sec or "Tables",
                        "page": None,
                        "printed_page": None,
                        "line_start": None,
                        "line_end": None,
                        "locator_type": "table_cell",
                        "table_id": str(tid),
                        "text": norm_ws(f"{cap} {md}".strip()),
                    })
            elif tg == "fig":
                fid = ch.attrib.get("id") or f"Fig_{len(figures)+1}"
                cap = ""
                for sub in ch:
                    if local_tag(sub.tag) == "caption":
                        cap = norm_ws(_xml_text_reconcile(sub, notes))
                figures.append({
                    "figure_id": str(fid),
                    "section": cur_sec or "Figures",
                    "page": None,
                    "caption": cap,
                })
                if cap:
                    p_counter += 1
                    paragraphs.append({
                        "paragraph_id": f"{fid}_caption",
                        "section": cur_sec or "Figures",
                        "page": None,
                        "printed_page": None,
                        "line_start": None,
                        "line_end": None,
                        "locator_type": "figure_caption",
                        "figure_id": str(fid),
                        "text": cap,
                    })
            else:
                walk_sec(ch, cur_sec)

    walk_sec(root, "Document")
    return {
        "has_pdf_pages": False,
        "page_count": None,
        "metadata": meta,
        "paragraphs": paragraphs,
        "tables": tables,
        "figures": figures,
        "unreadable_pages": [],
        "extraction_backend": "xml.etree.ElementTree",
        "notes": notes,
    }


def _extract_meta_from_json_dict(obj: Dict[str, Any]) -> Dict[str, Any]:
    m = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else obj
    return {
        "title": m.get("title") or m.get("paper_title") or m.get("article_title"),
        "authors": _normalize_authors(m.get("authors") or m.get("author") or m.get("contributors")),
        "year": _parse_year(m.get("year") or m.get("pub_year") or m.get("published_year") or m.get("date")),
        "venue": m.get("venue") or m.get("journal") or m.get("conference") or m.get("publisher"),
        "version": m.get("version") or m.get("edition") or m.get("preprint_version"),
        "doi": norm_doi(m.get("doi")) or None,
        "pmid": str(m["pmid"]) if m.get("pmid") else None,
        "arxiv_id": str(m["arxiv_id"]) if m.get("arxiv_id") else None,
        "url": m.get("url"),
    }


def parse_json_material(path: Path) -> Dict[str, Any]:
    data = load_json(path)
    meta: Dict[str, Any] = {
        "title": None, "authors": [], "year": None, "venue": None, "version": None,
        "doi": None, "pmid": None, "arxiv_id": None, "url": None,
    }
    paragraphs: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    figures: List[Dict[str, Any]] = []
    has_pdf_pages = False

    if isinstance(data, dict):
        meta = _extract_meta_from_json_dict(data)
        if data.get("abstract") and isinstance(data["abstract"], str):
            paragraphs.append({
                "paragraph_id": "abstract_1",
                "section": "Abstract",
                "page": None,
                "printed_page": None,
                "line_start": None,
                "line_end": None,
                "locator_type": "section_paragraph",
                "text": norm_ws(data["abstract"]),
            })

        def collect_para_items(items: List[Any], default_sec: str = "Body") -> None:
            for idx, item in enumerate(items, 1):
                if isinstance(item, str) and norm_ws(item):
                    paragraphs.append({
                        "paragraph_id": f"P{len(paragraphs)+1:03d}",
                        "section": default_sec,
                        "page": None,
                        "printed_page": None,
                        "line_start": None,
                        "line_end": None,
                        "locator_type": "section_paragraph",
                        "text": norm_ws(item),
                    })
                elif isinstance(item, dict):
                    txt = item.get("text") or item.get("content") or item.get("body") or item.get("paragraph")
                    if isinstance(txt, list):
                        txt = " ".join(str(x) for x in txt)
                    if not txt or not norm_ws(str(txt)):
                        continue
                    pid = item.get("paragraph_id") or item.get("id") or item.get("pid") or f"P{len(paragraphs)+1:03d}"
                    sec = item.get("section") or item.get("sec") or item.get("heading") or default_sec
                    pg = item.get("page")
                    if isinstance(pg, int) and pg >= 1:
                        nonlocal has_pdf_pages
                        has_pdf_pages = True
                        ltype = "pdf_page"
                    else:
                        pg = None
                        ltype = "jats_paragraph_id" if re.match(r"^(?:p|para|sec|abs)", str(pid), re.I) else "section_paragraph"
                    paragraphs.append({
                        "paragraph_id": str(pid),
                        "section": str(sec),
                        "page": pg,
                        "printed_page": item.get("printed_page"),
                        "line_start": item.get("line_start"),
                        "line_end": item.get("line_end"),
                        "locator_type": ltype,
                        "text": norm_ws(str(txt)),
                    })

        for key in ("paragraphs", "chunks", "blocks", "segments", "body_text"):
            if isinstance(data.get(key), list):
                collect_para_items(data[key])
        if isinstance(data.get("sections"), list):
            for sec_item in data["sections"]:
                if isinstance(sec_item, dict):
                    sname = str(sec_item.get("title") or sec_item.get("heading") or sec_item.get("section") or "Section")
                    if isinstance(sec_item.get("paragraphs"), list):
                        collect_para_items(sec_item["paragraphs"], sname)
                    elif isinstance(sec_item.get("text"), str):
                        collect_para_items([{"paragraph_id": sec_item.get("id"), "section": sname, "text": sec_item["text"]}], sname)
        elif isinstance(data.get("sections"), dict):
            for sname, sval in data["sections"].items():
                if isinstance(sval, list):
                    collect_para_items(sval, str(sname))
                elif isinstance(sval, str):
                    collect_para_items([{"section": str(sname), "text": sval}], str(sname))

        if not paragraphs and isinstance(data.get("text"), str):
            for idx, blk in enumerate(re.split(r"\n\s*\n", data["text"]), 1):
                if norm_ws(blk):
                    paragraphs.append({
                        "paragraph_id": f"P{idx:03d}",
                        "section": "Body",
                        "page": None,
                        "printed_page": None,
                        "line_start": None,
                        "line_end": None,
                        "locator_type": "section_paragraph",
                        "text": norm_ws(blk),
                    })
        for t in (data.get("tables") or []):
            if isinstance(t, dict):
                tid = str(t.get("id") or t.get("table_id") or f"Table_{len(tables)+1}")
                cap = norm_ws(t.get("caption") or "")
                md = str(t.get("markdown") or t.get("text") or "")
                tables.append({"table_id": tid, "section": t.get("section") or "Tables", "page": t.get("page"), "caption": cap, "markdown": md})
                if cap or md:
                    paragraphs.append({
                        "paragraph_id": f"{tid}_block",
                        "section": t.get("section") or "Tables",
                        "page": t.get("page"),
                        "printed_page": None,
                        "line_start": None,
                        "line_end": None,
                        "locator_type": "table_cell",
                        "table_id": tid,
                        "text": norm_ws(f"{cap} {md}".strip()),
                    })
        for fig in (data.get("figures") or []):
            if isinstance(fig, dict):
                fid = str(fig.get("id") or fig.get("figure_id") or f"Fig_{len(figures)+1}")
                cap = norm_ws(fig.get("caption") or fig.get("text") or "")
                figures.append({"figure_id": fid, "section": fig.get("section") or "Figures", "page": fig.get("page"), "caption": cap})
                if cap:
                    paragraphs.append({
                        "paragraph_id": f"{fid}_caption",
                        "section": fig.get("section") or "Figures",
                        "page": fig.get("page"),
                        "printed_page": None,
                        "line_start": None,
                        "line_end": None,
                        "locator_type": "figure_caption",
                        "figure_id": fid,
                        "text": cap,
                    })
    elif isinstance(data, list):
        for idx, item in enumerate(data, 1):
            if isinstance(item, dict):
                txt = item.get("text") or item.get("content") or ""
                if norm_ws(str(txt)):
                    paragraphs.append({
                        "paragraph_id": str(item.get("paragraph_id") or item.get("id") or f"P{idx:03d}"),
                        "section": str(item.get("section") or "Body"),
                        "page": item.get("page") if isinstance(item.get("page"), int) else None,
                        "printed_page": None,
                        "line_start": None,
                        "line_end": None,
                        "locator_type": "pdf_page" if isinstance(item.get("page"), int) else "section_paragraph",
                        "text": norm_ws(str(txt)),
                    })
                    if isinstance(item.get("page"), int):
                        has_pdf_pages = True

    return {
        "has_pdf_pages": has_pdf_pages,
        "page_count": None,
        "metadata": meta,
        "paragraphs": paragraphs,
        "tables": tables,
        "figures": figures,
        "unreadable_pages": [],
        "extraction_backend": "json",
        "notes": [],
    }


def _extract_plain_metadata(text: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "title": None, "authors": [], "year": None, "venue": None, "version": None,
        "doi": None, "pmid": None, "arxiv_id": None, "url": None,
    }
    head = "\n".join(text.splitlines()[:40])
    # Title from first H1 or Title: line
    m_title = re.search(r"^(?:#\s+|Title:\s*)(.+)$", head, re.MULTILINE | re.IGNORECASE)
    if m_title:
        meta["title"] = norm_ws(m_title.group(1))
    m_auth = re.search(r"^(?:Authors?|By):\s*(.+)$", head, re.MULTILINE | re.IGNORECASE)
    if m_auth:
        meta["authors"] = _normalize_authors(m_auth.group(1))
    m_doi = re.search(r"(?:doi[:\s]+|https?://(?:dx\.)?doi\.org/)(10\.\d{4,9}/[^\s,;)\]]+)", head, re.IGNORECASE)
    if m_doi:
        meta["doi"] = norm_doi(m_doi.group(1))
    m_arxiv = re.search(r"\barXiv[:\s]*(\d{4}\.\d{4,5}(?:v\d+)?)\b", head, re.IGNORECASE)
    if m_arxiv:
        meta["arxiv_id"] = m_arxiv.group(1)
    m_pmid = re.search(r"\bPMID[:\s]*(\d{6,10})\b", head, re.IGNORECASE)
    if m_pmid:
        meta["pmid"] = m_pmid.group(1)
    m_ver = re.search(r"\bVersion[:\s]*(v?\d+(?:\.\d+)*)\b", head, re.IGNORECASE)
    if m_ver:
        meta["version"] = m_ver.group(1)
    m_venue = re.search(r"^(?:Journal|Venue|Published in):\s*(.+?)(?:\s*\(\d{4}\))?$", head, re.MULTILINE | re.IGNORECASE)
    if m_venue:
        meta["venue"] = norm_ws(m_venue.group(1))
    m_year = re.search(r"^(?:Year|Date|Published):\s*.*?((?:19|20)\d{2})", head, re.MULTILINE | re.IGNORECASE)
    if m_year:
        meta["year"] = int(m_year.group(1))
    else:
        y2 = _parse_year(head)
        if y2:
            meta["year"] = y2
    return meta


def parse_text_or_markdown(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in {".html", ".htm"}:
        raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
        raw = re.sub(r"</(p|div|h1|h2|h3|h4|li|tr)>", "\n\n", raw, flags=re.I)
        raw = re.sub(r"<[^>]+>", " ", raw)
        raw = html.unescape(raw)
    elif path.suffix.lower() in {".csv", ".tsv"}:
        delim = "\t" if path.suffix.lower() == ".tsv" else ","
        reader = list(csv.reader(io.StringIO(raw), delimiter=delim))
        if reader:
            ncols = max(len(r) for r in reader)
            rows = [r + [""] * (ncols - len(r)) for r in reader]
            md = "\n".join([
                "| " + " | ".join(rows[0]) + " |",
                "| " + " | ".join(["---"] * ncols) + " |",
                *["| " + " | ".join(r) + " |" for r in rows[1:]],
            ])
            return {
                "has_pdf_pages": False,
                "page_count": None,
                "metadata": _extract_plain_metadata(""),
                "paragraphs": [{
                    "paragraph_id": "T001_block",
                    "section": "TableData",
                    "page": None,
                    "printed_page": None,
                    "line_start": 1,
                    "line_end": len(rows),
                    "locator_type": "table_cell",
                    "table_id": path.stem,
                    "text": md,
                }],
                "tables": [{"table_id": path.stem, "section": "TableData", "page": None, "caption": path.name, "markdown": md}],
                "figures": [],
                "unreadable_pages": [],
                "extraction_backend": "csv",
                "notes": [],
            }

    meta = _extract_plain_metadata(raw)
    lines = raw.splitlines()
    paragraphs: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    figures: List[Dict[str, Any]] = []
    cur_sec = "Header"
    buf: List[str] = []
    buf_start = 1

    def flush_buf(end_line: int) -> None:
        if not buf:
            return
        block = "\n".join(buf).strip()
        buf.clear()
        if not block:
            return
        # Detect Markdown pipe table
        blk_lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(blk_lines) >= 2 and all(ln.startswith("|") for ln in blk_lines[:2]):
            tid = f"Table_{len(tables)+1}"
            tables.append({"table_id": tid, "section": cur_sec, "page": None, "caption": "", "markdown": "\n".join(blk_lines)})
            paragraphs.append({
                "paragraph_id": f"{tid}_block",
                "section": cur_sec,
                "page": None,
                "printed_page": None,
                "line_start": buf_start,
                "line_end": end_line,
                "locator_type": "table_cell",
                "table_id": tid,
                "text": norm_ws(" ".join(blk_lines)),
            })
            return
        txt = norm_ws(block)
        if not txt:
            return
        pid = f"P{len(paragraphs)+1:03d}"
        m_fig = re.match(r"^(?:\*\*)?(?:Fig\.?|Figure)\s*(\d+[A-Za-z]?)\b", txt, re.I)
        if m_fig:
            fid = f"Fig_{m_fig.group(1)}"
            figures.append({"figure_id": fid, "section": cur_sec, "page": None, "caption": txt})
            paragraphs.append({
                "paragraph_id": pid,
                "section": cur_sec,
                "page": None,
                "printed_page": None,
                "line_start": buf_start,
                "line_end": end_line,
                "locator_type": "figure_caption",
                "figure_id": fid,
                "text": txt,
            })
            return
        paragraphs.append({
            "paragraph_id": pid,
            "section": cur_sec,
            "page": None,
            "printed_page": None,
            "line_start": buf_start,
            "line_end": end_line,
            "locator_type": "line_range",
            "text": txt,
        })

    for idx, line in enumerate(lines, 1):
        s = line.strip()
        hm = re.match(r"^(#{1,6})\s+(.+)$", s)
        if hm:
            flush_buf(idx - 1)
            cur_sec = norm_ws(hm.group(2))
            if len(hm.group(1)) == 1 and not meta["title"]:
                meta["title"] = cur_sec
            buf_start = idx + 1
            continue
        if not s:
            flush_buf(idx - 1)
            buf_start = idx + 1
            continue
        if not buf:
            buf_start = idx
        buf.append(line)
    flush_buf(len(lines))

    return {
        "has_pdf_pages": False,
        "page_count": None,
        "metadata": meta,
        "paragraphs": paragraphs,
        "tables": tables,
        "figures": figures,
        "unreadable_pages": [],
        "extraction_backend": "markdown_text",
        "notes": [],
    }


def _xycut_sort_blocks(blocks: List[Tuple[float, float, float, float, str]], page_w: float, cfg: Dict[str, Any]) -> List[str]:
    """Sort PDF text blocks by column (detect two-column gutter) then top-to-bottom."""
    if not blocks:
        return []
    pdf_cfg = cfg.get("pdf_ingestion") or {}
    gmin = float(pdf_cfg.get("two_column_gutter_min_frac", 0.35)) * page_w
    gmax = float(pdf_cfg.get("two_column_gutter_max_frac", 0.65)) * page_w
    mid = 0.5 * page_w

    left_blocks = [b for b in blocks if b[2] < gmax and b[0] < mid]
    right_blocks = [b for b in blocks if b[0] >= gmin and b[2] > mid]
    span_blocks = [b for b in blocks if b not in left_blocks and b not in right_blocks]

    # True two-column layout if both columns contain >= 2 blocks and little cross-gutter overlap
    if len(left_blocks) >= 2 and len(right_blocks) >= 2 and len(span_blocks) <= max(2, len(blocks) // 4):
        top_spans = sorted([b for b in span_blocks if b[1] < min(x[1] for x in left_blocks + right_blocks)], key=lambda b: (round(b[1], 1), b[0]))
        bot_spans = sorted([b for b in span_blocks if b not in top_spans], key=lambda b: (round(b[1], 1), b[0]))
        left_sorted = sorted(left_blocks, key=lambda b: (round(b[1], 1), b[0]))
        right_sorted = sorted(right_blocks, key=lambda b: (round(b[1], 1), b[0]))
        ordered = top_spans + left_sorted + right_sorted + bot_spans
    else:
        ordered = sorted(blocks, key=lambda b: (round(b[1], 1), b[0]))
    return [norm_ws(b[4]) for b in ordered if norm_ws(b[4])]


def _garble_ratio(text: str) -> float:
    if not text:
        return 1.0
    bad = sum(1 for ch in text if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\n\r\t"))
    return bad / max(1, len(text))


def parse_pdf_document(path: Path, cfg: Dict[str, Any], ocr_auto: bool = False) -> Dict[str, Any]:
    pdf_cfg = cfg.get("pdf_ingestion") or {}
    min_chars = int(pdf_cfg.get("min_chars_per_page_text_layer", 80))
    max_garble = float(pdf_cfg.get("max_garble_ratio", 0.25))
    band_frac = float(pdf_cfg.get("header_footer_band_frac", 0.08))

    paragraphs: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    figures: List[Dict[str, Any]] = []
    unreadable_pages: List[Dict[str, Any]] = []
    notes: List[str] = []
    meta: Dict[str, Any] = {
        "title": None, "authors": [], "year": None, "venue": None, "version": None,
        "doi": None, "pmid": None, "arxiv_id": None, "url": None,
    }
    page_count = 0
    backend = "none"

    try:
        try:
            import pymupdf as fitz  # type: ignore
        except ImportError:
            import fitz  # type: ignore

        backend = "pymupdf"
        doc = fitz.open(str(path))
        if doc.needs_pass:
            return {
                "has_pdf_pages": True,
                "page_count": len(doc),
                "metadata": meta,
                "paragraphs": [],
                "tables": [],
                "figures": [],
                "unreadable_pages": [{"page": 1, "reason": "encrypted_requires_password"}],
                "extraction_backend": "pymupdf",
                "notes": ["PDF is encrypted and requires a password."],
            }
        page_count = len(doc)
        pdf_meta = doc.metadata or {}
        if pdf_meta.get("title") and len(norm_ws(pdf_meta["title"])) > 4:
            meta["title"] = norm_ws(pdf_meta["title"])
        if pdf_meta.get("author"):
            meta["authors"] = _normalize_authors(pdf_meta["author"])

        raw_pages: List[Tuple[int, float, float, List[Tuple[float, float, float, float, str]]]] = []
        edge_line_counts: Counter = Counter()

        for pno in range(page_count):
            page = doc[pno]
            rect = page.rect
            pw, ph = float(rect.width), float(rect.height)
            blocks_raw = page.get_text("blocks") or []
            text_blocks: List[Tuple[float, float, float, float, str]] = []
            for blk in blocks_raw:
                if len(blk) >= 5 and (len(blk) < 7 or blk[6] == 0):
                    txt = norm_ws(str(blk[4]))
                    if txt:
                        text_blocks.append((float(blk[0]), float(blk[1]), float(blk[2]), float(blk[3]), txt))
                        if page_count >= 2 and (blk[1] < ph * band_frac or blk[3] > ph * (1.0 - band_frac)):
                            edge_line_counts[re.sub(r"\d+", "#", txt.lower())] += 1
            raw_pages.append((pno + 1, pw, ph, text_blocks))

        repeated_edges = {k for k, cnt in edge_line_counts.items() if cnt >= max(2, page_count // 2)}
        cur_sec = "Document"
        all_p1_text = ""

        for pno, pw, ph, text_blocks in raw_pages:
            filtered = [
                b for b in text_blocks
                if not ((b[1] < ph * band_frac or b[3] > ph * (1.0 - band_frac)) and re.sub(r"\d+", "#", b[4].lower()) in repeated_edges)
            ]
            ordered_texts = _xycut_sort_blocks(filtered, pw, cfg)
            joined = "\n".join(ordered_texts)
            if pno == 1:
                all_p1_text = joined

            gr = _garble_ratio(joined)
            if len(norm_ws(joined)) < min_chars or gr > max_garble:
                ocr_done = False
                if ocr_auto and shutil.which("tesseract"):
                    try:
                        pix = doc[pno - 1].get_pixmap(dpi=250)
                        img_bytes = pix.tobytes("png")
                        proc = subprocess.run(
                            ["tesseract", "stdin", "stdout", "--psm", "6"],
                            input=img_bytes,
                            capture_output=True,
                            timeout=45,
                        )
                        ocr_txt = proc.stdout.decode("utf-8", errors="replace").strip()
                        if len(ocr_txt) >= min_chars:
                            ocr_done = True
                            ordered_texts = [norm_ws(x) for x in re.split(r"\n\s*\n", ocr_txt) if norm_ws(x)]
                            notes.append(f"Page {pno} recovered via Tesseract OCR fallback.")
                    except Exception as exc:
                        notes.append(f"OCR failed on page {pno}: {exc}")
                if not ocr_done:
                    unreadable_pages.append({
                        "page": pno,
                        "char_count": len(norm_ws(joined)),
                        "garble_ratio": round(gr, 3),
                        "reason": "scanned_no_text_layer" if len(norm_ws(joined)) < min_chars else "garbled_encoding",
                    })
                    continue

            for blk_txt in ordered_texts:
                if re.match(r"^(?:\d+(?:\.\d+)*\.?\s+)?(Abstract|Introduction|Methods?|Materials?\s+and\s+Methods?|Results?|Discussion|Conclusions?|References)\b", blk_txt, re.I) and len(blk_txt) < 80:
                    cur_sec = blk_txt
                    continue
                pid = f"P{len(paragraphs)+1:03d}"
                m_fig = re.match(r"^(?:Fig\.?|Figure)\s*(\d+[A-Za-z]?)\b", blk_txt, re.I)
                m_tab = re.match(r"^Table\s*(\d+[A-Za-z]?)\b", blk_txt, re.I)
                if m_fig:
                    fid = f"Fig_{m_fig.group(1)}"
                    figures.append({"figure_id": fid, "section": cur_sec, "page": pno, "caption": blk_txt})
                    paragraphs.append({
                        "paragraph_id": pid,
                        "section": cur_sec,
                        "page": pno,
                        "printed_page": str(pno),
                        "line_start": None,
                        "line_end": None,
                        "locator_type": "figure_caption",
                        "figure_id": fid,
                        "text": blk_txt,
                    })
                elif m_tab:
                    tid = f"Table_{m_tab.group(1)}"
                    tables.append({"table_id": tid, "section": cur_sec, "page": pno, "caption": blk_txt, "markdown": ""})
                    paragraphs.append({
                        "paragraph_id": pid,
                        "section": cur_sec,
                        "page": pno,
                        "printed_page": str(pno),
                        "line_start": None,
                        "line_end": None,
                        "locator_type": "table_cell",
                        "table_id": tid,
                        "text": blk_txt,
                    })
                else:
                    paragraphs.append({
                        "paragraph_id": pid,
                        "section": cur_sec,
                        "page": pno,
                        "printed_page": str(pno),
                        "line_start": None,
                        "line_end": None,
                        "locator_type": "pdf_page",
                        "text": blk_txt,
                    })

        if all_p1_text:
            p1_meta = _extract_plain_metadata(all_p1_text)
            for k in ("doi", "pmid", "arxiv_id", "year", "venue", "version"):
                if not meta.get(k) and p1_meta.get(k):
                    meta[k] = p1_meta[k]
            if not meta["title"] and ordered_texts:
                # Fallback: first non-empty block on page 1 if reasonable title length
                p1_blocks = _xycut_sort_blocks(raw_pages[0][3], raw_pages[0][1], cfg) if raw_pages else []
                for cand in p1_blocks[:3]:
                    if 12 <= len(cand) <= 220 and not re.match(r"^(doi|abstract|journal|volume)", cand, re.I):
                        meta["title"] = cand
                        break
        doc.close()
    except ImportError:
        # Fallback to pdftotext if pymupdf unavailable
        if shutil.which("pdftotext"):
            backend = "pdftotext"
            proc = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True, timeout=60)
            pages_txt = proc.stdout.split("\x0c")
            page_count = max(1, len([p for p in pages_txt if p.strip()]) or len(pages_txt))
            for pno, ptxt in enumerate(pages_txt, 1):
                if not ptxt.strip():
                    if pno <= page_count:
                        unreadable_pages.append({"page": pno, "char_count": 0, "garble_ratio": 1.0, "reason": "scanned_no_text_layer"})
                    continue
                for blk in re.split(r"\n\s*\n", ptxt):
                    t = norm_ws(blk)
                    if t:
                        paragraphs.append({
                            "paragraph_id": f"P{len(paragraphs)+1:03d}",
                            "section": "Document",
                            "page": pno,
                            "printed_page": str(pno),
                            "line_start": None,
                            "line_end": None,
                            "locator_type": "pdf_page",
                            "text": t,
                        })
        else:
            unreadable_pages.append({"page": 1, "char_count": 0, "garble_ratio": 1.0, "reason": "no_pdf_backend_available"})
            notes.append("Neither PyMuPDF nor pdftotext is installed to parse PDF.")
    except Exception as exc:
        unreadable_pages.append({"page": 1, "char_count": 0, "garble_ratio": 1.0, "reason": f"pdf_parse_error: {exc}"})
        notes.append(f"PDF open/parse failed: {exc}")

    return {
        "has_pdf_pages": True,
        "page_count": page_count,
        "metadata": meta,
        "paragraphs": paragraphs,
        "tables": tables,
        "figures": figures,
        "unreadable_pages": unreadable_pages,
        "extraction_backend": backend,
        "notes": notes,
    }


def _enrich_metadata_from_manifest(meta: Dict[str, Any], entry: Optional[Dict[str, Any]], case_data: Dict[str, Any], is_primary: bool) -> Dict[str, Any]:
    """Populate per-document metadata strictly from manifest entry or primary-only case fallback."""
    out = dict(meta)
    if entry:
        out["title"] = out.get("title") or entry.get("title")
        if not out.get("authors"):
            out["authors"] = _normalize_authors(entry.get("authors") or entry.get("author"))
        out["year"] = out.get("year") or _parse_year(entry.get("year") or entry.get("date"))
        out["venue"] = out.get("venue") or entry.get("venue") or entry.get("journal")
        out["version"] = out.get("version") or entry.get("version")
        out["doi"] = out.get("doi") or norm_doi(entry.get("doi")) or None
        out["pmid"] = out.get("pmid") or (str(entry["pmid"]) if entry.get("pmid") else None)
        out["arxiv_id"] = out.get("arxiv_id") or (str(entry["arxiv_id"]) if entry.get("arxiv_id") else None)
        out["url"] = out.get("url") or entry.get("url")
    # CRITICAL GUARDRAIL: Root case.json metadata applies ONLY to primary paper (never secondary papers!)
    if is_primary:
        pm = case_data.get("paper") if isinstance(case_data.get("paper"), dict) else case_data
        out["title"] = out.get("title") or pm.get("title") or pm.get("paper_title")
        if not out.get("authors"):
            out["authors"] = _normalize_authors(pm.get("authors"))
        out["year"] = out.get("year") or _parse_year(pm.get("year"))
        out["venue"] = out.get("venue") or pm.get("venue") or pm.get("journal")
        out["version"] = out.get("version") or pm.get("version")
        out["doi"] = out.get("doi") or norm_doi(pm.get("doi")) or None
        out["pmid"] = out.get("pmid") or (str(pm["pmid"]) if pm.get("pmid") else None)
        out["arxiv_id"] = out.get("arxiv_id") or (str(pm["arxiv_id"]) if pm.get("arxiv_id") else None)
        out["url"] = out.get("url") or pm.get("url")
    return out


def _index_sentences(paragraphs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Attach sentence IDs (S001, S002...) to every paragraph and build flat sentence lookup."""
    sentences_flat: List[Dict[str, Any]] = []
    s_idx = 0
    for p in paragraphs:
        sents = split_sentences(p["text"])
        p_sents: List[Dict[str, Any]] = []
        cursor = 0
        for st in sents:
            s_idx += 1
            sid = f"S{s_idx:03d}"
            pos = p["text"].find(st, cursor)
            if pos < 0:
                pos = p["text"].find(st)
            cstart = pos if pos >= 0 else None
            cend = (pos + len(st)) if pos >= 0 else None
            if pos >= 0:
                cursor = pos + len(st)
            rec = {
                "sentence_id": sid,
                "paragraph_id": p["paragraph_id"],
                "section": p.get("section"),
                "page": p.get("page"),
                "printed_page": p.get("printed_page"),
                "locator_type": p.get("locator_type"),
                "char_start": cstart,
                "char_end": cend,
                "text": st,
            }
            p_sents.append(rec)
            sentences_flat.append(rec)
        p["sentences"] = p_sents
    return paragraphs, sentences_flat


def ingest(input_dir: Path, out_dir: Path, ocr_auto: bool = False) -> Dict[str, Any]:
    cfg = load_config()
    out_dir.mkdir(parents=True, exist_ok=True)

    case_path = input_dir / "case.json"
    task_path = input_dir / "task.md"
    case_data: Dict[str, Any] = load_json(case_path) if case_path.exists() else {}
    task_text = task_path.read_text(encoding="utf-8", errors="replace") if task_path.exists() else ""
    case_id = str(case_data.get("case_id") or case_data.get("id") or input_dir.name)
    _, contract_fields = extract_output_contract_fields(case_data)
    questions, claims_to_verify = extract_questions_and_claims(case_data, task_text)

    manifest_map: Dict[str, Dict[str, Any]] = {}
    for mf_name in ("corpus-manifest.json", "manifest.json"):
        mf = input_dir / mf_name
        if mf.exists():
            manifest_map.update(_parse_manifest(mf))

    # Discover material files deterministically
    candidate_files: List[Path] = []
    other_assets: List[str] = []
    if input_dir.exists():
        for fp in sorted(input_dir.rglob("*")):
            if not fp.is_file():
                continue
            rel = fp.relative_to(input_dir).as_posix()
            if fp.name.lower() in CONTROL_FILES:
                continue
            if fp.suffix.lower() in DOC_EXTS:
                candidate_files.append(fp)
            else:
                other_assets.append(rel)

    # Sort primary first if recognizable (exact primary before copy/secondary)
    def sort_key(p: Path) -> Tuple[int, str]:
        nm = p.name.lower()
        if "copy" in nm or "dup" in nm:
            return (1, nm)
        if "primary" in nm or "paper" in nm or "main" in nm:
            return (0, nm)
        if "secondary" in nm:
            return (2, nm)
        return (3, nm)

    candidate_files.sort(key=sort_key)

    documents: List[Dict[str, Any]] = []
    warnings: List[str] = []
    missing_refs: List[str] = []

    # Check if case.json mentions input files that don't exist
    for k in ("input_file", "paper_path", "pdf_path", "material_path"):
        ref_val = case_data.get(k)
        if isinstance(ref_val, str) and ref_val.strip():
            ref_p = input_dir / Path(ref_val).name
            if not (input_dir / ref_val).exists() and not ref_p.exists():
                missing_refs.append(ref_val)
                warnings.append(f"Declared input file `{ref_val}` not found under {input_dir}.")

    if not candidate_files:
        warnings.append("No readable document files found in input directory.")

    shingle_cache: List[Tuple[str, set, Dict[str, Any]]] = []
    group_counter = 0

    for idx, fp in enumerate(candidate_files):
        rel = fp.relative_to(input_dir).as_posix()
        source_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", fp.stem)
        if any(d["source_id"] == source_id for d in documents):
            source_id = f"{source_id}_{idx+1}"
        fhash = sha256_file(fp)
        ext = fp.suffix.lower()

        try:
            if ext == ".pdf":
                parsed = parse_pdf_document(fp, cfg, ocr_auto=ocr_auto)
            elif ext in {".xml", ".nxml"}:
                parsed = parse_xml_document(fp, source_id)
            elif ext == ".json":
                parsed = parse_json_material(fp)
            else:
                parsed = parse_text_or_markdown(fp)
        except Exception as exc:
            warnings.append(f"Failed to parse {rel}: {exc}")
            parsed = {
                "has_pdf_pages": ext == ".pdf",
                "page_count": None,
                "metadata": {"title": None, "authors": [], "year": None, "venue": None, "version": None, "doi": None, "pmid": None, "arxiv_id": None, "url": None},
                "paragraphs": [],
                "tables": [],
                "figures": [],
                "unreadable_pages": [{"page": 1, "reason": f"parse_exception: {exc}"}],
                "extraction_backend": "failed",
                "notes": [str(exc)],
            }

        m_entry = manifest_map.get(rel) or manifest_map.get(fp.name)
        is_primary = (m_entry is not None and str(m_entry.get("role", "")).lower() == "primary") or (idx == 0 and m_entry is None)
        meta = _enrich_metadata_from_manifest(parsed["metadata"], m_entry, case_data, is_primary=is_primary)
        meta["source_id"] = source_id
        meta["source_file"] = rel
        meta["file_sha256"] = fhash

        missing_meta = [fld for fld in ("title", "authors", "year", "doi") if not meta.get(fld)]
        if missing_meta:
            warnings.append(f"Source `{source_id}` ({rel}) missing bibliographic fields: {', '.join(missing_meta)}.")

        if parsed["unreadable_pages"]:
            warnings.append(f"Source `{source_id}` has {len(parsed['unreadable_pages'])} unreadable/scanned page(s).")

        paragraphs, sentences = _index_sentences(parsed["paragraphs"])
        full_text = " ".join(p["text"] for p in paragraphs)
        sh = text_shingles(full_text)

        # Same-paper multi-format detection via DOI / title / 5-gram Jaccard >= 0.70
        thresh = float((cfg.get("deduplication") or {}).get("same_paper_doc_jaccard_threshold", 0.70))
        assigned_group: Optional[str] = None
        for gid, prev_sh, prev_meta in shingle_cache:
            if meta.get("doi") and prev_meta.get("doi") and meta["doi"] == prev_meta["doi"]:
                assigned_group = gid
                break
            if meta.get("title") and prev_meta.get("title") and norm_key(meta["title"]) == norm_key(prev_meta["title"]) and len(norm_key(meta["title"])) > 15:
                assigned_group = gid
                break
            if set_jaccard(sh, prev_sh) >= thresh:
                assigned_group = gid
                break
        if not assigned_group:
            group_counter += 1
            assigned_group = f"PG{group_counter:02d}"
            shingle_cache.append((assigned_group, sh, meta))

        documents.append({
            "source_id": source_id,
            "paper_group_id": assigned_group,
            "source_file": rel,
            "file_sha256": fhash,
            "extraction_backend": parsed["extraction_backend"],
            "has_pdf_pages": bool(parsed["has_pdf_pages"]),
            "page_count": parsed["page_count"],
            "metadata": meta,
            "paragraph_count": len(paragraphs),
            "sentence_count": len(sentences),
            "table_count": len(parsed["tables"]),
            "figure_count": len(parsed["figures"]),
            "unreadable_pages": parsed["unreadable_pages"],
            "notes": parsed["notes"],
            "paragraphs": paragraphs,
            "sentences": sentences,
            "tables": parsed["tables"],
            "figures": parsed["figures"],
        })

    # Determine workflow_mode honestly
    readable_docs = [d for d in documents if d["sentence_count"] > 0]
    distinct_groups = {d["paper_group_id"] for d in readable_docs}
    if not readable_docs:
        workflow_mode = "degraded_missing_or_unreadable"
    elif claims_to_verify:
        workflow_mode = "verify_supplied_claims"
    elif questions:
        workflow_mode = "qa_with_nanopubs"
    elif len(distinct_groups) > 1:
        workflow_mode = "multi_paper_nanopubs"
    else:
        workflow_mode = "extract_and_structure"

    any_pdf_pages = any(d["has_pdf_pages"] for d in documents)

    # Write full source_index.json
    source_index = {
        "case_id": case_id,
        "workflow_mode": workflow_mode,
        "has_pdf_pages": any_pdf_pages,
        "documents": documents,
        "other_assets": other_assets,
        "missing_declared_files": missing_refs,
        "warnings": warnings,
    }
    save_json(out_dir / "source_index.json", source_index)

    # Write compact case_brief.json
    case_brief = {
        "case_id": case_id,
        "workflow_mode": workflow_mode,
        "has_pdf_pages": any_pdf_pages,
        "output_contract_raw": case_data.get("output_contract"),
        "output_contract_fields": contract_fields,
        "questions": questions,
        "claims_to_verify": claims_to_verify,
        "sources_summary": [
            {
                "source_id": d["source_id"],
                "paper_group_id": d["paper_group_id"],
                "source_file": d["source_file"],
                "file_sha256": d["file_sha256"],
                "has_pdf_pages": d["has_pdf_pages"],
                "page_count": d["page_count"],
                "paragraph_count": d["paragraph_count"],
                "sentence_count": d["sentence_count"],
                "unreadable_pages": d["unreadable_pages"],
                "metadata": d["metadata"],
            }
            for d in documents
        ],
        "missing_declared_files": missing_refs,
        "other_assets": other_assets,
        "warnings": warnings,
    }
    save_json(out_dir / "case_brief.json", case_brief)

    # Write compact token-efficient source_digest.md & per-source digests
    digest_lines: List[str] = [
        f"# Source Digest for Case `{case_id}`",
        f"- **Workflow Mode**: `{workflow_mode}`",
        f"- **Has PDF Pages**: `{any_pdf_pages}`",
        "",
    ]
    if questions:
        digest_lines.append("## Case Questions")
        for q in questions:
            digest_lines.append(f"- **{q['id']}** ({q['source']}): {q['question']}")
        digest_lines.append("")
    if claims_to_verify:
        digest_lines.append("## Claims to Verify")
        for c in claims_to_verify:
            digest_lines.append(f"- **{c['id']}** ({c['source']}): {c['claim']}")
        digest_lines.append("")
    if warnings:
        digest_lines.append("## Ingestion Warnings & Boundaries")
        for w in warnings:
            digest_lines.append(f"- {w}")
        digest_lines.append("")

    for doc in documents:
        doc_lines = [
            f"## Source `{doc['source_id']}` (Group `{doc['paper_group_id']}`, File `{doc['source_file']}`)",
            f"- **Title**: {doc['metadata'].get('title') or 'UNREPORTED'}",
            f"- **Authors**: {', '.join(doc['metadata'].get('authors') or []) or 'UNREPORTED'}",
            f"- **Year**: {doc['metadata'].get('year') or 'UNREPORTED'} | **Venue**: {doc['metadata'].get('venue') or 'UNREPORTED'} | **Version**: {doc['metadata'].get('version') or 'UNREPORTED'}",
            f"- **DOI**: {doc['metadata'].get('doi') or 'UNREPORTED'} | **PMID**: {doc['metadata'].get('pmid') or 'UNREPORTED'} | **arXiv**: {doc['metadata'].get('arxiv_id') or 'UNREPORTED'}",
            f"- **SHA-256**: `{doc['file_sha256']}` | **has_pdf_pages**: `{doc['has_pdf_pages']}`",
            "",
        ]
        for p in doc["paragraphs"]:
            loc_tag = f"p.{p['page']} | " if (doc["has_pdf_pages"] and p.get("page") is not None) else ""
            if p.get("line_start") is not None:
                loc_tag += f"L{p['line_start']}-{p['line_end']} | "
            doc_lines.append(f"### [{p['paragraph_id']} | {loc_tag}Sec: {p.get('section')}]")
            for s in p.get("sentences") or []:
                doc_lines.append(f"- `[{s['sentence_id']}]` {s['text']}")
            doc_lines.append("")
        if doc["tables"]:
            doc_lines.append("### Tables")
            for tb in doc["tables"]:
                doc_lines.append(f"#### {tb['table_id']} ({tb.get('caption') or 'no caption'})")
                if tb.get("markdown"):
                    doc_lines.append(tb["markdown"])
                doc_lines.append("")
        (out_dir / f"digest_{doc['source_id']}.md").write_text("\n".join(doc_lines), encoding="utf-8")
        digest_lines.extend(doc_lines)

    (out_dir / "source_digest.md").write_text("\n".join(digest_lines), encoding="utf-8")

    summary = {
        "status": "ok" if readable_docs else "degraded",
        "case_id": case_id,
        "workflow_mode": workflow_mode,
        "has_pdf_pages": any_pdf_pages,
        "documents_count": len(documents),
        "readable_documents_count": len(readable_docs),
        "distinct_paper_groups": len(distinct_groups),
        "total_sentences": sum(d["sentence_count"] for d in documents),
        "warnings_count": len(warnings),
        "out_dir": str(out_dir),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest scientific papers and build verifiable Nanopub source index.")
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--ocr-auto", action="store_true", help="Attempt Tesseract OCR on scanned PDF pages if available.")
    args = ap.parse_args()
    ingest(args.input_dir, args.out_dir, ocr_auto=args.ocr_auto)


if __name__ == "__main__":
    main()
