---
name: literature-kg-builder
description: >
  Verifiable, provenance-bound, incremental scientific literature knowledge graph construction skill.
  Use when extracting entities and directed conditional relations from one or multiple research papers
  (JATS JSON/XML manifests, Markdown, plain text, or PDFs), performing cross-paper alias alignment with
  explicit do-not-merge guardrails for distinct physical samples/conditions/homonyms, preserving stable
  IDs during incremental graph updates, executing real provenance-backed graph queries, and exporting
  GraphML, Markdown reports, visual previews, and Harbor-compliant result.json deliverables.
---

# Verifiable Multi-Paper Literature Knowledge Graph Builder (`literature-kg-builder`)

Build an **auditable, provenance-bound, queryable scientific knowledge graph** across one or multiple papers while mechanically preventing hallucinated entities, root-metadata bleed, fake PDF page numbers, same-paper multi-format illusions, false cross-paper merges, and unverified claims.

## Runtime Paths & Tool Location

- **Inputs**: `/input/case.json`, `/input/task.md`, `corpus-manifest.json`, `primary-material.json`, `secondary-material.json`, `material.json`, `secondary-publisher.xml` (and/or `.pdf`, `.md`, `.txt`, `.xml`, `.json` files under `/input/`).
- **Skill scripts**: Locate relative to this `SKILL.md` — in Harbor evaluation this is `/skills/weekly-skill/scripts/` (locally `./scripts/`). Set `SKILL_DIR=/skills/weekly-skill` (or the directory containing this `SKILL.md`).
- **Working directory**: `/app/work` (intermediate indexes, extractions, v1/v2 graphs, audit logs).
- **Final outputs**: `/app/output/` (`result.json`, `kg.json`, `kg.graphml`, `kg_queries.json`, `kg_verification_report.json`, `kg_report.md`, `kg_preview.svg`, and `kg_preview.png`/`kg_preview.pdf` when `matplotlib` is available).

## Seven Hard Guardrails (Mechanically Enforced by `verify_kg.py`)

1. **Per-Paper Metadata Isolation**: Root `case.json` `doi`/`title`/`authors` belong **ONLY** to the primary paper. Never copy primary DOI/title/authors onto secondary or subsequent papers. Always rely on `corpus_index.json` per-document records.
2. **Zero Fake PDF Page Numbers**: If `has_pdf_pages == false` (e.g., JATS paragraph ID packages), **NEVER** output `locator_type: "pdf_page"`, `"page": 1`, or claim synthetic page 1 as an original PDF page. Always use the exact JATS `paragraph_id` (`locator_type: "jats_paragraph_id"`).
3. **Same-Paper Multi-Format Guard**: Multiple file formats (e.g., JSON + Markdown) of the **same paper** share one `paper_group_id` and **NEVER** count as cross-paper evidence.
4. **Verbatim Quote Grounding**: Every substantive node mention and every edge/alignment/conflict evidence item MUST bind `(source_id, paragraph_id, quote)` where `quote` is a real verbatim excerpt from that exact paragraph. Never fabricate entities from world knowledge.
5. **Two-Tier Alignment & Do-Not-Merge Discipline**:
   - Merge via `verdict: "merge_alias"` ONLY when two mentions clearly denote the exact same canonical concept, standard metric, standard reaction/task, or researcher.
   - Sharing a generic method/material/task/sample term across papers does **NOT** mean both papers used the same physical sample, model instance, dataset, or experimental condition. Keep distinct instances separate via `verdict: "keep_separate_distinct_instance"` (optionally set `shared_concept_name` to link both to an abstract `Concept` node via `INSTANTIATES_CONCEPT` without merging samples, conditions, or numeric results!).
   - Keep homonyms separate via `verdict: "keep_separate_homonym"` and ambiguous pairs separate via `verdict: "uncertain_no_merge"`.
6. **Epistemic & Condition Precision**: Distinguish `author_direct_result`, `author_method_statement`, `cited_prior_work`, and `speculation_or_hypothesis`. Never assign `evidence_strength: "strong_direct"` to `cited_prior_work` or `speculation_or_hypothesis`. Conditional relations (`OUTPERFORMS`, `SUPPORTS`, `CONTRADICTS`, `COMPARES`, `REPORTS_METRIC`) MUST specify explicit `conditions` (sample/dataset, setup, baseline, units). Never blend two papers' numeric results into one non-comparative edge.
7. **Honest Boundaries & Publisher XML Verification**: Reconcile linearized inline formulas/subscripts/superscripts against `xml_math_reconciliation.json` (when `.xml` exists). Never infer values from unavailable figure pixels, supplementary attachments, or raw clinical/experimental data; record missing modalities in `uncertainty` and `limitations`.

---

## Execution Workflow (Target: 10–16 Model Requests within 64-Request / 900s Budget)

### Step 1: Inspect Corpus, Isolate Metadata, & Check XML Formulas

Run `inspect_corpus.py` first before reading raw input JSON/XML files:

```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
mkdir -p /app/work /app/output
python3 "$SKILL_DIR/scripts/inspect_corpus.py" \
  --input-dir /input \
  --out-dir /app/work/inspect
```

Read `/app/work/inspect/case_brief.json` and the per-paper digests `/app/work/inspect/digest_<source_id>.md` (or `/app/work/inspect/corpus_digest.md`).
- Confirm `workflow_mode` (`two_paper_incremental` | `multi_paper_incremental` | `single_paper` | `degraded_missing_inputs`).
- Note `canonical_source_ids`, `has_pdf_pages`, `required_output_contract_fields`, `case_questions`, `duplicate_format_groups`, `declared_input_boundaries`, and any XML formula/subscript reconciliations embedded right below linearized paragraphs in `digest_<source_id>.md`.

### Step 2: Extract Primary Paper → Build Stage-1 KG (`kg_v1.json`) + Early Checkpoint

Read `digest_<primary_source_id>.md` and write `/app/work/primary_extraction.json` using controlled types from `schema/kg_schema.json`:
- **Entity types**: `Researcher`, `Material`, `Method`, `Task`, `Metric`, `Conclusion`, `Dataset_Or_Sample`
- **Relation types**: `USES`, `EVALUATED_ON`, `MEASURES`, `REPORTS_METRIC`, `COMPARES`, `OUTPERFORMS`, `SUPPORTS`, `CONTRADICTS`, `DERIVES_FROM`, `PART_OF`, `APPLIED_TO`, `ASSOCIATED_WITH`, `CITES_PRIOR_CLAIM`
- **Attribution**: `author_direct_result` | `author_method_statement` | `cited_prior_work` | `speculation_or_hypothesis`
- **Evidence strength**: `strong_direct` | `moderate_indirect` | `cited_secondary` | `weak_speculative`

Minimal schema for `/app/work/primary_extraction.json`:

```json
{
  "source_id": "<primary_source_id>",
  "entities": [
    {
      "temp_id": "P1_E1",
      "canonical_name": "<grounded entity name>",
      "entity_type": "Material",
      "aliases": ["<exact alias or abbreviation from text>"],
      "attributes": {"<key>": "<grounded value with unit>"},
      "mentions": [
        {
          "source_id": "<primary_source_id>",
          "paragraph_id": "<exact paragraph_id from digest>",
          "section": "<section name>",
          "surface_form": "<exact surface text>",
          "quote": "<verbatim excerpt copied directly from that paragraph>",
          "attribution": "author_direct_result"
        }
      ]
    }
  ],
  "relations": [
    {
      "source": "P1_E1",
      "target": "P1_E2",
      "relation_type": "REPORTS_METRIC",
      "attribution": "author_direct_result",
      "evidence_strength": "strong_direct",
      "conditions": "<sample/dataset, setup, baseline, exact value + unit>",
      "evidence": [
        {
          "source_id": "<primary_source_id>",
          "paragraph_id": "<exact paragraph_id>",
          "quote": "<verbatim excerpt supporting this directed relation>"
        }
      ]
    }
  ],
  "conflicts": [],
  "uncertainty": []
}
```

Build `kg_v1.json` (locks stable IDs `E001..`, `R001..`):

```bash
python3 "$SKILL_DIR/scripts/build_and_update_kg.py" \
  --mode init \
  --corpus-index /app/work/inspect/corpus_index.json \
  --extraction /app/work/primary_extraction.json \
  --out /app/work/kg_v1.json \
  --log-out /app/work/v1_init_log.json
```

Verify Stage-1 grounding immediately (fix any `quote_not_grounded_in_paragraph` error right away):

```bash
python3 "$SKILL_DIR/scripts/verify_kg.py" \
  --kg /app/work/kg_v1.json \
  --corpus-index /app/work/inspect/corpus_index.json \
  --xml-recon /app/work/inspect/xml_math_reconciliation.json \
  --out /app/work/v1_verify.json
```

> **Early Partial Checkpoint (recommended on multi-paper/long corpora)**: If the corpus is large, run `query_kg.py` + `export_and_assemble.py --partial-checkpoint` on `kg_v1.json` so `/app/output/result.json` already exists as an honest partial deliverable before moving to Paper 2.

*(If `workflow_mode == "single_paper"`, skip Steps 3–4 and proceed directly from `kg_v1.json` to Step 5.)*

### Step 3: Extract Secondary Paper & Detect Alignment / Non-Merge Candidates

Read `digest_<secondary_source_id>.md` (noting every `XML Formula/Subscript Check` line and XML table if `secondary-publisher.xml` is present) and write `/app/work/secondary_extraction.json` in the exact same format (using `source_id: "<secondary_source_id>"` and `temp_id: "P2_E1"`, `"P2_E2"`, ...).
- Include paper qualifiers in `canonical_name` when an entity represents a paper-specific physical sample, cohort, model instance, or conclusion (e.g., `"<SampleName> (<secondary_source_id>)"`) to keep instances visually and logically distinct.

Run the deterministic collision & candidate detector:

```bash
python3 "$SKILL_DIR/scripts/detect_alignment_candidates.py" \
  --base-kg /app/work/kg_v1.json \
  --new-extraction /app/work/secondary_extraction.json \
  --out /app/work/alignment_candidates.json
```

### Step 4: Author Alignment Decisions & Cross-Paper Relations → Incremental Update (`kg_v2.json`)

Inspect `/app/work/alignment_candidates.json` (especially every candidate with `risk_flags`). Write:
1. `/app/work/alignment_decisions.json`:

```json
{
  "decisions": [
    {
      "decision_id": "A001",
      "candidate_a": {"node_id_or_name": "E002", "source_id": "<primary_sid>"},
      "candidate_b": {"node_id_or_name": "P2_E2", "source_id": "<secondary_sid>"},
      "verdict": "merge_alias",
      "canonical_target_id": "E002",
      "shared_concept_name": null,
      "rationale": "<explicit textual/scientific justification>",
      "evidence": [
        {"source_id": "<primary_sid>", "paragraph_id": "<pid1>", "quote": "<verbatim quote 1>"},
        {"source_id": "<secondary_sid>", "paragraph_id": "<pid2>", "quote": "<verbatim quote 2>"}
      ]
    },
    {
      "decision_id": "A002",
      "candidate_a": {"node_id_or_name": "E001", "source_id": "<primary_sid>"},
      "candidate_b": {"node_id_or_name": "P2_E1", "source_id": "<secondary_sid>"},
      "verdict": "keep_separate_distinct_instance",
      "canonical_target_id": null,
      "shared_concept_name": "<abstract shared Concept name>",
      "rationale": "DO NOT MERGE: distinct physical samples/datasets/experimental conditions across papers.",
      "evidence": [
        {"source_id": "<primary_sid>", "paragraph_id": "<pid1>", "quote": "<verbatim quote 1>"},
        {"source_id": "<secondary_sid>", "paragraph_id": "<pid2>", "quote": "<verbatim quote 2>"}
      ]
    }
  ]
}
```

2. `/app/work/cross_paper_relations.json`: explicit cross-paper `COMPARES`, `SUPPORTS`, `CONTRADICTS`, or `DERIVES_FROM` relations (always with dual-paper `evidence[]` and non-empty `conditions` explaining condition/sample differences) plus any `conflicts[]` records (both cross-paper and intra-paper conflicts!).

Apply the **ID-preserving incremental update**:

```bash
python3 "$SKILL_DIR/scripts/build_and_update_kg.py" \
  --mode update \
  --corpus-index /app/work/inspect/corpus_index.json \
  --base-kg /app/work/kg_v1.json \
  --extraction /app/work/secondary_extraction.json \
  --alignment /app/work/alignment_decisions.json \
  --cross-relations /app/work/cross_paper_relations.json \
  --out /app/work/kg_v2.json \
  --log-out /app/work/incremental_update_log.json
```

*(If there are 3+ canonical papers in `corpus_index.json`, repeat Steps 3–4 sequentially: `kg_v2.json -> kg_v3.json`, preserving all prior IDs at each step.)*

### Step 5: Execute Real Graph Queries (≥ 3–5 Grounded Queries)

Write `/app/work/query_specs.json` incorporating any explicit questions in `case_brief.json` (`case_questions`) PLUS at least:
1. A `cross_paper_comparison` query comparing both papers on a shared `Concept` or entity while keeping per-paper samples/conditions/metrics separate;
2. A `multi_hop_chain` query tracing `Material`/`Method` → `Metric`/`Conclusion` with direct author provenance;
3. A `conflict_and_boundary_audit` query surfacing conflicts, non-merge decisions, and cited/speculative boundaries;
4. A `gap_or_unanswerable_check` query proving honest `"unable_to_determine"` handling for untested head-to-head comparisons or unavailable figure pixels/supplementary/raw data.

*(See `schema/query_templates.json` for exact operator parameters.)*

Execute the queries directly against the final graph:

```bash
python3 "$SKILL_DIR/scripts/query_kg.py" \
  --kg /app/work/kg_v2.json \
  --query-specs /app/work/query_specs.json \
  --auto-fill-min 3 \
  --out /app/work/kg_queries.json
```

### Step 6: Mechanical Verification Gate (Must Reach `error_count == 0`)

Run full verification checking quote grounding, metadata isolation, fake PDF page ban, same-paper format guard, epistemic strength & condition rules, alignment candidate coverage, v1 ID preservation, and query subgraph validity:

```bash
python3 "$SKILL_DIR/scripts/verify_kg.py" \
  --kg /app/work/kg_v2.json \
  --corpus-index /app/work/inspect/corpus_index.json \
  --xml-recon /app/work/inspect/xml_math_reconciliation.json \
  --prev-kg /app/work/kg_v1.json \
  --candidates /app/work/alignment_candidates.json \
  --queries /app/work/kg_queries.json \
  --out /app/work/kg_verification_report.json
```

- If exit code is non-zero (`error_count > 0`), read the exact `location`, `code`, and `hint` from `kg_verification_report.json`, fix the extraction/alignment/query JSON, rebuild, and re-run `verify_kg.py` until `error_count == 0`!

### Step 7: Export Multi-Format Graph, Visual Previews, & Contract-Aligned `result.json`

```bash
python3 "$SKILL_DIR/scripts/export_and_assemble.py" \
  --case /input/case.json \
  --kg /app/work/kg_v2.json \
  --queries /app/work/kg_queries.json \
  --verification /app/work/kg_verification_report.json \
  --xml-recon /app/work/inspect/xml_math_reconciliation.json \
  --out-dir /app/output
```

Confirm `/app/output/result.json` contains `case_id`, honest `status`, all `output_contract` fields, `sources`, `uncertainty`, `change_records`, and that `kg.json`, `kg.graphml`, `kg_queries.json`, `kg_verification_report.json`, `kg_report.md`, and visual previews (`kg_preview.svg` + `kg_preview.png`/`kg_preview.pdf`) are present.

---

## Exception & Edge-Case Handling

| Condition | Required Action |
|---|---|
| Missing input files or empty corpus | Record missing paths in `uncertainty` and `warnings`; produce honest `/app/output/result.json` with `status: "unable_to_determine"` (or `"partial"` if at least one paper is readable). Never fabricate text. |
| Encrypted / scanned / unreadable PDF | `inspect_corpus.py` logs `empty_or_scanned_pages` or encryption warning. Never guess unseen pages; mark `status: "partial"` and note unreadable pages in `uncertainty`. |
| Same paper supplied in multiple formats (e.g., JSON + MD) | `inspect_corpus.py` assigns the same `paper_group_id`. Extract only from the canonical source; NEVER claim cross-paper alignment or cross-paper edges within one `paper_group_id`. |
| Linearized formula / subscript ambiguity | Cross-check `xml_math_reconciliation.json` (`secondary-publisher.xml` or any `.xml`). Record exact XML-verified notation in `xml_verified_formula` and document any JSON-vs-XML discrepancy in `conflicts[]`. |
| Ambiguous cross-paper entity identity | Default safe policy is **DO NOT MERGE**: set `verdict: "uncertain_no_merge"` (or `"keep_separate_distinct_instance"` / `"keep_separate_homonym"`), log rationale, and record in `uncertainty`. |
| Query has no supported evidence in graph | Return `status: "unable_to_determine"` with explicit `uncertain_or_unanswerable_relations` explaining what evidence or modality is missing. |
| Remaining unresolvable verification error at budget limit | Save honest `result.json` with `status: "partial"` and full `verification_report` issues showing exact manual review locations. |
