#!/usr/bin/env python3
"""One-command final compilation, 6-axis verification, and final /app/output assembly for latex-sci-composer."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def _run(script: Path, args: list[str], allow_fail: bool = False, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [sys.executable, "-B", str(script)] + args,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0 and not allow_fail:
        raise SystemExit(f"{script.name} failed (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc


def _hash_project_tree(root: Path) -> str:
    h = hashlib.sha256()
    if not root.is_dir():
        return ""
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith("_fallback") or part in {".git", "__pycache__"} for part in p.parts):
            continue
        if p.suffix.lower() in {".aux", ".log", ".fls", ".fdb_latexmk", ".bbl", ".blg", ".bcf", ".toc", ".out", ".lof", ".lot", ".run.xml", ".synctex.gz"}:
            continue
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description="Compile .tex (with auto-fallback diagnostics), verify 6 axes, and assemble /app/output.")
    ap.add_argument("--tex", default=None, help="Path to main editable .tex file under /app/work/src (optional when --skip-compile)")
    ap.add_argument("--project-root", default=None, help="Root directory of editable project tree (defaults to <work-dir>/src when applicable)")
    ap.add_argument("--payload", default="/app/work/deliverable_payload.json")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--out-dir", default="/app/output")
    ap.add_argument("--engine", default="auto", choices=["auto", "pdflatex", "xelatex", "lualatex"])
    ap.add_argument("--bib", default="auto", choices=["auto", "bibtex", "biber", "none"])
    ap.add_argument("--max-passes", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=300, help="Wrapper subprocess timeout in seconds (default 300).")
    ap.add_argument("--skip-compile", action="store_true", help="Skip compilation (use for honest blocked/unable_to_determine cases).")
    ap.add_argument("--force-compile", action="store_true", help="Force recompile even if staged source tree hash is unchanged.")
    args = ap.parse_args()

    sdir = Path(__file__).resolve().parent
    work = Path(args.work_dir)
    out_dir = Path(args.out_dir)
    case_plan = work / "case_plan.json"
    audit = work / "latex_audit.json"
    compile_report = work / "compile_report.json"
    compile_summary = work / "compile_summary.md"
    ver_report = work / "verification_report.json"
    tex_path = Path(args.tex).resolve() if args.tex else None

    if args.project_root:
        project_root = Path(args.project_root).resolve()
    elif tex_path and (work / "src").is_dir():
        try:
            tex_path.relative_to((work / "src").resolve())
            project_root = (work / "src").resolve()
        except ValueError:
            project_root = tex_path.parent
    elif tex_path and tex_path.is_file():
        project_root = tex_path.parent
    else:
        project_root = (work / "src").resolve() if (work / "src").is_dir() else work.resolve()

    used_compile_report = False
    if not args.skip_compile:
        if tex_path is None or not tex_path.is_file():
            raise SystemExit(f"finalize_workspace.py requires an existing --tex file unless --skip-compile is set (got: {args.tex})")
        tree_hash = _hash_project_tree(project_root)
        reuse_existing = False
        if not args.force_compile and compile_report.is_file():
            try:
                prev_rep = json.loads(compile_report.read_text(encoding="utf-8"))
                if (
                    prev_rep.get("_staged_sources_sha256") == tree_hash
                    and prev_rep.get("engine_arg") == args.engine
                    and prev_rep.get("bib_arg") == args.bib
                    and prev_rep.get("status") == "compiled_clean"
                ):
                    reuse_existing = True
            except Exception:
                reuse_existing = False
        if not reuse_existing:
            _run(
                sdir / "compile_and_diagnose.py",
                [
                    "--tex", str(tex_path),
                    "--project-root", str(project_root),
                    "--work-dir", str(work / "build"),
                    "--preview-dir", str(work / "previews"),
                    "--engine", args.engine,
                    "--bib", args.bib,
                    "--max-passes", str(args.max_passes),
                    "--out-report", str(compile_report),
                    "--out-summary", str(compile_summary),
                ],
                timeout=args.timeout,
            )
            if compile_report.is_file():
                try:
                    rep_obj = json.loads(compile_report.read_text(encoding="utf-8"))
                    rep_obj["_staged_sources_sha256"] = tree_hash
                    rep_obj["engine_arg"] = args.engine
                    rep_obj["bib_arg"] = args.bib
                    compile_report.write_text(json.dumps(rep_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                except Exception:
                    pass
        used_compile_report = compile_report.is_file()

    ver_args = [
        "--payload", args.payload,
        "--case-plan", str(case_plan),
        "--audit", str(audit),
        "--work-tex-dir", str(project_root),
        "--out", str(ver_report),
    ]
    if used_compile_report:
        ver_args.extend(["--compile-report", str(compile_report)])
    else:
        ver_args.extend(["--compile-report", ""])
    ver_proc = _run(sdir / "verify_deliverable.py", ver_args, allow_fail=True, timeout=args.timeout)

    asm_args = [
        "--case-plan", str(case_plan),
        "--audit", str(audit),
        "--payload", args.payload,
        "--verification", str(ver_report),
        "--work-tex-dir", str(project_root),
        "--checkpoint", "final",
        "--out-dir", str(out_dir),
    ]
    if used_compile_report:
        asm_args.extend(["--compile-report", str(compile_report)])
    asm_proc = _run(sdir / "assemble_deliverables.py", asm_args, timeout=args.timeout)

    summary_out = {
        "verify_rc": ver_proc.returncode,
        "verify_stdout": ver_proc.stdout.strip(),
        "assemble_stdout": asm_proc.stdout.strip(),
        "compile_summary": str(compile_summary) if used_compile_report and compile_summary.is_file() else None,
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
