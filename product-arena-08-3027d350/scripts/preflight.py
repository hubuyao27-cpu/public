#!/usr/bin/env python3
"""Environment probe and scientific PDF preflight diagnostic tool."""

import argparse
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def probe_tools():
    """Detect available Python PDF/OCR modules and CLI binaries."""
    py_mods = {}
    for name in ("pymupdf", "fitz", "pdfplumber", "pypdf", "pypdf2", "pdfminer", "pytesseract", "PIL", "cv2", "numpy"):
        try:
            mod = importlib.import_module(name)
            py_mods[name] = getattr(mod, "__version__", "installed")
        except Exception:
            py_mods[name] = None

    cli_bins = {}
    for b in ("pdftotext", "pdftohtml", "pdfinfo", "pdftoppm", "tesseract", "gs", "soffice", "xelatex"):
        cli_bins[b] = shutil.which(b)

    return {"python": py_mods, "cli": cli_bins}


def compute_garble_stats(text: str):
    """Quantify replacement chars, PUA glyphs, CID markers, and control chars."""
    if not text:
        return {"non_ws_chars": 0, "garble_chars": 0, "garble_ratio": 0.0, "cid_hits": 0}
    non_ws = [c for c in text if not c.isspace()]
    n = len(non_ws)
    if n == 0:
        return {"non_ws_chars": 0, "garble_chars": 0, "garble_ratio": 0.0, "cid_hits": 0}
    garble = 0
    for c in non_ws:
        cp = ord(c)
        if cp == 0xFFFD or (0xE000 <= cp <= 0xF8FF) or (cp < 32):
            garble += 1
    cid_hits = len(re.findall(r"\(cid:\d+\)", text))
    garble += cid_hits * 3
    return {
        "non_ws_chars": n,
        "garble_chars": garble,
        "garble_ratio": round(min(1.0, garble / max(1, n)), 4),
        "cid_hits": cid_hits,
    }


def choose_backend(tools):
    """Select the highest-fidelity available extraction backend."""
    py = tools["python"]
    cli = tools["cli"]
    if py.get("pymupdf") or py.get("fitz"):
        return "pymupdf"
    if py.get("pdfplumber"):
        return "pdfplumber"
    if cli.get("pdftohtml"):
        return "pdftohtml_xml"
    if cli.get("pdftotext"):
        return "pdftotext"
    if py.get("pypdf") or py.get("pypdf2"):
        return "pypdf"
    return None


def analyze_pdf_pymupdf(pdf_path: str):
    """Inspect encryption, page count, text density, image coverage, and garble ratio via PyMuPDF."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        return {"status": "corrupt_or_unreadable", "error": str(exc), "pages": []}

    if doc.is_encrypted and doc.needs_pass:
        return {
            "status": "encrypted_requires_password",
            "page_count": len(doc),
            "is_encrypted": True,
            "needs_pass": True,
            "pages": [],
        }

    pages = []
    scanned_pages = []
    garbled_pages = []
    empty_pages = []

    for idx in range(len(doc)):
        page = doc[idx]
        pno = idx + 1
        rect = page.rect
        page_area = max(1.0, rect.width * rect.height)
        raw_text = page.get_text("text") or ""
        gstat = compute_garble_stats(raw_text)

        img_area = 0.0
        img_count = 0
        for info in page.get_image_info(xrefs=False):
            bbox = info.get("bbox")
            if bbox:
                w = max(0.0, bbox[2] - bbox[0])
                h = max(0.0, bbox[3] - bbox[1])
                img_area += w * h
                img_count += 1
        image_ratio = round(min(1.0, img_area / page_area), 4)

        p_type = "text"
        if gstat["non_ws_chars"] == 0 and img_count == 0:
            p_type = "empty"
            empty_pages.append(pno)
        elif (gstat["non_ws_chars"] < 40 and image_ratio >= 0.25) or (gstat["non_ws_chars"] < 15 and img_count > 0):
            p_type = "scanned_or_image"
            scanned_pages.append(pno)
        elif gstat["garble_ratio"] >= 0.05:
            p_type = "garbled_font_layer"
            garbled_pages.append(pno)

        pages.append({
            "page": pno,
            "printed_label": page.get_label() or None,
            "width_pt": round(rect.width, 2),
            "height_pt": round(rect.height, 2),
            "non_ws_chars": gstat["non_ws_chars"],
            "garble_ratio": gstat["garble_ratio"],
            "cid_hits": gstat["cid_hits"],
            "image_count": img_count,
            "image_area_ratio": image_ratio,
            "page_type": p_type,
        })

    doc.close()
    return {
        "status": "ok",
        "page_count": len(pages),
        "is_encrypted": False,
        "needs_pass": False,
        "scanned_pages": scanned_pages,
        "garbled_pages": garbled_pages,
        "empty_pages": empty_pages,
        "ocr_recommended_pages": sorted(set(scanned_pages + garbled_pages)),
        "pages": pages,
    }


def analyze_pdf_cli(pdf_path: str, tools):
    """Fallback inspection via pdfinfo / pdftotext when PyMuPDF is unavailable."""
    cli = tools["cli"]
    page_count = None
    encrypted = False
    if cli.get("pdfinfo"):
        res = subprocess.run([cli["pdfinfo"], pdf_path], capture_output=True, text=True)
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()
            if "encrypted" in err.lower() or "password" in err.lower():
                return {"status": "encrypted_requires_password", "is_encrypted": True, "needs_pass": True, "error": err}
            return {"status": "corrupt_or_unreadable", "error": err}
        for line in res.stdout.splitlines():
            if line.startswith("Pages:"):
                page_count = int(line.split(":", 1)[1].strip())
            elif line.startswith("Encrypted:"):
                encrypted = "yes" in line.lower()

    if cli.get("pdftotext") and page_count:
        pages = []
        scanned = []
        garbled = []
        for pno in range(1, page_count + 1):
            r = subprocess.run([cli["pdftotext"], "-f", str(pno), "-l", str(pno), pdf_path, "-"], capture_output=True, text=True, errors="replace")
            txt = r.stdout or ""
            gs = compute_garble_stats(txt)
            ptype = "text"
            if gs["non_ws_chars"] < 20:
                ptype = "scanned_or_empty"
                scanned.append(pno)
            elif gs["garble_ratio"] >= 0.05:
                ptype = "garbled_font_layer"
                garbled.append(pno)
            pages.append({"page": pno, "non_ws_chars": gs["non_ws_chars"], "garble_ratio": gs["garble_ratio"], "page_type": ptype})
        return {
            "status": "ok",
            "page_count": page_count,
            "is_encrypted": encrypted,
            "scanned_pages": scanned,
            "garbled_pages": garbled,
            "ocr_recommended_pages": sorted(set(scanned + garbled)),
            "pages": pages,
        }

    return {"status": "limited_probe", "page_count": page_count, "is_encrypted": encrypted, "pages": []}


def run_preflight(pdf_path: str):
    """Run full environment and PDF preflight checks."""
    tools = probe_tools()
    backend = choose_backend(tools)
    p = Path(pdf_path)

    if not p.exists():
        return {
            "pdf_path": str(p),
            "exists": False,
            "status": "missing_file",
            "recommended_backend": backend,
            "tools": tools,
        }

    if p.stat().st_size == 0:
        return {
            "pdf_path": str(p),
            "exists": True,
            "file_bytes": 0,
            "status": "empty_file",
            "recommended_backend": backend,
            "tools": tools,
        }

    with open(p, "rb") as f:
        header = f.read(16)
    if b"%PDF-" not in header:
        return {
            "pdf_path": str(p),
            "exists": True,
            "file_bytes": p.stat().st_size,
            "status": "invalid_pdf_header",
            "header_hex": header.hex(),
            "recommended_backend": backend,
            "tools": tools,
        }

    if tools["python"].get("pymupdf") or tools["python"].get("fitz"):
        pdf_diag = analyze_pdf_pymupdf(str(p))
    else:
        pdf_diag = analyze_pdf_cli(str(p), tools)

    ocr_available = bool(tools["cli"].get("tesseract") or tools["python"].get("pytesseract"))
    render_available = bool(tools["python"].get("pymupdf") or tools["python"].get("fitz") or tools["cli"].get("pdftoppm"))

    return {
        "pdf_path": str(p),
        "exists": True,
        "file_bytes": p.stat().st_size,
        "recommended_backend": backend,
        "ocr_available": ocr_available,
        "render_available": render_available,
        "tools": tools,
        **pdf_diag,
    }


def main():
    parser = argparse.ArgumentParser(description="Preflight check for PDF parsing tools and document health.")
    parser.add_argument("pdf", help="Path to the input PDF file.")
    parser.add_argument("--out", help="Optional path to write JSON diagnostics.")
    args = parser.parse_args()

    report = run_preflight(args.pdf)
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(payload, encoding="utf-8")
    print(payload)
    if report.get("status") != "ok":
        sys.exit(2)


if __name__ == "__main__":
    main()
