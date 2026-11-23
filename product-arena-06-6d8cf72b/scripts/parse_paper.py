#!/usr/bin/env python3
"""Step 2: Layout-aware paper parser with section tree, anchors, and edge-case detection.

Supports PDF (pymupdf two-column band reordering, pdfplumber, pdftotext, pypdf,
plus optional OCR fallback), Markdown, TXT, LaTeX, DOCX, HTML, and JSON.
Produces source_index.json, section_outline.json, anchored_paper.md, and extraction_report.json.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    detect_language,
    extract_cues,
    load_json,
    normalize_whitespace,
    read_text_safe,
    repair_hyphenation,
    save_json,
)


KNOWN_TOP_HEADINGS = {
    "abstract", "introduction", "background", "related work", "literature review",
    "theory", "methods", "methodology", "materials and methods", "experimental",
    "experimental section", "experimental setup", "results", "results and discussion",
    "discussion", "conclusions", "conclusion", "summary", "limitations", "acknowledgments",
    "acknowledgements", "references", "bibliography", "appendix", "supporting information",
    "supplementary material", "supplementary information",
    "摘要", "引言", "前言", "研究背景", "文献综述", "实验部分", "实验方法", "材料与方法",
    "方法", "理论分析", "结果与讨论", "结果", "讨论", "结论", "局限性", "致谢", "参考文献", "附录",
}

REFERENCE_OR_APPENDIX_KEYWORDS = (
    "reference", "bibliography", "acknowledg", "appendix", "supporting information",
    "supplementary", "参考文献", "致谢", "附录", "支撑信息",
)

LIMITATION_KEYWORDS = ("limitation", "caveat", "threat to validity", "局限", "不足")

NUMBERED_HEADING_RE = re.compile(
    r"^(?:"
    r"(?P<num>\d+(?:\.\d+){0,3})[\s.)、:：]+(?P<title1>[A-Za-z\u4e00-\u9fff].{1,90})"
    r"|(?P<roman>[IVX]{1,5})[.\s]+(?P<title2>[A-Za-z].{1,90})"
    r"|(?P<zh>[一二三四五六七八九十]+)[、.\s]+(?P<title3>.{1,60})"
    r"|（(?P<zhp>[一二三四五六七八九十]+)）\s*(?P<title4>.{1,60})"
    r")$"
)

CAPTION_FIG_RE = re.compile(
    r"^(?:Fig(?:ure)?\.?|Scheme)\s*(?P<num>S?\d+[a-zA-Z]?)\b|^(?:图)\s*(?P<zhnum>\d+[a-zA-Z]?)\b",
    re.IGNORECASE,
)
CAPTION_TAB_RE = re.compile(
    r"^Table\s*(?P<num>S?\d+[a-zA-Z]?)\b|^表\s*(?P<zhnum>\d+[a-zA-Z]?)\b",
    re.IGNORECASE,
)
EQUATION_END_RE = re.compile(r"\((?:Eq\.?\s*)?(?P<num>S?\d+(?:\.\d+)?)\)\s*$")


def detect_pdf_backend() -> str:
    """Return highest-priority available PDF extraction backend."""
    for mod in ("pymupdf", "fitz", "pdfplumber", "pypdf"):
        try:
            __import__(mod)
            return "pymupdf" if mod in ("pymupdf", "fitz") else mod
        except ImportError:
            continue
    if shutil.which("pdftotext"):
        return "pdftotext"
    return "none"


def detect_ocr_capability() -> Tuple[bool, str]:
    """Check whether pytesseract or tesseract CLI is available."""
    try:
        import pytesseract  # type: ignore  # noqa: F401
        if shutil.which("tesseract"):
            return True, "pytesseract"
    except ImportError:
        pass
    if shutil.which("tesseract"):
        return True, "tesseract_cli"
    return False, "none"


def ocr_image_bytes(png_bytes: bytes, capability: str) -> Tuple[str, str]:
    """Run OCR on rendered page PNG bytes, preferring chi_sim+eng then eng."""
    if capability == "none":
        return "", "ocr_unavailable"
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tf.write(png_bytes)
        tmp_png = Path(tf.name)
    try:
        for lang in ("chi_sim+eng", "eng"):
            try:
                proc = subprocess.run(
                    ["tesseract", str(tmp_png), "stdout", "-l", lang, "--psm", "6"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout.strip(), f"tesseract:{lang}"
            except Exception:
                continue
        return "", "ocr_empty_or_failed"
    finally:
        tmp_png.unlink(missing_ok=True)


def normalize_hf_pattern(text: str) -> str:
    """Normalize digits in candidate header/footer text for cross-page repetition matching."""
    s = normalize_whitespace(text)
    s = re.sub(r"\d+", "#", s)
    return s.lower()


def is_standalone_page_number(text: str) -> bool:
    s = normalize_whitespace(text)
    return bool(re.fullmatch(r"[-–—\s]*(?:page\s*)?\d{1,4}(?:\s*/\s*\d{1,4})?[-–—\s]*", s, re.IGNORECASE))


def reorder_two_column_blocks(blocks: List[Dict[str, Any]], page_width: float) -> Tuple[List[Dict[str, Any]], str]:
    """Detect two-column layout and reorder blocks into natural band-by-band reading order."""
    if not blocks or page_width <= 0:
        return blocks, "single_column"

    mid = 0.50 * page_width
    full_width: List[Dict[str, Any]] = []
    left_col: List[Dict[str, Any]] = []
    right_col: List[Dict[str, Any]] = []

    for b in blocks:
        x0, y0, x1, y1 = b["bbox"]
        w = x1 - x0
        xc = 0.5 * (x0 + x1)
        # Any block straddling the page midline belongs to full-width flow (including short centered caption tails)
        if (x0 < 0.47 * page_width and x1 > 0.53 * page_width) or w > 0.52 * page_width:
            full_width.append(b)
        elif xc <= mid:
            left_col.append(b)
        else:
            right_col.append(b)

    # Detect two-column layout: either >=2 blocks per side, or >=1 side-by-side pair
    # occupying separate columns at overlapping vertical positions.
    side_by_side_pair = False
    for lb in left_col:
        lx0, ly0, lx1, ly1 = lb["bbox"]
        if (lx1 - lx0) < 0.22 * page_width or lx1 > 0.52 * page_width:
            continue
        for rb in right_col:
            rx0, ry0, rx1, ry1 = rb["bbox"]
            if (rx1 - rx0) < 0.22 * page_width or rx0 < 0.48 * page_width:
                continue
            if min(ly1, ry1) > max(ly0, ry0) + 4.0:
                side_by_side_pair = True
                break
        if side_by_side_pair:
            break

    is_two_col = (len(left_col) >= 2 and len(right_col) >= 2) or side_by_side_pair
    if not is_two_col:
        ordered = sorted(blocks, key=lambda b: (round(b["bbox"][1], 1), round(b["bbox"][0], 1)))
        for b in ordered:
            b["column"] = "single"
        return ordered, "single_column"

    # Split page vertically into horizontal bands separated by full-width spanning blocks
    full_sorted = sorted(full_width, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    for b in full_sorted:
        b["column"] = "full"
    for b in left_col:
        b["column"] = "left"
    for b in right_col:
        b["column"] = "right"

    ordered_out: List[Dict[str, Any]] = []
    prev_y = -1e9
    for fw in full_sorted:
        fy0, fy1 = fw["bbox"][1], fw["bbox"][3]
        band_left = sorted(
            [b for b in left_col if prev_y <= b["bbox"][1] < fy0],
            key=lambda b: (b["bbox"][1], b["bbox"][0]),
        )
        band_right = sorted(
            [b for b in right_col if prev_y <= b["bbox"][1] < fy0],
            key=lambda b: (b["bbox"][1], b["bbox"][0]),
        )
        ordered_out.extend(band_left)
        ordered_out.extend(band_right)
        ordered_out.append(fw)
        prev_y = max(prev_y, fy1 - 2.0)

    tail_left = sorted([b for b in left_col if b["bbox"][1] >= prev_y], key=lambda b: (b["bbox"][1], b["bbox"][0]))
    tail_right = sorted([b for b in right_col if b["bbox"][1] >= prev_y], key=lambda b: (b["bbox"][1], b["bbox"][0]))
    ordered_out.extend(tail_left)
    ordered_out.extend(tail_right)

    # Fallback safety: include any block missed by strict boundary checks
    seen_ids = {id(b) for b in ordered_out}
    for b in sorted(blocks, key=lambda x: (x["bbox"][1], x["bbox"][0])):
        if id(b) not in seen_ids:
            ordered_out.append(b)

    return ordered_out, "two_column"


def parse_pdf_pymupdf(
    pdf_path: Path,
    render_flagged: bool = False,
    preview_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Extract layout-aware blocks, font metadata, OCR fallback, and visual risk flags using PyMuPDF."""
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:
        import fitz  # type: ignore

    doc = fitz.open(str(pdf_path))
    if doc.is_encrypted and doc.needs_pass:
        page_count = len(doc)
        doc.close()
        return {
            "backend": "pymupdf",
            "encrypted": True,
            "total_pages": page_count,
            "pages": [],
            "blocks": [],
            "warnings": [f"PDF '{pdf_path.name}' is password-encrypted or permission-locked."],
        }

    total_pages = len(doc)
    raw_pages: List[Dict[str, Any]] = []
    hf_counter: Counter[str] = Counter()
    all_font_sizes: List[float] = []

    for pno in range(total_pages):
        page = doc[pno]
        rect = page.rect
        w, h = float(rect.width), float(rect.height)
        pdict = page.get_text("dict")
        text_blocks: List[Dict[str, Any]] = []
        image_count = 0

        for blk in pdict.get("blocks", []):
            btype = blk.get("type", 0)
            if btype == 1:
                image_count += 1
                continue
            if btype != 0:
                continue

            bbox = tuple(float(v) for v in blk.get("bbox", (0, 0, 0, 0)))
            lines_text: List[str] = []
            block_sizes: List[float] = []
            bold_chars = 0
            total_chars = 0
            for line in blk.get("lines", []):
                spans = line.get("spans", [])
                line_str = "".join(sp.get("text", "") for sp in spans)
                if line_str.strip():
                    lines_text.append(line_str.rstrip())
                for sp in spans:
                    stxt = sp.get("text", "")
                    sz = float(sp.get("size", 0.0))
                    flags = int(sp.get("flags", 0))
                    if stxt.strip() and sz > 0:
                        block_sizes.append(sz)
                        all_font_sizes.append(sz)
                        total_chars += len(stxt.strip())
                        if flags & 16 or "bold" in str(sp.get("font", "")).lower():
                            bold_chars += len(stxt.strip())

            raw_text = "\n".join(lines_text).strip()
            if not raw_text:
                continue

            if bbox[3] < 0.09 * h or bbox[1] > 0.90 * h:
                pat = normalize_hf_pattern(raw_text)
                if pat and len(pat) <= 120:
                    hf_counter[pat] += 1

            avg_sz = statistics.mean(block_sizes) if block_sizes else 0.0
            is_bold = (bold_chars / total_chars >= 0.65) if total_chars > 0 else False
            text_blocks.append(
                {
                    "page": pno + 1,
                    "bbox": bbox,
                    "text": raw_text,
                    "avg_font_size": round(avg_sz, 2),
                    "is_bold": is_bold,
                }
            )

        raw_pages.append(
            {
                "page": pno + 1,
                "width": w,
                "height": h,
                "blocks": text_blocks,
                "image_count": image_count,
                "drawing_count": len(page.get_drawings()) if hasattr(page, "get_drawings") else 0,
            }
        )

    median_font = statistics.median(all_font_sizes) if all_font_sizes else 10.0
    hf_threshold = max(2, int(total_pages * 0.35)) if total_pages >= 2 else 999
    repeated_hf = {pat for pat, cnt in hf_counter.items() if cnt >= hf_threshold}

    ocr_avail, ocr_cap = detect_ocr_capability()
    pages_report: List[Dict[str, Any]] = []
    final_blocks: List[Dict[str, Any]] = []
    warnings: List[str] = []
    filtered_hf_count = 0

    for rp in raw_pages:
        pno = rp["page"]
        w, h = rp["width"], rp["height"]
        kept: List[Dict[str, Any]] = []
        for b in rp["blocks"]:
            x0, y0, x1, y1 = b["bbox"]
            txt = b["text"]
            in_margin = (y1 < 0.09 * h) or (y0 > 0.90 * h)
            if in_margin and (is_standalone_page_number(txt) or normalize_hf_pattern(txt) in repeated_hf):
                filtered_hf_count += 1
                continue
            if is_standalone_page_number(txt) and len(txt) <= 10 and (y0 > 0.85 * h or y1 < 0.12 * h):
                filtered_hf_count += 1
                continue
            kept.append(b)

        ordered, layout_mode = reorder_two_column_blocks(kept, w)
        joined_text = "\n".join(b["text"] for b in ordered)
        char_count = len(joined_text.strip())
        replacement_chars = joined_text.count("\ufffd")
        ocr_used = False
        ocr_note = ""

        # Handle scanned / empty pages
        if char_count < 40:
            if ocr_avail:
                pix = doc[pno - 1].get_pixmap(dpi=200)
                ocr_txt, ocr_note = ocr_image_bytes(pix.tobytes("png"), ocr_cap)
                if ocr_txt:
                    ocr_used = True
                    ordered = [
                        {
                            "page": pno,
                            "bbox": (0.0, 0.0, w, h),
                            "text": ocr_txt,
                            "avg_font_size": median_font,
                            "is_bold": False,
                            "column": "ocr",
                        }
                    ]
                    joined_text = ocr_txt
                    char_count = len(ocr_txt.strip())

        status = "ok"
        if char_count < 40:
            status = "empty_or_scanned"
            warnings.append(f"Page {pno} has minimal/no extractable text ({char_count} chars); OCR={ocr_note or 'none'}.")
        elif replacement_chars > 5 or (char_count > 0 and replacement_chars / char_count > 0.05):
            status = "garbled_text_risk"
            warnings.append(f"Page {pno} contains {replacement_chars} replacement characters (possible missing math font).")
        elif char_count < 150:
            status = "low_text"

        visual_risk = (
            status in ("empty_or_scanned", "garbled_text_risk")
            or rp["image_count"] >= 1
            or rp["drawing_count"] >= 40
        )

        preview_path = None
        if render_flagged and preview_dir is not None and visual_risk:
            preview_dir.mkdir(parents=True, exist_ok=True)
            out_png = preview_dir / f"{pdf_path.stem}_page_{pno:03d}.png"
            doc[pno - 1].get_pixmap(dpi=150).save(str(out_png))
            preview_path = str(out_png)

        pages_report.append(
            {
                "page": pno,
                "status": status,
                "layout": layout_mode,
                "char_count": char_count,
                "image_count": rp["image_count"],
                "drawing_count": rp["drawing_count"],
                "ocr_used": ocr_used,
                "ocr_note": ocr_note,
                "visual_or_formula_risk": visual_risk,
                "preview_image": preview_path,
            }
        )

        for b in ordered:
            b["median_font_size"] = round(median_font, 2)
            final_blocks.append(b)

    doc.close()
    return {
        "backend": "pymupdf",
        "has_pdf_pages": True,
        "encrypted": False,
        "total_pages": total_pages,
        "median_font_size": round(median_font, 2),
        "header_footer_filtered_count": filtered_hf_count,
        "pages": pages_report,
        "blocks": final_blocks,
        "warnings": warnings,
    }


def parse_pdf_fallback(pdf_path: Path, backend: str) -> Dict[str, Any]:
    """Fallback PDF extraction via pdfplumber, pdftotext, or pypdf."""
    blocks: List[Dict[str, Any]] = []
    pages_report: List[Dict[str, Any]] = []
    warnings: List[str] = []

    if backend == "pdfplumber":
        import pdfplumber  # type: ignore

        with pdfplumber.open(str(pdf_path)) as pdf:
            total_pages = len(pdf.pages)
            for idx, page in enumerate(pdf.pages, start=1):
                txt = page.extract_text(layout=False) or ""
                tables = page.extract_tables() or []
                for t_idx, tbl in enumerate(tables, start=1):
                    rows = [" | ".join(normalize_whitespace(c or "") for c in row) for row in tbl if row]
                    if rows:
                        txt += f"\n\n[Extracted Table {t_idx} on Page {idx}]\n" + "\n".join(rows)
                st = "ok" if len(txt.strip()) >= 40 else "empty_or_scanned"
                pages_report.append({"page": idx, "status": st, "layout": "unknown", "char_count": len(txt.strip())})
                for chunk in re.split(r"\n{2,}", txt):
                    if chunk.strip():
                        blocks.append({"page": idx, "text": chunk.strip(), "avg_font_size": 0.0, "is_bold": False})
    elif backend == "pdftotext":
        proc = subprocess.run(
            ["pdftotext", str(pdf_path), "-"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        raw = proc.stdout or ""
        raw_pages = raw.split("\x0c")
        if raw_pages and not raw_pages[-1].strip():
            raw_pages.pop()
        total_pages = len(raw_pages)
        for idx, ptxt in enumerate(raw_pages, start=1):
            st = "ok" if len(ptxt.strip()) >= 40 else "empty_or_scanned"
            pages_report.append({"page": idx, "status": st, "layout": "unknown", "char_count": len(ptxt.strip())})
            for chunk in re.split(r"\n{2,}", ptxt):
                if chunk.strip():
                    blocks.append({"page": idx, "text": chunk.strip(), "avg_font_size": 0.0, "is_bold": False})
    elif backend == "pypdf":
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:
            return {
                "backend": "pypdf",
                "encrypted": True,
                "total_pages": len(reader.pages),
                "pages": [],
                "blocks": [],
                "warnings": [f"PDF '{pdf_path.name}' is encrypted."],
            }
        total_pages = len(reader.pages)
        for idx, page in enumerate(reader.pages, start=1):
            ptxt = page.extract_text() or ""
            st = "ok" if len(ptxt.strip()) >= 40 else "empty_or_scanned"
            pages_report.append({"page": idx, "status": st, "layout": "unknown", "char_count": len(ptxt.strip())})
            for chunk in re.split(r"\n{2,}", ptxt):
                if chunk.strip():
                    blocks.append({"page": idx, "text": chunk.strip(), "avg_font_size": 0.0, "is_bold": False})
    else:
        return {
            "backend": "none",
            "encrypted": False,
            "total_pages": 0,
            "pages": [],
            "blocks": [],
            "warnings": ["No PDF extraction tool/library available in runtime environment."],
        }

    return {
        "backend": backend,
        "has_pdf_pages": True,
        "encrypted": False,
        "total_pages": total_pages,
        "median_font_size": 0.0,
        "header_footer_filtered_count": 0,
        "pages": pages_report,
        "blocks": blocks,
        "warnings": warnings,
    }


def _xml_inner_text(elem: ET.Element) -> str:
    """Extract clean inline text from an XML element including child math/italics/xref."""
    return normalize_whitespace("".join(elem.itertext()))


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _extract_xml_blocks(root: ET.Element) -> List[Dict[str, Any]]:
    """Extract structured blocks from JATS/PMC or generic academic XML."""
    blocks: List[Dict[str, Any]] = []

    def walk_sec(sec_elem: ET.Element, depth: int) -> None:
        title_el = next((c for c in sec_elem if _local_tag(c.tag) == "title"), None)
        if title_el is not None:
            ttxt = _xml_inner_text(title_el)
            if ttxt:
                prefix = "#" * min(max(1, depth), 4)
                blocks.append({"page": None, "text": f"{prefix} {ttxt}", "avg_font_size": 14.0, "is_bold": True})
        for child in sec_elem:
            ctag = _local_tag(child.tag)
            if ctag == "title":
                continue
            if ctag == "sec":
                walk_sec(child, depth + 1)
            elif ctag == "p":
                ptxt = _xml_inner_text(child)
                if ptxt:
                    blocks.append({"page": None, "text": ptxt, "source_para_id": child.attrib.get("id"), "avg_font_size": 10.0, "is_bold": False})
            elif ctag in ("fig", "table-wrap", "disp-formula"):
                label_el = next((c for c in child if _local_tag(c.tag) == "label"), None)
                cap_el = next((c for c in child if _local_tag(c.tag) == "caption"), None)
                lbl = _xml_inner_text(label_el) if label_el is not None else ""
                cap = _xml_inner_text(cap_el) if cap_el is not None else _xml_inner_text(child)
                combined = normalize_whitespace(f"{lbl} {cap}".strip())
                if combined:
                    blocks.append({"page": None, "text": combined, "source_para_id": child.attrib.get("id"), "avg_font_size": 10.0, "is_bold": False})
            else:
                # Recurse shallowly for boxed-text / list / etc.
                sub = _xml_inner_text(child)
                if sub and ctag not in ("ref-list",):
                    blocks.append({"page": None, "text": sub, "source_para_id": child.attrib.get("id"), "avg_font_size": 10.0, "is_bold": False})
                elif ctag == "ref-list":
                    blocks.append({"page": None, "text": "# References", "avg_font_size": 14.0, "is_bold": True})
                    for ref in child:
                        rtxt = _xml_inner_text(ref)
                        if rtxt:
                            blocks.append({"page": None, "text": rtxt, "source_para_id": ref.attrib.get("id"), "avg_font_size": 9.0, "is_bold": False})

    # Frontmatter title and abstract
    for el in root.iter():
        tag = _local_tag(el.tag)
        if tag == "article-title":
            t = _xml_inner_text(el)
            if t:
                blocks.append({"page": None, "text": f"# {t}", "avg_font_size": 16.0, "is_bold": True})
            break
    for el in root.iter():
        if _local_tag(el.tag) == "abstract":
            blocks.append({"page": None, "text": "## Abstract", "avg_font_size": 14.0, "is_bold": True})
            ps = [c for c in el if _local_tag(c.tag) == "p"]
            if ps:
                for p in ps:
                    ptxt = _xml_inner_text(p)
                    if ptxt:
                        blocks.append({"page": None, "text": ptxt, "source_para_id": p.attrib.get("id"), "avg_font_size": 10.0, "is_bold": False})
            else:
                atxt = _xml_inner_text(el)
                if atxt:
                    blocks.append({"page": None, "text": atxt, "avg_font_size": 10.0, "is_bold": False})
            break

    body_el = next((el for el in root.iter() if _local_tag(el.tag) == "body"), None)
    if body_el is not None:
        for child in body_el:
            ctag = _local_tag(child.tag)
            if ctag == "sec":
                walk_sec(child, 2)
            elif ctag == "p":
                ptxt = _xml_inner_text(child)
                if ptxt:
                    blocks.append({"page": None, "text": ptxt, "source_para_id": child.attrib.get("id"), "avg_font_size": 10.0, "is_bold": False})
    back_el = next((el for el in root.iter() if _local_tag(el.tag) == "back"), None)
    if back_el is not None:
        for child in back_el:
            if _local_tag(child.tag) in ("sec", "app-group", "ref-list", "ack"):
                walk_sec(child, 2)

    if not blocks:
        for p in root.iter():
            if _local_tag(p.tag) == "p":
                ptxt = _xml_inner_text(p)
                if ptxt:
                    blocks.append({"page": None, "text": ptxt, "source_para_id": p.attrib.get("id"), "avg_font_size": 10.0, "is_bold": False})
    return blocks


def _extract_json_paper_blocks(obj: Any) -> Optional[List[Dict[str, Any]]]:
    """If JSON encodes structured paper sections/paragraphs, flatten into headings and blocks."""
    if not isinstance(obj, dict):
        return None
    if not any(k in obj for k in ("sections", "paragraphs", "body", "abstract", "article_title")):
        return None

    blocks: List[Dict[str, Any]] = []
    title = obj.get("title") or obj.get("article_title")
    if isinstance(title, str) and title.strip():
        blocks.append({"page": None, "text": f"# {title.strip()}", "avg_font_size": 16.0, "is_bold": True})
    abstract = obj.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        blocks.append({"page": None, "text": "## Abstract", "avg_font_size": 14.0, "is_bold": True})
        blocks.append({"page": None, "text": abstract.strip(), "avg_font_size": 10.0, "is_bold": False})
    elif isinstance(abstract, list):
        blocks.append({"page": None, "text": "## Abstract", "avg_font_size": 14.0, "is_bold": True})
        for a in abstract:
            t = a.get("text") if isinstance(a, dict) else str(a)
            if t and str(t).strip():
                blocks.append({"page": None, "text": str(t).strip(), "avg_font_size": 10.0, "is_bold": False})

    def walk_json_secs(secs: Any, depth: int) -> None:
        if not isinstance(secs, list):
            return
        for sec in secs:
            if not isinstance(sec, dict):
                continue
            stitle = sec.get("title") or sec.get("heading") or sec.get("name")
            if isinstance(stitle, str) and stitle.strip():
                blocks.append({"page": None, "text": f"{'#' * min(4, depth)} {stitle.strip()}", "avg_font_size": 14.0, "is_bold": True})
            paras = sec.get("paragraphs") or sec.get("content") or sec.get("text")
            if isinstance(paras, str) and paras.strip():
                blocks.append({"page": None, "text": paras.strip(), "avg_font_size": 10.0, "is_bold": False})
            elif isinstance(paras, list):
                for p in paras:
                    if isinstance(p, dict):
                        pt = p.get("text") or p.get("content") or ""
                        if str(pt).strip():
                            blocks.append({"page": None, "text": str(pt).strip(), "source_para_id": p.get("id") or p.get("paragraph_id"), "avg_font_size": 10.0, "is_bold": False})
                    elif str(p).strip():
                        blocks.append({"page": None, "text": str(p).strip(), "avg_font_size": 10.0, "is_bold": False})
            if sec.get("subsections") or sec.get("sections"):
                walk_json_secs(sec.get("subsections") or sec.get("sections"), depth + 1)

    if obj.get("sections"):
        walk_json_secs(obj["sections"], 2)
    elif obj.get("body") and isinstance(obj["body"], list):
        walk_json_secs(obj["body"], 2)
    elif obj.get("paragraphs") and isinstance(obj["paragraphs"], list):
        for p in obj["paragraphs"]:
            if isinstance(p, dict):
                pt = p.get("text") or p.get("content") or ""
                if str(pt).strip():
                    blocks.append({"page": None, "text": str(pt).strip(), "source_para_id": p.get("id") or p.get("paragraph_id"), "avg_font_size": 10.0, "is_bold": False})
            elif str(p).strip():
                blocks.append({"page": None, "text": str(p).strip(), "avg_font_size": 10.0, "is_bold": False})
    return blocks if blocks else None


def parse_plain_or_structured(path: Path) -> Dict[str, Any]:
    """Parse TXT, Markdown, LaTeX, DOCX, XML/NXML, HTML, or JSON into unified block format."""
    ext = path.suffix.lower()
    blocks: List[Dict[str, Any]] = []

    if ext == ".docx":
        with zipfile.ZipFile(path) as zf:
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        for p in root.findall(".//w:body/w:p", ns):
            pstyle = p.find("./w:pPr/w:pStyle", ns)
            style_val = pstyle.attrib.get(f"{{{ns['w']}}}val", "") if pstyle is not None else ""
            texts = [t.text for t in p.findall(".//w:t", ns) if t.text]
            joined = "".join(texts).strip()
            if joined:
                is_heading = "heading" in style_val.lower() or "title" in style_val.lower()
                blocks.append({"page": None, "text": joined, "avg_font_size": 14.0 if is_heading else 10.0, "is_bold": is_heading})
        backend = "docx_xml"
    elif ext in (".xml", ".nxml"):
        raw = read_text_safe(path)
        root = ET.fromstring(raw)
        blocks = _extract_xml_blocks(root)
        backend = "jats_xml"
    else:
        raw = read_text_safe(path)
        parsed_json_blocks = None
        if ext == ".json":
            try:
                obj = json.loads(raw)
                parsed_json_blocks = _extract_json_paper_blocks(obj)
                if parsed_json_blocks is None:
                    raw = json.dumps(obj, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass
        if parsed_json_blocks is not None:
            blocks = parsed_json_blocks
            backend = "structured_json"
        else:
            if ext in (".html", ".htm"):
                raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
                raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
                raw = re.sub(r"</(?:p|div|h\d|li|tr)>", "\n\n", raw, flags=re.IGNORECASE)
                raw = re.sub(r"<[^>]+>", " ", raw)
            # Ensure markdown headings / LaTeX sections start a fresh block even without blank lines
            raw = re.sub(r"(?m)^(#{1,4}\s+.+|\\(?:section|subsection|subsubsection)\*?\{.+\})$", r"\n\1\n", raw)
            for chunk in re.split(r"\n{2,}", raw):
                if chunk.strip():
                    blocks.append({"page": None, "text": chunk.strip(), "avg_font_size": 0.0, "is_bold": False})
            backend = "plaintext"

    total_chars = sum(len(b["text"]) for b in blocks)
    return {
        "backend": backend,
        "has_pdf_pages": False,
        "encrypted": False,
        "total_pages": 0,
        "median_font_size": 10.0,
        "header_footer_filtered_count": 0,
        "pages": [{"page": None, "status": "ok" if total_chars > 0 else "empty_or_scanned", "layout": "single_column", "char_count": total_chars}],
        "blocks": blocks,
        "warnings": [] if total_chars > 0 else [f"File '{path.name}' contains no readable text."],
    }


UNIT_AFTER_NUM_RE = re.compile(
    r"^(?:A|V|mV|mA|uA|μA|g|mg|kg|mL|L|wt%|%|h|min|s|K|MPa|kPa|Pa|nm|um|μm|mm|cm|m|eV|kJ|mol|sccm|GPU|Hz|kHz|MHz|°C|C)\b"
)

def classify_heading(line: str, avg_font: float, median_font: float, is_bold: bool, in_frontmatter: bool = False) -> Optional[Tuple[int, str]]:
    """Return (heading_level, clean_title) if block/line represents a real paper section heading."""
    s = normalize_whitespace(line)
    # Detect inline Abstract / 摘要 prefix in a paragraph block
    abstract_inline_m = re.match(r"^(Abstract|摘要)\s*[:：—\-]\s+(.+)$", s, re.IGNORECASE | re.DOTALL)
    if abstract_inline_m:
        return 1, abstract_inline_m.group(1)

    if not s or len(s) > 120:
        return None
    if s.endswith((".", "。", ";", "；")) and not re.match(r"^\d+\.", s):
        return None

    # Markdown heading
    md_m = re.match(r"^(#{1,4})\s+(.+)$", s)
    if md_m:
        return len(md_m.group(1)), md_m.group(2).strip()

    # LaTeX section
    tex_m = re.match(r"^\\(section|subsection|subsubsection)\*?\{(.+)\}$", s)
    if tex_m:
        lvl_map = {"section": 1, "subsection": 2, "subsubsection": 3}
        return lvl_map[tex_m.group(1)], tex_m.group(2).strip()

    # Avoid misclassifying figure/table captions or equations as headings
    if CAPTION_FIG_RE.match(s) or CAPTION_TAB_RE.match(s) or EQUATION_END_RE.search(s):
        return None

    # Known section name (case-insensitive)
    bare = re.sub(r"[:：]+$", "", s).strip().lower()
    if bare in KNOWN_TOP_HEADINGS:
        return 1, re.sub(r"[:：]+$", "", s).strip()

    # Numbered heading: 1., 2.1, II., 一、
    nm = NUMBERED_HEADING_RE.match(s)
    if nm:
        if nm.group("num"):
            num_str = nm.group("num")
            t1 = nm.group("title1").strip()
            # Reject measurements ("1.0 A cm-2"), affiliations ("1 Beijing Univ., 100029"), or prose fragments ("2 主峰，表明...")
            if UNIT_AFTER_NUM_RE.match(t1):
                return None
            if any(ch in t1 for ch in ("，", ",", ";", "；", "=", ">", "<", "±", "∼", "~", "@")):
                return None
            has_punct_sep = bool(re.match(r"^\d+(?:\.\d+)*[.、:：)]", s))
            # Single integer without dot/dunhao (e.g. "1 Foo") requires bold/larger font and short phrase
            if "." not in num_str and not has_punct_sep:
                font_boost = (median_font > 0 and avg_font >= median_font * 1.08)
                if not (is_bold or font_boost) or len(t1) > 40:
                    return None
            depth = num_str.count(".") + 1
            return min(depth, 3), s
        if nm.group("roman") or nm.group("zh"):
            return 1, s
        if nm.group("zhp"):
            return 2, s

    # Skip unnumbered decorative headings while still in frontmatter (subtitle/authors/affiliations)
    if in_frontmatter:
        return None

    # Font-based heading heuristic for PDFs without numbering
    font_boost = (median_font > 0 and avg_font >= median_font * 1.12)
    if (font_boost or is_bold) and len(s.split()) <= 10 and not any(ch in s for ch in (",", "，", "。", ";", "；")):
        if not s[0].isdigit():
            return 1 if font_boost else 2, s

    return None


def split_blocks_into_segments(
    doc_id: str,
    doc_prefix: str,
    filename: str,
    raw_result: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Convert raw blocks into anchored segments, hierarchical section tree, and fig/tab/eq index."""
    has_pdf_pages = bool(raw_result.get("has_pdf_pages", True))
    median_font = float(raw_result.get("median_font_size") or 0.0)
    segments: List[Dict[str, Any]] = []
    sections: List[Dict[str, Any]] = []
    artifacts_idx: Dict[str, Dict[str, Any]] = {}

    sec_counters = [0, 0, 0]
    current_sec_id = "S00"
    current_sec_title = "Frontmatter / Title & Authors"
    current_sec_is_ref = False

    def register_section(level: int, title: str, page: Optional[int]) -> str:
        nonlocal current_sec_id, current_sec_title, current_sec_is_ref
        lvl = max(1, min(level, 3))
        sec_counters[lvl - 1] += 1
        for i in range(lvl, 3):
            sec_counters[i] = 0
        if lvl == 1:
            sid = f"{doc_prefix}S{sec_counters[0]:02d}"
        elif lvl == 2:
            sid = f"{doc_prefix}S{sec_counters[0]:02d}.{sec_counters[1]}"
        else:
            sid = f"{doc_prefix}S{sec_counters[0]:02d}.{sec_counters[1]}.{sec_counters[2]}"

        low = title.lower()
        is_ref = any(k in low for k in REFERENCE_OR_APPENDIX_KEYWORDS)
        sections.append(
            {
                "section_id": sid,
                "doc_id": doc_id,
                "level": lvl,
                "title": title,
                "start_page": page,
                "end_page": page,
                "paragraph_ids": [],
                "word_count": 0,
                "char_count": 0,
                "is_reference_or_appendix": is_ref,
                "has_limitation_cue": any(k in low for k in LIMITATION_KEYWORDS),
            }
        )
        current_sec_id = sid
        current_sec_title = title
        current_sec_is_ref = is_ref
        return sid

    # Always open S00 for frontmatter/title unless first block is already a heading
    sections.append(
        {
            "section_id": f"{doc_prefix}S00",
            "doc_id": doc_id,
            "level": 1,
            "title": "Frontmatter / Title & Authors",
            "start_page": 1 if has_pdf_pages else None,
            "end_page": 1 if has_pdf_pages else None,
            "paragraph_ids": [],
            "word_count": 0,
            "char_count": 0,
            "is_reference_or_appendix": False,
            "has_limitation_cue": False,
        }
    )
    current_sec_id = f"{doc_prefix}S00"

    # Detect if markdown uses single '#' solely for the document title on top
    blocks = raw_result.get("blocks", [])
    md_levels = []
    for blk in blocks:
        for ln in blk.get("text", "").splitlines():
            mm = re.match(r"^(#{1,4})\s+", ln.strip())
            if mm:
                md_levels.append(len(mm.group(1)))
    has_single_top_h1 = md_levels.count(1) == 1 and any(lvl >= 2 for lvl in md_levels)

    p_seq = 0
    for blk_idx, blk in enumerate(blocks):
        raw_page = blk.get("page")
        page: Optional[int] = int(raw_page) if (has_pdf_pages and raw_page is not None) else None
        source_para_id = blk.get("source_para_id")
        avg_font = float(blk.get("avg_font_size") or 0.0)
        is_bold = bool(blk.get("is_bold", False))
        col = blk.get("column", "single")
        cleaned = repair_hyphenation(blk.get("text", "")).strip()
        if not cleaned:
            continue

        # Check if first line of block is a section heading followed by body text
        lines = [ln.strip() for ln in cleaned.split("\n") if ln.strip()]
        if not lines:
            continue

        first_line = lines[0]
        in_fm = current_sec_id.endswith("S00")
        heading_hit = classify_heading(first_line, avg_font, median_font, is_bold, in_frontmatter=in_fm)

        # Keep document title block (initial H1 or unnumbered oversized title before any section) as S00 title segment
        if heading_hit and p_seq == 0 and len(sections) == 1 and (page is None or page == 1):
            lvl_cand, title_cand = heading_hit
            low_cand = title_cand.lower().strip()
            is_known_sec = low_cand in KNOWN_TOP_HEADINGS or bool(NUMBERED_HEADING_RE.match(title_cand))
            if (has_single_top_h1 and lvl_cand == 1) or (not is_known_sec and len(lines) == 1):
                p_seq += 1
                pid = f"{doc_prefix}P{p_seq:03d}"
                seg = {
                    "id": pid,
                    "doc_id": doc_id,
                    "filename": filename,
                    "page": page,
                    "has_pdf_page": has_pdf_pages,
                    "source_para_id": source_para_id,
                    "column": col,
                    "section_id": current_sec_id,
                    "section_title": current_sec_title,
                    "kind": "title",
                    "text": title_cand,
                    "cues": extract_cues(title_cand),
                }
                segments.append(seg)
                sec_obj = sections[-1]
                sec_obj["paragraph_ids"].append(pid)
                sec_obj["word_count"] += len(title_cand.split())
                sec_obj["char_count"] += len(title_cand)
                continue

        if heading_hit and has_single_top_h1 and first_line.startswith("#"):
            lvl_h, t_h = heading_hit
            heading_hit = (max(1, lvl_h - 1), t_h)

        if heading_hit and len(lines) == 1:
            lvl, htitle = heading_hit
            # Handle inline "Abstract: text" or "摘要：text" single-line block
            ab_m = re.match(r"^(?:Abstract|摘要)\s*[:：—\-]\s+(.+)$", first_line, re.IGNORECASE)
            register_section(lvl, htitle, page)
            if ab_m:
                cleaned = ab_m.group(1).strip()
            else:
                continue
        elif heading_hit and len(lines) > 1 and len(first_line) <= 80:
            lvl, htitle = heading_hit
            register_section(lvl, htitle, page)
            cleaned = "\n".join(lines[1:]).strip()
        elif heading_hit:
            ab_m = re.match(r"^(?:Abstract|摘要)\s*[:：—\-]\s+(.+)$", cleaned, re.IGNORECASE | re.DOTALL)
            if ab_m:
                lvl, htitle = heading_hit
                register_section(lvl, htitle, page)
                cleaned = ab_m.group(1).strip()

        para_text = normalize_whitespace(cleaned)
        if not para_text:
            continue

        p_seq += 1
        pid = f"{doc_prefix}P{p_seq:03d}"

        kind = "reference" if current_sec_is_ref else "paragraph"
        fig_m = CAPTION_FIG_RE.match(para_text)
        tab_m = CAPTION_TAB_RE.match(para_text)
        eq_m = EQUATION_END_RE.search(para_text)
        if fig_m:
            kind = "caption_figure"
            fnum = fig_m.group("num") or fig_m.group("zhnum")
            aid = f"{doc_prefix}FIG:Fig. {fnum}"
            artifacts_idx[aid] = {
                "anchor_id": aid,
                "kind": "figure",
                "page": page,
                "section_id": current_sec_id,
                "section_title": current_sec_title,
                "paragraph_id": pid,
                "text": para_text,
            }
        elif tab_m:
            kind = "caption_table"
            tnum = tab_m.group("num") or tab_m.group("zhnum")
            aid = f"{doc_prefix}TAB:Table {tnum}"
            artifacts_idx[aid] = {
                "anchor_id": aid,
                "kind": "table",
                "page": page,
                "section_id": current_sec_id,
                "section_title": current_sec_title,
                "paragraph_id": pid,
                "text": para_text,
            }
        elif eq_m and len(para_text) < 260:
            kind = "equation"
            enum = eq_m.group("num")
            aid = f"{doc_prefix}EQ:Eq. ({enum})"
            artifacts_idx[aid] = {
                "anchor_id": aid,
                "kind": "equation",
                "page": page,
                "section_id": current_sec_id,
                "section_title": current_sec_title,
                "paragraph_id": pid,
                "text": para_text,
            }

        cues = extract_cues(para_text)
        seg = {
            "id": pid,
            "doc_id": doc_id,
            "filename": filename,
            "page": page,
            "has_pdf_page": has_pdf_pages,
            "source_para_id": source_para_id,
            "column": col,
            "section_id": current_sec_id,
            "section_title": current_sec_title,
            "kind": kind,
            "text": para_text,
            "cues": cues,
        }
        segments.append(seg)

        sec_obj = sections[-1]
        if page is not None:
            sec_obj["end_page"] = max(sec_obj["end_page"], page) if sec_obj["end_page"] is not None else page
            if sec_obj["start_page"] is None:
                sec_obj["start_page"] = page
        sec_obj["paragraph_ids"].append(pid)
        sec_obj["word_count"] += len(para_text.split())
        sec_obj["char_count"] += len(para_text)

    # Remove empty S00 if paper started immediately with a heading
    sections = [s for s in sections if s["paragraph_ids"] or s["section_id"] != f"{doc_prefix}S00"]
    return segments, sections, artifacts_idx


def build_batch_plan(sections: List[Dict[str, Any]], max_chars_per_batch: int = 11000) -> List[Dict[str, Any]]:
    """Group non-reference sections into bounded reading batches for long papers."""
    batches: List[Dict[str, Any]] = []
    cur_secs: List[str] = []
    cur_pids: List[str] = []
    cur_chars = 0

    for sec in sections:
        if sec.get("is_reference_or_appendix"):
            continue
        if not sec["paragraph_ids"]:
            continue
        sc = int(sec.get("char_count", 0))
        if cur_secs and (cur_chars + sc > max_chars_per_batch):
            batches.append(
                {
                    "batch_id": f"B{len(batches) + 1}",
                    "section_ids": cur_secs,
                    "paragraph_range": [cur_pids[0], cur_pids[-1]],
                    "paragraph_count": len(cur_pids),
                    "char_count": cur_chars,
                }
            )
            cur_secs = []
            cur_pids = []
            cur_chars = 0
        cur_secs.append(sec["section_id"])
        cur_pids.extend(sec["paragraph_ids"])
        cur_chars += sc

    if cur_secs:
        batches.append(
            {
                "batch_id": f"B{len(batches) + 1}",
                "section_ids": cur_secs,
                "paragraph_range": [cur_pids[0], cur_pids[-1]],
                "paragraph_count": len(cur_pids),
                "char_count": cur_chars,
            }
        )
    return batches


def render_anchored_markdown(
    segments: List[Dict[str, Any]],
    sections: List[Dict[str, Any]],
    extraction_report: Dict[str, Any],
) -> str:
    """Create compact anchored Markdown text for grounded reading and quoting."""
    lines: List[str] = [
        "# Anchored Source Document(s)",
        "",
        f"- **Documents**: {len(extraction_report.get('documents', []))}",
        f"- **Total Segments**: {len(segments)}",
        f"- **Detected Language**: {extraction_report.get('language', 'unknown')}",
        "",
    ]

    if sections:
        lines.append("## Extracted Section Outline")
        for s in sections:
            indent = "  " * (s["level"] - 1)
            tag = " [REF/APP]" if s.get("is_reference_or_appendix") else ""
            if s.get("start_page") is not None:
                loc_str = f"pp. {s['start_page']}–{s['end_page']}, {len(s['paragraph_ids'])} paras"
            else:
                loc_str = f"no-pdf-pages, {len(s['paragraph_ids'])} paras"
            lines.append(
                f"{indent}- **{s['section_id']}** {s['title']} ({loc_str}){tag}"
            )
        lines.append("")

    current_doc = None
    current_sec = None
    for seg in segments:
        if seg["doc_id"] != current_doc:
            current_doc = seg["doc_id"]
            lines.append("---")
            lines.append(f"# Document {current_doc}: {seg['filename']}")
            lines.append("")
        if seg["section_id"] != current_sec:
            current_sec = seg["section_id"]
            lines.append(f"## {seg['section_id']} | {seg['section_title']}")
            lines.append("")
        loc_parts = [seg["id"]]
        if seg.get("page") is not None:
            loc_parts.append(f"p.{seg['page']}")
        elif seg.get("source_para_id"):
            loc_parts.append(f"src:{seg['source_para_id']}")
        loc_parts.append(seg["section_id"])
        anchor_tag = "[" + " | ".join(loc_parts) + "]"
        kind_tag = f" ({seg['kind']})" if seg["kind"] != "paragraph" else ""
        lines.append(f"{anchor_tag}{kind_tag} {seg['text']}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Layout-aware scientific paper parser with section & anchor indexing.")
    parser.add_argument("--input", nargs="*", default=[], help="One or more input files (PDF/MD/TXT/DOCX/HTML/JSON).")
    parser.add_argument("--case-plan", default=None, help="Optional case_plan.json from inspect_case.py.")
    parser.add_argument("--out-dir", default="/app/work/parsed", help="Output directory for parsed artifacts.")
    parser.add_argument("--render-flagged-pages", action="store_true", help="Render pages with images/formulas/errors to PNG.")
    args = parser.parse_args()

    input_files: List[Tuple[str, Path]] = []
    if args.input:
        for idx, f in enumerate(args.input, start=1):
            p = Path(f)
            if p.exists() and p.is_file():
                input_files.append((f"D{idx}", p))
    elif args.case_plan:
        plan = load_json(args.case_plan, {})
        for m in plan.get("materials", []):
            p = Path(m["path"])
            if p.exists() and p.is_file():
                input_files.append((m.get("doc_id", f"D{len(input_files) + 1}"), p))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = out_dir / "page_previews"

    multi_doc = len(input_files) > 1
    pdf_backend = detect_pdf_backend()

    all_segments: List[Dict[str, Any]] = []
    all_sections: List[Dict[str, Any]] = []
    all_artifacts: Dict[str, Dict[str, Any]] = {}
    doc_reports: List[Dict[str, Any]] = []
    all_warnings: List[str] = []

    for doc_id, fpath in input_files:
        prefix = f"{doc_id}-" if multi_doc else ""
        if fpath.suffix.lower() == ".pdf":
            if pdf_backend == "pymupdf":
                raw_res = parse_pdf_pymupdf(fpath, args.render_flagged_pages, preview_dir)
            else:
                raw_res = parse_pdf_fallback(fpath, pdf_backend)
        else:
            raw_res = parse_plain_or_structured(fpath)

        segs, secs, arts = split_blocks_into_segments(doc_id, prefix, fpath.name, raw_res)
        all_segments.extend(segs)
        all_sections.extend(secs)
        all_artifacts.update(arts)
        all_warnings.extend(raw_res.get("warnings", []))

        unreadable = [p["page"] for p in raw_res.get("pages", []) if p.get("status") == "empty_or_scanned"]
        visual_pages = [p["page"] for p in raw_res.get("pages", []) if p.get("visual_or_formula_risk")]
        doc_reports.append(
            {
                "doc_id": doc_id,
                "filename": fpath.name,
                "path": str(fpath.resolve()),
                "backend": raw_res.get("backend"),
                "has_pdf_pages": bool(raw_res.get("has_pdf_pages", True)),
                "encrypted": raw_res.get("encrypted", False),
                "total_pages": raw_res.get("total_pages", 0),
                "header_footer_filtered_count": raw_res.get("header_footer_filtered_count", 0),
                "segment_count": len(segs),
                "section_count": len(secs),
                "artifact_anchor_count": len(arts),
                "unreadable_pages": unreadable,
                "visual_or_formula_risk_pages": visual_pages,
                "pages": raw_res.get("pages", []),
            }
        )

    full_text_sample = " ".join(s["text"] for s in all_segments[:30])
    lang = detect_language(full_text_sample)
    batch_plan = build_batch_plan(all_sections)
    is_long_document = len(batch_plan) > 1

    extraction_report = {
        "documents": doc_reports,
        "has_pdf_pages": any(d.get("has_pdf_pages", True) for d in doc_reports),
        "language": lang,
        "total_segments": len(all_segments),
        "total_sections": len(all_sections),
        "total_artifact_anchors": len(all_artifacts),
        "is_long_document": is_long_document,
        "batch_count": len(batch_plan),
        "any_encrypted": any(d.get("encrypted") for d in doc_reports),
        "any_unreadable_pages": any(d.get("unreadable_pages") for d in doc_reports),
        "warnings": all_warnings,
    }

    source_index = {
        "documents": [{"doc_id": d["doc_id"], "filename": d["filename"], "total_pages": d["total_pages"]} for d in doc_reports],
        "segments": {s["id"]: s for s in all_segments},
        "segment_order": [s["id"] for s in all_segments],
        "artifacts": all_artifacts,
    }

    body_section_ids = [
        s["section_id"]
        for s in all_sections
        if not s.get("is_reference_or_appendix") and not s["section_id"].endswith("S00") and s.get("paragraph_ids")
    ]
    section_outline = {
        "sections": all_sections,
        "non_reference_section_ids": [s["section_id"] for s in all_sections if not s.get("is_reference_or_appendix")],
        "body_section_ids": body_section_ids,
        "batch_plan": batch_plan,
    }

    save_json(out_dir / "source_index.json", source_index)
    save_json(out_dir / "section_outline.json", section_outline)
    save_json(out_dir / "extraction_report.json", extraction_report)
    (out_dir / "anchored_paper.md").write_text(
        render_anchored_markdown(all_segments, all_sections, extraction_report),
        encoding="utf-8",
    )

    print(
        f"Parsed {len(doc_reports)} doc(s) -> {len(all_segments)} segments, "
        f"{len(all_sections)} sections, {len(all_artifacts)} fig/tab/eq anchors, "
        f"{len(batch_plan)} batch(es), lang={lang}."
    )


if __name__ == "__main__":
    main()
