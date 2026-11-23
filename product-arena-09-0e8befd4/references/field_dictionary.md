# Field Dictionary & Ontology Mapping (`nanopub-skill/1.0.0`)

This dictionary defines every field in the canonical 4-graph **Nanopublication** structure (`nanopubs.json`, `nanopubs.jsonld`, `nanopubs.trig`, `nanopubs.csv`) and the Harbor `result.json` output wrapper.

---

## 1. Nanopublication 4-Graph Envelope

| Graph / Field | Type | Required | Ontology Mapping | Definition & Constraints |
|---|---|---|---|---|
| `nanopub_id` | string | Yes | `npext:nanopubId` | Content-addressed Trusty-style URN (`urn:nanopub:RA<43-char-base64url>`), SHA-256 over canonical `(assertion, provenance_core)`. |
| `head.nanopub_uri` | IRI | Yes | `np:Nanopublication` | Base HTTP/HTTPS IRI (`https://w3id.org/np/arena/RA...`). |
| `head.assertion_graph` | IRI | Yes | `np:hasAssertion` | Named graph IRI (`<nanopub_uri>#assertion`). |
| `head.provenance_graph` | IRI | Yes | `np:hasProvenance` | Named graph IRI (`<nanopub_uri>#provenance`). |
| `head.pubinfo_graph` | IRI | Yes | `np:hasPublicationInfo` | Named graph IRI (`<nanopub_uri>#pubinfo`). |

---

## 2. `assertion` Graph (Atomic Claim & Conditions)

| Field | Type | Required | Constraints / Controlled Values |
|---|---|---|---|
| `claim_text` | string | Yes | Single verifiable proposition; <= 45 English words (<= 90 CJK chars); no compound multi-claim clauses. |
| `subject.label` | string | Yes | Canonical name of the primary semantic subject (active agent / material / method / condition). |
| `subject.entity_type` | enum | Yes | `Material`, `Compound`, `Method`, `Model`, `Device`, `Process`, `Gene_Or_Protein`, `Organism_Or_Cell`, `Disease_Or_Phenotype`, `Dataset_Or_Cohort`, `Property_Or_Metric`, `Parameter_Or_Condition`, `Theory_Or_Claim`, `Study`. |
| `subject.aliases` | string[] | No | Exact aliases, abbreviations, or surface forms occurring in the paper. |
| `subject.identifier` | string\|null | No | Optional external ontology/registry IRI or ID (e.g., ChEBI, UniProt, DOID) **only if verified in source**. |
| `predicate` | enum | Yes | Must be one of the 24 controlled predicates in `schema/predicate_vocab.json` (mapped to OBO RO, BFO, SIO, CiTO, EDAM). |
| `predicate_uri` | IRI | Auto | Automatically populated from `schema/predicate_vocab.json`. |
| `object` | object | Yes | Same schema as `subject`. |
| `conditions.experimental_system` | string\|null | Yes | Medium, cell line, benchmark, organism, or reactor system (e.g., `"1.0 M KOH"`, `"HEK293T cells"`). |
| `conditions.parameters` | string[] | Yes | Temperature, dosage, concentration, sample size, metric target, etc. |
| `conditions.comparator` | string\|null | Yes | Control group or baseline comparator when comparative (`outperforms`, `increases`, etc.). |
| `conditions.scope_qualifiers` | string[] | Yes | Applicability boundaries, exclusions, or statistical notes. |
| `quantitative_values` | object[] | Yes | Each item: `{metric, operator, value, unit, uncertainty_or_stat}`. Every numeric token must appear in bound `evidence_spans`. |
| `polarity` | enum | Yes | `positive` \| `negative` \| `null_result` \| `conditional`. |
| `epistemic_status` | enum | Yes | `author_direct_finding` \| `author_method_claim` \| `cited_prior_work` \| `hypothesis_or_speculation` \| `limitation`. |

---

## 3. `provenance` Graph (Evidence Alignment & Source Metadata)

| Field | Type | Required | Constraints / Ontology Mapping |
|---|---|---|---|
| `evidence_spans` | array | Yes (min 1) | Minimal sufficient verbatim fragments (`prov:Entity`) backing the assertion. |
| `evidence_spans[].source_id` | string | Yes | Must match a `source_id` in `ingest/source_index.json`. |
| `evidence_spans[].quote` | string | Yes | Exact verbatim excerpt copied from the source; verified via `find_verbatim_span()`. Default <= 2 sentences / 450 chars unless `multi_sentence_justification` is supplied. |
| `evidence_spans[].evidence_type` | enum | Yes | `direct_empirical` \| `direct_analytical` \| `table_or_caption_direct` \| `indirect_inference` \| `cited_prior_work` \| `method_specification`. |
| `evidence_spans[].locator.locator_type` | enum | Yes | `pdf_page` \| `jats_paragraph_id` \| `section_paragraph` \| `line_range` \| `table_cell` \| `figure_caption` \| `equation_block`. |
| `evidence_spans[].locator.page` | int\|null | Cond. | **MUST be `null` whenever `has_pdf_pages == false`** (synthetic PDF page numbers are strictly forbidden). |
| `evidence_spans[].locator.paragraph_id` | string\|null | Rec. | Exact paragraph/block ID from `source_digest.md`. |
| `evidence_spans[].locator.sentence_id` | string\|null | Auto | Exact `S001..SNNN` sentence ID (auto-populated during verification when matched). |
| `evidence_spans[].locator.char_start` / `char_end` | int\|null | Auto | Exact 0-based character offsets within the target paragraph (auto-populated during verification). |
| `source_metadata` | object | Yes | Reconciled directly from `source_index.json`: `source_id`, `title`, `authors`, `year`, `venue`, `version`, `doi`, `pmid`, `arxiv_id`, `url`, `source_file`, `file_sha256`. |
| `confidence.level` | enum | Auto | `high` (>= 0.80) \| `medium` (>= 0.55) \| `low` (>= 0.30) \| `unverified` (< 0.30). |
| `confidence.score` | number | Auto | Deterministic weighted rubric in `[0.0, 1.0]`. |
| `confidence.rationale` | string | Auto | Human-readable breakdown of rubric factors (`directness`, `quote_match`, `conditions`, `numeric_fidelity`, `corroboration_locs`). |

---

## 4. `pubinfo` Graph & Harbor `result.json`

| Field | Type | Required | Description |
|---|---|---|---|
| `pubinfo.dedup_fingerprint` | string | Auto | 24-hex SHA-256 over normalized `(subject, predicate, object, polarity, conditions)`. |
| `pubinfo.corroboration_count` | integer | Auto | Count of distinct `(source_id, paragraph_id)` locations backing this merged Nanopublication. |
| `pubinfo.merged_from_temp_ids` | string[] | Auto | Traceability list of original candidate IDs merged into this Nanopublication. |
| `result.json -> case_id` | string | Yes | Exact `case_id` copied from `/input/case.json`. |
| `result.json -> status` | enum | Yes | `success` \| `partial` \| `unable_to_determine` \| `refused`. |
| `result.json -> qa_answers` | array | Cond. | Required when case includes questions: `{question_id, question, answer_status, answer, supporting_nanopub_ids, evidence_spans, uncertainty_note}`. |
| `result.json -> conflicts` | array | Yes | Opposite-predicate, polarity, numeric, or cross-paper disagreements with condition differences. |
| `result.json -> uncertainty` | string[] | Yes | Unreported items, missing metadata, scanned/garbled pages, or unverified claims. |
| `result.json -> limitations` | string[] | Yes | Scope limits and modal boundaries (e.g., lack of PDF pagination, unreadable figures). |
| `result.json -> change_records` | object[] | Yes | Deterministic audit log of predicate normalizations, deduplication merges, and repairs. |
| `result.json -> manual_takeover_points` | string[] | Yes | Explicit handoff instructions when human inspection/OCR is required. |
