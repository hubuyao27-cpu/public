# Citation & Reference Manager (`citation-reference-manager`)

An evidence-grounded, Harbor-compliant Agent Skill for end-to-end academic **citation placement, deterministic renumbering, explainable library deduplication, multi-format style conversion, claim-to-source verification, and provenance-tracked output generation**.

---

## 1. Problems Addressed & Core Capabilities

| Pillar | Problem Solved | Mechanism in Skill |
|---|---|---|
| **挂得对** (Placement) | Citations dumped at paragraph ends (`[1-4]`) or missing at quantitative/method claims | `parse_manuscript.py` flags paragraph-end clusters & uncited attribution/numeric claims; Agent authors clause-level `[[CITE:REF_XXX]]` tokens in `citation_plan.json` |
| **编得顺** (Synchronization) | Broken numbers, gaps, caption/footnote desync after insert/delete/chapter reorder | `sync_and_format.py` computes deterministic first-appearance order (or author-year sort + `a/b` suffixes) across body, captions, tables, footnotes, appendices & bibliography |
| **收得净** (Deduplication) | Preprint vs. published, CN/EN bilingual pairs, cross-DB duplicates vs. false merges | `parse_and_dedup_library.py` uses 3-tier rules (`dedup_rules.json`), source precedence, anti-false-merge guards (distinct DOIs / disjoint pages), and recycled-DOI detection |
| **对得上** (Verification) | Ghost citations, metadata errors, claim-to-reference mismatch, undocumented edits | `verify_sources_pre.py` + `verify_deliverables.py` cross-check local PDFs/abstracts, verify quotes mechanically, preserve `field_provenance`, and enforce 10 invariants |

---

## 2. Supported Formats & Citation Styles

- **Input Libraries**: BibTeX (`.bib`, fault-tolerant brace state machine), RIS (`.ris`), EndNote Tagged (`.enw`), EndNote XML (`.xml`), CNKI RefWorks/NoteExpress & custom CNKI `.txt`, CSL-JSON / JSON / JSONL, CSV / TSV, numbered reference lists, and local evidence PDFs.
- **Input Manuscripts**: Markdown (`.md`), Word (`.docx` with superscript run preservation), LaTeX (`.tex`), plain text (`.txt`), HTML (`.html`), PDF (`.pdf`).
- **Target Citation Styles** (`resources/style_rules.json`):
  - `gbt7714_2015_numeric` (GB/T 7714—2015 顺序编码制, CN/EN mixed rules `等.` / `et al.`)
  - `gbt7714_2015_author_year` (GB/T 7714—2015 著者—出版年制)
  - `apa7`, `apa6`
  - `ieee`
  - `nature`
  - `vancouver`
  - `chicago_author_date`
- **Deliverables Emitted** (`/app/output`):
  - `result.json` (honoring `case_id` + every `output_contract` key + full provenance)
  - `revised_manuscript.md` (plus `.docx` + PDF/PNG previews when requested & LibreOffice available; `.tex` when LaTeX input/requested)
  - `references_formatted.md`, `references_clean.bib`, `references_clean.ris`, `references_clean.csl.json`, `references_table.csv`
  - `citation_audit_report.md`, `provenance_ledger.json`

---

## 3. Dependencies & Installation

- **Core Pipeline**: **Pure Python 3.10+ standard library** (zero mandatory external packages — runs reliably inside minimal Harbor containers).
- **Optional Enhancements** (gracefully detected at runtime):
  - `pymupdf` (`fitz`) or `pdftotext` CLI — PDF evidence text extraction & PDF-to-PNG preview rendering
  - `python-docx` — `.docx` editable export with true superscript formatting (reading `.docx` already works via stdlib XML fallback)
  - LibreOffice (`soffice`) — headless `.docx` → `.pdf` preview rendering
  - `xelatex` / `pdflatex` — `.tex` compilation preview

---

## 4. Directory Structure

```text
citation-reference-manager/
├── SKILL.md
├── README.md
├── GITHUB_URL.txt
├── resources/
│   ├── style_rules.json
│   ├── dedup_rules.json
│   ├── plan_schema.json
│   └── failure_modes_and_edge_cases.md
├── scripts/
│   ├── common.py
│   ├── inspect_case.py
│   ├── parse_manuscript.py
│   ├── parse_and_dedup_library.py
│   ├── verify_sources_pre.py
│   ├── prepare_workspace.py
│   ├── style_formatters.py
│   ├── sync_and_format.py
│   ├── assemble_deliverables.py
│   ├── verify_deliverables.py
│   └── finalize_workspace.py
└── examples/
    ├── minimal_example.md
    ├── sample_manuscript.md
    └── sample_refs.bib
```

---

## 5. Standalone Usage & Verification

Run the complete pipeline against any local case directory `--input-dir` (see [`examples/minimal_example.md`](examples/minimal_example.md) for a walkthrough):

```bash
export SKILL_DIR=/path/to/citation-reference-manager
python3 "$SKILL_DIR/scripts/inspect_case.py" --input-dir ./case_in --out ./work/case_plan.json
python3 "$SKILL_DIR/scripts/parse_manuscript.py" --case-plan ./work/case_plan.json --out-dir ./work/manuscript
python3 "$SKILL_DIR/scripts/parse_and_dedup_library.py" --case-plan ./work/case_plan.json --manuscript ./work/manuscript/manuscript_parsed.json --rules "$SKILL_DIR/resources/dedup_rules.json" --style-rules "$SKILL_DIR/resources/style_rules.json" --out-dir ./work/library
python3 "$SKILL_DIR/scripts/verify_sources_pre.py" --case-plan ./work/case_plan.json --library ./work/library/library_canonical.json --evidence ./work/library/evidence_fulltexts.json --manuscript ./work/manuscript/manuscript_parsed.json --out-dir ./work/verify
# Author ./work/citation_plan.json following resources/plan_schema.json, then:
python3 "$SKILL_DIR/scripts/sync_and_format.py" --case-plan ./work/case_plan.json --library ./work/library/library_canonical.json --manuscript ./work/manuscript/manuscript_parsed.json --pre-audit ./work/verify/pre_audit.json --plan ./work/citation_plan.json --style-rules "$SKILL_DIR/resources/style_rules.json" --out-dir ./work/synced
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" --case-plan ./work/case_plan.json --library ./work/library/library_canonical.json --manuscript ./work/manuscript/manuscript_parsed.json --pre-audit ./work/verify/pre_audit.json --plan ./work/citation_plan.json --synced ./work/synced/sync_result.json --checkpoint final --out-dir ./output
python3 "$SKILL_DIR/scripts/verify_deliverables.py" --case-plan ./work/case_plan.json --library ./work/library/library_canonical.json --evidence ./work/library/evidence_fulltexts.json --manuscript ./work/manuscript/manuscript_parsed.json --pre-audit ./work/verify/pre_audit.json --plan ./work/citation_plan.json --synced ./work/synced/sync_result.json --output-dir ./output --out ./work/verification_report.json
```

---

## 6. Known Limitations & Maintenance

- **Offline DOI Lookup**: In network-isolated Harbor runs, DOIs are syntax-validated (`^10\.\d{4,9}/...`) and cross-checked against local evidence PDFs only, honestly tagged `not_checked_offline`.
- **Scanned PDFs**: Image-only PDFs without an embedded text layer cannot be full-text verified without OCR; flagged transparently in `uncertainty`.
- **Version**: `1.0.0` (2026-11-23). Designed for Harbor (`product-arena-03-429cc14d`) and general academic writing workflows.
