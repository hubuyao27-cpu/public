#!/usr/bin/env python3
"""Standalone reusable self-test for nanopublication-extractor.

Verifies:
  1. Positive pipeline on examples/synthetic_paper.md + examples/benchmark_20_assertions.json:
     - 20 distinct assertions extracted, 1 Abstract/Results duplicate merged with corroboration preserved
     - Exact verbatim grounding, char offsets, and Trusty URN IDs
     - Export + round-trip validation of nanopubs.json, nanopubs.jsonld, nanopubs.trig, csv, md, result.json
  2. 8 Adversarial Negative Gates:
     G1. Compound/bundled assertion caught by ATOMICITY
     G2. Paraphrased/fabricated quote caught by EVIDENCE_GROUNDING
     G3. Whole-paragraph bloated quote caught by MINIMAL_SPAN
     G4. Fake page=1 on non-PDF input caught by LOCATOR_INTEGRITY
     G5. Passive-voice subject/object inversion caught by PREDICATE_AND_DIRECTION
     G6. Hallucinated numeric value & negation polarity flip caught by NUMERIC_NEGATION_FIDELITY
     G7. Cited prior work mislabeled author_direct_finding + direct_empirical caught by EPISTEMIC_CONSISTENCY
     G8. Missing/unreadable input directory degrades honestly to status="unable_to_determine"
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from common import load_json, save_json  # noqa: E402
from export_nanopubs import export_all, validate_trig_syntax  # noqa: E402
from ingest_source import ingest  # noqa: E402
from verify_nanopubs import verify_nanopubs  # noqa: E402


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def run_selftest() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nanopub_selftest_") as tmp:
        tmp_path = Path(tmp)
        in_dir = tmp_path / "input"
        work_dir = tmp_path / "work"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        work_dir.mkdir()

        bm = load_json(SKILL_ROOT / "examples" / "benchmark_20_assertions.json")
        shutil.copy2(SKILL_ROOT / "examples" / "synthetic_paper.md", in_dir / "synthetic_paper.md")
        save_json(in_dir / "case.json", {
            "case_id": bm["case_id"],
            "questions": bm["questions"],
            "output_contract": {
                "case_id": "string",
                "status": "string",
                "nanopublications": "array",
                "qa_answers": "array",
                "conflicts": "array",
                "sources": "array",
                "uncertainty": "array",
                "limitations": "array",
                "change_records": "array",
            },
        })
        (in_dir / "task.md").write_text(
            "# Synthetic Self-Test\nExtract atomic Nanopublications and answer Q1-Q3 with exact evidence.",
            encoding="utf-8",
        )

        # 1. Ingest (including early partial checkpoint)
        ingest_summary = ingest(in_dir, work_dir / "ingest", output_dir=out_dir)
        _assert(ingest_summary["status"] == "ok", f"Ingest failed: {ingest_summary}")
        _assert(ingest_summary["has_pdf_pages"] is False, "Markdown should have has_pdf_pages=False")
        _assert((out_dir / "result.json").exists() and load_json(out_dir / "result.json")["status"] == "partial", "Expected early partial checkpoint")

        source_index = load_json(work_dir / "ingest" / "source_index.json")
        case_brief = load_json(work_dir / "ingest" / "case_brief.json")

        # 2. Verify positive 20-assertion benchmark (21 raw -> 20 after BM_01 + BM_02_DUP_RESULTS merge)
        audited, v_report = verify_nanopubs(bm, source_index)
        _assert(v_report["error_count"] == 0, f"Positive benchmark had errors: {v_report['issues']}")
        _assert(v_report["raw_items_count"] == 21, f"Expected 21 raw items, got {v_report['raw_items_count']}")
        _assert(v_report["audited_nanopubs_count"] == 20, f"Expected 20 merged nanopubs, got {v_report['audited_nanopubs_count']}")
        _assert(v_report["merged_duplicates_count"] == 1, "Expected 1 duplicate merge (BM_01 + BM_02_DUP_RESULTS)")
        _assert(v_report["quote_grounding_rate"] == 1.0, "Expected 100% quote grounding rate")
        exp_pos = export_all(
            audited, source_index, case_brief, v_report, out_dir, qa_data=None, strict=False
        )
        _assert(exp_pos["status"] == "success", f"Expected success export, got {exp_pos}")
        pos_rj = load_json(out_dir / "result.json")
        _assert(pos_rj["sources"][0].get("field_provenance"), "Missing field_provenance in result.json['sources']")
        _assert(pos_rj["sources"][0].get("evidence_anchors"), "Missing evidence_anchors in result.json['sources']")
        _assert("Field-Level Metadata & Evidence Provenance" in (out_dir / "nanopub_report.md").read_text(encoding="utf-8"), "Missing Field-Level Metadata & Evidence Provenance in nanopub_report.md")
        _assert("High-Signal Candidate Sentences" in (work_dir / "ingest" / "source_digest.md").read_text(encoding="utf-8"), "Missing High-Signal Candidate Sentences in source_digest.md")

        # Verify auto-correction of mismatched paragraph_id when quote is verbatim in same source
        g_loc_fix = copy.deepcopy(bm["nanopublications"][0])
        g_loc_fix["temp_id"] = "LOC_AUTO_FIX"
        g_loc_fix["evidence_spans"][0]["locator"]["paragraph_id"] = "P_WRONG_ID"
        aud_fix, rep_fix = verify_nanopubs([g_loc_fix], source_index)
        _assert(rep_fix["error_count"] == 0, f"Auto-locator fix should pass with 0 errors, got {rep_fix['issues']}")
        _assert(aud_fix["nanopublications"][0]["provenance"]["evidence_spans"][0]["locator"]["paragraph_id"] != "P_WRONG_ID", "paragraph_id was not auto-corrected")

        # Verify corroborating spans preserved on merged NP
        merged_np = next(
            np for np in audited["nanopublications"]
            if "BM_01" in np["pubinfo"]["merged_from_temp_ids"]
        )
        _assert(len(merged_np["provenance"]["evidence_spans"]) == 2, "Merged duplicate must preserve both Abstract & Results spans")
        _assert(merged_np["pubinfo"]["corroboration_count"] == 2, "Expected corroboration_count=2 on merged NP")

        # Build illustrative QA answers for Q1-Q3 (including Q3 unreported boundary)
        np_id_oer = merged_np["nanopub_id"]
        np_her = next(np for np in audited["nanopublications"] if "BM_16_NULL" in np["pubinfo"]["merged_from_temp_ids"])
        qa_answers = [
            {
                "question_id": "Q1",
                "question": bm["questions"][0]["question"],
                "answer_status": "answered",
                "answer": "Meso-NiFeOOH requires 218 ± 4 mV at 10 mA cm^-2 in 1.0 M KOH at 298 K, outperforming commercial IrO2 (312 ± 6 mV) by 94 mV.",
                "supporting_nanopub_ids": [np_id_oer],
                "evidence_spans": merged_np["provenance"]["evidence_spans"][:1],
                "uncertainty_note": None,
            },
            {
                "question_id": "Q2",
                "question": bm["questions"][1]["question"],
                "answer_status": "answered",
                "answer": "No; Meso-NiFeOOH showed no statistically significant HER activity in 1.0 M KOH (overpotential > 480 mV at 10 mA cm^-2, p = 0.42).",
                "supporting_nanopub_ids": [np_her["nanopub_id"]],
                "evidence_spans": np_her["provenance"]["evidence_spans"],
                "uncertainty_note": None,
            },
            {
                "question_id": "Q3",
                "question": bm["questions"][2]["question"],
                "answer_status": "unreported_in_source",
                "answer": "Unreported in the source paper: no gas chromatography Faradaic efficiency measurement at 353 K is reported (353 K only appears for NaOH silica etching).",
                "supporting_nanopub_ids": [],
                "evidence_spans": [],
                "uncertainty_note": "Target metric (GC Faradaic efficiency at 353 K) is absent from source text.",
            },
        ]

        # 3. Export + strict round-trip validation
        exp_summary = export_all(
            audited_bundle=audited,
            source_index=source_index,
            case_brief=case_brief,
            verify_report=v_report,
            out_dir=out_dir,
            qa_data=qa_answers,
            strict=True,
        )
        _assert(exp_summary["status"] == "success", f"Unexpected export status: {exp_summary}")
        _assert(exp_summary["trig_roundtrip_valid"] is True, "TriG round-trip validation failed")
        for fn in ("nanopubs.json", "nanopubs.jsonld", "nanopubs.trig", "nanopubs.csv", "nanopub_report.md", "result.json"):
            _assert((out_dir / fn).exists(), f"Missing exported file {fn}")
        trig_ok, trig_errs = validate_trig_syntax((out_dir / "nanopubs.trig").read_text(encoding="utf-8"))
        _assert(trig_ok, f"TriG validation errors: {trig_errs}")

        # 4. Run 8 Adversarial Negative Gates
        adv_results: Dict[str, str] = {}

        # G1: Compound / bundled claim
        g1 = copy.deepcopy(bm["nanopublications"][0])
        g1["temp_id"] = "ADV_G1"
        g1["claim_text"] = "Meso-NiFeOOH achieves 218 mV overpotential; moreover, it reduces resistance and outperforms IrO2 across all electrolytes."
        _, r1 = verify_nanopubs([g1], source_index)
        _assert(any(i["code"] == "compound_clause_detected" for i in r1["issues"]), "G1 failed to catch compound clause")
        adv_results["G1_ATOMICITY"] = "CAUGHT"

        # G2: Fabricated/paraphrased quote
        g2 = copy.deepcopy(bm["nanopublications"][0])
        g2["temp_id"] = "ADV_G2"
        g2["evidence_spans"][0]["quote"] = "Meso-NiFeOOH achieved an extraordinary 180 mV overpotential under acidic conditions."
        _, r2 = verify_nanopubs([g2], source_index)
        _assert(any(i["code"] == "quote_not_grounded_in_source" for i in r2["issues"]), "G2 failed to catch ungrounded quote")
        adv_results["G2_EVIDENCE_GROUNDING"] = "CAUGHT"

        # G3: Whole-paragraph bloated span (> 450 chars & > 2 sentences)
        long_p = source_index["documents"][0]["paragraphs"][4]["text"]  # P005 > 450 chars, 3 sentences
        g3 = copy.deepcopy(bm["nanopublications"][10])
        g3["temp_id"] = "ADV_G3"
        g3["evidence_spans"][0]["quote"] = long_p
        _, r3 = verify_nanopubs([g3], source_index)
        _assert(any(i["code"] == "evidence_span_too_long" for i in r3["issues"]), "G3 failed to catch bloated evidence span")
        adv_results["G3_MINIMAL_SPAN"] = "CAUGHT"

        # G4: Fake PDF page=1 on non-PDF Markdown input
        g4 = copy.deepcopy(bm["nanopublications"][0])
        g4["temp_id"] = "ADV_G4"
        g4["evidence_spans"][0]["locator"] = {"locator_type": "pdf_page", "page": 1, "paragraph_id": "P002"}
        _, r4 = verify_nanopubs([g4], source_index)
        _assert(any(i["code"] == "fake_pdf_page_number" for i in r4["issues"]), "G4 failed to catch fake PDF page number")
        adv_results["G4_LOCATOR_INTEGRITY"] = "CAUGHT"

        # G5: Passive-voice subject/object inversion
        g5 = {
            "temp_id": "ADV_G5",
            "claim_text": "Pure NiOOH increases 0.1 mM FeCl3 turnover frequency.",
            "subject": {"label": "turnover frequency", "entity_type": "Property_Or_Metric"},
            "predicate": "increases",
            "object": {"label": "0.1 mM FeCl3", "entity_type": "Compound"},
            "conditions": {"experimental_system": "alkaline", "parameters": [], "comparator": null if False else None, "scope_qualifiers": []},
            "quantitative_values": [],
            "polarity": "positive",
            "epistemic_status": "author_direct_finding",
            "evidence_spans": [{
                "source_id": "synthetic_paper",
                "quote": "Addition of 0.1 mM FeCl3 to the electrolyte further increased the turnover frequency (TOF) of pure NiOOH by 4.3-fold at 1.50 V vs. RHE.",
                "evidence_type": "direct_empirical",
                "locator": {"locator_type": "line_range", "paragraph_id": "P005"},
            }],
        }
        # Also test exact passive marker: "<subj> was derived from <obj>" or "<subj> is inhibited by <obj>"
        # Inject passive check against predicate_vocab passive marker on synthetic sentence present in P009 ("required by" / "is inhibited by")
        # Construct using a sentence from synthetic_paper that matches passive_markers: "derived from" vs "was raised" -> use P003 "derived from" or test passive marker on existing text
        # Wait: let's inject a temp paragraph with "B is inhibited by A" to verify exact passive_markers check deterministically
        si_passive = copy.deepcopy(source_index)
        si_passive["documents"][0]["paragraphs"].append({
            "paragraph_id": "P_PASSIVE",
            "section": "Test",
            "page": None,
            "locator_type": "line_range",
            "text": "Catalyst degradation is inhibited by mesoporous silica removal.",
            "sentences": [{"sentence_id": "S999", "text": "Catalyst degradation is inhibited by mesoporous silica removal.", "char_start": 0, "char_end": 63}],
        })
        si_passive["documents"][0]["sentences"].append({"sentence_id": "S999", "paragraph_id": "P_PASSIVE", "text": "Catalyst degradation is inhibited by mesoporous silica removal."})
        g5_p = {
            "temp_id": "ADV_G5",
            "claim_text": "Catalyst degradation inhibits mesoporous silica removal.",
            "subject": {"label": "Catalyst degradation", "entity_type": "Process"},
            "predicate": "inhibits",
            "object": {"label": "mesoporous silica removal", "entity_type": "Process"},
            "conditions": {"experimental_system": "test", "parameters": [], "comparator": None, "scope_qualifiers": []},
            "quantitative_values": [],
            "polarity": "positive",
            "epistemic_status": "author_direct_finding",
            "evidence_spans": [{
                "source_id": "synthetic_paper",
                "quote": "Catalyst degradation is inhibited by mesoporous silica removal.",
                "evidence_type": "direct_empirical",
                "locator": {"locator_type": "line_range", "paragraph_id": "P_PASSIVE"},
            }],
        }
        _, r5 = verify_nanopubs([g5_p], si_passive)
        _assert(any(i["code"] == "likely_subject_object_inversion" for i in r5["issues"]), f"G5 failed to catch passive inversion: {r5['issues']}")
        adv_results["G5_PASSIVE_DIRECTION"] = "CAUGHT"

        # G6: Hallucinated numeric value & negation polarity flip
        g6 = copy.deepcopy(bm["nanopublications"][15])  # BM_16_NULL (HER null result)
        g6["temp_id"] = "ADV_G6"
        g6["claim_text"] = "Meso-NiFeOOH catalyzes HER with an overpotential of 195 mV."
        g6["polarity"] = "positive"
        g6["quantitative_values"] = [{"metric": "overpotential", "operator": "=", "value": "195", "unit": "mV"}]
        _, r6 = verify_nanopubs([g6], source_index)
        codes6 = {i["code"] for i in r6["issues"]}
        _assert("ungrounded_numeric_value" in codes6 and "negation_polarity_mismatch" in codes6, f"G6 missed numeric/negation error: {codes6}")
        adv_results["G6_NUMERIC_AND_NEGATION"] = "CAUGHT"

        # G7: Cited prior work mislabeled author_direct_finding + direct_empirical
        g7 = copy.deepcopy(bm["nanopublications"][5])  # BM_06 (cited prior work)
        g7["temp_id"] = "ADV_G7"
        g7["epistemic_status"] = "author_direct_finding"
        g7["evidence_spans"][0]["evidence_type"] = "direct_empirical"
        _, r7 = verify_nanopubs([g7], source_index)
        _assert(any(i["code"] == "cited_prior_work_mislabeled_direct_finding" for i in r7["issues"]), "G7 failed to catch cited prior work mislabeling")
        adv_results["G7_EPISTEMIC_CONSISTENCY"] = "CAUGHT"

        # G8: Missing / unreadable input directory honest degradation
        empty_in = tmp_path / "empty_input"
        empty_in.mkdir()
        save_json(empty_in / "case.json", {"case_id": "missing_case_test", "input_file": "missing_paper.pdf"})
        empty_work = tmp_path / "empty_work"
        empty_out = tmp_path / "empty_out"
        ing_empty = ingest(empty_in, empty_work / "ingest")
        _assert(ing_empty["workflow_mode"] == "degraded_missing_or_unreadable", "G8 failed to detect degraded mode")
        si_empty = load_json(empty_work / "ingest" / "source_index.json")
        cb_empty = load_json(empty_work / "ingest" / "case_brief.json")
        aud_empty, rep_empty = verify_nanopubs([], si_empty)
        exp_empty = export_all(aud_empty, si_empty, cb_empty, rep_empty, empty_out, strict=False)
        _assert(exp_empty["status"] == "unable_to_determine", f"G8 expected unable_to_determine, got {exp_empty['status']}")
        adv_results["G8_HONEST_DEGRADATION"] = "CAUGHT"

        summary = {
            "selftest": "PASSED",
            "benchmark_assertions_tested": v_report["raw_items_count"],
            "canonical_nanopubs_exported": v_report["audited_nanopubs_count"],
            "duplicates_merged": v_report["merged_duplicates_count"],
            "quote_grounding_rate": v_report["quote_grounding_rate"],
            "trig_roundtrip_valid": True,
            "adversarial_gates_passed": f"{len(adv_results)}/8",
            "adversarial_details": adv_results,
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return summary


if __name__ == "__main__":
    run_selftest()
