#!/usr/bin/env python3
"""Shared utilities for the academic-language-polisher skill."""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SKILL_ROOT = Path(__file__).resolve().parent.parent
RESOURCES_DIR = SKILL_ROOT / "resources"

EDIT_TYPES = (
    "unchanged",
    "grammar_syntax",
    "article_preposition",
    "tense_voice",
    "punctuation_mechanics",
    "terminology_consistency",
    "abbreviation_expansion",
    "academic_register",
    "chinglish_translationese",
    "sentence_splitting_flow",
    "bilingual_alignment",
)


def posix_rel(path: Path, base: Path) -> str:
    """Return a normalized POSIX relative path (fallback to string path)."""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def load_json_safe(path: Path, default: Any = None) -> Tuple[Any, Optional[str]]:
    """Load JSON safely, returning (data, error_message_or_none)."""
    if not path.exists():
        return default, f"File not found: {path}"
    text, enc, warn = read_text_safe(path)
    if text is None:
        return default, warn or f"Unable to read {path}"
    try:
        return json.loads(text), warn
    except Exception as exc:
        return default, f"Invalid JSON in {path.name} ({enc}): {exc}"


def save_json(path: Path, data: Any) -> None:
    """Write UTF-8 JSON with LF line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def save_text(path: Path, text: str) -> None:
    """Write UTF-8 text normalized to LF line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(normalized)


def load_resource_json(filename: str) -> Dict[str, Any]:
    """Load a bundled JSON file from resources/."""
    data, err = load_json_safe(RESOURCES_DIR / filename, default={})
    if not isinstance(data, dict):
        return {}
    return data


def read_text_safe(path: Path) -> Tuple[Optional[str], str, Optional[str]]:
    """Read a text file across common encodings with honest warnings."""
    if not path.exists():
        return None, "missing", f"File does not exist: {path}"
    try:
        raw = path.read_bytes()
    except PermissionError as exc:
        return None, "permission_denied", f"Permission denied reading {path.name}: {exc}"
    except OSError as exc:
        return None, "os_error", f"OS error reading {path.name}: {exc}"

    if b"\x00" in raw[:4096]:
        return None, "binary", f"File appears to be binary (contains NUL bytes): {path.name}"

    for enc in ("utf-8-sig", "utf-8", "gb18030", "cp1252"):
        try:
            text = raw.decode(enc)
            warn = None if enc in ("utf-8", "utf-8-sig") else f"Decoded {path.name} using fallback encoding {enc}"
            return text.replace("\r\n", "\n").replace("\r", "\n"), enc, warn
        except UnicodeDecodeError:
            continue

    text = raw.decode("latin-1", errors="replace")
    return (
        text.replace("\r\n", "\n").replace("\r", "\n"),
        "latin-1-replace",
        f"Decoded {path.name} with lossy latin-1 replacement; verify non-ASCII characters.",
    )


def extract_docx_paragraphs(path: Path) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """Extract ordered paragraphs/table text from .docx using python-docx or stdlib XML."""
    try:
        import docx  # type: ignore

        doc = docx.Document(str(path))
        items: List[Dict[str, str]] = []
        for idx, para in enumerate(doc.paragraphs):
            txt = para.text.strip()
            if txt:
                style = para.style.name if para.style else "Normal"
                items.append({"kind": "heading" if "Heading" in style else "paragraph", "style": style, "text": txt, "loc": f"para_{idx+1}"})
        for t_idx, tbl in enumerate(doc.tables):
            for r_idx, row in enumerate(tbl.rows):
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                if any(cells):
                    items.append({"kind": "table_row", "style": "Table", "text": " | ".join(cells), "loc": f"table_{t_idx+1}_row_{r_idx+1}"})
        return items, None
    except ImportError:
        pass
    except Exception as exc:
        return [], f"Failed to parse DOCX {path.name} with python-docx: {exc}"

    # Stdlib zipfile + ElementTree fallback
    try:
        with zipfile.ZipFile(path) as zf:
            if "word/document.xml" not in zf.namelist():
                return [], f"Corrupt DOCX (missing word/document.xml): {path.name}"
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        items = []
        for idx, p in enumerate(root.findall(".//w:body/w:p", ns)):
            texts = [t.text for t in p.findall(".//w:t", ns) if t.text]
            joined = "".join(texts).strip()
            if joined:
                pstyle_el = p.find("./w:pPr/w:pStyle", ns)
                style = pstyle_el.attrib.get(f"{{{ns['w']}}}val", "Normal") if pstyle_el is not None else "Normal"
                kind = "heading" if "heading" in style.lower() or re.match(r"^h\d$", style.lower()) else "paragraph"
                items.append({"kind": kind, "style": style, "text": joined, "loc": f"xml_para_{idx+1}"})
        return items, f"Extracted DOCX {path.name} via stdlib XML fallback (tables/styles simplified)."
    except Exception as exc:
        return [], f"Failed to parse DOCX {path.name}: {exc}"


def extract_pdf_text(path: Path) -> Tuple[str, Dict[str, Any], Optional[str]]:
    """Extract text from PDF via pymupdf, pypdf, or pdftotext CLI without fabricating content."""
    meta: Dict[str, Any] = {"pages": 0, "method": "none", "encrypted": False, "ocr_needed": False}

    # Try PyMuPDF (fitz)
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(path))
        meta["pages"] = len(doc)
        if doc.is_encrypted and not doc.authenticate(""):
            meta["encrypted"] = True
            return "", meta, f"PDF is password-protected/encrypted: {path.name}"
        pages_text = []
        for i, page in enumerate(doc):
            t = page.get_text("text") or ""
            pages_text.append(f"<!-- PAGE {i+1} -->\n{t.strip()}")
        combined = "\n\n".join(pages_text).strip()
        meta["method"] = "pymupdf"
        if len(re.sub(r"<!-- PAGE \d+ -->|\s+", "", combined)) < 40 and len(doc) > 0:
            meta["ocr_needed"] = True
            return combined, meta, f"PDF {path.name} appears scanned or image-only (minimal extractable text)."
        return combined, meta, None
    except ImportError:
        pass
    except Exception as exc:
        warn_fitz = str(exc)
    else:
        warn_fitz = ""

    # Try pypdf / PyPDF2
    for mod_name in ("pypdf", "PyPDF2"):
        try:
            mod = __import__(mod_name)
            reader = mod.PdfReader(str(path))
            meta["pages"] = len(reader.pages)
            if getattr(reader, "is_encrypted", False):
                try:
                    ok = reader.decrypt("")
                    if not ok:
                        meta["encrypted"] = True
                        return "", meta, f"PDF is encrypted: {path.name}"
                except Exception:
                    meta["encrypted"] = True
                    return "", meta, f"PDF is encrypted: {path.name}"
            pages_text = []
            for i, p in enumerate(reader.pages):
                pages_text.append(f"<!-- PAGE {i+1} -->\n{(p.extract_text() or '').strip()}")
            combined = "\n\n".join(pages_text).strip()
            meta["method"] = mod_name
            if len(re.sub(r"<!-- PAGE \d+ -->|\s+", "", combined)) < 40 and len(reader.pages) > 0:
                meta["ocr_needed"] = True
                return combined, meta, f"PDF {path.name} has negligible extractable text (possibly scanned)."
            return combined, meta, None
        except ImportError:
            continue
        except Exception:
            continue

    # Try pdftotext CLI
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            proc = subprocess.run([pdftotext, "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True, timeout=60)
            if proc.returncode == 0 and proc.stdout.strip():
                meta["method"] = "pdftotext"
                return proc.stdout.strip(), meta, None
            return "", meta, f"pdftotext failed on {path.name}: {proc.stderr.strip()}"
        except Exception as exc:
            return "", meta, f"pdftotext execution error on {path.name}: {exc}"

    return "", meta, f"No PDF text extractor available (pymupdf/pypdf/pdftotext missing) for {path.name}. {warn_fitz}".strip()


def read_document_any(path: Path) -> Tuple[str, str, Optional[str]]:
    """Read txt/md/tex/docx/pdf/json/csv into plain text with provenance note."""
    ext = path.suffix.lower()
    if ext == ".docx":
        paras, warn = extract_docx_paragraphs(path)
        if not paras:
            return "", "docx", warn or f"Empty or unreadable DOCX: {path.name}"
        lines = []
        for p in paras:
            if p["kind"] == "heading":
                lines.append(f"## {p['text']}")
            else:
                lines.append(p["text"])
        return "\n\n".join(lines), "docx", warn
    if ext == ".pdf":
        txt, meta, warn = extract_pdf_text(path)
        return txt, "pdf", warn
    text, enc, warn = read_text_safe(path)
    return text or "", enc, warn


def inline_word_diff(original: str, polished: str) -> str:
    """Create a human-readable Markdown inline word/character diff."""
    if original == polished:
        return polished
    # Use word tokens when mostly Latin, char-aware tokens when CJK present
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", original))
    if cjk_count > max(3, len(original) * 0.2):
        tok_a = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_.-]+|\s+|[^\w\s]", original)
        tok_b = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_.-]+|\s+|[^\w\s]", polished)
    else:
        tok_a = re.findall(r"\S+|\s+", original)
        tok_b = re.findall(r"\S+|\s+", polished)

    sm = difflib.SequenceMatcher(None, tok_a, tok_b, autojunk=False)
    out: List[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        a_seg = "".join(tok_a[i1:i2])
        b_seg = "".join(tok_b[j1:j2])
        if tag == "equal":
            out.append(a_seg)
        elif tag == "delete":
            if a_seg.strip():
                out.append(f"~~{a_seg}~~")
        elif tag == "insert":
            if b_seg.strip():
                out.append(f"**{b_seg}**")
            else:
                out.append(b_seg)
        elif tag == "replace":
            out.append(f"~~{a_seg}~~ **{b_seg}**")
    return "".join(out)


def normalized_edit_distance_ratio(a: str, b: str) -> float:
    """Return 1 - SequenceMatcher similarity ratio in [0.0, 1.0]."""
    if a == b:
        return 0.0
    if not a and not b:
        return 0.0
    return round(1.0 - difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 4)
