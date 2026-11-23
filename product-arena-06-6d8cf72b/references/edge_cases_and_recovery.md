# Edge Cases, Exception Detection & Recovery Playbook

This guide defines deterministic identification and handling rules for abnormal or challenging scientific documents.

## 1. Encrypted or Permission-Restricted PDFs
- **Detection**: `inspect_case.py` and `parse_paper.py` inspect `doc.is_encrypted and doc.needs_pass` and record `encrypted: true`.
- **Action**:
  1. Do NOT attempt password bypassing or fabricate content from the filename.
  2. Run `assemble_notes.py --checkpoint final` with `status: "unable_to_determine"` (if all inputs locked) or `"partial"` (if other materials remain readable).
  3. Record exact filename in `manual_takeover_points` with `reason: "encrypted_pdf"`.

## 2. Scanned PDFs / Missing Text Layer (`empty_or_scanned`)
- **Detection**: Page contains `< 40` extractable characters (often paired with `image_count >= 1`).
- **Action**:
  1. `parse_paper.py` automatically attempts `tesseract` OCR (`chi_sim+eng` then `eng`) if `tesseract` is installed in the runtime image (`ocr-tool`), tagging extracted text with `ocr_used: true`.
  2. If OCR is unavailable or yields `< 40` characters, `parse_paper.py` marks the page as `unreadable_pages`.
  3. Never fill in missing scanned pages from memory or domain priors. Add the exact page numbers to `uncertainty.unable_to_determine` and `manual_takeover_points`, and set overall `status = "partial"` (or `"unable_to_determine"` if all pages failed).

## 3. Two-Column Interleaving & Header/Footer Pollution
- **Detection**: `parse_paper.py` evaluates block bounding boxes (`x0, y0, x1, y1`) relative to page width `W` and height `H`.
- **Action**:
  - Cross-page repeated margins (`y1 < 0.09H` or `y0 > 0.90H`) and standalone page numbers are filtered out before segmentation.
  - Pages with distinct left and right column blocks are split horizontally at full-width spanning figures/titles (`horizontal bands`) and sorted `left column top-to-bottom -> right column top-to-bottom` per band.
  - If a non-PyMuPDF fallback (`pdftotext`/`pypdf`) is used and `anchored_paper.md` still exhibits line interleaving, record `"layout_interleaving_risk"` in `uncertainty` and cross-check before quoting.

## 4. Missing Equations / Visual-Only Figures (`garbled_text_risk` & `visual_or_formula_risk`)
- **Detection**: High `\ufffd` replacement character ratio (`> 5%` or `> 5` chars per page) or figure captions referencing plots whose numeric values are absent from body text.
- **Action**:
  1. Run `parse_paper.py --render-flagged-pages` to render flagged pages to PNGs under `/app/work/parsed/page_previews/`.
  2. If the Agent harness supports vision inspection, verify equations/plots directly from the rendered PNGs and mark `verification_mode: "human_review_required"` with `visual_inspection` note.
  3. If unrecoverable without visual reading, state `"unable_to_determine_without_visual_plot_or_equation"` in `task_responses` / `uncertainty`, never invent plot numbers.

## 5. Ultra-Long Papers (> 3,500 Words or > 15 Pages)
- **Detection**: `parse_paper.py` sets `is_long_document: true` and creates `section_outline.json["batch_plan"]`.
- **Action**:
  1. First save an early skeletal checkpoint via `assemble_notes.py --checkpoint partial`.
  2. Read `anchored_paper.md` batch-by-batch following `batch_plan` (`B1`, `B2`, ...), writing `batch_1.json`, `batch_2.json`, etc.
  3. Merge all batches deterministically with `merge_notes.py`, then audit with `verify_notes.py` and finalize with `assemble_notes.py --checkpoint final`.
  4. This guarantees staying well within the 64-request / 900s / 16,384-token budget.

## 6. Non-Chinese / Bilingual / Cross-Document Conflicts
- **Non-Chinese Papers**: Keep canonical technical terms and exact verbatim `quote` snippets in the original source language inside `evidence` (so `verify_notes.py` passes strict substring verification), while writing summaries/explanations in the language requested by `task.md` (default Chinese or bilingual Chinese+English).
- **Material Conflicts**: When two paragraphs, text vs. table, or main text vs. supplement report contradictory values, record BOTH anchors (`source_a` and `source_b`) in `not_reported_and_conflicts.conflicts` instead of silently picking one.
