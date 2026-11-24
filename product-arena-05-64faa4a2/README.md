# `sci-methods-writer` — Evidence-Grounded Methods & Experiments Chapter Writing Skill

A Harbor-ready, domain-adaptive Agent Skill for drafting, revising, auditing, and structuring scientific **Methods & Experiments** chapters from scattered raw materials (configs, source code, shell scripts, experiment logs, CSV/TSV tables, lab notes, rough drafts, and reference PDFs) while enforcing **zero-hallucination numeric grounding**, **precedence-aware conflict resolution**, **prose-to-table alignment**, and **actionable author-confirmation checklists**.

---

## 1. Core Capabilities & Innovations

Instead of relying on fill-in-the-blank templates or unchecked prose generation, `sci-methods-writer` implements a closed-loop **Extract -> Align -> Detect Conflicts & Gaps -> Mechanically Verify -> Assemble** pipeline:

1. **Multi-Artifact Fact Ledger with Precedence-Aware Conflict Engine (`scripts/extract_facts.py`)**:
   - Deterministically parses YAML/JSON/TOML/INI/`.env` configs, Python AST (`argparse` defaults, constants, seed/split/optimizer calls), Shell CLI overrides, runtime logs (effective configs, hardware/software versions, git commits), CSV/TSV tables, Markdown/DOCX/LaTeX notes & drafts, and PDFs.
   - Normalizes cross-file parameter aliases (`lr`/`learning_rate`/`eta`, `bs`/`batch_size`, `seed`/`random_state`, `T`/`calcination_temp`, etc.) and SI units (`30 min == 1800 s`, `3e-4 == 0.0003`, `500 °C == 773.15 K`, `80% == 0.8`).
   - Enforces explicit evidence precedence (`runtime_log > cli_script > exp_config > data_table > paper_source > lab_note > code_default > narrative_draft`) and surfaces every cross-artifact conflict (`CONF-XX`) plus uncorroborated draft-only claims before writing begins.
2. **Bidirectional Prose <-> Table <-> Raw Artifact Hallucination Gate (`scripts/verify_chapter.py`)**:
   - Scans drafted prose and parameter tables for quantitative claims and rejects ungrounded numbers fabricated for fluency.
   - Detects prose-vs-table numeric drift, invalid file/line locators, silently suppressed conflicts, draft-only claims falsely labeled `"verified"`, missing baseline budget-parity declarations, dangling `[待作者确认: GAP-XX]` tags, and generic boilerplate limitations.
3. **Domain-Adaptive & Task-Mode-Adaptive Engine (`scripts/inspect_case.py`, `resources/six_pillar_rubric.json`)**:
   - Adapts automatically to `ml_computational`, `wetlab_chem_bio_materials`, `simulation_engineering`, or `general_scientific` strictly from actual file content (never fabricating facts from metadata labels).
   - Supports drafting from scratch, revising/aligning rough drafts with `change_records`, proofreading/auditing, bilingual writing, and structured table extraction.
4. **Dynamic Harbor `output_contract` Assembly & Optional Visual Previews (`scripts/assemble_deliverables.py`)**:
   - Saves an honest early `partial` checkpoint right after Step 2 to guard against budget/timeout interruptions.
   - Maps arbitrary JSON-Schema / dict / list `output_contract` keys cleanly into `/app/output/result.json`, alongside editable Markdown, CSV, JSON, and optional DOCX/LaTeX/workflow diagram + real PDF/PNG previews.

---

## 2. Directory Layout

```text
submission/
├── SKILL.md                                     # Agent entrypoint & 6-step workflow
├── README.md                                    # Documentation, usage & maintenance guide
├── scripts/
│   ├── common.py                                # Safe I/O, stdlib fallback parsers, alias & SI unit engine
│   ├── inspect_case.py                          # Step 1: case.json/task.md/output_contract & file inventory
│   ├── extract_facts.py                         # Step 2: Fact Ledger, conflict engine, skeleton & 6-pillar pre-audit
│   ├── prepare_workspace.py                     # One-command prep: inspect -> extract -> skeleton -> partial checkpoint
│   ├── verify_chapter.py                        # Step 5: Mechanical hallucination & alignment verifier
│   ├── assemble_deliverables.py                 # Step 3 & 6: Partial checkpoint & final multi-format assembler
│   └── finalize_workspace.py                    # One-command verify + final deliverable assembly
├── resources/
│   ├── six_pillar_rubric.json                   # Domain-adaptive 6-pillar slots & boilerplate detector list
│   ├── parameter_aliases.json                   # Canonical parameter aliases, source precedence & unit factors
│   ├── payload_schema.json                      # JSON schema for /app/work/chapter_payload.json
│   ├── failure_modes_and_edge_cases.md          # Exception & conflict-handling playbook
│   └── templates/
│       ├── methods_section_template.md          # Reusable 6-pillar Methods & Experiments template
│       └── author_confirmation_template.md      # Reusable conflict & author-question template
└── examples/
    └── minimal_example.md                       # Self-contained illustrative walkthrough (not a formal case)
```

---

## 3. Dependencies & Runtime Compatibility

- **Required**: **Python 3.10+ standard library only** (`ast`, `csv`, `json`, `re`, `xml.etree.ElementTree`, `zipfile`, `tomllib` on 3.11+). All 5 scripts execute cleanly with zero external packages installed.
- **Optional Enhancements (auto-detected with graceful stdlib fallbacks)**:
  - `PyYAML` (`yaml`): full YAML 1.1 parsing (falls back to indentation-aware stdlib YAML parser).
  - `PyMuPDF` (`pymupdf`) or `pdftotext`: PDF text & encryption detection (honest warning if absent).
  - `python-docx` (`docx`): DOCX extraction & export (reading falls back to stdlib `zipfile` + `word/document.xml`).
  - `matplotlib`: workflow diagram PNG preview when diagrams are requested.
  - `soffice` / `libreoffice`, `xelatex` / `pdflatex`, `pdftoppm`: real PDF/PNG preview rendering for requested DOCX/LaTeX deliverables.

---

## 4. Quickstart CLI Invocation

```bash
export SKILL_DIR=/skills/weekly-skill   # or local path to submission/
mkdir -p /app/work /app/output

# Step 1: Inspect case contract & materials
python3 "$SKILL_DIR/scripts/inspect_case.py" --input-dir /input --out /app/work/case_plan.json

# Step 2: Extract anchored Fact Ledger, conflicts & preliminary gaps
python3 "$SKILL_DIR/scripts/extract_facts.py" \
  --case-plan /app/work/case_plan.json \
  --out-ledger /app/work/fact_ledger.json \
  --out-digest /app/work/fact_digest.md

# Step 3: Save early honest partial checkpoint
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json \
  --ledger /app/work/fact_ledger.json \
  --checkpoint partial --out-dir /app/output

# Step 4: Agent reads /app/work/fact_digest.md and authors /app/work/chapter_payload.json

# Step 5: Run mechanical zero-hallucination & alignment audit
python3 "$SKILL_DIR/scripts/verify_chapter.py" \
  --payload /app/work/chapter_payload.json \
  --ledger /app/work/fact_ledger.json \
  --case-plan /app/work/case_plan.json \
  --out /app/work/verification_report.json

# Step 6: Assemble final verified deliverables
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan /app/work/case_plan.json \
  --ledger /app/work/fact_ledger.json \
  --payload /app/work/chapter_payload.json \
  --verification /app/work/verification_report.json \
  --checkpoint final --out-dir /app/output
```

---

## 5. Output Artifacts (`/app/output/`)

| Artifact | Purpose |
|---|---|
| `result.json` | Harbor-compliant structured output containing `case_id`, all `output_contract` fields, `status`, `sources`, `uncertainty`, `change_records`, 6-pillar structured records, `conflicts`, and `author_confirmation_items`. |
| `methods_and_experiments.md` | Clean, editable publication-ready Methods & Experiments chapter with inline `[待作者确认: GAP-XX]` anchors. |
| `parameter_table.md` & `.csv` | Reusable parameter provenance table linking every value/unit to its exact file+line source and verification status. |
| `reproducibility_and_author_checklist.md` | Actionable reproducibility checklist, prioritized author-confirmation questionnaire, conflict table, concrete limitations, and change log. |
| `provenance_map.json` | Flat claim-to-source audit ledger for automated downstream checks. |
| Optional `.docx` / `.tex` / `.pdf` / `.png` | Emitted and preview-rendered whenever requested in `task.md` or `case.json`. |

---

## 6. Applicable Scope, Limitations & Human Confirmation Boundaries

- **Best Fit**: Scientific Methods & Experiments drafting, draft-vs-code/config alignment, parameter provenance auditing, reproducibility checklist construction, and concrete limitation authoring across ML/CS, chemistry/materials/biology wet-lab protocols, and numerical simulation workflows.
- **Explicit Boundaries**:
  - Cannot recover parameters buried solely inside opaque binary checkpoints (`.pt`, `.h5`, `.pkl`) or password-encrypted PDFs without author-supplied metadata; such artifacts are honestly logged in `uncertainty` and `author_confirmation_items`.
  - When two authoritative sources of identical precedence disagree without an execution log, the Skill intentionally refuses to guess and escalates to human author confirmation.
  - Complex domain-specific equations or bespoke image-only experimental figures without text layers require author visual sign-off.
- **Maintenance**: Extend `resources/parameter_aliases.json` for domain-specific parameter keys/units and `resources/six_pillar_rubric.json` for custom journal checklists without modifying Python source code.
