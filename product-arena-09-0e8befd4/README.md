# Nanopublication Extractor Skill (`nanopublication-extractor`)

A verifiable, provenance-bound, pure-Python-stdlib Skill for decomposing scientific literature into atomic **Nanopublications** (`head`, `assertion`, `provenance`, `pubinfo`), auditing assertions and evidence spans through 10 deterministic quality gates, answering grounded reading questions, and exporting ready-to-parse W3C **RDF 1.1 TriG**, **JSON-LD 1.1**, **JSON**, **CSV**, **Markdown**, and Harbor **`result.json`**.

---

## Architecture Overview

```text
/input/* (PDF, JATS XML, JSON, Markdown, TXT, CSV, case.json, task.md)
   │
   ▼  scripts/ingest_source.py
/app/work/ingest/
   ├── case_brief.json       (case_id, workflow_mode, output_contract, questions, warnings)
   ├── source_index.json     (per-paper isolated metadata, SHA-256, paragraphs, sentences, tables)
   └── source_digest.md      (token-efficient digest with [S### | paragraph_id | p.#] anchors)
   │
   ▼  Agent writes /app/work/raw_nanopubs.json (+ optional qa_answers.json)
   │
   ▼  scripts/verify_nanopubs.py --strict  (10 Deterministic Quality Gates)
/app/work/
   ├── audited_nanopubs.json (4-graph Nanopubs, Trusty urn:nanopub:RA... IDs, merged corroboration)
   └── verification_report.json
   │
   ▼  scripts/export_nanopubs.py --strict  (with built-in TriG & JSON-LD validation)
/app/output/
   ├── result.json           (Harbor output_contract + status + uncertainty + change_records)
   ├── nanopubs.json         (canonical 4-graph JSON)
   ├── nanopubs.jsonld       (W3C JSON-LD 1.1)
   ├── nanopubs.trig         (W3C RDF 1.1 TriG, syntax-verified)
   ├── nanopubs.csv          (tabular export)
   ├── nanopub_report.md     (human-auditable report)
   └── nanopub_verification_report.json
```

---

## Repository Structure & File Inventory

| Path | Role |
|---|---|
| `SKILL.md` | Agent entrypoint instructions, 7 Hard Guardrails, and 5-step Harbor execution workflow. |
| `README.md` | Usage manual, architecture, installation/runtime requirements, edge-case guide, and self-test docs. |
| `GITHUB_URL.txt` | Public GitHub repository link for this Skill submission. |
| `schema/predicate_vocab.json` | 24 controlled scientific predicates mapped to OBO RO/BFO, SIO, CiTO, EDAM IRIs with passive markers & opposites. |
| `schema/nanopub_schema.json` | Draft 2020-12 JSON Schema for the 4-graph Nanopublication structure and Harbor `result.json`. |
| `schema/config_defaults.json` | Deterministic thresholds for atomicity, minimal span lengths, deduplication, OCR triggers, and confidence rubric. |
| `scripts/common.py` | Pure-stdlib normalization, Trusty ID hashing (`urn:nanopub:RA...`), verbatim span matcher, numeric & negation detectors. |
| `scripts/ingest_source.py` | Multi-format ingestion (PDF XY-Cut + header/footer filter, JATS XML sub/sup/tex-math, JSON, Markdown, CSV), per-paper metadata isolation, and sentence indexing. |
| `scripts/verify_nanopubs.py` | 10 deterministic verification gates: atomicity, verbatim minimal span, locator integrity, passive direction, numeric/negation, epistemic status, metadata authenticity, conflict detection, dedup merge, Trusty ID & confidence rubric. |
| `scripts/export_nanopubs.py` | Serializes JSON, JSON-LD, RDF TriG (with built-in TriG validator), CSV, Markdown report, and dynamic `result.json`. |
| `scripts/run_selftest.py` | Reusable zero-external-dependency self-test covering the 20-assertion synthetic benchmark + 8 adversarial negative gates. |
| `references/field_dictionary.md` | Field-by-field data dictionary and semantic web ontology mappings. |
| `references/extraction_rules.md` | Atomicity criteria, span minimality rules, epistemic classification, confidence rubric, and failure-mode matrix. |
| `examples/synthetic_paper.md` | Illustrative synthetic paper fixture (20+ testable claims, table, figure caption, cited prior work, null result). |
| `examples/benchmark_20_assertions.json` | Reusable 20-assertion synthetic benchmark + 3 Q&A items (including 1 unanswerable boundary item). |
| `examples/minimal_example.md` | Illustrative minimal CLI invocation and excerpted outputs. |

---

## Requirements & Installation

- **Core Runtime**: Python **3.10+** standard library only (`json`, `re`, `csv`, `hashlib`, `base64`, `xml.etree.ElementTree`, `unicodedata`, `pathlib`, `argparse`). Zero external Python packages are required for JSON, XML/JATS, Markdown, TXT, HTML, or CSV workflows.
- **Optional PDF/OCR Backends** (only invoked when `.pdf` files are present under `/input/`):
  - `pymupdf` (`fitz`), or `pdftotext` (Poppler) for PDF text-layer extraction.
  - `tesseract` CLI for `--ocr-auto` fallback on scanned image-only PDF pages.

---

## Independent Self-Test

Run the bundled self-test from anywhere with no external datasets or API keys:

```bash
python3 scripts/run_selftest.py
```

Expected output: `"selftest": "PASSED"`, `"adversarial_gates_passed": "8/8"`, `"quote_grounding_rate": 1.0`, `"trig_roundtrip_valid": true`.

---

## Applicable Scenarios, Limitations & Failure Handling

- **Applicable Scenarios**: Single- or multi-paper scientific reading, atomic claim decomposition, evidence span auditing, bibliographic provenance standardization, supplied-claim fact-checking, and grounded Q&A.
- **Limitations**:
  - Cannot read numeric values plotted solely inside raster figure images if those numbers do not appear in text, captions, or tables (flags `cross_figure_reference_check` and records uncertainty).
  - Scanned/image-only PDFs without a text layer require `pymupdf` + `tesseract`; otherwise pages are flagged in `unreadable_pages` and `manual_takeover_points`, and `status` degrades honestly to `partial` or `unable_to_determine`.
  - Predicate vocabulary covers 24 standard scientific relations; highly specialized domain predicates map to the closest canonical relation with full context preserved in `claim_text` and `conditions`.

---

## Maintenance & Versioning

- **Skill Name**: `nanopublication-extractor`
- **Version**: `1.0.0`
- **Target Challenge**: `product-arena-09-0e8befd4` (阅读14｜Nanopublication)
- **Public Repository**: `https://github.com/hubuyao27-cpu/public/tree/main/product-arena-09-0e8befd4`
