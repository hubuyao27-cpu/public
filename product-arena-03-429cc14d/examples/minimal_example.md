# Minimal Calling Example (`academic-citation-manager`)

> **ILLUSTRATIVE EXAMPLE ONLY — NOT A FORMAL BENCHMARK CASE**

## Sample Inputs
- Manuscript: `examples/sample_manuscript.md`
- Libraries: `examples/sample_library.bib`, `examples/sample_library.ris`
- Goal: Deduplicate preprint + published edition, keep distinct same-author-same-year papers separate, synchronize body + `Fig. 1` caption numbering under GB/T 7714—2015.

## Invocation Steps
```bash
SKILL_DIR="/skills/weekly-skill"
python3 "$SKILL_DIR/scripts/inspect_case.py" --input-dir /input --out /app/work/case_plan.json
python3 "$SKILL_DIR/scripts/parse_and_dedup_library.py" --case-plan /app/work/case_plan.json --out-dir /app/work
python3 "$SKILL_DIR/scripts/analyze_manuscript_citations.py" \
  --case-plan /app/work/case_plan.json \
  --library /app/work/canonical_library.json \
  --out-dir /app/work
python3 "$SKILL_DIR/scripts/assemble_output.py" \
  --case-plan /app/work/case_plan.json \
  --snapshot /app/work/library_snapshot.json \
  --dedup /app/work/dedup_report.json \
  --checkpoint partial --out-dir /app/output
# Author /app/work/citation_decisions.json from /app/work/citation_brief.md
python3 "$SKILL_DIR/scripts/sync_and_format.py" \
  --case-plan /app/work/case_plan.json \
  --library /app/work/canonical_library.json \
  --analysis /app/work/manuscript_analysis.json \
  --decisions /app/work/citation_decisions.json \
  --out /app/work/synced_package.json
python3 "$SKILL_DIR/scripts/verify_citations.py" \
  --synced /app/work/synced_package.json \
  --snapshot /app/work/library_snapshot.json \
  --decisions /app/work/citation_decisions.json \
  --case-plan /app/work/case_plan.json \
  --out /app/work/verification_report.json
python3 "$SKILL_DIR/scripts/assemble_output.py" \
  --case-plan /app/work/case_plan.json \
  --snapshot /app/work/library_snapshot.json \
  --dedup /app/work/dedup_report.json \
  --synced /app/work/synced_package.json \
  --decisions /app/work/citation_decisions.json \
  --verification /app/work/verification_report.json \
  --checkpoint final --out-dir /app/output --strict
```

## Illustrative Output Snippet (Schematic Only)
- **Dedup**: `smith2021_ni` + `smith2021_arxiv` merged via `tier_1_exact_doi` keeping `Nature Catalysis` metadata primary; `Electrochemical Impedance Modeling of Porous Electrodes` (also Smith, 2021) preserved distinct by the anti-false-merge guard.
- **Renumbered Text**: Body `[2]` -> `[1]`; `Fig. 1` `[2, 1]` -> `[1,2]`; synchronized GB/T 7714—2015 reference list `[1]` and `[2]` matches body order 1-to-1.
