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
2. **写得准 (Zero Hallucination & Precedence Alignment)**: Never invent numbers for prose smoothness. Follow explicit source precedence (`runtime_log > cli_script > exp_config > data_table > paper_source > lab_note > code_default > narrative_draft`), disclose every cross-file conflict, and mechanically verify prose-to-table-to-artifact alignment.
3. **做得着 (Executable Reproducibility)**: Deliver concrete commands, code/environment/instrument versions, random seeds, repeat counts, and data/material access paths.
4. **说得住 (Honest Gaps & Concrete Boundaries)**: Mark missing, conflicting, or draft-only unverified items inline with `[待作者确认: GAP-XX]` (or `[AUTHOR_CONFIRM: GAP-XX]`), compile closed-ended author questions, and ground every limitation in a concrete failure scenario, trigger condition, and validity scope (no generic boilerplate).

---

## Runtime Paths & Skill Location
```bash
SKILL_DIR="${SKILL_DIR:-}"
for d in "$SKILL_DIR" "/skills/sci-methods-writer" "/skills/weekly-skill" "/app/submission" "/app/skill" "."; do
  [ -n "$d" ] && [ -f "$d/scripts/prepare_workspace.py" ] && SKILL_DIR="$d" && break
done
mkdir -p /app/work /app/output
```
- **Inputs**: `/input/case.json`, `/input/task.md` (or `./task.md`), and real source files under `/input/`. Never assume facts from filenames or domain labels.
- **Workspace**: `/app/work` (`case_plan.json`, `fact_ledger.json`, `fact_digest.md`, `chapter_payload_skeleton.json`, `chapter_payload.json`, `verification_report.json`).
- **Outputs**: `/app/output/` (`result.json` matching `case_id` and every `output_contract` field, `methods_and_experiments.md`, `parameter_table.md/.csv`, `reproducibility_and_author_checklist.md`, `provenance_map.json`, plus `.docx`/`.tex`/diagram and rendered PDF/PNG previews when requested).

---

## Streamlined 3-Step Workflow (Budget-Safe for 64 Requests / 900s)

### Step 1: One-Command Extraction, Skeleton Generation & Partial Checkpoint
Run `prepare_workspace.py` (which executes `inspect_case.py` -> `extract_facts.py` -> `assemble_deliverables.py --checkpoint partial`):
```bash
python3 -B "$SKILL_DIR/scripts/prepare_workspace.py" \
  --input-dir /input \
  --work-dir /app/work \
  --out-dir /app/output
```
This single command:
1. Parses `case_id`, every `output_contract` key, `signals.task_mode`, `language`, `requested_formats`, `detected_domain`, and missing/corrupt files into `/app/work/case_plan.json`.
2. Extracts configs, Python AST, shell scripts, bounded high-signal runtime logs, CSV/TSV tables, DOCX/MD/TeX/PDFs, and inline `case.json`/`task.md` materials into `/app/work/fact_ledger.json` and `/app/work/fact_digest.md`.
3. Emits pre-grounded **`/app/work/chapter_payload_skeleton.json`** with `parameter_table[]` (ranked by source precedence), all `CONF-XX` `conflicts[]`, `GAP-XX` `author_confirmation_items[]`, `sources[]`, `uncertainty[]`, hardware/software setup, and reproduction commands already populated!
4. Saves an honest early `/app/output/result.json` partial checkpoint immediately.

*(Individual stage scripts `inspect_case.py`, `extract_facts.py`, `verify_chapter.py`, and `assemble_deliverables.py` remain independently callable.)*
- **Empty/Unreadable Short-Circuit**: If `total_files == 0` and `inline_material_blocks == 0` (or all files failed reading), do **not** invent content — copy the skeleton to `/app/work/chapter_payload.json` citing exact blockers and run Step 3 with `--allow-partial`.

### Step 2: Review `fact_digest.md` & Complete `/app/work/chapter_payload.json`
Read `/app/work/fact_digest.md` and `/app/work/chapter_payload_skeleton.json` (inspect raw files only when extra verbatim detail is needed; consult `$SKILL_DIR/resources/payload_schema.json` and `$SKILL_DIR/resources/templates/` only if field shapes are unclear).
Edit `/app/work/chapter_payload_skeleton.json` and save to `/app/work/chapter_payload.json`:
1. **`manuscript_markdown`**: Publication-ready Methods & Experiments text matching the requested language, venue, `task_mode`, and domain:
   - Separate stages cleanly (Train / Val / Test or Synthesis / Characterization / Testing).
   - State `Input -> Operation -> Output -> Stop/Decision criterion` for multi-step procedures.
   - Cite exact locators (e.g., `` `configs/train.yaml:L6` ``, `` `logs/train.log:L2` ``) and ensure every quantitative parameter in prose matches `parameter_table[]` (keep the skeleton's authoritative values!).
   - Insert inline tags `[待作者确认: GAP-XX]` (Chinese) or `[AUTHOR_CONFIRM: GAP-XX]` (English) at missing, conflicting, or draft-only unverified points, keeping every inline `GAP-XX` present in `author_confirmation_items[]`.
2. **Complete Remaining 6-Pillar Arrays**: Fill `method_steps[]`, `datasets_and_splits[]`, `baseline_comparisons[]` (always setting `budget_parity_status`), `limitations[]` (concrete `failure_scenario`, `trigger_condition` >= 15 chars, `affected_scope` >= 10 chars — zero generic boilerplate), and `change_records[]` (when revising a draft or resolving conflicts).
3. **Keep Pre-Grounded Conflicts & Uncertainties Intact**: Preserve `conflicts[]` and `uncertainty[]` from `chapter_payload_skeleton.json` (refining descriptions as needed) so `undisclosed_conflict` and `missing_uncertainty_disclosure` never trigger. Populate `contract_overrides` if `case_plan.json` defines custom `output_contract` fields.

### Step 3: One-Command Verification & Final Assembly (Bounded Self-Correction)
```bash
python3 -B "$SKILL_DIR/scripts/finalize_workspace.py" \
  --work-dir /app/work \
  --out-dir /app/output \
  --payload /app/work/chapter_payload.json
```
`finalize_workspace.py` runs `verify_chapter.py` AND updates `/app/output/result.json` + all editable deliverables.
- If exit code is non-zero (`error_count > 0`), inspect `/app/work/verification_report.json`, fix the exact reported fields in `/app/work/chapter_payload.json`, and re-run `finalize_workspace.py` **at most 2 times**.
- **Anti-Timeout Guard**: Never enter an unbounded retry loop. If subtle edge-case errors remain after 2 correction passes, run `python3 -B "$SKILL_DIR/scripts/finalize_workspace.py" --work-dir /app/work --out-dir /app/output --payload /app/work/chapter_payload.json --allow-partial` so final deliverables and `/app/output/result.json` are cleanly finalized within the 900s timeout.

---

## Strict Honesty & Exception Rules
- Consult `$SKILL_DIR/resources/failure_modes_and_edge_cases.md` for missing files, corrupted configs, encrypted/scanned PDFs, unknown dataset versions, unmatched baseline budgets, or non-ML disciplines.
- **Never** invent random seeds, split ratios, dataset versions, hardware models, or baseline compute parity.
