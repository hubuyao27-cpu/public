# Minimal Illustrative Invocation Example (`nanopublication-extractor`)

> **Notice**: All inputs and outputs below are **illustrative synthetic examples** demonstrating the CLI workflow and output formats (`nanopubs.json`, `nanopubs.jsonld`, `nanopubs.trig`, `result.json`). They are NOT formal competition test cases or pre-baked answers.

---

## 1. Sample Illustrative Input

Assume `/input/` contains `case.json`, `task.md`, and `synthetic_paper.md` (excerpt from `examples/synthetic_paper.md`):

```markdown
## Abstract
We report a hierarchical mesoporous nickel-iron oxyhydroxide electrocatalyst (Meso-NiFeOOH) ...
In 1.0 M KOH at 298 K, Meso-NiFeOOH requires an overpotential of 218 ± 4 mV to achieve 10 mA cm^-2, outperforming commercial IrO2 (312 ± 6 mV) by 94 mV.
```

---

## 2. Step-by-Step Invocation

```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
mkdir -p /app/work /app/output

# Step 1: Ingest source & generate sentence-indexed digest
python3 "$SKILL_DIR/scripts/ingest_source.py" \
  --input-dir /input \
  --out-dir /app/work/ingest

# Step 2: Author /app/work/raw_nanopubs.json (atomic assertions + verbatim quotes)
# and run 10-gate deterministic verifier
python3 "$SKILL_DIR/scripts/verify_nanopubs.py" \
  --input /app/work/raw_nanopubs.json \
  --source-index /app/work/ingest/source_index.json \
  --out /app/work/audited_nanopubs.json \
  --report /app/work/verification_report.json \
  --strict

# Step 3: Export JSON, JSON-LD, RDF TriG, CSV, Markdown report, and Harbor result.json
python3 "$SKILL_DIR/scripts/export_nanopubs.py" \
  --audited /app/work/audited_nanopubs.json \
  --source-index /app/work/ingest/source_index.json \
  --case-brief /app/work/ingest/case_brief.json \
  --verify-report /app/work/verification_report.json \
  --out-dir /app/output \
  --strict
```

---

## 3. Excerpted Illustrative Outputs

### A. `nanopubs.json` (Excerpt — 1 Atomic Nanopublication)

```json
{
  "nanopub_id": "urn:nanopub:RA...illustrative_sha256_hash...",
  "head": {
    "nanopub_uri": "https://w3id.org/np/arena/RA...illustrative_sha256_hash...",
    "assertion_graph": "https://w3id.org/np/arena/RA...#assertion",
    "provenance_graph": "https://w3id.org/np/arena/RA...#provenance",
    "pubinfo_graph": "https://w3id.org/np/arena/RA...#pubinfo"
  },
  "assertion": {
    "claim_text": "Meso-NiFeOOH outperforms commercial IrO2 by 94 mV in OER overpotential at 10 mA cm^-2 in 1.0 M KOH at 298 K.",
    "subject": {"label": "Meso-NiFeOOH", "entity_type": "Material", "aliases": [], "identifier": null},
    "predicate": "outperforms",
    "predicate_uri": "https://w3id.org/np/o/ntemplate/ext/outperforms",
    "object": {"label": "commercial IrO2", "entity_type": "Material", "aliases": [], "identifier": null},
    "conditions": {
      "experimental_system": "1.0 M KOH at 298 K",
      "parameters": ["10 mA cm^-2", "94 mV lower overpotential"],
      "comparator": "commercial IrO2 (312 ± 6 mV)",
      "scope_qualifiers": []
    },
    "quantitative_values": [{"metric": "overpotential advantage", "operator": "=", "value": "94", "unit": "mV", "uncertainty_or_stat": null}],
    "polarity": "positive",
    "epistemic_status": "author_direct_finding"
  },
  "provenance": {
    "evidence_spans": [
      {
        "source_id": "synthetic_paper",
        "quote": "Meso-NiFeOOH requires an overpotential of 218 ± 4 mV to achieve 10 mA cm^-2, outperforming commercial IrO2 (312 ± 6 mV) by 94 mV.",
        "evidence_type": "direct_empirical",
        "locator": {
          "locator_type": "line_range",
          "paragraph_id": "P001",
          "sentence_id": "S002",
          "section": "Abstract",
          "char_start": 161,
          "char_end": 298
        }
      }
    ],
    "confidence": {"level": "high", "score": 0.9, "rationale": "directness=1.00, quote_match=1.00, conditions=1.00, numeric_fidelity=1.00, corroboration_locs=1"}
  }
}
```

### B. `nanopubs.trig` (Illustrative RDF 1.1 TriG Excerpt)

```turtle
@prefix np: <http://www.nanopub.org/nschema#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<https://w3id.org/np/arena/RAexample#Head> {
  <https://w3id.org/np/arena/RAexample> a np:Nanopublication ;
    np:hasAssertion <https://w3id.org/np/arena/RAexample#assertion> ;
    np:hasProvenance <https://w3id.org/np/arena/RAexample#provenance> ;
    np:hasPublicationInfo <https://w3id.org/np/arena/RAexample#pubinfo> .
}
```
