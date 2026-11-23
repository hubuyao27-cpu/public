#!/usr/bin/env python3
"""Render high-DPI page PNGs and run Tesseract OCR fallback for scanned/garbled PDF pages."""

import argparse
import csv
import io
import json
import shutil
import subprocess
from pathlib import Path


def render_page_png(pdf_path: str, page_no: int, out_png: Path, dpi: int = 300):
    """Render a 1-indexed PDF page to PNG via PyMuPDF or pdftoppm."""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fitz_mod = None
    try:
        import pymupdf as fitz_mod
    except ImportError:
        try:
            import fitz as fitz_mod
        except ImportError:
            fitz_mod = None

    if fitz_mod is not None:
        doc = fitz_mod.open(pdf_path)
        if page_no < 1 or page_no > len(doc):
            doc.close()
            raise ValueError(f"Page {page_no} out of range (1..{len(doc)})")
        pix = doc[page_no - 1].get_pixmap(dpi=dpi)
        pix.save(str(out_png))
        doc.close()
        return str(out_png)

    if shutil.which("pdftoppm"):
        prefix = out_png.with_suffix("")
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), "-f", str(page_no), "-l", str(page_no), "-singlefile", pdf_path, str(prefix)],
            check=True,
            capture_output=True,
        )
        if out_png.exists():
            return str(out_png)

    raise RuntimeError("Neither pymupdf nor pdftoppm is available to render PDF pages.")


def preprocess_png_for_ocr(png_path: Path):
    """Apply contrast enhancement via Pillow when available."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return png_path

    img = Image.open(png_path).convert("L")
    img = ImageOps.autocontrast(img, cutoff=1)
    proc_path = png_path.with_name(png_path.stem + "_prep.png")
    img.save(proc_path)
    return proc_path


def run_tesseract_tsv(img_path: Path, lang: str = "eng"):
    """Run tesseract TSV extraction and compute mean confidence + low-confidence tokens."""
    tess_bin = shutil.which("tesseract")
    if not tess_bin:
        return None

    avail = subprocess.run([tess_bin, "--list-langs"], capture_output=True, text=True)
    installed = set(avail.stdout.splitlines()[1:]) if avail.returncode == 0 else set()
    req_langs = [l for l in lang.split("+") if (not installed or l in installed)]
    use_lang = "+".join(req_langs) if req_langs else "eng"

    res = subprocess.run(
        [tess_bin, str(img_path), "stdout", "-l", use_lang, "--psm", "3", "tsv"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if res.returncode != 0:
        return {"error": (res.stderr or "").strip(), "lang_used": use_lang}

    reader = csv.DictReader(io.StringIO(res.stdout), delimiter="\t")
    lines = {}
    confs = []
    uncertain_words = []
    for row in reader:
        txt = (row.get("text") or "").strip()
        if not txt:
            continue
        try:
            conf = float(row.get("conf", -1))
        except ValueError:
            conf = -1.0
        key = (row.get("block_num"), row.get("par_num"), row.get("line_num"))
        lines.setdefault(key, []).append(txt)
        if conf >= 0:
            confs.append(conf)
            if conf < 60.0 and len(txt) >= 2:
                uncertain_words.append({"word": txt, "conf": conf})

    full_text = "\n".join(" ".join(words) for words in lines.values())
    mean_conf = round(sum(confs) / len(confs), 2) if confs else None
    return {
        "lang_used": use_lang,
        "text": full_text,
        "mean_confidence": mean_conf,
        "word_count": len(confs),
        "low_confidence_words": uncertain_words[:40],
    }


def main():
    parser = argparse.ArgumentParser(description="Render PDF pages and run OCR fallback on scanned/garbled pages.")
    parser.add_argument("--input", required=True, help="Path to PDF file")
    parser.add_argument("--pages", help="Comma-separated 1-based pages (e.g. 1,3)")
    parser.add_argument("--from-report", help="Read ocr_recommended_pages from extraction_report.json")
    parser.add_argument("--out-dir", default="/app/work/ocr", help="Output directory for PNGs and OCR JSON")
    parser.add_argument("--dpi", type=int, default=300, help="Rendering DPI (default 300)")
    parser.add_argument("--lang", default="eng+chi_sim", help="Preferred Tesseract languages")
    args = parser.parse_args()

    pages = []
    if args.pages:
        pages.extend(int(x.strip()) for x in args.pages.split(",") if x.strip())
    if args.from_report and Path(args.from_report).exists():
        rep = json.loads(Path(args.from_report).read_text(encoding="utf-8"))
        pages.extend(rep.get("ocr_recommended_pages", []))
    pages = sorted(set(pages))

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if not pages:
        empty_res = {"status": "skipped_no_pages_needed", "pages": []}
        (out / "ocr_results.json").write_text(json.dumps(empty_res, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(empty_res, indent=2, ensure_ascii=False))
        return

    results = []
    md_lines = ["# OCR & Visual Inspection Results\n"]
    tess_present = bool(shutil.which("tesseract"))

    for pno in pages:
        png_path = out / f"page_{pno:03d}.png"
        render_page_png(args.input, pno, png_path, dpi=args.dpi)
        prep_path = preprocess_png_for_ocr(png_path)
        ocr_info = run_tesseract_tsv(prep_path, lang=args.lang) if tess_present else None
        results.append({
            "page": pno,
            "rendered_png": str(png_path),
            "preprocessed_png": str(prep_path),
            "tesseract_available": tess_present,
            "ocr": ocr_info,
        })
        md_lines.append(f"## Page {pno} (PNG: `{png_path.name}`)\n")
        if ocr_info and "text" in ocr_info:
            md_lines.append(f"- Mean OCR confidence: {ocr_info.get('mean_confidence')}% (lang={ocr_info.get('lang_used')})\n")
            if ocr_info.get("low_confidence_words"):
                low_str = ", ".join(f"{w['word']}({w['conf']})" for w in ocr_info["low_confidence_words"][:15])
                md_lines.append(f"- Low-confidence tokens (<60%): {low_str}\n")
            md_lines.append("\n```text\n" + ocr_info["text"] + "\n```\n")
        else:
            md_lines.append("- Tesseract unavailable or errored; inspect rendered PNG directly or mark unreadable.\n")

    final = {
        "status": "ocr_completed" if tess_present else "rendered_png_no_tesseract",
        "pdf_path": args.input,
        "pages_processed": pages,
        "results": results,
    }
    (out / "ocr_results.json").write_text(json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "ocr_pages.md").write_text("\n".join(md_lines), encoding="utf-8")
    print(json.dumps({"status": final["status"], "pages_processed": pages, "out_dir": str(out)}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
