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
1. **立得住 (Grounded Background — CARS Move 1)**: Open directly with domain lineage, concrete technical bottlenecks, and why-now urgency backed by sources — never hollow clichés (*"近年来受到广泛关注"* / *"attracted widespread attention"*).
2. **分得清 (Taxonomic Related Work — Analytical Synthesis)**: Group literature by problem setting, technical route, or evaluation paradigm (`2–4` routes) and compare conclusions, applicability conditions, and limitations across a unified matrix — never chronological paper-by-paper summaries or unanchored citation dumps (`[1-5]`).
3. **说得实 (Evidence-Chained Research Gaps — CARS Move 2 + Bridge)**: Derive gaps explicitly from shared limitations, uncovered operating regimes, or inter-study conflicts across cited literature, ending Related Work with a clear bridge paragraph showing how our method addresses those gaps; mark uncorroborated synthetic inferences explicitly as `[作者判断]` / `[AUTHOR_JUDGMENT]`.
4. **对得上 (Verified Contributions & Citations — CARS Move 3)**: Bind every contribution 1-to-1 to specific methods, figures, tables, and grounded metrics with explicit scope qualification (operating regime/dataset bounds — no unqualified superlatives), and ensure 100% citation metadata accuracy against parsed sources.

---

## Runtime Paths
Resolve `$SKILL_DIR` automatically before invoking scripts (covers Harbor `/skills/weekly-skill`, custom mounts, and local checkouts):
```bash
for cand in "${SKILL_DIR:-}" /skills/weekly-skill /skills/* "$HOME/.agents/skills/sci-intro-rw-writer" "$(pwd)"; do
  if [ -n "$cand" ] && [ -f "$cand/scripts/prepare_workspace.py" ]; then SKILL_DIR="$cand"; break; fi
done
mkdir -p /app/work /app/output
```
- **Inputs**: `/input/case.json`, `/input/task.md` (or `./task.md`), and actual files under `/input/`. Never infer scientific facts from directory labels or filenames alone.
- **Work**: `/app/work` (`case_plan.json`, `source_ledger.json`, `evidence_digest.md`, `section_payload_skeleton.json`, optional `manuscript.md`, `section_payload.json`, `verification_report.json`).
- **Outputs**: `/app/output/` (`result.json` with `case_id` + all `output_contract` fields, `introduction_and_related_work.md`, `related_work_matrix.md/.csv`, `evidence_and_citation_audit.md`, `provenance_map.json`, plus structured `.tex`/`.bib`/`.docx`/taxonomy previews when requested).

---

## Streamlined 3-Step Workflow (Budget-Aware for 64 Requests)
*(POSIX shells use `python3 -B`; on Windows native PowerShell/CMD use `python -B`.)*

### Step 1: One-Command Workspace Prep & Honest Partial Checkpoint
Run `prepare_workspace.py` (executes `inspect_case.py` -> `ingest_materials.py` -> `assemble_deliverables.py --checkpoint partial` without running slow binary compilers):
```bash
python3 -B "$SKILL_DIR/scripts/prepare_workspace.py" \
  --input-dir /input \
  --work-dir /app/work \
  --out-dir /app/output
```
This single command:
1. Parses `case_id`, `output_contract`, `signals.task_mode`, `language`, `citation_style`, `requested_formats`, and missing/corrupt files into `/app/work/case_plan.json`.
2. Ingests `.bib`, `.ris`, `.json`, `.csv`/`.tsv`, `.md`, `.txt`, `.tex`, `.docx`, `.pdf` (via reading-order-sorted multi-section extraction across Abstract/Method/Experiments/Limitations), and inline `case.json`/`task.md` materials in `evidence_tier_precedence` order into `/app/work/source_ledger.json` and `/app/work/evidence_digest.md`.
3. Emits pre-grounded `/app/work/section_payload_skeleton.json` (with exact `case_id`, `"manuscript_file": ""`, verified `references[]`, pre-detected `conflicts[]`, `author_confirmation_items[]`, and `uncertainty[]`) so you never re-type bibliographic metadata by hand.
4. Saves an honest early `/app/output/result.json` partial checkpoint immediately.

- **Empty/Unreadable Short-Circuit**: If `references_catalog` and `own_paper_evidence` are both empty (or all files failed decryption/parsing), do **not** invent citations or metrics. Consult `$SKILL_DIR/resources/edge_cases_and_fallbacks.md`, complete `/app/work/section_payload.json` from the skeleton with `status: "insufficient_material"`, and finalize.

### Step 2: Read Digest & Write Prose + Payload
Read `/app/work/evidence_digest.md` (which embeds sectioned per-paper excerpts AND the compact `section_payload.json` sub-schema & enum cheat-sheet) and `/app/work/section_payload_skeleton.json` (inspect raw source files only when verbatim quotes/details are needed; consult `$SKILL_DIR/resources/payload_schema.json` only if deeper definitions are needed).

**Tip for Long Prose (Avoid JSON Escaping Errors)**: Write publication-ready Markdown prose cleanly to `/app/work/manuscript.md` and set `"manuscript_file": "/app/work/manuscript.md"` in `/app/work/section_payload.json` (or populate `"manuscript_markdown"` inline).

1. **Drafting Quality & Uniform Citation Anchors**:
   - Use **`[R01]` / `[R01, R02]`** uniformly in prose — `assemble_deliverables.py` orders sequential numeric bibliographies (`gbt7714`/`ieee_numeric`/`nature_numeric`) by **first appearance in prose** and renders `[R01]` into `[1]`, `[1], [2]`, `(Surname et al., Year)`, or `\cite{key}` across `.md`, `.docx`, `.tex`, and `result.json` with 100% consistency.
   - For full `draft_intro_rw` / `revise_and_strengthen` / `bilingual_writing`: cover **Introduction** (domain lineage, bottleneck, gap preview, scoped contributions), **Taxonomic Related Work** (`2–4` routes comparing conclusions, applicability conditions, limitations + bridge paragraph), **Research Gaps**, and **Contributions**.
   - For focused `organize_related_work` / `fill_and_audit_citations` / `align_contributions`: prioritize the exact requested deliverables without padding unrequested sections; still anchor every claim and citation.
   - Mark synthetic inferences inline with `[作者判断]` / `[AUTHOR_JUDGMENT]`, and missing metadata/conflicts/unverified claims inline with `[待作者确认: GAP-XX]` / `[AUTHOR_CONFIRM: GAP-XX]`.
2. **Keep Pre-Grounded Metadata & Let Deterministic Sync Help**: Retain `references[]`, `conflicts[]`, and `author_confirmation_items[]` from `section_payload_skeleton.json` so `METADATA_YEAR_MISMATCH` never triggers. Both `verify_intro_rw.py` and `assemble_deliverables.py` auto-derive starter `comparison_matrix[]` from `related_work_groups[]` and candidate `claim_citation_map[]` from cited sentences if left empty.
3. **`contract_overrides`**: Supply explicit values only if `case_plan.json` lists custom `output_contract` fields not automatically covered by semantic roles.

### Step 3: One-Command Verify & Assemble (`finalize_workspace.py`)
Run `finalize_workspace.py` (executes `verify_intro_rw.py` -> if passed, runs `assemble_deliverables.py --checkpoint final`; if failed, refreshes `--checkpoint partial` and outputs exact `fix_hint`s):
```bash
python3 -B "$SKILL_DIR/scripts/finalize_workspace.py" \
  --work-dir /app/work \
  --out-dir /app/output
```
If exit code is non-zero (`error_count > 0`), inspect `/app/work/verification_report.json`, fix any blocking errors (`UNRESOLVED_INLINE_CITATION`, `METADATA_YEAR_MISMATCH`, `UNBACKED_RESEARCH_GAP`, `UNALIGNED_CONTRIBUTION`, `UNGROUNDED_METRIC`, `UNDISCLOSED_CONFLICT`, `ORPHAN_INLINE_GAP`, etc.), and re-run `finalize_workspace.py` until `"verification_passed": true`. Confirm `/app/output/result.json` contains `case_id`, all `output_contract` fields, clean rendered citations, `sources`, `uncertainty`, and `change_records`.
