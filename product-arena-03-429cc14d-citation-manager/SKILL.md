---
name: citation-reference-manager
description: "Evidence-grounded academic citation and reference management skill for Harbor and standalone scientific writing. Use when inserting or relocating citations to exact claim clauses, deterministically renumbering and synchronizing in-text markers (captions, footnotes, appendices, and bibliography), deduplicating heterogeneous libraries (BibTeX, RIS, EndNote, CNKI, CSL-JSON, CSV) across preprints and bilingual CN/EN records without false merges, converting styles (GB/T 7714-2015, APA 7th/6th, IEEE, Nature, Vancouver, Chicago), and auditing claim-to-reference support and metadata provenance."
---

# Citation & Reference Manager (`citation-reference-manager`)

Transform messy academic drafts and heterogeneous reference libraries into **publication-ready, deterministically synchronized manuscripts and provenance-tracked bibliographies** addressing four core failure modes:
1. **挂得对 (Clause-Level Placement)**: Relocate paragraph-end citation dumps (`[1-4]`) to the exact supporting clause/claim; mark unsupported claims honestly with `[证据不足/待人工核验]` / `[INSUFFICIENT_EVIDENCE]`.
2. **编得顺 (Deterministic Renumbering & Cross-Ref Sync)**: Never hand-renumber. Author `[[CITE:REF_XXX]]` tokens and let `sync_and_format.py` guarantee strict `1..K` sequential continuity across body, figure/table captions, table cells, footnotes, appendices, and bibliography.
3. **收得净 (Explainable Dedup + Anti-False-Merge Guardrails)**: Merge arXiv/bioRxiv preprints with published editions, bilingual CN/EN duplicates, and cross-database exports with explicit rule/similarity provenance, while protecting same-author distinct papers and catching recycled DOIs.
4. **对得上 (Source & Claim Verification + Field Provenance)**: Cross-verify metadata and quantitative claims against local PDFs/abstracts, audit dangling/fabricated keys, preserve field-level provenance (`source_file:line`), and disclose offline DOI limits honestly.

---

## Runtime Paths & Environment Setup

Resolve `$SKILL_DIR` first (works under Harbor `/skills/weekly-skill` or local checkouts):

```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
mkdir -p /app/work /app/output
```

- **Inputs**: `/input/case.json`, `/input/task.md` (or `./task.md`), manuscripts (`.md`, `.docx`, `.tex`, `.txt`, `.html`, `.pdf`), libraries (`.bib`, `.ris`, `.enw`, `.xml`, CNKI `.txt`, `.json`/`.jsonl`, `.csv`/`.tsv`), and local evidence `.pdf`s.
- **Workspace**: `/app/work`
- **Outputs**: `/app/output` (`result.json`, `revised_manuscript.md`, `references_formatted.md`, `references_clean.bib/.ris/.csl.json`, `references_table.csv`, `citation_audit_report.md`, `provenance_ledger.json`, plus `.docx`/`.tex` + rendered PDF/PNG previews when requested).

---

## Mandatory 6-Step Execution Workflow

### Step 1: Inspect Case Contract, Constraints & Materials
```bash
python3 "$SKILL_DIR/scripts/inspect_case.py" \
  --input-dir /input \
  --out /app/work/case_plan.json
```
Read `/app/work/case_plan.json` (and `/input/task.md`) to confirm `case_id`, all `output_contract_parsed.fields`, detected `target_style` (`gbt7714_2015_numeric`, `gbt7714_2015_author_year`, `apa7`, `apa6`, `ieee`, `nature`, `vancouver`, `chicago_author_date`), `in_text_format`, `requested_output_formats`, and `missing_referenced_files`.

### Step 2: Parse Manuscript, Deduplicate Library & Pre-Audit Sources
Run the three deterministic extractors in sequence (manuscript first so inline reference lists are also ingested):
```bash
python3 "$SKILL_DIR/scripts/parse_manuscript.py" \
  --case-plan /app/work/case_plan.json \
  --out-dir /app/work/manuscript

python3 "$SKILL_DIR/scripts/parse_and_dedup_library.py" \
  --case-plan /app/work/case_plan.json \
  --manuscript /app/work/manuscript/manuscript_parsed.json \
  --rules "$SKILL_DIR/resources/dedup_rules.json" \
  --style-rules "$SKILL_DIR/resources/style_rules.json" \
  --out-dir /app/work/library

python3 "$SKILL_DIR/scripts/verify_sources_pre.py" \
  --case-plan /app/work/case_plan.json \
  --library /app/work/library/library_canonical.json \
  --evidence /app/work/library/evidence_fulltexts.json \
  --manuscript /app/work/manuscript/manuscript_parsed.json \
  --out-dir /app/work/verify
```

### Step 3: Persist Early Honest Partial Checkpoint
Immediately write a partial checkpoint so `/app/output/result.json` exists even if budgets tighten:
```bash
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json \
  --library /app/work/library/library_canonical.json \
  --manuscript /app/work/manuscript/manuscript_parsed.json \
  --pre-audit /app/work/verify/pre_audit.json \
  --checkpoint partial \
  --out-dir /app/output
```
- **Exception Short-Circuit**: If `stats.total_material_files == 0` or all input files failed (`corrupt_or_unreadable` / encrypted), do **not** invent references or prose; proceed directly to Step 5/6 documenting exact errors in `uncertainty` and `human_confirmation_items`.

### Step 4: Read Compact Digests & Author `/app/work/citation_plan.json`
Read `/app/work/library/library_digest.md`, `/app/work/manuscript/manuscript_claims_digest.md`, and `/app/work/verify/verification_worklist.md` (inspect raw files/evidence PDFs only where needed). Author `/app/work/citation_plan.json` following `$SKILL_DIR/resources/plan_schema.json`:
1. **`dedup_decisions`**: Explicitly decide `"merge"` or `"keep_separate"` for every `AMB_XXX` entry in `ambiguous_duplicate_candidates`. Never merge distinct works by the same author or `recycled_doi_conflicts`.
2. **`metadata_resolutions`**: Resolve field conflicts or fill verified fields ONLY from raw library records or local evidence PDFs (citing exact `source_locator`).
3. **`manuscript_edits`**: For every sentence requiring insertion, paragraph-end cluster splitting, dangling-ref replacement, or relocation:
   - Embed `[[CITE:REF_001]]` or `[[CITE:REF_001,REF_002]]` (or `[[CITE_NARR:REF_001]]` for narrative author-year like `Smith (2020)`) at the **exact supported clause**, right before sentence/clause punctuation.
   - Replace unresolvable placeholders (`[REF]`, `[待引用]`) with verified `[[CITE:...]]` tokens if supported, or `[证据不足/待人工核验]` / `[INSUFFICIENT_EVIDENCE]` if no library record supports the claim.
   - If the task reorders chapters/sections, populate `section_order_override`.
4. **`claim_verifications`**: Record per-claim support verdicts (`supported_by_local_fulltext`, `supported_by_abstract`, `title_metadata_only_unverified_claim`, `claim_mismatch`, `fabricated_or_dangling_ref`, `metadata_conflict`, `insufficient_evidence_in_library`) with verbatim `evidence_quote` from local abstracts/PDFs.
5. **`contract_overrides`**: Supply explicit values for any custom keys in `case_plan.json["output_contract_parsed"]` if needed.

### Step 5: Synchronize, Renumber, Convert Formats & Assemble Final Deliverables
```bash
python3 "$SKILL_DIR/scripts/sync_and_format.py" \
  --case-plan /app/work/case_plan.json \
  --library /app/work/library/library_canonical.json \
  --manuscript /app/work/manuscript/manuscript_parsed.json \
  --pre-audit /app/work/verify/pre_audit.json \
  --plan /app/work/citation_plan.json \
  --style-rules "$SKILL_DIR/resources/style_rules.json" \
  --out-dir /app/work/synced

python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json \
  --library /app/work/library/library_canonical.json \
  --manuscript /app/work/manuscript/manuscript_parsed.json \
  --pre-audit /app/work/verify/pre_audit.json \
  --plan /app/work/citation_plan.json \
  --synced /app/work/synced/sync_result.json \
  --checkpoint final \
  --out-dir /app/output
```

### Step 6: Mechanical Self-Audit & Final Assembly Loop
```bash
python3 "$SKILL_DIR/scripts/verify_deliverables.py" \
  --case-plan /app/work/case_plan.json \
  --library /app/work/library/library_canonical.json \
  --evidence /app/work/library/evidence_fulltexts.json \
  --manuscript /app/work/manuscript/manuscript_parsed.json \
  --pre-audit /app/work/verify/pre_audit.json \
  --plan /app/work/citation_plan.json \
  --synced /app/work/synced/sync_result.json \
  --output-dir /app/output \
  --out /app/work/verification_report.json
```
If `error_count > 0`, inspect `/app/work/verification_report.json`, fix `/app/work/citation_plan.json`, re-run Step 5 (`sync_and_format.py` + `assemble_deliverables.py --checkpoint final`), and re-run `verify_deliverables.py` until `"passed": true`.

---

## Strict Honesty & Exception Guidelines
- Consult `$SKILL_DIR/resources/failure_modes_and_edge_cases.md` on offline DOIs, recycled DOIs, missing required fields, malformed `.bib`, GBK text, encrypted PDFs, or empty inputs.
- **Never** invent DOIs, page numbers, volume/issue numbers, online verification confirmations (`not_checked_offline`), or fabricated `evidence_quote` substrings.
