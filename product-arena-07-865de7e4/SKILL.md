---
name: latex-sci-composer
description: >
  End-to-end academic LaTeX authoring, template migration, static diagnosis, multi-pass
  compilation, and reproducibility verification skill. Use when drafting LaTeX from
  Markdown/CSV/DOCX/PDF sources, migrating manuscripts to IEEEtran/acmart/elsarticle/llncs/ctex
  templates, debugging cascading TeX/BibTeX/Biber errors, fixing formulas, booktabs tables,
  cross-references, or CJK fonts, and producing editable .tex/.bib, PDF previews, build.sh,
  and source-traceable result.json under Harbor (/input/case.json, /app/output).
---

# Academic LaTeX Composer & Build Verifier (`latex-sci-composer`)

Transform messy drafts, broken TeX projects, or multi-format research inputs into **compilable, template-compliant, editable, source-traceable, and reproducibly built LaTeX deliverables**.

## Core Design Guarantees
1. **编译得通 (Root-Cause Build Convergence)**: Pinpoint exact `file:line` root causes instead of getting fooled by cascading EOF/undefined-CS noise; execute deterministic multi-pass compilation (`engine -> bibtex/biber -> engine*`) until `.aux`/`.toc`/`.out` hashes stabilize and PDF previews audit cleanly (`0` errors, `0` missing glyphs, `0` literal `??`/`[?]`).
2. **改得对 (Fidelity & Template Compliance)**: Preserve every source number, unit, equation symbol, and citation with `100%` traceability; enforce `amsmath`, upright units, `booktabs` tables (never hide overflow with `\resizebox`), `\label` after `\caption`, and mandatory journal/conference metadata (`template_profiles.json`).
3. **交得可用 (Clean Editable Sources)**: Deliver structured, modular `.tex`/`.bib` files alongside compiled `.pdf` and `previews/page-*.png` — never return screenshots, PDF-only blobs, or uncompilable fragments.
4. **复现得了 (Reproducible Build Provenance)**: Probe live compilers & system CJK fonts (`fc-list`) before choosing XeLaTeX/Fandol/Noto fonts, and emit executable `build.sh` plus `reproducibility_manifest.json`.

---

## Runtime Paths & Skill Resolution
Resolve `$SKILL_DIR` cleanly before calling scripts (works under Harbor `/skills/weekly-skill` and local checkouts):
```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
mkdir -p /app/work/src /app/work/build /app/work/previews /app/output
```
- **Inputs**: `/input/case.json`, `/input/task.md` (or `./task.md`), and real files under `/input/`. Never assume facts from labels or filenames.
- **Workspace**: `/app/work/` (always copy editable sources to `/app/work/src/` — never compile in read-only `/input`).
- **Outputs**: `/app/output/` (`result.json` with `case_id` and every `output_contract` field, editable `.tex`/`.bib`, `build.sh`, `reproducibility_manifest.json`, `.pdf`, `previews/*.png`, `compilation_and_provenance_report.md`).

---

## Mandatory 6-Step Execution Workflow

### Step 1: Inspect Case Contract, Materials & Live TeX/Font Environment
```bash
python3 "$SKILL_DIR/scripts/inspect_case.py" \
  --input-dir /input \
  --out /app/work/case_plan.json
```
Read `/app/work/case_plan.json` (and `/input/task.md`) to confirm `case_id`, all `output_contract` fields, `signals.primary_mode`, `candidate_entry_tex`, `missing_referenced_files`, `access_warnings`, and **available CJK fonts** (`environment.cjk_fonts`).

### Step 2: Static Project Graph, Bug Audit & Source Fact Extraction
```bash
python3 "$SKILL_DIR/scripts/analyze_latex.py" \
  --case-plan /app/work/case_plan.json \
  --out-audit /app/work/latex_audit.json \
  --out-digest /app/work/latex_digest.md
```
Read `/app/work/latex_digest.md`. It pinpoints:
- Unclosed `{` and `\begin/\end` mismatches at the *opening* line;
- Package conflicts & load-order violations (`resources/package_conflicts.json`);
- Missing `\ref` labels, `\label` before `\caption`, `.bib` syntax/key mismatches, missing figure assets, `\resizebox` table hacks;
- Non-TeX source numbers, table rows, and headings with exact `(file, line/page)` locators.

### Step 3: Save Early Honest Partial Checkpoint
Protect against tight execution budgets (`64` requests / `900` s) by saving an immediate partial `result.json`:
```bash
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json \
  --audit /app/work/latex_audit.json \
  --checkpoint partial \
  --out-dir /app/output
```
- **Exception Short-Circuit**: If `/input/` has `0` readable files or missing mandatory publisher `.cls` / unrecoverable access errors, consult `$SKILL_DIR/resources/failure_modes_and_edge_cases.md` and emit an honest `"partial"` or `"unable_to_determine"` deliverable rather than fabricating missing classes or data.

### Step 4: Author/Repair Editable Sources & Run Multi-Pass Compilation Loop
1. Copy existing `/input/` sources (if any) into `/app/work/src/` and write or edit clean `.tex`/`.bib` files following `$SKILL_DIR/resources/latex_quality_checklist.md` and `$SKILL_DIR/resources/template_profiles.json`.
   - Annotate non-trivial data/edits concisely via `% [SRC: file:line]` and missing/conflicting items via `% [AUTHOR_CONFIRM: GAP-XX]`.
   - For CJK documents, use `xelatex` and ONLY select fonts verified in `case_plan.json["environment"]["cjk_fonts"]` (or `fontset=fandol` / `Noto Serif CJK SC`).
2. Run deterministic multi-pass compilation, log diagnosis, PDF audit, and PNG rendering:
```bash
python3 "$SKILL_DIR/scripts/compile_and_diagnose.py" \
  --tex /app/work/src/<MAIN_TEX> \
  --work-dir /app/work/build \
  --preview-dir /app/work/previews \
  --out-report /app/work/compile_report.json \
  --out-summary /app/work/compile_summary.md
```
3. Read `/app/work/compile_summary.md`. If errors, missing glyphs, or `??`/`[?]` leaks occur, fix **root causes first** in `/app/work/src/` (consulting `$SKILL_DIR/resources/error_taxonomy.json`), sync edits, and re-run `compile_and_diagnose.py` until `status == "compiled_clean"` (or an honest unresolvable blocker is confirmed).
4. Visually inspect rendered `previews/page-*.png` if image viewing is available to confirm zero clipped tables or blank pages.
5. Write `/app/work/deliverable_payload.json` conforming to `$SKILL_DIR/resources/payload_schema.json` (recording `sources[]`, `change_records[]`, `uncertainty[]`, `author_confirmation_items[]` 1-to-1 with every `GAP-XX`, concrete `limitations[]`, and any custom `contract_overrides`).

### Step 5: Mechanical 6-Axis Verification Gate
```bash
python3 "$SKILL_DIR/scripts/verify_deliverable.py" \
  --payload /app/work/deliverable_payload.json \
  --case-plan /app/work/case_plan.json \
  --audit /app/work/latex_audit.json \
  --compile-report /app/work/compile_report.json \
  --work-tex-dir /app/work/src \
  --out /app/work/verification_report.json
```
If exit code is non-zero, read `/app/work/verification_report.json`, fix reported `.tex`/`.bib`/payload defects, re-compile if `.tex`/`.bib` changed, and re-run `verify_deliverable.py` until `"passed": true`.

### Step 6: Assemble Final Verified Deliverables
```bash
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json \
  --audit /app/work/latex_audit.json \
  --compile-report /app/work/compile_report.json \
  --payload /app/work/deliverable_payload.json \
  --verification /app/work/verification_report.json \
  --work-tex-dir /app/work/src \
  --checkpoint final \
  --out-dir /app/output
```
Confirm `/app/output/` contains `result.json`, clean editable `.tex`/`.bib`, `build.sh`, `reproducibility_manifest.json`, compiled `.pdf`, `previews/*.png`, and `compilation_and_provenance_report.md`.

---

## Strict Honesty Rules
- **Never** invent BibTeX metadata, DOIs, numeric measurements, or missing `.cls`/`.png` binaries.
- **Never** mask wide-table overflow with `\resizebox` or suppress fatal errors into fake success.
- Always map every missing/conflicting fact to `author_confirmation_items[]` and `uncertainty[]`.
