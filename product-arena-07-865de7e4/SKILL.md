---
name: latex-sci-composer
description: >
  End-to-end academic LaTeX authoring, template migration, static diagnosis, multi-pass
  compilation with engine/CJK/package fallback diagnostics, and reproducibility verification skill.
  Use when drafting LaTeX from Markdown/CSV/DOCX/PDF sources, migrating manuscripts to
  IEEEtran/acmart/elsarticle/llncs/ctex templates, debugging cascading TeX/BibTeX/Biber errors,
  fixing formulas, booktabs tables, cross-references, or CJK fonts, and producing editable
  .tex/.bib, PDF + PNG previews, build.sh, before/after diffs, and diagnostics-rich result.json
  under Harbor (/input/case.json, /app/output).
---

# Academic LaTeX Composer & Build Verifier (`latex-sci-composer`)

Transform messy drafts, broken TeX projects, or multi-format research inputs into **compilable, template-compliant, editable, source-traceable, and reproducibly built LaTeX deliverables**.

## Core Design Guarantees
1. **编译得通 (Root-Cause Convergence & Honest Fallback)**: Pinpoint exact `file:line` root causes instead of getting fooled by cascading EOF/undefined-CS noise; execute deterministic multi-pass compilation (`engine -> bibtex/biber -> engine*`, up to 5 passes) until `.aux`/`.toc`/`.out` hashes stabilize and PDF previews audit cleanly (`0` errors, `0` undefined refs/cites, `0` missing glyphs, `0` literal `??`/`[?]`). When `pdflatex` hits UTF-8/CJK errors, missing Linux CJK fonts, or missing optional `.sty` files, `compile_and_diagnose.py` runs non-destructive auto-fallback diagnostics to surface verified installed fonts and fallback PDF/PNG previews without faking `compiled_clean`.
2. **改得对 (Fidelity & Template Compliance)**: Preserve every source number, unit, equation symbol, and citation with `100%` traceability; enforce `amsmath`, upright units, `booktabs` tables (never hide overflow with `\resizebox`), `\label` after `\caption`, and mandatory journal/conference metadata (`template_profiles.json`).
3. **交得可用 (Clean Editable Sources & Verifiable Diffs)**: Deliver structured, modular `.tex`/`.bib` files alongside compiled `.pdf`, fresh `previews/page-*.png`, itemized `compile_diagnostics` (warnings, `undefined_references`, `undefined_citations`, `overfull_hboxes`, missing glyphs), and automatic `before_after_diff` inside `/app/output/result.json` and `compilation_and_provenance_report.md`.
4. **复现得了 (Reproducible Build Provenance)**: Probe live compilers & system CJK fonts (`fc-list`) before choosing XeLaTeX/Fandol/Noto fonts, and emit executable `build.sh` plus `reproducibility_manifest.json`.

---

## Runtime Paths & Budget Discipline (64 Requests / 900s)
Resolve `$SKILL_DIR` cleanly before calling scripts (works under Harbor `/skills/weekly-skill` and local checkouts):
```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
mkdir -p /app/work/src /app/work/build /app/work/previews /app/output
```
- **Inputs**: `/input/case.json`, `/input/task.md` (or `./task.md`), and real files under `/input/` (plus any inline `case.json` `.tex`/`.bib` fields). Never assume facts from labels or filenames.
- **Workspace**: `/app/work/src/` holds editable sources (never compile inside read-only `/input`).
- **Outputs**: `/app/output/` (`result.json` with `case_id`, every `output_contract` field, `compile_diagnostics`, and `before_after_diff`; editable `.tex`/`.bib`; `build.sh`; `reproducibility_manifest.json`; compiled `.pdf`; `previews/page-*.png`; `compilation_and_provenance_report.md`).

---

## Streamlined 4-Step Execution Workflow

### Step 1: One-Command Preparation, Source Staging, Static Audit & Partial Checkpoint
Run `prepare_workspace.py` (executes `inspect_case.py` -> stages `/input` & inline files into `/app/work/src/` -> `analyze_latex.py` -> `assemble_deliverables.py --checkpoint partial` in a single request):
```bash
python3 -B "$SKILL_DIR/scripts/prepare_workspace.py" \
  --input-dir /input \
  --work-dir /app/work \
  --out-dir /app/output
```
Read `/app/work/case_plan.json` and `/app/work/latex_digest.md` (plus `/input/task.md`) to confirm `case_id`, all `output_contract` fields, `signals.primary_mode`, `candidate_entry_tex`, `missing_referenced_files`, `access_warnings`, **available CJK fonts** (`environment.cjk_fonts`), static root-cause bugs (unclosed `{`, `\begin/\end` mismatches, package conflicts, `\label` before `\caption`, missing `.bib` keys/figures, `\resizebox` hacks), and grounded non-TeX source facts.
- **Exception Short-Circuit**: If `/input/` has `0` readable files or a missing mandatory publisher `.cls` / unrecoverable access error, consult `$SKILL_DIR/resources/failure_modes_and_edge_cases.md` and emit an honest `"partial"` or `"unable_to_determine"` deliverable rather than fabricating missing classes or data.

### Step 2: Author/Repair Editable Sources & Multi-Pass Compile Loop
1. Edit or author clean `.tex`/`.bib` files inside `/app/work/src/` following `$SKILL_DIR/resources/latex_quality_checklist.md` and `$SKILL_DIR/resources/template_profiles.json`.
   - Annotate non-trivial data/edits concisely via `% [SRC: file:line]` and missing/conflicting items via `% [AUTHOR_CONFIRM: GAP-XX]`.
   - For CJK documents, use `xelatex` and ONLY select fonts verified in `case_plan.json["environment"]["cjk_fonts"]` (or `\documentclass[UTF8,fontset=fandol]{ctexart}` / `Noto Serif CJK SC`).
2. Run deterministic multi-pass compilation, log diagnosis, auto-fallback check, PDF audit, and clean PNG preview rendering:
```bash
python3 -B "$SKILL_DIR/scripts/compile_and_diagnose.py" \
  --tex /app/work/src/<MAIN_TEX> \
  --work-dir /app/work/build \
  --preview-dir /app/work/previews \
  --out-report /app/work/compile_report.json \
  --out-summary /app/work/compile_summary.md
```
3. Read `/app/work/compile_summary.md`. Fix **root causes first** in `/app/work/src/` (consulting `$SKILL_DIR/resources/error_taxonomy.json` and any `Fallback & Recovery Notes` with verified installed CJK fonts) and re-run `compile_and_diagnose.py` until `status == "compiled_clean"`, `0` errors, `0` undefined references/citations, `0` missing glyphs, `0` `??`/`[?]` markers, and acceptable `Overfull \hbox` widths (or an honest unresolvable blocker is confirmed).
4. Visually inspect rendered `/app/work/previews/page-*.png` if image viewing is available to confirm zero clipped tables or blank pages.

### Step 3: Author Grounded `/app/work/deliverable_payload.json`
Write `/app/work/deliverable_payload.json` conforming to `$SKILL_DIR/resources/payload_schema.json`, honestly recording:
- `case_id`, `status` (`completed` | `partial` | `unable_to_determine`), `summary`, `primary_tex_path`, `template_compliance`;
- `sources[]` mapping delivered numbers/equations/tables/citations back to exact `/input` locators;
- `change_records[]` with before/after descriptions and engineering rationale for every substantive fix;
- `author_confirmation_items[]` 1-to-1 with every `% [AUTHOR_CONFIRM: GAP-XX]` tag in `.tex`;
- `uncertainty[]`, concrete `limitations[]`, and `contract_overrides` for any custom `output_contract` keys.

### Step 4: One-Command Compile, 6-Axis Verification & Final Deliverable Assembly
```bash
python3 -B "$SKILL_DIR/scripts/finalize_workspace.py" \
  --tex /app/work/src/<MAIN_TEX> \
  --payload /app/work/deliverable_payload.json \
  --work-dir /app/work \
  --out-dir /app/output
```
This runs `compile_and_diagnose.py` -> `verify_deliverable.py` -> `assemble_deliverables.py --checkpoint final`, guaranteeing `/app/output/` contains `result.json` (with `case_id`, all `output_contract` keys, `compile_diagnostics`, and `before_after_diff`), editable `.tex`/`.bib`, `build.sh`, `reproducibility_manifest.json`, compiled `.pdf`, `previews/page-*.png`, and `compilation_and_provenance_report.md`.
- If exit code is non-zero (`error_count > 0`), inspect `/app/work/verification_report.json`, fix reported `.tex`/`.bib`/payload defects, and re-run `finalize_workspace.py` until `"passed": true`. *(All individual stage scripts remain directly callable if needed.)*

---

## Strict Honesty Rules
- **Never** invent BibTeX metadata, DOIs, numeric measurements, or missing `.cls`/`.png` binaries.
- **Never** mask wide-table overflow with `\resizebox` or suppress fatal errors/fallback previews into fake `"completed"` / `"compiled_clean"` status.
- Always map every missing/conflicting fact to `author_confirmation_items[]` and `uncertainty[]`.
