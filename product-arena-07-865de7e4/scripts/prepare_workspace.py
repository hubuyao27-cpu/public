#!/usr/bin/env python3
"""One-command preparation wrapper for latex-sci-composer.

Runs inspect_case -> stage editable /input sources (including inline case.json fields) into /app/work/src
-> analyze_latex -> assemble_deliverables (--checkpoint partial) in a single command to save Harbor requests.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

CONTROL_FILES = {"case.json", "task.md"}


def _run(script: Path, args: list[str]) -> str:
    proc = subprocess.run(
        [sys.executable, "-B", str(script)] + args,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    if proc.returncode != 0:
        raise SystemExit(f"{script.name} failed (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout.strip()


def _stage_sources(input_dir: Path, src_dir: Path, case_plan: dict) -> list[str]:
    src_dir.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    if input_dir.is_dir():
        for item in sorted(input_dir.iterdir()):
            if item.name in CONTROL_FILES or item.name in {".git", "__pycache__"}:
                continue
            dest = src_dir / item.name
            try:
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
                staged.append(item.name)
            except OSError:
                continue

    # Materialize inline case.json tex/bib/draft fields if absent on disk
    raw = case_plan.get("case_json_raw") or {}
    if not any(src_dir.rglob("*.tex")):
        for k in ("source_tex", "tex", "tex_source", "latex", "latex_source"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                (src_dir / "main.tex").write_text(v, encoding="utf-8")
                staged.append(f"main.tex (from case.json#{k})")
                break
    if not any(src_dir.rglob("*.bib")):
        for k in ("bib_text", "bibtex"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                (src_dir / "refs.bib").write_text(v, encoding="utf-8")
                staged.append(f"refs.bib (from case.json#{k})")
                break
    return staged


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspect case, stage editable sources, run static audit, and save partial checkpoint.")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--out-dir", default="/app/output")
    args = ap.parse_args()

    sdir = Path(__file__).resolve().parent
    work = Path(args.work_dir)
    src_dir = work / "src"
    (work / "build").mkdir(parents=True, exist_ok=True)
    (work / "previews").mkdir(parents=True, exist_ok=True)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    case_plan_p = work / "case_plan.json"
    audit_p = work / "latex_audit.json"
    digest_p = work / "latex_digest.md"

    o1 = _run(sdir / "inspect_case.py", ["--input-dir", args.input_dir, "--out", str(case_plan_p)])
    case_plan = json.loads(case_plan_p.read_text(encoding="utf-8"))
    staged = _stage_sources(Path(args.input_dir), src_dir, case_plan)
    o2 = _run(sdir / "analyze_latex.py", ["--case-plan", str(case_plan_p), "--out-audit", str(audit_p), "--out-digest", str(digest_p)])
    o3 = _run(
        sdir / "assemble_deliverables.py",
        ["--case-plan", str(case_plan_p), "--audit", str(audit_p), "--checkpoint", "partial", "--out-dir", args.out_dir],
    )
    print(json.dumps({
        "status": "ok",
        "inspect": o1,
        "staged_to_work_src": staged,
        "analyze": o2,
        "partial_checkpoint": o3,
        "digest": str(digest_p),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
