---
name: citation-reference-manager
description: "Evidence-grounded academic citation and reference management skill for Harbor and standalone scientific writing. Use when inserting or relocating citations to exact claim clauses, deterministically renumbering and synchronizing in-text markers (body, captions, tables, footnotes, appendices, and bibliography), deduplicating heterogeneous libraries (BibTeX, RIS, EndNote, CNKI, CSL-JSON, CSV, inline case.json) across preprints and bilingual CN/EN records without false merges, converting styles (GB/T 7714-2015, APA 7th/6th, IEEE, Nature, Vancouver, Chicago), and auditing claim-to-reference support and metadata provenance."
---

# Citation & Reference Manager (`citation-reference-manager`)

Transform messy academic drafts and heterogeneous reference libraries into **publication-ready, deterministically synchronized manuscripts and provenance-tracked bibliographies** addressing four core failure modes:
1. **挂得对 (Clause-Level Placement)**: Relocate paragraph-end citation dumps (`[1-4]`) directly to the exact supporting clause or claim right before punctuation; replace unsupported placeholders (`[REF]`, `[待引用]`, `[?]`) with verified `[[CITE:REF_XXX]]` tokens or explicit `[证据不足/待人工核验]` / `[INSUFFICIENT_EVIDENCE]` markers.
2. **编得顺 (Deterministic Renumbering & Cross-Ref Sync)**: Never hand-renumber. Author `[[CITE:REF_XXX]]` / `[[CITE_NARR:REF_XXX]]` tokens and let `sync_and_format.py` guarantee strict `1..K` sequential continuity (or sorted Author-Year with `a/b` suffixes) across body text, figure/table captions, table cells, footnotes, appendices, and bibliography — even after insertions, deletions, or section reordering (`section_order_override`).
3. **收得净 (Explainable Dedup + Anti-False-Merge Guardrails)**: Merge arXiv/bioRxiv preprints with published editions, bilingual CN/EN duplicates, and cross-database exports (BibTeX, RIS, EndNote XML/tagged, CNKI, CSL-JSON, CSV, inline `case.json`/`task.md`) with explicit rule/similarity provenance, while protecting same-author distinct papers (distinct DOIs, disjoint pages, Part I vs Part II) and catching recycled DOIs.
4. **对得上 (Source & Claim Verification + Field Provenance)**: Cross-verify metadata and quantitative claims against local evidence PDFs/abstracts, detect dangling/fabricated references and claim mismatches, preserve field-level provenance (`source_file:line`), and disclose offline DOI verification limits honestly (`not_checked_offline`).

---

## Runtime Paths & Budget Discipline (64 Requests / 900s)

Resolve `$SKILL_DIR` first (works under Harbor `/skills/weekly-skill`, `/app/submission`, or local checkouts):
```bash
SKILL_DIR="${SKILL_DIR:-}"
for d in "$SKILL_DIR" "/skills/weekly-skill" "/app/submission" "."; do
  [ -n "$d" ] && [ -f "$d/scripts/prepare_workspace.py" ] && SKILL_DIR="$d" && break
done
mkdir -p /app/work /app/output
```

- **Inputs**: `/input/case.json`, `/input/task.md` (or `./task.md`), manuscripts (`.md`, `.docx`, `.tex`, `.txt`, `.html`, `.pdf`), libraries (`.bib`, `.ris`, `.enw`, `.xml`, CNKI `.txt`, `.json`/`.jsonl`, `.csv`/`.tsv`), local evidence `.pdf`s, plus inline materials inside `case.json` or `task.md`.
- **Workspace**: `/app/work`
- **Outputs**: `/app/output` (`result.json` with `case_id` and every declared `output_contract` key, `revised_manuscript.md`, `references_formatted.md`, `references_clean.{bib,ris,csl.json}`, `references_table.csv`, `citation_audit_report.md`, `provenance_ledger.json`, plus `.docx`/`.tex` and real rendered PDF/PNG previews when requested).

---

## Streamlined 3-Step Execution Workflow

### Step 1: One-Command Preparation, Pre-Audit & Early Partial Checkpoint
Run `prepare_workspace.py` (which executes `inspect_case.py` -> `parse_manuscript.py` -> `parse_and_dedup_library.py` -> `verify_sources_pre.py` -> `assemble_deliverables.py --checkpoint partial` in a single request):
```bash
python3 -B "$SKILL_DIR/scripts/prepare_workspace.py" \
  --input-dir /input \
  --work-dir /app/work \
  --out-dir /app/output
```
This single command:
1. Detects `case_id`, `output_contract_parsed.fields`, `target_style` (`gbt7714_2015_numeric`, `gbt7714_2015_author_year`, `apa7`, `apa6`, `ieee`, `nature`, `vancouver`, `chicago_author_date`), `in_text_format`, `requested_output_formats`, missing files, and inline `case.json`/`task.md` materials.
2. Parses the manuscript across body, captions, tables, footnotes, and appendices (`manuscript_claims_digest.md`) and deduplicates libraries with 3-tier anti-false-merge rules (`library_digest.md`).
3. Cross-checks citations against local evidence PDFs/abstracts (`verification_worklist.md`), emits a clean template `/app/work/citation_plan_skeleton.json` (with `_review_hints` listing every item needing review), and persists an early honest partial `/app/output/result.json`.

*(All individual stage scripts remain directly callable if step-by-step debugging is needed.)*
- **Exception Short-Circuit**: If both disk files and inline materials are empty or completely unreadable (`corrupt_or_unreadable` / encrypted), do **not** invent references or prose; write honest `uncertainty[]` and `human_confirmation_items[]` in `/app/work/citation_plan.json` and run Step 3 (`finalize_workspace.py`) so `result.json` exits cleanly with `status: "unable_to_determine"` or `"partial"`.
- **Library-Only Mode**: If `manuscript_parsed.json` has `0` sentences (`total_sentences == 0`) and the task only asks to deduplicate, clean, or convert references, keep `manuscript_edits: []` — `sync_and_format.py` automatically formats all canonical references.

### Step 2: Read Compact Digests & Author `/app/work/citation_plan.json`
Read `/input/task.md` (if present), `/app/work/case_plan.json`, `/app/work/library/library_digest.md`, `/app/work/manuscript/manuscript_claims_digest.md`, and `/app/work/verify/verification_worklist.md` (inspect `/app/work/library/evidence_fulltexts.json` when verifying local PDF claims/quotes). Author `/app/work/citation_plan.json` conforming to `$SKILL_DIR/resources/plan_schema.json` (using `/app/work/citation_plan_skeleton.json` as structural reference, never leaving unreviewed placeholder text):
0. **Confirm `target_style`, `in_text_format`, & `requested_output_formats`**: Verify auto-detected `target_style` and `in_text_format` against `/input/task.md` & `case.json`! Legal `(target_style, in_text_format)` pairs:
   - `gbt7714_2015_numeric`: `superscript_bracket` (`<sup>[1]</sup>`), `bracket` (`[1]`), `superscript_md`, `footnote`, or `latex_cite` (`.tex`)
   - `gbt7714_2015_author_year` / `apa7` / `apa6` / `chicago_author_date`: `author_year` (`(Smith, 2020)`) or `latex_cite` (`\citep`/`\citet` for `.tex`)
   - `ieee`: `bracket_separate` (`[1]–[3], [5]`), `bracket`, or `latex_cite` | `nature`: `superscript_html` (`<sup>1</sup>`) or `latex_cite` | `vancouver`: `bracket` or `superscript_bracket`
   - Optionally set `"requested_output_formats": ["docx"]` or `["tex"]` if the prompt requests Word/LaTeX output not auto-detected.
1. **`dedup_decisions`**: Explicitly decide `"merge"` or `"keep_separate"` with concrete rationale for every `AMB_XXX` entry in `ambiguous_duplicate_candidates` (use canonical `REF_XXX` or `RAW_XXX` IDs). Never merge distinct works by the same author or `recycled_doi_conflicts`.
2. **`metadata_resolutions`**: Resolve field conflicts or fill verified fields **only** from raw library records or local evidence PDFs (citing exact `source_locator`). Use canonical field names (`venue`, `issue`, `authors: list[str]`, bare `doi: "10.xxxx/..."`, `pages`, `year: int`, `publisher`, `city`, `arxiv_id`, `url`; common aliases `journal`/`booktitle`/`number`/`author`/`page` are also normalized). Never fabricate missing DOIs, volumes, issues, or page ranges.
3. **`manuscript_edits`**: Only include sentences that actually need insertion, paragraph-end `[1-4]` cluster splitting, dangling-ref replacement, ambiguous author-year disambiguation (`_review_hints.ambiguous_marker_links_to_disambiguate`), deletion (`"action": "delete"`), or clause-level relocation (all other clean sentences are automatically tokenized and renumbered in order):
   - Embed `[[CITE:REF_001]]` or `[[CITE:REF_001,REF_002]]` (or `[[CITE_NARR:REF_001]]` for narrative citations like `Smith (2020)` or `Smith<sup>[1]</sup>`) at the **exact supported clause**, right before clause/sentence punctuation — never dump citations at paragraph ends or near captions.
   - Replace every raw placeholder (`[REF]`, `[?]`, `[待引用]`, `[CITATION]`, `[TODO:...]`) with verified `[[CITE:...]]` tokens if supported, or explicit `[证据不足/待人工核验]` / `[INSUFFICIENT_EVIDENCE]` if no library record supports the claim (and log a matching entry in `human_confirmation_items`).
   - If the task asks to reorder chapters/sections, populate `section_order_override` (child subsections stay attached to their parent section).
4. **`claim_verifications`**: Audit every cited sentence honestly with per-claim verdicts (`supported_by_local_fulltext`, `supported_by_abstract`, `title_metadata_only_unverified_claim`, `claim_mismatch`, `fabricated_or_dangling_ref`, `metadata_conflict`, `insufficient_evidence_in_library`). Paste exact short verbatim `evidence_quote` substrings (`>= 12` chars copied verbatim from `library_digest.md` abstracts or `evidence_fulltexts.json` pages; leave `""` if only title/metadata exists!), and explain every contradiction in `mismatch_detail`.
5. **`human_confirmation_items` & `contract_overrides`**: Document every dangling citation, recycled DOI conflict, claim mismatch, or evidence gap in `human_confirmation_items[]` and `uncertainty[]`. Populate `contract_overrides` for any custom keys declared in `case_plan.json["output_contract_parsed"]` (values are automatically coerced to the declared contract type).

### Step 3: One-Command Synchronization, 10-Invariant Verification & Final Assembly
```bash
python3 -B "$SKILL_DIR/scripts/finalize_workspace.py" \
  --work-dir /app/work \
  --out-dir /app/output \
  --plan /app/work/citation_plan.json
```
This runs `sync_and_format.py` -> `assemble_deliverables.py --checkpoint final` -> `verify_deliverables.py` -> `assemble_deliverables.py --checkpoint final` (attaching `verification_report.json` cleanly into `/app/output/result.json`).
- If exit code is non-zero (`error_count > 0`), inspect `/app/work/verification_report.json`, fix every reported defect in `/app/work/citation_plan.json` (residual internal tokens, bidirectional/sequential numbering mismatches, dirty formatting, ungrounded quotes, leftover `[REF]` placeholders, missing contract keys), and re-run `finalize_workspace.py` until `"passed": true`.
- Confirm `/app/output/` contains `result.json` (preserving `case_id` and every `output_contract` field), editable `revised_manuscript.md` (+ `.docx`/`.tex` and previews if requested), `references_formatted.md`, `references_clean.{bib,ris,csl.json}`, `references_table.csv`, `citation_audit_report.md`, and `provenance_ledger.json`.

---

## Strict Honesty & Exception Guidelines
- Consult `$SKILL_DIR/resources/failure_modes_and_edge_cases.md` on offline DOIs, recycled DOIs, missing required fields, malformed `.bib`/`.ris`/CNKI exports, GBK text, encrypted PDFs, or empty inputs.
- **Never** invent DOIs, page numbers, volume/issue numbers, online verification confirmations (`not_checked_offline`), or fabricated `evidence_quote` substrings.
