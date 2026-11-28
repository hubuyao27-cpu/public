---
name: academic-citation-manager
description: >
  Claim-level academic citation placement, deterministic cross-zone renumbering & bidirectional
  sync, explainable 3-tier reference deduplication with anti-false-merge & recycled-DOI guardrails,
  multi-format style conversion (GB/T 7714-2015 numeric/author-year, APA 7/6, IEEE, Nature,
  Vancouver, Chicago; BibTeX, RIS, EndNote, CNKI, CSL-JSON, CSV), and local evidence claim
  verification. Use whenever tasks involve inserting, relocating, renumbering, deduplicating,
  formatting, converting, or auditing academic citations and reference libraries under Harbor
  (/input/case.json, /app/output) or standalone scientific writing.
---

# Academic Citation Manager (`academic-citation-manager`)

Deliver **publication-ready, deterministically synchronized manuscripts and provenance-tracked bibliographies** solving four core academic citation failures:
1. **挂得对 (Clause-Level Placement)**: Move paragraph-end citation clusters (`[1-4]`) directly to the exact supporting clause before punctuation; replace unsupported placeholders (`[REF]`, `[待引用]`, `[?]`) with verified `[[CITE:REF_XXX]]` tokens or explicit `[证据不足/待人工核验]` / `[INSUFFICIENT_EVIDENCE]` flags.
2. **编得顺 (Deterministic Cross-Zone Sync)**: Author `[[CITE:REF_XXX]]` / `[[CITE_NARR:REF_XXX]]` tokens and let `verify_and_assemble.py` assign strict `1..K` sequential numbering (or sorted Author-Year with `a/b` suffixes) across body text, captions, tables, appendices, footnotes, and bibliography — even after insertions, deletions, or `section_order_override`.
3. **收得净 (Explainable Dedup + Guardrails)**: Merge preprints with published editions, bilingual CN/EN records, and heterogeneous exports (`.bib`, `.ris`, `.enw`, EndNote `.xml`, CNKI `.txt`, `.csl.json`, `.csv`, inline references) with field-level provenance, while blocking false merges of same-author distinct works and flagging recycled DOIs.
4. **对得上 (Claim & Metadata Auditing)**: Cross-check claims against local evidence PDFs/abstracts, catch dangling/fabricated references and metadata anomalies, never invent missing DOIs/pages, and disclose offline verification boundaries honestly (`not_checked_offline`).

---

## Runtime Paths & Budget Discipline (64 Requests / 900s)

Resolve `$SKILL_DIR` first (works under `/skills/weekly-skill`, `/app/submission`, or local checkouts):
```bash
SKILL_DIR="${SKILL_DIR:-}"
for d in "$SKILL_DIR" "/skills/academic-citation-manager" "/skills/weekly-skill" "/app/submission" "/app/skill" "."; do
  [ -n "$d" ] && [ -f "$d/scripts/prepare_case.py" ] && SKILL_DIR="$d" && break
done
mkdir -p /app/work /app/output
```

---

## Clean 3-Step Execution Workflow

### Step 1: Prepare Case, Parse Inputs & Save Early Partial Checkpoint
```bash
python3 -B "$SKILL_DIR/scripts/prepare_case.py" \
  --input-dir /input \
  --work-dir /app/work \
  --out-dir /app/output
```
This single command inspects `case.json` + `task.md` + all input files (plus inline materials), parses the manuscript losslessly, deduplicates libraries (`library_canonical.json`), indexes local evidence PDFs/texts (`evidence_corpus.json`), emits compact digests (`case_brief.md`, `library_digest.md`, `manuscript_digest.md`) and `/app/work/citation_plan_skeleton.json`, and immediately persists an early partial `/app/output/result.json`.

- **Empty/Corrupt Short-Circuit**: If all inputs are missing/corrupt/encrypted (`canonical_refs == 0` and `sentences == 0`), do NOT invent prose or citations; write honest `uncertainty` in `/app/work/citation_plan.json` and run Step 3 so `result.json` exits cleanly with `status: "unable_to_determine"`.
- **Library-Only Mode**: If `sentences == 0` and the task only asks to deduplicate or convert references, keep `manuscript_edits: []`.

### Step 2: Read Compact Digests & Write `/app/work/citation_plan.json`
Read `/input/task.md` (if present), `/app/work/case_brief.md`, `/app/work/library_digest.md`, `/app/work/manuscript_digest.md` (and `/app/work/evidence_corpus.json` only if needed beyond the pre-matched evidence snippets). Write a **minimal** `/app/work/citation_plan.json` (fast-path: omit unmodified sentences and omit `claim_verifications` unless overriding pre-matched verdicts):
1. Confirm `target_style` (`gbt7714_2015_numeric`, `gbt7714_2015_author_year`, `apa7`, `apa6`, `ieee`, `nature`, `vancouver`, `chicago_author_date`), `in_text_format`, and optional `requested_output_formats` (`["docx"]`, `["tex"]`).
2. **`dedup_decisions`**: Decide `"merge"` or `"keep_separate"` for every `AMB_XXX` candidate (never merge `recycled_doi_conflicts`).
3. **`metadata_resolutions`**: Fill/correct fields **only** from raw records or local evidence (`source_locator` required). Never invent DOIs, pages, or volumes.
4. **`manuscript_edits`**: Only include sentences requiring insertion, paragraph-end dump splitting, dangling-marker replacement, deletion (`"action": "delete"`), or Author-Year disambiguation (all unmodified sentences are auto-tokenized and renumbered in order). Embed `[[CITE:REF_001]]`, `[[CITE:REF_001,REF_002]]`, or `[[CITE_NARR:REF_001]]` (or `[REF_001]` shorthand) at the exact supported clause before punctuation; mark unsupported claims `[证据不足/待人工核验]` (ZH) or `[INSUFFICIENT_EVIDENCE]` (EN). Set `section_order_override` if reordering sections.
5. **`claim_verifications`**: `citation_plan_skeleton.json` already pre-populates baseline entries with exact `file:pX_LY` / `#abstract` locators and verbatim quotes from local evidence (and `verify_and_assemble.py` auto-hydrates them if omitted). Review/override only claims that need adjustment (`claim_mismatch`, `fabricated_or_dangling_ref`, `metadata_conflict`, `insufficient_evidence_in_library`) without paraphrasing `evidence_quote`.
6. **`human_confirmation_items` & `contract_overrides`**: Record unresolved gaps/conflicts and any custom `output_contract` keys.

### Step 3: Synchronize, Verify 10 Invariants & Assemble `/app/output`
```bash
python3 -B "$SKILL_DIR/scripts/verify_and_assemble.py" \
  --work-dir /app/work \
  --out-dir /app/output \
  --plan /app/work/citation_plan.json
```
Inspect the printed verification report. If `error_count > 0`, fix the flagged defects in `/app/work/citation_plan.json` and re-run Step 3 until `"passed": true` so `result.json` finishes with `status: "completed"`. Confirm `/app/output/` contains `result.json`, `revised_manuscript.md` (+ `.docx`/`.tex` and previews when applicable), `references_formatted.md`, `references_clean.{bib,ris,csl.json}`, `references_table.csv`, `citation_audit_report.md` (including Field-Level Source Evidence Ledger), and `provenance_ledger.json`.

---

## Strict Honesty Rules
- Consult `$SKILL_DIR/resources/edge_cases_and_recovery.md` for offline DOIs, recycled DOIs, missing fields, malformed exports, GBK/UTF-16 files, and corrupt PDFs/DOCX.
- **Never** fabricate DOIs, page ranges, online verification confirmations, or ungrounded `evidence_quote` strings.
