#!/usr/bin/env python3
"""One-command preparation wrapper for sci-intro-rw-writer.

Runs inspect_case.py -> ingest_materials.py (producing source_ledger.json,
evidence_digest.md, and section_payload_skeleton.json) -> assemble_deliverables.py
(--checkpoint partial) in a single turn to conserve execution budget.
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
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"{script.name} failed (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return {"raw_stdout": proc.stdout.strip()}


def main() -> None:
    ap = argparse.ArgumentParser(description="Prepare Harbor workspace and honest partial checkpoint in one command.")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--out-dir", default="/app/output")
    args = ap.parse_args()

    sdir = Path(__file__).resolve().parent
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    case_plan = work / "case_plan.json"
    ledger = work / "source_ledger.json"
    digest = work / "evidence_digest.md"
    skeleton = work / "section_payload_skeleton.json"

    r1 = _run(sdir / "inspect_case.py", ["--input-dir", args.input_dir, "--out", str(case_plan)])
    r2 = _run(
        sdir / "ingest_materials.py",
        ["--case-plan", str(case_plan), "--out-ledger", str(ledger), "--out-digest", str(digest), "--out-skeleton", str(skeleton)],
    )
    r3 = _run(
        sdir / "assemble_deliverables.py",
        ["--case-plan", str(case_plan), "--ledger", str(ledger), "--checkpoint", "partial", "--out-dir", args.out_dir],
    )
    print(json.dumps({"status": "ok", "inspect": r1, "ingest": r2, "partial": r3, "skeleton": str(skeleton)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
