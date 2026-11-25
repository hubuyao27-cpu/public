#!/usr/bin/env python3
"""One-command final compilation, 6-axis verification, and final /app/output assembly for latex-sci-composer."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _run(script: Path, args: list[str], allow_fail: bool = False) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [sys.executable, "-B", str(script)] + args,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if proc.returncode != 0 and not allow_fail:
        raise SystemExit(f"{script.name} failed (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc


def main() -> None:
    ap = argparse.ArgumentParser(description="Compile .tex (with auto-fallback diagnostics), verify 6 axes, and assemble /app/output.")
    ap.add_argument("--tex", required=True, help="Path to main editable .tex file under /app/work/src")
    ap.add_argument("--payload", default="/app/work/deliverable_payload.json")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--out-dir", default="/app/output")
    ap.add_argument("--engine", default="auto", choices=["auto", "pdflatex", "xelatex", "lualatex"])
    ap.add_argument("--bib", default="auto", choices=["auto", "bibtex", "biber", "none"])
    ap.add_argument("--max-passes", type=int, default=5)
    ap.add_argument("--skip-compile", action="store_true", help="Skip compilation (use for honest blocked/unable_to_determine cases).")
    args = ap.parse_args()

    sdir = Path(__file__).resolve().parent
    work = Path(args.work_dir)
    out_dir = Path(args.out_dir)
    case_plan = work / "case_plan.json"
    audit = work / "latex_audit.json"
    compile_report = work / "compile_report.json"
    compile_summary = work / "compile_summary.md"
    ver_report = work / "verification_report.json"
    tex_path = Path(args.tex)

    if not args.skip_compile and tex_path.is_file():
        _run(
            sdir / "compile_and_diagnose.py",
            [
                "--tex", str(tex_path),
                "--work-dir", str(work / "build"),
                "--preview-dir", str(work / "previews"),
                "--engine", args.engine,
                "--bib", args.bib,
                "--max-passes", str(args.max_passes),
                "--out-report", str(compile_report),
                "--out-summary", str(compile_summary),
            ],
        )

    ver_args = [
        "--payload", args.payload,
        "--case-plan", str(case_plan),
        "--audit", str(audit),
        "--work-tex-dir", str(tex_path.parent if tex_path.is_file() else (work / "src")),
        "--out", str(ver_report),
    ]
    if compile_report.is_file():
        ver_args.extend(["--compile-report", str(compile_report)])
    ver_proc = _run(sdir / "verify_deliverable.py", ver_args, allow_fail=True)

    asm_args = [
        "--case-plan", str(case_plan),
        "--audit", str(audit),
        "--payload", args.payload,
        "--verification", str(ver_report),
        "--work-tex-dir", str(tex_path.parent if tex_path.is_file() else (work / "src")),
        "--checkpoint", "final",
        "--out-dir", str(out_dir),
    ]
    if compile_report.is_file():
        asm_args.extend(["--compile-report", str(compile_report)])
    asm_proc = _run(sdir / "assemble_deliverables.py", asm_args)

    summary_out = {
        "verify_rc": ver_proc.returncode,
        "verify_stdout": ver_proc.stdout.strip(),
        "assemble_stdout": asm_proc.stdout.strip(),
        "compile_summary": str(compile_summary) if compile_summary.is_file() else None,
        "verification_report": str(ver_report),
    }
    if ver_proc.returncode != 0 and ver_report.is_file():
        try:
            vr = json.loads(ver_report.read_text(encoding="utf-8"))
            summary_out["errors"] = vr.get("errors", [])
        except Exception:
            pass
    print(json.dumps(summary_out, ensure_ascii=False, indent=2))
    if ver_proc.returncode != 0:
        raise SystemExit(ver_proc.returncode)


if __name__ == "__main__":
    main()
