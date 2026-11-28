#!/usr/bin/env python3
"""In-package smoke self-test for sci-intro-rw-writer.

Verifies prepare_workspace -> query_sources -> finalize_workspace end-to-end
inside an isolated TemporaryDirectory with zero leftover files.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    sdir = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp, work, out = root / "input", root / "work", root / "output"
        inp.mkdir(parents=True)

        (inp / "case.json").write_text(
            json.dumps(
                {
                    "case_id": "selftest-01",
                    "language": "en",
                    "citation_style": "ieee_numeric",
                    "output_contract": ["case_id", "status", "manuscript_markdown", "references", "sources", "uncertainty", "change_records"],
                },
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        (inp / "task.md").write_text("Draft Introduction and Related Work from refs.bib and own_results.md.", encoding="utf-8", newline="\n")
        (inp / "refs.bib").write_text(
            "@article{Lee2022Flow,\n  author={Lee, Amy},\n  title={Fast Flow Solver},\n  journal={JFM},\n  year={2022}\n}\n"
            "@article{Park2024Grid,\n  author={Park, Ben},\n  title={Adaptive Grid Refinement},\n  journal={Journal of Fluid Mechanics},\n  year={2024}\n}\n",
            encoding="utf-8",
            newline="\n",
        )
        (inp / "lit_summary.md").write_text(
            "# Notes\n- [R01] Lee2022Flow: 15x speedup, limited to steady laminar flow.\n- [R02] Park2024Grid: handles turbulent wakes, high memory overhead.\n",
            encoding="utf-8",
            newline="\n",
        )
        (inp / "results.md").write_text(
            "Method Section 2.1, Fig. 1, Table 1: achieves 2.4% L2 error at 22.5x speedup in unsteady wakes.\n",
            encoding="utf-8",
            newline="\n",
        )

        p1 = subprocess.run(
            [sys.executable, "-B", str(sdir / "prepare_workspace.py"), "--input-dir", str(inp), "--work-dir", str(work), "--out-dir", str(out)],
            capture_output=True,
            text=True,
        )
        assert p1.returncode == 0, f"prepare_workspace failed: {p1.stderr}\n{p1.stdout}"

        pq = subprocess.run(
            [sys.executable, "-B", str(sdir / "query_sources.py"), "--extracted-dir", str(work / "extracted"), "--section", "Limitations", "--json"],
            capture_output=True,
            text=True,
        )
        assert pq.returncode == 0 and json.loads(pq.stdout)["count"] >= 1, f"query_sources failed: {pq.stdout}"

        skel = json.loads((work / "section_payload_skeleton.json").read_text(encoding="utf-8"))
        ms = (
            "## 1. Introduction\n"
            "Unsteady wake simulation faces a trade-off between solver throughput and memory footprint [R01, R02].\n\n"
            "## 2. Related Work\n"
            "### 2.1 Fixed-Grid Acceleration\nLee et al. achieved 15x speedup in steady laminar regimes but fail in unsteady wakes [R01].\n"
            "### 2.2 Adaptive Discretization\nPark et al. capture unsteady wakes at high memory cost [R02].\n"
            "In contrast to prior routes, our method couples reduced-order blocks with adaptive cells.\n\n"
            "## 3. Contributions\n"
            "We achieve 2.4% L2 error at 22.5x speedup in unsteady wakes (Section 2.1, Fig. 1, Table 1).\n"
        )
        (work / "manuscript.md").write_text(ms, encoding="utf-8", newline="\n")
        skel.update(
            {
                "status": "completed",
                "manuscript_file": str(work / "manuscript.md"),
                "research_background": {"domain_lineage": "Flow solvers", "concrete_problem": "Unsteady wake cost", "technical_constraints": ["Memory vs speed"], "why_now": "Wake control", "evidence_locators": ["lit_summary.md:L2"], "citation_ids": ["R01"]},
                "related_work_groups": [
                    {"group_id": "G1", "route_name": "Fixed-Grid", "grouping_axis": "Discretization", "comparison_dimensions": ["Speed", "Limits"], "works_covered": [{"ref_id": "R01", "core_conclusion": "15x speedup", "applicability_conditions": "Steady laminar", "limitations": "Fails in unsteady wakes", "source_locator": "lit_summary.md:L2"}], "route_tradeoff_summary": "Fast but laminar-only."},
                    {"group_id": "G2", "route_name": "Adaptive Grid", "grouping_axis": "Discretization", "comparison_dimensions": ["Regime", "Limits"], "works_covered": [{"ref_id": "R02", "core_conclusion": "Unsteady wakes", "applicability_conditions": "Turbulent", "limitations": "High memory", "source_locator": "lit_summary.md:L3"}], "route_tradeoff_summary": "Accurate but memory-heavy."},
                ],
                "research_gaps": [{"gap_id": "GAP-S1", "gap_statement": "Unsteady wake accuracy vs memory", "derivation_type": "shared_limitation", "supporting_ref_ids": ["R01", "R02"], "supporting_evidence_locators": ["lit_summary.md:L2", "lit_summary.md:L3"], "how_this_work_addresses": "Hybrid adaptive blocks", "attribution": "literature_derived"}],
                "contributions": [{"contrib_id": "C01", "claim_text": "2.4% L2 error at 22.5x speedup.", "scope_qualification": "Unsteady wake test case only", "linked_methods": ["Section 2.1"], "linked_figures_tables": ["Fig. 1", "Table 1"], "linked_metrics": ["2.4%", "22.5x"], "own_evidence_locators": ["results.md:L1"], "verification_status": "verified"}],
            }
        )
        (work / "section_payload.json").write_text(json.dumps(skel, indent=2), encoding="utf-8", newline="\n")

        p2 = subprocess.run(
            [sys.executable, "-B", str(sdir / "finalize_workspace.py"), "--work-dir", str(work), "--out-dir", str(out)],
            capture_output=True,
            text=True,
        )
        assert p2.returncode == 0, f"finalize_workspace failed: {p2.stdout}\n{p2.stderr}"
        rj = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert rj["status"] == "completed" and "[1], [2]" in rj["manuscript_markdown"]
        print(json.dumps({"self_test_1": "passed", "case_id": rj["case_id"], "deliverables": len(rj["deliverable_files"])}))

    # Scenario 2: Realistic multi-PDF + BibTeX/RIS conflict + CSV rounding + [R1] + minimal payload zero-rework auto-hydration
    with tempfile.TemporaryDirectory() as td2:
        root2 = Path(td2)
        inp2, work2, out2 = root2 / "input", root2 / "work", root2 / "output"
        inp2.mkdir(parents=True)
        (inp2 / "case.json").write_text(
            json.dumps(
                {
                    "case_id": "selftest-02-field-evidence",
                    "language": "en",
                    "citation_style": "ieee_numeric",
                    "output_contract": ["case_id", "status", "manuscript_markdown", "references", "sources", "conflicts", "uncertainty"],
                },
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        (inp2 / "task.md").write_text("Write Introduction and Related Work using attached PDF, bib, ris, and csv metrics.", encoding="utf-8", newline="\n")
        (inp2 / "refs.bib").write_text(
            "@article{Gao2023Catal,\n  author={Gao, Lin and Xu, Bo},\n  title={Nanoporous Nickel Electrodes for CO2 Reduction},\n  journal={Journal of Catalysis},\n  year={2022},\n  volume={418},\n  pages={101-112}\n}\n",
            encoding="utf-8",
            newline="\n",
        )
        (inp2 / "refs.ris").write_text(
            "TY  - JOUR\nAU  - Gao, Lin\nAU  - Xu, Bo\nTI  - Nanoporous Nickel Electrodes for CO2 Reduction\nJO  - J. Catal.\nPY  - 2023\nER  -\n",
            encoding="utf-8",
            newline="\n",
        )
        pdf_name = "gao2023.pdf"
        try:
            try:
                import pymupdf as fitz  # type: ignore
            except ImportError:
                import fitz  # type: ignore
            doc = fitz.open()
            p1 = doc.new_page()
            p1.insert_text((72, 72), "Title: Nanoporous Nickel Electrodes for CO2 Reduction\nAuthors: Gao, Lin; Xu, Bo\nYear: 2023\nVenue: Journal of Catalysis\nDOI: 10.1016/j.jcat.2023.001\n")
            p2 = doc.new_page()
            p2.insert_text((72, 72), "Limitations\nNanoporous nickel degrades above 350 mA/cm2 due to pore flooding and carbonate buildup.\n")
            doc.save(str(inp2 / pdf_name))
            doc.close()
        except ImportError:
            pdf_name = "gao2023.md"
            (inp2 / pdf_name).write_text(
                "# Title: Nanoporous Nickel Electrodes for CO2 Reduction\nAuthors: Gao, Lin; Xu, Bo\nYear: 2023\nVenue: Journal of Catalysis\nDOI: 10.1016/j.jcat.2023.001\n"
                "## Limitations\nNanoporous nickel degrades above 350 mA/cm2 due to pore flooding and carbonate buildup.\n",
                encoding="utf-8",
                newline="\n",
            )
        (inp2 / "own_metrics.csv").write_text("method,fe_co_pct,current_ma_cm2\nHollowNi,94.62,480\n", encoding="utf-8", newline="\n")
        (inp2 / "own_paper_notes.md").write_text("Section 3.1, Table 2: HollowNi reaches 94.62% FE(CO) at 480 mA/cm2.\n", encoding="utf-8", newline="\n")

        p1 = subprocess.run([sys.executable, "-B", str(sdir / "prepare_workspace.py"), "--input-dir", str(inp2), "--work-dir", str(work2), "--out-dir", str(out2)], capture_output=True, text=True)
        assert p1.returncode == 0, f"prepare_workspace s2 failed: {p1.stderr}\n{p1.stdout}"

        ms2 = (
            "## 1. Introduction\n"
            "High-rate CO2 electrolysis degrades rapidly under pore flooding [R1].\n\n"
            "## 2. Related Work\n"
            "### 2.1 Nanoporous Electrode Engineering\n"
            "Gao et al. achieved high selectivity, yet nanoporous nickel degrades above 350 mA/cm2 due to pore flooding [R1].\n"
            "In contrast, our HollowNi architecture prevents flooding at 480 mA/cm2.\n\n"
            "## 3. Contributions\n"
            "We achieve 94.6% FE(CO) at 480 mA/cm2 (Table 2).\n"
        )
        (work2 / "manuscript.md").write_text(ms2, encoding="utf-8", newline="\n")
        minimal_payload = {
            "case_id": "selftest-02-field-evidence",
            "status": "completed",
            "language": "en",
            "citation_style": "ieee_numeric",
            "manuscript_file": str(work2 / "manuscript.md"),
            "research_gaps": [
                {"gap_id": "GAP-S1", "gap_statement": "Flooding limits high-current electrolysis above 350 mA/cm2.", "derivation_type": "shared_limitation", "supporting_ref_ids": ["R1"], "supporting_evidence_locators": [f"{pdf_name}:p2_L2"], "how_this_work_addresses": "HollowNi hydrophobic walls", "attribution": "literature_derived"}
            ],
            "contributions": [
                {"contrib_id": "C01", "claim_text": "94.6% FE(CO) at 480 mA/cm2.", "scope_qualification": "Zero-gap cell at 480 mA/cm2", "linked_methods": ["Section 3.1"], "linked_figures_tables": ["Table 2"], "linked_metrics": ["94.6%"], "own_evidence_locators": ["own_paper_notes.md:L1"], "verification_status": "verified"}
            ],
        }
        (work2 / "section_payload.json").write_text(json.dumps(minimal_payload, indent=2), encoding="utf-8", newline="\n")
        p2 = subprocess.run([sys.executable, "-B", str(sdir / "finalize_workspace.py"), "--work-dir", str(work2), "--out-dir", str(out2)], capture_output=True, text=True)
        assert p2.returncode == 0, f"finalize_workspace s2 failed: {p2.stdout}\n{p2.stderr}"
        rj2 = json.loads((out2 / "result.json").read_text(encoding="utf-8"))
        assert rj2["status"] == "completed" and "[1]" in rj2["manuscript_markdown"] and "[R1]" not in rj2["manuscript_markdown"], rj2["manuscript_markdown"]
        assert any(s.get("kind") == "reference" and s.get("field_provenance") for s in rj2["sources"]), rj2["sources"]
        assert any(s.get("kind") == "claim_citation" and s.get("evidence_quote") for s in rj2["sources"]), rj2["sources"]
        audit2 = (out2 / "evidence_and_citation_audit.md").read_text(encoding="utf-8")
        assert "Field-Level Metadata Provenance" in audit2 and "pore flooding" in audit2, audit2
        pmap2 = json.loads((out2 / "provenance_map.json").read_text(encoding="utf-8"))
        assert pmap2.get("field_provenance_by_ref", {}).get("R01"), pmap2
        print(json.dumps({"self_test_2": "passed", "case_id": rj2["case_id"], "deliverables": len(rj2["deliverable_files"])}))


if __name__ == "__main__":
    main()
