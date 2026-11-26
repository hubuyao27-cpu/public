#!/usr/bin/env python3
"""One-command synchronization, verification, and final deliverable assembly."""

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
        timeout=90,
    )
    if proc.returncode != 0 and not allow_fail:
        raise SystemExit(f"{script.name} failed (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync citations, assemble /app/output deliverables, verify 10 invariants, and attach verification_summary.")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--out-dir", default="/app/output")
    ap.add_argument("--plan", default=None)
    ap.add_argument("--allow-partial", action="store_true", help="Exit 0 even if verification errors remain after honest partial assembly")
    args = ap.parse_args()

    sdir = Path(__file__).resolve().parent
    rdir = sdir.parent / "resources"
    work = Path(args.work_dir)
    out_dir = Path(args.out_dir)
    case_plan = work / "case_plan.json"
    lib_json = work / "library" / "library_canonical.json"
    ev_json = work / "library" / "evidence_fulltexts.json"
    ms_json = work / "manuscript" / "manuscript_parsed.json"
    pre_json = work / "verify" / "pre_audit.json"
    plan_json = Path(args.plan) if args.plan else (work / "citation_plan.json")
    sync_dir = work / "synced"
    sync_json = sync_dir / "sync_result.json"
    ver_json = work / "verification_report.json"

    _run(
        sdir / "sync_and_format.py",
        [
            "--case-plan", str(case_plan),
            "--library", str(lib_json),
            "--manuscript", str(ms_json),
            "--pre-audit", str(pre_json),
            "--plan", str(plan_json),
            "--style-rules", str(rdir / "style_rules.json"),
            "--out-dir", str(sync_dir),
        ],
    )
    _run(
        sdir / "assemble_deliverables.py",
        [
            "--case-plan", str(case_plan),
            "--library", str(lib_json),
            "--manuscript", str(ms_json),
            "--pre-audit", str(pre_json),
            "--plan", str(plan_json),
            "--synced", str(sync_json),
            "--checkpoint", "final",
            "--out-dir", str(out_dir),
        ],
    )
    ver_proc = _run(
        sdir / "verify_deliverables.py",
        [
            "--case-plan", str(case_plan),
            "--library", str(lib_json),
            "--evidence", str(ev_json),
            "--manuscript", str(ms_json),
            "--pre-audit", str(pre_json),
            "--plan", str(plan_json),
            "--synced", str(sync_json),
            "--output-dir", str(out_dir),
            "--out", str(ver_json),
        ],
        allow_fail=True,
    )
    _run(
        sdir / "assemble_deliverables.py",
        [
            "--case-plan", str(case_plan),
            "--library", str(lib_json),
            "--manuscript", str(ms_json),
            "--pre-audit", str(pre_json),
            "--plan", str(plan_json),
            "--synced", str(sync_json),
            "--verification", str(ver_json),
            "--checkpoint", "final",
            "--skip-previews",
            "--out-dir", str(out_dir),
        ],
    )
    print(ver_proc.stdout)
    if ver_proc.returncode != 0 and not args.allow_partial:
        raise SystemExit(ver_proc.returncode)


if __name__ == "__main__":
    main()
