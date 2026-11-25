# Failure Modes, Edge Cases & Honesty Rules (`citation-reference-manager`)

Consult this guide whenever an input file, reference library, or claim exhibits anomalies. **Never fabricate metadata, DOIs, quotes, or verification outcomes.**

---

## 1. Offline / Unreachable DOI & External Metadata Verification
- Harbor and air-gapped sandboxes do not guarantee live Crossref/OpenAlex/CNKI access.
- Never claim `"doi_verified_online"` unless an actual verified local snapshot or explicit API response exists in `/input/`.
- Record unverified DOIs honestly as `doi_verification_status: "not_checked_offline"` (if syntax matches `^10\.\d{4,9}/...`) or `"invalid_doi_syntax"` (if malformed), and note the boundary in `uncertainty`.

## 2. Recycled or Conflicting DOI Across Unrelated Records
- If two records share a normalized DOI (or arXiv ID) **but** share the same script with `title_similarity < 0.30` and incompatible authors:
  - **DO NOT MERGE.**
  - Flag both records under `recycled_doi_conflicts[]` and `human_confirmation_items[]` (`severity: "critical"`).

## 3. Same-Author Distinct Works (Anti-False-Merge Guardrail)
- Authors frequently publish multiple papers in the same year or follow-up conference-to-journal works.
- Never auto-merge two records simply because they share `(first_author, year)` or even `(authors, year, journal)`.
- Keep separate whenever:
  - Both carry distinct valid DOIs (and neither is an arXiv/bioRxiv preprint of the other), OR
  - Both carry non-overlapping numeric page ranges in the same volume, OR
  - Same-script normalized `title_similarity < 0.70`.
- For conference vs. extended journal (`0.72 <= title_sim < 0.90`, same authors), put in `ambiguous_duplicate_candidates` and decide explicitly (or mark `needs_human_confirm`) rather than silently dropping one.

## 4. Fabricated / Ghost Citations & Dangling Keys
- If an in-text citation `[9]`, `\cite{foo2024}`, or `(Bar, 2024)` has no matching record in any reference library or inline reference list:
  - Mark `dangling_citations` and emit verdict `"fabricated_or_dangling_ref"`.
  - Annotate inline in `revised_manuscript.md` as `[待核验引用: 原标记 ...]` (ZH) or `[UNVERIFIED_CITE: ...]` (EN) — never invent a bibliographic entry!
- If a library entry has `missing_venue_and_identifier` (no journal/publisher/conference AND no DOI/arXiv/ISBN/URL) or `impossible_year` (`< 1800` or `> current_year + 1`) or `inverted_page_range` (`start > end`):
  - Flag as suspect metadata needing human confirmation.

## 5. Missing Fields During Format Conversion
- Never pad missing `volume`, `issue`, `pages`, `publisher`, `city`, or `doi` with `"N/A"`, `"None"`, `"0"`, or guesses.
- Omit absent optional delimiters cleanly (e.g., `2023, 45: 12-20.` rather than `2023, 45(): 12-20.`), and record missing required fields in `missing_metadata_fields[]` + `human_confirmation_items[]`.

## 6. Clause-Level Placement vs. Paragraph-End Dumping ("挂得对")
- When a multi-sentence paragraph contains multiple factual/quantitative claims or named methods followed by `[1-4]` (or `(A, 2020; B, 2021)`) stacked only at the very last sentence:
  - Inspect each sentence against each candidate reference's title/abstract/evidence text.
  - Relocate each citation token `[[CITE:REF_XXX]]` immediately after the specific clause/claim it actually supports (right before punctuation in English/Chinese prose, e.g., `...达到 94.2%[[CITE:REF_002]]。` or `...achieved 94.2%[[CITE:REF_002]].`).
  - If a claim cannot be linked to ANY record in the library (`insufficient_evidence_in_library`), mark `[证据不足/待人工核验]` (ZH) or `[INSUFFICIENT_EVIDENCE]` (EN) and add a `human_confirmation_items` entry.

## 7. Cross-Reference & Structural Edge Cases ("编得顺")
- Synchronize citations inside **body text, figure captions (`Figure 1`), table captions (`Table 2`), table cells, footnotes, and appendices** identically via `[[CITE:REF_XXX]]` tokens.
- Never leave uncited library entries inside the numbered reference list (`1..K`); segregate uncited entries into a dedicated `uncited_library_entries` section of the audit report so the bibliography matches in-text citations 1-to-1.

## 8. File Encoding, Malformed Exports, Empty/Corrupt Inputs
- Always try UTF-16 BOM (`utf-16`), `utf-8-sig`, `utf-8`, `gb18030`, `gbk`, `latin-1` in order and report decoding warnings.
- For malformed BibTeX (unclosed braces, quotes nested inside `{...}`, missing commas, duplicate keys), salvage valid entries forward to the next `\n@entry{` and log exact line spans in `parse_warnings` without crashing.
- If `/input/` has 0 usable files, encrypted PDFs, or corrupted binary inputs (`.docx`/`.pdf`), record honest `uncertainty[]` and `human_confirmation_items[]` in `/app/work/citation_plan.json` and run `finalize_workspace.py` (Step 3) so `result.json` exits cleanly with `status: "unable_to_determine"` or `"partial"`.
