# Extraction, Atomicity, Grounding, and Exception Rules

These rules govern how the Agent and the deterministic verification toolchain (`verify_nanopubs.py`) construct, align, audit, and degrade Nanopublications.

---

## 1. Five-Criterion Atomicity Test ("拆得细")

Never place a multi-sentence summary or compound finding into one `claim_text`. Every candidate assertion must satisfy **all 5 criteria**:

1. **Single Truth-Value Criterion**: The claim can be adjudicated `true`, `false`, or `unverified` independently without splitting into partial truths.
2. **Maximum Length**: `<= 45` English words or `<= 90` CJK characters.
3. **No Compound Clause Bundling**:
   - Split parallel effects (`"reduces A and increases B"` -> 2 Nanopubs).
   - Split contrastive findings (`"X improved Y, whereas Z showed no effect"` -> 2 Nanopubs).
   - Split distinct outcome metrics (`"overpotential of 218 mV and Tafel slope of 34.2 mV dec^-1"` -> 2 Nanopubs unless explicitly testing a single ratio/combined endpoint; max 2 `quantitative_values` per Nanopub).
4. **Condition Preservation**: Never strip temperature, concentration, dosage, pH, cohort, baseline comparator, or statistical qualifier (`p = 0.42`, `± 4 mV`). Record them in `assertion.conditions` and `quantitative_values`.
5. **Subject-Object Passive-Voice Disambiguation**:
   - Active: `"A inhibits B"` -> `subject: A`, `predicate: inhibits`, `object: B`.
   - Passive: `"B was inhibited by A"` -> **DO NOT** set `subject: B`! Normalize to `subject: A`, `predicate: inhibits`, `object: B`. `verify_nanopubs.py` mechanically flags `<subject> ... <passive_marker> ... <object>` inversions.

---

## 2. Minimal Sufficient Evidence Binding ("对得上")

1. **Minimal Verbatim Span**:
   - Extract the **smallest contiguous verbatim excerpt** (typically 1–2 sentences, `<= 450` chars) that directly establishes subject, predicate, object, conditions, and quantitative values.
   - Never cite an entire multi-sentence paragraph when 1 sentence suffices. If 2–3 non-adjacent sentences are genuinely needed (e.g., pronoun antecedent resolution), supply separate items in `evidence_spans[]` or set `multi_sentence_justification`.
2. **Exact Verbatim Fidelity**:
   - Quotes must appear verbatim in `source_digest.md` (with `find_verbatim_span()` checking exact / whitespace-NFKC alignment and assigning `char_start..char_end`). Paraphrasing a quote is a hard `ERROR`.
3. **Evidence Type & Epistemic Alignment**:
   - `author_direct_finding` + `direct_empirical`: Only when the paper itself reports new empirical/experimental results.
   - `author_method_claim` + `method_specification`: Experimental protocol, synthesis route, apparatus, or evaluation setup.
   - `table_or_caption_direct`: Values read directly from a structured table row or figure caption.
   - `cited_prior_work`: Results attributed to references (`[1]`, `(Smith et al., 2020)`, `"Previous studies"`). **Never** label cited prior work as `author_direct_finding`.
   - `hypothesis_or_speculation` + `indirect_inference`: Statements marked by `"may"`, `"might"`, `"suggests"`, `"tentative"`, or `"could"`.

---

## 3. Provenance & Traceability ("追得回")

1. **Per-Paper Metadata Isolation**:
   - Each document carries its own `source_metadata` (`title`, `authors`, `year`, `venue`, `version`, `doi`, `pmid`, `arxiv_id`, `url`, `source_file`, `file_sha256`).
   - Root `case.json` metadata applies **only** to the primary paper; secondary papers take metadata strictly from their own headers/XML or `corpus-manifest.json`. Missing metadata stays `null` and is logged in `uncertainty[]`.
2. **Zero Fake Page Numbers**:
   - When `has_pdf_pages == false` (Markdown, JSON, JATS XML, TXT), **NEVER** fabricate `page: 1` or `locator_type: "pdf_page"`. Use exact `paragraph_id`, `sentence_id`, `line_start..line_end`, `table_id`, or `figure_id`.
3. **Cross-Section Deduplication & Corroboration Merge**:
   - When the same claim appears in both the Abstract and Results (or across duplicate formats of the same paper via `paper_group_id`), merge them into **one** canonical Nanopublication (`dedup_fingerprint` / Jaccard `>= 0.82`) while preserving **all unique `evidence_spans[]`** and incrementing `corroboration_count`.

---

## 4. Deterministic Confidence Rubric

`confidence.score` is computed reproducibly in `[0.0, 1.0]`:
- **0.30 × `evidence_directness`**: `direct_empirical=1.0`, `direct_analytical=0.95`, `table_or_caption_direct=0.90`, `method_specification=0.85`, `indirect_inference=0.60`, `cited_prior_work=0.45`.
- **0.25 × `quote_match_exactness`**: `1.0` exact substring match, `0.85` whitespace-normalized match, `0.0` unmatched (caps total score at `0.20` and sets `unverified`).
- **0.20 × `condition_completeness`**: `min(1.0, populated_condition_fields / 2.0)`.
- **0.15 × `numeric_fidelity`**: `1.0` if all numeric values appear in quote, `0.0` if ungrounded numbers detected.
- **0.10 × `corroboration_bonus`**: `min(1.0, (distinct_paragraph_locations - 1) * 0.5)`.

Levels: `high` (`>= 0.80` with 0 item errors), `medium` (`>= 0.55`), `low` (`>= 0.30`), `unverified` (`< 0.30`).

---

## 5. Exception & Failure Mode Matrix

| Failure / Boundary Condition | Detection Mechanism | Mandatory Skill Behavior |
|---|---|---|
| **Scanned PDF without text layer** | `ingest_source.py` flags `char_count < 80` or `garble_ratio > 0.25` | Run `--ocr-auto` if `tesseract` available (flag low-confidence OCR `< 60%`); otherwise omit page, set `status: "partial"` or `"unable_to_determine"`, add `manual_takeover_points`. |
| **Encrypted / corrupt / missing source** | `doc.needs_pass` or 0 readable docs or missing `input_file` | Do **not** fabricate claims. Output `status: "unable_to_determine"`, record exact path/error in `uncertainty[]` and `limitations[]`. |
| **Cross-figure / un-tabulated visual value** | Claim mentions `Fig. X` without caption/table backing | Warn `cross_figure_reference_check`; extract only values explicitly written in captions/text/tables, never guess axis pixels. |
| **Supplied claim unsupported in text** | `quote_not_grounded_in_source` or Q&A fails | Record `answer_status: "unreported_in_source"` or `confidence.level: "unverified"` with honest gap note. |
| **Intra- or cross-paper contradiction** | Gate 8 (`opposite_predicate` / `polarity_conflict`) | Preserve both assertions honestly, document in `conflicts[]` with `condition_difference`, do not silently drop either side. |
| **Missing bibliographic metadata** | Empty `doi`/`year`/`authors` in `source_index.json` | Keep missing fields `null`, log warning in `uncertainty[]`; never invent DOIs or copy primary paper DOI onto secondary papers. |
