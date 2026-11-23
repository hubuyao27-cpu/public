# Minimal Invocation Example (`sci-methods-writer`)

> **Notice**: This document is a **synthetic illustrative example** designed solely to demonstrate the input interface, CLI workflow, mechanical verification behavior, and output schema of `sci-methods-writer`. It is **NOT** a formal evaluation case or prepared test answer.

---

## 1. Illustrative Input Bundle (`/input/`)

Suppose `/input/` contains a small synthetic ML experiment folder with an intentional config-vs-log conflict, a rough draft containing an uncorroborated split claim, and a missing dataset version:

- **`/input/case.json`**:
```json
{
  "case_id": "example-ml-001",
  "task": "Revise the rough Methods & Experiments draft against configs, code, and training logs; output aligned prose, a parameter provenance table, conflict disclosures, and author confirmation questions.",
  "output_contract": {
    "required": ["case_id", "status", "manuscript_markdown", "parameter_table", "conflicts", "author_confirmation_items", "sources", "uncertainty", "change_records"]
  }
}
```
- **`/input/configs/train.yaml`**:
```yaml
optimizer: adamw
learning_rate: 1e-3
batch_size: 32
epochs: 30
weight_decay: 0.01
```
- **`/input/run.sh`**:
```bash
#!/usr/bin/env bash
python train.py --config configs/train.yaml --lr 3e-4 --seed 42
```
- **`/input/logs/train.log`** *(excerpt)*:
```text
[ENV] GPU: 1x NVIDIA A100-80GB | Python 3.10.12 | PyTorch 2.1.2+cu121
[CFG] Effective args: lr=0.0003, batch_size=32, epochs=30, weight_decay=0.01, seed=42
[DATA] Dataset: ToyMolBench (train=800, val=100, test=100)
[EVAL] Metric: Macro-F1 across 1 run (seed=42)
```
- **`/input/draft/methods_draft.md`** *(flawed rough draft)*:
```markdown
We trained our model using AdamW with a learning rate of 1e-3 for 50 epochs on ToyMolBench (80/10/10 split) and compared fairly against GraphBaseline.
```

---

## 2. Step-by-Step Invocation

```bash
SKILL_DIR=/skills/weekly-skill
mkdir -p /app/work /app/output

# Step 1 & 2: Inspect and extract anchored Fact Ledger
python3 "$SKILL_DIR/scripts/inspect_case.py" --input-dir /input --out /app/work/case_plan.json
python3 "$SKILL_DIR/scripts/extract_facts.py" \
  --case-plan /app/work/case_plan.json \
  --out-ledger /app/work/fact_ledger.json \
  --out-digest /app/work/fact_digest.md

# Step 3: Save early honest partial checkpoint
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json \
  --ledger /app/work/fact_ledger.json \
  --checkpoint partial --out-dir /app/output
```

**What `extract_facts.py` automatically discovers before writing:**
- **`CONF-01` (`learning_rate`, `higher_tier_override`)**: `logs/train.log:L2` (`0.0003`) & `run.sh:L2` (`3e-4`) override `configs/train.yaml:L2` (`1e-3`) and `draft/methods_draft.md:L1` (`1e-3`).
- **`CONF-02` (`epochs`, `higher_tier_override`)**: `logs/train.log:L2` & `configs/train.yaml:L4` (`30`) contradict `draft/methods_draft.md:L1` (`50`).
- **Preliminary Gaps**:
  - `GAP-01` (`datasets_and_splits`, `critical`): Exact version/snapshot & random split strategy for `ToyMolBench` not reported.
  - `GAP-02` (`baselines_and_controls`, `critical`): Exact version, hyperparameters, and compute-budget parity for `GraphBaseline` not reported; only 1 seed (`42`) logged (`repeat_counts` / multi-seed variance missing).

```bash
# Step 4: Agent writes /app/work/chapter_payload.json following resources/payload_schema.json

# Step 5: Mechanical Verification
python3 "$SKILL_DIR/scripts/verify_chapter.py" \
  --payload /app/work/chapter_payload.json \
  --ledger /app/work/fact_ledger.json \
  --case-plan /app/work/case_plan.json \
  --out /app/work/verification_report.json

# Step 6: Assemble final verified outputs
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json \
  --ledger /app/work/fact_ledger.json \
  --payload /app/work/chapter_payload.json \
  --verification /app/work/verification_report.json \
  --checkpoint final --out-dir /app/output
```

---

## 3. Illustrative Output Excerpt *(EXAMPLE ONLY)*

### Excerpt of `/app/output/methods_and_experiments.md`
```markdown
### Experimental Setup & Parameter Alignment
Training was executed on 1x NVIDIA A100-80GB using Python 3.10.12 and PyTorch 2.1.2+cu121 (`logs/train.log:L1`)
with AdamW (`configs/train.yaml:L1`), effective learning rate `3e-4` (overriding the default `1e-3` in
`configs/train.yaml:L2` via `run.sh:L2` and confirmed as `0.0003` in `logs/train.log:L2`), batch size `32`,
weight decay `0.01`, and `30` epochs (correcting `50` epochs in the initial draft `draft/methods_draft.md:L1`).
Only a single seed (`42`) is recorded in `logs/train.log:L2`; multi-seed repeat runs require confirmation
[AUTHOR_CONFIRM: GAP-02].

### Dataset & Baselines
Experiments use ToyMolBench with 800 training, 100 validation, and 100 test samples (`logs/train.log:L3`);
the exact release version, preprocessing fitting scope, and split randomization protocol are absent from
the provided files [AUTHOR_CONFIRM: GAP-01]. Comparison against GraphBaseline is evaluated via Macro-F1
(`logs/train.log:L4`), but baseline version and compute-budget parity are unverified [AUTHOR_CONFIRM: GAP-03].
```

### Excerpt of `/app/output/result.json` *(Illustrative)*
```json
{
  "case_id": "example-ml-001",
  "status": "completed",
  "parameter_table": [
    {
      "param_id": "P01",
      "name": "Learning Rate",
      "symbol_or_key": "learning_rate",
      "value": 0.0003,
      "unit": "",
      "target_object": "AdamW optimizer",
      "stage": "training",
      "source_locator": "logs/train.log:L2",
      "source_type": "runtime_log",
      "verification_status": "conflict_resolved_with_note",
      "note": "CLI --lr 3e-4 in run.sh:L2 overrides configs/train.yaml:L2 (1e-3) and draft (1e-3)."
    }
  ],
  "conflicts": [
    {
      "conflict_id": "CONF-01",
      "item": "learning_rate",
      "adopted_value_or_status": "0.0003 (3e-4) from logs/train.log:L2",
      "needs_author_confirmation": true
    }
  ],
  "verification_summary": {
    "passed": true,
    "error_count": 0,
    "metrics": {
      "numeric_grounding_rate": 1.0,
      "prose_table_alignment_rate": 1.0,
      "conflict_disclosure_rate": 1.0
    }
  }
}
```
