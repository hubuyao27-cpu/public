---
name: sci-intro-rw-writer
description: >
  Evidence-grounded academic Introduction and Related Work authoring, revision, citation audit,
  and alignment skill. Use when asked to write or revise an Introduction ("写引言"), organize
  Related Work by methodological routes ("整理相关工作" / comparative synthesis), derive
  literature-backed research gaps ("推导研究空白"), align contributions with methods, figures,
  tables, and experimental results ("核对贡献"), or fill and audit citations ("补引用" /
  BibTeX/GB-T 7714/IEEE/APA/Nature verification) under Harbor (/input/case.json -> /app/output).
---

# Scientific Introduction & Related Work Writer (`sci-intro-rw-writer`)

Draft, revise, structure, and mechanically audit academic **Introduction & Related Work** deliverables directly from real source files while preserving end-to-end citation, gap, and contribution provenance.

## Four Core Guarantees
1. **立得住 (Grounded Background)**: Open directly with domain lineage, concrete technical bottlenecks, and why-now urgency backed by sources — never hollow clichés (*"近年来受到广泛关注"* / *"attracted widespread attention"*).
2. **分得清 (Taxonomic Related Work)**: Group literature by problem setting, technical route, or evaluation paradigm (`2–4` routes) and compare conclusions, applicability conditions, and limitations across a unified matrix — never chronological paper-by-paper summaries or unanchored citation dumps (`[1-5]`).
3. **说得实 (Evidence-Chained Research Gaps)**: Derive gaps explicitly from shared limitations, uncovered operating regimes, or inter-study conflicts across cited literature; mark uncorroborated synthetic inferences explicitly as `[作者判断]` / `[AUTHOR_JUDGMENT]`.
4. **对得上 (Verified Contributions & Citations)**: Bind every contribution to specific methods, figures, tables, and grounded metrics with explicit scope limits, and ensure 100% citation metadata accuracy against parsed sources.

---

## Runtime Paths
Resolve `$SKILL_DIR` automatically before invoking scripts:
```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
mkdir -p /app/work /app/output
```
- **Inputs**: `/input/case.json`, `/input/task.md` (or `./task.md`), and actual files under `/input/`. Never infer scientific facts from directory labels or filenames alone.
- **Work**: `/app/work` (`case_plan.json`, `source_ledger.json`, `evidence_digest.md`, `section_payload.json`, `verification_report.json`).
- **Outputs**: `/app/output/` (`result.json` with `case_id` + all `output_contract` fields, `introduction_and_related_work.md`, `related_work_matrix.md/.csv`, `evidence_and_citation_audit.md`, `provenance_map.json`, plus `.tex`/`.bib`/`.docx`/taxonomy previews when requested).

---

## Mandatory 6-Step Workflow

### Step 1: Inspect Case Contract, Constraints & File Inventory
```bash
python3 "$SKILL_DIR/scripts/inspect_case.py" \
  --input-dir /input \
  --out /app/work/case_plan.json
```
Read `/app/work/case_plan.json` (and `/input/task.md`) to confirm `case_id`, all `output_contract` required fields, `signals.task_mode`, `language`, `citation_style`, `requested_formats`, and `missing_referenced_files`.

### Step 2: Deterministic Ingestion & Conflict/Gap Pre-Audit
```bash
python3 "$SKILL_DIR/scripts/ingest_materials.py" \
  --case-plan /app/work/case_plan.json \
  --out-ledger /app/work/source_ledger.json \
  --out-digest /app/work/evidence_digest.md
```
Parses `.bib`, `.ris`, `.json`, `.csv`/`.tsv`, `.md`, `.txt`, `.tex`, `.docx`, `.pdf` (with OCR/error detection), and inline `case.json` materials; builds `references_catalog` (`R01..RNN`), extracts own-paper methods/figures/tables/metrics, audits any existing draft, detects cross-source metadata conflicts (`CONF-XX`), and pre-audits gaps (`GAP-XX`).

### Step 3: Save Honest Early Partial Checkpoint
Immediately persist an early checkpoint so budget exhaustion never leaves `/app/output/result.json` missing:
```bash
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json \
  --ledger /app/work/source_ledger.json \
  --checkpoint partial \
  --out-dir /app/output
```
- **Empty/Unreadable Short-Circuit**: If `references_catalog` and `own_paper_evidence` are both empty (or all files failed decryption/parsing), do **not** invent citations or metrics. Consult `$SKILL_DIR/resources/edge_cases_and_fallbacks.md`, write an honest `status: "insufficient_material"` payload documenting exact blockers, verify, and assemble.

### Step 4: Read `evidence_digest.md` & Author `/app/work/section_payload.json`
Read `/app/work/evidence_digest.md` (only inspect raw source files when verbatim confirmation is needed). Author `/app/work/section_payload.json` conforming strictly to `$SKILL_DIR/resources/payload_schema.json` and `$SKILL_DIR/resources/templates/intro_rw_template.md`:
1. **`manuscript_markdown`**: Publication-ready Introduction & Related Work prose matching requested language, audience, and citation format.
   - Open with domain lineage + concrete technical constraint (no cliché fillers).
   - Group Related Work into `2–4` technical/problem routes (`related_work_groups[]` + `comparison_matrix[]`), stating each work's conclusions, applicability conditions, and limitations.
   - Derive `research_gaps[]` from cited limitations/conflicts; mark uncorroborated synthetic inferences inline with `[作者判断]` or `[AUTHOR_JUDGMENT]`.
   - Bind every entry in `contributions[]` to concrete `own_evidence_locators`, `linked_methods`, `linked_figures_tables`, `linked_metrics`, and explicit `scope_qualification`.
   - Tag every missing metadata field, missing citation text, conflict, or unverified claim inline as `[待作者确认: GAP-XX]` (or `[AUTHOR_CONFIRM: GAP-XX]`) matched 1-to-1 in `author_confirmation_items[]`.
2. **`references[]` & `excluded_references[]`**: Include verified metadata (`ref_id`, `cite_key`, `authors`, `year`, `title`, `venue`, `doi_or_id`, `recency_role`, `source_locator`, `formatted_citation`) and justify any excluded candidate references.
3. **`conflicts[]`, `change_records[]`, `uncertainty[]`**: Disclose every `CONF-XX`, log revisions if revising a draft, and record format/evidence uncertainties (e.g., adopted default when journal style is unspecified).
4. **`contract_overrides`**: Supply exact values for any custom `output_contract` field names not covered by canonical semantic roles.

### Step 5: 10-Axis Mechanical Verification & Self-Correction Loop
```bash
python3 "$SKILL_DIR/scripts/verify_intro_rw.py" \
  --payload /app/work/section_payload.json \
  --ledger /app/work/source_ledger.json \
  --case-plan /app/work/case_plan.json \
  --out /app/work/verification_report.json
```
If exit code is non-zero (`error_count > 0`), inspect `/app/work/verification_report.json`, fix every reported error (`UNRESOLVED_INLINE_CITATION`, `FABRICATED_REFERENCE`, `METADATA_YEAR_MISMATCH`, `UNBACKED_RESEARCH_GAP`, `UNALIGNED_CONTRIBUTION`, `UNGROUNDED_METRIC`, `UNDISCLOSED_CONFLICT`, `ORPHAN_INLINE_GAP`, etc.) and re-run until `"passed": true`. Also address high-value `warnings` (`HOLLOW_CLICHE_OPENING`, `CITATION_DUMP`, `CONTRIBUTION_OVERCLAIM`).

### Step 6: Assemble Final Verified Deliverables
```bash
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json \
  --ledger /app/work/source_ledger.json \
  --payload /app/work/section_payload.json \
  --verification /app/work/verification_report.json \
  --checkpoint final \
  --out-dir /app/output
```
Confirm `/app/output/result.json` contains `case_id`, all `output_contract` keys, honest `status`, `sources`, `uncertainty`, and `change_records`, alongside editable deliverables.
