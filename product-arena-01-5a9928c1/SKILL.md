---
name: scientific-literature-translator
description: >
  Layout-aware, glossary-locked, paragraph-aligned, and mechanically audited scientific
  literature translation skill. Use when translating, aligning, or verifying scientific PDFs
  or papers into bilingual Chinese-English outputs with multi-column XY-Cut ordering, booktabs
  table preservation, sub/superscript and formula fidelity, OCR fallback, locked glossary
  enforcement, and page/segment provenance.
---

# Scientific Literature Translator (`scientific-literature-translator`)

Translate scientific literature into **verifiable, layout-faithful, paragraph-aligned bilingual output** with locked terminology, preserved tables/formulas/units/negations, and full page/segment provenance.

## Core Guarantees
1. **术语稳 (Terminology Stability)**: Seed from `glossary_candidates.json`, lock domain terms in `glossary.json` before translating, and enforce consistent Chinese terms across Title, Abstract, body, captions, tables, and Conclusions.
2. **段落全 (Complete & Ordered Extraction)**: Use adaptive XY-Cut column sorting, header/footer/marginalia stripping, booktabs table reconstruction, and cross-page/cross-column continuation tracking (`P001`, `P002`, ...) so no paragraph is dropped, duplicated, or scrambled across columns.
3. **关系准 (Relation & Formula Fidelity)**: Audit numbers, operators (`±`, `≤`, `≥`, `≈`), physical units (`kJ/mol`, `nm`, `mg/mL`, `%`, `K`), chemical/math scripts (`CHCl_{3}`, `10^{-6}`), citations (`[1]`), figure/table/equation refs, table Markdown shapes, and bidirectional negation/concession polarity.
4. **可核验 (Verifiable Provenance)**: Link every translated block to `(page, printed_page, segment_id, section, quote)`, record `change_records`, and flag scanned/garbled/ambiguous segments in `uncertainty` rather than fabricating content.

---

## Skill Path Resolution & Runtime Rules
Resolve the skill directory first (works under Harbor `/skills/weekly-skill` and local directories):
```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
mkdir -p /app/work/extracted /app/work/ocr /app/output
```
- Budget discipline (64 requests / 900 s): read `source_preview.md` and `glossary_candidates.json` instead of raw full-PDF dumps; save incremental translations early.
- Preserve **every** `output_contract` field and `case_id` from `/input/case.json` in `/app/output/result.json`.

---

## 6-Step Execution Workflow

### Step 1: Inspect Case Contract & Discover Inputs
```bash
python3 "$SKILL_DIR/scripts/inspect_case.py" \
  --case /input/case.json --task /input/task.md --input-dir /input \
  --out /app/work/case_brief.json
```
Read `/app/work/case_brief.json` and `/input/task.md` to confirm `case_id`, input PDF/text path(s), translation scope, target language, and `output_contract` keys.

### Step 2: Layout-Aware Extraction, Segmentation & Glossary Seeding
```bash
python3 "$SKILL_DIR/scripts/extract_and_segment.py" \
  --input "<INPUT_PATH>" \
  --out-dir /app/work/extracted
```
Generated files in `/app/work/extracted/`:
- `segments.json`: XY-Cut ordered segments (`P001`..`PNNN`) with `kind` (`title` | `abstract` | `heading` | `paragraph` | `table` | `caption` | `equation` | `footnote` | `reference` | `unreadable_page`), `page`, `bbox`, `extracted_cues`, and `prev_context_tail` for cross-column/cross-page sentences.
- `source_preview.md`: Compact token-efficient reading view with inline segment IDs.
- `glossary_candidates.json`: Automatically mined `Full Term (ACR)` and recurring domain phrases.
- `extraction_report.json`: Per-page `char_count`, `garble_ratio`, `ocr_recommended_pages`, and encryption/corruption status.

**Exception Handling (Encrypted / Scanned / Garbled Pages)**:
- If `extraction_report.json` reports `encrypted_requires_password` or `corrupt_or_unreadable`: do not guess; assemble an honest `status: "unable_to_determine"` report with exact reason in `uncertainty`.
- If `ocr_recommended_pages` is non-empty, run Step 2b before translating:
```bash
python3 "$SKILL_DIR/scripts/ocr_fallback.py" \
  --input "<INPUT_PATH>" \
  --from-report /app/work/extracted/extraction_report.json \
  --out-dir /app/work/ocr --dpi 300
```
Inspect `/app/work/ocr/ocr_pages.md` and `mean_confidence`; mark tokens `< 60%` confidence (or pages where `tesseract` is missing) as `[UNCERTAIN: ...]` with `"uncertain": true` rather than fabricating.

### Step 3: Lock Domain Glossary (`/app/work/glossary.json`)
Review `/app/work/extracted/glossary_candidates.json` plus Title/Abstract/Headings, and lock canonical translations in `/app/work/glossary.json`:
```json
{
  "terms": [
    {
      "source": "specific surface area",
      "target": "比表面积",
      "allowed_variants": ["比表面积（specific surface area, SSA）", "SSA"],
      "note": "材料表征标准译名，全篇统一"
    }
  ]
}
```
Include `Full Name (ACR)` parenthetical form on first occurrence.

### Step 4: Translate Segment-by-Segment with Continuation Context
Translate `/app/work/extracted/segments.json` in ordered chunks (10–15 segments per batch), writing `/app/work/translated_segments.json`:
```json
[
  {
    "id": "P001",
    "page": 1,
    "section": "Title",
    "source_text": "...",
    "target_text": "...",
    "rationale": "锁定领域术语；保留公式/数值与否定限定",
    "uncertain": false,
    "uncertainty_note": ""
  }
]
```
**Rules**:
- For `kind == "table"` segments: preserve the Markdown pipe table structure (`| col | ... |`), translating header/text labels cleanly while keeping numerical cells exact.
- For segments with `continued_from_prev == true`: use `prev_context_tail` to keep sentence grammar continuous across columns/pages while keeping each segment aligned strictly 1-to-1 to its own `id`.
- Preserve all numbers, units, operators (`±`, `≤`, `≥`), `CHCl_{3}`/`10^{-6}` scripts, `[1]` citations, `Fig. X` / `Table Y` / `Eq. (Z)` markers, and negation/concession polarity.

### Step 5: 7-Axis Mechanical Verification & Fix Loop
```bash
python3 "$SKILL_DIR/scripts/verify_translation.py" \
  --segments /app/work/extracted/segments.json \
  --translated /app/work/translated_segments.json \
  --glossary /app/work/glossary.json \
  --out /app/work/audit_report.json
```
Fix all `ERROR` findings (missing/duplicate segments, glossary mismatches, dropped citations, Markdown table shape mismatches) and inspect high-signal `WARNING` items (missing units, operators, formulas, negation inversions). Re-run `verify_translation.py` until clean.

### Step 6: Assemble Deliverables & Strict Contract Verification
If `/input/case.json` defines custom `output_contract` fields beyond the default bilingual deliverables, save those mapped values in `/app/work/extra_fields.json` and pass `--extra-fields`:
```bash
python3 "$SKILL_DIR/scripts/assemble_output.py" \
  --case /input/case.json \
  --translated /app/work/translated_segments.json \
  --glossary /app/work/glossary.json \
  --audit /app/work/audit_report.json \
  --extraction /app/work/extracted/extraction_report.json \
  --out-dir /app/output \
  --strict
```
Deliverables under `/app/output/`:
- `result.json` (`case_id`, all `output_contract` fields, `status`, `bilingual_segments`, `glossary`, `audit_metrics`, `sources`, `uncertainty`, `change_records`, `limitations`)
- `bilingual_translation.md`
- `glossary.md`
