#!/usr/bin/env python3
"""One-command preparation wrapper for citation-reference-manager.

Runs inspect_case -> parse_manuscript -> parse_and_dedup_library -> verify_sources_pre
(emitting /app/work/citation_plan_skeleton.json) -> assemble_deliverables (--checkpoint partial)
in a single command to conserve Harbor request budget.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _run(script: Path, args: list[str]) -> dict:
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
    try:
        return json.loads(proc.stdout.strip())
    except Exception:
        return {"raw_stdout": proc.stdout.strip()}


def main() -> None:
    ap = argparse.ArgumentParser(description="Prepare workspace, digests, skeleton plan, and partial checkpoint.")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--out-dir", default="/app/output")
    args = ap.parse_args()

    sdir = Path(__file__).resolve().parent
    rdir = sdir.parent / "resources"
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    case_plan = work / "case_plan.json"
    ms_dir = work / "manuscript"
    lib_dir = work / "library"
    ver_dir = work / "verify"
    skeleton = work / "citation_plan_skeleton.json"

    r1 = _run(sdir / "inspect_case.py", ["--input-dir", args.input_dir, "--out", str(case_plan)])
    r2 = _run(sdir / "parse_manuscript.py", ["--case-plan", str(case_plan), "--out-dir", str(ms_dir)])
    r3 = _run(
        sdir / "parse_and_dedup_library.py",
        [
            "--case-plan", str(case_plan),
            "--manuscript", str(ms_dir / "manuscript_parsed.json"),
            "--rules", str(rdir / "dedup_rules.json"),
            "--style-rules", str(rdir / "style_rules.json"),
            "--out-dir", str(lib_dir),
        ],
    )
    r4 = _run(
        sdir / "verify_sources_pre.py",
        [
            "--case-plan", str(case_plan),
            "--library", str(lib_dir / "library_canonical.json"),
            "--evidence", str(lib_dir / "evidence_fulltexts.json"),
            "--manuscript", str(ms_dir / "manuscript_parsed.json"),
            "--out-dir", str(ver_dir),
            "--out-skeleton", str(skeleton),
        ],
    )
    r5 = _run(
        sdir / "assemble_deliverables.py",
        [
            "--case-plan", str(case_plan),
            "--library", str(lib_dir / "library_canonical.json"),
            "--manuscript", str(ms_dir / "manuscript_parsed.json"),
            "--pre-audit", str(ver_dir / "pre_audit.json"),
            "--checkpoint", "partial",
            "--out-dir", args.out_dir,
        ],
    )
    print(json.dumps({
        "status": "ok",
        "inspect": r1,
        "manuscript": r2,
        "library": r3,
        "pre_audit": r4,
        "partial_checkpoint": r5,
        "skeleton": str(skeleton),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
