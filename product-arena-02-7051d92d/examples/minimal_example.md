# Minimal Calling Example (`academic-language-polisher`)

> **ILLUSTRATIVE EXAMPLE ONLY — NOT A FORMAL BENCHMARK CASE**

## Sample Input
- Manuscript: `examples/sample_manuscript.md`
- Glossary: `examples/sample_glossary.json`
- Task: Polish this journal abstract with minimal editing, keeping exact quantitative data, citation markers, and epistemic hedging intact.

## Invocation Steps
```bash
SKILL_DIR="/skills/weekly-skill"
python3 "$SKILL_DIR/scripts/inspect_case.py" --input-dir /input --out /app/work/case_plan.json
python3 "$SKILL_DIR/scripts/analyze_manuscript.py" --case-plan /app/work/case_plan.json --out-dir /app/work
python3 "$SKILL_DIR/scripts/assemble_output.py" \
  --case-plan /app/work/case_plan.json \
  --segments /app/work/source_segments.json \
  --glossary /app/work/glossary_resolved.json \
  --checkpoint partial --out-dir /app/output
# Author /app/work/polished_payload.json from /app/work/polish_brief.md
python3 "$SKILL_DIR/scripts/verify_polish.py" \
  --payload /app/work/polished_payload.json \
  --segments /app/work/source_segments.json \
  --glossary /app/work/glossary_resolved.json \
  --case-plan /app/work/case_plan.json \
  --out /app/work/verification_report.json
python3 "$SKILL_DIR/scripts/assemble_output.py" \
  --case-plan /app/work/case_plan.json \
  --segments /app/work/source_segments.json \
  --glossary /app/work/glossary_resolved.json \
  --payload /app/work/polished_payload.json \
  --verification /app/work/verification_report.json \
  --checkpoint final --out-dir /app/output --strict
```

## Illustrative Output Snippet (Schematic Only)
- **Original (`P002.S02`)**: `Our experiments may indicate that higher pyridine-N content is associated with improved Faraday efficiency of 96.4 ± 1.2% at -0.8 V vs. RHE, as shown in Fig. 1.`
- **Polished (`P002.S02`)**: `Our experiments suggest that higher pyridine-N content is associated with an improved faradaic efficiency (FE) of 96.4 ± 1.2% at -0.8 V vs. RHE, as shown in Fig. 1.`
- **Edit Types**: `["terminology_consistency", "abbreviation_expansion", "grammar_syntax", "academic_register"]`
- **Rationale**: Standardized `Faraday efficiency` -> `faradaic efficiency (FE)` per glossary, inserted indefinite article `an`, replaced `may indicate` -> `suggest` while keeping hedged tier-2 epistemic strength, correlational verb `is associated with`, exact number `96.4 ± 1.2%`, and `Fig. 1` unchanged.
