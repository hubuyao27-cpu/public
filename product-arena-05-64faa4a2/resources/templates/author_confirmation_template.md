# Author Confirmation, Conflict Resolution & Reproducibility Checklist Template

## 1. Critical Conflicts Detected Across Materials (跨材料冲突核实)

| Conflict ID | Parameter / Fact | Conflicting Sources & Values | Adopted Policy / Current Draft | Author Action Required |
|---|---|---|---|---|
| CONF-01 | learning_rate | `config.yaml:L14` (`1e-3`) vs `train.log:L8` (`3e-4`) | Adopted `runtime_log` (`3e-4`) per precedence rule | Confirm `3e-4` was used for all reported runs |

## 2. Author Confirmation Questionnaire (待作者确认清单)

### Priority 1 — Critical Reproducibility Blockers (`critical`)
- **[GAP-01]** *(Pillar: `datasets_and_splits` | Draft location: Section 2)*
  - **Issue Type**: `version_ambiguous` / `missing_in_materials` / `unsourced_in_draft`
  - **What Was Checked**: Listed inspected files (`README.md`, `config.yaml`, `train.py`, `train.log`).
  - **Closed-Ended Question for Author**: Please provide the exact release version/snapshot date and random seed used to split Dataset X.

### Priority 2 — Important Details (`important`)
- **[GAP-02]** ...

## 3. Revision & Change Records (修改与溯源记录)

| Change ID | Target Location | Action | Before (Draft / Default) | After (Aligned Manuscript) | Authoritative Source Basis |
|---|---|---|---|---|---|
| CHG-01 | Section 3.1 / `P01` | `reconciled_conflict` | `lr = 1e-3` (draft) | `lr = 3e-4` | `train.log:L8`, `run.sh:L5` |
