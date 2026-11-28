# `latex-sci-composer` — Academic LaTeX Writing, Repair & Reproducible Compilation Skill

An Agent Skill built for **Harbor / DeepSeek Harness** and local CLI workflows to solve real-world scientific LaTeX authoring, migration, debugging, bibliography alignment, and reproducible compilation challenges.

- **Challenge**: `product-arena-07-865de7e4` — *写作13｜LaTeX 写作与编译*
- **GitHub Repository**: https://github.com/hubuyao27-cpu/public/tree/main/product-arena-07-865de7e4

---

## 1. Design Goals & Four Pillars

| Pillar | What `latex-sci-composer` Enforces |
|---|---|
| **编译得通 (Build Convergence)** | Tracks TeX `.log` balanced-paren file stacks and `-file-line-error` entries; clusters cascading downstream errors back to the earliest root cause (`error_taxonomy.json`); runs deterministic multi-pass compilation (`engine -> bibtex/biber -> engine*`) until auxiliary hashes stabilize. |
| **改得对 (Fidelity & Template Compliance)** | Extracts grounded numbers, tables, and citations from `.md`/`.txt`/`.csv`/`.tsv`/`.docx`/`.pdf`; enforces `amsmath`, upright units, `booktabs` tables (blocking `\resizebox` overflow hacks), tabular column-count checks, `\label` after `\caption`, and mandatory fields per `template_profiles.json`. |
| **交得可用 (Editable & Clean Sources)** | Outputs modular `.tex`/`.bib` source files alongside compiled `.pdf`, `previews/page-*.png`, `compile_diagnostics`, and `before_after_diff` — never PDF-only blobs or uncompilable snippets. |
| **复现得了 (Reproducible Builds)** | Probes live compilers (`pdflatex`/`xelatex`/`lualatex`/`bibtex`/`biber`) and installed CJK fonts (`fc-list :lang=zh`); captures `.fls` inputs, embedded PDF fonts, and PDF SHA-256 in `reproducibility_manifest.json` + executable `build.sh`. |

---

## 2. Directory Layout

```text
.
├── SKILL.md                             # Agent execution instructions & 3-step workflow
├── README.md                            # Architecture, CLI reference, and verification guide
├── GITHUB_URL.txt                       # Public repository reference
├── examples/
│   └── minimal_example.md               # Illustrative end-to-end input/output walkthrough
├── resources/
│   ├── error_taxonomy.json              # 30 structured TeX/BibTeX/Biber error rules & cascades
│   ├── package_conflicts.json           # Incompatible package pairs, load order, anti-patterns
│   ├── template_profiles.json           # Mandatory metadata & pitfalls (IEEE/ACM/Elsevier/Springer/ctex/...)
│   ├── latex_quality_checklist.md       # Math, table, citation, CJK font, and provenance checklist
│   ├── payload_schema.json              # Schema for optional/auto-hydrated deliverable_payload.json
│   └── failure_modes_and_edge_cases.md  # Decision tree for missing .cls/fonts/assets/corrupt inputs
└── scripts/
    ├── common.py                        # Single source of truth: safe I/O, TeX/CJK probing, sanitizers, contract mapper
    ├── prepare_and_audit.py             # Step 1: Inspect -> stage /app/work/src -> safe autofix -> static audit -> partial checkpoint
    ├── compile_and_diagnose.py          # Step 2: Multi-pass compiler (up to 5 passes), auto-fallback, log/font/PDF audit
    └── verify_and_assemble.py           # Step 3: Compile-if-changed -> payload auto-hydration -> 6-axis gate -> /app/output packager
```

---

## 3. Environment & Dependencies

- **Python**: Python 3.8+ using **pure standard library** (`json`, `re`, `csv`, `zipfile`, `xml.etree.ElementTree`, `subprocess`, `pathlib`, `hashlib`, `difflib`).
- **Optional Python Enhancement**: `PyMuPDF` (`pymupdf` / `fitz`) — when installed, used for PDF text extraction, embedded font listing, margin-overflow bbox checks, and PNG rendering; gracefully falls back to `pdftotext` / `pdftoppm` / `pdffonts` if absent.
- **External System Tools (probed dynamically)**: `pdflatex`, `xelatex`, `lualatex`, `bibtex`, `biber`, `kpsewhich`, `fc-list`, `pdftoppm`, `pdftotext`, `pdffonts`.

---

## 4. Quickstart & Streamlined 3-Step Invocation

```bash
export SKILL_DIR=/skills/weekly-skill
mkdir -p /app/work/src /app/work/build /app/work/previews /app/output

# Step 1: Inspect /input, stage sources (+ inline case.json), apply safe mechanical pre-fixes,
#         run static audit, and save early partial /app/output/result.json
python3 -B "$SKILL_DIR/scripts/prepare_and_audit.py" \
  --input-dir /input --work-dir /app/work --out-dir /app/output --apply-safe-fixes

# Step 2: Author/repair .tex/.bib in /app/work/src/ and run multi-pass compile + diagnosis
python3 -B "$SKILL_DIR/scripts/compile_and_diagnose.py" \
  --tex /app/work/src/main.tex --project-root /app/work/src \
  --work-dir /app/work/build --preview-dir /app/work/previews \
  --out-report /app/work/compile_report.json --out-summary /app/work/compile_summary.md

# Step 3: Auto-hydrate payload (or load deliverable_payload.json), verify 6 axes, and assemble /app/output
python3 -B "$SKILL_DIR/scripts/verify_and_assemble.py" \
  --tex /app/work/src/main.tex --project-root /app/work/src \
  --payload /app/work/deliverable_payload.json --work-dir /app/work --out-dir /app/output
```

---

## 5. Design Highlights & Honest Limits

- **Zero-Duplication In-Process Architecture**: All 3 workflow steps share `common.py` and import `audit_tex_tree()` / `compile_project()` directly in-process, avoiding subprocess sprawl and ensuring static lint and final verification never drift out of sync.
- **Auto-Hydrated Provenance**: If an agent writes concise `% [SRC: ...]` and `% [AUTHOR_CONFIRM: GAP-XX ...]` comments inside `.tex` (even without manually crafting a verbose JSON payload), `verify_and_assemble.py` mines them automatically alongside `auto_change_records.json` and `before_after_diff`.
- **No Shell Escape & Honest Blockers**: Every TeX invocation runs with `-no-shell-escape` and bounded timeouts; missing `.cls` files, missing figure binaries, and unreadable inputs surface honestly as `"partial"` or `"unable_to_determine"`.

---

## 6. Verification

Validated via `work/tests/run_test_suite.py` across 8 test suites (submission constraints + 7 end-to-end mock-TeX scenarios including `--apply-safe-fixes`, tabular column mismatch & unescaped underscore lint, CSV-to-booktabs snippets, CJK font fallback + embedded PDF font/SHA-256 manifests, BibTeX multi-pass & `??` detection, missing `.cls` honesty, auto-hydrated payloads with custom `output_contract`, and subdirectory projects).
