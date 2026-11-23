#!/usr/bin/env python3
"""Shared utilities for academic-citation-manager."""

from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SKILL_ROOT = Path(__file__).resolve().parent.parent
RESOURCES_DIR = SKILL_ROOT / "resources"

DOI_REGEX = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$")


def posix_rel(path: Path, base: Path) -> str:
    """Return normalized POSIX relative path."""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def load_json_safe(path: Path, default: Any = None) -> Tuple[Any, Optional[str]]:
    """Load JSON safely returning (data, error_or_none)."""
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
    """Save UTF-8 JSON with LF line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def save_text(path: Path, text: str) -> None:
    """Save UTF-8 text normalized to LF line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(norm)


def load_resource_json(filename: str) -> Dict[str, Any]:
    """Load bundled JSON resource from resources/."""
    data, _ = load_json_safe(RESOURCES_DIR / filename, default={})
    return data if isinstance(data, dict) else {}


def read_text_safe(path: Path) -> Tuple[Optional[str], str, Optional[str]]:
    """Read text across common encodings with honest warnings."""
    if not path.exists():
        return None, "missing", f"File does not exist: {path}"
    try:
        raw = path.read_bytes()
    except PermissionError as exc:
        return None, "permission_denied", f"Permission denied reading {path.name}: {exc}"
    except OSError as exc:
        return None, "os_error", f"OS error reading {path.name}: {exc}"

    if b"\x00" in raw[:4096]:
        return None, "binary", f"File appears binary: {path.name}"

    for enc in ("utf-8-sig", "utf-8", "gb18030", "cp1252"):
        try:
            text = raw.decode(enc)
            warn = None if enc in ("utf-8", "utf-8-sig") else f"Decoded {path.name} using fallback encoding {enc}"
            return text.replace("\r\n", "\n").replace("\r", "\n"), enc, warn
        except UnicodeDecodeError:
            continue

    text = raw.decode("latin-1", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n"), "latin-1-replace", f"Lossy latin-1 decode on {path.name}"


def extract_docx_paragraphs(path: Path) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """Extract paragraphs and table text from .docx via python-docx or stdlib XML."""
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
        return [], f"DOCX parse error on {path.name}: {exc}"

    try:
        with zipfile.ZipFile(path) as zf:
            if "word/document.xml" not in zf.namelist():
                return [], f"Corrupt DOCX (missing word/document.xml): {path.name}"
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        items = []
        for idx, p in enumerate(root.findall(".//w:body/w:p", ns)):
            joined = "".join(t.text for t in p.findall(".//w:t", ns) if t.text).strip()
            if joined:
                pstyle = p.find("./w:pPr/w:pStyle", ns)
                style = pstyle.attrib.get(f"{{{ns['w']}}}val", "Normal") if pstyle is not None else "Normal"
                kind = "heading" if "heading" in style.lower() else "paragraph"
                items.append({"kind": kind, "style": style, "text": joined, "loc": f"xml_para_{idx+1}"})
        return items, f"Extracted {path.name} via stdlib XML fallback."
    except Exception as exc:
        return [], f"Failed to parse DOCX {path.name}: {exc}"


def extract_pdf_text(path: Path) -> Tuple[str, Optional[str]]:
    """Extract text layer from PDF via pymupdf, pypdf, or pdftotext."""
    for mod_name in ("fitz", "pypdf", "PyPDF2"):
        try:
            mod = __import__(mod_name)
            if mod_name == "fitz":
                doc = mod.open(str(path))
                if doc.is_encrypted and not doc.authenticate(""):
                    return "", f"Encrypted PDF: {path.name}"
                txt = "\n\n".join((p.get_text("text") or "").strip() for p in doc).strip()
                if len(txt) < 40 and len(doc) > 0:
                    return txt, f"PDF {path.name} appears scanned/image-only."
                return txt, None
            reader = mod.PdfReader(str(path))
            if getattr(reader, "is_encrypted", False) and not reader.decrypt(""):
                return "", f"Encrypted PDF: {path.name}"
            txt = "\n\n".join((p.extract_text() or "").strip() for p in reader.pages).strip()
            if len(txt) < 40 and len(reader.pages) > 0:
                return txt, f"PDF {path.name} appears scanned/image-only."
            return txt, None
        except ImportError:
            continue
        except Exception as exc:
            return "", f"PDF parse error ({mod_name}) on {path.name}: {exc}"

    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            proc = subprocess.run([pdftotext, "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True, timeout=60)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip(), None
        except Exception as exc:
            return "", f"pdftotext failed on {path.name}: {exc}"
    return "", f"No PDF extractor available for {path.name}."


def read_document_any(path: Path) -> Tuple[str, str, Optional[str]]:
    """Read manuscript file into plain text."""
    ext = path.suffix.lower()
    if ext == ".docx":
        items, warn = extract_docx_paragraphs(path)
        if not items:
            return "", "docx", warn or f"Empty/corrupt DOCX: {path.name}"
        lines = [f"## {it['text']}" if it["kind"] == "heading" else it["text"] for it in items]
        return "\n\n".join(lines), "docx", warn
    if ext == ".pdf":
        txt, warn = extract_pdf_text(path)
        return txt, "pdf", warn
    txt, enc, warn = read_text_safe(path)
    return txt or "", enc, warn


def has_cjk(text: str) -> bool:
    """Return True if text contains CJK Unified Ideographs."""
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def strip_latex_markup(s: str) -> str:
    """Clean common BibTeX braces and LaTeX diacritics for matching/display."""
    out = s or ""
    out = re.sub(r"\\['\"`^~=.uvHcdbk]\{?([A-Za-z])\}?", r"\1", out)
    out = out.replace("{", "").replace("}", "")
    out = re.sub(r"\s+", " ", out).strip()
    return out


def normalize_title(title: str) -> str:
    """Normalize title for deduplication (NFKD fold, lowercase, strip punctuation)."""
    s = strip_latex_markup(title)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_similarity(t1: str, t2: str) -> float:
    """Compute similarity in [0, 1] between two normalized titles."""
    n1, n2 = normalize_title(t1), normalize_title(t2)
    if not n1 or not n2:
        return 0.0
    if n1 == n2:
        return 1.0
    # Combine character sequence ratio and token Jaccard for robustness
    seq = difflib.SequenceMatcher(None, n1, n2, autojunk=False).ratio()
    toks1 = set(n1.split())
    toks2 = set(n2.split())
    jacc = len(toks1 & toks2) / max(1, len(toks1 | toks2))
    return round(0.65 * seq + 0.35 * jacc, 4)


def normalize_doi(raw: str) -> str:
    """Extract and lowercase canonical DOI if present."""
    if not raw:
        return ""
    s = raw.strip()
    s = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^doi:\s*", "", s, flags=re.IGNORECASE)
    m = re.search(r"10\.\d{4,9}/[^\s\"'<>\]\}]+", s)
    if not m:
        return ""
    return m.group(0).rstrip(".,;)").lower()


def validate_doi_syntax(doi: str) -> bool:
    """Return True if normalized DOI passes RFC/Crossref structural syntax."""
    return bool(doi and DOI_REGEX.match(doi))


def extract_arxiv_id(text: str) -> str:
    """Extract canonical arXiv identifier if present (without version suffix vN)."""
    if not text:
        return ""
    m = re.search(r"arxiv[:\s/]*(\d{4}\.\d{4,5})(?:v\d+)?", text, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    m2 = re.search(r"\b(\d{4}\.\d{4,5})(?:v\d+)?\b", text)
    if m2 and "arxiv" in text.lower():
        return m2.group(1)
    return ""


def split_authors_list(raw: str) -> List[str]:
    """Split author string (BibTeX 'and', RIS/CNKI ';', ',', '&') into normalized list."""
    if not raw:
        return []
    cleaned = strip_latex_markup(raw).strip()
    if not cleaned:
        return []
    # BibTeX uses ' and '
    if re.search(r"\s+and\s+", cleaned, flags=re.IGNORECASE):
        parts = re.split(r"\s+and\s+", cleaned, flags=re.IGNORECASE)
    elif ";" in cleaned or "；" in cleaned:
        parts = re.split(r"[;；]", cleaned)
    elif "、" in cleaned:
        parts = cleaned.split("、")
    elif has_cjk(cleaned) and ("，" in cleaned or "," in cleaned):
        parts = re.split(r"[,，]", cleaned)
    else:
        # Handle "Last1, F., Last2, F." vs "First1 Last1, First2 Last2"
        comma_segs = [p.strip() for p in cleaned.split(",") if p.strip()]
        if len(comma_segs) == 2 and len(comma_segs[1].split()) <= 2 and len(comma_segs[0].split()) == 1:
            parts = [cleaned]
        elif len(comma_segs) >= 4 and len(comma_segs) % 2 == 0 and all(len(comma_segs[i].split()) <= 2 for i in range(1, len(comma_segs), 2)):
            parts = [f"{comma_segs[i]}, {comma_segs[i+1]}" for i in range(0, len(comma_segs), 2)]
        else:
            parts = comma_segs
    return [p.strip() for p in parts if p.strip() and p.strip().lower() not in ("et al", "et al.", "等")]


def parse_author_name(name: str) -> Dict[str, str]:
    """Parse an author string into family, given, initials, and full."""
    s = strip_latex_markup(name).strip()
    if has_cjk(s):
        return {"family": s, "given": "", "initials": "", "full": s, "is_cjk": True}  # type: ignore[return-value]
    if "," in s:
        fam, giv = [x.strip() for x in s.split(",", 1)]
    else:
        toks = s.split()
        if len(toks) == 1:
            fam, giv = toks[0], ""
        else:
            fam, giv = toks[-1], " ".join(toks[:-1])
    initials_list = [w[0].upper() for w in re.split(r"[\s.-]+", giv) if w and w[0].isalpha()]
    return {
        "family": fam,
        "given": giv,
        "initials": " ".join(initials_list),
        "initials_compact": "".join(initials_list),
        "full": s,
        "is_cjk": False,  # type: ignore[dict-item]
    }


def first_author_surname(authors: List[str]) -> str:
    """Return lowercase normalized first-author surname."""
    if not authors:
        return ""
    parsed = parse_author_name(authors[0])
    return re.sub(r"[^\w\u4e00-\u9fff]", "", parsed["family"].lower())
