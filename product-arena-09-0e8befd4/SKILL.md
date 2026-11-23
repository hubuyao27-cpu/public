---
name: nanopublication-extractor
description: >
  Verifiable, atomic, provenance-bound scientific Nanopublication extraction, assertion audit,
  evidence binding, and grounded Q&A Skill. Use when decomposing scientific paper conclusions
  into single-truth-value assertions with preserved conditions and polarity, aligning minimal
  verbatim evidence spans with authentic page/paragraph/sentence/table locators, isolating
  authentic per-paper bibliographic metadata, running 10 deterministic quality gates (atomicity,
  verbatim grounding, passive-voice direction, numeric/negation fidelity, epistemic separation,
  conflict detection, corroboration deduplication), and exporting valid W3C JSON-LD, RDF TriG,
  CSV, Markdown reports, and Harbor-compliant result.json.
---

# Verifiable Scientific Nanopublication Extractor (`nanopublication-extractor`)

Transform scientific papers into **atomic, verbatim-grounded, provenance-traceable, machine-readable Nanopublications** while eliminating compound claims, paraphrased or bloated evidence, fake PDF page numbers, passive-voice subject/object inversions, hallucinated numbers, and cited-work misattribution.

## Four Core Guarantees

1. **拆得细 (Atomic Claims)**: Split compound, parallel, contrastive, and multi-metric sentences into single-truth-value propositions (`<= 45` words / `<= 90` CJK chars), preserving conditions, units, comparators, polarity (`positive` / `negative` / `null_result` / `conditional`), and epistemic status (`author_direct_finding` / `author_method_claim` / `cited_prior_work` / `hypothesis_or_speculation` / `limitation`).
2. **对得上 (Minimal Verbatim Evidence)**: Bind each assertion to its **smallest sufficient verbatim excerpt** (`<= 2` sentences / `<= 450` chars unless justified), verified by character-offset matching (`char_start..char_end`) against authentic `paragraph_id`, `sentence_id`, `page` (only when real PDF), `table_id`, or `figure_id`.
3. **追得回 (Authentic Provenance Chain)**: Isolate authentic bibliographic metadata (`title`, `authors`, `year`, `venue`, `version`, `doi`, `pmid`, `arxiv_id`, `source_file`, `file_sha256`) per paper without copying primary metadata onto secondary papers; stamp content-addressed Trusty-style IDs (`urn:nanopub:RA...`); record intra/cross-paper conflicts and uncertainties.
4. **读得懂 (Machine-Readable Export)**: Serialize to validated 4-graph W3C **RDF 1.1 TriG** (`nanopubs.trig`), **JSON-LD 1.1** (`nanopubs.jsonld`), canonical **JSON** (`nanopubs.json`), **CSV** (`nanopubs.csv`), **Markdown audit report** (`nanopub_report.md`), and Harbor **`result.json`** covering every `output_contract` field.

---

## Seven Hard Guardrails (Mechanically Enforced by `verify_nanopubs.py`)

1. **Zero Compound Bundling**: Never dump a paragraph or abstract into one `claim_text`. Split multi-effect sentences into separate Nanopublications.
2. **Exact Verbatim Quotes Only**: Every `evidence_spans[].quote` MUST match verbatim inside `source_digest.md`. Never paraphrase quotes.
3. **Zero Fake PDF Page Numbers**: If `has_pdf_pages == false` (JSON/XML/Markdown/TXT), **NEVER** set `locator.page` or `locator_type: "pdf_page"`. Always use exact `paragraph_id` / `sentence_id` / `line_range`.
4. **Active-Voice Direction Check**: In passive sentences (`"B was inhibited by A"`), normalize to `subject: A`, `predicate: inhibits`, `object: B`. Never invert cause and effect.
5. **Strict Numeric & Negation Fidelity**: Every number in `claim_text` and `quantitative_values` must appear in the bound quote; negative or null findings (`"no significant effect"`, `"p = 0.42"`) must use `polarity: "negative"` or `"null_result"`.
6. **Epistemic Separation**: Never label `cited_prior_work` (`[1]`, `"previous studies"`) or speculative statements (`"may"`, `"might"`) as `author_direct_finding` + `direct_empirical`.
7. **Honest Boundaries**: Never fabricate missing DOIs, missing files, scanned PDF text without OCR, or unreported Q&A answers. Use `status: "unable_to_determine"` or `"partial"` and list gaps in `uncertainty` / `manual_takeover_points`.

---

## Runtime Paths & Budget Discipline (64 Requests / 900s)

- **Inputs**: `/input/case.json`, `/input/task.md`, optional `corpus-manifest.json`, and source files (`.pdf`, `.xml`, `.nxml`, `.json`, `.md`, `.txt`, `.html`, `.csv`).
- **Skill Directory**: Resolve `SKILL_DIR`:
  ```bash
  SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
  [ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
  mkdir -p /app/work /app/output
  ```
- **Target Budget**: Complete extraction, verification, and export in **6–12 model requests**. Read `/app/work/ingest/case_brief.json` and `/app/work/ingest/source_digest.md` instead of raw full PDF/XML dumps. On long papers, save an early partial checkpoint with `export_nanopubs.py --partial`.

---

## 5-Step Execution Workflow

### Step 1: Ingest Sources & Inspect Case Contract

```bash
python3 "$SKILL_DIR/scripts/ingest_source.py" \
  --input-dir /input \
  --out-dir /app/work/ingest
```
*(If `ingestion` reports `unreadable_or_scanned_pages` on a PDF, re-run once with `--ocr-auto`.)*

Read `/app/work/ingest/case_brief.json` and `/app/work/ingest/source_digest.md` (or per-source `digest_<source_id>.md`):
- Note `case_id`, `workflow_mode` (`extract_and_structure` | `verify_supplied_claims` | `qa_with_nanopubs` | `multi_paper_nanopubs` | `degraded_missing_or_unreadable`), `has_pdf_pages`, `output_contract_fields`, `questions`, `claims_to_verify`, and `warnings`.
- If `workflow_mode == "degraded_missing_or_unreadable"`: skip Step 2, write `[]` to `/app/work/raw_nanopubs.json`, run Steps 3–4 directly to produce an honest `status: "unable_to_determine"` `result.json`, and stop.

### Step 2: Author Atomic Nanopublications (`/app/work/raw_nanopubs.json`)

Consult `$SKILL_DIR/schema/predicate_vocab.json` (24 controlled predicates: `increases`, `decreases`, `causes`, `inhibits`, `activates`, `catalyzes`, `prevents`, `correlates_with`, `associated_with`, `interacts_with`, `part_of`, `composed_of`, `derived_from`, `outperforms`, `comparable_to`, `underperforms`, `exhibits_property`, `achieves_metric`, `uses_method`, `evaluated_on`, `applicable_to`, `requires_condition`, `supports`, `contradicts`) and write `/app/work/raw_nanopubs.json`:

```json
{
  "case_id": "<case_id from case_brief.json>",
  "nanopublications": [
    {
      "temp_id": "NP_001",
      "claim_text": "<single-truth-value atomic claim <= 45 words>",
      "subject": {"label": "<semantic subject>", "entity_type": "Material", "aliases": [], "identifier": null},
      "predicate": "achieves_metric",
      "object": {"label": "<semantic object / metric>", "entity_type": "Property_Or_Metric", "aliases": []},
      "conditions": {
        "experimental_system": "<medium / benchmark / model / null>",
        "parameters": ["<param1>", "<param2>"],
        "comparator": "<baseline or null>",
        "scope_qualifiers": ["<scope boundary>"]
      },
      "quantitative_values": [
        {"metric": "<name>", "operator": "=", "value": "<num>", "unit": "<unit>", "uncertainty_or_stat": "<± or p-value or null>"}
      ],
      "polarity": "positive",
      "epistemic_status": "author_direct_finding",
      "evidence_spans": [
        {
          "source_id": "<exact source_id from case_brief.json>",
          "quote": "<EXACT minimal verbatim excerpt copied from source_digest.md>",
          "evidence_type": "direct_empirical",
          "locator": {
            "locator_type": "<pdf_page|jats_paragraph_id|section_paragraph|line_range|table_cell|figure_caption>",
            "page": null,
            "section": "<section name>",
            "paragraph_id": "<exact paragraph_id from digest>",
            "sentence_id": "<exact S### from digest>"
          }
        }
      ]
    }
  ]
}
```

**Scenario-Specific Guidance**:
- **Assertion Verification (`verify_supplied_claims`)**: For every supplied claim, either bind verified supporting evidence or mark `polarity`/`epistemic_status` and add explicit failure/conflict notes if the claim contradicts or is absent from the source.
- **Grounded Q&A (`qa_with_nanopubs`)**: Also write `/app/work/qa_answers.json`:
  ```json
  [
    {
      "question_id": "Q1",
      "question": "<exact question>",
      "answer_status": "answered",
      "answer": "<concise grounded answer>",
      "supporting_nanopub_ids": ["<nanopub_id or temp_id>"],
      "evidence_spans": [ /* same verbatim span format */ ],
      "uncertainty_note": null
    }
  ]
  ```
  If a question cannot be answered from the source materials, set `"answer_status": "unreported_in_source"`, leave `"supporting_nanopub_ids": []`, and explain what was checked in `"uncertainty_note"`.
- **Early Checkpoint (long papers)**: Run Steps 3–4 with `--partial` after finishing the main Results assertions, then add remaining sections and re-run without `--partial`.

### Step 3: Run 10-Gate Deterministic Audit (`verify_nanopubs.py`)

```bash
python3 "$SKILL_DIR/scripts/verify_nanopubs.py" \
  --input /app/work/raw_nanopubs.json \
  --source-index /app/work/ingest/source_index.json \
  --out /app/work/audited_nanopubs.json \
  --report /app/work/verification_report.json \
  --strict
```
If `--strict` exits non-zero, read `/app/work/verification_report.json`, fix every `ERROR` (`compound_clause_detected`, `quote_not_grounded_in_source`, `evidence_span_too_long`, `fake_pdf_page_number`, `likely_subject_object_inversion`, `ungrounded_numeric_value`, `negation_polarity_mismatch`, `cited_prior_work_mislabeled_direct_finding`), and re-run until `error_count == 0`.

*(Note: Duplicate assertions from Abstract + Results are automatically merged while preserving all unique `evidence_spans[]` and assigning Trusty `urn:nanopub:RA...` IDs.)*

### Step 4: Export All Formats & Validate Contract (`export_nanopubs.py`)

If `/input/case.json` defines custom `output_contract` fields beyond Nanopubs/Q&A/conflicts/sources/uncertainty/limitations/change_records, place explicit overrides in `/app/work/extra_fields.json` and pass `--extra-fields`. If `qa_answers.json` used `temp_id`s before Step 3, map `supporting_nanopub_ids` using `pubinfo.merged_from_temp_ids` from `audited_nanopubs.json`.

```bash
python3 "$SKILL_DIR/scripts/export_nanopubs.py" \
  --audited /app/work/audited_nanopubs.json \
  --source-index /app/work/ingest/source_index.json \
  --case-brief /app/work/ingest/case_brief.json \
  --verify-report /app/work/verification_report.json \
  --out-dir /app/output \
  --strict
```
*(Pass `--qa /app/work/qa_answers.json` when questions exist; pass `--extra-fields /app/work/extra_fields.json` if needed; pass `--partial` for early checkpoints.)*

### Step 5: Verify Deliverables Before Finishing

Confirm `/app/output/result.json`, `nanopubs.json`, `nanopubs.jsonld`, `nanopubs.trig`, `nanopubs.csv`, `nanopub_report.md`, and `nanopub_verification_report.json` exist, `trig_roundtrip_valid == true`, `contract_fields_covered` matches `case_brief.json`, and `status` honestly reflects any unreadable pages or unreported items.
