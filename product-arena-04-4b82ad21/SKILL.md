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

Transform raw papers, bibliographies, drafts, and experimental logs into **publication-ready Introduction & Related Work** deliverables with verifiable provenance across every citation, gap, and contribution.

## Academic Writing Craft Standards (4 Core Guarantees)
1. **立得住 (Grounded Background — CARS Move 1)**: Open directly with domain lineage, the concrete technical bottleneck, and quantitative constraints backed by `[R01]` sources. Never start with hollow fillers (*"近年来受到广泛关注"* / *"In recent years, ... attracted widespread attention"*).
2. **分得清 (Taxonomic Related Work — 2D Synthesis + Bridge)**: Group literature into `2–4` explicit methodological or problem-setting routes (never chronological paper-by-paper summaries or unanchored `[1-5]` clusters). Within each route, compare core conclusions, **applicability conditions**, and **limitations** along shared dimensions, and end Related Work with a **bridge paragraph** positioning this work against those exact limits.
3. **说得实 (Evidence-Chained Research Gaps — CARS Move 2)**: Derive each gap from shared limitations, uncovered operating/data regimes, or inter-study conflicts across cited works; mark uncorroborated synthetic inferences explicitly inline with `[作者判断]` or `[AUTHOR_JUDGMENT]`.
4. **对得上 (Scoped Contributions & Verified Citations — CARS Move 3)**: Express each contribution via the 4-part formula (`Method + locator` -> `Metric + locator` -> `Operating/Dataset Scope` -> `Boundary`), avoiding unqualified superlatives (*"首次彻底解决"* / *"universally outperforms all"*). Keep 100% metadata fidelity against parsed sources.

Consult `$SKILL_DIR/resources/templates/intro_rw_template.md` for Base-vs-Gold contrast examples and `$SKILL_DIR/resources/edge_cases_and_fallbacks.md` for empty/corrupt/conflicting inputs.

---

## Runtime Paths & Budget Discipline
Resolve `$SKILL_DIR` once before running commands:
```bash
for cand in "${SKILL_DIR:-}" /skills/weekly-skill /skills/* "$HOME/.agents/skills/sci-intro-rw-writer" "$(pwd)/submission" "$(pwd)"; do
  if [ -n "$cand" ] && [ -f "$cand/scripts/prepare_workspace.py" ]; then SKILL_DIR="$cand"; break; fi
done
mkdir -p /app/work /app/output
```
- **Inputs**: `/input/case.json`, `/input/task.md` (or `./task.md`), and actual files under `/input/`. Read real extracted evidence — never guess from filenames or domain labels.
- **Trigger Guard**: If `signals.is_non_writing_qa` is `true` (pure Q&A asking no writing, revising, organizing, or auditing), return an honest `not_applicable` status without inventing prose.

---

## Streamlined 3-Step Execution Workflow

### Step 1: One-Command Workspace Prep & Honest Partial Checkpoint
```bash
python3 -B "$SKILL_DIR/scripts/prepare_workspace.py" \
  --input-dir /input --work-dir /app/work --out-dir /app/output
```
Runs `inspect_case -> ingest_materials -> assemble_deliverables (--checkpoint partial)` in one step:
- Produces `/app/work/case_plan.json`, `/app/work/source_ledger.json`, `/app/work/evidence_digest.md`, pre-populated `/app/work/section_payload_skeleton.json`, lossless `[locator]`-tagged files under `/app/work/extracted/`, and an early `/app/output/result.json`.
- **Short-Circuit**: If `references_catalog` and `own_paper_evidence` are both empty (or all files failed parsing/decryption), do **not** fabricate citations or metrics — refine `section_payload.json` from the skeleton with `status: "insufficient_material"` and run Step 3.

### Step 2: Inspect Digest / Targeted Queries & Author Prose + Payload
1. Read `/app/work/evidence_digest.md` and `/app/work/section_payload_skeleton.json`.
2. For full-paper PDFs or long notes, query exact `[locator]` lines for limitations, methods, or table metrics instead of dumping entire documents:
   ```bash
   python3 -B "$SKILL_DIR/scripts/query_sources.py" \
     --extracted-dir /app/work/extracted --section Limitations --limit 25
   ```
3. Write publication-ready prose cleanly to `/app/work/manuscript.md` (with `## Introduction`, `## Related Work` + `###` route subsections + concluding bridge paragraph, `## Research Gaps`, `## Contributions`) and save `/app/work/section_payload.json` (`"manuscript_file": "/app/work/manuscript.md"` plus **explicit** `research_background`, `related_work_groups[]` with concrete `applicability_conditions` & `limitations`, `research_gaps[]`, and `contributions[]` with concrete `scope_qualification`):
   - **Uniform Internal Anchors**: Cite works in prose as **`[R01]` / `[R1]` / `[R01, R02]`** — `assemble_deliverables.py` orders numeric bibliographies (`gbt7714`/`ieee_numeric`/`nature_numeric`) by **first appearance in prose** and renders `[R01]` uniformly into `[1]`, `[1], [2]`, `(Author, Year)`, or `\cite{key}` across `.md`, `.docx`, `.tex`, and `result.json`.
   - **Honest Inline Markers**: Tag synthetic inferences `[作者判断]` / `[AUTHOR_JUDGMENT]`, and missing/conflicting items `[待作者确认: GAP-XX]` / `[AUTHOR_CONFIRM: GAP-XX]`.
   - **Lossless Auto-Hydration**: `references[]` (with `field_provenance`), `conflicts[]`, `comparison_matrix[]`, `author_confirmation_items[]`, and `claim_citation_map[]` (with verbatim `evidence_quote` + `[file:pX_LY]` locators) deterministically auto-hydrate from `source_ledger.json`, `/app/work/extracted/`, and `related_work_groups[]` when omitted — focus your tokens on crisp prose and concrete `related_work_groups[]`, `research_gaps[]`, and `contributions[]`.
   - **Custom Contracts**: Populate `contract_overrides` only for custom `output_contract` fields not mapped automatically by semantic roles.

### Step 3: One-Command Verify & Non-Destructive Final Assembly
```bash
python3 -B "$SKILL_DIR/scripts/finalize_workspace.py" \
  --work-dir /app/work --out-dir /app/output
```
- If `verify_intro_rw.py` passes, it writes final verified deliverables (`result.json`, `introduction_and_related_work.md`, `related_work_matrix.md/.csv`, `evidence_and_citation_audit.md` with Field-Level Metadata Provenance + claim quotes, `provenance_map.json`, plus `.tex`/`.bib`/`.docx`/diagram previews only when explicitly requested).
- **Bounded Self-Correction Rule**: If `verify_intro_rw.py` flags blocking errors (`error_count > 0`), `finalize_workspace.py` **preserves your authored manuscript** under an honest `status: "partial"` checkpoint; inspect `/app/work/verification_report.json` and perform **at most 2 repair passes**. If blockers remain due to missing or conflicting user inputs, run `finalize_workspace.py --work-dir /app/work --out-dir /app/output --allow-partial` so an honest `partial` output is finalized cleanly within budget.
