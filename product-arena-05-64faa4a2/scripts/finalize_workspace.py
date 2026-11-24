#!/usr/bin/env python3
"""One-command verification and final deliverable assembly for sci-methods-writer.

Always updates /app/output/result.json and editable deliverables (choosing honest
'completed' vs 'partial' status from verification_report.json) so budget limits never
leave stale partial outputs behind.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify chapter_payload.json and assemble final /app/output deliverables.")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--out-dir", default="/app/output")
    ap.add_argument("--payload", default=None)
    ap.add_argument("--allow-partial", action="store_true", help="Exit 0 even if verification warnings/errors remain after assembling honest partial output")
    args = ap.parse_args()

    sdir = Path(__file__).resolve().parent
    work = Path(args.work_dir)
    out_dir = Path(args.out_dir)
    case_plan = work / "case_plan.json"
    ledger = work / "fact_ledger.json"
    payload = Path(args.payload) if args.payload else (work / "chapter_payload.json")
    vrep = work / "verification_report.json"

    ver_proc = subprocess.run(
        [sys.executable, "-B", str(sdir / "verify_chapter.py"), "--payload", str(payload), "--ledger", str(ledger), "--case-plan", str(case_plan), "--out", str(vrep)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    asm_proc = subprocess.run(
        [
            sys.executable,
            "-B",
            str(sdir / "assemble_deliverables.py"),
            "--case-plan",
            str(case_plan),
            "--ledger",
            str(ledger),
            "--payload",
            str(payload),
            "--verification",
            str(vrep),
            "--checkpoint",
            "final",
            "--out-dir",
            str(out_dir),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if ver_proc.stdout.strip():
        print(ver_proc.stdout.strip())
    if asm_proc.stdout.strip():
        print(asm_proc.stdout.strip())
    if asm_proc.returncode != 0:
        raise SystemExit(asm_proc.returncode)
    if ver_proc.returncode != 0 and not args.allow_partial:
        raise SystemExit(ver_proc.returncode)


if __name__ == "__main__":
    main()
