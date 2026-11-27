#!/usr/bin/env python3
"""One-command verification + final deliverable assembly wrapper for sci-intro-rw-writer.

Runs verify_intro_rw.py -> if passed, invokes assemble_deliverables.py --checkpoint final;
if verification fails, refreshes --checkpoint partial so /app/output/result.json remains honest
and exits non-zero with exact fix hints.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify payload and assemble Harbor deliverables in one step.")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--out-dir", default="/app/output")
    ap.add_argument("--payload", default=None)
    ap.add_argument("--manuscript", default=None)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    sdir = Path(__file__).resolve().parent
    work = Path(args.work_dir)
    out = Path(args.out_dir)
    case_plan = work / "case_plan.json"
    ledger = work / "source_ledger.json"
    payload = Path(args.payload) if args.payload else (work / "section_payload.json")
    vreport = work / "verification_report.json"

    v_cmd = [
        sys.executable,
        "-B",
        str(sdir / "verify_intro_rw.py"),
        "--payload",
        str(payload),
        "--ledger",
        str(ledger),
        "--case-plan",
        str(case_plan),
        "--out",
        str(vreport),
    ]
    if args.manuscript:
        v_cmd.extend(["--manuscript", args.manuscript])
    if args.strict:
        v_cmd.append("--strict")

    v_proc = subprocess.run(v_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    if v_proc.returncode != 0:
        # Preserve authored payload honestly under --checkpoint partial so work is NEVER overwritten with an empty stub
        p_cmd = [
            sys.executable,
            "-B",
            str(sdir / "assemble_deliverables.py"),
            "--case-plan",
            str(case_plan),
            "--ledger",
            str(ledger),
            "--verification",
            str(vreport),
            "--checkpoint",
            "partial",
            "--out-dir",
            str(out),
        ]
        if payload.exists():
            p_cmd.extend(["--payload", str(payload)])
        if args.manuscript:
            p_cmd.extend(["--manuscript", args.manuscript])
        subprocess.run(p_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        sys.stdout.write(v_proc.stdout)
        sys.stderr.write(v_proc.stderr)
        raise SystemExit(v_proc.returncode)

    a_cmd = [
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
        str(vreport),
        "--checkpoint",
        "final",
        "--out-dir",
        str(out),
    ]
    if args.manuscript:
        a_cmd.extend(["--manuscript", args.manuscript])
    a_proc = subprocess.run(a_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    if a_proc.returncode != 0:
        sys.stdout.write(a_proc.stdout)
        sys.stderr.write(a_proc.stderr)
        raise SystemExit(a_proc.returncode)

    try:
        v_obj = json.loads(vreport.read_text(encoding="utf-8"))
        a_obj = json.loads(a_proc.stdout.strip().splitlines()[-1])
    except Exception:
        v_obj, a_obj = {}, {"raw": a_proc.stdout.strip()}
    print(json.dumps({"status": "ok", "verification_passed": v_obj.get("passed", True), "warnings": v_obj.get("warning_count", 0), "assemble": a_obj}, ensure_ascii=False))


if __name__ == "__main__":
    main()
