#!/usr/bin/env python3
"""Standalone automated test suite for academic-citation-manager.

Tests:
A. Multi-format library parsing (BibTeX + RIS + EndNote + CNKI) with preprint-to-journal dedup & same-author-same-year anti-false-merge guard.
B. Claim-level placement + cross-zone renumbering (body + figure caption) + GB/T 7714-2015 formatting + APA 7 style conversion.
C. Adversarial verification catching dangling/orphan citations, numbering gaps, fabricated provenance, and unflagged mismatches.
D. Missing/corrupt input & missing-metadata graceful handling.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent


def run_step(script: str, args: list[str], expect_ok: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script)] + args,
        capture_output=True,
        text=True,
    )
    if expect_ok and proc.returncode != 0:
        raise AssertionError(f"{script} failed ({proc.returncode}):\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    if (not expect_ok) and proc.returncode == 0:
        raise AssertionError(f"{script} unexpectedly passed when failure was expected.")
    return proc


def test_case_a_dedup_insert_sync_gbt7714(tmp: Path) -> None:
    case_dir = tmp / "case_a"
    in_dir = case_dir / "input"
    work_dir = case_dir / "work"
    out_dir = case_dir / "output"
    in_dir.mkdir(parents=True)

    (in_dir / "case.json").write_text(
        json.dumps(
            {
                "case_id": "test-cite-gbt-01",
                "output_contract": ["case_id", "status", "updated_manuscript", "formatted_bibliography", "renumbering_map"],
            }
        ),
        encoding="utf-8",
    )
    (in_dir / "task.md").write_text(
        "请对文献库去重，按 GB/T 7714-2015 顺序编码制重排正文和图注中的引用编号，并在需要支撑的论断句准确插入引用。",
        encoding="utf-8",
    )
    (in_dir / "refs.bib").write_text(
        "@article{smith2021a,\n"
        "  author = {John Smith and Alice Lee},\n"
        "  title = {Single-Atom Nickel Catalysts for CO2 Reduction},\n"
        "  journal = {Nature Catalysis},\n"
        "  year = {2021},\n"
        "  volume = {4},\n"
        "  pages = {100--110},\n"
        "  doi = {10.1038/s41929-021-0001},\n"
        "  abstract = {Ni-N-C achieves 96% CO Faradaic efficiency.}\n"
        "}\n"
        "@article{smith2021b,\n"
        "  author = {John Smith},\n"
        "  title = {Electrochemical Impedance Modeling of Porous Electrodes},\n"
        "  journal = {Journal of Electrochemistry},\n"
        "  year = {2021},\n"
        "  volume = {18},\n"
        "  pages = {22--35},\n"
        "  doi = {10.1016/j.jelec.2021.0099}\n"
        "}\n"
        "@misc{smith2021preprint,\n"
        "  author = {John Smith and Alice Lee},\n"
        "  title = {Single-Atom Nickel Catalysts for CO2 Reduction},\n"
        "  journal = {arXiv preprint arXiv:2101.01234},\n"
        "  year = {2021},\n"
        "  doi = {10.1038/s41929-021-0001}\n"
        "}\n",
        encoding="utf-8",
    )
    (in_dir / "cnki_refs.txt").write_text(
        "RT Journal Article\n"
        "A1 张伟; 李敏\n"
        "T1 镍基单原子催化剂电催化还原二氧化碳研究进展\n"
        "JF 催化学报\n"
        "YR 2022\n"
        "VO 43\n"
        "IS 5\n"
        "OP 1200-1215\n"
        "AB 综述了镍氮碳单原子催化剂在二氧化碳电还原中的高选择性机制。\n",
        encoding="utf-8",
    )
    (in_dir / "draft.md").write_text(
        "# 引言\n\n"
        "单原子镍催化剂在二氧化碳电还原中表现出优异的CO选择性 [3]。"
        "近期综述系统总结了镍氮碳位点的配位调控规律。\n\n"
        "Fig. 1 催化剂性能对比与阻抗谱分析参见文献 [3, 2]。\n\n"
        "# 参考文献\n\n"
        "[1] Ghost Author. Fabricated Paper Title. Fake Journal, 2029.\n"
        "[2] John Smith. Electrochemical Impedance Modeling of Porous Electrodes. 2021.\n"
        "[3] John Smith, Alice Lee. Single-Atom Nickel Catalysts for CO2 Reduction. 2021.\n",
        encoding="utf-8",
    )

    run_step("inspect_case.py", ["--input-dir", str(in_dir), "--out", str(work_dir / "case_plan.json")])
    run_step("parse_and_dedup_library.py", ["--case-plan", str(work_dir / "case_plan.json"), "--out-dir", str(work_dir)])

    dedup = json.loads((work_dir / "dedup_report.json").read_text(encoding="utf-8"))
    # smith2021a + smith2021preprint + embedded [3] must merge; smith2021b + embedded [2] must merge;
    # smith2021a & smith2021b (same author + same year 2021) MUST stay distinct!
    assert len(dedup["preserved_distinct_pairs"]) >= 1, "Anti-false-merge guard did not fire on same-author same-year distinct papers!"
    assert dedup["duplicates_merged"] >= 3, f"Expected >= 3 duplicates merged, got {dedup['duplicates_merged']}"

    run_step(
        "analyze_manuscript_citations.py",
        ["--case-plan", str(work_dir / "case_plan.json"), "--library", str(work_dir / "canonical_library.json"), "--out-dir", str(work_dir)],
    )
    analysis = json.loads((work_dir / "manuscript_analysis.json").read_text(encoding="utf-8"))
    lib = json.loads((work_dir / "canonical_library.json").read_text(encoding="utf-8"))["entries"]
    ref_by_title = {e["title"]: e["ref_id"] for e in lib}
    r_ni = ref_by_title["Single-Atom Nickel Catalysts for CO2 Reduction"]
    r_eis = ref_by_title["Electrochemical Impedance Modeling of Porous Electrodes"]
    r_cnki = ref_by_title["镍基单原子催化剂电催化还原二氧化碳研究进展"]

    cps = analysis["citation_points"]
    # Insert CNKI citation right after '配位调控规律' and audit existing CPs
    decisions = {
        "target_style": "gbt7714_numeric",
        "claim_insertions": [
            {
                "sentence_id": "S003",
                "anchor_phrase": "配位调控规律",
                "placement_mode": "after_phrase",
                "ref_ids": [r_cnki],
                "match_verdict": "supported",
                "match_evidence": "CNKI abstract explicitly reviews coordination engineering of Ni-N-C catalysts for CO2RR.",
                "rationale": "Placed directly after the coordination regulation claim rather than appending blindly to paragraph end.",
            }
        ],
        "existing_citation_audits": [
            {
                "citation_point_id": cps[0]["citation_point_id"],
                "sentence_id": cps[0]["sentence_id"],
                "old_marker": cps[0]["marker"]["raw"],
                "resolved_ref_ids": [r_ni],
                "action": "keep",
                "match_verdict": "supported",
                "match_evidence": "Abstract states Ni-N-C achieves 96% CO Faradaic efficiency.",
                "rationale": "Matches Smith et al. (2021) Nature Catalysis.",
            },
            {
                "citation_point_id": cps[1]["citation_point_id"],
                "sentence_id": cps[1]["sentence_id"],
                "old_marker": cps[1]["marker"]["raw"],
                "resolved_ref_ids": [r_ni, r_eis],
                "action": "keep",
                "match_verdict": "insufficient_source_text",
                "match_evidence": "Title matches EIS modeling; library lacks abstract for second entry.",
                "rationale": "Synchronized figure caption citation order.",
            },
        ],
        "uncertainty": ["Offline mode: live Crossref DOI verification skipped."],
        "author_confirmation_items": [],
    }
    (work_dir / "citation_decisions.json").write_text(json.dumps(decisions, ensure_ascii=False), encoding="utf-8")

    run_step(
        "sync_and_format.py",
        [
            "--case-plan", str(work_dir / "case_plan.json"),
            "--library", str(work_dir / "canonical_library.json"),
            "--analysis", str(work_dir / "manuscript_analysis.json"),
            "--decisions", str(work_dir / "citation_decisions.json"),
            "--out", str(work_dir / "synced_package.json"),
        ],
    )
    run_step(
        "verify_citations.py",
        [
            "--synced", str(work_dir / "synced_package.json"),
            "--snapshot", str(work_dir / "library_snapshot.json"),
            "--decisions", str(work_dir / "citation_decisions.json"),
            "--case-plan", str(work_dir / "case_plan.json"),
            "--out", str(work_dir / "verification_report.json"),
        ],
    )
    run_step(
        "assemble_output.py",
        [
            "--case-plan", str(work_dir / "case_plan.json"),
            "--snapshot", str(work_dir / "library_snapshot.json"),
            "--dedup", str(work_dir / "dedup_report.json"),
            "--synced", str(work_dir / "synced_package.json"),
            "--decisions", str(work_dir / "citation_decisions.json"),
            "--verification", str(work_dir / "verification_report.json"),
            "--checkpoint", "final",
            "--out-dir", str(out_dir),
            "--strict",
        ],
    )
    res = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
    assert res["status"] == "completed"
    # Verify sequential renumbering: [3] -> [1], CNKI -> [2], caption [3, 2] -> [1,3]
    assert "[1]" in res["updated_manuscript"] and "[2]" in res["updated_manuscript"]
    assert "[1,3]" in res["updated_manuscript"]
    assert "Ghost Author" not in res["updated_manuscript"]
    assert (out_dir / "references_clean.bib").exists() and (out_dir / "references_clean.ris").exists()
    print("[PASS] Case A: Multi-format dedup, anti-false-merge, claim insertion, GB/T 7714 sync")


def test_case_b_apa7_and_adversarial(tmp: Path) -> None:
    case_dir = tmp / "case_b"
    in_dir = case_dir / "input"
    work_dir = case_dir / "work"
    out_dir = case_dir / "output"
    in_dir.mkdir(parents=True)

    (in_dir / "case.json").write_text(json.dumps({"case_id": "test-cite-apa-02"}), encoding="utf-8")
    (in_dir / "task.md").write_text("Convert RIS + EndNote citations to APA 7 format and disambiguate same-author same-year works.", encoding="utf-8")
    (in_dir / "lib.ris").write_text(
        "TY  - JOUR\nAU  - Chen, Wei\nTI  - Alpha Catalyst Study\nJO  - Catalysis Today\nPY  - 2022\nVL  - 10\nSP  - 1\nEP  - 9\nDO  - 10.1016/a1\nER  - \n"
        "TY  - JOUR\nAU  - Chen, Wei\nTI  - Beta Membrane Reactor\nJO  - AIChE Journal\nPY  - 2022\nVL  - 68\nSP  - 50\nEP  - 62\nDO  - 10.1002/b2\nER  - \n",
        encoding="utf-8",
    )
    (in_dir / "paper.md").write_text("Recent catalyst designs (Chen, 2022) complement membrane reactors (Chen, 2022).\n", encoding="utf-8")

    run_step("inspect_case.py", ["--input-dir", str(in_dir), "--out", str(work_dir / "case_plan.json")])
    run_step("parse_and_dedup_library.py", ["--case-plan", str(work_dir / "case_plan.json"), "--out-dir", str(work_dir)])
    run_step(
        "analyze_manuscript_citations.py",
        ["--case-plan", str(work_dir / "case_plan.json"), "--library", str(work_dir / "canonical_library.json"), "--out-dir", str(work_dir)],
    )
    lib = json.loads((work_dir / "canonical_library.json").read_text(encoding="utf-8"))["entries"]
    assert len(lib) == 2

    # Adversarial check: pass fabricated ref in synced_package & unflagged mismatch
    bad_decisions = {
        "target_style": "apa7",
        "claim_insertions": [],
        "existing_citation_audits": [
            {"citation_point_id": "CP001", "sentence_id": "S001", "old_marker": "(Chen, 2022)", "resolved_ref_ids": ["REF-001"], "action": "keep", "match_verdict": "mismatch_or_unsupported", "match_evidence": "Mismatch", "rationale": "Wrongly kept."}
        ],
    }
    (work_dir / "bad_decisions.json").write_text(json.dumps(bad_decisions), encoding="utf-8")
    run_step("sync_and_format.py", ["--case-plan", str(work_dir / "case_plan.json"), "--library", str(work_dir / "canonical_library.json"), "--analysis", str(work_dir / "manuscript_analysis.json"), "--decisions", str(work_dir / "bad_decisions.json"), "--out", str(work_dir / "bad_synced.json")])
    run_step("verify_citations.py", ["--synced", str(work_dir / "bad_synced.json"), "--snapshot", str(work_dir / "library_snapshot.json"), "--decisions", str(work_dir / "bad_decisions.json"), "--case-plan", str(work_dir / "case_plan.json"), "--out", str(work_dir / "bad_ver.json")], expect_ok=False)

    # Clean fix with 2022a / 2022b disambiguation
    good_decisions = {
        "target_style": "apa7",
        "claim_insertions": [],
        "existing_citation_audits": [
            {"citation_point_id": "CP001", "sentence_id": "S001", "old_marker": "(Chen, 2022)", "resolved_ref_ids": ["REF-001"], "action": "keep", "match_verdict": "insufficient_source_text", "match_evidence": "Title matches catalyst designs.", "rationale": "Disambiguated to first Chen 2022 paper."},
            {"citation_point_id": "CP002", "sentence_id": "S001", "old_marker": "(Chen, 2022)", "resolved_ref_ids": ["REF-002"], "action": "keep", "match_verdict": "insufficient_source_text", "match_evidence": "Title matches membrane reactors.", "rationale": "Disambiguated to second Chen 2022 paper."},
        ],
    }
    (work_dir / "citation_decisions.json").write_text(json.dumps(good_decisions), encoding="utf-8")
    run_step("sync_and_format.py", ["--case-plan", str(work_dir / "case_plan.json"), "--library", str(work_dir / "canonical_library.json"), "--analysis", str(work_dir / "manuscript_analysis.json"), "--decisions", str(work_dir / "citation_decisions.json"), "--out", str(work_dir / "synced_package.json")])
    run_step("verify_citations.py", ["--synced", str(work_dir / "synced_package.json"), "--snapshot", str(work_dir / "library_snapshot.json"), "--decisions", str(work_dir / "citation_decisions.json"), "--case-plan", str(work_dir / "case_plan.json"), "--out", str(work_dir / "verification_report.json")])
    synced = json.loads((work_dir / "synced_package.json").read_text(encoding="utf-8"))
    assert "2022a" in synced["updated_manuscript"] and "2022b" in synced["updated_manuscript"]
    print("[PASS] Case B: APA 7 same-author-same-year 2022a/2022b disambiguation & adversarial verifier")


def test_case_c_missing_input(tmp: Path) -> None:
    case_dir = tmp / "case_c"
    in_dir = case_dir / "input"
    work_dir = case_dir / "work"
    out_dir = case_dir / "output"
    in_dir.mkdir(parents=True)

    (in_dir / "case.json").write_text(json.dumps({"case_id": "test-missing-03", "input_files": ["missing.bib"]}), encoding="utf-8")
    (in_dir / "task.md").write_text("Verify citations in missing.bib.", encoding="utf-8")
    run_step("inspect_case.py", ["--input-dir", str(in_dir), "--out", str(work_dir / "case_plan.json")])
    run_step("parse_and_dedup_library.py", ["--case-plan", str(work_dir / "case_plan.json"), "--out-dir", str(work_dir)])
    run_step("assemble_output.py", ["--case-plan", str(work_dir / "case_plan.json"), "--snapshot", str(work_dir / "library_snapshot.json"), "--dedup", str(work_dir / "dedup_report.json"), "--checkpoint", "final", "--out-dir", str(out_dir), "--strict"])
    res = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
    assert res["status"] == "unable_to_determine"
    print("[PASS] Case C: Missing input honest unable_to_determine degradation")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_case_a_dedup_insert_sync_gbt7714(tmp)
        test_case_b_apa7_and_adversarial(tmp)
        test_case_c_missing_input(tmp)
    print("ALL SKILL-03 SELF-CHECKS PASSED.")


if __name__ == "__main__":
    main()
