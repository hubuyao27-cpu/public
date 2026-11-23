#!/usr/bin/env python3
"""Shared utilities for sci-intro-rw-writer.

Pure Python standard library at core, with optional graceful acceleration for
PDF parsing (pypdf / fitz / pdfplumber / pdftotext), OCR (pdftoppm + tesseract),
and DOCX parsing (python-docx with pure-stdlib zipfile XML fallback).
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def load_json_safe(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    if not path.exists():
        return None, f"file_not_found:{path}"
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            return None, f"encoding_error:{exc}"
    except PermissionError as exc:
        return None, f"permission_denied:{exc}"
    except Exception as exc:
        return None, f"read_error:{exc}"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, f"json_decode_error:{exc}"


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def read_text_safe(path: Path) -> Tuple[Optional[str], Optional[str]]:
    if not path.exists():
        return None, f"file_not_found:{path}"
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=enc), None
        except UnicodeDecodeError:
            continue
        except PermissionError as exc:
            return None, f"permission_denied:{exc}"
        except Exception as exc:
            return None, f"read_error:{exc}"
    return None, f"decode_failed:{path}"


def posix_rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path.name


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def extract_docx_text(path: Path) -> Tuple[List[str], Optional[str]]:
    """Extract paragraph + table text lines from DOCX (python-docx or stdlib XML)."""
    try:
        import docx  # type: ignore

        doc = docx.Document(str(path))
        lines: List[str] = []
        for p in doc.paragraphs:
            if p.text.strip():
                lines.append(p.text.strip())
        for tbl in doc.tables:
            for row in tbl.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
        return lines, None
    except ImportError:
        pass
    except Exception as exc:
        return [], f"docx_read_error:{exc}"

    # Pure-stdlib XML fallback
    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                return [], "docx_missing_document_xml"
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        lines = []
        for p in root.findall(".//w:p", ns):
            texts = [t.text for t in p.findall(".//w:t", ns) if t.text]
            joined = "".join(texts).strip()
            if joined:
                lines.append(joined)
        return lines, None
    except zipfile.BadZipFile:
        return [], "docx_corrupt_archive"
    except Exception as exc:
        return [], f"docx_xml_error:{exc}"


def _pdf_ocr_pages(path: Path, page_nums: List[int]) -> Dict[int, str]:
    """Optional OCR fallback via pdftoppm + tesseract for scanned PDF pages."""
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        return {}
    results: Dict[int, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for pno in page_nums[:6]:
            prefix = tmp_dir / f"page_{pno}"
            res = subprocess.run(
                ["pdftoppm", "-png", "-r", "200", "-f", str(pno), "-l", str(pno), str(path), str(prefix)],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                continue
            pngs = sorted(tmp_dir.glob(f"page_{pno}*.png"))
            if not pngs:
                continue
            ocr = subprocess.run(
                ["tesseract", str(pngs[0]), "stdout"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if ocr.returncode == 0 and ocr.stdout.strip():
                results[pno] = ocr.stdout.strip()
    return results


def extract_pdf_text(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Extract per-page text from PDF with graceful fallback and error flags.

    Returns: (pages=[{"page": 1, "text": "...", "method": "..."}], warnings=[...])
    """
    warnings: List[str] = []
    if not path.exists():
        return [], [f"file_not_found:{path}"]
    try:
        header = path.read_bytes()[:1024]
    except Exception as exc:
        return [], [f"read_error:{exc}"]

    if not header.lstrip().startswith(b"%PDF"):
        return [], [f"pdf_corrupt:missing_pdf_header:{path.name}"]

    pages: List[Dict[str, Any]] = []

    # 1. pypdf
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            try:
                if reader.decrypt("") == 0:
                    return [], [f"pdf_encrypted:{path.name}"]
            except Exception:
                return [], [f"pdf_encrypted:{path.name}"]
        for i, page in enumerate(reader.pages, start=1):
            txt = page.extract_text() or ""
            pages.append({"page": i, "text": txt, "method": "pypdf"})
    except ImportError:
        pass
    except Exception as exc:
        msg = str(exc).lower()
        if "encrypt" in msg or "password" in msg:
            return [], [f"pdf_encrypted:{path.name}"]
        warnings.append(f"pypdf_failed:{exc}")
        pages = []

    # 2. fitz (PyMuPDF)
    if not pages:
        try:
            import fitz  # type: ignore

            doc = fitz.open(str(path))
            if getattr(doc, "needs_pass", False):
                return [], [f"pdf_encrypted:{path.name}"]
            for i, page in enumerate(doc, start=1):
                pages.append({"page": i, "text": page.get_text("text") or "", "method": "pymupdf"})
            doc.close()
        except ImportError:
            pass
        except Exception as exc:
            warnings.append(f"pymupdf_failed:{exc}")
            pages = []

    # 3. pdfplumber
    if not pages:
        try:
            import pdfplumber  # type: ignore

            with pdfplumber.open(str(path)) as pdf:
                for i, page in enumerate(pdf.pages, start=1):
                    pages.append({"page": i, "text": page.extract_text() or "", "method": "pdfplumber"})
        except ImportError:
            pass
        except Exception as exc:
            warnings.append(f"pdfplumber_failed:{exc}")
            pages = []

    # 4. pdftotext CLI
    if not pages and shutil.which("pdftotext"):
        proc = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0 and proc.stdout.strip():
            chunks = proc.stdout.split("\x0c")
            for i, chunk in enumerate(chunks, start=1):
                if chunk.strip() or i < len(chunks):
                    pages.append({"page": i, "text": chunk, "method": "pdftotext"})
        else:
            err_low = (proc.stderr or "").lower()
            if "encrypt" in err_low or "password" in err_low:
                return [], [f"pdf_encrypted:{path.name}"]
            warnings.append(f"pdftotext_failed:{proc.stderr.strip()[:120]}")

    # 5. Pure-stdlib fallback: scan PDF text literals or mark corrupt/unavailable
    if not pages:
        raw_bytes = path.read_bytes()
        if b"/Encrypt" in raw_bytes[:16384]:
            return [], [f"pdf_encrypted:{path.name}"]
        if b"%%EOF" not in raw_bytes[-4096:] and b"endobj" not in raw_bytes:
            return [], [f"pdf_corrupt:{path.name}"]
        # Attempt lightweight literal extraction from uncompressed streams
        decoded = raw_bytes.decode("latin-1", errors="ignore")
        lits = re.findall(r"\(([^()]{4,200})\)\s*Tj", decoded)
        if lits:
            pages.append({"page": 1, "text": " ".join(lits), "method": "stdlib_literal_scan"})
        else:
            warnings.append(f"pdf_no_text_extractor_available:{path.name}")
            return [], warnings

    # Check scanned pages (very low character count) and try OCR if available
    low_pages = [p["page"] for p in pages if len((p.get("text") or "").strip()) < 30]
    if low_pages:
        ocr_map = _pdf_ocr_pages(path, low_pages)
        for p in pages:
            pno = p["page"]
            if pno in ocr_map:
                p["text"] = ocr_map[pno]
                p["method"] = "ocr_tesseract"
                warnings.append(f"ocr_used_page_{pno}:{path.name}")
            elif pno in low_pages:
                warnings.append(f"scanned_or_low_text_page_{pno}:{path.name}")

    return pages, warnings


def _clean_bib_value(val: str) -> str:
    val = val.strip().rstrip(",").strip()
    while (val.startswith("{") and val.endswith("}")) or (val.startswith('"') and val.endswith('"')):
        val = val[1:-1].strip()
    val = re.sub(r"[{}]", "", val)
    return normalize_ws(val)


def parse_bibtex_text(text: str, source_path: str) -> List[Dict[str, Any]]:
    """Parse BibTeX entries with exact starting line numbers."""
    entries: List[Dict[str, Any]] = []
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        m = re.match(r"^\s*@([a-zA-Z]+)\s*[{(]\s*([^,\s]+)\s*,?", line)
        if not m:
            i += 1
            continue
        etype = m.group(1).lower()
        if etype in ("comment", "preamble", "string"):
            i += 1
            continue
        cite_key = m.group(2).strip()
        start_line = i + 1
        buf_lines = [line]
        depth = line.count("{") - line.count("}")
        paren_mode = line.lstrip().startswith(f"@{m.group(1)}(")
        if paren_mode:
            depth = line.count("(") - line.count(")")
        i += 1
        while i < n and depth > 0:
            cur = lines[i]
            buf_lines.append(cur)
            if paren_mode:
                depth += cur.count("(") - cur.count(")")
            else:
                depth += cur.count("{") - cur.count("}")
            i += 1

        block = "\n".join(buf_lines)
        fields: Dict[str, str] = {}
        # Parse field = value pairs respecting braces and quotes
        body_start = block.find(",")
        body = block[body_start + 1 : -1] if body_start != -1 else ""
        idx = 0
        blen = len(body)
        while idx < blen:
            fm = re.match(r"\s*([a-zA-Z_][a-zA-Z0-9_-]*)\s*=\s*", body[idx:])
            if not fm:
                idx += 1
                continue
            fname = fm.group(1).lower()
            idx += fm.end()
            if idx >= blen:
                break
            ch = body[idx]
            if ch == "{":
                d = 0
                start_v = idx
                while idx < blen:
                    if body[idx] == "{":
                        d += 1
                    elif body[idx] == "}":
                        d -= 1
                        if d == 0:
                            idx += 1
                            break
                    idx += 1
                val = body[start_v:idx]
            elif ch == '"':
                start_v = idx
                idx += 1
                while idx < blen:
                    if body[idx] == '"' and body[idx - 1] != "\\":
                        idx += 1
                        break
                    idx += 1
                val = body[start_v:idx]
            else:
                start_v = idx
                while idx < blen and body[idx] not in ",\n\r":
                    idx += 1
                val = body[start_v:idx]
            fields[fname] = _clean_bib_value(val)
            while idx < blen and body[idx] in " ,\t\r\n":
                idx += 1

        authors = fields.get("author", "") or fields.get("editor", "")
        venue = (
            fields.get("journal")
            or fields.get("booktitle")
            or fields.get("publisher")
            or fields.get("school")
            or fields.get("howpublished")
            or ""
        )
        doi_or_id = fields.get("doi") or fields.get("eprint") or fields.get("arxivid") or fields.get("url") or ""
        year_m = re.search(r"(19|20)\d{2}", fields.get("year", ""))
        year = year_m.group(0) if year_m else fields.get("year", "")

        entries.append(
            {
                "cite_key": cite_key,
                "entry_type": etype,
                "authors": authors,
                "year": year,
                "title": fields.get("title", ""),
                "venue": venue,
                "doi_or_id": doi_or_id,
                "volume": fields.get("volume", ""),
                "pages": fields.get("pages", ""),
                "abstract": fields.get("abstract", "") or fields.get("note", ""),
                "source_locator": f"{source_path}:L{start_line}",
            }
        )
    return entries


def parse_ris_text(text: str, source_path: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    cur: Dict[str, Any] = {"authors_list": [], "start_line": 1}
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"^([A-Z0-9]{2})\s{1,2}-\s*(.*)$", line.strip())
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur = {"authors_list": [], "start_line": lineno, "entry_type": val}
        elif tag in ("AU", "A1"):
            cur["authors_list"].append(val)
        elif tag in ("TI", "T1"):
            cur["title"] = val
        elif tag in ("PY", "Y1"):
            ym = re.search(r"(19|20)\d{2}", val)
            cur["year"] = ym.group(0) if ym else val
        elif tag in ("JO", "JF", "T2", "BT"):
            cur.setdefault("venue", val)
        elif tag in ("DO", "UR"):
            cur.setdefault("doi_or_id", val)
        elif tag in ("ID", "AN"):
            cur.setdefault("cite_key", val)
        elif tag == "ER":
            authors = "; ".join(cur.get("authors_list", []))
            year = cur.get("year", "")
            first_surname = extract_first_author_surname(authors) or "Ref"
            cite_key = cur.get("cite_key") or f"{first_surname}{year}"
            entries.append(
                {
                    "cite_key": cite_key,
                    "entry_type": cur.get("entry_type", "article"),
                    "authors": authors,
                    "year": year,
                    "title": cur.get("title", ""),
                    "venue": cur.get("venue", ""),
                    "doi_or_id": cur.get("doi_or_id", ""),
                    "abstract": "",
                    "source_locator": f"{source_path}:L{cur.get('start_line', lineno)}",
                }
            )
            cur = {"authors_list": [], "start_line": lineno + 1}
    return entries


def extract_first_author_surname(authors: str) -> str:
    if not authors:
        return ""
    first = re.split(r"\band\b|;|、|&", authors, maxsplit=1)[0].strip()
    if "," in first:
        return re.sub(r"[^A-Za-z\u4e00-\u9fff-]", "", first.split(",")[0].strip())
    parts = [p for p in first.split() if p]
    if not parts:
        return ""
    # Chinese name vs Western name
    if re.search(r"[\u4e00-\u9fff]", parts[0]):
        return parts[0]
    return re.sub(r"[^A-Za-z-]", "", parts[-1])


def parse_markdown_or_text_refs(text: str, source_path: str) -> List[Dict[str, Any]]:
    """Extract numbered or bulleted bibliographic lines from markdown/txt/paper ref lists."""
    entries: List[Dict[str, Any]] = []
    lines = text.splitlines()
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        # Skip bullet notes (e.g., '- [R01] Key: summary...') unless bullet starts a full bib line with year/DOI
        if line.startswith("- "):
            line_cand = line[2:].strip()
            # Only treat bulleted line as a bibliographic record if it contains a 4-digit year or DOI
            if not re.search(r"\b(?:19|20)\d{2}\b", line_cand) and not re.search(r"10\.\d{4,9}/", line_cand):
                continue
            line = line_cand
        # Match [1] Author... or [R01] ... or 1. Author (2023) ...
        m = re.match(r"^(?:\[([A-Za-z0-9_-]{1,24})\]|(\d{1,3})\.)\s+(.+)$", line)
        if not m:
            continue
        key = m.group(1) or f"ref_{m.group(2)}"
        body = m.group(3).strip()
        # Require at least a year or DOI/title signal so ordinary numbered prose isn't misparsed
        ym = re.search(r"\b((?:19|20)\d{2})\b", body)
        doi_m = re.search(r"(10\.\d{4,9}/[^\s,;]+|arXiv:\d{4}\.\d{4,5})", body, re.I)
        if not ym and not doi_m:
            continue
        year = ym.group(1) if ym else ""
        doi_or_id = doi_m.group(1).rstrip(".") if doi_m else ""
        # Heuristic split: Authors. Title. Venue, Year OR Authors (Year). Title. Venue.
        authors, title, venue = "", body, ""
        ay_m = re.match(r"^(.+?)\s*[\(（]\s*(?:19|20)\d{2}[a-z]?\s*[\)）][.,:\s]+(.+)$", body)
        if ay_m:
            authors = ay_m.group(1).strip(" .")
            rest = ay_m.group(2).strip()
            parts = [p.strip() for p in re.split(r"(?<=[.。])\s+", rest, maxsplit=2) if p.strip()]
            if parts:
                title = parts[0].strip(" .\"“”")
                venue = parts[1].strip(" .") if len(parts) > 1 else ""
        else:
            parts = [p.strip() for p in re.split(r"\.\s+|。\s*", body) if p.strip()]
            if len(parts) >= 3:
                authors = parts[0]
                title = parts[1].strip(" \"“”")
                venue = re.sub(r",?\s*(19|20)\d{2}.*$", "", parts[2]).strip(" ,.")
            elif len(parts) == 2:
                authors = parts[0]
                title = parts[1]

        entries.append(
            {
                "cite_key": key,
                "entry_type": "article",
                "authors": authors,
                "year": year,
                "title": title,
                "venue": venue,
                "doi_or_id": doi_or_id,
                "abstract": "",
                "source_locator": f"{source_path}:L{lineno}",
            }
        )
    return entries


def extract_numeric_tokens(text: str, filter_structural: bool = True) -> List[Dict[str, Any]]:
    """Extract quantitative numeric tokens while optionally skipping Fig./Table/Year/Citation indices."""
    if not text:
        return []
    cleaned = text
    if filter_structural:
        # Mask structural references: [1], [1, 2], [R01], GAP-01, CONF-01, P01, Fig. 1, Table 2, Eq. (3), years
        cleaned = re.sub(r"\[(?:R?\d+(?:\s*[-,–]\s*R?\d+)*)\]", " ", cleaned)
        cleaned = re.sub(r"(?:GAP|CONF|CONTRIB|CLAIM|RG|G|C|R)-?\d{1,3}\b", " ", cleaned, flags=re.I)
        cleaned = re.sub(r"(?:Fig(?:ure)?|Table|Tab\.|Eq\.?|Section|Sec\.|图|表|式|第)\s*\.?\s*\(?\d+(?:\.\d+)?\)?", " ", cleaned, flags=re.I)
        # Mask locators like :L2, :p1_b02, :L10-L20 without eating adjacent numbers
        cleaned = re.sub(r":[Lp]\d+(?:[_-][LpA-Za-z]?\d+)*", " ", cleaned)

    tokens: List[Dict[str, Any]] = []
    # When extracting grounded source numbers (filter_structural=False), allow numbers right after letters like Re1e4
    left_boundary = r"(?<![A-Za-z0-9_.-])" if filter_structural else r"(?<![0-9_.-])"
    pat = re.compile(left_boundary + r"([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(%|％|x|×|ms|s|min|h|Hz|kHz|MHz|GHz|nm|um|μm|mm|cm|m|km|mg|g|kg|K|°C|Pa|kPa|MPa|eV|keV|MeV|W|kW|V|mV|A|mA|mol|mmol|kmol|dB|GB|MB|KB|B|fps|tokens)?")
    for m in pat.finditer(cleaned):
        raw_num = m.group(1).replace(",", "")
        # Guard against matching a tail slice inside a number (e.g. '4%' inside '92.4%') when left_boundary doesn't exclude '.' for non-structural or due to regex engine
        if m.start() > 0 and cleaned[m.start() - 1] in ".0123456789":
            continue
        unit = m.group(2) or ""
        try:
            val = float(raw_num)
        except ValueError:
            continue
        # Only count '%' / '％' when NOT inside a comma-separated 3-digit grouping anomaly; and for filter_structural=True skip bare years / small ints
        if filter_structural and not unit and "." not in raw_num and "e" not in raw_num.lower():
            if 1900 <= val <= 2099:
                continue
            if abs(val) < 10:
                continue
        tokens.append(
            {
                "raw": m.group(0).strip(),
                "value": val,
                "unit": unit,
                "has_percent": unit in ("%", "％"),
                "decimals": len(raw_num.split(".")[1]) if "." in raw_num and "e" not in raw_num.lower() else 0,
            }
        )
    return tokens


def numbers_match(tok_a: Dict[str, Any], tok_b: Dict[str, Any]) -> bool:
    """Compare two numeric tokens allowing % <-> decimal equivalence and rounding tolerance."""
    va, vb = tok_a["value"], tok_b["value"]
    candidates_a = [va]
    candidates_b = [vb]
    if tok_a.get("has_percent"):
        candidates_a.append(va / 100.0)
    if tok_b.get("has_percent"):
        candidates_b.append(vb / 100.0)

    dec = min(tok_a.get("decimals", 2), tok_b.get("decimals", 2))
    round_tol = 0.55 * (10.0 ** (-dec)) if dec > 0 else 0.05

    for x in candidates_a:
        for y in candidates_b:
            rel_tol = 0.005 * max(abs(x), abs(y), 1e-12)
            effective_tol = max(round_tol, rel_tol, 1e-9)
            if tok_a.get("has_percent") and tok_b.get("has_percent"):
                effective_tol = round_tol if dec > 0 else 0.05
            # When one has decimals > 0 and the other is an exact integer (e.g., 88.3 vs 90), don't match 0.55 rounding against unrelated integers
            if tok_a.get("decimals", 0) != tok_b.get("decimals", 0) and (tok_a.get("decimals", 0) == 0 or tok_b.get("decimals", 0) == 0):
                effective_tol = max(rel_tol, 0.05)
            if abs(x - y) <= effective_tol:
                return True
    return False


def format_citation_entry(ref: Dict[str, Any], style: str, idx: int) -> str:
    authors = ref.get("authors") or "[待补充作者]"
    year = ref.get("year") or "[待补充年份]"
    title = ref.get("title") or "[待补充标题]"
    venue = ref.get("venue") or "[待补充来源]"
    doi = ref.get("doi_or_id") or ""
    extra = f" DOI: {doi}" if doi else ""

    if style == "apa_author_year":
        return f"{authors} ({year}). {title}. {venue}.{extra}".strip()
    if style == "ieee_numeric":
        return f"[{idx}] {authors}, \"{title},\" {venue}, {year}.{extra}".strip()
    if style == "nature_numeric":
        return f"{idx}. {authors} {title}. {venue} ({year}).{extra}".strip()
    if style == "bibtex_latex":
        key = ref.get("cite_key") or f"ref{idx}"
        return f"@article{{{key}, author={{{authors}}}, title={{{title}}}, journal={{{venue}}}, year={{{year}}}}}"
    # Default: GB/T 7714-2015
    return f"[{idx}] {authors}. {title}[J/C]. {venue}, {year}.{extra}".strip()
