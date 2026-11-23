# Evidence, Citation & Provenance Audit Report Template

## 1. Citation Coverage & Metadata Verification Ledger
- Total input references discovered: `N` | Cited in draft: `M` | Excluded with rationale: `K`
- Recency breakdown: Foundational (`F`), Representative (`R`), Recent (`C`)
- Table columns: `Ref ID | Cite Key | Authors | Year | Title | Venue | DOI/ID | Recency Role | Source Locator | Status`

## 2. Claim-to-Citation Mapping (`claim_citation_map`)
- Maps each factual claim in Background, Related Work, Research Gaps, and Contributions to exact `citation_ids`, `source_locators`, and `attribution_type` (`cited_fact` / `own_result` / `author_judgment` / `author_confirm`).

## 3. Research Gap Derivation Chain
- Shows how each `GAP` emerges from specific cited limitations, uncovered scenarios, or inter-study conflicts (or explicit `[作者判断]`/`[AUTHOR_JUDGMENT]`), and how this paper's work maps to it.

## 4. Contribution-to-Evidence Alignment Matrix
- Maps each contribution item to: `Scope Qualification | Linked Methods | Linked Figures/Tables | Linked Metrics | Own Evidence Locators | Status`.

## 5. Cross-Source Conflicts (`CONF-XX`) & Revision Change Records
- Lists metadata/numeric mismatches across `.bib`, PDFs, notes, and drafts with source precedence resolution and all edits made (`Before -> After | Basis Locator`).

## 6. Author Confirmation Questionnaire (`author_confirmation_items`)
- Closed-ended questions grouped by priority (`CRITICAL`, `IMPORTANT`, `RECOMMENDED`), 1-to-1 matched with inline `[待作者确认: GAP-XX]` / `[AUTHOR_CONFIRM: GAP-XX]` tags.

## 7. Mechanical Verification Metrics (`verify_intro_rw.py`)
- Automated 10-axis verification pass/fail summary, error count, warning count, and diagnostic checklist.
