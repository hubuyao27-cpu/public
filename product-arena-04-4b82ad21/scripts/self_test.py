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
        print(json.dumps({"self_test": "passed", "case_id": rj["case_id"], "deliverables": len(rj["deliverable_files"])}))


if __name__ == "__main__":
    main()
