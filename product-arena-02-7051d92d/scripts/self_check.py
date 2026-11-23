#!/usr/bin/env python3
"""Standalone automated test suite for academic-language-polisher.

Tests:
A. English Abstract/Methods polishing + adversarial trap (hedge upgrade, correlation->causation, altered number).
B. Chinese-English bilingual text with Chinglish, term drift, and internal glossary conflict disclosure.
C. LaTeX manuscript preservation (\\cite{}, math, \\ref{}) and custom output_contract field mapping.
D. Non-polishing Q&A trigger guard & missing/corrupt input graceful handling.
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


def test_case_a_english_and_adversarial(tmp: Path) -> None:
    case_dir = tmp / "case_a"
    in_dir = case_dir / "input"
    work_dir = case_dir / "work"
    out_dir = case_dir / "output"
    in_dir.mkdir(parents=True)

    (in_dir / "case.json").write_text(
        json.dumps(
            {
                "case_id": "test-en-abstract-01",
                "output_contract": {
                    "case_id": {"type": "string"},
                    "status": {"type": "string"},
                    "polished_text": {"type": "string"},
                    "change_records": {"type": "array"},
                },
            }
        ),
        encoding="utf-8",
    )
    (in_dir / "task.md").write_text("Polish this English journal abstract with minimal edits while keeping all data and hedges intact.", encoding="utf-8")
    (in_dir / "glossary.json").write_text(
        json.dumps(
            {
                "terms": [
                    {
                        "canonical_en": "faradaic efficiency",
                        "canonical_zh": "法拉第效率",
                        "acronym": "FE",
                        "avoid": ["Faraday efficiency"],
                        "note": "Standard electrocatalysis term",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (in_dir / "draft.md").write_text(
        "# Abstract\n\n"
        "With the rapid development of electrocatalysis, single-atom Ni catalysts has attracted widespread attention [1]. "
        "Our results may indicate that higher pyridine-N content is associated with improved Faraday efficiency of 96.4 ± 1.2% at -0.8 V vs. RHE.\n",
        encoding="utf-8",
    )

    run_step("inspect_case.py", ["--input-dir", str(in_dir), "--out", str(work_dir / "case_plan.json")])
    run_step("analyze_manuscript.py", ["--case-plan", str(work_dir / "case_plan.json"), "--out-dir", str(work_dir)])
    run_step(
        "assemble_output.py",
        [
            "--case-plan", str(work_dir / "case_plan.json"),
            "--segments", str(work_dir / "source_segments.json"),
            "--glossary", str(work_dir / "glossary_resolved.json"),
            "--checkpoint", "partial",
            "--out-dir", str(out_dir),
        ],
    )

    segs = json.loads((work_dir / "source_segments.json").read_text(encoding="utf-8"))["segments"]
    assert len(segs) == 3, f"Expected 3 segments (heading + 2 sentences), got {len(segs)}"

    # Adversarial payload: drops hedge ('may indicate' -> 'proves'), upgrades correlation->causation ('causes'), alters number (96.4 -> 97.0)
    bad_payload = {
        "status": "completed",
        "genre": "journal_article",
        "language": "en",
        "edit_intensity": "minimal",
        "sentences": [
            {
                "sentence_id": segs[0]["sentence_id"],
                "paragraph_id": segs[0]["paragraph_id"],
                "section": segs[0]["section"],
                "original": segs[0]["original"],
                "polished": segs[0]["original"],
                "changed": False,
                "edit_types": ["unchanged"],
                "rationale": "Section heading unchanged.",
            },
            {
                "sentence_id": segs[1]["sentence_id"],
                "paragraph_id": segs[1]["paragraph_id"],
                "section": segs[1]["section"],
                "original": segs[1]["original"],
                "polished": "Single-atom Ni catalysts have attracted widespread attention [1].",
                "changed": True,
                "edit_types": ["grammar_syntax", "chinglish_translationese"],
                "rationale": "Removed stock opener and fixed subject-verb agreement (has -> have).",
            },
            {
                "sentence_id": segs[2]["sentence_id"],
                "paragraph_id": segs[2]["paragraph_id"],
                "section": segs[2]["section"],
                "original": segs[2]["original"],
                "polished": "Our results prove that higher pyridine-N content causes an improved faradaic efficiency of 97.0 ± 1.2% at -0.8 V vs. RHE.",
                "changed": True,
                "edit_types": ["terminology_consistency"],
                "rationale": "Standardized term.",
            },
        ],
        "glossary_final": [],
    }
    (work_dir / "bad_payload.json").write_text(json.dumps(bad_payload, ensure_ascii=False), encoding="utf-8")
    run_step(
        "verify_polish.py",
        [
            "--payload", str(work_dir / "bad_payload.json"),
            "--segments", str(work_dir / "source_segments.json"),
            "--glossary", str(work_dir / "glossary_resolved.json"),
            "--case-plan", str(work_dir / "case_plan.json"),
            "--out", str(work_dir / "bad_report.json"),
        ],
        expect_ok=False,
    )
    bad_rep = json.loads((work_dir / "bad_report.json").read_text(encoding="utf-8"))
    axes_hit = {f["axis"] for f in bad_rep["findings"] if f["severity"] == "ERROR"}
    assert {"protected_spans", "epistemic_hedging", "causality_polarity"} <= axes_hit, f"Expected axes not all caught: {axes_hit}"

    # Fixed payload
    good_payload = bad_payload
    good_payload["sentences"][2]["polished"] = (
        "Our results suggest that higher pyridine-N content is associated with an improved "
        "faradaic efficiency (FE) of 96.4 ± 1.2% at -0.8 V vs. RHE."
    )
    good_payload["sentences"][2]["edit_types"] = ["terminology_consistency", "abbreviation_expansion", "grammar_syntax"]
    good_payload["sentences"][2]["rationale"] = "Replaced 'Faraday efficiency' with canonical 'faradaic efficiency (FE)' and added missing article 'an', while preserving hedge 'suggest', correlational link 'associated with', and exact data."
    (work_dir / "polished_payload.json").write_text(json.dumps(good_payload, ensure_ascii=False), encoding="utf-8")

    run_step(
        "verify_polish.py",
        [
            "--payload", str(work_dir / "polished_payload.json"),
            "--segments", str(work_dir / "source_segments.json"),
            "--glossary", str(work_dir / "glossary_resolved.json"),
            "--case-plan", str(work_dir / "case_plan.json"),
            "--out", str(work_dir / "verification_report.json"),
        ],
    )
    run_step(
        "assemble_output.py",
        [
            "--case-plan", str(work_dir / "case_plan.json"),
            "--segments", str(work_dir / "source_segments.json"),
            "--glossary", str(work_dir / "glossary_resolved.json"),
            "--payload", str(work_dir / "polished_payload.json"),
            "--verification", str(work_dir / "verification_report.json"),
            "--checkpoint", "final",
            "--out-dir", str(out_dir),
            "--strict",
        ],
    )
    res = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
    assert res["status"] == "completed"
    assert res["audit_metrics"]["protected_span_retention_rate"] == 1.0
    print("[PASS] Case A: English polishing + adversarial semantic trap caught & fixed")


def test_case_b_conflict_and_bilingual(tmp: Path) -> None:
    case_dir = tmp / "case_b"
    in_dir = case_dir / "input"
    work_dir = case_dir / "work"
    out_dir = case_dir / "output"
    in_dir.mkdir(parents=True)

    (in_dir / "case.json").write_text(json.dumps({"case_id": "test-bilingual-conflict-02"}), encoding="utf-8")
    (in_dir / "task.md").write_text("中英双语润色该基金申请书片段，并处理术语表。", encoding="utf-8")
    (in_dir / "glossary.csv").write_text(
        "canonical_en,canonical_zh,acronym,avoid\n"
        "electrochemical impedance spectroscopy,电化学阻抗谱,EIS,\n"
        "electrochemical impedance spectroscopy,交流阻抗谱,EIS,\n",
        encoding="utf-8",
    )
    (in_dir / "draft.md").write_text(
        "本课题组将利用电化学阻抗谱研究电荷转移行为。\n",
        encoding="utf-8",
    )

    run_step("inspect_case.py", ["--input-dir", str(in_dir), "--out", str(work_dir / "case_plan.json")])
    run_step("analyze_manuscript.py", ["--case-plan", str(work_dir / "case_plan.json"), "--out-dir", str(work_dir)])
    g_res = json.loads((work_dir / "glossary_resolved.json").read_text(encoding="utf-8"))
    assert len(g_res["glossary_conflicts"]) == 1, "Expected internal glossary conflict to be detected"

    segs = json.loads((work_dir / "source_segments.json").read_text(encoding="utf-8"))["segments"]
    payload = {
        "status": "completed",
        "genre": "grant_proposal",
        "language": "bilingual",
        "edit_intensity": "standard",
        "sentences": [
            {
                "sentence_id": segs[0]["sentence_id"],
                "paragraph_id": segs[0]["paragraph_id"],
                "section": segs[0]["section"],
                "original": segs[0]["original"],
                "polished": "本课题组拟采用电化学阻抗谱（EIS）表征界面电荷转移行为。",
                "changed": True,
                "edit_types": ["academic_register", "abbreviation_expansion"],
                "rationale": "Adjusted grant wording ('将利用' -> '拟采用', added '表征界面') and retained provisional term pending glossary conflict resolution.",
                "needs_human_confirmation": True,
                "confirmation_question": "Confirm canonical Chinese translation of EIS: '电化学阻抗谱' vs '交流阻抗谱'?",
            }
        ],
        "glossary_final": [],
        "uncertainty": ["GCONF-01: glossary.csv maps 'electrochemical impedance spectroscopy' to both '电化学阻抗谱' and '交流阻抗谱'."],
        "author_confirmation_items": [
            {
                "id": "GCONF-01",
                "sentence_id": segs[0]["sentence_id"],
                "issue_type": "glossary_conflict",
                "question": "Which Chinese term should be locked for 'electrochemical impedance spectroscopy'?",
                "options": ["电化学阻抗谱", "交流阻抗谱"],
                "current_handling": "Retained '电化学阻抗谱' provisionally.",
            }
        ],
    }
    (work_dir / "polished_payload.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    run_step(
        "verify_polish.py",
        [
            "--payload", str(work_dir / "polished_payload.json"),
            "--segments", str(work_dir / "source_segments.json"),
            "--glossary", str(work_dir / "glossary_resolved.json"),
            "--case-plan", str(work_dir / "case_plan.json"),
            "--out", str(work_dir / "verification_report.json"),
        ],
    )
    run_step(
        "assemble_output.py",
        [
            "--case-plan", str(work_dir / "case_plan.json"),
            "--segments", str(work_dir / "source_segments.json"),
            "--glossary", str(work_dir / "glossary_resolved.json"),
            "--payload", str(work_dir / "polished_payload.json"),
            "--verification", str(work_dir / "verification_report.json"),
            "--checkpoint", "final",
            "--out-dir", str(out_dir),
            "--strict",
        ],
    )
    print("[PASS] Case B: Bilingual grant polish + glossary conflict disclosure")


def test_case_c_latex_and_custom_contract(tmp: Path) -> None:
    case_dir = tmp / "case_c"
    in_dir = case_dir / "input"
    work_dir = case_dir / "work"
    out_dir = case_dir / "output"
    in_dir.mkdir(parents=True)

    (in_dir / "case.json").write_text(
        json.dumps(
            {
                "case_id": "test-latex-03",
                "output_contract": {
                    "properties": {
                        "case_id": {"type": "string"},
                        "status": {"type": "string"},
                        "revised_manuscript": {"type": "string"},
                        "edits": {"type": "array"},
                    },
                    "required": ["case_id", "status", "revised_manuscript", "edits"],
                },
            }
        ),
        encoding="utf-8",
    )
    (in_dir / "task.md").write_text("Copyedit this LaTeX paper section.", encoding="utf-8")
    (in_dir / "section.tex").write_text(
        "\\section{Methodology}\n"
        "The reaction energy $\\Delta G = -0.35\\,\\mathrm{eV}$ was computed using DFT \\cite{kresse1996}, as shown in Fig.~\\ref{fig:1}.\n",
        encoding="utf-8",
    )

    run_step("inspect_case.py", ["--input-dir", str(in_dir), "--out", str(work_dir / "case_plan.json")])
    run_step("analyze_manuscript.py", ["--case-plan", str(work_dir / "case_plan.json"), "--out-dir", str(work_dir)])
    segs = json.loads((work_dir / "source_segments.json").read_text(encoding="utf-8"))["segments"]
    assert len(segs) == 2

    payload = {
        "status": "completed",
        "genre": "journal_article",
        "language": "en",
        "edit_intensity": "minimal",
        "sentences": [
            {
                "sentence_id": segs[0]["sentence_id"],
                "paragraph_id": segs[0]["paragraph_id"],
                "section": segs[0]["section"],
                "original": segs[0]["original"],
                "polished": segs[0]["original"],
                "changed": False,
                "edit_types": ["unchanged"],
                "rationale": "LaTeX section heading preserved.",
            },
            {
                "sentence_id": segs[1]["sentence_id"],
                "paragraph_id": segs[1]["paragraph_id"],
                "section": segs[1]["section"],
                "original": segs[1]["original"],
                "polished": "The reaction free energy $\\Delta G = -0.35\\,\\mathrm{eV}$ was calculated via DFT \\cite{kresse1996}, as shown in Fig.~\\ref{fig:1}.",
                "changed": True,
                "edit_types": ["academic_register"],
                "rationale": "Refined 'computed using' -> 'calculated via' while keeping inline LaTeX math, \\cite{}, and \\ref{} untouched.",
            },
        ],
        "glossary_final": [],
    }
    (work_dir / "polished_payload.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    run_step(
        "verify_polish.py",
        [
            "--payload", str(work_dir / "polished_payload.json"),
            "--segments", str(work_dir / "source_segments.json"),
            "--glossary", str(work_dir / "glossary_resolved.json"),
            "--case-plan", str(work_dir / "case_plan.json"),
            "--out", str(work_dir / "verification_report.json"),
        ],
    )
    run_step(
        "assemble_output.py",
        [
            "--case-plan", str(work_dir / "case_plan.json"),
            "--segments", str(work_dir / "source_segments.json"),
            "--glossary", str(work_dir / "glossary_resolved.json"),
            "--payload", str(work_dir / "polished_payload.json"),
            "--verification", str(work_dir / "verification_report.json"),
            "--checkpoint", "final",
            "--out-dir", str(out_dir),
            "--strict",
        ],
    )
    res = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
    assert "revised_manuscript" in res and "edits" in res
    assert (out_dir / "polished_manuscript.tex").exists()
    print("[PASS] Case C: LaTeX preservation & strict custom output_contract")


def test_case_d_qa_guard_and_missing(tmp: Path) -> None:
    case_dir = tmp / "case_d"
    in_dir = case_dir / "input"
    work_dir = case_dir / "work"
    out_dir = case_dir / "output"
    in_dir.mkdir(parents=True)

    (in_dir / "case.json").write_text(json.dumps({"case_id": "test-qa-guard-04", "input_files": ["nonexistent.md"]}), encoding="utf-8")
    (in_dir / "task.md").write_text("What is the operating temperature reported in Table 1?", encoding="utf-8")

    run_step("inspect_case.py", ["--input-dir", str(in_dir), "--out", str(work_dir / "case_plan.json")])
    plan = json.loads((work_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert plan["signals"]["is_non_polish_qa"] is True
    assert "nonexistent.md" in plan["missing_referenced_files"]

    run_step("analyze_manuscript.py", ["--case-plan", str(work_dir / "case_plan.json"), "--out-dir", str(work_dir)])
    run_step(
        "assemble_output.py",
        [
            "--case-plan", str(work_dir / "case_plan.json"),
            "--segments", str(work_dir / "source_segments.json"),
            "--glossary", str(work_dir / "glossary_resolved.json"),
            "--checkpoint", "partial",
            "--out-dir", str(out_dir),
            "--strict",
        ],
    )
    res = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
    assert res["status"] == "not_applicable_qa"
    print("[PASS] Case D: Non-polishing Q&A guard & missing input honesty")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_case_a_english_and_adversarial(tmp)
        test_case_b_conflict_and_bilingual(tmp)
        test_case_c_latex_and_custom_contract(tmp)
        test_case_d_qa_guard_and_missing(tmp)
    print("ALL SKILL-02 SELF-CHECKS PASSED.")


if __name__ == "__main__":
    main()
