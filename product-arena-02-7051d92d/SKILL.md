---
name: academic-language-polisher
description: >
  Sentence-level, minimal-edit, terminology-locked, and hedge-preserving academic language polishing
  and bilingual editing skill. Use when proofreading, polishing, copyediting, calibrating academic
  register, fixing grammar/syntax/Chinglish/translationese, enforcing full-text glossary & acronym
  consistency, or producing verifiable redline diffs and change logs for Markdown, LaTeX, DOCX,
  TXT, or PDF academic manuscripts, abstracts, theses, grant proposals, and reviewer responses.
  Do NOT trigger or rewrite text when the user only asks pure factual or conceptual Q&A without
  requesting editing, proofreading, polishing, or rewriting.
---

# Academic Language Polisher (`academic-language-polisher`)

Transform academic drafts into **grammatically accurate, register-calibrated, terminology-consistent, and semantically faithful** manuscripts with **sentence-level provenance and mechanical safety checks**.

## Core Guarantees
1. **改得对 (Accurate Grammar & Syntax)**: Fix subject-verb agreement, tense/voice mismatches, articles, prepositions, singular/plural, dangling modifiers, collocations, punctuation, and sentence boundaries rather than masking errors with paraphrase.
2. **说得像 (Register Calibration & Full-Text Terminology Lock)**: Adapt tone cleanly to `journal_article`, `thesis_dissertation`, `grant_proposal`, `reviewer_response`, `english_abstract`, or `bilingual_zh_en`; lock glossary terms and `Full Term (ACR)` first-occurrence conventions across all sections.
3. **没改坏 (Zero Meaning Drift & Protected-Span Conservation)**: Conserve every number, physical unit, math/LaTeX block, citation marker, figure/table/equation cross-ref, negation/conditional/quantifier polarity, causality vs. correlation distinction, and epistemic hedge strength (`may` != `proves`).
4. **讲得清 (Auditable Sentence-Level Track Changes)**: Deliver the complete editable manuscript plus per-sentence `original`, `polished`, word-level diff, controlled `edit_types`, and concrete `rationale`, while surfacing glossary conflicts and ambiguous edits for human confirmation.

---

## Runtime Paths & Budget Discipline
Resolve `$SKILL_DIR` automatically before executing any step:
```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
mkdir -p /app/work /app/output
```
- **Inputs**: `/input/case.json`, `/input/task.md` (or `./task.md`), and actual manuscript/glossary/style files under `/input/`. Always read actual files rather than guessing from labels.
- **Trigger Guard**: If `/input/task.md` is purely a factual/conceptual Q&A asking no editing, rewriting, proofreading, or polishing, do NOT rewrite text; output an honest `not_applicable_qa` status in `result.json`.
- **Budget Discipline (64 requests / 900 s)**: Read `/app/work/polish_brief.md` and `/app/work/glossary_resolved.json` first rather than re-dumping giant raw files; save an early partial checkpoint at Step 3.

---

## 6-Step Execution Workflow

### Step 1: Inspect Case Contract, Genre, Format & Inputs
```bash
python3 "$SKILL_DIR/scripts/inspect_case.py" \
  --input-dir /input \
  --out /app/work/case_plan.json
```
Read `/app/work/case_plan.json` (and `/input/task.md`) to confirm `case_id`, all `output_contract` fields, detected `genre`, `language`, `edit_intensity`, `manuscript_files`, `glossary_files`, `missing_referenced_files`, and `is_non_polish_qa`.

### Step 2: Segment Manuscript, Mine Terminology & Pre-Audit Diagnostics
```bash
python3 "$SKILL_DIR/scripts/analyze_manuscript.py" \
  --case-plan /app/work/case_plan.json \
  --out-dir /app/work
```
Produces:
- `/app/work/source_segments.json`: Lossless paragraph/sentence segmentation (`P001.S01`...) with `protected_spans` (numbers/units, LaTeX math, citations, cross-refs, chemical formulas), hedge tier (`1..5`), causality/negation markers, and diagnostic flags.
- `/app/work/glossary_resolved.json`: Merged user glossaries, detected `glossary_conflicts[]`, mined `Full Term (ACR)` definitions, and cross-section variant drifts.
- `/app/work/polish_brief.md`: Compact token-efficient brief for authoring.

### Step 3: Save Early Honest Partial Checkpoint
Persist an honest baseline checkpoint immediately so budget exhaustion or corrupt inputs never leave `/app/output/result.json` empty:
```bash
python3 "$SKILL_DIR/scripts/assemble_output.py" \
  --case-plan /app/work/case_plan.json \
  --segments /app/work/source_segments.json \
  --glossary /app/work/glossary_resolved.json \
  --checkpoint partial \
  --out-dir /app/output
```
- **Short-Circuit Exceptions**: If `is_non_polish_qa` is `true`, or all manuscript files are missing/encrypted/corrupt, do NOT fabricate edits; finalize an honest `not_applicable_qa` or `unable_to_determine` `result.json` explaining exact causes in `uncertainty` and `limitations`.

### Step 4: Author Sentence-Level Edits (`/app/work/polished_payload.json`)
Read `/app/work/polish_brief.md`, `/app/work/glossary_resolved.json`, and `$SKILL_DIR/resources/genre_style_guides.json`. Process `/app/work/source_segments.json` in ordered batches (20–30 sentences per batch for long texts) and write `/app/work/polished_payload.json` conforming to `$SKILL_DIR/resources/payload_schema.json`:
- Include **every** `sentence_id` in exact order with verbatim `original` and `polished`.
- Apply **minimal editing**: leave already sound sentences unchanged (`"changed": false`, `"polished" == "original"`); do NOT perform wholesale paragraph rewrites unless Chinglish/translationese explicitly requires structural reordering.
- Keep every protected span (`numbers_units`, `math_latex`, `citations`, `cross_refs`, `chemical_symbols`) intact.
- Preserve epistemic hedge strength and correlation-vs-causation polarity.
- Enforce every non-conflicting canonical term in `glossary_resolved.json`; for any conflicting glossary entries (`glossary_conflicts[]`), keep original text or pick the dominant convention consistently, set `needs_human_confirmation: true`, and add an item to `author_confirmation_items[]`.
- Assign valid `edit_types[]` (from `$SKILL_DIR/resources/payload_schema.json`) and specific, verifiable `rationale` for every changed sentence.
- Populate `contract_overrides` if `case_plan.json["output_contract"]` defines custom keys beyond standard semantic roles.

### Step 5: 8-Axis Mechanical Verification & Self-Correction Loop
```bash
python3 "$SKILL_DIR/scripts/verify_polish.py" \
  --payload /app/work/polished_payload.json \
  --segments /app/work/source_segments.json \
  --glossary /app/work/glossary_resolved.json \
  --case-plan /app/work/case_plan.json \
  --out /app/work/verification_report.json
```
Inspect `/app/work/verification_report.json`. Fix every `ERROR` (missing/reordered sentences, altered numbers/units/citations/LaTeX, unauthorized hedge-tier upgrades, correlation-to-causation shifts, negation flips, glossary violations, empty rationales, unbalanced delimiters, undisclosed conflicts) in `/app/work/polished_payload.json` and re-run `verify_polish.py` until `"passed": true` (exit code `0`).

### Step 6: Assemble Verified Deliverables
```bash
python3 "$SKILL_DIR/scripts/assemble_output.py" \
  --case-plan /app/work/case_plan.json \
  --segments /app/work/source_segments.json \
  --glossary /app/work/glossary_resolved.json \
  --payload /app/work/polished_payload.json \
  --verification /app/work/verification_report.json \
  --checkpoint final \
  --out-dir /app/output \
  --strict
```
Verify `/app/output/` contains `result.json` (with `case_id` and every `output_contract` key), `polished_manuscript.md` (plus `.tex`/`.docx` and rendered PDF/PNG previews when applicable), `redline_diff.md`, `change_log.csv`, `change_log.md`, `glossary_table.md`, and `audit_and_confirmation_report.md`.
