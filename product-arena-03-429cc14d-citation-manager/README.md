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

Run the streamlined 2-command wrapper pipeline against any local case directory `--input-dir` (see [`examples/minimal_example.md`](examples/minimal_example.md) for a walkthrough; all individual stage scripts remain callable for debugging):

```bash
export SKILL_DIR=/path/to/citation-reference-manager

# Step 1: Inspect, parse manuscript & library, dedup, pre-audit, emit skeleton + early partial checkpoint
python3 -B "$SKILL_DIR/scripts/prepare_workspace.py" \
  --input-dir ./case_in --work-dir ./work --out-dir ./output

# Step 2: Review ./work/library/library_digest.md, ./work/manuscript/manuscript_claims_digest.md,
#         ./work/verify/verification_worklist.md and author ./work/citation_plan.json

# Step 3: Deterministic sync/renumbering, deliverable assembly, 10-invariant verification, final result.json
python3 -B "$SKILL_DIR/scripts/finalize_workspace.py" \
  --work-dir ./work --out-dir ./output --plan ./work/citation_plan.json
```

---

## 6. Design Trade-Offs & Alternatives Considered

- **Symbolic `[[CITE:REF_XXX]]` Tokenization vs. Direct LLM Renumbering**: Asking an LLM to hand-renumber citations across prose, captions, tables, and footnotes regularly drifts on insertions/deletions/reordering. Separating semantic placement (Agent emits stable `[[CITE:REF_XXX]]` tokens) from deterministic formatting/numbering (`sync_and_format.py`) eliminates off-by-one and bidirectional mismatch bugs.
- **Zero-Dependency Pure-Python Engine vs. External `citeproc-py` / `pybtex` / JabRef**: Harbor sandboxes vary in preinstalled packages. Borrowing fault-tolerant BibTeX brace-state & LaTeX-accent stripping patterns from high-star ecosystems (`JabRef/jabref`, `sciunto-org/python-bibtexparser`) in pure Python stdlib guarantees portability while keeping runtime under a few seconds.
- **3-Tier Guarded Union-Find Deduplication vs. Aggressive Fuzzy Merging**: Transitive merges can accidentally collapse distinct works by the same author. Two-phase guarded Union-Find explicitly checks every merge against `protected_non_duplicate` (distinct DOIs, disjoint pages) and `recycled_doi_conflict` sets, routing borderline cases to explicit Agent/human review (`AMB_XXX`).

---

## 7. Goals, Non-Goals & Known Limits

- **Goals**: Deterministic 1-to-1 citation sync, explainable multi-format library dedup without false merges, 8 major citation styles + lossless BibTeX/RIS/CSL-JSON/CSV exports, and 10-invariant mechanical verification.
- **Non-Goals**: Live online Crossref/OpenAlex/CNKI API scraping or automatic fabrication of missing bibliographic fields.
- **Offline DOI Lookup**: In network-isolated Harbor runs, DOIs are syntax-validated (`^10\.\d{4,9}/...`) and cross-checked against local evidence PDFs only, honestly tagged `not_checked_offline`.
- **Scanned PDFs**: Image-only PDFs automatically attempt bounded local `tesseract` OCR (when `tesseract` + `pdftoppm`/`pymupdf` are present, capped at 6 pages / 20s); otherwise flagged transparently in `uncertainty`.
- **Security**: All optional `.tex` preview subprocesses run with `-no-shell-escape` and hard timeouts.
- **Version**: `1.1.0` (2026-11-25). Designed for Harbor (`product-arena-03-429cc14d`) and general academic writing workflows.
