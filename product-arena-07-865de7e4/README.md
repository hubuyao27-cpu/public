# `latex-sci-composer` — Academic LaTeX Writing, Repair & Reproducible Compilation Skill

An Agent Skill built for **Harbor / DeepSeek Harness** and local CLI workflows to solve real-world scientific LaTeX authoring, migration, debugging, bibliography alignment, and reproducible compilation challenges.

- **Challenge**: `product-arena-07-865de7e4` — *写作13｜LaTeX 写作与编译*
- **GitHub Repository**: https://github.com/hubuyao27-cpu/public/tree/main/product-arena-07-865de7e4

---

## 1. Design Goals & Four Pillars

| Pillar | What `latex-sci-composer` Enforces |
|---|---|
| **编译得通 (Build Convergence)** | Tracks TeX `.log` file stacks `(./sub.tex ...)` and `-file-line-error` entries; clusters cascading downstream errors back to the earliest root cause (`error_taxonomy.json`); runs deterministic multi-pass compilation (`engine -> bibtex/biber -> engine*`) until auxiliary hashes converge. |
| **改得对 (Fidelity & Template Compliance)** | Extracts grounded numbers, tables, and citations from `.md`/`.txt`/`.csv`/`.tsv`/`.docx`/`.pdf`; enforces `amsmath`, upright units, `booktabs` tables (blocking `\resizebox` overflow hacks), `\label` after `\caption`, and mandatory fields per `template_profiles.json`. |
| **交得可用 (Editable & Clean Sources)** | Outputs modular, commented `.tex`/`.bib` source files plus compiled `.pdf` and `previews/page-*.png` — never PDF-only blobs or uncompilable snippets. |
| **复现得了 (Reproducible Builds)** | Probes live compilers (`pdflatex`/`xelatex`/`lualatex`/`bibtex`/`biber`) and installed CJK fonts (`fc-list :lang=zh`) before picking fonts/engines; captures `.fls` inputs and package versions into `reproducibility_manifest.json` and executable `build.sh`. |

---

## 2. Directory Layout

```text
.
├── SKILL.md                             # Agent execution instructions & workflow contract
├── README.md                            # Complete usage, architecture, and verification guide
├── examples/
│   └── minimal_example.md               # Illustrative end-to-end input/output walkthrough
├── resources/
│   ├── error_taxonomy.json              # 30 structured TeX/BibTeX/Biber error rules & cascades
│   ├── package_conflicts.json           # Incompatible package pairs, load order, anti-patterns
│   ├── template_profiles.json           # Mandatory metadata & pitfalls (IEEE/ACM/Elsevier/Springer/ctex/...)
│   ├── latex_quality_checklist.md       # Math, table, citation, CJK font, and provenance checklist
│   ├── payload_schema.json              # Schema for /app/work/deliverable_payload.json
│   └── failure_modes_and_edge_cases.md  # Decision tree for missing .cls/fonts/assets/corrupt inputs
└── scripts/
    ├── common.py                        # Shared IO, TeX comment stripping, brace/cmd parsing, contract mapper
    ├── inspect_case.py                  # Step 1a: Inspect /input contract, inline fields, live TeX & fc-list fonts
    ├── analyze_latex.py                 # Step 1b: Static include/bib/ref/brace/table audit & source fact mining
    ├── prepare_workspace.py             # Step 1 wrapper: inspect -> stage /app/work/src -> analyze -> partial checkpoint
    ├── compile_and_diagnose.py          # Step 2: Multi-pass compiler (up to 5 passes), auto-fallback, log & PDF audit
    ├── verify_deliverable.py            # Step 4a: 6-axis verification gate (including undefined refs/cites check)
    ├── assemble_deliverables.py         # Step 4b: Packager emitting compile_diagnostics & before_after_diff in result.json
    └── finalize_workspace.py            # Step 4 wrapper: compile -> verify -> assemble final /app/output
```

---

## 3. Environment & Dependencies

- **Python**: Python 3.8+ using **pure standard library** (`json`, `re`, `csv`, `zipfile`, `xml.etree.ElementTree`, `subprocess`, `pathlib`, `hashlib`).
- **Optional Python Enhancement**: `PyMuPDF` (`fitz`) — if installed, used for PDF text extraction, bounding-box right-margin overflow inspection, and fallback PNG rendering; gracefully falls back to `pdftotext` / `pdftoppm` if absent.
- **External System Tools (probed dynamically by `inspect_case.py`)**:
  - TeX engines: `pdflatex`, `xelatex`, `lualatex`
  - Bibliography backends: `bibtex`, `biber`
  - Utilities: `kpsewhich`, `fc-list` (fontconfig), `pdftoppm`, `pdftotext`

No external Python packages (`pip install`) or hardcoded Windows/macOS font paths are required.

---

## 4. Quickstart & Step-by-Step Invocation

```bash
export SKILL_DIR=/skills/weekly-skill
mkdir -p /app/work/src /app/work/build /app/work/previews /app/output

# 1. Inspect /input/case.json, task.md, files, and live TeX/CJK fonts
python3 "$SKILL_DIR/scripts/inspect_case.py" --input-dir /input --out /app/work/case_plan.json

# 2. Run static TeX/Bib audit and extract non-TeX source facts
python3 "$SKILL_DIR/scripts/analyze_latex.py" --case-plan /app/work/case_plan.json \
  --out-audit /app/work/latex_audit.json --out-digest /app/work/latex_digest.md

# 3. Save early partial checkpoint (safety net against 64-req / 900s limits)
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json --audit /app/work/latex_audit.json \
  --checkpoint partial --out-dir /app/output

# 4. Edit/author sources in /app/work/src/ and compile with multi-pass diagnosis
python3 "$SKILL_DIR/scripts/compile_and_diagnose.py" \
  --tex /app/work/src/main.tex --work-dir /app/work/build \
  --preview-dir /app/work/previews --out-report /app/work/compile_report.json \
  --out-summary /app/work/compile_summary.md

# 5. Author /app/work/deliverable_payload.json and run 6-axis verification gate
python3 "$SKILL_DIR/scripts/verify_deliverable.py" \
  --payload /app/work/deliverable_payload.json --case-plan /app/work/case_plan.json \
  --audit /app/work/latex_audit.json --compile-report /app/work/compile_report.json \
  --work-tex-dir /app/work/src --out /app/work/verification_report.json

# 6. Assemble final verified deliverables under /app/output/
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json --audit /app/work/latex_audit.json \
  --compile-report /app/work/compile_report.json --payload /app/work/deliverable_payload.json \
  --verification /app/work/verification_report.json --work-tex-dir /app/work/src \
  --checkpoint final --out-dir /app/output
```

---

## 5. Supported Task Modes & Failure Handling

- **Task Modes**:
  1. `compile_and_repair`: Fix syntax, cascading errors, package clashes, and build convergence.
  2. `template_migration`: Migrate Markdown/DOCX/generic `article` drafts into `IEEEtran`, `acmart`, `elsarticle`, `llncs`, `sn-jnl`, `ctexart`, etc.
  3. `formula_table_refinement`: Convert `eqnarray` -> `align`, raw `\hline` -> `booktabs`, fix wide tables structurally without `\resizebox`, align units.
  4. `citation_bib_alignment`: Fix `.bib` syntax, duplicate keys, `\cite` mismatches, and `bibtex`/`biber` multi-pass convergence.
  5. `bilingual_latex_writing`: Auto-switch CJK sources to `xelatex` and verify Linux system fonts via `fc-list` before compiling.
  6. `draft_to_latex`: Convert scattered Markdown/CSV/PDF/DOCX notes into structured, source-traceable LaTeX with explicit `[AUTHOR_CONFIRM: GAP-XX]`.

- **Honest Limitations & Failure Handling**:
  - Missing proprietary `.cls`/`.sty` files are never fabricated; the Skill reports `status: "partial"` with exact missing filenames in `uncertainty[]` and `author_confirmation_items[]`.
  - Unreadable/encrypted PDFs or OS permission blocks short-circuit cleanly with exact diagnostic reasons.
  - Numeric/citation discrepancies across multiple input files are flagged for author confirmation rather than silently overwritten.

---

## 6. Verification & Maintenance

All scripts are checked for syntax (`python -m py_compile`) and validated across 6 end-to-end scenarios (cascading syntax repair, template migration with `\resizebox` rejection, CJK `xelatex` + glyph checks, multi-pass `bibtex` + `??` leak checks, missing-class/file honesty, and custom `output_contract` compliance) via an isolated mock-TeX integration harness (`work/tests/run_test_suite.py` in the challenge workspace).
