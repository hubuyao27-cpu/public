#!/usr/bin/env python3
"""Comprehensive 8-scenario offline self-test suite for academic-citation-manager."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent


def _run_py(script: str, args: list[str], expect_rc: int = 0) -> dict:
    proc = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS_DIR / script)] + args,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if proc.returncode != expect_rc:
        raise AssertionError(f"{script} rc={proc.returncode} (expected {expect_rc})\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    try:
        return json.loads(proc.stdout.strip())
    except Exception:
        return {"raw": proc.stdout.strip()}


def test_1_zh_gbt_numeric_full_workflow() -> None:
    """Scenario 1: Chinese GB/T 7714-2015 numeric, dump splitting, dedup, anti-false-merge, reorder, math guard, DOCX."""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        in_d, wk_d, out_d = t / "input", t / "work", t / "output"
        in_d.mkdir()
        (in_d / "case.json").write_text(json.dumps({
            "case_id": "case_zh_01",
            "output_contract": {"case_id": "string", "status": "string", "revised_manuscript": "string", "reference_list": "array", "renumbering_map": "array", "dedup_report": "object", "claim_verifications": "array", "sources": "object", "change_records": "array", "uncertainty": "array", "human_confirmation_items": "array"},
        }), encoding="utf-8")
        (in_d / "task.md").write_text("请按 GB/T 7714-2015 顺序编码制（上标方括号）整理引言与实验章节顺序（先方法后结果），拆解段尾堆叠引用，导出 DOCX 和 Markdown。", encoding="utf-8")
        (in_d / "manuscript.md").write_text(
            "# 1 引言\n\n"
            "Transformer 架构首次提出了自注意力机制。后续研究将其扩展到了分子催化领域，且催化剂转化率达到 94.2%[待引用]。具体数学约束为 $[1, 2]$ 且比表面积为 10<sup>3</sup> m<sup>2</sup>/g[1-2]。\n\n"
            "图1 催化性能对比[2]。\n\n"
            "# 3 结果\n\n"
            "实验验证了张伟2023a方法的有效性[3]。\n\n"
            "# 2 方法\n\n"
            "我们采用张伟2023b中的溶剂热法[4]。\n\n"
            "[^1]: 详见补充数据[1]。\n",
            encoding="utf-8",
        )
        (in_d / "refs.bib").write_text(
            "@misc{vaswani2017arxiv,\n  title={Attention Is All You Need},\n  author={Ashish Vaswani and Noam Shazeer},\n  year={2017},\n  eprint={1706.03762},\n  archivePrefix={arXiv}\n}\n"
            "@inproceedings{vaswani2017nips,\n  title={Attention Is All You Need},\n  author={Vaswani, Ashish and Shazeer, Noam},\n  booktitle={Advances in Neural Information Processing Systems},\n  year={2017},\n  pages={5998-6008}\n}\n"
            "@article{zhang2023a,\n  title={Nickel single-atom catalysts for CO2 reduction},\n  author={张伟 and 李敏},\n  journal={化学学报},\n  year={2023},\n  volume={81},\n  number={2},\n  pages={101-110},\n  doi={10.1000/test.2023.101}\n}\n"
            "@article{zhang2023b,\n  title={Solvothermal synthesis of porous carbon supports},\n  author={张伟 and 李敏},\n  journal={化学学报},\n  year={2023},\n  volume={81},\n  number={2},\n  pages={220-230},\n  doi={10.1000/test.2023.220}\n}\n"
            "@article{uncited2024,\n  title={Unused baseline study},\n  author={Wang, Lei},\n  journal={J. Catal.},\n  year={2024},\n  pages={1-9}\n}\n",
            encoding="utf-8",
        )
        _run_py("prepare_case.py", ["--input-dir", str(in_d), "--work-dir", str(wk_d), "--out-dir", str(out_d)])
        # Verify partial checkpoint exists immediately
        assert (out_d / "result.json").exists()
        lib_c = json.loads((wk_d / "library_canonical.json").read_text(encoding="utf-8"))
        # vaswani preprint + nips merged -> 4 canonical records; zhang2023a & zhang2023b protected separate!
        assert lib_c["stats"]["canonical_record_count"] == 4, lib_c["stats"]
        assert len(lib_c["protected_non_duplicates"]) >= 1

        kmap = lib_c["key_to_canonical_map"]
        ms_p = json.loads((wk_d / "manuscript_parsed.json").read_text(encoding="utf-8"))
        s_map = {s["sentence_id"]: s for s in ms_p["sentences"]}
        # Find sentences and construct plan
        plan = {
            "case_id": "case_zh_01",
            "target_style": "gbt7714_2015_numeric",
            "in_text_format": "superscript_bracket",
            "requested_output_formats": ["docx"],
            "section_order_override": ["1 引言", "2 方法", "3 结果"],
            "manuscript_edits": [
                {"sentence_id": "S001", "action": "insert_citation", "revised_sentence_with_tokens": f"Transformer 架构首次提出了自注意力机制[[CITE:{kmap['vaswani2017nips']}]]。", "anchor_rationale": "Exact Transformer claim"},
                {"sentence_id": "S002", "action": "flag_insufficient_evidence", "revised_sentence_with_tokens": "后续研究将其扩展到了分子催化领域，且催化剂转化率达到 94.2%[证据不足/待人工核验]。", "anchor_rationale": "94.2% metric absent in library"},
                {"sentence_id": "S003", "action": "relocate_citation", "revised_sentence_with_tokens": f"具体数学约束为 $[1, 2]$ 且比表面积为 10<sup>3</sup> m<sup>2</sup>/g[[CITE:{kmap['zhang2023a']}]]。", "anchor_rationale": "Preserve math and units"},
                {"sentence_id": "S004", "action": "keep", "revised_sentence_with_tokens": f"图1 催化性能对比[[CITE:{kmap['zhang2023a']}]].", "anchor_rationale": "Caption sync"},
                {"sentence_id": "S005", "action": "keep", "revised_sentence_with_tokens": f"实验验证了张伟2023a方法的有效性[[CITE:{kmap['zhang2023a']}]].", "anchor_rationale": "Body ref"},
                {"sentence_id": "S006", "action": "keep", "revised_sentence_with_tokens": f"我们采用张伟2023b中的溶剂热法[[CITE:{kmap['zhang2023b']}]].", "anchor_rationale": "Reordered method section"},
                {"sentence_id": "S007", "action": "keep", "revised_sentence_with_tokens": f"详见补充数据[[CITE:{kmap['vaswani2017nips']}]].", "anchor_rationale": "Footnote sync"},
            ],
            "claim_verifications": [],
        }
        (wk_d / "citation_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        ver = _run_py("verify_and_assemble.py", ["--work-dir", str(wk_d), "--out-dir", str(out_d), "--plan", str(wk_d / "citation_plan.json")])
        assert ver["passed"] is True, ver
        res_md = (out_d / "revised_manuscript.md").read_text(encoding="utf-8")
        assert "$[1, 2]$" in res_md and "10<sup>3</sup>" in res_md
        # Method (# 2 方法) moved before Result (# 3 结果), so zhang2023b gets [3] if zhang2023a appeared in S003 as [2]
        assert res_md.index("# 2 方法") < res_md.index("# 3 结果")
        assert (out_d / "revised_manuscript.docx").exists() and (out_d / "revised_manuscript.docx").stat().st_size > 0


def test_2_apa7_author_year_and_disambiguation() -> None:
    """Scenario 2: APA 7th Author-Year parenthetical (&) vs narrative (and), a/b suffixes, 3+ et al."""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        in_d, wk_d, out_d = t / "input", t / "work", t / "output"
        in_d.mkdir()
        (in_d / "case.json").write_text(json.dumps({"case_id": "case_apa7"}), encoding="utf-8")
        (in_d / "task.md").write_text("Format citations and bibliography strictly in APA 7th edition.", encoding="utf-8")
        (in_d / "draft.md").write_text(
            "# Introduction\n\n"
            "Alpha studies established the theory (Smith & Jones, 2021). Later work expanded it (Smith & Jones, 2021).\n\n"
            "Lee et al. (2022) demonstrated multi-agent coordination.\n",
            encoding="utf-8",
        )
        (in_d / "library.bib").write_text(
            "@article{s1, title={First model}, author={Smith, Alice and Jones, Bob}, journal={Nature}, year={2021}, volume={10}, pages={1-5}, doi={10.1038/a1}}\n"
            "@article{s2, title={Second model}, author={Smith, Alice and Jones, Bob}, journal={Science}, year={2021}, volume={20}, pages={6-10}, doi={10.1126/a2}}\n"
            "@article{l1, title={Multi-agent systems}, author={Lee, Kevin and Chen, Wei and Park, Min}, journal={AIJ}, year={2022}, volume={30}, pages={11-22}}\n",
            encoding="utf-8",
        )
        _run_py("prepare_case.py", ["--input-dir", str(in_d), "--work-dir", str(wk_d), "--out-dir", str(out_d)])
        lib_c = json.loads((wk_d / "library_canonical.json").read_text(encoding="utf-8"))
        km = lib_c["key_to_canonical_map"]
        plan = {
            "case_id": "case_apa7",
            "target_style": "apa7",
            "in_text_format": "author_year",
            "manuscript_edits": [
                {"sentence_id": "S001", "action": "disambiguate_author_year", "revised_sentence_with_tokens": f"Alpha studies established the theory [[CITE:{km['s1']}]].", "anchor_rationale": "Disambiguate 2021a"},
                {"sentence_id": "S002", "action": "disambiguate_author_year", "revised_sentence_with_tokens": f"Later work expanded it [[CITE:{km['s2']}]].", "anchor_rationale": "Disambiguate 2021b"},
            ],
            "claim_verifications": [],
        }
        (wk_d / "citation_plan.json").write_text(json.dumps(plan), encoding="utf-8")
        ver = _run_py("verify_and_assemble.py", ["--work-dir", str(wk_d), "--out-dir", str(out_d), "--plan", str(wk_d / "citation_plan.json")])
        assert ver["passed"] is True, ver
        md = (out_d / "revised_manuscript.md").read_text(encoding="utf-8")
        assert "(Smith & Jones, 2021a)" in md and "(Smith & Jones, 2021b)" in md
        assert "Lee et al. (2022)" in md


def test_3_all_other_styles_and_missing_fields() -> None:
    """Scenario 3: IEEE, Nature, Vancouver, Chicago, GB/T Author-Year, APA 6th with missing fields cleanly omitted."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    from library_engine import format_in_text_citation, format_reference_entry
    rec = {
        "ref_id": "REF_001", "doc_type": "article", "language": "en",
        "authors": ["Alice Smith", "Bob Jones", "Carol Lee", "Dan Wu"],
        "title": "Clean Catalysis", "venue": "J. Catal.", "year": 2024,
        "volume": "", "issue": "", "pages": "", "doi": "10.1016/j.jcat.2024.01", "url": "",
    }
    for st in ("ieee", "nature", "vancouver", "chicago_author_date", "gbt7714_2015_author_year", "apa6"):
        out = format_reference_entry(rec, st, 1, year_suffix="a")
        assert "None" not in out and "()" not in out and ".." not in out.replace("...", ""), f"Dirty {st}: {out}"
    ieee_m = format_in_text_citation([(rec, 1, ""), (rec, 2, ""), (rec, 3, ""), (rec, 5, "")], "ieee", "bracket_separate")
    assert ieee_m == "[1]–[3], [5]", ieee_m
    nat_m = format_in_text_citation([(rec, 1, ""), (rec, 2, ""), (rec, 3, "")], "nature", "superscript_html")
    assert nat_m == "<sup>1–3</sup>", nat_m


def test_4_multiformat_ingestion_and_recycled_doi_guard() -> None:
    """Scenario 4: Malformed BibTeX, RIS, ENW, EndNote XML, CNKI, CSL-JSON, CSV, GBK + recycled DOI guard."""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        in_d, wk_d, out_d = t / "input", t / "work", t / "output"
        in_d.mkdir()
        (in_d / "case.json").write_text(json.dumps({"case_id": "case_multi_fmt"}), encoding="utf-8")
        (in_d / "task.md").write_text("Deduplicate and convert library to GB/T 7714-2015.", encoding="utf-8")
        (in_d / "a.bib").write_text("@article{k1, title={Alpha Catalyst}, author={Smith, John}, journal={Chem. Eng.}, year={2022}, doi={10.1000/dup.1}\n@article{k2, title={Unrelated Astronomy Survey}, author={Zeta, Maria}, journal={Astrophys. J.}, year={2021}, doi={10.1000/dup.1}}", encoding="utf-8")
        (in_d / "b.ris").write_text("TY  - JOUR\nAU  - Doe, Jane\nTI  - Beta Membrane\nJO  - J. Membr. Sci.\nPY  - 2023\nDO  - 10.1000/beta.2\nER  - \n", encoding="utf-8")
        (in_d / "c.enw").write_text("%0 Journal Article\n%A Wang, Li\n%T Gamma Reactor\n%J AIChE J.\n%D 2020\n", encoding="utf-8")
        (in_d / "d.xml").write_text("<xml><records><record><ref-type name=\"Journal Article\"/><contributors><authors><author>Chen, Bo</author></authors></contributors><titles><title>Delta Adsorption</title></titles><dates><year>2021</year></dates></record></records></xml>", encoding="utf-8")
        (in_d / "e_cnki.txt").write_bytes("SrcDatabase-来源库: 期刊\nTitle-题名: 碳捕集工艺研究\nAuthor-作者: 赵强;孙丽\nSource-文献来源: 化工进展\nPubTime-发表时间: 2023\n".encode("gbk"))
        (in_d / "f.csv").write_text("title,authors,year,journal\nEpsilon Kinetics,Wu Fang,2024,Ind. Eng. Chem. Res.\n", encoding="utf-8")

        _run_py("prepare_case.py", ["--input-dir", str(in_d), "--work-dir", str(wk_d), "--out-dir", str(out_d)])
        lib_c = json.loads((wk_d / "library_canonical.json").read_text(encoding="utf-8"))
        assert len(lib_c["recycled_doi_conflicts"]) == 1, lib_c["recycled_doi_conflicts"]
        assert lib_c["stats"]["canonical_record_count"] == 7, lib_c["stats"]
        (wk_d / "citation_plan.json").write_text(json.dumps({"case_id": "case_multi_fmt", "target_style": "gbt7714_2015_numeric", "in_text_format": "bracket", "manuscript_edits": [], "claim_verifications": []}), encoding="utf-8")
        ver = _run_py("verify_and_assemble.py", ["--work-dir", str(wk_d), "--out-dir", str(out_d), "--plan", str(wk_d / "citation_plan.json")])
        assert ver["passed"] is True, ver


def test_5_latex_preservation_and_sync() -> None:
    """Scenario 5: LaTeX .tex input preservation, equations, captions, \\cite / \\citep sync."""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        in_d, wk_d, out_d = t / "input", t / "work", t / "output"
        in_d.mkdir()
        (in_d / "case.json").write_text(json.dumps({"case_id": "case_tex"}), encoding="utf-8")
        (in_d / "task.md").write_text("Update LaTeX manuscript citations in IEEE style.", encoding="utf-8")
        (in_d / "paper.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\n\\section{Intro}\nDeep learning improved catalysis \\cite{smith2023}.\n\n"
            "\\begin{equation}\nE = mc^2\n\\end{equation}\n\\end{document}\n",
            encoding="utf-8",
        )
        (in_d / "refs.bib").write_text("@article{smith2023, title={Deep Catalysis}, author={Smith, John}, journal={IEEE Trans.}, year={2023}, volume={5}, pages={10-18}}\n", encoding="utf-8")
        _run_py("prepare_case.py", ["--input-dir", str(in_d), "--work-dir", str(wk_d), "--out-dir", str(out_d)])
        (wk_d / "citation_plan.json").write_text(json.dumps({"case_id": "case_tex", "target_style": "ieee", "in_text_format": "latex_cite", "manuscript_edits": [], "claim_verifications": []}), encoding="utf-8")
        ver = _run_py("verify_and_assemble.py", ["--work-dir", str(wk_d), "--out-dir", str(out_d), "--plan", str(wk_d / "citation_plan.json")])
        assert ver["passed"] is True, ver
        tex_out = (out_d / "revised_manuscript.tex").read_text(encoding="utf-8")
        assert "\\begin{equation}" in tex_out and "\\bibitem{" in tex_out


def test_6_claim_verification_and_anti_hallucination() -> None:
    """Scenario 6: Claim verification against local evidence (hyphenation tolerant) + catching fabricated quote."""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        in_d, wk_d, out_d = t / "input", t / "work", t / "output"
        in_d.mkdir()
        (in_d / "case.json").write_text(json.dumps({"case_id": "case_verify"}), encoding="utf-8")
        (in_d / "task.md").write_text("Verify citations against local evidence paper.", encoding="utf-8")
        (in_d / "draft.md").write_text("The catalyst achieved 88.5% Faradaic efficiency[1] and surpassed benchmarks[99].", encoding="utf-8")
        (in_d / "refs.bib").write_text("@article{k1, title={Nickel Catalyst Study}, author={Lin, Bo}, journal={J. Catal.}, year={2024}, doi={10.1000/cat.88}}\n", encoding="utf-8")
        (in_d / "evidence_paper.md").write_text("# Nickel Catalyst Study\nDOI: 10.1000/cat.88\nThe nickel catalyst achieved 88.5% Faradaic-\nefficiency at -0.8 V.\n", encoding="utf-8")
        _run_py("prepare_case.py", ["--input-dir", str(in_d), "--work-dir", str(wk_d), "--out-dir", str(out_d)])
        lib_c = json.loads((wk_d / "library_canonical.json").read_text(encoding="utf-8"))
        rid = lib_c["canonical_records"][0]["ref_id"]

        # Valid quote (even with PDF line-break hyphenation)
        good_plan = {
            "case_id": "case_verify", "target_style": "gbt7714_2015_numeric", "in_text_format": "bracket",
            "manuscript_edits": [
                {"sentence_id": "S001", "action": "replace_citation", "revised_sentence_with_tokens": f"The catalyst achieved 88.5% Faradaic efficiency[[CITE:{rid}]] and surpassed benchmarks[UNVERIFIED_CITE: ref-99].", "anchor_rationale": "Link [1] to Lin 2024 and mark [99] dangling"},
            ],
            "claim_verifications": [{"claim_id": "C1", "sentence_id": "S001", "cited_ref_ids": [rid], "verdict": "supported_by_local_fulltext", "evidence_locator": "evidence_paper.md:3", "evidence_quote": "achieved 88.5% Faradaic efficiency at -0.8 V"}],
        }
        (wk_d / "citation_plan.json").write_text(json.dumps(good_plan), encoding="utf-8")
        ver = _run_py("verify_and_assemble.py", ["--work-dir", str(wk_d), "--out-dir", str(out_d), "--plan", str(wk_d / "citation_plan.json")])
        assert ver["passed"] is True, ver
        assert "[UNVERIFIED_CITE: ref-99]" in (out_d / "revised_manuscript.md").read_text(encoding="utf-8")

        # Fabricated quote must be caught!
        bad_plan = dict(good_plan)
        bad_plan["claim_verifications"] = [{"claim_id": "C1", "sentence_id": "S001", "cited_ref_ids": [rid], "verdict": "supported_by_local_fulltext", "evidence_locator": "fake", "evidence_quote": "quantum superconducting graphene achieved 99.99% yield"}]
        (wk_d / "citation_plan.json").write_text(json.dumps(bad_plan), encoding="utf-8")
        ver_bad = _run_py("verify_and_assemble.py", ["--work-dir", str(wk_d), "--out-dir", str(out_d), "--plan", str(wk_d / "citation_plan.json")], expect_rc=1)
        assert any(e["code"] == "ungrounded_evidence_quote" for e in ver_bad["errors"]), ver_bad


def test_7_library_only_mode() -> None:
    """Scenario 7: Library-only conversion & deduplication with 0 manuscript sentences."""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        in_d, wk_d, out_d = t / "input", t / "work", t / "output"
        in_d.mkdir()
        (in_d / "case.json").write_text(json.dumps({"case_id": "case_lib_only"}), encoding="utf-8")
        (in_d / "task.md").write_text("Deduplicate references and export in GB/T 7714-2015 numeric style.", encoding="utf-8")
        (in_d / "lib.bib").write_text("@article{a, title={Paper One}, author={Li, Ming}, journal={Sci. China}, year={2023}, pages={1-8}}\n", encoding="utf-8")
        _run_py("prepare_case.py", ["--input-dir", str(in_d), "--work-dir", str(wk_d), "--out-dir", str(out_d)])
        (wk_d / "citation_plan.json").write_text(json.dumps({"case_id": "case_lib_only", "target_style": "gbt7714_2015_numeric", "in_text_format": "bracket", "manuscript_edits": [], "claim_verifications": []}), encoding="utf-8")
        ver = _run_py("verify_and_assemble.py", ["--work-dir", str(wk_d), "--out-dir", str(out_d), "--plan", str(wk_d / "citation_plan.json")])
        assert ver["passed"] is True and ver["metrics"]["cited_references"] == 1


def test_8_empty_corrupt_and_heterogeneous_contracts() -> None:
    """Scenario 8: Corrupt/empty input -> honest unable_to_determine + heterogeneous output_contract compliance."""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        in_d, wk_d, out_d = t / "input", t / "work", t / "output"
        in_d.mkdir()
        (in_d / "case.json").write_text(json.dumps({
            "case_id": "case_empty_contract",
            "output_contract": {
                "properties": {
                    "case_id": {"type": "string"},
                    "status": {"type": "string"},
                    "custom_summary": {"type": "string"},
                    "reference_list": {"type": "array"},
                },
                "required": ["case_id", "status", "custom_summary", "reference_list"],
            },
        }), encoding="utf-8")
        (in_d / "task.md").write_text("Process references.", encoding="utf-8")
        (in_d / "bad.pdf").write_bytes(b"NOT_A_PDF_CORRUPT")
        _run_py("prepare_case.py", ["--input-dir", str(in_d), "--work-dir", str(wk_d), "--out-dir", str(out_d)])
        (wk_d / "citation_plan.json").write_text(json.dumps({"case_id": "case_empty_contract", "target_style": "apa7", "in_text_format": "author_year", "manuscript_edits": [], "claim_verifications": []}), encoding="utf-8")
        ver = _run_py("verify_and_assemble.py", ["--work-dir", str(wk_d), "--out-dir", str(out_d), "--plan", str(wk_d / "citation_plan.json")])
        assert ver["passed"] is True, ver
        rj = json.loads((out_d / "result.json").read_text(encoding="utf-8"))
        assert rj["status"] == "unable_to_determine"
        assert isinstance(rj["custom_summary"], str) and isinstance(rj["reference_list"], list)


def test_9_real_pdf_field_provenance_and_zero_rework() -> None:
    """Scenario 9: Real PyMuPDF PDF evidence, field provenance table, [REF_1] shorthand & zero-rework claim hydration."""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        in_d, wk_d, out_d = t / "input", t / "work", t / "output"
        in_d.mkdir()
        (in_d / "case.json").write_text(json.dumps({"case_id": "case_real_pdf"}), encoding="utf-8")
        (in_d / "task.md").write_text("按 GB/T 7714-2015 顺序编码制整理引用并验证 PDF 原文证据。", encoding="utf-8")
        (in_d / "manuscript.md").write_text(
            "Hydrophobic microchannel Ni-N-C catalyst achieved 96.4% CO Faradaic efficiency over 150 h (Zhang & Wang, 2024).\n"
            "分级多孔镍单原子催化剂的低温传质优势显著[待引用]。\n",
            encoding="utf-8",
        )
        (in_d / "refs.bib").write_text(
            "@misc{Zhang2024Membrane,\n"
            "  title={Hydrophobic Microchannel Catalysts for Stable CO2 Electrolysis},\n"
            "  author={Zhang, Lei and Wang, Min},\n"
            "  year={2024},\n"
            "  eprint={2401.12345},\n"
            "  archivePrefix={arXiv}\n"
            "}\n",
            encoding="utf-8",
        )
        (in_d / "refs.ris").write_text(
            "TY  - JOUR\n"
            "AU  - Zhang, Lei\n"
            "AU  - Wang, Min\n"
            "TI  - Hydrophobic Microchannel Catalysts for Stable CO2 Electrolysis\n"
            "JO  - Applied Catalysis B: Environmental\n"
            "PY  - 2024\n"
            "VL  - 342\n"
            "SP  - 123456\n"
            "DO  - 10.1016/j.apcatb.2024.123456\n"
            "ER  -\n",
            encoding="utf-8",
        )
        ev_file = "evidence_zhang2024.pdf"
        try:
            try:
                import pymupdf as fitz  # type: ignore
            except ImportError:
                import fitz  # type: ignore

            doc = fitz.open()
            p1 = doc.new_page()
            p1.insert_text((72, 72), "Hydrophobic Microchannel Catalysts for Stable CO2 Electrolysis\nDOI: 10.1016/j.apcatb.2024.123456\nLei Zhang, Min Wang\n")
            p2 = doc.new_page()
            p2.insert_text((72, 72), "Section 3.2 Performance Summary\nThe hydrophobic microchannel Ni-N-C catalyst achieved 96.4% CO Faradaic efficiency over 150 h without salt precipitation.\n")
            doc.save(str(in_d / ev_file))
            doc.close()
        except ImportError:
            ev_file = "evidence_zhang2024.md"
            (in_d / ev_file).write_text(
                "# Hydrophobic Microchannel Catalysts for Stable CO2 Electrolysis\nDOI: 10.1016/j.apcatb.2024.123456\n"
                "The hydrophobic microchannel Ni-N-C catalyst achieved 96.4% CO Faradaic efficiency over 150 h without salt precipitation.\n",
                encoding="utf-8",
            )

        prep = _run_py("prepare_case.py", ["--input-dir", str(in_d), "--work-dir", str(wk_d), "--out-dir", str(out_d)])
        assert prep["canonical_refs"] == 1, prep
        skel = json.loads((wk_d / "citation_plan_skeleton.json").read_text(encoding="utf-8"))
        assert skel["claim_verifications"], f"Expected pre-populated claim_verifications in skeleton: {skel}"
        assert any("evidence_zhang2024" in cv.get("evidence_locator", "") and "96.4%" in cv.get("evidence_quote", "") for cv in skel["claim_verifications"]), skel["claim_verifications"]

        # Intentionally omit claim_verifications & use [REF_1] shorthand to verify zero-rework auto-hydration
        plan = {
            "case_id": "case_real_pdf",
            "target_style": "gbt7714_2015_numeric",
            "in_text_format": "bracket",
            "manuscript_edits": [
                {
                    "sentence_id": "S002",
                    "action": "replace_placeholder",
                    "revised_sentence_with_tokens": "分级多孔镍单原子催化剂的低温传质优势显著[REF_1]。",
                    "anchor_rationale": "Grounded via REF_1 shorthand and PDF page 2 performance summary.",
                }
            ],
        }
        (wk_d / "citation_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        ver = _run_py("verify_and_assemble.py", ["--work-dir", str(wk_d), "--out-dir", str(out_d), "--plan", str(wk_d / "citation_plan.json")])
        assert ver["passed"] is True, ver

        rj = json.loads((out_d / "result.json").read_text(encoding="utf-8"))
        assert rj["status"] == "completed" and "[1]" in rj["revised_manuscript"] and "[REF_1]" not in rj["revised_manuscript"]
        assert rj["sources"].get("claim_evidence_anchors"), f"Missing claim_evidence_anchors in result.json['sources']: {rj['sources']}"
        assert rj["sources"]["claim_evidence_anchors"][0]["evidence_quote"], rj["sources"]["claim_evidence_anchors"]
        audit_txt = (out_d / "citation_audit_report.md").read_text(encoding="utf-8")
        assert "Field-Level Source Evidence Ledger" in audit_txt and "refs.ris" in audit_txt and "10.1016/j.apcatb.2024.123456" in audit_txt, audit_txt


def main() -> None:
    tests = [
        test_1_zh_gbt_numeric_full_workflow,
        test_2_apa7_author_year_and_disambiguation,
        test_3_all_other_styles_and_missing_fields,
        test_4_multiformat_ingestion_and_recycled_doi_guard,
        test_5_latex_preservation_and_sync,
        test_6_claim_verification_and_anti_hallucination,
        test_7_library_only_mode,
        test_8_empty_corrupt_and_heterogeneous_contracts,
        test_9_real_pdf_field_provenance_and_zero_rework,
    ]
    for fn in tests:
        fn()
        print(f"PASS: {fn.__name__}")
    print(f"\nALL {len(tests)}/{len(tests)} SELF-TESTS PASSED.")


if __name__ == "__main__":
    main()
