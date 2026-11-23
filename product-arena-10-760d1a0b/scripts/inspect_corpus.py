#!/usr/bin/env python3
"""Inspect scientific literature inputs, isolate per-paper metadata, detect format duplicates,
reconcile JATS XML formulas/subscripts/tables, and produce an Agent-ready corpus index & digest.

Pure Python 3.10+ standard library for all JSON/XML/Markdown/TXT workflows.
Optional PDF extraction backends (pymupdf, pdfplumber, pypdf, pdftotext) are used only when PDFs exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SKIP_FILENAMES = {
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

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp", ".gif"}
DATA_EXTS = {".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".h5", ".hdf5"}
SUPP_HINTS = ("supp", "supplement", "appendix", "si_", "s1_", "s2_", "clinical_raw")


def local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def norm_key(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", s)


def norm_doi(doi: Optional[str]) -> str:
    if not doi:
        return ""
    s = str(doi).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s.strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except Exception:
        return ""


def text_shingles(text: str, k: int = 5) -> set:
    words = re.findall(r"[a-z0-9\u4e00-\u9fff]+", (text or "").lower())
    if len(words) < k:
        return set(words)
    return {" ".join(words[i : i + k]) for i in range(0, min(len(words) - k + 1, 800))}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def load_json_safe(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:
        return None, f"Failed to parse JSON {path.name}: {exc}"


def extract_output_contract_fields(case_data: Dict[str, Any]) -> Tuple[Any, List[str]]:
    contract = case_data.get("output_contract")
    fields: List[str] = []
    if isinstance(contract, list):
        fields = [str(x) for x in contract if isinstance(x, str)]
    elif isinstance(contract, dict):
        if isinstance(contract.get("required"), list):
            fields.extend(str(x) for x in contract["required"])
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


def extract_case_questions(case_data: Dict[str, Any], task_text: str) -> List[str]:
    questions: List[str] = []
    for key in ("queries", "questions", "query_list", "evaluation_queries", "required_queries"):
        val = case_data.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item.strip():
                    questions.append(item.strip())
                elif isinstance(item, dict):
                    q = item.get("question") or item.get("query") or item.get("text") or item.get("prompt")
                    if q and str(q).strip():
                        questions.append(str(q).strip())
    # Scan task.md for explicit numbered queries/questions if present
    for line in (task_text or "").splitlines():
        s = line.strip()
        if re.match(r"^(Q\d+|查询\s*\d+|问题\s*\d+)[:：.\s]", s, re.IGNORECASE):
            if s not in questions:
                questions.append(s)
    return questions


def parse_manifest(manifest_path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    warnings: List[str] = []
    data, err = load_json_safe(manifest_path)
    if err or data is None:
        return [], [err or f"Empty manifest: {manifest_path}"]

    raw_entries: List[Dict[str, Any]] = []
    if isinstance(data, list):
        raw_entries = [x for x in data if isinstance(x, dict)]
    elif isinstance(data, dict):
        for list_key in ("documents", "papers", "sources", "corpus", "materials", "files", "items", "entries"):
            val = data.get(list_key)
            if isinstance(val, list):
                raw_entries = [x for x in val if isinstance(x, dict)]
                break
        if not raw_entries:
            # Possibly dict keyed by source_id
            for k, v in data.items():
                if isinstance(v, dict) and any(
                    fld in v for fld in ("doi", "title", "authors", "file", "path", "material_file", "license")
                ):
                    entry = dict(v)
                    entry.setdefault("source_id", str(k))
                    raw_entries.append(entry)

    entries: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_entries, start=1):
        sid = (
            item.get("source_id")
            or item.get("doc_id")
            or item.get("paper_id")
            or item.get("id")
            or item.get("role")
            or f"doc_{idx}"
        )
        authors = item.get("authors") or item.get("author") or []
        if isinstance(authors, str):
            authors = [a.strip() for a in re.split(r"[;,]", authors) if a.strip()]
        elif isinstance(authors, list):
            norm_auth = []
            for a in authors:
                if isinstance(a, str):
                    norm_auth.append(a.strip())
                elif isinstance(a, dict):
                    name = a.get("name") or " ".join(
                        p for p in [a.get("given"), a.get("family"), a.get("surname")] if p
                    ).strip()
                    if name:
                        norm_auth.append(name)
            authors = norm_auth

        file_candidates: List[str] = []
        for fk in ("file", "path", "material_file", "json_file", "text_file", "source_file", "filename"):
            if item.get(fk):
                file_candidates.append(str(item[fk]))
        if isinstance(item.get("files"), list):
            for f in item["files"]:
                if isinstance(f, str):
                    file_candidates.append(f)
                elif isinstance(f, dict) and f.get("path"):
                    file_candidates.append(str(f["path"]))

        xml_candidates: List[str] = []
        for xk in ("xml_file", "publisher_xml", "source_xml", "jats_xml"):
            if item.get(xk):
                xml_candidates.append(str(item[xk]))

        entries.append(
            {
                "source_id": str(sid),
                "role": str(item.get("role") or ("primary" if idx == 1 else "secondary" if idx == 2 else f"doc_{idx}")),
                "doi": item.get("doi") or item.get("DOI") or "",
                "title": item.get("title") or "",
                "authors": authors,
                "year": item.get("year") or item.get("publication_year") or item.get("date") or None,
                "journal": item.get("journal") or item.get("venue") or None,
                "license": item.get("license") or item.get("licence") or "",
                "source_hash": item.get("sha256") or item.get("source_hash") or item.get("hash") or "",
                "file_candidates": file_candidates,
                "xml_candidates": xml_candidates,
                "raw_meta": item,
            }
        )
    if not entries:
        warnings.append(f"Manifest {manifest_path.name} parsed but yielded 0 document entries.")
    return entries, warnings


def normalize_paragraph_obj(raw: Any, default_pid: str, default_section: str = "", default_source_id: str = "") -> Optional[Dict[str, Any]]:
    if isinstance(raw, str):
        txt = norm_ws(raw)
        if not txt:
            return None
        return {
            "paragraph_id": default_pid,
            "section": default_section,
            "text": txt,
            "source_id": default_source_id,
            "locator_type": "jats_paragraph_id",
        }
    if not isinstance(raw, dict):
        return None
    txt = (
        raw.get("text")
        or raw.get("content")
        or raw.get("body")
        or raw.get("paragraph")
        or raw.get("value")
        or ""
    )
    txt = norm_ws(str(txt))
    if not txt:
        return None
    pid = (
        raw.get("paragraph_id")
        or raw.get("jats_id")
        or raw.get("para_id")
        or raw.get("pid")
        or raw.get("block_id")
        or raw.get("chunk_id")
        or raw.get("locator")
        or raw.get("id")
        or raw.get("loc_id")
        or default_pid
    )
    sec = (
        raw.get("section")
        or raw.get("section_title")
        or raw.get("heading")
        or raw.get("sec_title")
        or default_section
        or ""
    )
    sid = raw.get("source_id") or raw.get("doc_id") or raw.get("paper_id") or default_source_id or ""
    loc_type = raw.get("locator_type") or "jats_paragraph_id"
    out = {
        "paragraph_id": str(pid),
        "section": str(sec),
        "text": txt,
        "source_id": str(sid),
        "locator_type": str(loc_type),
    }
    if raw.get("page") is not None:
        out["declared_page"] = raw.get("page")
    return out


def extract_paragraphs_from_json_obj(obj: Any, default_source_id: str = "") -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """Returns (doc_meta, single_doc_paragraphs, multi_doc_paragraphs_by_sid)."""
    meta: Dict[str, Any] = {}
    paras: List[Dict[str, Any]] = []
    by_sid: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    if isinstance(obj, list):
        for i, item in enumerate(obj, start=1):
            p = normalize_paragraph_obj(item, f"p{i}", "", default_source_id)
            if p:
                if p["source_id"] and p["source_id"] != default_source_id:
                    by_sid[p["source_id"]].append(p)
                else:
                    paras.append(p)
        return meta, paras, dict(by_sid)

    if not isinstance(obj, dict):
        return meta, paras, {}

    # Unwrap common single-document nested containers (article / document / data / paper)
    for wrap_key in ("article", "document", "paper", "data", "record"):
        if isinstance(obj.get(wrap_key), dict) and not any(
            k in obj for k in ("paragraphs", "segments", "sections", "documents", "papers")
        ):
            inner_meta, inner_paras, inner_by = extract_paragraphs_from_json_obj(obj[wrap_key], default_source_id)
            for k in ("source_id", "doc_id", "paper_id", "id", "doi", "DOI", "title", "authors", "license", "role"):
                if obj.get(k) is not None and k not in inner_meta:
                    inner_meta[k] = obj[k]
            return inner_meta, inner_paras, inner_by

    # Capture document-level metadata if present
    for k in ("source_id", "doc_id", "paper_id", "id", "doi", "DOI", "title", "authors", "license", "year", "journal", "role", "sha256", "source_hash"):
        if obj.get(k) is not None:
            meta[k] = obj[k]

    doc_sid = str(obj.get("source_id") or obj.get("doc_id") or obj.get("paper_id") or default_source_id or "")

    # Case 1: Multi-document container (e.g., material.json -> {"documents": [...]})
    for multi_key in ("documents", "papers", "sources", "articles", "corpus"):
        if isinstance(obj.get(multi_key), list) and obj[multi_key] and isinstance(obj[multi_key][0], dict):
            for idx, subdoc in enumerate(obj[multi_key], start=1):
                sub_sid = str(
                    subdoc.get("source_id")
                    or subdoc.get("doc_id")
                    or subdoc.get("paper_id")
                    or subdoc.get("id")
                    or f"doc_{idx}"
                )
                _, sub_paras, _ = extract_paragraphs_from_json_obj(subdoc, sub_sid)
                for sp in sub_paras:
                    sp["source_id"] = sub_sid
                    by_sid[sub_sid].append(sp)
            if by_sid:
                return meta, [], dict(by_sid)

    # Case 1b: Dict keyed by source_id (e.g., {"primary": {"paragraphs": [...]}, "secondary": {"paragraphs": [...]}})
    if not any(k in obj for k in ("paragraphs", "segments", "blocks", "sections", "body", "text", "content")):
        dict_doc_candidates = [
            (k, v)
            for k, v in obj.items()
            if isinstance(v, dict)
            and any(sk in v for sk in ("paragraphs", "segments", "sections", "body", "text", "content"))
        ]
        if len(dict_doc_candidates) >= 2:
            for sub_sid, subdoc in dict_doc_candidates:
                _, sub_paras, _ = extract_paragraphs_from_json_obj(subdoc, str(sub_sid))
                for sp in sub_paras:
                    sp["source_id"] = str(sub_sid)
                    by_sid[str(sub_sid)].append(sp)
            if by_sid:
                return meta, [], dict(by_sid)

    # Case 2: Flat paragraph list (e.g. paragraphs / segments / blocks / chunks / items)
    for list_key in ("paragraphs", "segments", "blocks", "text_blocks", "chunks", "items", "body_paragraphs", "entries", "records"):
        val = obj.get(list_key)
        if isinstance(val, list) and val:
            distinct_sids = set()
            collected: List[Dict[str, Any]] = []
            for i, item in enumerate(val, start=1):
                p = normalize_paragraph_obj(item, f"p{i}", "", doc_sid)
                if p:
                    collected.append(p)
                    if p["source_id"]:
                        distinct_sids.add(p["source_id"])
            if len(distinct_sids) >= 2:
                for p in collected:
                    by_sid[p["source_id"] or "unassigned"].append(p)
                return meta, [], dict(by_sid)
            paras.extend(collected)
            break

    # Case 3: Abstract + Sections hierarchy
    if not paras:
        if isinstance(obj.get("abstract"), str) and obj["abstract"].strip():
            paras.append(
                {
                    "paragraph_id": "abs-p1",
                    "section": "Abstract",
                    "text": norm_ws(obj["abstract"]),
                    "source_id": doc_sid,
                    "locator_type": "jats_paragraph_id",
                }
            )
        elif isinstance(obj.get("abstract"), list):
            for i, item in enumerate(obj["abstract"], start=1):
                p = normalize_paragraph_obj(item, f"abs-p{i}", "Abstract", doc_sid)
                if p:
                    paras.append(p)

        sections = obj.get("sections") or obj.get("body")
        if isinstance(sections, list):
            p_counter = 0
            for s_idx, sec in enumerate(sections, start=1):
                if isinstance(sec, dict):
                    sec_title = str(sec.get("title") or sec.get("heading") or sec.get("section") or f"Section {s_idx}")
                    sec_paras = sec.get("paragraphs") or sec.get("content") or sec.get("text")
                    if isinstance(sec_paras, list):
                        for j, item in enumerate(sec_paras, start=1):
                            p_counter += 1
                            p = normalize_paragraph_obj(item, f"sec{s_idx}-p{j}", sec_title, doc_sid)
                            if p:
                                paras.append(p)
                    elif isinstance(sec_paras, str) and sec_paras.strip():
                        p_counter += 1
                        paras.append(
                            {
                                "paragraph_id": str(sec.get("paragraph_id") or sec.get("id") or f"sec{s_idx}-p1"),
                                "section": sec_title,
                                "text": norm_ws(sec_paras),
                                "source_id": doc_sid,
                                "locator_type": "jats_paragraph_id",
                            }
                        )
                elif isinstance(sec, str) and sec.strip():
                    p_counter += 1
                    paras.append(
                        {
                            "paragraph_id": f"p{p_counter}",
                            "section": "Body",
                            "text": norm_ws(sec),
                            "source_id": doc_sid,
                            "locator_type": "jats_paragraph_id",
                        }
                    )

    # Case 4: Full-text string fallback
    if not paras:
        for fk in ("full_text", "text", "content", "markdown", "body_text"):
            if isinstance(obj.get(fk), str) and obj[fk].strip():
                chunks = [norm_ws(c) for c in re.split(r"\n\s*\n+", obj[fk]) if norm_ws(c)]
                for i, chunk in enumerate(chunks, start=1):
                    paras.append(
                        {
                            "paragraph_id": f"p{i}",
                            "section": "Body",
                            "text": chunk,
                            "source_id": doc_sid,
                            "locator_type": "section_line",
                        }
                    )
                break

    return meta, paras, dict(by_sid)


def render_xml_with_math_markup(elem: ET.Element) -> Tuple[str, str, List[str]]:
    """Return (linearized_text, structured_markup_text, formula_tokens) for an XML element."""
    tokens: List[str] = []

    def walk(node: ET.Element) -> str:
        t = local_tag(node.tag)
        if t == "tex-math":
            tex = norm_ws("".join(node.itertext()))
            if tex:
                tokens.append(f"tex:{tex}")
                return f" ${tex}$ "
            return ""
        if t == "sub":
            inner = norm_ws("".join(walk(c) for c in node) if list(node) else (node.text or ""))
            if node.text and list(node):
                inner = norm_ws(node.text + "".join(walk(c) + (c.tail or "") for c in node))
            if inner:
                tokens.append(f"sub:_{{{inner}}}")
                return f"_{{{inner}}}"
            return ""
        if t == "sup":
            inner = norm_ws("".join(walk(c) for c in node) if list(node) else (node.text or ""))
            if node.text and list(node):
                inner = norm_ws(node.text + "".join(walk(c) + (c.tail or "") for c in node))
            if inner:
                tokens.append(f"sup:^{{{inner}}}")
                return f"^{{{inner}}}"
            return ""
        if t == "alternatives":
            tex_nodes = [c for c in node if local_tag(c.tag) == "tex-math"]
            if tex_nodes:
                return walk(tex_nodes[0])
        if t in ("inline-formula", "disp-formula"):
            # Prefer tex-math if present inside
            tex_nodes = [c for c in node.iter() if local_tag(c.tag) == "tex-math"]
            if tex_nodes:
                tex = norm_ws("".join(tex_nodes[0].itertext()))
                if tex:
                    tokens.append(f"{t}:${tex}$")
                    return f" ${tex}$ "
            parts = [node.text or ""]
            for c in node:
                parts.append(walk(c))
                parts.append(c.tail or "")
            rendered = norm_ws("".join(parts))
            if rendered:
                tokens.append(f"{t}:{rendered}")
            return f" [{rendered}] " if rendered else ""

        parts = [node.text or ""]
        for c in node:
            parts.append(walk(c))
            parts.append(c.tail or "")
        return "".join(parts)

    linearized = norm_ws("".join(elem.itertext()))
    structured = norm_ws(walk(elem))
    return linearized, structured, tokens


def parse_jats_xml(xml_path: Path) -> Dict[str, Any]:
    """Extract metadata, paragraphs, formula/subscript/superscript reconciliations, and tables from JATS XML."""
    result: Dict[str, Any] = {
        "file": str(xml_path.name),
        "path": str(xml_path),
        "sha256": sha256_file(xml_path),
        "doi": "",
        "title": "",
        "authors": [],
        "paragraphs": [],
        "formula_reconciliations": [],
        "tables": [],
        "warnings": [],
    }
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception as exc:
        result["warnings"].append(f"XML parse error in {xml_path.name}: {exc}")
        return result

    # Metadata
    for el in root.iter():
        t = local_tag(el.tag)
        if t == "article-id" and el.attrib.get("pub-id-type", "").lower() == "doi" and not result["doi"]:
            result["doi"] = norm_ws("".join(el.itertext()))
        elif t == "article-title" and not result["title"]:
            result["title"] = norm_ws("".join(el.itertext()))
        elif t == "contrib" and el.attrib.get("contrib-type", "author") == "author":
            surname = ""
            given = ""
            for child in el.iter():
                ct = local_tag(child.tag)
                if ct == "surname":
                    surname = norm_ws("".join(child.itertext()))
                elif ct == "given-names":
                    given = norm_ws("".join(child.itertext()))
            full = f"{given} {surname}".strip()
            if full and full not in result["authors"]:
                result["authors"].append(full)

    # Walk sections & paragraphs
    p_idx = 0

    def walk_sec(node: ET.Element, current_section: str) -> None:
        nonlocal p_idx
        t = local_tag(node.tag)
        if t == "sec":
            title_el = next((c for c in node if local_tag(c.tag) == "title"), None)
            if title_el is not None:
                current_section = norm_ws("".join(title_el.itertext())) or current_section
        elif t == "abstract":
            current_section = "Abstract"
        elif t == "table-wrap":
            tid = node.attrib.get("id") or f"table_{len(result['tables']) + 1}"
            label = ""
            caption = ""
            rows: List[List[str]] = []
            for c in node:
                ct = local_tag(c.tag)
                if ct == "label":
                    label = norm_ws("".join(c.itertext()))
                elif ct == "caption":
                    _, cap_struct, _ = render_xml_with_math_markup(c)
                    caption = cap_struct
            for tr in node.iter():
                if local_tag(tr.tag) == "tr":
                    row_cells = []
                    for cell in tr:
                        if local_tag(cell.tag) in ("th", "td"):
                            _, cell_struct, _ = render_xml_with_math_markup(cell)
                            row_cells.append(cell_struct)
                    if row_cells:
                        rows.append(row_cells)
            result["tables"].append(
                {
                    "table_id": tid,
                    "label": label,
                    "caption": caption,
                    "row_count": len(rows),
                    "rows": rows[:40],
                }
            )
            return
        elif t == "p":
            p_idx += 1
            pid = node.attrib.get("id") or f"xml-p{p_idx}"
            linear, struct, tokens = render_xml_with_math_markup(node)
            if linear:
                result["paragraphs"].append(
                    {
                        "paragraph_id": pid,
                        "section": current_section,
                        "text": linear,
                        "structured_xml_text": struct,
                        "locator_type": "jats_paragraph_id",
                    }
                )
                if tokens:
                    result["formula_reconciliations"].append(
                        {
                            "paragraph_id": pid,
                            "section": current_section,
                            "linearized_text": linear,
                            "xml_structured_text": struct,
                            "tokens": tokens,
                        }
                    )
            return

        for child in node:
            walk_sec(child, current_section)

    walk_sec(root, "Body")
    return result


def parse_markdown_or_text(path: Path, source_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    title = ""
    section = "Body"
    paras: List[Dict[str, Any]] = []
    buf: List[str] = []
    start_line = 1
    p_num = 0

    def flush(end_line: int) -> None:
        nonlocal p_num, buf
        txt = norm_ws(" ".join(buf))
        buf = []
        if not txt:
            return
        p_num += 1
        paras.append(
            {
                "paragraph_id": f"L{start_line}-L{end_line}",
                "section": section,
                "text": txt,
                "source_id": source_id,
                "locator_type": "section_line",
            }
        )

    for idx, line in enumerate(lines, start=1):
        s = line.strip()
        if s.startswith("#"):
            flush(idx - 1)
            heading = s.lstrip("#").strip()
            if not title and s.startswith("# "):
                title = heading
            section = heading or section
            start_line = idx + 1
        elif not s:
            flush(idx - 1)
            start_line = idx + 1
        else:
            if not buf:
                start_line = idx
            buf.append(s)
    flush(len(lines))

    doi_match = re.search(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", raw)
    meta = {
        "title": title,
        "doi": doi_match.group(1).rstrip(".,;)") if doi_match else "",
    }
    return meta, paras


def parse_pdf_file(path: Path, source_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str], bool]:
    """Attempt PDF extraction via pymupdf -> pdfplumber -> pypdf -> pdftotext.
    Returns (meta, paragraphs, warnings, has_real_pdf_pages)."""
    warnings: List[str] = []
    pages_text: List[Tuple[int, str]] = []
    backend = None

    try:
        import fitz  # type: ignore

        doc = fitz.open(str(path))
        if doc.is_encrypted:
            warnings.append(f"PDF {path.name} is encrypted and cannot be extracted without password.")
            return {"title": path.stem}, [], warnings, False
        for i, page in enumerate(doc, start=1):
            pages_text.append((i, page.get_text("text") or ""))
        backend = "pymupdf"
    except Exception:
        pass

    if not backend:
        try:
            import pdfplumber  # type: ignore

            with pdfplumber.open(str(path)) as pdf:
                for i, page in enumerate(pdf.pages, start=1):
                    pages_text.append((i, page.extract_text() or ""))
            backend = "pdfplumber"
        except Exception:
            pass

    if not backend:
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(str(path))
            if reader.is_encrypted:
                warnings.append(f"PDF {path.name} is encrypted.")
                return {"title": path.stem}, [], warnings, False
            for i, page in enumerate(reader.pages, start=1):
                pages_text.append((i, page.extract_text() or ""))
            backend = "pypdf"
        except Exception:
            pass

    if not backend and shutil.which("pdftotext"):
        try:
            proc = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                raw_pages = proc.stdout.split("\x0c")
                for i, ptxt in enumerate(raw_pages, start=1):
                    if ptxt.strip() or i < len(raw_pages):
                        pages_text.append((i, ptxt))
                backend = "pdftotext"
        except Exception as exc:
            warnings.append(f"pdftotext failed on {path.name}: {exc}")

    if not backend:
        warnings.append(
            f"No PDF extraction backend succeeded for {path.name}; content is unreadable and must NOT be fabricated."
        )
        return {"title": path.stem}, [], warnings, False

    paras: List[Dict[str, Any]] = []
    empty_pages: List[int] = []
    for pno, ptxt in pages_text:
        cleaned = re.sub(r"-\n(?=[a-z])", "", ptxt)
        chunks = [norm_ws(c) for c in re.split(r"\n\s*\n+", cleaned) if norm_ws(c)]
        if sum(len(c) for c in chunks) < 40:
            empty_pages.append(pno)
        for j, chunk in enumerate(chunks, start=1):
            paras.append(
                {
                    "paragraph_id": f"page{pno}-p{j}",
                    "page": pno,
                    "section": f"Page {pno}",
                    "text": chunk,
                    "source_id": source_id,
                    "locator_type": "pdf_page",
                }
            )
    if empty_pages:
        warnings.append(
            f"PDF {path.name} has low-text/scanned pages {empty_pages} (backend={backend}); do NOT guess unseen text."
        )
    full_joined = "\n".join(p["text"] for p in paras[:5])
    doi_m = re.search(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", full_joined)
    meta = {
        "title": paras[0]["text"][:180] if paras else path.stem,
        "doi": doi_m.group(1).rstrip(".,;)") if doi_m else "",
        "pdf_backend": backend,
        "total_pdf_pages": len(pages_text),
        "empty_or_scanned_pages": empty_pages,
    }
    return meta, paras, warnings, True


def inspect_corpus(
    input_dir: Path,
    case_path: Optional[Path],
    task_path: Optional[Path],
    out_dir: Path,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: List[str] = []
    declared_boundaries: List[str] = []

    # 1. Load case.json & task.md
    case_data: Dict[str, Any] = {}
    if case_path and case_path.exists():
        loaded, err = load_json_safe(case_path)
        if err:
            warnings.append(err)
        elif isinstance(loaded, dict):
            case_data = loaded
    else:
        warnings.append(f"case.json not found at {case_path}")

    task_text = ""
    if task_path and task_path.exists():
        task_text = task_path.read_text(encoding="utf-8", errors="replace")

    contract_raw, required_fields = extract_output_contract_fields(case_data)
    case_questions = extract_case_questions(case_data, task_text)

    # 2. Discover all files under input_dir
    all_files = sorted([p for p in input_dir.rglob("*") if p.is_file()]) if input_dir.exists() else []
    rel_files = [str(p.relative_to(input_dir)).replace("\\", "/") for p in all_files]

    has_images = any(p.suffix.lower() in IMAGE_EXTS for p in all_files)
    has_raw_data = any(p.suffix.lower() in DATA_EXTS for p in all_files)
    has_supp = any(any(h in p.name.lower() for h in SUPP_HINTS) for p in all_files)
    has_any_pdf = any(p.suffix.lower() == ".pdf" for p in all_files)

    if not has_images:
        declared_boundaries.append(
            "figure_pixels_unavailable: No image pixel files are provided; never infer numerical values from unseen figures."
        )
    if not has_supp:
        declared_boundaries.append(
            "supplementary_unavailable: No supplementary attachment files are provided; never infer values from cited supplementary items."
        )
    if not has_raw_data:
        declared_boundaries.append(
            "raw_data_unavailable: No raw experimental/clinical tabular datasets (CSV/TSV/XLSX) are provided; report only text-grounded values."
        )
    if not has_any_pdf:
        declared_boundaries.append(
            "pdf_pages_unavailable: No original PDF files exist in this input package; positions are JATS paragraph IDs or text locators, NEVER synthetic PDF page 1."
        )

    # 3. Parse corpus-manifest.json if present
    manifest_entries: List[Dict[str, Any]] = []
    manifest_files = [p for p in all_files if p.name.lower() in ("corpus-manifest.json", "manifest.json")]
    if manifest_files:
        manifest_entries, m_warns = parse_manifest(manifest_files[0])
        warnings.extend(m_warns)

    # 4. Parse publisher XML files first (for formula/table reconciliation & optional text fallback)
    xml_files = [p for p in all_files if p.suffix.lower() == ".xml"]
    xml_parsed_list = [parse_jats_xml(xp) for xp in xml_files]
    for xp in xml_parsed_list:
        warnings.extend(xp.get("warnings", []))

    # 5. Ingest per-paper material files cleanly without double-counting aggregated material.json
    docs_by_sid: Dict[str, Dict[str, Any]] = {}
    consumed_files: set = set()

    def ensure_doc(sid: str, role: str = "paper") -> Dict[str, Any]:
        if sid not in docs_by_sid:
            docs_by_sid[sid] = {
                "source_id": sid,
                "role": role,
                "doi": "",
                "title": "",
                "authors": [],
                "year": None,
                "journal": None,
                "license": "",
                "source_hash": "",
                "locator_type": "jats_paragraph_id",
                "has_pdf_pages": False,
                "backing_files": [],
                "publisher_xml_files": [],
                "paragraphs": [],
                "paragraph_count": 0,
            }
        return docs_by_sid[sid]

    # Seed from manifest
    for m in manifest_entries:
        d = ensure_doc(m["source_id"], m["role"])
        d["doi"] = m["doi"] or d["doi"]
        d["title"] = m["title"] or d["title"]
        d["authors"] = m["authors"] or d["authors"]
        d["year"] = m["year"] or d["year"]
        d["journal"] = m["journal"] or d["journal"]
        d["license"] = m["license"] or d["license"]
        d["source_hash"] = m["source_hash"] or d["source_hash"]

    def resolve_candidate_path(cand: str) -> Optional[Path]:
        p = Path(cand)
        if p.is_absolute() and p.exists():
            return p
        p2 = input_dir / cand
        if p2.exists():
            return p2
        for f in all_files:
            if f.name == Path(cand).name:
                return f
        return None

    def merge_paragraphs(doc: Dict[str, Any], new_paras: List[Dict[str, Any]], file_rel: str) -> None:
        existing_pids = {p["paragraph_id"] for p in doc["paragraphs"]}
        existing_hashes = {sha256_bytes(norm_key(p["text"]).encode("utf-8")) for p in doc["paragraphs"]}
        for p in new_paras:
            p_copy = dict(p)
            p_copy["source_id"] = doc["source_id"]
            if p_copy["paragraph_id"] in existing_pids:
                continue
            th = sha256_bytes(norm_key(p_copy["text"]).encode("utf-8"))
            if th in existing_hashes and len(p_copy["text"]) > 40:
                continue
            doc["paragraphs"].append(p_copy)
            existing_pids.add(p_copy["paragraph_id"])
            existing_hashes.add(th)
        if file_rel not in doc["backing_files"]:
            doc["backing_files"].append(file_rel)

    # First pass: ingest files explicitly named in manifest OR named primary-material.json / secondary-material.json
    for m in manifest_entries:
        doc = ensure_doc(m["source_id"], m["role"])
        for fc in m["file_candidates"]:
            fp = resolve_candidate_path(fc)
            if not fp or not fp.exists():
                warnings.append(f"Manifest file '{fc}' for source_id '{m['source_id']}' is missing.")
                continue
            if fp.suffix.lower() == ".xml":
                continue
            rel = str(fp.relative_to(input_dir)).replace("\\", "/") if input_dir in fp.parents else fp.name
            if fp.suffix.lower() == ".json":
                obj, err = load_json_safe(fp)
                if err:
                    warnings.append(err)
                    continue
                meta, single_p, multi_p = extract_paragraphs_from_json_obj(obj, m["source_id"])
                if single_p:
                    merge_paragraphs(doc, single_p, rel)
                elif m["source_id"] in multi_p:
                    merge_paragraphs(doc, multi_p[m["source_id"]], rel)
                if not doc["doi"] and meta.get("doi"):
                    doc["doi"] = str(meta["doi"])
                if not doc["title"] and meta.get("title"):
                    doc["title"] = str(meta["title"])
                if not doc["authors"] and isinstance(meta.get("authors"), list):
                    doc["authors"] = meta["authors"]
                if not doc["source_hash"]:
                    doc["source_hash"] = sha256_file(fp)
                consumed_files.add(fp.resolve())
            elif fp.suffix.lower() in (".md", ".txt"):
                meta, single_p = parse_markdown_or_text(fp, m["source_id"])
                merge_paragraphs(doc, single_p, rel)
                doc["locator_type"] = "section_line"
                if not doc["title"] and meta.get("title"):
                    doc["title"] = meta["title"]
                if not doc["doi"] and meta.get("doi"):
                    doc["doi"] = meta["doi"]
                consumed_files.add(fp.resolve())
            elif fp.suffix.lower() == ".pdf":
                meta, single_p, pwarns, has_pdf = parse_pdf_file(fp, m["source_id"])
                warnings.extend(pwarns)
                merge_paragraphs(doc, single_p, rel)
                doc["has_pdf_pages"] = has_pdf
                if has_pdf:
                    doc["locator_type"] = "pdf_page"
                consumed_files.add(fp.resolve())

    # Second pass: ingest primary-material.json / secondary-material.json if not yet consumed
    role_file_order = []
    other_material_files = []
    aggregated_material_files = []

    for fp in all_files:
        if fp.resolve() in consumed_files:
            continue
        lname = fp.name.lower()
        if lname in SKIP_FILENAMES or fp.suffix.lower() in ({".xml", ".yaml", ".yml"} | IMAGE_EXTS | DATA_EXTS):
            continue
        if lname in ("primary-material.json", "secondary-material.json"):
            role_file_order.append(fp)
        elif lname == "material.json":
            aggregated_material_files.append(fp)
        elif fp.suffix.lower() in (".json", ".md", ".txt", ".pdf"):
            other_material_files.append(fp)

    role_file_order.sort(key=lambda x: 0 if "primary" in x.name.lower() else 1)

    def find_matching_manifest_sid(fp: Path, meta: Dict[str, Any], role_hint: str) -> Optional[str]:
        f_doi = norm_doi(meta.get("doi") or meta.get("DOI"))
        f_title = norm_key(str(meta.get("title") or ""))
        f_sid = str(meta.get("source_id") or meta.get("doc_id") or meta.get("paper_id") or "")
        for sid, d in docs_by_sid.items():
            if f_sid and sid == f_sid:
                return sid
            if f_doi and norm_doi(d.get("doi")) == f_doi:
                return sid
            if f_title and norm_key(d.get("title")) and SequenceMatcher(None, f_title, norm_key(d.get("title"))).ratio() > 0.88:
                return sid
            if role_hint and d.get("role", "").lower() == role_hint.lower() and not d["paragraphs"]:
                return sid
        return None

    for fp in role_file_order + other_material_files:
        rel = str(fp.relative_to(input_dir)).replace("\\", "/")
        role_hint = "primary" if "primary" in fp.name.lower() else ("secondary" if "secondary" in fp.name.lower() else "")
        if fp.suffix.lower() == ".json":
            obj, err = load_json_safe(fp)
            if err:
                warnings.append(err)
                continue
            meta, single_p, multi_p = extract_paragraphs_from_json_obj(obj, "")
            if multi_p:
                for sub_sid, plist in multi_p.items():
                    d = ensure_doc(sub_sid, role_hint or sub_sid)
                    merge_paragraphs(d, plist, rel)
                    if not d["source_hash"]:
                        d["source_hash"] = sha256_file(fp)
            else:
                matched_sid = find_matching_manifest_sid(fp, meta, role_hint)
                sid = matched_sid or str(meta.get("source_id") or meta.get("doc_id") or (role_hint or fp.stem))
                d = ensure_doc(sid, role_hint or "paper")
                merge_paragraphs(d, single_p, rel)
                if not d["doi"] and (meta.get("doi") or meta.get("DOI")):
                    d["doi"] = str(meta.get("doi") or meta.get("DOI"))
                if not d["title"] and meta.get("title"):
                    d["title"] = str(meta["title"])
                if not d["authors"] and isinstance(meta.get("authors"), list):
                    d["authors"] = meta["authors"]
                if not d["license"] and meta.get("license"):
                    d["license"] = str(meta["license"])
                if not d["source_hash"]:
                    d["source_hash"] = sha256_file(fp)
            consumed_files.add(fp.resolve())
        elif fp.suffix.lower() in (".md", ".txt"):
            sid = fp.stem
            meta, single_p = parse_markdown_or_text(fp, sid)
            d = ensure_doc(sid, role_hint or "paper")
            merge_paragraphs(d, single_p, rel)
            d["locator_type"] = "section_line"
            if not d["title"] and meta.get("title"):
                d["title"] = meta["title"]
            if not d["doi"] and meta.get("doi"):
                d["doi"] = meta["doi"]
            d["source_hash"] = d["source_hash"] or sha256_file(fp)
            consumed_files.add(fp.resolve())
        elif fp.suffix.lower() == ".pdf":
            sid = fp.stem
            meta, single_p, pwarns, has_pdf = parse_pdf_file(fp, sid)
            warnings.extend(pwarns)
            d = ensure_doc(sid, role_hint or "paper")
            merge_paragraphs(d, single_p, rel)
            d["has_pdf_pages"] = has_pdf
            if has_pdf:
                d["locator_type"] = "pdf_page"
            if not d["title"] and meta.get("title"):
                d["title"] = meta["title"]
            if not d["doi"] and meta.get("doi"):
                d["doi"] = meta["doi"]
            d["source_hash"] = d["source_hash"] or sha256_file(fp)
            consumed_files.add(fp.resolve())

    # Third pass: process aggregated material.json WITHOUT creating a spurious 3rd paper
    for fp in aggregated_material_files:
        rel = str(fp.relative_to(input_dir)).replace("\\", "/")
        obj, err = load_json_safe(fp)
        if err:
            warnings.append(err)
            continue
        meta, single_p, multi_p = extract_paragraphs_from_json_obj(obj, "")
        if multi_p:
            for sub_sid, plist in multi_p.items():
                target_sid = sub_sid
                if target_sid not in docs_by_sid:
                    # Match by role name if sub_sid is 'primary'/'secondary'
                    for existing_sid, edoc in docs_by_sid.items():
                        if edoc.get("role", "").lower() == sub_sid.lower():
                            target_sid = existing_sid
                            break
                d = ensure_doc(target_sid, sub_sid)
                merge_paragraphs(d, plist, rel)
        elif single_p:
            # Check if every paragraph already exists in existing docs
            all_existing_hashes = {
                sha256_bytes(norm_key(p["text"]).encode("utf-8"))
                for d in docs_by_sid.values()
                for p in d["paragraphs"]
            }
            unmatched = [
                p for p in single_p if sha256_bytes(norm_key(p["text"]).encode("utf-8")) not in all_existing_hashes
            ]
            if not docs_by_sid or len(unmatched) > max(2, len(single_p) // 4):
                sid = str(meta.get("source_id") or "primary")
                d = ensure_doc(sid, "primary")
                merge_paragraphs(d, single_p, rel)
            else:
                for d in docs_by_sid.values():
                    if rel not in d["backing_files"]:
                        d["backing_files"].append(rel)

    # 6. Link publisher XML files to their target paper (or add as paper if standalone XML)
    xml_reconciliations_out: List[Dict[str, Any]] = []
    for xp in xml_parsed_list:
        x_doi = norm_doi(xp.get("doi"))
        x_title = norm_key(xp.get("title"))
        x_name = xp["file"].lower()
        matched_sid = None
        for sid, d in docs_by_sid.items():
            if x_doi and norm_doi(d.get("doi")) == x_doi:
                matched_sid = sid
                break
            if x_title and norm_key(d.get("title")) and SequenceMatcher(None, x_title, norm_key(d.get("title"))).ratio() > 0.85:
                matched_sid = sid
                break
        if not matched_sid:
            for sid, d in docs_by_sid.items():
                if "secondary" in x_name and d.get("role") == "secondary":
                    matched_sid = sid
                    break
                if "primary" in x_name and d.get("role") == "primary":
                    matched_sid = sid
                    break
        if not matched_sid and not docs_by_sid and xp["paragraphs"]:
            matched_sid = Path(xp["file"]).stem
            d = ensure_doc(matched_sid, "primary")
            d["doi"] = xp["doi"]
            d["title"] = xp["title"]
            d["authors"] = xp["authors"]
            merge_paragraphs(d, xp["paragraphs"], xp["file"])
        elif matched_sid:
            d = docs_by_sid[matched_sid]
            if xp["file"] not in d["publisher_xml_files"]:
                d["publisher_xml_files"].append(xp["file"])
            if not d["doi"] and xp["doi"]:
                d["doi"] = xp["doi"]
            if not d["title"] and xp["title"]:
                d["title"] = xp["title"]
            if not d["authors"] and xp["authors"]:
                d["authors"] = xp["authors"]
            # If the JSON paper had 0 paragraphs, fall back to XML paragraphs
            if not d["paragraphs"] and xp["paragraphs"]:
                merge_paragraphs(d, xp["paragraphs"], xp["file"])

        xml_reconciliations_out.append(
            {
                "xml_file": xp["file"],
                "linked_source_id": matched_sid,
                "xml_doi": xp["doi"],
                "xml_title": xp["title"],
                "formula_paragraph_count": len(xp["formula_reconciliations"]),
                "table_count": len(xp["tables"]),
                "formula_reconciliations": xp["formula_reconciliations"],
                "tables": xp["tables"],
            }
        )

    # 7. Root metadata isolation check (case.json DOI/title/authors belong ONLY to primary!)
    root_doi = norm_doi(case_data.get("doi") or case_data.get("DOI"))
    root_title = str(case_data.get("title") or "")
    root_authors = case_data.get("authors") or []
    primary_sid = None
    for sid, d in docs_by_sid.items():
        if d.get("role") == "primary":
            primary_sid = sid
            break
    if not primary_sid and docs_by_sid:
        primary_sid = next(iter(docs_by_sid.keys()))

    # Only fill missing metadata on primary paper from case.json root — NEVER on secondary!
    if primary_sid:
        pdoc = docs_by_sid[primary_sid]
        if not pdoc["doi"] and root_doi:
            pdoc["doi"] = str(case_data.get("doi") or case_data.get("DOI"))
        if not pdoc["title"] and root_title:
            pdoc["title"] = root_title
        if not pdoc["authors"] and isinstance(root_authors, list) and root_authors:
            pdoc["authors"] = root_authors

    root_metadata_isolation = {
        "case_root_doi": case_data.get("doi") or case_data.get("DOI") or None,
        "case_root_title": root_title or None,
        "case_root_authors": root_authors or [],
        "applies_exclusively_to_source_id": primary_sid,
        "rule": (
            f"Root case.json DOI/title/authors apply ONLY to primary paper ({primary_sid}); "
            "NEVER copy or attribute them to secondary or subsequent papers."
        ),
    }

    # 8. Same-paper multi-format duplicate detection (assign paper_group_id)
    doc_list = list(docs_by_sid.values())
    shingle_map = {
        d["source_id"]: text_shingles(" ".join(p["text"] for p in d["paragraphs"])) for d in doc_list
    }
    group_counter = 0
    assigned_groups: Dict[str, str] = {}
    group_members: Dict[str, List[str]] = defaultdict(list)

    def format_priority(doc: Dict[str, Any]) -> int:
        files = " ".join(doc.get("backing_files", [])).lower()
        if ".json" in files:
            return 0
        if ".xml" in files:
            return 1
        if ".md" in files:
            return 2
        if ".txt" in files:
            return 3
        return 4

    for i, d1 in enumerate(doc_list):
        sid1 = d1["source_id"]
        if sid1 in assigned_groups:
            continue
        group_counter += 1
        gid = f"PG{group_counter:02d}"
        assigned_groups[sid1] = gid
        group_members[gid].append(sid1)
        doi1 = norm_doi(d1.get("doi"))
        title1 = norm_key(d1.get("title"))
        for j in range(i + 1, len(doc_list)):
            d2 = doc_list[j]
            sid2 = d2["source_id"]
            if sid2 in assigned_groups:
                continue
            doi2 = norm_doi(d2.get("doi"))
            title2 = norm_key(d2.get("title"))
            same = False
            reason = ""
            if doi1 and doi2 and doi1 == doi2:
                same = True
                reason = f"identical DOI ({doi1})"
            elif title1 and title2 and len(title1) > 15 and SequenceMatcher(None, title1, title2).ratio() > 0.90:
                same = True
                reason = "identical normalized title (>0.90)"
            elif jaccard(shingle_map[sid1], shingle_map[sid2]) > 0.65:
                same = True
                reason = "high text shingle overlap (>0.65)"
            if same:
                assigned_groups[sid2] = gid
                group_members[gid].append(sid2)
                d2["duplicate_reason"] = reason

    duplicate_format_groups: List[Dict[str, Any]] = []
    for gid, sids in group_members.items():
        ranked = sorted(sids, key=lambda s: format_priority(docs_by_sid[s]))
        canon = ranked[0]
        for s in ranked:
            docs_by_sid[s]["paper_group_id"] = gid
            docs_by_sid[s]["is_canonical_in_group"] = s == canon
            docs_by_sid[s]["format_duplicate_of"] = None if s == canon else canon
            docs_by_sid[s]["paragraph_count"] = len(docs_by_sid[s]["paragraphs"])
        if len(ranked) > 1:
            duplicate_format_groups.append(
                {
                    "paper_group_id": gid,
                    "canonical_source_id": canon,
                    "duplicate_source_ids": ranked[1:],
                    "rule": "Multiple file formats of the same paper do NOT constitute cross-paper evidence.",
                }
            )
            warnings.append(
                f"Detected same-paper multi-format group {gid}: canonical={canon}, duplicates={ranked[1:]}. "
                "Do NOT treat them as independent cross-paper evidence."
            )

    canonical_docs = [d for d in doc_list if d.get("is_canonical_in_group", True)]
    has_any_real_pdf_pages = any(d.get("has_pdf_pages", False) for d in canonical_docs)

    if len(canonical_docs) == 0:
        workflow_mode = "degraded_missing_inputs"
        warnings.append("No readable source documents were discovered under input directory.")
    elif len(canonical_docs) == 1:
        workflow_mode = "single_paper"
    elif len(canonical_docs) == 2:
        workflow_mode = "two_paper_incremental"
    else:
        workflow_mode = "multi_paper_incremental"

    corpus_index = {
        "schema_version": "1.0.0",
        "case_id": case_data.get("case_id") or input_dir.name,
        "input_dir": str(input_dir),
        "discovered_files": rel_files,
        "workflow_mode": workflow_mode,
        "unique_paper_count": len(canonical_docs),
        "has_pdf_pages": has_any_real_pdf_pages,
        "root_metadata_isolation": root_metadata_isolation,
        "duplicate_format_groups": duplicate_format_groups,
        "declared_input_boundaries": declared_boundaries,
        "warnings": warnings,
        "documents": list(docs_by_sid.values()),
    }

    case_brief = {
        "case_id": corpus_index["case_id"],
        "case_json_found": bool(case_path and case_path.exists()),
        "task_md_found": bool(task_text),
        "workflow_mode": workflow_mode,
        "unique_paper_count": len(canonical_docs),
        "canonical_source_ids": [d["source_id"] for d in canonical_docs],
        "has_pdf_pages": has_any_real_pdf_pages,
        "required_output_contract_fields": required_fields,
        "output_contract_raw": contract_raw,
        "case_questions": case_questions,
        "declared_input_boundaries": declared_boundaries,
        "root_metadata_isolation": root_metadata_isolation,
        "warnings": warnings,
        "xml_reconciliation_files": [x["xml_file"] for x in xml_reconciliations_out],
        "task_excerpt": task_text[:1800] if task_text else "",
    }

    (out_dir / "corpus_index.json").write_text(
        json.dumps(corpus_index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "case_brief.json").write_text(
        json.dumps(case_brief, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "xml_math_reconciliation.json").write_text(
        json.dumps({"xml_files": xml_reconciliations_out}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Build Agent-friendly corpus_digest.md for low-token, high-accuracy extraction
    xml_formula_lookup: Dict[Tuple[str, str], str] = {}
    for xr in xml_reconciliations_out:
        lsid = xr.get("linked_source_id") or ""
        for fr in xr.get("formula_reconciliations", []):
            xml_formula_lookup[(lsid, fr["paragraph_id"])] = fr["xml_structured_text"]

    digest_lines = [
        f"# Corpus Reading Digest — `{corpus_index['case_id']}`",
        "",
        f"- **Workflow Mode**: `{workflow_mode}` | **Unique Papers**: `{len(canonical_docs)}` | **Real PDF Pages Present**: `{has_any_real_pdf_pages}`",
        f"- **Root Metadata Isolation**: {root_metadata_isolation['rule']}",
    ]
    for b in declared_boundaries:
        digest_lines.append(f"- **Boundary**: {b}")
    if warnings:
        digest_lines.append("- **Warnings**:")
        for w in warnings:
            digest_lines.append(f"  - {w}")
    digest_lines.append("")

    for doc in canonical_docs:
        doc_lines = [
            "---",
            f"## Source `{doc['source_id']}` (role=`{doc['role']}`, group=`{doc['paper_group_id']}`, locator=`{doc['locator_type']}`)",
            f"- **Title**: {doc['title'] or '(unreported)'}",
            f"- **DOI**: {doc['doi'] or '(unreported)'}",
            f"- **Authors**: {', '.join(doc['authors']) if doc['authors'] else '(unreported)'}",
            f"- **License**: {doc['license'] or '(unreported)'}",
            f"- **Backing Files**: {', '.join(doc['backing_files'])}",
            f"- **Publisher XML**: {', '.join(doc['publisher_xml_files']) if doc['publisher_xml_files'] else 'none'}",
            f"- **Paragraph Count**: {doc['paragraph_count']}",
            "",
        ]
        for p in doc["paragraphs"]:
            sec = f" | {p['section']}" if p.get("section") else ""
            doc_lines.append(f"### [{doc['source_id']} | {p['paragraph_id']}{sec}]")
            doc_lines.append(p["text"])
            xml_note = xml_formula_lookup.get((doc["source_id"], p["paragraph_id"]))
            if xml_note and xml_note != p["text"]:
                doc_lines.append(f"> **XML Formula/Subscript Check**: {xml_note}")
            doc_lines.append("")

        # Append any XML tables linked to this source
        for xr in xml_reconciliations_out:
            if xr.get("linked_source_id") == doc["source_id"] and xr.get("tables"):
                doc_lines.append(f"### XML Tables for `{doc['source_id']}` ({xr['xml_file']})")
                for tbl in xr["tables"]:
                    doc_lines.append(f"- **{tbl['table_id']} ({tbl['label']})**: {tbl['caption']}")
                    for row in tbl["rows"][:15]:
                        doc_lines.append("  - | " + " | ".join(row) + " |")
                doc_lines.append("")

        digest_lines.extend(doc_lines)
        safe_sid = re.sub(r"[^A-Za-z0-9_.-]+", "_", doc["source_id"])
        (out_dir / f"digest_{safe_sid}.md").write_text("\n".join(doc_lines), encoding="utf-8")

    (out_dir / "corpus_digest.md").write_text("\n".join(digest_lines), encoding="utf-8")

    return case_brief


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect multi-paper corpus inputs, isolate per-paper metadata, detect format duplicates, and check JATS XML formulas."
    )
    parser.add_argument("--input-dir", default="/input", help="Input directory containing case.json and materials")
    parser.add_argument("--case", default=None, help="Optional explicit path to case.json (defaults to <input-dir>/case.json)")
    parser.add_argument("--task", default=None, help="Optional explicit path to task.md (defaults to <input-dir>/task.md)")
    parser.add_argument("--out-dir", default="/app/work/inspect", help="Output directory for index, brief, XML check, and digest")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    case_path = Path(args.case) if args.case else (input_dir / "case.json")
    task_path = Path(args.task) if args.task else (input_dir / "task.md")
    out_dir = Path(args.out_dir)

    brief = inspect_corpus(input_dir, case_path, task_path, out_dir)
    print(json.dumps(brief, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
