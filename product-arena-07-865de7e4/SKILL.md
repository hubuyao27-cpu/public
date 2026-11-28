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

## Core Guarantees
1. **编译得通 (Root-Cause Convergence & Honest Fallback)**: Pinpoint exact `file:line` root causes instead of cascading EOF noise; run deterministic multi-pass compilation (`engine -> bibtex/biber -> engine*`, up to 5 passes) until `.aux`/`.toc`/`.out` hashes stabilize (`0` errors, `0` undefined refs/cites, `0` missing glyphs, `0` literal `??`/`[?]`). Non-destructive fallback diagnostics test `xelatex`/verified CJK fonts without faking `compiled_clean`.
2. **改得对 (Fidelity & Template Compliance)**: Preserve every source number, unit, symbol, and citation; enforce `amsmath`, `booktabs` tables (never `\resizebox`/`\scalebox`), `\label` after `\caption`, and mandatory template metadata (`resources/template_profiles.json`).
3. **交得可用 (Clean Editable Sources & Verifiable Diffs)**: Deliver modular `.tex`/`.bib` sources alongside compiled `.pdf`, `previews/page-*.png`, `compile_diagnostics`, and `before_after_diff` inside `/app/output/result.json`.
4. **复现得了 (Reproducible Build Provenance)**: Probe live compilers & `fc-list` CJK fonts before selecting fonts/engines; emit executable `build.sh` and `reproducibility_manifest.json` (with PDF SHA-256 and embedded fonts).

---

## Runtime Paths & Skill Resolution
```bash
SKILL_DIR="${SKILL_DIR:-}"
for d in "$SKILL_DIR" "/skills/weekly-skill" "/app/submission" "."; do
  [ -n "$d" ] && [ -f "$d/scripts/prepare_and_audit.py" ] && SKILL_DIR="$d" && break
done
mkdir -p /app/work/src /app/work/build /app/work/previews /app/output
```
- **Workspace rule**: Never compile inside read-only `/input`. Edit only inside `/app/work/src/`, and **never** leave backup copies like `main_old.tex` inside `/app/work/src/` (all `.tex` under `/app/work/src/` are scanned and delivered).

---

## Streamlined 3-Step Execution Workflow

### Step 1: Prepare, Stage, Safe-Autofix & Early Partial Checkpoint
```bash
python3 -B "$SKILL_DIR/scripts/prepare_and_audit.py" \
  --input-dir /input \
  --work-dir /app/work \
  --out-dir /app/output \
  --apply-safe-fixes
```
Read `/app/work/case_plan.json` and `/app/work/latex_digest.md` (plus `/input/task.md`). This single command inspects `/input` (and inline `case.json` fields), probes TeX/CJK fonts, stages sources into `/app/work/src`, applies safe mechanical pre-fixes (BOM removal, `\label`-after-`\caption`, OS-missing CJK font replacement, BibTeX `&`/`%` escaping), detects tabular/math/citation/class bugs, extracts grounded source numbers + `booktabs` CSV snippets, and saves an early partial `/app/output/result.json`.
- **Exception Short-Circuit**: If `stats.readable_files == 0` or a mandatory publisher `.cls` is genuinely missing, consult `$SKILL_DIR/resources/failure_modes_and_edge_cases.md`, write `/app/work/deliverable_payload.json` with honest `"status": "partial"` (or `"unable_to_determine"`) + `uncertainty[]`, and jump to Step 3 with `--skip-compile`.

### Step 2: Author/Repair `.tex`/`.bib` in `/app/work/src/` & Compile Loop
1. Select `<MAIN_TEX>` (`candidate_entry_tex[0]` or `main.tex` for `draft_to_latex`) and edit `.tex`/`.bib` following `$SKILL_DIR/resources/latex_quality_checklist.md` and `$SKILL_DIR/resources/template_profiles.json`.
   - Annotate non-trivial data inline with `% [SRC: file:line]` and missing/conflicting items with `% [AUTHOR_CONFIRM: GAP-XX description]`.
   - For CJK documents: use `xelatex`; use `fontset=fandol` ONLY if `case_plan.json["environment"]["fandol_available"]` is `true`, otherwise `fontset=none` + `\setCJKmainfont{<verified font from environment.cjk_fonts>}`.
   - Fix wide tables structurally (`tabularx`, `p{width}`, `\small`, `\tabcolsep`) — never wrap `tabular` in `\resizebox`/`\scalebox`.
2. Compile and diagnose:
```bash
python3 -B "$SKILL_DIR/scripts/compile_and_diagnose.py" \
  --tex /app/work/src/<MAIN_TEX> \
  --project-root /app/work/src \
  --work-dir /app/work/build \
  --preview-dir /app/work/previews \
  --out-report /app/work/compile_report.json \
  --out-summary /app/work/compile_summary.md
```
3. Read `/app/work/compile_summary.md`. Fix **root causes first** (via `$SKILL_DIR/resources/error_taxonomy.json`) and re-run until `status == "compiled_clean"` (0 errors, 0 undefined refs/cites, 0 missing glyphs, `Overfull \hbox < 10pt`) or a real blocker is confirmed. Inspect `previews/page-*.png` visually when image viewing is available.

### Step 3: Verify 6 Axes & Assemble Final `/app/output/`
Optionally author `/app/work/deliverable_payload.json` (per `$SKILL_DIR/resources/payload_schema.json`) to add `contract_overrides`, detailed `summary`, or custom `change_records[]` — `verify_and_assemble.py` automatically hydrates `sources[]` (with verbatim `source_quote`s & Field-Level Source Evidence table from `% [SRC:]` and `source_facts`), `author_confirmation_items[]` (from `% [AUTHOR_CONFIRM: GAP-XX]`), `auto_change_records.json`, and `before_after_diff`:
```bash
python3 -B "$SKILL_DIR/scripts/verify_and_assemble.py" \
  --tex /app/work/src/<MAIN_TEX> \
  --project-root /app/work/src \
  --payload /app/work/deliverable_payload.json \
  --work-dir /app/work \
  --out-dir /app/output
```
*(Pass `--skip-compile` for honest blocked/uncompilable cases; redundant recompiles are skipped automatically if `/app/work/src` is unchanged since Step 2.)*
If exit code is non-zero, read `/app/work/verification_report.json`, fix the flagged `.tex`/`.bib`/payload items, and re-run until `"passed": true`.

---

## Strict Honesty Rules
- **Never** invent BibTeX entries, DOIs, numeric measurements, or missing `.cls`/`.png` binaries.
- **Never** mask wide tables with `\resizebox`/`\scalebox` or report `"completed"`/`"compiled_clean"` when errors, missing glyphs, or fallback-only previews remain.
