# Edge Cases & Exception Recovery Playbook (`academic-citation-manager`)

## 1. Missing Library Metadata Fields (Volume / Issue / Pages / DOI)
- **Rule**: Never invent missing DOIs, page numbers, journal volumes, or publication years.
- `sync_and_format.py` omits empty fields cleanly without emitting `"None"`, `"null"`, empty parentheses `()`, or stray commas, logs missing mandatory style fields into `missing_fields` per entry, and reports them in `author_confirmation_items[]` and `uncertainty[]`.

## 2. Offline / Failed DOI Verification
- In sandboxed execution environments without external internet access, live DOI resolution cannot be confirmed via Crossref/doi.org.
- **Action**: Validate DOI syntactic format (`^10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$`), check internal consistency against `library_snapshot.json`, and explicitly record in `uncertainty` / `limitations` that live external DOI HTTP resolution was unavailable offline.

## 3. Potentially Fabricated or Unresolved Citations
- **Condition**: An in-text marker (e.g., `[99]`, `(Ghost et al., 2029)`, `\cite{missing_key}`, or `[REF]`) does not resolve to any record in `canonical_library.json` or embedded reference list, OR an existing reference's author/year contradicts the in-text statement.
- **Action**: Never invent a fake reference to fill the gap. Set `action: "flag_unresolved"` (or `match_verdict: "mismatch_or_unsupported"` / `"unresolved_ref"`), insert an explicit inline confirmation tag `[待确认引用: ...]` / `[UNVERIFIED_CITE: ...]`, and create an entry in `author_confirmation_items[]`.

## 4. Same-Author Same-Year Papers & Ambiguous Deduplication
- **Anti-False-Merge Guard**: Two papers with the same first author and same year (`Smith, 2021`) whose normalized titles have similarity `< 0.75` and share no DOI/arXiv/PMID MUST remain distinct (`REF-xxx` and `REF-yyy`).
- **Author-Year Disambiguation**: `sync_and_format.py` automatically appends deterministic suffixes (`2021a`, `2021b`) across all in-text labels and bibliography entries.
- **Ambiguous Pairs (`0.75 <= sim < 0.90`)**: Surface in `dedup_report.json["ambiguous_candidates"]` and log in `author_confirmation_items[]` unless unambiguously resolved via `dedup_overrides`.

## 5. Preprint vs. Formal Published Edition & Bilingual Records
- When a preprint (e.g., `arXiv:2305.01234`, `bioRxiv`) and a formal journal/conference version share a DOI, arXiv ID, or high title+author similarity, merge them into one canonical entry keeping `journal_article` / `conference_paper` metadata primary while retaining `preprint` IDs/URLs in `alternate_ids` and `merged_from`.
- Never merge different papers simply because they share an author or topic; record the exact merge tier and rationale in `dedup_report.json`.

## 6. Claim-to-Source Verification Without Abstracts
- If the library record contains only bibliographic fields (no `abstract` or full text), do NOT pretend the claim was verified against paper findings. Use `match_verdict: "insufficient_source_text"` and note `"Metadata matched, but library export lacks abstract/full text to verify empirical claim."`

## 7. Corrupt, Encrypted, or Unsupported Manuscript / Library Files
- Missing referenced files, binary/corrupt `.bib`/`.ris`/`.docx` files, or encrypted PDFs must be reported honestly in `uncertainty[]` and `limitations[]` with `status: "unable_to_determine"` (or `"partial"` if partial valid inputs exist).
