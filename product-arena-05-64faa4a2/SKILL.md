---
name: sci-methods-writer
description: >
  Evidence-grounded academic Methods & Experiments chapter authoring, revision, and audit skill.
  Use when drafting, revising, proofreading, aligning, or structuring scientific Methods,
  Experimental Setup, Parameter Tables, Datasets & Splits, Baseline Comparisons, Reproducibility
  Checklists, or Limitations from heterogeneous materials (configs, code, shell scripts, logs,
  CSV/TSV sheets, lab notes, rough drafts, or PDFs) with line-level source traceability,
  cross-artifact conflict detection, zero fabricated numbers, and explicit author-confirmation
  questionnaires under Harbor (/input/case.json, /app/output).
---

# Scientific Methods & Experiments Writer (`sci-methods-writer`)

Transform scattered experimental artifacts (configs, code, CLI scripts, logs, tables, lab notes, rough drafts, and papers) into **complete, accurate, reproducible, and source-traceable Methods & Experiments deliverables** with mechanical zero-hallucination verification.

## Core Design Guarantees
1. **写得全 (Complete 6 Pillars)**: Cover ordered **Method Steps** (`input -> operation -> output -> decision/stop`), stage-separated **Experimental Setup** (Train/Val/Test or Synthesis/Characterization/Testing), **Parameter Provenance Table**, **Datasets/Materials & Splits**, **Baselines/Controls & Budget Parity**, and **Reproducibility Checklist + Failure-Mode Limitations**.
2. **写得准 (Zero Hallucination & Precedence Alignment)**: Never invent numbers for prose smoothness. Follow explicit source precedence (`runtime_log > cli_script > exp_config > data_table > paper_source > lab_note > code_default > narrative_draft`), disclose every cross-file conflict, and mechanically verify 100% prose-to-table-to-artifact alignment.
3. **做得着 (Executable Reproducibility)**: Deliver concrete commands, code/environment/instrument versions, random seeds, repeat counts, and data/material access paths.
4. **说得住 (Honest Gaps & Concrete Boundaries)**: Mark missing, conflicting, or draft-only unverified items inline with `[待作者确认: GAP-XX]` (or `[AUTHOR_CONFIRM: GAP-XX]`), compile closed-ended author questions, and ground every limitation in a concrete failure scenario, trigger condition, and validity scope (no generic boilerplate).

---

## Runtime Paths & Skill Location
Resolve `$SKILL_DIR` automatically before running commands:
```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
mkdir -p /app/work /app/output
```
- **Inputs**: `/input/case.json`, `/input/task.md` (or `./task.md`), and real source files under `/input/`. Read actual files — never assume facts from filenames or domain labels.
- **Workspace**: `/app/work` (intermediate ledger, digest, payload, audit report).
- **Outputs**: `/app/output/` (must include `result.json` matching `case_id` and every `output_contract` field, plus editable Markdown/CSV/JSON deliverables and rendered previews when DOCX/LaTeX/diagrams are requested).

---

## Mandatory 6-Step Execution Workflow

### Step 1: Inspect Case Contract, Task Constraints & Materials
```bash
python3 "$SKILL_DIR/scripts/inspect_case.py" \
  --input-dir /input \
  --out /app/work/case_plan.json
```
Read `/app/work/case_plan.json` (and `/input/task.md`) to confirm:
- `case_id` and EVERY required key/type in `output_contract`;
- `signals.task_mode` (`draft_from_materials` | `revise_and_align_draft` | `proofread_and_audit` | `structured_extraction` | `bilingual_writing`), `language` (`zh` | `en` | `bilingual`), `requested_formats`, and content-grounded `detected_domain` (`ml_computational` | `wetlab_chem_bio_materials` | `simulation_engineering` | `general_scientific`);
- `missing_referenced_files` and `access_warnings`.

### Step 2: Deterministic Multi-Artifact Extraction, Conflict & Gap Pre-Audit
```bash
python3 "$SKILL_DIR/scripts/extract_facts.py" \
  --case-plan /app/work/case_plan.json \
  --out-ledger /app/work/fact_ledger.json \
  --out-digest /app/work/fact_digest.md
```
This parses all configs, Python AST, shell scripts, logs, CSV/TSV tables, DOCX/MD/TeX/PDFs, detects cross-source parameter conflicts (`CONF-XX`), flags uncorroborated draft-only claims, and pre-audits domain-appropriate 6-pillar gaps (`GAP-XX`).

### Step 3: Save Early Honest Partial Checkpoint
Immediately persist an honest partial deliverable so tight budgets or unexpected interruptions never leave `/app/output/result.json` empty:
```bash
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json \
  --ledger /app/work/fact_ledger.json \
  --checkpoint partial \
  --out-dir /app/output
```
- **Exception Short-Circuit**: If `stats.total_files == 0` or all input files failed to read (`permission_denied` / `pdf_encrypted` / `binary_or_corrupt_file`), do **not** fabricate content. Proceed directly to author an honest `unanswerable`/`partial` payload citing exact errors in `uncertainty` and `author_confirmation_items`.

### Step 4: Read `fact_digest.md` & Author `/app/work/chapter_payload.json`
Read `/app/work/fact_digest.md` (and inspect specific raw files only when extra verbatim detail is needed). Follow the schema in `$SKILL_DIR/resources/payload_schema.json` and templates in `$SKILL_DIR/resources/templates/` to write `/app/work/chapter_payload.json`:

1. **`manuscript_markdown`**: Publication-ready Methods & Experiments text matching the audience, venue, language, and domain evidenced in `/input/`.
   - Separate stages cleanly (Train / Val / Test or Synthesis / Characterization / Testing).
   - Never collapse multi-step operations into one sentence; state Input -> Operation -> Output -> Stop/Decision criterion.
   - Cite exact locators (e.g., `` `config.yaml:L12` ``, `` `train.log:L8` ``, `` `paper.pdf:p4_b02` ``) or structured footnote markers as appropriate for the requested format.
   - Insert explicit inline tags `[待作者确认: GAP-XX]` (Chinese) or `[AUTHOR_CONFIRM: GAP-XX]` (English) at every missing, conflicting, or uncorroborated point.
2. **Structured 6-Pillar Fields**: Populate `method_steps`, `experimental_setup`, `parameter_table`, `datasets_and_splits`, `baseline_comparisons` (always stating `budget_parity_status`), `reproducibility_checklist`, and `limitations` (every limitation must specify concrete `failure_scenario`, `trigger_condition`, `affected_scope`, and `evidence_or_attribution` — zero boilerplate clichés).
3. **Conflicts, Gaps, Sources & Changes**:
   - Disclose EVERY `CONF-XX` from `fact_ledger.json` in `conflicts[]` and `change_records[]` / `uncertainty[]`. For same-tier conflicts, never silently pick a winner — present both values and add a `critical` author question.
   - Never mark a draft-only uncorroborated claim as `"verified"`; use `verification_status: "draft_only_unverified"` and add an `author_confirmation_items` entry.
   - Map EVERY inline `GAP-XX` 1-to-1 to a closed-ended question in `author_confirmation_items[]` and mirror major uncertainties into `uncertainty[]`.
4. **`contract_overrides`**: If `case_plan.json["output_contract"]` defines custom field names beyond standard semantic roles, supply exact values keyed by those field names here.

### Step 5: Mechanical Verification & Self-Correction Loop
```bash
python3 "$SKILL_DIR/scripts/verify_chapter.py" \
  --payload /app/work/chapter_payload.json \
  --ledger /app/work/fact_ledger.json \
  --case-plan /app/work/case_plan.json \
  --out /app/work/verification_report.json
```
`verify_chapter.py` mechanically enforces:
- Zero ungrounded numbers in `manuscript_markdown` and `parameter_table` (with SI unit & scientific-notation equivalence);
- 100% Prose <-> Parameter Table numeric alignment;
- Valid file & line-bound `source_locator` references;
- 100% disclosure of detected cross-source conflicts & draft-only claims;
- Baseline budget-parity disclosure, anti-boilerplate limitation checks, and 1-to-1 `GAP-XX` alignment.

If exit code is non-zero (`error_count > 0`), inspect `/app/work/verification_report.json`, fix the exact reported fields in `/app/work/chapter_payload.json`, and re-run `verify_chapter.py` until `"passed": true`.

### Step 6: Assemble Final Verified Deliverables
```bash
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json \
  --ledger /app/work/fact_ledger.json \
  --payload /app/work/chapter_payload.json \
  --verification /app/work/verification_report.json \
  --checkpoint final \
  --out-dir /app/output
```
Confirm `/app/output/` contains `result.json`, `methods_and_experiments.md`, `parameter_table.md`, `parameter_table.csv`, `reproducibility_and_author_checklist.md`, `provenance_map.json` (plus `.docx`/`.tex`/diagram and rendered PDF/PNG previews when requested).

---

## Strict Honesty & Exception Rules
- Consult `$SKILL_DIR/resources/failure_modes_and_edge_cases.md` whenever encountering missing files, corrupted configs, encrypted/scanned PDFs, unknown dataset versions, unmatched baseline budgets, or non-ML disciplines.
- **Never** invent random seeds, split ratios, dataset release versions, hardware models, or baseline compute parity to make prose sound smoother.
