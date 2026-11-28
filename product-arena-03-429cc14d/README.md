# Academic Citation Manager (`academic-citation-manager`)

Harbor-compatible Agent Skill for **Bohrium AI Product Arena — 写作04｜参考文献与引用管理 (`product-arena-03-429cc14d`)**.

## Core Capabilities
- **挂得对 (Clause-Level Citation Placement)**: Splits paragraph-end citation dumps (`[1-4]`) and places `[[CITE:REF_XXX]]` markers at the exact supporting clause before punctuation, while preserving math (`$...$`, `$$...$$`), code blocks, and scientific units (`10<sup>3</sup>`).
- **编得顺 (Deterministic Cross-Zone Renumbering)**: Synchronizes sequential `1..K` numbering (or Author-Year sorting + `a/b/c` suffixes) across body text, figure/table captions, Markdown/DOCX tables, appendices, footnotes, and bibliography — including after section reordering (`section_order_override`).
- **收得净 (Explainable 3-Tier Deduplication)**: Parses `.bib`, `.ris`, `.enw`, EndNote `.xml`, CNKI `.txt`, `.csl.json`, `.csv`/`.tsv`, and embedded/inline reference lists across UTF-16/UTF-8/GB18030/GBK encodings; merges preprints and bilingual CN/EN duplicates with field-level provenance while blocking false merges of same-author distinct papers and flagging recycled DOIs.
- **对得上 (Source & Claim Verification)**: Indexes local evidence PDFs/texts and abstracts, catches dangling/fabricated citations and claim mismatches, verifies verbatim `evidence_quote` substrings (hyphenation-tolerant), and discloses offline DOI limits (`not_checked_offline`) honestly.
- **8 Publication Styles & 7 Export Artifacts**: `gbt7714_2015_numeric`, `gbt7714_2015_author_year`, `apa7`, `apa6`, `ieee`, `nature`, `vancouver`, `chicago_author_date` -> `revised_manuscript.md` (+ optional `.docx`/`.tex` with real LibreOffice/XeLaTeX previews), `references_formatted.md`, `references_clean.{bib,ris,csl.json}`, `references_table.csv`, `citation_audit_report.md`, `provenance_ledger.json`, and contract-compliant `result.json`.

---

## Architecture (`16` UTF-8 Text Files)

```text
submission/
├── SKILL.md                          # Agent workflow & runtime contract (<100 lines)
├── README.md                         # Documentation & verification guide
├── GITHUB_URL.txt                    # Public repository reference
├── resources/
│   ├── style_rules.json              # 8 citation style & doc-type specifications
│   ├── dedup_rules.json              # 3-tier dedup & anti-false-merge rules
│   ├── plan_schema.json              # citation_plan.json schema
│   └── edge_cases_and_recovery.md    # Offline, recycled DOI, encoding & error guide
├── scripts/
│   ├── common.py                     # Multi-encoding I/O, author/DOI/page/PDF/DOCX utils
│   ├── library_engine.py             # Multi-format parsers, 3-tier dedup, 8-style formatter
│   ├── prepare_case.py               # Step 1: inspect, parse, index evidence, partial checkpoint
│   ├── manuscript_and_sync.py        # Step 2/3 sync engine: lossless edits & numbering
│   ├── verify_and_assemble.py        # Step 3: 10-invariant audit, previews & result.json
│   └── self_test.py                  # 9-scenario offline verification suite
└── examples/
    ├── minimal_example.md            # Illustrative example (NOT formal test output)
    ├── sample_manuscript.md
    └── sample_library_fixtures.md    # Embedded BibTeX & RIS fixtures (.md for Harbor parity)
```

---

## Dependencies & Runtime Requirements
- **Required**: Python 3.9+ (pure standard library core; zero network access required).
- **Optional** (auto-detected with graceful fallback):
  - `pymupdf` (`fitz`) or `pdfplumber` or `pdftotext` for PDF evidence extraction.
  - `python-docx` for `.docx` input/output; `soffice` (LibreOffice) + `pdftoppm`/`pymupdf` for `.docx` PDF/PNG previews; `xelatex`/`pdflatex` for `.tex` PDF/PNG previews.

---

## Quick Verification

Run the bundled 8-scenario offline self-test suite:
```bash
python3 -B scripts/self_test.py
```

Run manually on an input directory:
```bash
python3 -B scripts/prepare_case.py --input-dir /input --work-dir /app/work --out-dir /app/output
# Write /app/work/citation_plan.json following /app/work/citation_plan_skeleton.json
python3 -B scripts/verify_and_assemble.py --work-dir /app/work --out-dir /app/output --plan /app/work/citation_plan.json
```

## Limitations & Failure Handling
- **Offline Sandbox**: External DOI resolution (`doi.org`, Crossref, OpenAlex) is never assumed or fabricated; DOIs passing syntax validation are marked `not_checked_offline`.
- **Corrupt/Encrypted/Empty Inputs**: Immediately produces a clean `status: "unable_to_determine"` `result.json` explaining exact causes rather than fabricating prose or references.
