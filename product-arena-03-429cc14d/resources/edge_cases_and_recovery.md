# Edge Cases, Failure Modes & Honesty Rules

Follow these rules whenever inputs, libraries, or claims contain anomalies. **Never fabricate metadata, DOIs, quotes, or verification results.**

## 1. Offline DOI & Metadata Verification Boundaries
- Harbor and air-gapped sandboxes do not guarantee live Crossref/OpenAlex/CNKI access.
- Never report `"doi_verified_online"` unless a local verification snapshot exists in `/input/`.
- Classify DOIs honestly: `"not_checked_offline"` (matches `^10\.\d{4,9}/[^\s]+$`), `"invalid_doi_syntax"`, or `"missing"`, and record offline limits in `uncertainty[]`.

## 2. Recycled / Conflicting DOI Guardrail
- If two records share a normalized DOI or arXiv ID, yet have same-script `title_similarity < 0.30` and zero author surname overlap:
  - **DO NOT MERGE.**
  - Record under `recycled_doi_conflicts[]` and `human_confirmation_items[]` (`severity: "critical"`).

## 3. Same-Author Distinct Works (Anti-False-Merge)
- Never auto-merge two records solely because they share `(first_author, year)` or `(authors, year, journal)`.
- Protect as separate whenever:
  - Both carry distinct valid non-preprint DOIs, OR
  - Both carry disjoint numeric page ranges in the same volume/year, OR
  - Title contains distinct series markers (`Part I` vs `Part II`, `上篇` vs `下篇`), OR
  - Same-script `title_similarity < 0.65`.

## 4. Dangling / Ghost Citations & Suspect Metadata
- In-text markers (`[9]`, `\cite{ghost2024}`, `(Nobody, 2024)`) with no matching library entry:
  - Verdict: `"fabricated_or_dangling_ref"`.
  - Render inline as `[待核验引用: 原标记 ...]` (ZH) or `[UNVERIFIED_CITE: ...]` (EN) — never invent a bibliography record.
- Flag `impossible_year` (`< 1800` or `> current_year + 2`), `inverted_page_range` (`start > end`), and `missing_venue_and_identifier` in `integrity_flags` and `human_confirmation_items`.

## 5. Missing Metadata Fields During Style Conversion
- Never pad missing `volume`, `issue`, `pages`, `publisher`, `city`, or `doi` with `"N/A"`, `"None"`, `"null"`, or guesses.
- Omit absent optional punctuation cleanly (e.g., `2023, 45: 12-20.` instead of `2023, 45(): 12-20.`), while logging `missing_required_fields`.

## 6. Clause-Level Placement ("挂得对") vs. Paragraph-End Dumping
- When a multi-sentence paragraph stacks `[1-4]` or `(A, 2020; B, 2021)` at the end of the final sentence while earlier sentences make distinct claims:
  - Map each sentence/clause to the specific reference supporting it and place `[[CITE:REF_XXX]]` immediately after that clause before punctuation (`...达到 94.2%[[CITE:REF_002]]。`).
  - Claims unsupported by any library record get `[证据不足/待人工核验]` (ZH) or `[INSUFFICIENT_EVIDENCE]` (EN).

## 7. Cross-Zone Continuity & Uncited Library Segregation ("编得顺")
- Synchronize body text, figure captions, table captions, table cells, appendices, and footnotes in exact reading order.
- Exclude uncited library entries from the numbered bibliography (`1..K`); report them in `uncited_library_entries` so in-text citations and bibliography match 1-to-1.

## 8. Encoding, Malformed Exports & Empty/Corrupt Inputs
- Decode files in order: `utf-16`, `utf-8-sig`, `utf-8`, `gb18030`, `gbk`, `latin-1`.
- Salvage malformed `.bib` / `.ris` / CNKI blocks without crashing and log exact line warnings.
- If `/input/` has zero usable files or all files are corrupt/encrypted, write an honest `status: "unable_to_determine"` `result.json` explaining exact causes.
