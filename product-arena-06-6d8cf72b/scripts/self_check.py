#!/usr/bin/env python3
"""Self-check script for Harbor compliance and end-to-end minimal example execution."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


MAX_FILES = 50
MAX_FILE_BYTES = 500 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024


def check_harbor_constraints(skill_dir: Path) -> None:
    assert (skill_dir / "SKILL.md").exists(), "Missing SKILL.md"
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert skill_text.startswith("---\n"), "SKILL.md missing YAML frontmatter start"
    assert "name:" in skill_text[:400] and "description:" in skill_text[:600], "SKILL.md missing name/description frontmatter"

    files = [p for p in skill_dir.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    for p in skill_dir.rglob("*"):
        assert not p.is_symlink(), f"Symlink prohibited: {p}"

    assert len(files) <= MAX_FILES, f"Too many files: {len(files)} > {MAX_FILES}"
    total_bytes = 0
    for f in files:
        sz = f.stat().st_size
        assert sz <= MAX_FILE_BYTES, f"File exceeds 500KB: {f} ({sz} bytes)"
        # Check UTF-8 decodability
        f.read_text(encoding="utf-8")
        total_bytes += sz
    assert total_bytes <= MAX_TOTAL_BYTES, f"Total size exceeds 2MB: {total_bytes} bytes"
    gh_file = skill_dir / "GITHUB_URL.txt"
    if gh_file.exists():
        first_line = gh_file.read_text(encoding="utf-8").splitlines()[0].strip()
        assert first_line.startswith("https://github.com/"), f"Invalid GITHUB_URL.txt URL: {first_line}"
    print(f"[OK] Harbor resource constraints: {len(files)} UTF-8 text files, {total_bytes} bytes total.")


def run_step(cmd: list[str]) -> None:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if proc.returncode != 0:
        print("STDOUT:", proc.stdout)
        print("STDERR:", proc.stderr)
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}")
    if proc.stdout.strip():
        print(" ", proc.stdout.strip())


def run_minimal_e2e(skill_dir: Path) -> None:
    scripts = skill_dir / "scripts"
    examples = skill_dir / "examples"

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        in_dir = tmp / "input"
        work_dir = tmp / "work"
        out_dir = tmp / "output"
        in_dir.mkdir()
        (in_dir / "sample_paper.txt").write_text((examples / "sample_paper.txt").read_text(encoding="utf-8"), encoding="utf-8")
        (in_dir / "case.json").write_text(
            json.dumps(
                {
                    "case_id": "self-check-demo",
                    "questions": [
                        "Compared with Ru/Al2O3, how much does CeO2 promotion change activation energy and under what operating conditions?",
                        "Did the paper report catalyst cost or 1,000-hour stability?",
                    ],
                    "output_contract": {
                        "case_id": "string",
                        "status": "string",
                        "answers": "array",
                        "paper_card": "object",
                        "section_summaries": "array",
                        "knowledge_items": "array",
                        "evidence": "array",
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        py = sys.executable
        run_step([py, str(scripts / "inspect_case.py"), "--input-dir", str(in_dir), "--out", str(work_dir / "case_plan.json")])
        run_step([py, str(scripts / "parse_paper.py"), "--case-plan", str(work_dir / "case_plan.json"), "--out-dir", str(work_dir / "parsed")])
        run_step(
            [
                py,
                str(scripts / "verify_notes.py"),
                "--source",
                str(work_dir / "parsed" / "source_index.json"),
                "--outline",
                str(work_dir / "parsed" / "section_outline.json"),
                "--notes",
                str(examples / "sample_notes_payload.json"),
                "--case-plan",
                str(work_dir / "case_plan.json"),
                "--extraction",
                str(work_dir / "parsed" / "extraction_report.json"),
                "--out",
                str(work_dir / "verification_report.json"),
            ]
        )
        run_step(
            [
                py,
                str(scripts / "assemble_notes.py"),
                "--case-plan",
                str(work_dir / "case_plan.json"),
                "--extraction",
                str(work_dir / "parsed" / "extraction_report.json"),
                "--outline",
                str(work_dir / "parsed" / "section_outline.json"),
                "--notes",
                str(examples / "sample_notes_payload.json"),
                "--verification",
                str(work_dir / "verification_report.json"),
                "--checkpoint",
                "final",
                "--out-dir",
                str(out_dir),
            ]
        )

        ver = json.loads((work_dir / "verification_report.json").read_text(encoding="utf-8"))
        res = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
        assert ver["passed"] is True, f"Verification failed: {ver['issues']}"
        assert res["status"] == "success", f"Unexpected status: {res['status']}"
        assert res.get("answers"), "Contract field 'answers' was not populated!"
        assert res.get("evidence"), "Contract field 'evidence' was not populated!"
        for artifact in ("result.json", "structured_notes.md", "knowledge_items.jsonl", "paper_card_row.csv", "verification_report.json"):
            assert (out_dir / artifact).exists(), f"Missing output artifact: {artifact}"
        print("[OK] End-to-end minimal example passed with status=success and 100% verification.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Self-check Harbor constraints and minimal E2E workflow.")
    parser.add_argument("--skill-dir", default=".", help="Path to skill root directory containing SKILL.md.")
    args = parser.parse_args()

    skill_dir = Path(args.skill_dir).resolve()
    check_harbor_constraints(skill_dir)
    run_minimal_e2e(skill_dir)


if __name__ == "__main__":
    main()
