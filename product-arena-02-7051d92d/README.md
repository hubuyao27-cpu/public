# Academic Language Polisher (`academic-language-polisher`)

A verifiable, minimal-edit, terminology-locked, and hedge-preserving academic language polishing skill designed for AI coding/writing agents and Harbor evaluation (`product-arena-02-7051d92d`).

## 1. Design Philosophy & Four Guarantees

Generic LLM paraphrasing frequently introduces silent scientific corruption: dropping epistemic hedges (*may indicate* -> *proves*), upgrading correlation to causation (*associated with* -> *causes*), altering units or significance bounds, breaking LaTeX/citation tokens, or letting terminology drift between Introduction and Methods.

This Skill replaces free-form rewriting with an auditable 6-step engineering pipeline enforcing four guarantees:

1. **改得对 (Grammatical & Syntactic Accuracy)**: Fixes subject-verb agreement, tense/voice mismatches, articles, prepositions, singular/plural forms, dangling modifiers, collocations, punctuation, and mixed CJK/ASCII spacing directly rather than masking errors via loose paraphrase.
2. **说得像 (Genre Register & Full-Text Terminology Consistency)**: Calibrates tone across `journal_article`, `thesis_dissertation`, `grant_proposal`, `reviewer_response`, `english_abstract`, and `bilingual_zh_en`; builds/locks a canonical glossary and enforces `Full Term (ACR)` first-occurrence expansion across every section.
3. **没改坏 (Semantic & Protected-Span Conservation)**: Mechanically verifies 100% conservation of numbers, SI units, math/LaTeX commands, citation markers, figure/table cross-references, negation/conditional/quantifier polarity, causal vs. correlational verbs, and 5-tier epistemic hedge strength.
4. **讲得清 (Sentence-Level Provenance & Track Changes)**: Maps every edit 1-to-1 to an immutable `sentence_id` (`P001.S01`) with `original`, `polished`, inline word diff, controlled `edit_types`, concrete `rationale`, and explicit `author_confirmation_items` whenever glossary conflicts or ambiguous meanings occur.

---

## 2. Repository Layout

```text
.
├── GITHUB_URL.txt
├── README.md
├── SKILL.md
├── examples/
│   ├── minimal_example.md
│   ├── sample_glossary.json
│   └── sample_manuscript.md
├── resources/
│   ├── chinglish_and_register_rules.json
│   ├── edge_cases_and_recovery.md
│   ├── genre_style_guides.json
│   ├── hedge_and_causality_lexicon.json
│   └── payload_schema.json
└── scripts/
    ├── analyze_manuscript.py
    ├── assemble_output.py
    ├── common.py
    ├── inspect_case.py
    ├── self_check.py
    └── verify_polish.py
```

---

## 3. Dependencies & Runtime Compatibility

- **Core Runtime**: Python 3.9+ using **standard library only** (`json`, `re`, `csv`, `difflib`, `zipfile`, `xml.etree.ElementTree`, `unicodedata`, `pathlib`, `argparse`).
- **Optional Format Libraries** (gracefully degraded when absent):
  - `python-docx` for `.docx` read/write (falls back to stdlib `zipfile` XML extraction on read).
  - `pymupdf` (`fitz`), `pypdf`, or `pdftotext` CLI for PDF text extraction.
  - `soffice` (LibreOffice) / `xelatex` + `pdftoppm` for rendering real PDF/PNG previews when `.docx` or `.tex` outputs are produced.

---

## 4. Usage & Workflow Summary

1. **Inspect Contract (`inspect_case.py`)**: Reads `/input/case.json`, `/input/task.md`, and discovers manuscripts, glossaries, style guides, genre, target language, edit intensity, and `output_contract` fields. Includes an explicit trigger guard (`is_non_polish_qa`) so pure content Q&A tasks do not rewrite text.
2. **Analyze & Segment (`analyze_manuscript.py`)**: Segments text into paragraphs and sentences (`P001.S01`), extracts protected spans, computes 5-tier hedge levels, detects causality/negation anchors, flags Chinglish/colloquial patterns, resolves external glossaries (detecting internal conflicts), mines cross-section term/acronym drift, and writes `/app/work/polish_brief.md`.
3. **Partial Checkpoint (`assemble_output.py --checkpoint partial`)**: Saves an honest baseline `result.json` early.
4. **Author Edits (`/app/work/polished_payload.json`)**: Agent edits sentence-by-sentence in batches following `resources/payload_schema.json` and `resources/genre_style_guides.json`.
5. **8-Axis Verification (`verify_polish.py`)**: Mechanically audits coverage, protected spans, hedge conservation, causality/negation conservation, glossary/acronym compliance, edit rationale quality, LaTeX/delimiter balance + minimal-edit ratio, and conflict disclosure. Exits non-zero until `error_count == 0`.
6. **Assemble Final Deliverables (`assemble_output.py --checkpoint final --strict`)**: Writes `result.json`, `polished_manuscript.md` (and `.tex`/`.docx` + PDF/PNG previews if applicable), `redline_diff.md`, `change_log.csv`, `change_log.md`, `glossary_table.md`, and `audit_and_confirmation_report.md`.

---

## 5. Self-Check & Standalone Verification

Run the built-in self-check suite (uses temporary directories only, zero side effects):
```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/self_check.py
```
The self-check tests:
1. English abstract/methods polishing + adversarial injection (confirms `verify_polish.py` catches hedge upgrades, correlation-to-causation shifts, and altered numbers, then passes once fixed).
2. Chinese-English bilingual text with Chinglish, cross-section terminology drift, and an internal glossary conflict.
3. LaTeX manuscript preservation (`\cite{}`, math, `\ref{}`) and custom `output_contract` field mapping.
4. Missing/corrupt input & non-UTF8 encoding recovery.

---

## 6. Known Limitations & Failure Handling

- **Scanned / Image-Only PDFs without OCR**: If PDF text extraction yields empty text or high garble ratio without OCR tools, the pipeline reports `unable_to_determine` rather than fabricating manuscript text.
- **No External Fact Verification**: Polishing preserves author-supplied facts, citations, and figures without inventing external references or verifying unstated experimental claims.
- **Ambiguous Semantic Intent**: When fixing broken syntax requires guessing scientific intent (e.g., whether a result is causal or correlational, or resolving conflicting glossary entries), the skill marks `needs_human_confirmation: true` and logs a closed-ended question for the author.
