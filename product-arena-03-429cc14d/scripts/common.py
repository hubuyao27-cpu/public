#!/usr/bin/env python3
"""Shared utilities for academic-citation-manager (pure Python 3.9+ stdlib + optional PDF/DOCX)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unicodedata
import zipfile
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"'<>\]\)},;]+)", re.I)
VALID_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
ARXIV_RE = re.compile(r"(?:arxiv[:\s/]+|abs/|pdf/)(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+(?:\.[a-z\-]+)?/\d{7}(?:v\d+)?)", re.I)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")

NOBILIARY_PARTICLES = {
    "van", "von", "de", "del", "della", "da", "di", "du", "la", "le", "dos", "das",
    "van der", "van den", "van de", "de la", "de los", "de las", "ter", "ten",
}
COMPOUND_CJK_SURNAMES = {
    "欧阳", "太史", "端木", "上官", "司马", "东方", "独孤", "南宫", "万俟", "闻人",
    "夏侯", "诸葛", "尉迟", "公羊", "赫连", "澹台", "皇甫", "宗政", "濮阳", "公冶",
    "太叔", "申屠", "公孙", "慕容", "仲孙", "钟离", "长孙", "宇文", "司徒", "鲜于",
    "司空", "闾丘", "子车", "亓官", "司寇", "巫马", "公西", "颛孙", "壤驷", "公良",
    "漆雕", "乐正", "宰父", "谷梁", "拓跋", "夹谷", "轩辕", "令狐", "段干", "百里",
    "呼延", "东郭", "南门", "羊舌", "微生", "公户", "公玉", "公仪", "梁丘", "公仲",
    "公上", "公门", "公山", "公坚", "左丘", "公伯", "西门", "公祖", "第五", "公乘",
    "贯丘", "公皙", "南荣", "东里", "东宫", "仲长", "子书", "子桑", "即墨", "达奚",
    "褚师",
}

_LATEX_ACCENT_MAP = {
    r"\'a": "á", r"\'e": "é", r"\'i": "í", r"\'o": "ó", r"\'u": "ú", r"\'y": "ý", r"\'c": "ć", r"\'n": "ń", r"\'s": "ś", r"\'z": "ź",
    r"\`a": "à", r"\`e": "è", r"\`i": "ì", r"\`o": "ò", r"\`u": "ù",
    r'\"a': "ä", r'\"e': "ë", r'\"i': "ï", r'\"o': "ö", r'\"u': "ü", r'\"y': "ÿ",
    r"\^a": "â", r"\^e": "ê", r"\^i": "î", r"\^o": "ô", r"\^u": "û",
    r"\~a": "ã", r"\~n": "ñ", r"\~o": "õ",
    r"\c{c}": "ç", r"\c{C}": "Ç", r"\aa": "å", r"\AA": "Å", r"\o": "ø", r"\O": "Ø", r"\l": "ł", r"\L": "Ł", r"\ss": "ß",
    r"\'A": "Á", r"\'E": "É", r"\'I": "Í", r"\'O": "Ó", r"\'U": "Ú",
    r'\"A': "Ä", r'\"E': "Ë", r'\"I': "Ï", r'\"O': "Ö", r'\"U': "Ü",
}


def posix_rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def read_text_safe(path: Path) -> Tuple[str, str, Optional[str]]:
    """Decode text across UTF-16, UTF-8-SIG, UTF-8, GB18030, GBK, Latin-1 without crashing."""
    try:
        raw = path.read_bytes()
    except Exception as exc:
        return "", "error", f"Failed to read {path.name}: {exc}"
    if not raw:
        return "", "utf-8", None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16"), "utf-16", None
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            text = raw.decode(enc)
            warn = None if enc in ("utf-8", "utf-8-sig") else f"Decoded {path.name} using fallback encoding `{enc}`."
            return text, enc, warn
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1", f"Decoded {path.name} via lossy latin-1 fallback."


def load_json_safe(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    txt, _, _ = read_text_safe(path)
    if not txt.strip():
        return {} if default is None else default
    try:
        return json.loads(txt)
    except Exception:
        return {} if default is None else default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def detect_script_lang(text: str) -> str:
    if not text:
        return "en"
    cjk_cnt = len(CJK_RE.findall(text))
    alpha_cnt = len(re.findall(r"[A-Za-z]", text))
    return "zh" if cjk_cnt >= max(4, int(0.15 * max(1, cjk_cnt + alpha_cnt))) else "en"


def clean_latex_accents(text: str) -> str:
    if not text:
        return ""
    s = str(text)
    for k, v in _LATEX_ACCENT_MAP.items():
        s = s.replace(k, v)
    # Handle braced variants like {\"{o}} or {\'e}
    s = re.sub(r"\{\\(['\"`^~])\{?([A-Za-z])\}?\}", lambda m: _LATEX_ACCENT_MAP.get(f"\\{m.group(1)}{m.group(2)}", m.group(2)), s)
    s = s.replace("--", "–").replace("---", "—")
    s = re.sub(r"\\(?:emph|textit|textbf|textrm|textsc|text)\{([^{}]*)\}", r"\1", s)
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def normalize_doi(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    s = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    m = DOI_RE.search(s)
    cand = m.group(1) if m else s
    cand = cand.rstrip(".,;:)]}>\"'")
    return cand.lower().strip()


def is_valid_doi(doi: str) -> bool:
    return bool(doi and VALID_DOI_RE.match(doi.strip()))


def normalize_arxiv(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    m = ARXIV_RE.search(s)
    val = m.group(1) if m else s
    val = re.sub(r"^arxiv:\s*", "", val, flags=re.I)
    val = re.sub(r"v\d+$", "", val, flags=re.I)
    return val.lower().strip()


def normalize_url(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).strip().strip("<>\"'")
    return s.rstrip(".,;")


def normalize_title(text: str) -> str:
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", clean_latex_accents(text)).lower()
    s = re.sub(r"[\u2010-\u2015\-–—]+", " ", s)
    s = re.sub(r"[^\w\u4e00-\u9fff\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    seq = SequenceMatcher(None, na, nb).ratio()
    has_cjk = bool(CJK_RE.search(na) or CJK_RE.search(nb))
    if has_cjk:
        ca = "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", na))
        cb = "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", nb))
        if len(ca) >= 2 and len(cb) >= 2:
            ba = {ca[i : i + 2] for i in range(len(ca) - 1)}
            bb = {cb[i : i + 2] for i in range(len(cb) - 1)}
            jac = len(ba & bb) / max(1, len(ba | bb))
            return max(seq, jac)
        return seq
    ta = set(na.split())
    tb = set(nb.split())
    jac = len(ta & tb) / max(1, len(ta | tb))
    return 0.55 * seq + 0.45 * jac


def _initials_from_given(given: str) -> str:
    parts = re.split(r"[\s.\-]+", given.strip())
    return " ".join(p[0].upper() for p in parts if p and p[0].isalpha())


def parse_author_name(raw_name: str) -> Dict[str, Any]:
    """Parse CJK, Western ('Last, First' or 'First Last'), particle, and org author names cleanly."""
    name = clean_latex_accents(raw_name).strip()
    if not name:
        return {"raw": "", "family": "", "given": "", "initials": "", "is_cjk": False, "is_org": False}

    # Explicit braced org or multi-word institutional keyword
    if (raw_name.strip().startswith("{") and raw_name.strip().endswith("}")) or re.search(
        r"\b(?:Organization|Association|Institute|Committee|Consortium|Ministry|University|Society|Group|Team|WHO|IEEE|ISO)\b",
        name,
    ):
        return {"raw": name, "family": name, "given": "", "initials": "", "is_cjk": bool(CJK_RE.search(name)), "is_org": True}

    if CJK_RE.search(name) and not re.search(r"[A-Za-z]{2,}", name):
        clean_zh = re.sub(r"[\s·•]+", "", name)
        fam = clean_zh[:2] if clean_zh[:2] in COMPOUND_CJK_SURNAMES else clean_zh[:1]
        giv = clean_zh[len(fam):]
        return {"raw": clean_zh, "family": fam, "given": giv, "initials": "", "is_cjk": True, "is_org": False}

    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        fam = parts[0]
        giv = parts[1] if len(parts) > 1 else ""
        return {
            "raw": name,
            "family": fam,
            "given": giv,
            "initials": _initials_from_given(giv),
            "is_cjk": False,
            "is_org": False,
        }

    tokens = name.split()
    if len(tokens) == 1:
        return {"raw": name, "family": tokens[0], "given": "", "initials": "", "is_cjk": False, "is_org": False}

    # Check if formatted already as 'SURNAME AB' or 'Smith J A'
    if len(tokens) >= 2 and all(len(t.rstrip(".")) <= 2 and t.rstrip(".").isupper() for t in tokens[1:]):
        fam = tokens[0]
        giv = " ".join(tokens[1:])
        return {"raw": name, "family": fam, "given": giv, "initials": _initials_from_given(giv), "is_cjk": False, "is_org": False}

    # Check nobiliary particles before surname (e.g. 'Johannes van der Waals')
    fam_idx = len(tokens) - 1
    if len(tokens) >= 3:
        two_tok = f"{tokens[-3].lower()} {tokens[-2].lower()}"
        if two_tok in NOBILIARY_PARTICLES:
            fam_idx = len(tokens) - 3
        elif tokens[-2].lower() in NOBILIARY_PARTICLES:
            fam_idx = len(tokens) - 2
    fam = " ".join(tokens[fam_idx:])
    giv = " ".join(tokens[:fam_idx])
    return {"raw": name, "family": fam, "given": giv, "initials": _initials_from_given(giv), "is_cjk": False, "is_org": False}


def split_authors_string(raw: str) -> List[str]:
    if not raw:
        return []
    s = clean_latex_accents(raw).strip()
    s = re.sub(r"\s*(?:等|et\s+al\.?)\s*$", "", s, flags=re.I).strip().rstrip(",;，；")
    if not s:
        return []
    if re.search(r"\s+and\s+", s, flags=re.I) or ";" in s or "；" in s or "、" in s:
        chunks = re.split(r"\s+and\s+|[;；、]+", s, flags=re.I)
        out: List[str] = []
        for ch in chunks:
            c = ch.strip().strip(",")
            if not c:
                continue
            # If a chunk has multiple comma-separated full names like 'Zhang San, Li Si'
            if "," in c:
                sub = [x.strip() for x in c.split(",") if x.strip()]
                # Distinguish 'Last, First' (2 parts where 2nd part is short/initials) vs 'Name1, Name2'
                if len(sub) == 2 and (len(sub[1].split()) <= 2 and not CJK_RE.search(sub[0])):
                    out.append(c)
                else:
                    out.extend(sub)
            else:
                out.append(c)
        return [x for x in out if x]
    if "，" in s:
        return [x.strip() for x in s.split("，") if x.strip()]
    if "," in s:
        parts = [x.strip() for x in s.split(",") if x.strip()]
        if len(parts) == 2 and len(parts[1].split()) <= 2 and not CJK_RE.search(parts[0]):
            return [s]
        # Pairwise 'Last, F., Last2, F2.' detection
        if len(parts) >= 4 and len(parts) % 2 == 0 and all(len(parts[i + 1]) <= 6 for i in range(0, len(parts), 2)):
            return [f"{parts[i]}, {parts[i + 1]}" for i in range(0, len(parts), 2)]
        return parts
    return [s]


def parse_year(raw: Any) -> Tuple[Optional[int], Optional[str]]:
    if raw is None or raw == "":
        return None, None
    m = re.search(r"(\d{4})", str(raw))
    if not m:
        return None, f"unparseable_year:{raw}"
    yr = int(m.group(1))
    cur_yr = datetime.now(timezone.utc).year
    if yr < 1800 or yr > cur_yr + 2:
        return yr, f"impossible_year:{yr}"
    return yr, None


def parse_page_range(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {"normalized": "", "start": None, "end": None, "inverted": False}
    s = str(raw).strip()
    s = re.sub(r"^(?:pp?\.|pages?)\s*", "", s, flags=re.I)
    s = re.sub(r"[\u2012-\u2015−]+", "-", s)
    s = re.sub(r"\s*-\s*", "-", s)
    m = re.match(r"^(\d+)-(\d+)$", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return {"normalized": f"{a}-{b}", "start": a, "end": b, "inverted": a > b}
    return {"normalized": s, "start": None, "end": None, "inverted": False}


def extract_numbers(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?%?", text)


def extract_pdf_text_safe(path: Path) -> Dict[str, Any]:
    """Extract per-page text from PDF with graceful error reporting for encrypted/corrupt/scanned files."""
    if not path.exists() or path.stat().st_size == 0:
        return {"pages": [], "error": "empty_or_missing_pdf", "backend": "none"}
    try:
        header = path.read_bytes()[:16]
    except Exception as exc:
        return {"pages": [], "error": f"unreadable_pdf:{exc}", "backend": "none"}
    if not header.startswith(b"%PDF"):
        return {"pages": [], "error": "invalid_pdf_header", "backend": "none"}

    # 1. PyMuPDF
    try:
        try:
            import pymupdf as fitz  # type: ignore
        except ImportError:
            import fitz  # type: ignore
        doc = fitz.open(str(path))
        if getattr(doc, "needs_pass", False):
            doc.close()
            return {"pages": [], "error": "encrypted_pdf_requires_password", "backend": "pymupdf"}
        pages: List[Dict[str, Any]] = []
        for idx, page in enumerate(doc, start=1):
            pages.append({"page": idx, "text": page.get_text("text") or ""})
        doc.close()
        total_chars = sum(len(p["text"].strip()) for p in pages)
        err = "scanned_or_image_only_pdf_no_text" if pages and total_chars < 20 else None
        return {"pages": pages, "error": err, "backend": "pymupdf"}
    except ImportError:
        pass
    except Exception as exc:
        pymupdf_err = str(exc)
    else:
        pymupdf_err = ""

    # 2. pdfplumber
    try:
        import pdfplumber  # type: ignore
        pages = []
        with pdfplumber.open(str(path)) as pdf:
            for idx, p in enumerate(pdf.pages, start=1):
                pages.append({"page": idx, "text": p.extract_text() or ""})
        total_chars = sum(len(p["text"].strip()) for p in pages)
        err = "scanned_or_image_only_pdf_no_text" if pages and total_chars < 20 else None
        return {"pages": pages, "error": err, "backend": "pdfplumber"}
    except ImportError:
        pass
    except Exception:
        pass

    # 3. pdftotext CLI
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            proc = subprocess.run(
                [pdftotext, "-layout", str(path), "-"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                raw_pages = proc.stdout.split("\f")
                pages = [{"page": i + 1, "text": p} for i, p in enumerate(raw_pages) if p.strip()]
                return {"pages": pages, "error": None, "backend": "pdftotext"}
        except Exception:
            pass

    return {"pages": [], "error": f"pdf_extraction_unavailable:{pymupdf_err}" if pymupdf_err else "no_pdf_backend_available", "backend": "none"}


def extract_docx_structured(path: Path) -> Dict[str, Any]:
    """Extract paragraphs, heading levels, superscript markers, tables, and XML footnotes from .docx."""
    if not path.exists() or path.stat().st_size == 0:
        return {"blocks": [], "error": "empty_or_missing_docx"}
    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                return {"blocks": [], "error": "invalid_docx_missing_document_xml"}
            footnotes_blocks: List[Dict[str, Any]] = []
            if "word/footnotes.xml" in zf.namelist():
                try:
                    root = ET.fromstring(zf.read("word/footnotes.xml"))
                    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                    for fn in root.findall(".//w:footnote", ns):
                        fid = fn.attrib.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}id", "")
                        if fid in ("-1", "0"):
                            continue
                        txt = "".join(fn.itertext()).strip()
                        if txt:
                            footnotes_blocks.append({"block_type": "footnote", "text": f"[^{fid}]: {txt}"})
                except Exception:
                    pass
    except Exception as exc:
        return {"blocks": [], "error": f"corrupt_docx:{exc}"}

    try:
        import docx  # type: ignore
        doc = docx.Document(str(path))
        blocks: List[Dict[str, Any]] = []
        for para in doc.paragraphs:
            style_name = (para.style.name or "") if para.style else ""
            parts: List[str] = []
            for run in para.runs:
                t = run.text or ""
                if not t:
                    continue
                if run.font and run.font.superscript and re.match(r"^\[?[0-9,\-–—\s]+\]?$", t.strip()):
                    parts.append(f"<sup>{t.strip()}</sup>")
                else:
                    parts.append(t)
            text = "".join(parts).strip() or (para.text or "").strip()
            if not text:
                continue
            m_head = re.match(r"Heading\s*(\d+)", style_name, re.I)
            if m_head:
                lvl = min(4, max(1, int(m_head.group(1))))
                blocks.append({"block_type": "heading", "heading_level": lvl, "text": f"{'#' * lvl} {text}"})
            elif "title" in style_name.lower():
                blocks.append({"block_type": "heading", "heading_level": 1, "text": f"# {text}"})
            else:
                blocks.append({"block_type": "paragraph", "text": text})
        for tbl in doc.tables:
            rows = []
            for row in tbl.rows:
                cells = [re.sub(r"\s+", " ", c.text.strip()).replace("|", "\\|") for c in row.cells]
                rows.append("| " + " | ".join(cells) + " |")
            if rows:
                ncols = max(1, rows[0].count("|") - 1)
                sep = "| " + " | ".join(["---"] * ncols) + " |"
                table_md = "\n".join([rows[0], sep] + rows[1:])
                blocks.append({"block_type": "table", "text": table_md})
        blocks.extend(footnotes_blocks)
        return {"blocks": blocks, "error": None}
    except ImportError:
        # Pure-stdlib XML fallback if python-docx is absent
        try:
            with zipfile.ZipFile(path, "r") as zf:
                root = ET.fromstring(zf.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            blocks = []
            for p in root.findall(".//w:body/w:p", ns):
                txt = "".join(p.itertext()).strip()
                if txt:
                    blocks.append({"block_type": "paragraph", "text": txt})
            blocks.extend(footnotes_blocks)
            return {"blocks": blocks, "error": None}
        except Exception as exc:
            return {"blocks": [], "error": f"docx_xml_parse_error:{exc}"}
    except Exception as exc:
        return {"blocks": [], "error": f"docx_parse_error:{exc}"}
