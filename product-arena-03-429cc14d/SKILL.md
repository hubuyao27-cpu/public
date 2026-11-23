---
name: academic-citation-manager
description: >
  Claim-level citation placement, lock-step cross-zone renumbering & bidirectional body-bibliography
  sync, explainable 4-tier reference deduplication, multi-format conversion (BibTeX, RIS, EndNote,
  CNKI, GB/T 7714-2015, APA 7, IEEE, Nature, Vancouver, Chicago), and source authenticity /
  claim-to-reference verification skill. Use when inserting citations at exact claim clauses,
  reordering/renumbering references across body/captions/tables/footnotes/appendices, merging
  duplicate preprint/published or bilingual records, converting citation styles, or auditing
  fabricated/mismatched citations in Markdown, LaTeX, DOCX, TXT, or PDF manuscripts.
---

# Academic Citation Manager (`academic-citation-manager`)

Manage academic citations end-to-end with **claim-level precision, zero orphan/gap errors, explainable deduplication, and full snapshot provenance**.

## Core Guarantees
1. **挂得对 (Claim-Level Placement)**: Insert citations right after the exact claim clause or named entity requiring support (`after_phrase` / `before_clause_punct`) — never dump citations lazily at paragraph ends or near captions.
2. **编得顺 (Lock-Step Cross-Zone Sync)**: After insertions, deletions, or section reordering, traverse Abstract, Body, Captions, Tables, Footnotes, and Appendices in reading order to assign continuous `1..N` numbering (or `Author, Year[a/b]`), sort/compress brackets (`[2–5]`), and regenerate the bibliography in lock step.
3. **收得净 (Explainable 4-Tier Deduplication)**: Merge duplicate records across BibTeX, RIS, EndNote, CNKI, CSL-JSON, CSV, and embedded reference lists using DOI/arXiv/PMID -> normalized title + first author -> bilingual cross-script matching, prioritizing formal journal/conference metadata over preprints while enforcing an anti-false-merge guard on same-author same-year distinct papers.
4. **对得上 (Authenticity & Claim-Match Auditing)**: Keep an immutable `library_snapshot.json`; flag unresolved/fabricated citations, metadata mismatches, missing fields (never fabricate!), and verify claim-to-reference alignment honestly (`supported`, `partially_supported`, `mismatch_or_unsupported`, `insufficient_source_text`, `unresolved_ref`).

---

## Runtime Paths & Budget Discipline
Resolve `$SKILL_DIR` automatically before running commands:
```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
mkdir -p /app/work /app/output
```
- **Inputs**: `/input/case.json`, `/input/task.md`, manuscript file(s), and reference library file(s) (`.bib`, `.ris`, `.enw`, `.xml`, CNKI `.txt`, `.json`, `.csv`, `.tsv`, or embedded bibliography).
- **Offline Integrity**: Never invent missing DOIs, page ranges, volumes, abstracts, or references. If library entries lack abstracts/full text, mark claim alignment as `insufficient_source_text` rather than guessing.
- **Budget Discipline (64 requests / 900 s)**: Read `/app/work/citation_brief.md` and `/app/work/dedup_report.json` instead of raw library dumps; save an early partial checkpoint at Step 3.

---

## 6-Step Execution Workflow

### Step 1: Inspect Case Contract, Style & Requested Actions
```bash
python3 "$SKILL_DIR/scripts/inspect_case.py" \
  --input-dir /input \
  --out /app/work/case_plan.json
```
Confirm `case_id`, `output_contract` fields, `target_style` (`gbt7714_numeric` | `gbt7714_author_year` | `apa7` | `ieee` | `nature` | `vancouver` | `chicago_author_year`), `requested_actions`, `manuscript_files`, `library_files`, and `missing_referenced_files`.

### Step 2: Parse Multi-Format Libraries & Run Explainable Deduplication
```bash
python3 "$SKILL_DIR/scripts/parse_and_dedup_library.py" \
  --case-plan /app/work/case_plan.json \
  --out-dir /app/work
```
Produces `/app/work/library_snapshot.json` (immutable raw provenance `RAW-001`..), `/app/work/canonical_library.json` (`REF-001`.. with preprint->published precedence), and `/app/work/dedup_report.json` (`merge_groups`, `preserved_distinct_pairs`, `ambiguous_candidates`).

### Step 3: Analyze Manuscript Citations & Save Early Partial Checkpoint
```bash
python3 "$SKILL_DIR/scripts/analyze_manuscript_citations.py" \
  --case-plan /app/work/case_plan.json \
  --library /app/work/canonical_library.json \
  --out-dir /app/work
```
Save an honest partial checkpoint immediately:
```bash
python3 "$SKILL_DIR/scripts/assemble_output.py" \
  --case-plan /app/work/case_plan.json \
  --snapshot /app/work/library_snapshot.json \
  --dedup /app/work/dedup_report.json \
  --checkpoint partial \
  --out-dir /app/output
```
- **Exception Short-Circuit**: If manuscript or library files are completely unreadable/missing/corrupt, emit an honest `unable_to_determine` `result.json` documenting exact errors without fabricating entries.

### Step 4: Author Decisions & Run Deterministic Sync/Formatter
Read `/app/work/citation_brief.md` and `/app/work/dedup_report.json`, then write `/app/work/citation_decisions.json` conforming to `$SKILL_DIR/resources/payload_schema.json`:
- Resolve any `ambiguous_candidates` in `dedup_overrides`.
- For uncited claims needing citations, specify exact `anchor_phrase` + `placement_mode` (`after_phrase` / `before_clause_punct`) + `ref_ids` + `match_verdict` + `match_evidence`.
- Audit every existing citation point (`keep`, `replace`, `remove`, or `flag_unresolved`) with honest `match_verdict` and `match_evidence`.
- Record only source-backed `metadata_corrections` (with `evidence_source`); never invent missing fields.

Execute deterministic cross-zone synchronization & bibliography formatting:
```bash
python3 "$SKILL_DIR/scripts/sync_and_format.py" \
  --case-plan /app/work/case_plan.json \
  --library /app/work/canonical_library.json \
  --analysis /app/work/manuscript_analysis.json \
  --decisions /app/work/citation_decisions.json \
  --out /app/work/synced_package.json
```

### Step 5: 8-Axis Mechanical Citation Verification Loop
```bash
python3 "$SKILL_DIR/scripts/verify_citations.py" \
  --synced /app/work/synced_package.json \
  --snapshot /app/work/library_snapshot.json \
  --decisions /app/work/citation_decisions.json \
  --case-plan /app/work/case_plan.json \
  --out /app/work/verification_report.json
```
Inspect `/app/work/verification_report.json`. Fix every `ERROR` (dangling/orphan citations, numbering gaps, residual duplicates, fabricated refs, literal `None` artifacts, unflagged mismatches, stale caption/footnote numbers, undisclosed gaps) and re-run `sync_and_format.py` + `verify_citations.py` until `"passed": true` (exit code `0`).

### Step 6: Assemble Verified Deliverables
```bash
python3 "$SKILL_DIR/scripts/assemble_output.py" \
  --case-plan /app/work/case_plan.json \
  --snapshot /app/work/library_snapshot.json \
  --dedup /app/work/dedup_report.json \
  --synced /app/work/synced_package.json \
  --decisions /app/work/citation_decisions.json \
  --verification /app/work/verification_report.json \
  --checkpoint final \
  --out-dir /app/output \
  --strict
```
Verify `/app/output/` contains `result.json` (with `case_id` and every `output_contract` field), `manuscript_updated.md` (plus `.tex`/`.docx` and PDF/PNG previews if applicable), `references_formatted.md`, `references_clean.bib`, `references_clean.ris`, `dedup_and_renumbering_report.md`, `citation_verification_matrix.csv`, and `citation_verification_matrix.md`.
