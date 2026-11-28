#!/usr/bin/env python3
"""Step 2: Multi-pass LaTeX compilation, log file-line parsing, cascading error clustering,
non-destructive engine/CJK/package fallback diagnostics, PDF audit (fonts, ??, glyphs), and PNG previews.

Produces:
- /app/work/compile_report.json
- /app/work/compile_summary.md
- PNG page previews in --preview-dir
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    SKIP_DIRS,
    ensure_dir,
    extract_tex_commands,
    fandol_is_available,
    has_cjk,
    load_json_safe,
    pick_best_cjk_font,
    read_text_safe,
    resolve_skill_dir,
    resolve_tex_target,
    run_cmd,
    sanitize_tex_for_scan,
    save_json,
    sha256_file,
    which_tool,
)

TEX_LOG_ENV = {
    "max_print_line": "10000",
    "error_line": "254",
    "half_error_line": "238",
}

RERUN_PATTERNS = (
    "Rerun to get cross-references right",
    "Rerun to get outlines right",
    "Rerun to get citations correct",
    "Rerun to get bibliographical references right",
    "Label(s) may have changed",
    "There were undefined references",
    "Rerun LaTeX",
    "Please rerun LaTeX",
    "Please (re)run Biber",
    "Please (re)run BibTeX",
    "Table widths have changed. Rerun LaTeX",
)

BIB_RERUN_PATTERNS = ("Please (re)run Biber", "Please (re)run BibTeX")
AUX_KEEP_CLEAN_EXTS = {".aux", ".log", ".fls", ".fdb_latexmk", ".bbl", ".blg", ".bcf", ".toc", ".out", ".lof", ".lot", ".run.xml", ".synctex.gz"}


def hash_project_tree(root: Path) -> str:
    """Hash editable source tree (excluding build artifacts and _fallback* scratch dirs)."""
    h = hashlib.sha256()
    if not root.is_dir():
        return ""
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith("_fallback") or part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in AUX_KEEP_CLEAN_EXTS:
            continue
        h.update(p.relative_to(root).as_posix().encode("utf-8"))
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
    return h.hexdigest()


def _build_tex_search_env(project_root: Path, build_dir: Path) -> Dict[str, str]:
    env = dict(TEX_LOG_ENV)
    tex_dirs: List[str] = []
    bib_dirs: List[str] = []
    bst_dirs: List[str] = []

    def _add(lst: List[str], d: Path) -> None:
        s = str(d.resolve())
        if s not in lst:
            lst.append(s)

    if project_root.is_dir():
        _add(tex_dirs, project_root)
        _add(bib_dirs, project_root)
        _add(bst_dirs, project_root)
        for p in sorted(project_root.rglob("*")):
            if not p.is_file() or any(part.startswith("_fallback") or part in SKIP_DIRS for part in p.parts):
                continue
            ext = p.suffix.lower()
            if ext in {".cls", ".sty", ".def", ".clo", ".tex", ".ltx", ".bbx", ".cbx", ".lbx", ".cfg", ".fd"}:
                _add(tex_dirs, p.parent)
            elif ext == ".bib":
                _add(bib_dirs, p.parent)
            elif ext == ".bst":
                _add(bst_dirs, p.parent)

    sep = os.pathsep
    if len(tex_dirs) > 1 or project_root.resolve() != build_dir.resolve():
        env["TEXINPUTS"] = sep.join(["."] + tex_dirs) + sep
        env["BIBINPUTS"] = sep.join(["."] + bib_dirs) + sep
        env["BSTINPUTS"] = sep.join(["."] + bst_dirs) + sep
    return env


def _collect_reachable_tex(tex_path: Path, project_root: Path, max_depth: int = 4) -> str:
    visited: Set[Path] = set()
    chunks: List[str] = []

    def _walk(cur: Path, depth: int) -> None:
        res_cur = cur.resolve()
        if res_cur in visited or depth > max_depth or not res_cur.is_file():
            return
        visited.add(res_cur)
        txt, err = read_text_safe(res_cur)
        if err:
            return
        clean = sanitize_tex_for_scan(txt)
        chunks.append(clean)
        for inc in extract_tex_commands(clean, ("input", "include", "subfile"), strip_comments=False):
            if inc["arg"]:
                tgt = resolve_tex_target(inc["arg"], res_cur, project_root)
                if tgt:
                    _walk(tgt, depth + 1)

    _walk(tex_path, 0)
    return "\n".join(chunks)


def detect_engine_and_bib(tex_path: Path, engine_arg: str, bib_arg: str, project_root: Optional[Path] = None) -> Tuple[str, str]:
    clean = _collect_reachable_tex(tex_path, project_root or tex_path.parent)
    if engine_arg != "auto":
        engine = engine_arg
    elif has_cjk(clean) or re.search(r"\\documentclass(?:\[[^\]]*\])?\{ctex", clean) or re.search(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*(?:xeCJK|fontspec|ctex|unicode-math)", clean):
        engine = "xelatex"
    else:
        engine = "pdflatex"

    if bib_arg != "auto":
        bib_tool = bib_arg
    else:
        m_bl = re.search(r"\\usepackage(?:\[([^\]]*)\])?\{[^}]*biblatex", clean)
        if m_bl or r"\addbibresource" in clean:
            bl_opts = (m_bl.group(1) if m_bl and m_bl.group(1) else "").lower()
            bib_tool = "bibtex" if "backend=bibtex" in bl_opts else "biber"
        elif r"\bibliography{" in clean or r"\bibliographystyle{" in clean:
            bib_tool = "bibtex"
        else:
            bib_tool = "none"
    return engine, bib_tool


def hash_aux_state(build_dir: Path, stem: str) -> Dict[str, str]:
    state: Dict[str, str] = {}
    for ext in (".aux", ".toc", ".lof", ".lot", ".out", ".bbl", ".bcf", ".nav", ".snm"):
        p = build_dir / f"{stem}{ext}"
        if p.is_file():
            state[ext] = sha256_file(p)
    for aux_p in sorted(build_dir.rglob("*.aux")):
        if not any(part.startswith("_fallback") for part in aux_p.parts):
            state[f"aux:{aux_p.relative_to(build_dir).as_posix()}"] = sha256_file(aux_p)
    return state


def parse_tex_log(log_text: str, taxonomy: Dict[str, Any], default_file: str) -> Dict[str, Any]:
    """Parse TeX .log with balanced paren stack, file:line errors, biblatex multi-line keys, and cascade clustering."""
    compiled_rules = [(r, re.compile(r["pattern"])) for r in taxonomy.get("rules", [])]
    lines = log_text.splitlines()
    paren_stack: List[Optional[str]] = [default_file]

    def _current_file() -> str:
        return next((item for item in reversed(paren_stack) if item), default_file)

    raw_issues: List[Dict[str, Any]] = []
    missing_chars, loaded_packages = [], []
    overfull_hboxes, overfull_vboxes = [], []
    undefined_references, undefined_citations = [], []

    i = 0
    in_biblatex_missing = False
    while i < len(lines):
        line = lines[i]
        pos_p, llen = 0, len(line)
        while pos_p < llen:
            ch = line[pos_p]
            if ch == "(":
                m_f = re.match(r"\"?([^\s()\"']+\.(?:tex|ltx|sty|cls|def|bbl|aux))\"?", line[pos_p + 1 :])
                if m_f:
                    paren_stack.append(m_f.group(1))
                    pos_p += 1 + m_f.end()
                    continue
                paren_stack.append(None)
            elif ch == ")" and len(paren_stack) > 1:
                paren_stack.pop()
            pos_p += 1

        cur_file = _current_file()
        if line.startswith(("Package: ", "Document Class: ")):
            loaded_packages.append(line.strip())
        if "Missing character: There is no " in line:
            missing_chars.append(line.strip())

        om = re.search(r"Overfull \\hbox \(([0-9.]+)pt too wide\)", line)
        if om:
            overfull_hboxes.append({"pt": float(om.group(1)), "file": cur_file, "line": line.strip()})
        vm = re.search(r"Overfull \\vbox \(([0-9.]+)pt too high\)", line)
        if vm:
            overfull_vboxes.append({"pt": float(vm.group(1)), "file": cur_file, "line": line.strip()})

        ur = re.search(r"Reference [`']([^']+)' on page (\S+) undefined(?: on input line (\d+))?", line)
        if ur:
            undefined_references.append({"key": ur.group(1), "page": ur.group(2), "line": int(ur.group(3)) if ur.group(3) else None, "file": cur_file})
        uc = re.search(r"Citation [`']([^']+)' on page (\S+) undefined(?: on input line (\d+))?", line)
        if uc:
            undefined_citations.append({"key": uc.group(1), "page": uc.group(2), "line": int(uc.group(3)) if uc.group(3) else None, "file": cur_file})

        if "The following entry could not be found" in line:
            in_biblatex_missing = True
        elif in_biblatex_missing:
            m_bl_cont = re.match(r"^\(biblatex\)\s+(.+)$", line)
            if m_bl_cont:
                tok_bl = m_bl_cont.group(1).strip()
                if tok_bl and not tok_bl.startswith(("in the database", "Please verify", "and rerun")):
                    undefined_citations.append({"key": tok_bl.lstrip("- "), "page": "?", "line": None, "file": cur_file})
            else:
                in_biblatex_missing = False

        if "Empty bibliography on input line" in line or "Package biblatex Warning: Empty bibliography" in line:
            lm_eb = re.search(r"on input line (\d+)", line)
            undefined_citations.append({"key": "<empty_bibliography>", "page": "?", "line": int(lm_eb.group(1)) if lm_eb else None, "file": cur_file})

        fl_m = re.match(r"^(.+\.(?:tex|ltx|sty|cls|bbl)):(\d+):\s*(.+)$", line)
        warn_m = re.match(r"^(LaTeX(?:\s+Font)?|Package\s+\S+|Class\s+\S+|pdfTeX)\s+[Ww]arning:\s*(.+)$", line)
        kind, err_file, err_line, msg, snippet = None, cur_file, None, "", ""

        if fl_m:
            err_file, err_line, msg, kind = fl_m.group(1).strip(), int(fl_m.group(2)), fl_m.group(3).strip(), "error"
            snippet = "\n".join(lines[i : min(i + 4, len(lines))])
        elif line.startswith("! "):
            msg, kind = line[2:].strip(), "error"
            block = lines[i : min(i + 6, len(lines))]
            snippet = "\n".join(block)
            for bl in block[1:]:
                lm = re.match(r"^l\.(\d+)\b", bl)
                if lm:
                    err_line = int(lm.group(1))
                    break
        elif warn_m:
            msg, kind = line.strip(), "warning"
            block = [line]
            j = i + 1
            while j < len(lines) and j < i + 5 and (lines[j].startswith("               ") or re.match(r"^\([A-Za-z0-9_-]+\)\s+", lines[j])):
                block.append(lines[j].strip())
                j += 1
            snippet = " ".join(block)
            lm = re.search(r"on input line (\d+)", snippet)
            if lm:
                err_line = int(lm.group(1))

        if kind:
            probe_text = f"{msg}\n{snippet}"
            matched = next((r for r, pat in compiled_rules if pat.search(probe_text)), None)
            raw_issues.append({
                "kind": kind,
                "rule_id": matched["id"] if matched else ("ERR-GENERIC" if kind == "error" else "WARN-GENERIC"),
                "category": matched["category"] if matched else "general",
                "severity": matched["severity"] if matched else kind,
                "file": err_file,
                "line": err_line,
                "message": msg,
                "fix_recipe": matched["fix_recipe"] if matched else "Inspect flagged file and line in .log.",
                "cascading_from": None,
            })
        i += 1

    root_causes = [
        iss for iss in raw_issues
        if iss["kind"] == "error" and iss["rule_id"] in {"ERR-UNCLOSED-BRACE", "ERR-ENV-MISMATCH", "ERR-MISSING-STY-CLS", "ERR-FONT-NOT-FOUND", "ERR-FONT-ENGINE-MISMATCH", "ERR-AMBIGUOUS-TEX-OPT", "ERR-MINTED-SHELL-ESCAPE"}
    ]
    if root_causes:
        first_root = root_causes[0]
        first_idx = raw_issues.index(first_root)
        for idx, iss in enumerate(raw_issues):
            if idx > first_idx and iss["rule_id"] in {"ERR-UNDEFINED-CS", "ERR-EXTRA-RBRACE", "ERR-PERMISSION-IO", "ERR-GENERIC"}:
                iss["cascading_from"] = f"{first_root['rule_id']}@{first_root['file']}:{first_root['line']}"

    errors = [x for x in raw_issues if x["kind"] == "error"]
    return {
        "errors": errors,
        "warnings": [x for x in raw_issues if x["kind"] == "warning"],
        "root_errors": [e for e in errors if not e["cascading_from"]],
        "undefined_references": undefined_references,
        "undefined_citations": undefined_citations,
        "missing_characters": missing_chars,
        "overfull_hboxes": overfull_hboxes,
        "large_overfull_hboxes": [o for o in overfull_hboxes if o["pt"] >= 10.0],
        "overfull_vboxes": overfull_vboxes,
        "loaded_packages": loaded_packages[:120],
        "rerun_requested": any(p in log_text for p in RERUN_PATTERNS),
    }


def parse_fls_inputs(fls_path: Path, build_dir: Optional[Path] = None) -> List[str]:
    if not fls_path.is_file():
        return []
    txt, err = read_text_safe(fls_path)
    if err:
        return []
    local_in, sys_in, seen = [], [], set()
    bdir_str = str(build_dir.resolve()).replace("\\", "/").lower() if build_dir else ""
    for line in txt.splitlines():
        if line.startswith("INPUT "):
            p = line[6:].strip()
            if p not in seen:
                seen.add(p)
                pn = p.replace("\\", "/")
                if (not pn.startswith("/") and not re.match(r"^[A-Za-z]:/", pn)) or (bdir_str and pn.lower().startswith(bdir_str)):
                    local_in.append(p)
                else:
                    sys_in.append(p)
    return (local_in + sys_in)[:500]


def render_pdf_previews(pdf_path: Path, preview_dir: Path) -> Tuple[List[str], Dict[str, Any]]:
    """Render PNG previews, audit text/??/[?]/empty pages, and extract embedded PDF font names."""
    ensure_dir(preview_dir)
    previews: List[str] = []
    pdf_audit: Dict[str, Any] = {
        "pdf_exists": pdf_path.is_file(),
        "page_count": 0,
        "empty_pages": [],
        "image_only_pages": [],
        "unresolved_markers": [],
        "overflow_findings": [],
        "embedded_fonts": [],
        "renderer_used": None,
    }
    if not pdf_path.is_file():
        return previews, pdf_audit

    for old_png in preview_dir.glob("page-*.png"):
        try:
            old_png.unlink()
        except OSError:
            pass

    if which_tool("pdftoppm"):
        res = run_cmd(["pdftoppm", "-png", "-r", "150", str(pdf_path), str(preview_dir / "page")], timeout=45)
        if res["returncode"] == 0:
            previews = [str(p) for p in sorted(preview_dir.glob("page-*.png"))]
            pdf_audit["renderer_used"] = "pdftoppm"

    fonts_seen: Set[str] = set()
    unresolved_re = re.compile(r"(?:\?\?|\[(?:\?|\s*\?\s*)\])")

    try:
        try:
            import pymupdf as fitz  # type: ignore
        except ImportError:
            import fitz  # type: ignore

        doc = fitz.open(str(pdf_path))
        pdf_audit["page_count"] = len(doc)
        for pno, page in enumerate(doc, start=1):
            if not previews:
                out_png = preview_dir / f"page-{pno:02d}.png"
                page.get_pixmap(dpi=150).save(str(out_png))
                previews.append(str(out_png))
                pdf_audit["renderer_used"] = "pymupdf"

            for f_item in page.get_fonts(full=True):
                if len(f_item) >= 4 and f_item[3]:
                    fonts_seen.add(str(f_item[3]))

            text = page.get_text("text")
            if not text.strip():
                has_vis = bool(page.get_images(full=True)) or bool(page.get_drawings())
                (pdf_audit["image_only_pages"] if has_vis else pdf_audit["empty_pages"]).append(pno)
            for marker in unresolved_re.findall(text):
                pdf_audit["unresolved_markers"].append({"page": pno, "marker": marker})

            page_w = page.rect.width
            for blk in page.get_text("blocks"):
                if blk[2] > page_w - 6 and isinstance(blk[4], str) and blk[4].strip():
                    pdf_audit["overflow_findings"].append({"page": pno, "x1": round(blk[2], 1), "page_width": round(page_w, 1), "snippet": blk[4].strip()[:80]})
        doc.close()
    except Exception:
        if which_tool("pdftotext"):
            res = run_cmd(["pdftotext", str(pdf_path), "-"], timeout=20)
            if res["returncode"] == 0:
                pages = res["stdout"].split("\f")
                if pages and not pages[-1].strip():
                    pages = pages[:-1]
                pdf_audit["page_count"] = len(pages)
                for pno, ptxt in enumerate(pages, start=1):
                    if not ptxt.strip():
                        pdf_audit["empty_pages"].append(pno)
                    for marker in unresolved_re.findall(ptxt):
                        pdf_audit["unresolved_markers"].append({"page": pno, "marker": marker})

    if not fonts_seen and which_tool("pdffonts"):
        rf = run_cmd(["pdffonts", str(pdf_path)], timeout=10)
        if rf["returncode"] == 0:
            for line in rf["stdout"].splitlines()[2:]:
                tok = line.split()[:1]
                if tok:
                    fonts_seen.add(tok[0])
    pdf_audit["embedded_fonts"] = sorted(fonts_seen)[:60]
    return previews, pdf_audit


def _run_multipass_engine(
    build_tex: Path,
    engine: str,
    bib_tool: str,
    taxonomy: Dict[str, Any],
    max_passes: int,
    timeout_sec: int,
    bib_arg: str = "auto",
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    build_dir = build_tex.parent
    stem = build_tex.stem
    commands_executed: List[Dict[str, Any]] = []
    eng_flags = ["-no-shell-escape", "-interaction=nonstopmode", "-file-line-error", "-recorder"]
    run_env = {**TEX_LOG_ENV, **(extra_env or {})}

    for stale_ext in (".aux", ".bcf", ".bbl", ".blg", ".toc", ".out", ".pdf"):
        sp = build_dir / f"{stem}{stale_ext}"
        if sp.is_file():
            try:
                sp.unlink()
            except OSError:
                pass
    for child_aux in build_dir.rglob("*.aux"):
        if not any(part.startswith("_fallback") for part in child_aux.parts):
            try:
                child_aux.unlink()
            except OSError:
                pass

    eng_version = ""
    if which_tool(engine):
        ver = run_cmd([engine, "--version"], timeout=10)
        eng_version = ((ver.get("stdout") or ver.get("stderr") or "").splitlines() or [""])[0].strip()

    cmd1 = [engine, *eng_flags, build_tex.name]
    r1 = run_cmd(cmd1, cwd=build_dir, timeout=timeout_sec, env=run_env)
    commands_executed.append({"step": "pass_1", "cmd": " ".join(cmd1), "returncode": r1["returncode"], "timed_out": r1["timed_out"], "not_found": r1["not_found"]})

    if r1["not_found"]:
        return {
            "not_found": True, "engine": engine, "engine_version": eng_version, "run_bib_tool": None,
            "bib_error": False, "commands_executed": commands_executed, "converged": False,
            "diagnosis": {"errors": [{"kind": "error", "rule_id": "ERR-NO-COMPILER", "file": build_tex.name, "line": None, "message": f"TeX compiler '{engine}' is not installed on PATH."}], "warnings": [], "root_errors": [], "undefined_references": [], "undefined_citations": [], "missing_characters": [], "overfull_hboxes": [], "large_overfull_hboxes": [], "overfull_vboxes": [], "loaded_packages": [], "rerun_requested": False},
            "bib_diagnostics": [], "fls_inputs": [], "pdf_path": build_dir / f"{stem}.pdf",
        }

    log_path, pdf_out = build_dir / f"{stem}.log", build_dir / f"{stem}.pdf"
    log1_txt, _ = read_text_safe(log_path)
    diag1 = parse_tex_log(log1_txt, taxonomy, build_tex.name)
    fatal_ids = {"ERR-MISSING-STY-CLS", "ERR-FONT-ENGINE-MISMATCH", "ERR-MINTED-SHELL-ESCAPE", "ERR-AMBIGUOUS-TEX-OPT"}
    if max_passes <= 1 or (r1["returncode"] != 0 and not pdf_out.is_file() and (any(e.get("rule_id") in fatal_ids for e in diag1["errors"]) or "Emergency stop" in log1_txt)):
        return {"not_found": False, "engine": engine, "engine_version": eng_version, "run_bib_tool": None, "bib_error": False, "commands_executed": commands_executed, "converged": False, "diagnosis": diag1, "bib_diagnostics": [], "fls_inputs": parse_fls_inputs(build_dir / f"{stem}.fls", build_dir), "pdf_path": pdf_out}

    bib_diagnostics: List[str] = []
    bib_error = False
    bcf_file, aux_file = build_dir / f"{stem}.bcf", build_dir / f"{stem}.aux"
    aux_txt, _ = read_text_safe(aux_file)
    run_bib_tool = None
    if bib_arg != "none" and bib_tool != "none":
        if bib_tool == "biber" or (bib_arg == "auto" and bcf_file.is_file()):
            run_bib_tool = "biber"
        elif bib_tool == "bibtex" or (bib_arg == "auto" and r"\bibdata{" in aux_txt):
            run_bib_tool = "bibtex"

    def _invoke_bib(label: str) -> None:
        nonlocal bib_error
        if not run_bib_tool:
            return
        if not which_tool(run_bib_tool):
            bib_error = True
            bib_diagnostics.append(f"Bibliography tool '{run_bib_tool}' not installed on PATH.")
            return
        bcmd = [run_bib_tool, stem]
        rb = run_cmd(bcmd, cwd=build_dir, timeout=timeout_sec, env=run_env)
        commands_executed.append({"step": label, "cmd": " ".join(bcmd), "returncode": rb["returncode"], "timed_out": rb["timed_out"], "not_found": rb["not_found"]})
        if rb["returncode"] != 0 or rb["timed_out"]:
            bib_error = True
        blg_txt, _ = read_text_safe(build_dir / f"{stem}.blg")
        for bl in (blg_txt + "\n" + rb["stdout"] + "\n" + rb["stderr"]).splitlines():
            s_bl = bl.strip()
            if any(k in s_bl for k in ("ERROR", "WARN", "Warning--", "I found no", "Repeated entry", "Illegal")) and s_bl not in bib_diagnostics:
                bib_diagnostics.append(s_bl)
            if any(k in s_bl for k in ("ERROR -", "I found no", "Illegal", "syntax error")):
                bib_error = True

    bib_runs = 0
    if run_bib_tool:
        _invoke_bib("bib_pass")
        bib_runs = 1

    prev_aux = hash_aux_state(build_dir, stem)
    converged = False
    max_total = max(1, max_passes)
    for p_idx in range(2, max_total + 1):
        cmd_n = [engine, *eng_flags, build_tex.name]
        rn = run_cmd(cmd_n, cwd=build_dir, timeout=timeout_sec, env=run_env)
        commands_executed.append({"step": f"pass_{p_idx}", "cmd": " ".join(cmd_n), "returncode": rn["returncode"], "timed_out": rn["timed_out"], "not_found": rn["not_found"]})
        new_aux = hash_aux_state(build_dir, stem)
        log_txt, _ = read_text_safe(log_path)
        rerun = any(p in log_txt for p in RERUN_PATTERNS)
        if run_bib_tool and bib_runs < 2 and any(p in log_txt for p in BIB_RERUN_PATTERNS) and p_idx < max_total:
            _invoke_bib(f"bib_pass_{bib_runs + 1}")
            bib_runs += 1
            prev_aux = hash_aux_state(build_dir, stem)
            continue
        if rn["returncode"] == 0 and new_aux == prev_aux and not rerun and (not run_bib_tool or p_idx >= 3):
            converged = True
            break
        if rn["returncode"] != 0 and new_aux == prev_aux and not rerun and p_idx >= 2 and not run_bib_tool:
            break
        prev_aux = new_aux

    log_txt, _ = read_text_safe(log_path)
    return {
        "not_found": False, "engine": engine, "engine_version": eng_version,
        "run_bib_tool": run_bib_tool, "bib_error": bib_error, "commands_executed": commands_executed,
        "converged": converged, "diagnosis": parse_tex_log(log_txt, taxonomy, build_tex.name),
        "bib_diagnostics": bib_diagnostics, "fls_inputs": parse_fls_inputs(build_dir / f"{stem}.fls", build_dir), "pdf_path": pdf_out,
    }


def _sync_project_to_work(src_root: Path, work_dir: Path) -> None:
    src_res, wrk_res = src_root.resolve(), work_dir.resolve()
    if src_res == wrk_res:
        return
    src_in_work = str(src_res).startswith(str(wrk_res) + os.sep)
    work_in_src = str(wrk_res).startswith(str(src_res) + os.sep)
    if not src_in_work:
        for child in list(wrk_res.iterdir()):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass
    for root, dirs, filenames in os.walk(src_res):
        cur_p = Path(root)
        dirs[:] = [d for d in sorted(dirs) if d not in SKIP_DIRS and not d.startswith("_fallback") and not (work_in_src and (cur_p / d).resolve() == wrk_res)]
        rel_root = cur_p.relative_to(src_res)
        for fname in sorted(filenames):
            dfile = wrk_res / rel_root / fname
            ensure_dir(dfile.parent)
            try:
                shutil.copy2(cur_p / fname, dfile)
            except (OSError, shutil.Error):
                pass


def compile_project(
    tex_path: Path,
    work_dir: Path,
    preview_dir: Path,
    skill_dir: Path,
    engine_arg: str = "auto",
    bib_arg: str = "auto",
    max_passes: int = 5,
    timeout_sec: int = 60,
    auto_fallback: bool = True,
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Copy project to writable work_dir, run multi-pass build, diagnose, run non-destructive fallback, and render previews."""
    tex_path = tex_path.resolve()
    work_dir = ensure_dir(work_dir.resolve())
    taxonomy = load_json_safe(skill_dir / "resources" / "error_taxonomy.json", default={}) or {}

    src_root = project_root.resolve() if project_root else tex_path.parent
    try:
        rel_from_root = tex_path.relative_to(src_root)
    except ValueError:
        src_root, rel_from_root = tex_path.parent, Path(tex_path.name)

    try:
        rel_inside = tex_path.relative_to(work_dir)
        build_tex, staged_root = tex_path, work_dir
    except ValueError:
        _sync_project_to_work(src_root, work_dir)
        build_tex, rel_inside, staged_root = (work_dir / rel_from_root).resolve(), rel_from_root, work_dir

    build_dir = build_tex.parent
    search_env = _build_tex_search_env(staged_root, build_dir)
    engine, bib_tool = detect_engine_and_bib(build_tex, engine_arg, bib_arg, project_root=staged_root)
    fallback_notes: List[str] = []
    fallback_preview_used = False

    run_res = _run_multipass_engine(build_tex, engine, bib_tool, taxonomy, max_passes, timeout_sec, bib_arg=bib_arg, extra_env=search_env)
    if run_res["not_found"]:
        return {
            "schema_version": "1.0", "status": "compiler_missing", "engine": engine, "engine_version": run_res["engine_version"],
            "bib_tool": bib_tool, "bib_error": False, "tex_file": str(rel_inside.as_posix()), "pdf_path": None, "previews": [],
            "converged": False, "pass_count": 0, "commands_executed": run_res["commands_executed"], "fallback_notes": fallback_notes,
            "fallback_preview_used": False, "log_diagnosis": run_res["diagnosis"], "bib_diagnostics": [],
            "pdf_audit": {"pdf_exists": False, "page_count": 0, "empty_pages": [], "image_only_pages": [], "unresolved_markers": [], "overflow_findings": [], "embedded_fonts": []},
            "reproducibility_manifest": {"engine": engine, "engine_version": run_res["engine_version"], "bib_tool": bib_tool, "pass_count": 0, "commands": [c["cmd"] for c in run_res["commands_executed"]], "fls_inputs": []},
            "_staged_sources_sha256": hash_project_tree(src_root), "engine_arg": engine_arg, "bib_arg": bib_arg,
        }

    diag = run_res["diagnosis"]
    rule_ids = {e.get("rule_id") for e in diag["errors"]}

    # Auto-fallback 1: pdflatex hit UTF-8/CJK/fontspec error -> retry in isolated _fallback_xelatex
    if auto_fallback and engine == "pdflatex" and engine_arg == "auto" and which_tool("xelatex") and ({"ERR-UTF8-INPUTENC", "ERR-FONT-ENGINE-MISMATCH"} & rule_ids or diag["missing_characters"]):
        xe_dir = work_dir / "_fallback_xelatex"
        shutil.rmtree(xe_dir, ignore_errors=True)
        shutil.copytree(staged_root, xe_dir, ignore=shutil.ignore_patterns("_fallback*", "*.aux", "*.log", "*.pdf", "*.fls"))
        xe_tex = (xe_dir / rel_inside).resolve()
        xe_res = _run_multipass_engine(xe_tex, "xelatex", bib_tool, taxonomy, max_passes, timeout_sec, bib_arg=bib_arg, extra_env=_build_tex_search_env(xe_dir, xe_tex.parent))
        if not xe_res["not_found"] and xe_res["pdf_path"].is_file() and (not run_res["pdf_path"].is_file() or len(xe_res["diagnosis"]["errors"]) < len(diag["errors"]) or (diag["missing_characters"] and not xe_res["diagnosis"]["missing_characters"])):
            for ext_copy in (".pdf", ".log", ".fls", ".aux", ".bbl", ".blg"):
                src_art = xe_tex.parent / f"{xe_tex.stem}{ext_copy}"
                if src_art.is_file():
                    shutil.copy2(src_art, build_dir / src_art.name)
            xe_res["pdf_path"] = build_dir / f"{build_tex.stem}.pdf"
            run_res, engine = xe_res, "xelatex"
            diag = run_res["diagnosis"]
            rule_ids = {e.get("rule_id") for e in diag["errors"]}
            fallback_notes.append("Auto-fallback: switched engine from pdflatex to xelatex due to UTF-8/CJK/fontspec errors.")

    pdf_path = run_res["pdf_path"]
    commands_executed, converged, bib_diagnostics = run_res["commands_executed"], run_res["converged"], run_res["bib_diagnostics"]
    bib_error, fls_inputs, eng_version, run_bib_tool = bool(run_res.get("bib_error")), run_res["fls_inputs"], run_res["engine_version"], run_res["run_bib_tool"]

    # Auto-fallback 2: missing CJK font or optional .sty prevented clean PDF
    if auto_fallback and (not pdf_path.is_file() or "ERR-FONT-NOT-FOUND" in rule_ids or diag["missing_characters"]):
        fb_dir = work_dir / "_fallback_preview"
        shutil.rmtree(fb_dir, ignore_errors=True)
        shutil.copytree(staged_root, fb_dir, ignore=shutil.ignore_patterns("_fallback*", "*.aux", "*.log", "*.pdf", "*.fls"))
        fb_tex = (fb_dir / rel_inside).resolve()
        fb_src, _ = read_text_safe(fb_tex)
        patched = False
        if "ERR-FONT-NOT-FOUND" in rule_ids or diag["missing_characters"]:
            cjk_font = pick_best_cjk_font()
            fandol_ok = fandol_is_available()
            new_src = fb_src
            if cjk_font:
                new_src = re.sub(r"\\setCJK(?:main|sans|mono)font(?:\[[^\]]*\])?\{[^}]+\}", lambda m: f"{m.group(0).split('{')[0]}{{{cjk_font}}}", new_src)
                if re.search(r"\\documentclass(?:\[[^\]]*\])?\{ctex", new_src):
                    font_opt = "fontset=fandol" if (fandol_ok and "fandol" in cjk_font.lower()) else "fontset=none"
                    inj = "" if font_opt == "fontset=fandol" else f"\n\\setCJKmainfont{{{cjk_font}}}\n"
                    new_src = re.sub(
                        r"\\documentclass(?:\[([^\]]*)\])?\{(ctex\w+)\}",
                        lambda m: f"\\documentclass[{','.join((['UTF8'] if 'UTF8' not in [o.strip() for o in (m.group(1) or '').split(',') if o.strip()] else []) + [o.strip() for o in (m.group(1) or '').split(',') if o.strip() and not o.strip().startswith('fontset=')] + [font_opt])}]{{{m.group(2)}}}{inj}",
                        new_src, count=1,
                    )
                elif re.search(r"\\usepackage(?:\[[^\]]*\])?\{ctex\}", new_src):
                    font_opt = "fontset=fandol" if (fandol_ok and "fandol" in cjk_font.lower()) else "fontset=none"
                    inj = "" if font_opt == "fontset=fandol" else f"\n\\setCJKmainfont{{{cjk_font}}}\n"
                    new_src = re.sub(r"\\usepackage(?:\[[^\]]*\])?\{ctex\}", f"\\\\usepackage[UTF8,{font_opt}]{{ctex}}{inj}", new_src, count=1)
                elif has_cjk(new_src) and "\\usepackage{xeCJK}" not in new_src and "\\setCJKmainfont" not in new_src:
                    new_src = new_src.replace("\\begin{document}", f"\\usepackage{{xeCJK}}\n\\setCJKmainfont{{{cjk_font}}}\n\\begin{{document}}", 1)
                if new_src != fb_src:
                    fb_tex.write_text(new_src, encoding="utf-8")
                    patched = True
                    fallback_notes.append(f"Diagnostic CJK font fallback tested with verified '{cjk_font}' (fandol_available={fandol_ok}).")
        if not patched and not pdf_path.is_file() and "ERR-MISSING-STY-CLS" in rule_ids:
            missing_stys = [m.group(1)[:-4] for e in diag["errors"] for m in [re.search(r"File `([^']+\.sty)' not found", e.get("message", ""))] if m]
            if missing_stys:
                new_src = fb_src
                for sty in missing_stys:
                    new_src = re.sub(rf"^\s*\\usepackage(?:\[[^\]]*\])?\{{{re.escape(sty)}\}}.*$", f"% [AUTO-FALLBACK COMMENTED MISSING STY: {sty}]", new_src, flags=re.M)
                if new_src != fb_src:
                    fb_tex.write_text(new_src, encoding="utf-8")
                    patched = True
                    fallback_notes.append(f"Diagnostic missing-.sty fallback preview attempted after commenting optional package(s): {missing_stys}.")
        if patched:
            fb_eng = "xelatex" if which_tool("xelatex") and (has_cjk(fb_tex.read_text(encoding="utf-8", errors="ignore")) or engine == "xelatex") else engine
            fb_res = _run_multipass_engine(fb_tex, fb_eng, bib_tool, taxonomy, min(3, max_passes), timeout_sec, bib_arg=bib_arg, extra_env=_build_tex_search_env(fb_dir, fb_tex.parent))
            fixes_glyphs = bool(diag["missing_characters"]) and not fb_res["diagnosis"]["missing_characters"]
            if fb_res["pdf_path"].is_file() and (not pdf_path.is_file() or fixes_glyphs):
                pdf_path = fb_res["pdf_path"]
                fallback_preview_used = True
                fallback_notes.append(f"Fallback preview PDF generated (fixes_missing_glyphs={fixes_glyphs}); adopt fix in primary .tex before marking status='completed'.")

    previews, pdf_audit = render_pdf_previews(pdf_path, preview_dir)
    last_rc = commands_executed[-1]["returncode"]
    if not converged and last_rc == 0 and not diag["errors"] and not diag["rerun_requested"] and not bib_error:
        converged = True

    if last_rc == 0 and not diag["errors"] and not bib_error and pdf_path.is_file() and not fallback_preview_used and not diag["missing_characters"] and not diag["undefined_references"] and not diag["undefined_citations"] and not pdf_audit["unresolved_markers"]:
        status = "compiled_clean"
    elif pdf_path.is_file():
        status = "compiled_with_issues"
    else:
        status = "compile_failed"

    pass_count = sum(1 for c in commands_executed if c["step"].startswith("pass_"))
    pdf_sha = sha256_file(pdf_path) if pdf_path.is_file() else None
    return {
        "schema_version": "1.0", "status": status, "engine": engine, "engine_version": eng_version,
        "bib_tool": run_bib_tool or "none", "bib_error": bib_error, "tex_file": str(rel_inside.as_posix()),
        "build_dir": str(build_dir), "project_root": str(staged_root),
        "pdf_path": str(pdf_path) if pdf_path.is_file() else None, "previews": previews,
        "converged": converged, "pass_count": pass_count, "commands_executed": commands_executed,
        "fallback_notes": fallback_notes, "fallback_preview_used": fallback_preview_used,
        "log_diagnosis": diag, "bib_diagnostics": bib_diagnostics[:60], "pdf_audit": pdf_audit,
        "reproducibility_manifest": {
            "engine": engine, "engine_version": eng_version, "bib_tool": run_bib_tool or "none",
            "pass_count": pass_count, "commands": [c["cmd"] for c in commands_executed], "converged": converged,
            "pdf_sha256": pdf_sha, "embedded_fonts": pdf_audit.get("embedded_fonts", []),
            "loaded_packages": diag["loaded_packages"], "fls_inputs": fls_inputs,
        },
        "_staged_sources_sha256": hash_project_tree(src_root), "engine_arg": engine_arg, "bib_arg": bib_arg,
    }


def render_compile_summary(report: Dict[str, Any]) -> str:
    diag, pa = report["log_diagnosis"], report["pdf_audit"]
    lines = [
        f"# Compile & Diagnosis Summary (`{report['tex_file']}`)", "",
        f"- **Status**: `{report['status']}` | **Engine**: `{report['engine']}` | **Bib**: `{report['bib_tool']}` | **Passes**: {report['pass_count']} | **Converged**: {report['converged']}",
        f"- **PDF**: `{report['pdf_path'] or 'none'}` ({pa.get('page_count', 0)} pages) | **PNG Previews**: {len(report['previews'])} | **Embedded Fonts**: {len(pa.get('embedded_fonts', []))}",
        f"- **Errors**: {len(diag['errors'])} total ({len(diag['root_errors'])} root causes) | **Warnings**: {len(diag['warnings'])} | **Undefined refs/cites**: {len(diag.get('undefined_references', []))}/{len(diag.get('undefined_citations', []))} | **Missing glyphs**: {len(diag['missing_characters'])} | **Overfull hbox**: {len(diag['overfull_hboxes'])} ({len(diag['large_overfull_hboxes'])} >=10pt)",
        "",
    ]
    if report.get("fallback_notes"):
        lines.extend(["## Fallback & Recovery Notes", *[f"- {fn}" for fn in report["fallback_notes"]], ""])
    if diag.get("undefined_references") or diag.get("undefined_citations"):
        lines.append("## Undefined References & Citations")
        lines.extend(f"- Undefined reference `{ur['key']}` (page {ur['page']}, {ur['file']}:{ur['line']})" for ur in diag.get("undefined_references", [])[:20])
        lines.extend(f"- Undefined citation `{uc['key']}` (page {uc['page']}, {uc['file']}:{uc['line']})" for uc in diag.get("undefined_citations", [])[:20])
        lines.append("")
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
        lines.extend(["## Bibliography Diagnostics", *[f"- {bd}" for bd in report["bib_diagnostics"][:20]], ""])
    if diag["missing_characters"] or pa.get("unresolved_markers") or pa.get("empty_pages"):
        lines.append("## PDF & Glyph Quality Alerts")
        lines.extend(f"- Missing glyph: `{mc}`" for mc in diag["missing_characters"][:15])
        lines.extend(f"- Unresolved marker on page {um['page']}: `{um['marker']}`" for um in pa.get("unresolved_markers", []))
        if pa.get("empty_pages"):
            lines.append(f"- Empty pages detected: {pa['empty_pages']}")
        lines.append("")
    lines.extend(["## Reproducible Command Sequence", *[f"- `{cmd}`" for cmd in report["reproducibility_manifest"]["commands"]]])
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Multi-pass LaTeX compile, error clustering, fallback diagnostics, PDF audit, and PNG previews.")
    ap.add_argument("--tex", required=True, help="Path to main .tex entry file.")
    ap.add_argument("--project-root", default=None, help="Root directory of project tree (preserves subfiles/figures/styles).")
    ap.add_argument("--work-dir", default="/app/work/build")
    ap.add_argument("--preview-dir", default="/app/work/previews")
    ap.add_argument("--skill-dir", default=None)
    ap.add_argument("--engine", default="auto", choices=["auto", "pdflatex", "xelatex", "lualatex"])
    ap.add_argument("--bib", default="auto", choices=["auto", "bibtex", "biber", "none"])
    ap.add_argument("--max-passes", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--no-fallback", action="store_true")
    ap.add_argument("--out-report", default="/app/work/compile_report.json")
    ap.add_argument("--out-summary", default="/app/work/compile_summary.md")
    args = ap.parse_args()

    rep = compile_project(
        tex_path=Path(args.tex), work_dir=Path(args.work_dir), preview_dir=Path(args.preview_dir),
        skill_dir=resolve_skill_dir(args.skill_dir), engine_arg=args.engine, bib_arg=args.bib,
        max_passes=args.max_passes, timeout_sec=args.timeout, auto_fallback=not args.no_fallback,
        project_root=Path(args.project_root) if args.project_root else None,
    )
    save_json(Path(args.out_report), rep)
    sp = Path(args.out_summary)
    ensure_dir(sp.parent)
    sp.write_text(render_compile_summary(rep), encoding="utf-8")
    print(f"[compile_and_diagnose] status={rep['status']} engine={rep['engine']} passes={rep['pass_count']} errors={len(rep['log_diagnosis']['errors'])} previews={len(rep['previews'])} -> {args.out_report}")


if __name__ == "__main__":
    main()
