---
name: sci-pdf-parser
description: >
  Layout-aware scientific PDF parser and evidence-grounded reader. Use when reading, extracting,
  translating, or answering questions from academic/scientific PDFs with multi-column layouts,
  booktabs/three-line or multi-page tables, numbered equations, sub/superscripts, Greek/math
  symbols, headers/footers/footnotes, or scanned/garbled pages requiring OCR and page/block
  provenance.
---

# Scientific PDF Parser (`sci-pdf-parser`)

Extract complete, reading-order-faithful, structurally decomposed, and source-grounded scientific content from PDF documents under strict honesty and budget discipline.

## Design Goals
1. **提得全 (Complete Extraction)**: Preserve body paragraphs, headings, figure/table captions, equations, footnotes, and references without losing lines or mixing headers/footers/marginalia into body prose.
2. **排得对 (Accurate Reading Order)**: Separate side-by-side columns, full-width spanning titles/abstracts/tables, and cross-page continuations using adaptive XY-Cut column ordering and hyphenation repair.
3. **拆得清 (Clean Structure)**: Reconstruct section hierarchy, booktabs/grid tables (`Markdown` + 2D grid + multi-page continuation merging), equation numbers, and span-level subscripts/superscripts (`_{...}`, `^{...}`).
4. **核得回 (Verifiable Provenance)**: Anchor every claim, extracted table, and answer to physical PDF `page`, `block_id` / `table_id` / `eq_id`, `bbox`, and short verbatim quotes; flag scanned, garbled, missing, or conflicting items in `uncertainty` instead of guessing.

## Skill Root & Path Resolution
Locate the installed skill directory automatically before running commands:
```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
```
Work inside `/app/work` for intermediate outputs and write final deliverables under `/app/output`.

## Mandatory 5-Step Workflow

### Step 1: Inspect Case Contract & Discover Inputs
Always inspect `/input/case.json` and `/input/task.md` first—never infer schema or facts from filenames alone:
```bash
mkdir -p /app/work/parsed /app/work/ocr /app/output
python3 "$SKILL_DIR/scripts/inspect_case.py" \
  --case /input/case.json --task /input/task.md --input-dir /input \
  --out /app/work/case_brief.json
```
Read `/app/work/case_brief.json` and `/input/task.md` to confirm `case_id`, all PDF paths, and every field in `output_contract`.

### Step 2: Tool Probe & PDF Preflight
Probe installed PDF/OCR backends and triage every page (`text`, `scanned_or_image`, `garbled_font_layer`, `empty`, `encrypted_requires_password`):
```bash
python3 "$SKILL_DIR/scripts/preflight.py" "<PDF_PATH>" --out /app/work/preflight.json
```
- If exit code is non-zero (`encrypted_requires_password`, `corrupt_or_unreadable`, `empty_file`, `missing_file`), do **not** fabricate content. Immediately emit an honest `/app/output/result.json` with `status: "failed"` or `"unanswerable"`, recording the exact reason and manual handoff path in `uncertainty`.

### Step 3: Layout-Aware Extraction (`parse_pdf.py`)
Run the deterministic multi-backend parser (prefers `pymupdf`, falls back cleanly to `pdftohtml -xml` or `pdftotext`):
```bash
python3 "$SKILL_DIR/scripts/parse_pdf.py" "<PDF_PATH>" --out-dir /app/work/parsed
```
Key generated files:
- `/app/work/parsed/parsed_document.md` — token-efficient, reading-order-restored Markdown tagged with `[pXX_bYYY]` block IDs, tables, equations, and footnotes. Read this instead of dumping giant raw PDF text.
- `/app/work/parsed/parsed_document.json` — full machine-readable structure (`pages`, `sections`, `tables`, `merged_tables`, `figures`, `equations`, `footnotes`, `references`, `headers_footers`, `quality_metrics`, `uncertainty`).
- `/app/work/parsed/tables/` — standalone Markdown tables (`pXX_tYY.md` and `merged_*.md`).

### Step 4: Conditional OCR & Visual Verification (`ocr_fallback.py`)
Whenever `quality_metrics.ocr_recommended_pages` is non-empty (scanned pages, image-only tables, or `garble_ratio >= 0.05`)—or if a dense mathematical formula needs visual verification—render 300 DPI PNGs and run OCR:
```bash
python3 "$SKILL_DIR/scripts/ocr_fallback.py" "<PDF_PATH>" \
  --from-parsed /app/work/parsed/parsed_document.json \
  --out-dir /app/work/ocr --dpi 300
```
- Read `/app/work/ocr/ocr_pages.md` and inspect `mean_confidence` and `low_confidence_words`.
- If `tesseract` is missing (`rendered_png_no_tesseract`) or confidence on critical numbers/symbols is low (`< 60%`), keep the PNG path in `/app/output/` (if visual artifact needed) or `uncertainty.human_handoff`, and **do not guess** illegible characters.

### Step 5: Draft Answer, Preserve Envelope & Audit (`audit_output.py`)
Write `/app/output/result.json` (plus any requested Markdown/JSON/rendered preview files in `/app/output/`) satisfying:
1. Exact `case_id` and **every** field declared in `/input/case.json`'s `output_contract`.
2. Mandatory provenance envelope keys:
   - `case_id`: matching `/input/case.json`
   - `status`: `"completed"` | `"partial"` | `"unanswerable"` | `"failed"`
   - `sources`: list of `{"claim_or_field", "page", "printed_page", "block_id", "section", "quote"}` citing verified IDs from `parsed_document.json`
   - `uncertainty`: list of `{"target", "reason", "details", "human_handoff"}` (empty `[]` only if zero ambiguity/ocr/unreported fields exist)
   - `change_records`: list of processing/repair actions taken (e.g. header/footer stripping, cross-page table merge, OCR fallback)
3. **Save an honest draft early** inside `/app/output/result.json` before polishing so budget limits never lose partial progress, then validate with:
```bash
python3 "$SKILL_DIR/scripts/audit_output.py" \
  --result /app/output/result.json \
  --case /input/case.json \
  --parsed /app/work/parsed/parsed_document.json \
  --out /app/work/audit_report.json
```
Fix any errors reported by `audit_output.py` until `"passed": true`.

## Edge Cases & Honesty Rules
- **Not reported / Unanswerable**: If the PDF does not state a requested parameter, output `null` (or explicit `"not_reported"`) and add an `uncertainty` entry (`reason: "not_reported_in_source"`) citing the closest checked section/page.
- **Conflicting values in paper** (e.g., abstract vs table): Report the discrepancy explicitly in both the answer field/notes and `uncertainty` (`reason: "conflict_in_source"`), citing both `block_id`/`table_id` locations.
- **Page labels vs PDF physical pages**: Always record 1-based physical PDF `page`, and include `printed_page` whenever footer/label differs from physical page index.
