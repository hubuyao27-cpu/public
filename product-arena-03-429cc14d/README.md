# Academic Citation Manager (`academic-citation-manager`)

An auditable, deterministic citation engineering skill for AI agents (`product-arena-03-429cc14d`) covering **claim-level citation insertion, cross-zone sequential/author-year synchronization, explainable multi-source deduplication, multi-format conversion, and source authenticity auditing**.

## 1. Four Core Guarantees

1. **挂得对 (Claim-Level Placement)**: Inserts markers directly after the specific claim phrase or clause (`after_phrase` / `before_clause_punct`), avoiding sloppy paragraph-end citation dumps.
2. **编得顺 (Lock-Step Bidirectional Sync)**: Reads Abstract, Body, Figure/Table Captions, Tables, Footnotes, and Appendices in true reading order; renumbers `1..N` (or disambiguates `2021a`/`2021b`), sorts and compresses bracket ranges (`[2–5]`), and rebuilds the reference list so body and bibliography match 100%.
3. **收得净 (Explainable 4-Tier Deduplication)**: Ingests BibTeX (`.bib`), RIS (`.ris`), EndNote Tagged (`.enw`) & XML (`.xml`), CNKI NoteExpress/RefWorks/custom exports, CSL-JSON, CSV/TSV, and plain-text reference lists. Merges duplicates across preprints (`arXiv`), conference/journal editions, and bilingual records with full field provenance and an anti-false-merge guard protecting distinct same-author-same-year papers.
4. **对得上 (Authenticity & Claim-Match Verification)**: Anchors every reference to `library_snapshot.json`, refuses to fabricate missing DOIs/volumes/pages, checks in-text author/year consistency against library records, and enforces honest verdicts (`supported`, `partially_supported`, `mismatch_or_unsupported`, `insufficient_source_text`, `unresolved_ref`).

---

## 2. Repository Layout

```text
.
├── GITHUB_URL.txt
├── README.md
├── SKILL.md
├── examples/
│   ├── minimal_example.md
│   ├── sample_library.bib
│   ├── sample_library.ris
│   └── sample_manuscript.md
├── resources/
│   ├── citation_styles.json
│   ├── dedup_and_matching_rules.json
│   ├── edge_cases_and_recovery.md
│   └── payload_schema.json
└── scripts/
    ├── analyze_manuscript_citations.py
    ├── assemble_output.py
    ├── common.py
    ├── inspect_case.py
    ├── parse_and_dedup_library.py
    ├── self_check.py
    ├── sync_and_format.py
    └── verify_citations.py
```

---

## 3. Supported Formats & Output Styles

- **Input Library Formats**: BibTeX (`.bib`), RIS (`.ris`), EndNote Tagged (`.enw`) & XML (`.xml`), CNKI RefWorks / NoteExpress / custom export (`.txt`), CSL-JSON / JSON, CSV / TSV, and embedded manuscript reference sections.
- **Input Manuscript Formats**: Markdown (`.md`), LaTeX (`.tex`), Word (`.docx`), plain text (`.txt`), PDF (`.pdf`, text layer via `pymupdf`/`pypdf`/`pdftotext`).
- **Output Citation Styles**:
  - `gbt7714_numeric` (GB/T 7714—2015 顺序编码制)
  - `gbt7714_author_year` (GB/T 7714—2015 著者—出版年制)
  - `apa7` (APA 7th Edition)
  - `ieee` (IEEE Numeric)
  - `nature` (Nature Numeric)
  - `vancouver` (ICMJE / Vancouver Numeric)
  - `chicago_author_year` (Chicago Author-Date)
- **Machine-Readable Exports**: Synchronized `references_clean.bib` and `references_clean.ris` alongside formatted Markdown/DOCX/LaTeX outputs.

---

## 4. Dependencies

- **Core**: Python 3.9+ standard library only (`json`, `re`, `csv`, `difflib`, `xml.etree.ElementTree`, `zipfile`, `unicodedata`, `pathlib`, `argparse`).
- **Optional**: `python-docx` for `.docx` export; `pymupdf` / `pypdf` / `pdftotext` for PDF text reading; `soffice` or `xelatex` + `pdftoppm` for PDF/PNG visual previews.

---

## 5. Self-Check & Validation

Run the standalone self-check suite (temp directories only, zero side effects):
```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/self_check.py
```
Covers:
1. Multi-format library parsing (BibTeX + RIS + EndNote + CNKI + embedded list), arXiv preprint -> journal merge, and same-author-same-year anti-false-merge preservation.
2. Claim-level insertion, deletion, section reordering, figure-caption synchronization, and style conversion across `gbt7714_numeric`, `apa7`, and `ieee`.
3. Adversarial verification catching dangling/orphan citations, numbering gaps (`[1], [3]`), fabricated entries, and unflagged claim mismatches.
4. Missing/corrupt input & missing-metadata honest degradation (zero fabrication).

---

## 6. Known Limitations & Offline Policy

- **Offline DOI Verification**: By default in isolated Harbor containers without outbound internet, DOI syntax and library snapshot provenance are verified locally; live Crossref/OpenAlex queries are never fabricated and unresolved external checks are logged honestly under `uncertainty`.
- **Title-Only Entries**: When library exports omit abstracts/full text, claim-level verification defaults to `insufficient_source_text` with an explicit confirmation prompt rather than guessing paper conclusions.
