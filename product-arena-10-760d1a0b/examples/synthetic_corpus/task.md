# Synthetic Illustrative Example Task (NOT a formal benchmark case)

> `_synthetic_example_only: true` — This synthetic two-paper mini-corpus demonstrates interface compliance, JATS XML formula reconciliation, two-tier entity alignment (merge vs do-not-merge), incremental update ID preservation, and executable graph queries.

## Objective
1. Read `corpus-manifest.json`, `primary-material.json`, `secondary-material.json`, `material.json`, and `secondary-publisher.xml`.
2. Isolate per-paper metadata: root `case.json` DOI/title/authors belong ONLY to `paper_primary`.
3. Extract grounded entities and directed conditional relations with JATS `paragraph_id` locators (no PDF pages exist).
4. Record both valid alias merges and explicit do-not-merge decisions (distinct physical samples, experimental conditions, and homonyms).
5. Execute at least 3 structured graph queries and verify all quotes mechanically.
