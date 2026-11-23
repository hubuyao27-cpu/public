# Minimal Usage Example (Illustrative Demo Only)

> **Important**: The input, commands, and sample outputs shown below use the synthetic demonstration file `examples/sample_paper.txt`. They illustrate the exact interface and verification workflow and are **NOT** official benchmark case results.

---

## 1. Input Setup

Suppose `/input/` contains:
- `sample_paper.txt` (or a multi-page scientific PDF)
- `case.json` with `case_id`, questions, and `output_contract`

## 2. Step-by-Step Execution

```bash
# Step 1: Inspect boundaries, questions, and output_contract
python3 scripts/inspect_case.py \
  --input-dir examples \
  --files examples/sample_paper.txt \
  --out /tmp/work/case_plan.json

# Step 2: Layout-aware extraction, section tree, and anchor indexing
python3 scripts/parse_paper.py \
  --input examples/sample_paper.txt \
  --out-dir /tmp/work/parsed

# Step 3 (Optional early checkpoint before long reading):
python3 scripts/assemble_notes.py \
  --case-plan /tmp/work/case_plan.json \
  --extraction /tmp/work/parsed/extraction_report.json \
  --outline /tmp/work/parsed/section_outline.json \
  --checkpoint partial \
  --out-dir /tmp/output

# Step 4: Mechanical audit of notes_payload.json
python3 scripts/verify_notes.py \
  --source /tmp/work/parsed/source_index.json \
  --outline /tmp/work/parsed/section_outline.json \
  --notes examples/sample_notes_payload.json \
  --case-plan /tmp/work/case_plan.json \
  --extraction /tmp/work/parsed/extraction_report.json \
  --out /tmp/work/verification_report.json

# Step 5: Assemble final result.json, structured_notes.md, knowledge_items.jsonl, paper_card_row.csv
python3 scripts/assemble_notes.py \
  --case-plan /tmp/work/case_plan.json \
  --extraction /tmp/work/parsed/extraction_report.json \
  --outline /tmp/work/parsed/section_outline.json \
  --notes examples/sample_notes_payload.json \
  --verification /tmp/work/verification_report.json \
  --checkpoint final \
  --out-dir /tmp/output
```

## 3. Expected Verification Summary (Illustrative Output)

```text
Verification PASSED: anchors=100.0%, quotes=100.0%, numbers=100.0%, sections=100.0%, errors=0, warnings=0
Assembled deliverables in /tmp/output (checkpoint=final, status=success).
```

## 4. Sample Output Snippet (`structured_notes.md`, Illustrative)

```markdown
### [Q1] Compared with Ru/Al2O3, how much does CeO2 promotion change activation energy and under what operating conditions?
- **回答 (`answered` | `machine_verifiable`)**: 加入 15 wt% CeO2 将表观活化能从 84.2 kJ/mol 降低至 61.5 kJ/mol；该对比基于 260 °C、0.1 MPa、200 mg 催化剂、H2/CO2/N2 = 4:1:1 及 GHSV = 12,000 h^-1 的固定床实验条件。
- **原文依据**: `[P002 | S01]` "Apparent activation energy decreased from 84.2 kJ/mol on Ru/Al2O3 to 61.5 kJ/mol on Ru/CeO2-Al2O3"; `[P004 | S03]` "using 200 mg catalyst (40–60 mesh) and a feed gas ratio of H2/CO2/N2 = 4:1:1 at 0.1 MPa"
- **成立边界/前提**: 需满足无硫进料及 GHSV <= 12,000 h^-1；若空速升至 30,000 h^-1，转化率降至 52.0%。
```
