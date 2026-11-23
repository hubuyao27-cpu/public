---
name: scientific-structured-notes
description: >
  Section-aligned, anchor-grounded, verifiable scientific literature reading, Q&A,
  and structured note-taking skill. Use when asked to read scientific papers or PDFs,
  answer research questions with exact evidence, build structured paper cards, extract
  section-by-section summaries preserving original argumentation flow, extract deduplicated
  knowledge items, flag preconditions/limitations/conflicts, or output reusable Markdown,
  JSON, JSONL, and CSV literature notes under Harbor (/input/case.json, /app/output).
---

# Scientific Structured Notes & Grounded Reading Skill

Transform real scientific papers (PDF, Markdown, TXT, DOCX, HTML, JSON) into **section-aligned, condition-preserving, anchor-grounded, and reusable structured notes** and evidence-backed answers.

## Core Design Guarantees

1. **对得上 (Section Alignment)**: Preserves the paper's true section hierarchy (`S01`, `S02.1`, ...) and inter-section transitions rather than imposing generic boilerplate.
2. **留得住 (Condition & Boundary Preservation)**: Explicitly records research questions, data/samples, operating/method conditions, **critical preconditions under which conclusions hold**, limitations, and `not_reported` / `unable_to_determine` / conflicting items.
3. **找得回 (Fine-Grained Provenance)**: Every claim, metric, precondition, and task answer cites verified anchors (`[P042 | p.5 | S03.2]`, `[FIG:Fig. 2 | p.4]`, `[TAB:Table 1 | p.6]`, `[EQ:Eq. (3) | p.3]`) plus exact verbatim source quotes.
4. **用得上 (Reusable & Verifiable Outputs)**: Mechanically audits anchor existence, page match, quote grounding, numeric grounding, section order, and knowledge deduplication via `verify_notes.py`, emitting `result.json` (aligned to `output_contract`), `structured_notes.md`, `knowledge_items.jsonl`, and `paper_card_row.csv`.

---

## Runtime Paths & Skill Location

Resolve `$SKILL_DIR` automatically before running commands:

```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(pwd)"
mkdir -p /app/work /app/output
```

- **Inputs**: `/input/case.json`, `/input/task.md` (or `./task.md`), and real source files under `/input/` (`.pdf`, `.xml`/`.nxml`, `.md`, `.txt`, `.tex`, `.docx`, `.html`, `.json`).
- **Workspace**: `/app/work` (use `./work` when running outside Harbor).
- **Outputs**: `/app/output/` (must include `result.json` with `case_id` and all `output_contract` fields).

---

## 5-Step Execution Workflow

### Step 1: Inspect Task Boundaries, Questions & `output_contract`
Never assume paper content from filenames or domain labels. Inspect the actual inputs first:

```bash
python3 "$SKILL_DIR/scripts/inspect_case.py" \
  --input-dir /input \
  --out /app/work/case_plan.json
```

Read `/app/work/case_plan.json` carefully to identify:
- `case_id` and discovered `materials` (including encryption checks);
- `questions_detected` from `task.md` and `case.json`;
- `output_contract_parsed.fields` (exact required keys and expected types for `result.json`).

### Step 2: Layout-Aware Extraction, Section Tree & Early Checkpoint
Parse all input documents (supports PyMuPDF Band+Two-Column reordering, header/footer removal, OCR fallback on scanned pages, and DOCX/TXT/MD/JSON):

```bash
python3 "$SKILL_DIR/scripts/parse_paper.py" \
  --case-plan /app/work/case_plan.json \
  --out-dir /app/work/parsed \
  --render-flagged-pages
```

Inspect `/app/work/parsed/extraction_report.json` and `/app/work/parsed/section_outline.json`.
Immediately write an **early partial checkpoint** so deliverables exist on disk even under tight budgets:

```bash
python3 "$SKILL_DIR/scripts/assemble_notes.py" \
  --case-plan /app/work/case_plan.json \
  --extraction /app/work/parsed/extraction_report.json \
  --outline /app/work/parsed/section_outline.json \
  --checkpoint partial \
  --out-dir /app/output
```

**Exception Handling Branch (Check `extraction_report.json` before reading)**:
- If `any_encrypted: true` or `total_segments == 0`: skip fabrication; produce honest `unable_to_determine` / `partial` output in Step 5 with clear `manual_takeover_points`.
- If `any_unreadable_pages: true` or `garbled_text_risk` pages appear: check `/app/work/parsed/page_previews/` if vision is supported; otherwise document exact unreadable pages in `uncertainty.unable_to_determine` and `manual_takeover_points`.
- See `references/edge_cases_and_recovery.md` for detailed rules.

### Step 3: Read `anchored_paper.md` & Author `/app/work/notes_payload.json`
Read `/app/work/parsed/anchored_paper.md` (and follow `section_outline.json["batch_plan"]` if `is_long_document: true`, writing `batch_1.json`, `batch_2.json`, ... and merging them via `scripts/merge_notes.py`).

Follow the exact JSON schema in `templates/notes_payload_template.json`:
1. **`task_responses`**: Answer EVERY question in `questions_detected` directly, attaching `evidence` (`anchor_id`, `page`, `section`, exact verbatim `quote` copied from `anchored_paper.md`), `boundaries_or_assumptions`, and `verification_mode`.
2. **`paper_card`**: Fill all 7 required dimensions (`research_question`, `data_or_samples`, `methodology_and_conditions`, `key_findings`, `preconditions`, `conclusions`, `limitations`) plus `bibliographic`. Tag each finding/limitation with `"attribution": "author_stated"` or `"reader_inferred"`. If any dimension is absent from the source, explicitly mark `"status": "not_reported"`.
3. **`section_summaries`**: Cover every `body_section_id` in `section_outline.json` **in the exact original order**, providing `transition_from_prev`, `purpose`, `methods`, `results`, `conclusion`, `key_points`, and `evidence`.
4. **`knowledge_items`**: Extract atomic, deduplicated terms/variables/methods/findings/preconditions with `aliases` (acronyms/synonyms merged).
5. **`not_reported_and_conflicts`**: Explicitly record missing items, unreadable/unresolvable items, and internal or cross-document contradictions (citing both `source_a` and `source_b`).
6. **`contract_overrides`**: If `case_plan.json` lists custom `output_contract` keys not automatically satisfied by semantic mapping, supply them explicitly here.

If working in batches on a long paper:
```bash
python3 "$SKILL_DIR/scripts/merge_notes.py" \
  --inputs /app/work/batch_1.json /app/work/batch_2.json \
  --outline /app/work/parsed/section_outline.json \
  --out /app/work/notes_payload.json
```

### Step 4: Independent Mechanical Verification & Self-Correction
Run the mechanical verifier:

```bash
python3 "$SKILL_DIR/scripts/verify_notes.py" \
  --source /app/work/parsed/source_index.json \
  --outline /app/work/parsed/section_outline.json \
  --notes /app/work/notes_payload.json \
  --case-plan /app/work/case_plan.json \
  --extraction /app/work/parsed/extraction_report.json \
  --out /app/work/verification_report.json
```

`verify_notes.py` audits:
- Anchor existence (`source_index.json` paragraphs + `FIG:`/`TAB:`/`EQ:` artifacts) & page match;
- Verbatim `quote` substring / normalized overlap match in the cited anchor;
- Quantitative number grounding (catches fabricated or misread metrics/percentages);
- Original section coverage & strict order monotonicity;
- Knowledge item deduplication & acronym collision;
- Boundary honesty & `output_contract` completeness.

If `error_count > 0` (or actionable `WARNING`s like `quote_in_adjacent_paragraph` occur), inspect `/app/work/verification_report.json`, fix the cited JSON paths in `/app/work/notes_payload.json`, and re-run `verify_notes.py`.

### Step 5: Assemble Final Deliverables
Run final assembly to populate `/app/output/`:

```bash
python3 "$SKILL_DIR/scripts/assemble_notes.py" \
  --case-plan /app/work/case_plan.json \
  --extraction /app/work/parsed/extraction_report.json \
  --outline /app/work/parsed/section_outline.json \
  --notes /app/work/notes_payload.json \
  --verification /app/work/verification_report.json \
  --checkpoint final \
  --out-dir /app/output
```

Verify `/app/output/` contains `result.json`, `structured_notes.md`, `knowledge_items.jsonl`, `paper_card_row.csv`, and `verification_report.json`.

---

## Strict Honesty & Quality Rules

- **Never fabricate**: Do not invent missing pages, unreadable equations, plot values absent from text, or omitted experimental parameters.
- **No synthetic PDF pages for non-PDF inputs**: Check `extraction_report.json["has_pdf_pages"]`. When `has_pdf_pages` is `false` (e.g., JATS XML, Markdown, TXT, DOCX, JSON), leave `"page": null` and cite `anchor_id` + `section` (plus XML `source_para_id` if present) rather than inventing `"page": 1`.
- **Exact quotes**: Copy `quote` directly from `anchored_paper.md` (in the source language) so mechanical verification passes cleanly.
- **Distinguish machine vs. human checks**: Preserve `verification_mode` (`machine_verifiable` vs `human_review_required`) and populate `manual_takeover_points` whenever exceptions occur.
