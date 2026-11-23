#!/usr/bin/env python3
"""Step 4: Multi-pass LaTeX compilation, log file-line parsing, cascading error clustering,
reproducibility manifest capture, PDF text/ref/glyph auditing, and PNG preview rendering.

Produces:
- /app/work/compile_report.json
- /app/work/compile_summary.md
- PNG page previews in --preview-dir
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    ensure_dir,
    has_cjk,
    load_json_safe,
    read_text_safe,
    resolve_skill_dir,
    run_cmd,
    save_json,
    sha256_file,
    strip_tex_comments_preserve_lines,
    which_tool,
)

RERUN_PATTERNS = (
    "Rerun to get cross-references right",
    "Rerun to get outlines right",
    "Rerun LaTeX",
    "Please (re)run Biber",
    "Table widths have changed. Rerun LaTeX",
)


def detect_engine_and_bib(tex_path: Path, engine_arg: str, bib_arg: str) -> Tuple[str, str]:
    """Choose TeX engine and bibliography backend from source preamble if 'auto'."""
    text, _ = read_text_safe(tex_path)
    clean = strip_tex_comments_preserve_lines(text)

    if engine_arg != "auto":
        engine = engine_arg
    else:
        if has_cjk(clean) or re.search(r"\\documentclass(?:\[[^\]]*\])?\{ctex", clean) or re.search(
            r"\\usepackage(?:\[[^\]]*\])?\{[^}]*(?:xeCJK|fontspec|ctex|unicode-math)", clean
        ):
            engine = "xelatex"
        else:
            engine = "pdflatex"

    if bib_arg != "auto":
        bib_tool = bib_arg
    else:
        if re.search(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*biblatex", clean) or r"\addbibresource" in clean:
            bib_tool = "biber"
        elif r"\bibliography{" in clean or r"\bibliographystyle{" in clean:
            bib_tool = "bibtex"
        else:
            bib_tool = "none"

    return engine, bib_tool


def hash_aux_state(build_dir: Path, stem: str) -> Dict[str, str]:
    """Hash auxiliary files to detect multi-pass convergence accurately."""
    state: Dict[str, str] = {}
    for ext in (".aux", ".toc", ".lof", ".lot", ".out", ".bbl", ".bcf", ".nav", ".snm"):
        p = build_dir / f"{stem}{ext}"
        if p.is_file():
            state[ext] = sha256_file(p)
    return state


def parse_tex_log(log_text: str, taxonomy: Dict[str, Any], default_file: str) -> Dict[str, Any]:
    """Parse TeX .log with file-stack tracking, file:line errors, warnings, and cascade clustering."""
    rules = taxonomy.get("rules", [])
    compiled_rules = [(r, re.compile(r["pattern"])) for r in rules]

    lines = log_text.splitlines()
    file_stack: List[str] = [default_file]
    raw_issues: List[Dict[str, Any]] = []
    missing_chars: List[str] = []
    overfull_hboxes: List[Dict[str, Any]] = []
    loaded_packages: List[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Track file opens/closes heuristically from TeX parens (only paths containing .tex/.sty/.cls/.bbl/.aux)
        for token in re.findall(r"\(([^\s()\"']+\.(?:tex|ltx|sty|cls|def|bbl|aux))", line):
            file_stack.append(token)
        close_count = line.count(")")
        open_count = line.count("(")
        if close_count > open_count and len(file_stack) > 1:
            for _ in range(min(close_count - open_count, len(file_stack) - 1)):
                file_stack.pop()

        if line.startswith("Package: ") or line.startswith("Document Class: "):
            loaded_packages.append(line.strip())

        if "Missing character: There is no " in line:
            missing_chars.append(line.strip())

        om = re.search(r"Overfull \\hbox \(([0-9.]+)pt too wide\)", line)
        if om:
            overfull_hboxes.append(
                {
                    "pt": float(om.group(1)),
                    "file": file_stack[-1],
                    "line": line.strip(),
                }
            )

        # File-line-error style: ./foo.tex:42: Error message
        fl_m = re.match(r"^(.+\.(?:tex|ltx|sty|cls|bbl)):(\d+):\s*(.+)$", line)
        bang_m = line.startswith("! ")
        warn_m = re.match(r"^(LaTeX|Package\s+\S+|Class\s+\S+)\s+Warning:\s*(.+)$", line)

        snippet = ""
        err_file = file_stack[-1]
        err_line: Optional[int] = None
        msg = ""
        kind = None

        if fl_m:
            err_file = fl_m.group(1).strip()
            err_line = int(fl_m.group(2))
            msg = fl_m.group(3).strip()
            kind = "error"
            snippet = "\n".join(lines[i : min(i + 4, len(lines))])
        elif bang_m:
            msg = line[2:].strip()
            kind = "error"
            block = lines[i : min(i + 6, len(lines))]
            snippet = "\n".join(block)
            for bl in block[1:]:
                lm = re.match(r"^l\.(\d+)\b", bl)
                if lm:
                    err_line = int(lm.group(1))
                    break
        elif warn_m:
            msg = line.strip()
            kind = "warning"
            block = [line]
            j = i + 1
            while j < len(lines) and j < i + 4 and lines[j].startswith("               "):
                block.append(lines[j].strip())
                j += 1
            snippet = " ".join(block)
            lm = re.search(r"on input line (\d+)", snippet)
            if lm:
                err_line = int(lm.group(1))

        if kind:
            matched_rule = None
            probe_text = f"{msg}\n{snippet}"
            for r, pat in compiled_rules:
                if pat.search(probe_text):
                    matched_rule = r
                    break

            raw_issues.append(
                {
                    "kind": kind,
                    "rule_id": matched_rule["id"] if matched_rule else ("ERR-GENERIC" if kind == "error" else "WARN-GENERIC"),
                    "category": matched_rule["category"] if matched_rule else "general",
                    "severity": matched_rule["severity"] if matched_rule else kind,
                    "file": err_file,
                    "line": err_line,
                    "message": msg,
                    "fix_recipe": matched_rule["fix_recipe"] if matched_rule else "Inspect flagged file and line in .log.",
                    "cascading_from": None,
                }
            )
        i += 1

    # Cluster cascading downstream errors onto earliest structural root cause
    root_causes = [
        iss
        for iss in raw_issues
        if iss["kind"] == "error"
        and iss["rule_id"] in {"ERR-UNCLOSED-BRACE", "ERR-ENV-MISMATCH", "ERR-MISSING-STY-CLS", "ERR-FONT-NOT-FOUND", "ERR-AMBIGUOUS-TEX-OPT"}
    ]
    if root_causes:
        first_root = root_causes[0]
        first_idx = raw_issues.index(first_root)
        cascade_targets = {"ERR-UNDEFINED-CS", "ERR-EXTRA-RBRACE", "ERR-PERMISSION-IO", "ERR-GENERIC"}
        for idx, iss in enumerate(raw_issues):
            if idx > first_idx and iss["rule_id"] in cascade_targets:
                iss["cascading_from"] = f"{first_root['rule_id']}@{first_root['file']}:{first_root['line']}"

    errors = [x for x in raw_issues if x["kind"] == "error"]
    warnings = [x for x in raw_issues if x["kind"] == "warning"]
    rerun_requested = any(p in log_text for p in RERUN_PATTERNS)

    return {
        "errors": errors,
        "warnings": warnings,
        "root_errors": [e for e in errors if not e["cascading_from"]],
        "missing_characters": missing_chars,
        "overfull_hboxes": overfull_hboxes,
        "large_overfull_hboxes": [o for o in overfull_hboxes if o["pt"] >= 10.0],
        "loaded_packages": loaded_packages[:120],
        "rerun_requested": rerun_requested,
    }


def parse_fls_inputs(fls_path: Path) -> List[str]:
    """Extract unique INPUT files from TeX -recorder .fls file."""
    if not fls_path.is_file():
        return []
    txt, err = read_text_safe(fls_path)
    if err:
        return []
    inputs: List[str] = []
    seen = set()
    for line in txt.splitlines():
        if line.startswith("INPUT "):
            p = line[6:].strip()
            if p not in seen:
                seen.add(p)
                inputs.append(p)
    return inputs[:200]


def render_pdf_previews(pdf_path: Path, preview_dir: Path) -> Tuple[List[str], Dict[str, Any]]:
    """Render PDF pages to PNG previews and audit rendered PDF text for ?? / [?] / blank pages."""
    ensure_dir(preview_dir)
    previews: List[str] = []
    pdf_audit: Dict[str, Any] = {
        "pdf_exists": pdf_path.is_file(),
        "page_count": 0,
        "empty_pages": [],
        "unresolved_markers": [],
        "overflow_findings": [],
        "renderer_used": None,
    }
    if not pdf_path.is_file():
        return previews, pdf_audit

    # Method 1: pdftoppm
    if which_tool("pdftoppm"):
        prefix = preview_dir / "page"
        res = run_cmd(["pdftoppm", "-png", "-r", "150", str(pdf_path), str(prefix)], timeout=45)
        if res["returncode"] == 0:
            pngs = sorted(preview_dir.glob("page-*.png"))
            previews = [str(p) for p in pngs]
            pdf_audit["renderer_used"] = "pdftoppm"

    # Method 2: PyMuPDF (also runs text/bbox audit)
    try:
        try:
            import pymupdf as fitz  # type: ignore
        except ImportError:
            import fitz  # type: ignore

        doc = fitz.open(str(pdf_path))
        pdf_audit["page_count"] = len(doc)
        for pno, page in enumerate(doc, start=1):
            if not previews:
                pix = page.get_pixmap(dpi=150)
                out_png = preview_dir / f"page-{pno:02d}.png"
                pix.save(str(out_png))
                previews.append(str(out_png))
                pdf_audit["renderer_used"] = "pymupdf"

            text = page.get_text("text")
            if not text.strip():
                pdf_audit["empty_pages"].append(pno)
            for marker in re.findall(r"(?:\?\?|\[?\?\]|Undefined control sequence)", text):
                if marker in {"??", "[?]"}:
                    pdf_audit["unresolved_markers"].append({"page": pno, "marker": marker})

            page_w = page.rect.width
            for blk in page.get_text("blocks"):
                x0, y0, x1, y1, btxt = blk[:5]
                if x1 > page_w - 6 and isinstance(btxt, str) and btxt.strip():
                    pdf_audit["overflow_findings"].append(
                        {"page": pno, "x1": round(x1, 1), "page_width": round(page_w, 1), "snippet": btxt.strip()[:80]}
                    )
    except Exception:
        if which_tool("pdftotext"):
            res = run_cmd(["pdftotext", str(pdf_path), "-"], timeout=20)
            if res["returncode"] == 0:
                pages = [p for p in res["stdout"].split("\f")]
                if pages and not pages[-1].strip():
                    pages = pages[:-1]
                pdf_audit["page_count"] = len(pages)
                for pno, ptxt in enumerate(pages, start=1):
                    if not ptxt.strip():
                        pdf_audit["empty_pages"].append(pno)
                    if "??" in ptxt or "[?]" in ptxt:
                        pdf_audit["unresolved_markers"].append({"page": pno, "marker": "??_or_[?]"})

    return previews, pdf_audit


def compile_project(
    tex_path: Path,
    work_dir: Path,
    preview_dir: Path,
    skill_dir: Path,
    engine_arg: str = "auto",
    bib_arg: str = "auto",
    max_passes: int = 4,
    timeout_sec: int = 60,
) -> Dict[str, Any]:
    """Copy project to writable work_dir if external, run multi-pass build, diagnose & preview."""
    tex_path = tex_path.resolve()
    work_dir = ensure_dir(work_dir.resolve())
    taxonomy = load_json_safe(skill_dir / "resources" / "error_taxonomy.json", default={}) or {}

    # Stage inside work_dir unless already under work_dir
    try:
        rel_inside = tex_path.relative_to(work_dir)
        build_tex = tex_path
    except ValueError:
        src_root = tex_path.parent
        for item in src_root.iterdir():
            dest = work_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        build_tex = work_dir / tex_path.name
        rel_inside = Path(tex_path.name)

    build_dir = build_tex.parent
    stem = build_tex.stem
    engine, bib_tool = detect_engine_and_bib(build_tex, engine_arg, bib_arg)

    commands_executed: List[Dict[str, Any]] = []
    eng_flags = ["-interaction=nonstopmode", "-file-line-error", "-recorder"]

    # Clean stale aux/bcf from previous failed tries? Keep only if intentionally resuming; fresh compile starts clean.
    for stale_ext in (".aux", ".bcf", ".bbl", ".blg", ".toc", ".out"):
        sp = build_dir / f"{stem}{stale_ext}"
        if sp.is_file():
            sp.unlink()

    eng_version = ""
    if which_tool(engine):
        ver_res = run_cmd([engine, "--version"], timeout=10)
        eng_version = ((ver_res.get("stdout") or ver_res.get("stderr") or "").splitlines() or [""])[0].strip()

    # Pass 1
    cmd1 = [engine, *eng_flags, build_tex.name]
    r1 = run_cmd(cmd1, cwd=build_dir, timeout=timeout_sec)
    commands_executed.append({"step": "pass_1", "cmd": " ".join(cmd1), "returncode": r1["returncode"], "timed_out": r1["timed_out"], "not_found": r1["not_found"]})

    if r1["not_found"]:
        return {
            "schema_version": "1.0",
            "status": "compiler_missing",
            "engine": engine,
            "engine_version": eng_version,
            "bib_tool": bib_tool,
            "tex_file": str(rel_inside.as_posix()),
            "pdf_path": None,
            "previews": [],
            "converged": False,
            "pass_count": 0,
            "commands_executed": commands_executed,
            "log_diagnosis": {"errors": [{"kind": "error", "rule_id": "ERR-NO-COMPILER", "file": build_tex.name, "line": None, "message": f"TeX compiler '{engine}' is not installed on PATH."}], "warnings": [], "root_errors": [], "missing_characters": [], "overfull_hboxes": [], "large_overfull_hboxes": [], "loaded_packages": [], "rerun_requested": False},
            "bib_diagnostics": [],
            "pdf_audit": {"pdf_exists": False, "page_count": 0, "empty_pages": [], "unresolved_markers": [], "overflow_findings": []},
            "reproducibility_manifest": {"engine": engine, "engine_version": eng_version, "bib_tool": bib_tool, "commands": [c["cmd"] for c in commands_executed], "fls_inputs": []},
        }

    # Decide bib step from actual generated .bcf / .aux
    bib_diagnostics: List[str] = []
    bcf_file = build_dir / f"{stem}.bcf"
    aux_file = build_dir / f"{stem}.aux"
    aux_txt, _ = read_text_safe(aux_file)
    run_bib_tool = None
    if bib_tool == "biber" or bcf_file.is_file():
        run_bib_tool = "biber"
    elif bib_tool == "bibtex" or r"\bibdata{" in aux_txt:
        run_bib_tool = "bibtex"

    if run_bib_tool:
        if which_tool(run_bib_tool):
            bcmd = [run_bib_tool, stem]
            rb = run_cmd(bcmd, cwd=build_dir, timeout=timeout_sec)
            commands_executed.append({"step": "bib_pass", "cmd": " ".join(bcmd), "returncode": rb["returncode"], "timed_out": rb["timed_out"], "not_found": rb["not_found"]})
            blg_txt, _ = read_text_safe(build_dir / f"{stem}.blg")
            for bl in (blg_txt + "\n" + rb["stdout"] + "\n" + rb["stderr"]).splitlines():
                if any(k in bl for k in ("ERROR", "WARN", "Warning--", "I found no", "Repeated entry", "Illegal")):
                    bib_diagnostics.append(bl.strip())
        else:
            bib_diagnostics.append(f"Bibliography tool '{run_bib_tool}' not installed on PATH.")

    prev_aux_hash = hash_aux_state(build_dir, stem)
    converged = False
    max_extra = max(1, max_passes - 1)
    if run_bib_tool and max_extra < 2:
        max_extra = 2

    log_path = build_dir / f"{stem}.log"
    for p_idx in range(2, 2 + max_extra):
        cmd_n = [engine, *eng_flags, build_tex.name]
        rn = run_cmd(cmd_n, cwd=build_dir, timeout=timeout_sec)
        commands_executed.append({"step": f"pass_{p_idx}", "cmd": " ".join(cmd_n), "returncode": rn["returncode"], "timed_out": rn["timed_out"], "not_found": rn["not_found"]})
        new_aux_hash = hash_aux_state(build_dir, stem)
        log_txt, _ = read_text_safe(log_path)
        rerun_needed = any(p in log_txt for p in RERUN_PATTERNS)
        if rn["returncode"] == 0 and new_aux_hash == prev_aux_hash and not rerun_needed and (not run_bib_tool or p_idx >= 3):
            converged = True
            break
        prev_aux_hash = new_aux_hash
        if rn["returncode"] != 0 and p_idx >= 2 and not run_bib_tool:
            # No reason to loop if fatal syntax error persists without bib changes
            break

    log_txt, _ = read_text_safe(log_path)
    diagnosis = parse_tex_log(log_txt, taxonomy, build_tex.name)
    fls_inputs = parse_fls_inputs(build_dir / f"{stem}.fls")

    pdf_path = build_dir / f"{stem}.pdf"
    previews, pdf_audit = render_pdf_previews(pdf_path, preview_dir)

    last_rc = commands_executed[-1]["returncode"]
    if not converged and last_rc == 0 and not diagnosis["errors"] and not diagnosis["rerun_requested"]:
        converged = True

    if last_rc == 0 and not diagnosis["errors"] and pdf_path.is_file() and not diagnosis["missing_characters"] and not pdf_audit["unresolved_markers"]:
        status = "compiled_clean"
    elif pdf_path.is_file():
        status = "compiled_with_issues"
    else:
        status = "compile_failed"

    return {
        "schema_version": "1.0",
        "status": status,
        "engine": engine,
        "engine_version": eng_version,
        "bib_tool": run_bib_tool or "none",
        "tex_file": str(rel_inside.as_posix()),
        "build_dir": str(build_dir),
        "pdf_path": str(pdf_path) if pdf_path.is_file() else None,
        "previews": previews,
        "converged": converged,
        "pass_count": sum(1 for c in commands_executed if c["step"].startswith("pass_")),
        "commands_executed": commands_executed,
        "log_diagnosis": diagnosis,
        "bib_diagnostics": bib_diagnostics[:60],
        "pdf_audit": pdf_audit,
        "reproducibility_manifest": {
            "engine": engine,
            "engine_version": eng_version,
            "bib_tool": run_bib_tool or "none",
            "commands": [c["cmd"] for c in commands_executed],
            "converged": converged,
            "loaded_packages": diagnosis["loaded_packages"],
            "fls_inputs": fls_inputs,
        },
    }


def render_compile_summary(report: Dict[str, Any]) -> str:
    """Render concise Markdown summary of build, root errors, bib issues, and PDF audit."""
    diag = report["log_diagnosis"]
    pa = report["pdf_audit"]
    lines = [
        f"# Compile & Diagnosis Summary (`{report['tex_file']}`)",
        "",
        f"- **Status**: `{report['status']}` | **Engine**: `{report['engine']}` | **Bib**: `{report['bib_tool']}` | **Passes**: {report['pass_count']} | **Converged**: {report['converged']}",
        f"- **PDF**: `{report['pdf_path'] or 'none'}` ({pa.get('page_count', 0)} pages) | **PNG Previews**: {len(report['previews'])}",
        f"- **Errors**: {len(diag['errors'])} total ({len(diag['root_errors'])} root causes) | **Warnings**: {len(diag['warnings'])} | **Missing glyphs**: {len(diag['missing_characters'])} | **Large overfull hbox (>=10pt)**: {len(diag['large_overfull_hboxes'])}",
        "",
    ]
    if diag["errors"]:
        lines.append("## Errors (Root Causes Highlighted)")
        for e in diag["errors"][:30]:
            casc = f" *(cascading from {e['cascading_from']})*" if e.get("cascading_from") else ""
            loc = f"{e['file']}:{e['line']}" if e.get("line") else e["file"]
            lines.append(f"- **[{e['rule_id']}]** `{loc}` — {e['message']}{casc}")
            if not e.get("cascading_from"):
                lines.append(f"  - Fix: {e['fix_recipe']}")
        lines.append("")

    if report["bib_diagnostics"]:
        lines.append("## Bibliography Diagnostics")
        for bd in report["bib_diagnostics"][:20]:
            lines.append(f"- {bd}")
        lines.append("")

    if diag["missing_characters"] or pa.get("unresolved_markers") or pa.get("empty_pages"):
        lines.append("## PDF & Glyph Quality Alerts")
        for mc in diag["missing_characters"][:15]:
            lines.append(f"- Missing glyph: `{mc}`")
        for um in pa.get("unresolved_markers", []):
            lines.append(f"- Unresolved marker on page {um['page']}: `{um['marker']}`")
        if pa.get("empty_pages"):
            lines.append(f"- Empty pages detected: {pa['empty_pages']}")
        lines.append("")

    lines.append("## Reproducible Command Sequence")
    for cmd in report["reproducibility_manifest"]["commands"]:
        lines.append(f"- `{cmd}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-pass LaTeX compile, error clustering, PDF audit, and preview generator.")
    parser.add_argument("--tex", required=True, help="Path to main .tex entry file.")
    parser.add_argument("--work-dir", default="/app/work/build", help="Writable build directory.")
    parser.add_argument("--preview-dir", default="/app/work/previews", help="Directory for rendered PNG page previews.")
    parser.add_argument("--skill-dir", default=None, help="Optional Skill root directory.")
    parser.add_argument("--engine", default="auto", choices=["auto", "pdflatex", "xelatex", "lualatex"])
    parser.add_argument("--bib", default="auto", choices=["auto", "bibtex", "biber", "none"])
    parser.add_argument("--max-passes", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--out-report", default="/app/work/compile_report.json")
    parser.add_argument("--out-summary", default="/app/work/compile_summary.md")
    args = parser.parse_args()

    skill_dir = resolve_skill_dir(args.skill_dir)
    report = compile_project(
        tex_path=Path(args.tex),
        work_dir=Path(args.work_dir),
        preview_dir=Path(args.preview_dir),
        skill_dir=skill_dir,
        engine_arg=args.engine,
        bib_arg=args.bib,
        max_passes=args.max_passes,
        timeout_sec=args.timeout,
    )
    save_json(Path(args.out_report), report)
    sum_path = Path(args.out_summary)
    sum_path.parent.mkdir(parents=True, exist_ok=True)
    sum_path.write_text(render_compile_summary(report), encoding="utf-8")

    print(
        f"[compile_and_diagnose] status={report['status']} engine={report['engine']} "
        f"passes={report['pass_count']} errors={len(report['log_diagnosis']['errors'])} "
        f"previews={len(report['previews'])} -> {args.out_report}"
    )


if __name__ == "__main__":
    main()
