---
name: citation-reference-manager
description: >
  End-to-end academic citation placement, deterministic sequential/author-year renumbering,
  cross-reference synchronization (body, captions, tables, footnotes, appendices), explainable
  multi-source bibliography deduplication (CN/EN, preprint vs. published, anti-false-merge guards),
  style conversion (GB/T 7714-2015, APA 7th/6th, IEEE, Nature, Vancouver, Chicago, BibTeX/RIS/CSL-JSON),
  and claim-to-reference source verification for Harbor academic writing tasks.
---

# Citation & Reference Manager (`citation-reference-manager`)

Deliver clean, editable manuscripts, synchronized reference lists, multi-format bibliography exports (`.bib`, `.ris`, `.csl.json`, `.csv`), field-level provenance, and verifiable audit trails directly from `/input` materials.

## Core Focuses
1. **挂得对 (Clause-Level Placement)**: Move citations from lazy paragraph-end clusters (`[1-4]`) directly to the specific claim clauses needing support; distinguish `[REF]` / unverified claims cleanly.
2. **编得顺 (Synchronized Renumbering)**: Maintain strict `1..K` sequential continuity after insertions, deletions, or section reordering across body text, figure/table captions, table cells, footnotes, and appendices, OR sorted Author-Year with `a/b` suffixes.
3. **收得净 (Explainable Deduplication & Anti-False-Merge)**: Merge duplicate CN/EN titles, preprints vs. published papers, and cross-DB exports (BibTeX/RIS/EndNote XML/CNKI/inline `case.json`) with clear rationales while protecting distinct works (e.g., distinct DOIs, disjoint pages, Part I vs Part II, recycled-DOI alerts).
4. **对得上 (Claim-to-Reference Verification)**: Cross-check citations against local evidence PDFs/abstracts and detect fabricated references, metadata mismatches, and evidence gaps without guessing offline facts.

---

## Runtime Paths
```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
mkdir -p /app/work /app/output
```
- **Inputs**: `/input/case.json`, `/input/task.md`, and input materials (`.md`, `.txt`, `.docx`, `.tex`, `.pdf`, `.bib`, `.ris`, `.enw`, `.xml`, `.json`, `.csv`, plus inline materials inside `case.json` or `task.md`).
- **Outputs**: `/app/output/result.json` (`case_id`, every `output_contract` field, honest `status`, provenance), `revised_manuscript.md` (+ `.tex`/`.docx`/PDF/PNG previews when requested), `references_formatted.md`, `references_clean.{bib,ris,csl.json}`, `references_table.csv`, `citation_audit_report.md`, `provenance_ledger.json`.

---

## Streamlined 3-Step Workflow (Budget-Aware for 64 Requests)

### Step 1: One-Command Preparation, Pre-Audit & Early Partial Checkpoint
Run `prepare_workspace.py` (which executes `inspect_case.py` -> `parse_manuscript.py` -> `parse_and_dedup_library.py` -> `verify_sources_pre.py` -> `assemble_deliverables.py --checkpoint partial`):
```bash
python3 -B "$SKILL_DIR/scripts/prepare_workspace.py" \
  --input-dir /input \
  --work-dir /app/work \
  --out-dir /app/output
```
This single command:
1. Detects `case_id`, `output_contract`, `target_style`, `in_text_format`, `requested_output_formats`, and inline `case.json`/`task.md` materials.
2. Deduplicates library records with 3-tier anti-false-merge rules (`library_digest.md`) and parses manuscript blocks/sentences/clusters/placeholders (`manuscript_claims_digest.md`).
3. Cross-checks local evidence PDFs (`verification_worklist.md`), emits a ready-to-edit **`/app/work/citation_plan_skeleton.json`**, and persists an honest early partial `/app/output/result.json`.

*(All individual stage scripts remain directly callable if step-by-step debugging is needed.)*
- **Library-Only Mode**: When there is no manuscript text and the task only asks to deduplicate, clean, or format references, keep `manuscript_edits: []` — `sync_and_format.py` automatically formats all canonical references.
- **Empty/Corrupt Short-Circuit**: If both materials and inline fields are completely empty or corrupted, set honest `uncertainty[]` entries in `/app/work/citation_plan.json` without inventing data.

### Step 2: Review Compact Digests & Finalize `/app/work/citation_plan.json`
Read `/app/work/library/library_digest.md`, `/app/work/manuscript/manuscript_claims_digest.md`, and `/app/work/verify/verification_worklist.md` alongside `/app/work/citation_plan_skeleton.json` (consult `$SKILL_DIR/resources/plan_schema.json` and `$SKILL_DIR/resources/failure_modes_and_edge_cases.md` when edge cases arise).
Edit `/app/work/citation_plan_skeleton.json` and save as `/app/work/citation_plan.json`:
1. **`dedup_decisions`**: Decide `"merge"` vs `"keep_separate"` for every Tier-3 ambiguous candidate pre-listed in the skeleton.
2. **`manuscript_edits`**: Update only sentences flagged in the skeleton (`PLACEHOLDER`, `paragraph-end cluster`, `NEEDS_CHECK_NO_CITE`, dangling citations). Embed canonical tokens **`[[CITE:REF_001]]`**, **`[[CITE:REF_001,REF_002]]`**, or **`[[CITE_NARR:REF_001]]`** directly at exact claim clauses (**never** hand-assign numbers!). Clean unmodified sentences are automatically tokenized and renumbered in order.
3. **`metadata_resolutions`**: Only apply metadata fixes directly grounded in raw files/local PDFs with explicit `source_locator` — never invent DOIs or pages offline.
4. **`claim_verifications`**: Confirm verdicts (`supported_by_local_fulltext`, `supported_by_abstract`, `title_metadata_only_unverified_claim`, `claim_mismatch`, `fabricated_or_dangling_ref`) and paste exact short `evidence_quote` substrings only when present in local evidence.
5. **`human_confirmation_items` / `contract_overrides`**: Add author questions for dangling citations, recycled DOIs, or unverified claims; supply `contract_overrides` if custom `output_contract` fields require tailored values.

### Step 3: One-Command Synchronization, 10-Invariant Verification & Final Assembly
```bash
python3 -B "$SKILL_DIR/scripts/finalize_workspace.py" \
  --work-dir /app/work \
  --out-dir /app/output \
  --plan /app/work/citation_plan.json
```
This runs `sync_and_format.py` -> `assemble_deliverables.py --checkpoint final` -> `verify_deliverables.py` -> `assemble_deliverables.py --checkpoint final` (attaching `verification_report.json` cleanly into `/app/output/result.json`). If exit code is non-zero (`error_count > 0`), inspect `/app/work/verification_report.json`, fix reported issues in `/app/work/citation_plan.json`, and re-run `finalize_workspace.py` until `"passed": true`.
