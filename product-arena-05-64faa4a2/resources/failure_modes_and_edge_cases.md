# Failure Modes, Edge Cases & Exception Handling Playbook

This playbook defines deterministic recovery rules whenever input materials are incomplete, conflicting, corrupted, ambiguous, or outside standard ML layouts.

## 1. Source Precedence & Conflict Resolution
When the same parameter or experimental condition appears with conflicting values across multiple files, follow this strict precedence order — and **always** disclose the conflict in `conflicts[]`, `uncertainty[]`, and `change_records[]`:

1. **`runtime_log` (Priority 100)**: Actual effective arguments/environment printed during execution (`train.log`, `eval.log`, instrument readout).
2. **`cli_script` (Priority 90)**: Execution shell script CLI flags (`run.sh`, `Makefile`, Slurm script) overriding config/code defaults.
3. **`exp_config` (Priority 80)**: Dedicated experiment configuration (`config.yaml`, `hparams.json`, `settings.toml`, `.ini`).
4. **`data_table` (Priority 75)**: Structured sample/metric CSV/TSV sheets.
5. **`paper_source` (Priority 70)**: Published or authoritative reference paper PDF/TeX.
6. **`lab_note` (Priority 65)**: Structured experimental notebook/protocol Markdown.
7. **`code_default` (Priority 60)**: `argparse` default or constant in `.py`/`.r` source code.
8. **`narrative_draft` (Priority 30)**: User's rough draft being revised/audited.

- **Same-tier conflict** (e.g., two logs report different batch sizes, or two configs disagree without a clarifying runtime log): Do **not** silently pick one. Mark `verification_status: "conflict_unresolved"`, mention both values with their exact file+line citations in the manuscript, and emit a `critical` author-confirmation item (`[待作者确认: GAP-XX]`).
- **Higher-tier overrides lower-tier** (e.g., `run.sh` overrides `config.yaml`, and `train.log` confirms the override): Adopt the authoritative runtime value, mark `verification_status: "conflict_resolved_with_note"`, log the exact reconciliation in `change_records[]` and `conflicts[]`, and ask the author to confirm no unreported runs used the older default.

## 2. Unsourced Claims in an Existing Draft
When revising an existing draft that states a concrete value (e.g., `"80/10/10 split"`, `"5 random seeds"`, `"300 epochs"`) with **zero** corroboration in configs, code, logs, or reference papers:
- Do **not** present the claim as verified fact, and do **not** silently delete it without record.
- Mark it `source_type: "narrative_draft"`, `verification_status: "draft_only_unverified"`, append an inline `[待作者确认: GAP-XX]` tag, and ask the author to supply the authoritative script/log or confirm whether the value is obsolete.

## 3. Missing Materials, Unreadable Files & Permission Errors
| Failure Condition | Detection Signal | Required Honest Handling |
|---|---|---|
| Referenced file absent from `/input/` | `missing_referenced_files` in `case_plan.json` | Log in `uncertainty` (`reason: "missing_file"`) and `author_confirmation_items`; set `status: "partial"` (or `"unanswerable"` if zero usable evidence). |
| Permission denied / unreadable binary / corrupt ZIP/DOCX/YAML | `ingestion_warnings` in `fact_ledger.json` | Never crash. Record exact path & OS error in `uncertainty` and `manual_takeover_points`; continue extracting all readable files. |
| Encrypted PDF or scanned PDF without text layer | `pdf_encrypted` / `pdf_scanned_low_text` in `fact_ledger.json` | Attempt `pdftotext` or `tesseract` if installed; if still unreadable, record exact page numbers in `uncertainty` and never guess obscured equations or tables. |
| Dataset version or split seed missing | `preliminary_gaps` in `fact_ledger.json` | Never invent `"v1.0"` or `"seed=42"` or `"80/10/10"`. Explicitly write `"未在输入材料中说明 [待作者确认: GAP-XX]"` and add a `critical` gap question. |
| Baseline comparison lacks budget/compute parity info | No compute/epoch/tuning evidence for baselines | Never claim "fair comparison" or "under identical settings". Set `budget_parity_status: "unknown_author_confirm"` and flag the comparability risk honestly. |
| Non-ML discipline (Wet-Lab Chem/Bio/Materials or Physics Simulation) | Domain classifier in `inspect_case.py` | Switch rubric to `wetlab_chem_bio_materials` or `simulation_engineering`. Never inject ML jargon (`epochs`, `batch_size`, `GPU`) into wet-lab SOPs. |
| Wrong or misleading domain label in metadata | Content contradicts label | Follow Harbor rule: **actual input files and original papers are the sole factual ground truth**. |

## 4. Boilerplate Limitation Prevention
Never output generic clichés such as `"future work will explore larger datasets"` or `"受限于算力，未来将进一步研究"`. Every limitation entry must specify:
1. **Concrete failure scenario** (what degrades or breaks),
2. **Trigger condition / regime** (exact parameter range, data distribution shift, sample size, reaction temperature/atmosphere, or numerical stiffness),
3. **Affected scope** (which claims or conclusions cease to hold), and
4. **Evidence or attribution** (`author_stated` with source locator, or `auditor_inferred_from_setup` tied to concrete observed boundaries in the materials).
