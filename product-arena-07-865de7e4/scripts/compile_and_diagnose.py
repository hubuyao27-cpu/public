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
    ensure_dir,
    extract_tex_commands,
    has_cjk,
    load_json_safe,
    read_text_safe,
    resolve_skill_dir,
    run_cmd,
    sanitize_tex_for_scan,
    save_json,
    sha256_file,
    strip_tex_comments_preserve_lines,
    which_tool,
)

# TeX Live / MiKTeX env vars preventing 79-column .log wrapping (LaTeX-Workshop / Tectonic best practice)
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

BIB_RERUN_PATTERNS = (
    "Please (re)run Biber",
    "Please (re)run BibTeX",
)


def _build_tex_search_env(project_root: Path, build_dir: Path) -> Dict[str, str]:
    """Export TEXINPUTS/BIBINPUTS/BSTINPUTS covering project_root subdirectories + log formatting env."""
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
            if not p.is_file():
                continue
            if any(part.startswith("_fallback") or part in {".git", "__pycache__"} for part in p.parts):
                continue
            ext = p.suffix.lower()
            if ext in {".cls", ".sty", ".def", ".clo", ".tex", ".ltx", ".bbx", ".cbx", ".lbx", ".cfg", ".fd"}:
                _add(tex_dirs, p.parent)
            if ext == ".bib":
                _add(bib_dirs, p.parent)
            if ext == ".bst":
                _add(bst_dirs, p.parent)

    sep = os.pathsep
    if len(tex_dirs) > 1 or project_root.resolve() != build_dir.resolve():
        env["TEXINPUTS"] = sep.join(["."] + tex_dirs) + sep
        env["BIBINPUTS"] = sep.join(["."] + bib_dirs) + sep
        env["BSTINPUTS"] = sep.join(["."] + bst_dirs) + sep
    return env


def _collect_reachable_tex(tex_path: Path, project_root: Path, max_depth: int = 4) -> str:
    """Walk \\input/\\include/\\subfile graph up to max_depth (cycle-safe) and return combined sanitized text."""
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
            tgt = inc["arg"]
            if not tgt:
                continue
            cands = [tgt] if Path(tgt).suffix else [tgt, tgt + ".tex"]
            for base in (res_cur.parent, project_root):
                found = None
                for cn in cands:
                    p = (base / cn).resolve()
                    if p.is_file():
                        found = p
                        break
                if found:
                    _walk(found, depth + 1)
                    break

    _walk(tex_path, 0)
    return "\n".join(chunks)


def detect_engine_and_bib(tex_path: Path, engine_arg: str, bib_arg: str, project_root: Optional[Path] = None) -> Tuple[str, str]:
    """Choose TeX engine and bibliography backend across entry + \\input/\\include graph if 'auto'."""
    root = project_root or tex_path.parent
    clean = _collect_reachable_tex(tex_path, root)

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
    """Recursively hash root + child \\include .aux files and control files (Tectonic convergence pattern)."""
    state: Dict[str, str] = {}
    for ext in (".aux", ".toc", ".lof", ".lot", ".out", ".bbl", ".bcf", ".nav", ".snm"):
        p = build_dir / f"{stem}{ext}"
        if p.is_file():
            state[ext] = sha256_file(p)
    for aux_p in sorted(build_dir.rglob("*.aux")):
        if any(part.startswith("_fallback") for part in aux_p.parts):
            continue
        rel = aux_p.relative_to(build_dir).as_posix()
        state[f"aux:{rel}"] = sha256_file(aux_p)
    return state


def parse_tex_log(log_text: str, taxonomy: Dict[str, Any], default_file: str) -> Dict[str, Any]:
    """Parse TeX .log with balanced paren file-stack tracking, file:line errors, warnings, and cascade clustering."""
    rules = taxonomy.get("rules", [])
    compiled_rules = [(r, re.compile(r["pattern"])) for r in rules]

    lines = log_text.splitlines()
    # Balanced paren stack: push tracked file path or None for untracked parens so single-line opens & .fd/.cfg never corrupt stack
    paren_stack: List[Optional[str]] = [default_file]

    def _current_file() -> str:
        for item in reversed(paren_stack):
            if item:
                return item
        return default_file

    raw_issues: List[Dict[str, Any]] = []
    missing_chars: List[str] = []
    overfull_hboxes: List[Dict[str, Any]] = []
    overfull_vboxes: List[Dict[str, Any]] = []
    undefined_references: List[Dict[str, Any]] = []
    undefined_citations: List[Dict[str, Any]] = []
    loaded_packages: List[str] = []

    i = 0
    in_biblatex_missing_block = False
    while i < len(lines):
        line = lines[i]

        # Update balanced paren file stack cleanly across line
        pos_p = 0
        llen = len(line)
        while pos_p < llen:
            ch = line[pos_p]
            if ch == "(":
                m_f = re.match(r"\"?([^\s()\"']+\.(?:tex|ltx|sty|cls|def|bbl|aux))\"?", line[pos_p + 1 :])
                if m_f:
                    paren_stack.append(m_f.group(1))
                    pos_p += 1 + m_f.end()
                    continue
                paren_stack.append(None)
            elif ch == ")":
                if len(paren_stack) > 1:
                    paren_stack.pop()
            pos_p += 1

        cur_file = _current_file()

        if line.startswith("Package: ") or line.startswith("Document Class: "):
            loaded_packages.append(line.strip())

        if "Missing character: There is no " in line:
            missing_chars.append(line.strip())

        om = re.search(r"Overfull \\hbox \(([0-9.]+)pt too wide\)", line)
        if om:
            overfull_hboxes.append(
                {
                    "pt": float(om.group(1)),
                    "file": cur_file,
                    "line": line.strip(),
                    "raw_line": line.strip(),
                }
            )

        vm = re.search(r"Overfull \\vbox \(([0-9.]+)pt too high\)", line)
        if vm:
            overfull_vboxes.append(
                {
                    "pt": float(vm.group(1)),
                    "file": cur_file,
                    "line": line.strip(),
                    "raw_line": line.strip(),
                }
            )

        ur = re.search(r"Reference [`']([^']+)' on page (\S+) undefined(?: on input line (\d+))?", line)
        if ur:
            undefined_references.append(
                {
                    "key": ur.group(1),
                    "page": ur.group(2),
                    "line": int(ur.group(3)) if ur.group(3) else None,
                    "file": cur_file,
                }
            )

        # Match BOTH LaTeX/natbib (`key') and biblatex ('key') undefined citation warnings
        uc = re.search(r"Citation [`']([^']+)' on page (\S+) undefined(?: on input line (\d+))?", line)
        if uc:
            undefined_citations.append(
                {
                    "key": uc.group(1),
                    "page": uc.group(2),
                    "line": int(uc.group(3)) if uc.group(3) else None,
                    "file": cur_file,
                }
            )

        # Multi-line biblatex missing entry block:
        # Package biblatex Warning: The following entry could not be found
        # (biblatex)                in the database:
        # (biblatex)                missing_key
        if "The following entry could not be found" in line:
            in_biblatex_missing_block = True
        elif in_biblatex_missing_block:
            m_bl_cont = re.match(r"^\(biblatex\)\s+(.+)$", line)
            if m_bl_cont:
                tok_bl = m_bl_cont.group(1).strip()
                if tok_bl and not tok_bl.startswith(("in the database", "Please verify", "and rerun")):
                    undefined_citations.append({"key": tok_bl.lstrip("- "), "page": "?", "line": None, "file": cur_file})
            else:
                in_biblatex_missing_block = False

        if "Empty bibliography on input line" in line or "Package biblatex Warning: Empty bibliography" in line:
            lm_eb = re.search(r"on input line (\d+)", line)
            undefined_citations.append(
                {
                    "key": "<empty_bibliography>",
                    "page": "?",
                    "line": int(lm_eb.group(1)) if lm_eb else None,
                    "file": cur_file,
                }
            )

        # File-line-error style: ./foo.tex:42: Error message
        fl_m = re.match(r"^(.+\.(?:tex|ltx|sty|cls|bbl)):(\d+):\s*(.+)$", line)
        bang_m = line.startswith("! ")
        warn_m = re.match(r"^(LaTeX(?:\s+Font)?|Package\s+\S+|Class\s+\S+|pdfTeX)\s+[Ww]arning:\s*(.+)$", line)

        snippet = ""
        err_file = cur_file
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
            while j < len(lines) and j < i + 5 and (lines[j].startswith("               ") or lines[j].startswith("(")):
                if lines[j].startswith("(") and not re.match(r"^\([A-Za-z0-9_-]+\)\s+", lines[j]):
                    break
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
        and iss["rule_id"] in {
            "ERR-UNCLOSED-BRACE",
            "ERR-ENV-MISMATCH",
            "ERR-MISSING-STY-CLS",
            "ERR-FONT-NOT-FOUND",
            "ERR-FONT-ENGINE-MISMATCH",
            "ERR-AMBIGUOUS-TEX-OPT",
            "ERR-MINTED-SHELL-ESCAPE",
        }
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
        "undefined_references": undefined_references,
        "undefined_citations": undefined_citations,
        "missing_characters": missing_chars,
        "overfull_hboxes": overfull_hboxes,
        "large_overfull_hboxes": [o for o in overfull_hboxes if o["pt"] >= 10.0],
        "overfull_vboxes": overfull_vboxes,
        "loaded_packages": loaded_packages[:120],
        "rerun_requested": rerun_requested,
    }


def parse_fls_inputs(fls_path: Path, build_dir: Optional[Path] = None) -> List[str]:
    """Extract unique INPUT files from TeX -recorder .fls file, prioritizing project-local files first."""
    if not fls_path.is_file():
        return []
    txt, err = read_text_safe(fls_path)
    if err:
        return []
    local_inputs: List[str] = []
    system_inputs: List[str] = []
    seen: Set[str] = set()
    bdir_str = str(build_dir.resolve()).replace("\\", "/").lower() if build_dir else ""
    for line in txt.splitlines():
        if line.startswith("INPUT "):
            p = line[6:].strip()
            if p not in seen:
                seen.add(p)
                p_norm = p.replace("\\", "/")
                is_local = (
                    not p_norm.startswith("/")
                    and not re.match(r"^[A-Za-z]:/", p_norm)
                    or (bdir_str and p_norm.lower().startswith(bdir_str))
                )
                if is_local:
                    local_inputs.append(p)
                else:
                    system_inputs.append(p)
    return (local_inputs + system_inputs)[:500]


def render_pdf_previews(pdf_path: Path, preview_dir: Path) -> Tuple[List[str], Dict[str, Any]]:
    """Render PDF pages to PNG previews and audit rendered PDF text for ?? / [?] / blank pages."""
    ensure_dir(preview_dir)
    previews: List[str] = []
    pdf_audit: Dict[str, Any] = {
        "pdf_exists": pdf_path.is_file(),
        "page_count": 0,
        "empty_pages": [],
        "image_only_pages": [],
        "unresolved_markers": [],
        "overflow_findings": [],
        "renderer_used": None,
    }
    if not pdf_path.is_file():
        return previews, pdf_audit

    # Remove stale preview PNGs from prior compilations so page counts match strictly
    for old_png in preview_dir.glob("page-*.png"):
        try:
            old_png.unlink()
        except OSError:
            pass

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
                has_visual = bool(page.get_images(full=True)) or bool(page.get_drawings())
                if has_visual:
                    pdf_audit["image_only_pages"].append(pno)
                else:
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


def _pick_installed_cjk_font() -> Optional[str]:
    """Query fc-list for an installed Linux/Windows CJK serif font."""
    if not which_tool("fc-list"):
        return None
    res = run_cmd(["fc-list", ":lang=zh", "family"], timeout=10)
    if res["returncode"] != 0:
        return None
    installed: List[str] = []
    seen = set()
    for raw_line in res["stdout"].splitlines():
        for fam in raw_line.split(","):
            f = fam.strip()
            if f and f not in seen:
                seen.add(f)
                installed.append(f)
    for pref in ("Noto Serif CJK SC", "FandolSong", "AR PL UMing CN", "Source Han Serif SC", "Noto Sans CJK SC", "FandolHei", "WenQuanYi Micro Hei", "SimSun", "Microsoft YaHei"):
        if pref in seen:
            return pref
    return installed[0] if installed else None


def _fandol_is_available() -> bool:
    if which_tool("kpsewhich"):
        res = run_cmd(["kpsewhich", "FandolSong-Regular.otf"], timeout=10)
        if res["returncode"] == 0 and (res.get("stdout") or "").strip():
            return True
    if which_tool("fc-list"):
        res = run_cmd(["fc-list", ":lang=zh", "family"], timeout=10)
        if res["returncode"] == 0 and "fandol" in res.get("stdout", "").lower():
            return True
    return False


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
    """Execute deterministic engine -> bib -> engine* convergence loop inside build_tex.parent."""
    build_dir = build_tex.parent
    stem = build_tex.stem
    commands_executed: List[Dict[str, Any]] = []
    eng_flags = ["-no-shell-escape", "-interaction=nonstopmode", "-file-line-error", "-recorder"]
    run_env = dict(TEX_LOG_ENV)
    if extra_env:
        run_env.update(extra_env)

    # Clean root and child .aux files cleanly (excluding _fallback* scratch dirs)
    for stale_ext in (".aux", ".bcf", ".bbl", ".blg", ".toc", ".out", ".pdf"):
        sp = build_dir / f"{stem}{stale_ext}"
        if sp.is_file():
            try:
                sp.unlink()
            except OSError:
                pass
    for child_aux in build_dir.rglob("*.aux"):
        if any(part.startswith("_fallback") for part in child_aux.parts):
            continue
        try:
            child_aux.unlink()
        except OSError:
            pass

    eng_version = ""
    if which_tool(engine):
        ver_res = run_cmd([engine, "--version"], timeout=10)
        eng_version = ((ver_res.get("stdout") or ver_res.get("stderr") or "").splitlines() or [""])[0].strip()

    cmd1 = [engine, *eng_flags, build_tex.name]
    r1 = run_cmd(cmd1, cwd=build_dir, timeout=timeout_sec, env=run_env)
    commands_executed.append({"step": "pass_1", "cmd": " ".join(cmd1), "returncode": r1["returncode"], "timed_out": r1["timed_out"], "not_found": r1["not_found"]})

    if r1["not_found"]:
        return {
            "not_found": True,
            "engine": engine,
            "engine_version": eng_version,
            "run_bib_tool": None,
            "bib_error": False,
            "commands_executed": commands_executed,
            "converged": False,
            "diagnosis": {
                "errors": [{"kind": "error", "rule_id": "ERR-NO-COMPILER", "file": build_tex.name, "line": None, "message": f"TeX compiler '{engine}' is not installed on PATH."}],
                "warnings": [],
                "root_errors": [],
                "undefined_references": [],
                "undefined_citations": [],
                "missing_characters": [],
                "overfull_hboxes": [],
                "large_overfull_hboxes": [],
                "overfull_vboxes": [],
                "loaded_packages": [],
                "rerun_requested": False,
            },
            "bib_diagnostics": [],
            "fls_inputs": [],
            "pdf_path": build_dir / f"{stem}.pdf",
        }

    log_path = build_dir / f"{stem}.log"
    pdf_out = build_dir / f"{stem}.pdf"
    log1_txt, _ = read_text_safe(log_path)
    diag1 = parse_tex_log(log1_txt, taxonomy, build_tex.name)
    fatal_rule_ids = {"ERR-MISSING-STY-CLS", "ERR-FONT-ENGINE-MISMATCH", "ERR-MINTED-SHELL-ESCAPE", "ERR-AMBIGUOUS-TEX-OPT"}
    pass1_fatal_no_pdf = (
        r1["returncode"] != 0
        and not pdf_out.is_file()
        and (any(e.get("rule_id") in fatal_rule_ids for e in diag1["errors"]) or "Emergency stop" in log1_txt)
    )
    if pass1_fatal_no_pdf or max_passes <= 1:
        fls_inputs = parse_fls_inputs(build_dir / f"{stem}.fls", build_dir=build_dir)
        return {
            "not_found": False,
            "engine": engine,
            "engine_version": eng_version,
            "run_bib_tool": None,
            "bib_error": False,
            "commands_executed": commands_executed,
            "converged": False,
            "diagnosis": diag1,
            "bib_diagnostics": [],
            "fls_inputs": fls_inputs,
            "pdf_path": pdf_out,
        }

    bib_diagnostics: List[str] = []
    bib_error = False
    bcf_file = build_dir / f"{stem}.bcf"
    aux_file = build_dir / f"{stem}.aux"
    aux_txt, _ = read_text_safe(aux_file)
    run_bib_tool = None
    # Strictly honor --bib none
    if bib_arg != "none" and bib_tool != "none":
        if bib_tool == "biber" or (bib_arg == "auto" and bcf_file.is_file()):
            run_bib_tool = "biber"
        elif bib_tool == "bibtex" or (bib_arg == "auto" and r"\bibdata{" in aux_txt):
            run_bib_tool = "bibtex"

    def _invoke_bib(step_label: str) -> None:
        nonlocal bib_error
        if not run_bib_tool:
            return
        if which_tool(run_bib_tool):
            bcmd = [run_bib_tool, stem]
            rb = run_cmd(bcmd, cwd=build_dir, timeout=timeout_sec, env=run_env)
            commands_executed.append({"step": step_label, "cmd": " ".join(bcmd), "returncode": rb["returncode"], "timed_out": rb["timed_out"], "not_found": rb["not_found"]})
            if rb["returncode"] != 0 or rb["timed_out"]:
                bib_error = True
            blg_txt, _ = read_text_safe(build_dir / f"{stem}.blg")
            for bl in (blg_txt + "\n" + rb["stdout"] + "\n" + rb["stderr"]).splitlines():
                s_bl = bl.strip()
                if any(k in s_bl for k in ("ERROR", "WARN", "Warning--", "I found no", "Repeated entry", "Illegal")):
                    if s_bl not in bib_diagnostics:
                        bib_diagnostics.append(s_bl)
                if any(k in s_bl for k in ("ERROR -", "I found no", "Illegal", "syntax error")):
                    bib_error = True
        else:
            bib_error = True
            bib_diagnostics.append(f"Bibliography tool '{run_bib_tool}' not installed on PATH.")

    bib_runs = 0
    if run_bib_tool:
        _invoke_bib("bib_pass")
        bib_runs = 1

    prev_aux_hash = hash_aux_state(build_dir, stem)
    converged = False
    # Strictly honor max_passes (pass_1 already executed; remaining passes = max_passes - 1)
    max_total_passes = max(1, max_passes)

    for p_idx in range(2, max_total_passes + 1):
        cmd_n = [engine, *eng_flags, build_tex.name]
        rn = run_cmd(cmd_n, cwd=build_dir, timeout=timeout_sec, env=run_env)
        commands_executed.append({"step": f"pass_{p_idx}", "cmd": " ".join(cmd_n), "returncode": rn["returncode"], "timed_out": rn["timed_out"], "not_found": rn["not_found"]})
        new_aux_hash = hash_aux_state(build_dir, stem)
        log_txt, _ = read_text_safe(log_path)
        rerun_needed = any(p in log_txt for p in RERUN_PATTERNS)
        # Tectonic-style: if Biber/BibTeX rerun requested inside loop, re-run bib tool (capped at 2)
        if run_bib_tool and bib_runs < 2 and any(p in log_txt for p in BIB_RERUN_PATTERNS) and p_idx < max_total_passes:
            _invoke_bib(f"bib_pass_{bib_runs + 1}")
            bib_runs += 1
            prev_aux_hash = hash_aux_state(build_dir, stem)
            continue
        if rn["returncode"] == 0 and new_aux_hash == prev_aux_hash and not rerun_needed and (not run_bib_tool or p_idx >= 3):
            converged = True
            break
        if rn["returncode"] != 0 and new_aux_hash == prev_aux_hash and not rerun_needed and p_idx >= 2 and not run_bib_tool:
            break
        prev_aux_hash = new_aux_hash

    log_txt, _ = read_text_safe(log_path)
    diagnosis = parse_tex_log(log_txt, taxonomy, build_tex.name)
    fls_inputs = parse_fls_inputs(build_dir / f"{stem}.fls", build_dir=build_dir)
    return {
        "not_found": False,
        "engine": engine,
        "engine_version": eng_version,
        "run_bib_tool": run_bib_tool,
        "bib_error": bib_error,
        "commands_executed": commands_executed,
        "converged": converged,
        "diagnosis": diagnosis,
        "bib_diagnostics": bib_diagnostics,
        "fls_inputs": fls_inputs,
        "pdf_path": pdf_out,
    }


def _sync_project_to_work(src_root: Path, work_dir: Path) -> None:
    """Mirror src_root into work_dir cleanly (removing stale files from prior iterations and avoiding self-copy)."""
    src_res = src_root.resolve()
    wrk_res = work_dir.resolve()
    if src_res == wrk_res:
        return
    # Clean stale entries in work_dir (except if src_root is inside work_dir)
    src_inside_work = False
    try:
        src_res.relative_to(wrk_res)
        src_inside_work = True
    except ValueError:
        pass
    work_inside_src = False
    try:
        wrk_res.relative_to(src_res)
        work_inside_src = True
    except ValueError:
        pass

    if not src_inside_work:
        for child in list(wrk_res.iterdir()):
            if child.name.startswith("_fallback"):
                shutil.rmtree(child, ignore_errors=True)
            elif child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass

    for root, dirs, filenames in os.walk(src_res):
        cur_p = Path(root)
        dirs[:] = [
            d for d in sorted(dirs)
            if d not in {".git", "__pycache__"}
            and not d.startswith("_fallback")
            and not (work_inside_src and (cur_p / d).resolve() == wrk_res)
        ]
        rel_root = cur_p.relative_to(src_res)
        for fname in sorted(filenames):
            sfile = cur_p / fname
            dfile = wrk_res / rel_root / fname
            ensure_dir(dfile.parent)
            try:
                shutil.copy2(sfile, dfile)
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
    """Copy full project tree to writable work_dir if external, run multi-pass build, diagnose & preview."""
    tex_path = tex_path.resolve()
    work_dir = ensure_dir(work_dir.resolve())
    taxonomy = load_json_safe(skill_dir / "resources" / "error_taxonomy.json", default={}) or {}

    # Determine project root & stage full project tree preserving subdirectory hierarchy
    src_root = project_root.resolve() if project_root else tex_path.parent
    try:
        rel_from_root = tex_path.relative_to(src_root)
    except ValueError:
        src_root = tex_path.parent
        rel_from_root = Path(tex_path.name)

    try:
        rel_inside = tex_path.relative_to(work_dir)
        build_tex = tex_path
        staged_root = work_dir
    except ValueError:
        _sync_project_to_work(src_root, work_dir)
        build_tex = (work_dir / rel_from_root).resolve()
        rel_inside = rel_from_root
        staged_root = work_dir

    build_dir = build_tex.parent
    search_env = _build_tex_search_env(staged_root, build_dir)
    engine, bib_tool = detect_engine_and_bib(build_tex, engine_arg, bib_arg, project_root=staged_root)
    fallback_notes: List[str] = []
    fallback_preview_used = False

    run_res = _run_multipass_engine(build_tex, engine, bib_tool, taxonomy, max_passes, timeout_sec, bib_arg=bib_arg, extra_env=search_env)
    if run_res["not_found"]:
        return {
            "schema_version": "1.0",
            "status": "compiler_missing",
            "engine": engine,
            "engine_version": run_res["engine_version"],
            "bib_tool": bib_tool,
            "bib_error": False,
            "tex_file": str(rel_inside.as_posix()),
            "pdf_path": None,
            "previews": [],
            "converged": False,
            "pass_count": 0,
            "commands_executed": run_res["commands_executed"],
            "fallback_notes": fallback_notes,
            "fallback_preview_used": False,
            "log_diagnosis": run_res["diagnosis"],
            "bib_diagnostics": [],
            "pdf_audit": {"pdf_exists": False, "page_count": 0, "empty_pages": [], "image_only_pages": [], "unresolved_markers": [], "overflow_findings": []},
            "reproducibility_manifest": {"engine": engine, "engine_version": run_res["engine_version"], "bib_tool": bib_tool, "pass_count": 0, "commands": [c["cmd"] for c in run_res["commands_executed"]], "fls_inputs": []},
        }

    # Auto-fallback 1: pdflatex hit UTF-8/CJK/fontspec error -> retry non-destructively in isolated _fallback_xelatex dir
    diag = run_res["diagnosis"]
    rule_ids = {e.get("rule_id") for e in diag["errors"]}
    if (
        auto_fallback
        and engine == "pdflatex"
        and engine_arg == "auto"
        and which_tool("xelatex")
        and ("ERR-UTF8-INPUTENC" in rule_ids or "ERR-FONT-ENGINE-MISMATCH" in rule_ids or bool(diag["missing_characters"]))
    ):
        xe_dir = work_dir / "_fallback_xelatex"
        if xe_dir.exists():
            shutil.rmtree(xe_dir, ignore_errors=True)
        shutil.copytree(staged_root, xe_dir, ignore=shutil.ignore_patterns("_fallback*", "*.aux", "*.log", "*.pdf", "*.fls"))
        xe_tex = (xe_dir / rel_inside).resolve()
        xe_env = _build_tex_search_env(xe_dir, xe_tex.parent)
        xe_res = _run_multipass_engine(xe_tex, "xelatex", bib_tool, taxonomy, max_passes, timeout_sec, bib_arg=bib_arg, extra_env=xe_env)
        improved = not xe_res["not_found"] and (
            (xe_res["pdf_path"].is_file() and not run_res["pdf_path"].is_file())
            or (xe_res["pdf_path"].is_file() and len(xe_res["diagnosis"]["errors"]) < len(diag["errors"]))
            or (xe_res["pdf_path"].is_file() and not xe_res["diagnosis"]["missing_characters"] and bool(diag["missing_characters"]))
        )
        if improved:
            # Promote xelatex PDF/aux/log back to build_dir safely
            for ext_copy in (".pdf", ".log", ".fls", ".aux", ".bbl", ".blg"):
                src_art = xe_tex.parent / f"{xe_tex.stem}{ext_copy}"
                if src_art.is_file():
                    shutil.copy2(src_art, build_dir / src_art.name)
            xe_res["pdf_path"] = build_dir / f"{build_tex.stem}.pdf"
            run_res = xe_res
            engine = "xelatex"
            diag = run_res["diagnosis"]
            rule_ids = {e.get("rule_id") for e in diag["errors"]}
            fallback_notes.append("Auto-fallback: switched engine from pdflatex to xelatex in isolated scratch dir due to UTF-8/CJK/fontspec errors.")

    pdf_path = run_res["pdf_path"]
    commands_executed = run_res["commands_executed"]
    converged = run_res["converged"]
    bib_diagnostics = run_res["bib_diagnostics"]
    bib_error = bool(run_res.get("bib_error"))
    fls_inputs = run_res["fls_inputs"]
    eng_version = run_res["engine_version"]
    run_bib_tool = run_res["run_bib_tool"]

    # Auto-fallback 2: missing CJK font or non-mandatory .sty prevented clean PDF -> build non-destructive preview copy
    if auto_fallback and (not pdf_path.is_file() or "ERR-FONT-NOT-FOUND" in rule_ids or bool(diag["missing_characters"])):
        fb_dir = work_dir / "_fallback_preview"
        if fb_dir.exists():
            shutil.rmtree(fb_dir, ignore_errors=True)
        shutil.copytree(staged_root, fb_dir, ignore=shutil.ignore_patterns("_fallback*", "*.aux", "*.log", "*.pdf", "*.fls"))
        fb_tex = (fb_dir / rel_inside).resolve()
        fb_src, _ = read_text_safe(fb_tex)
        patched = False
        if "ERR-FONT-NOT-FOUND" in rule_ids or bool(diag["missing_characters"]):
            cjk_font = _pick_installed_cjk_font()
            fandol_ok = _fandol_is_available()
            new_src = fb_src
            if cjk_font:
                # Patch explicit CJK font commands
                new_src = re.sub(r"\\setCJK(?:main|sans|mono)font(?:\[[^\]]*\])?\{[^}]+\}", lambda m: f"{m.group(0).split('{')[0]}{{{cjk_font}}}", new_src)
                # Patch \documentclass[...]{ctex*} preserving existing options
                if re.search(r"\\documentclass(?:\[[^\]]*\])?\{ctex", new_src):
                    if fandol_ok and "fandol" in cjk_font.lower():
                        font_opt = "fontset=fandol"
                        inject_cmd = ""
                    else:
                        font_opt = "fontset=none"
                        inject_cmd = f"\n\\setCJKmainfont{{{cjk_font}}}\n"

                    def _patch_ctex_dc(m: re.Match) -> str:
                        opts_raw = m.group(1) or ""
                        opts = [o.strip() for o in opts_raw.split(",") if o.strip() and not o.strip().startswith("fontset=")]
                        if "UTF8" not in opts:
                            opts.insert(0, "UTF8")
                        opts.append(font_opt)
                        return f"\\documentclass[{','.join(opts)}]{{{m.group(2)}}}{inject_cmd}"

                    new_src = re.sub(r"\\documentclass(?:\[([^\]]*)\])?\{(ctex\w+)\}", _patch_ctex_dc, new_src, count=1)
                elif re.search(r"\\usepackage(?:\[[^\]]*\])?\{ctex\}", new_src):
                    font_opt = "fontset=fandol" if (fandol_ok and "fandol" in cjk_font.lower()) else "fontset=none"
                    inject_cmd = "" if font_opt == "fontset=fandol" else f"\n\\setCJKmainfont{{{cjk_font}}}\n"
                    new_src = re.sub(r"\\usepackage(?:\[([^\]]*)\])?\{ctex\}", f"\\\\usepackage[UTF8,{font_opt}]{{ctex}}{inject_cmd}", new_src, count=1)
                elif has_cjk(new_src) and "\\usepackage{xeCJK}" not in new_src and "\\setCJKmainfont" not in new_src:
                    # Bare CJK inside standard article/IEEEtran -> inject xeCJK + verified system font
                    new_src = new_src.replace("\\begin{document}", f"\\usepackage{{xeCJK}}\n\\setCJKmainfont{{{cjk_font}}}\n\\begin{{document}}", 1)

                if new_src != fb_src:
                    fb_tex.write_text(new_src, encoding="utf-8")
                    patched = True
                    fallback_notes.append(f"Diagnostic CJK font fallback tested with verified installed font '{cjk_font}' (fandol_available={fandol_ok}; update source .tex to adopt permanently).")
        if not patched and not pdf_path.is_file() and "ERR-MISSING-STY-CLS" in rule_ids:
            missing_stys = []
            for e in diag["errors"]:
                m_sty = re.search(r"File `([^']+\.sty)' not found", e.get("message", ""))
                if m_sty:
                    missing_stys.append(m_sty.group(1)[:-4])
            if missing_stys:
                new_src = fb_src
                for sty in missing_stys:
                    new_src = re.sub(rf"^\s*\\usepackage(?:\[[^\]]*\])?\{{{re.escape(sty)}\}}.*$", f"% [AUTO-FALLBACK COMMENTED MISSING STY: {sty}]", new_src, flags=re.M)
                if new_src != fb_src:
                    fb_tex.write_text(new_src, encoding="utf-8")
                    patched = True
                    fallback_notes.append(f"Diagnostic missing-.sty fallback preview attempted after commenting optional missing package(s): {missing_stys}.")
        if patched:
            fb_env = _build_tex_search_env(fb_dir, fb_tex.parent)
            fb_eng = "xelatex" if which_tool("xelatex") and (has_cjk(fb_tex.read_text(encoding="utf-8", errors="ignore")) or engine == "xelatex") else engine
            fb_res = _run_multipass_engine(fb_tex, fb_eng, bib_tool, taxonomy, min(3, max_passes), timeout_sec, bib_arg=bib_arg, extra_env=fb_env)
            fixes_glyphs = bool(diag["missing_characters"]) and not fb_res["diagnosis"]["missing_characters"]
            if fb_res["pdf_path"].is_file() and (not pdf_path.is_file() or fixes_glyphs):
                pdf_path = fb_res["pdf_path"]
                fallback_preview_used = True
                fallback_notes.append(f"Fallback preview PDF generated at {pdf_path.name} (fixes_missing_glyphs={fixes_glyphs}); fix primary .tex source before marking status='completed'.")

    previews, pdf_audit = render_pdf_previews(pdf_path, preview_dir)

    last_rc = commands_executed[-1]["returncode"]
    if not converged and last_rc == 0 and not diag["errors"] and not diag["rerun_requested"] and not bib_error:
        converged = True

    if (
        last_rc == 0
        and not diag["errors"]
        and not bib_error
        and pdf_path.is_file()
        and not fallback_preview_used
        and not diag["missing_characters"]
        and not diag["undefined_references"]
        and not diag["undefined_citations"]
        and not pdf_audit["unresolved_markers"]
    ):
        status = "compiled_clean"
    elif pdf_path.is_file():
        status = "compiled_with_issues"
    else:
        status = "compile_failed"

    pass_count = sum(1 for c in commands_executed if c["step"].startswith("pass_"))
    return {
        "schema_version": "1.0",
        "status": status,
        "engine": engine,
        "engine_version": eng_version,
        "bib_tool": run_bib_tool or "none",
        "bib_error": bib_error,
        "tex_file": str(rel_inside.as_posix()),
        "build_dir": str(build_dir),
        "project_root": str(staged_root),
        "pdf_path": str(pdf_path) if pdf_path.is_file() else None,
        "previews": previews,
        "converged": converged,
        "pass_count": pass_count,
        "commands_executed": commands_executed,
        "fallback_notes": fallback_notes,
        "fallback_preview_used": fallback_preview_used,
        "log_diagnosis": diag,
        "bib_diagnostics": bib_diagnostics[:60],
        "pdf_audit": pdf_audit,
        "reproducibility_manifest": {
            "engine": engine,
            "engine_version": eng_version,
            "bib_tool": run_bib_tool or "none",
            "pass_count": pass_count,
            "commands": [c["cmd"] for c in commands_executed],
            "converged": converged,
            "loaded_packages": diag["loaded_packages"],
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
        f"- **Errors**: {len(diag['errors'])} total ({len(diag['root_errors'])} root causes) | **Warnings**: {len(diag['warnings'])} | **Undefined refs/cites**: {len(diag.get('undefined_references', []))}/{len(diag.get('undefined_citations', []))} | **Missing glyphs**: {len(diag['missing_characters'])} | **Overfull hbox**: {len(diag['overfull_hboxes'])} ({len(diag['large_overfull_hboxes'])} >=10pt)",
        "",
    ]
    if report.get("fallback_notes"):
        lines.append("## Fallback & Recovery Notes")
        for fn in report["fallback_notes"]:
            lines.append(f"- {fn}")
        lines.append("")

    if diag.get("undefined_references") or diag.get("undefined_citations"):
        lines.append("## Undefined References & Citations")
        for ur in diag.get("undefined_references", [])[:20]:
            lines.append(f"- Undefined reference `{ur['key']}` (page {ur['page']}, {ur['file']}:{ur['line']})")
        for uc in diag.get("undefined_citations", [])[:20]:
            lines.append(f"- Undefined citation `{uc['key']}` (page {uc['page']}, {uc['file']}:{uc['line']})")
        lines.append("")

    if diag.get("overfull_hboxes"):
        lines.append("## Overfull \\hbox Warnings")
        for oh in diag["overfull_hboxes"][:20]:
            lines.append(f"- `{oh['file']}` ({oh['pt']}pt): {oh['line']}")
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
        lines.append("## Bibliography Diagnostics")
        for bd in report["bib_diagnostics"][:20]:
            lines.append(f"- {bd}")
        lines.append("")

    if diag["missing_characters"] or pa.get("unresolved_markers") or pa.get("empty_pages") or pa.get("overflow_findings"):
        lines.append("## PDF & Glyph Quality Alerts")
        for mc in diag["missing_characters"][:15]:
            lines.append(f"- Missing glyph: `{mc}`")
        for um in pa.get("unresolved_markers", []):
            lines.append(f"- Unresolved marker on page {um['page']}: `{um['marker']}`")
        if pa.get("empty_pages"):
            lines.append(f"- Empty pages detected: {pa['empty_pages']}")
        for ov in pa.get("overflow_findings", [])[:15]:
            lines.append(f"- Right-margin visual overflow on page {ov['page']} (x1={ov['x1']}/{ov['page_width']}): `{ov['snippet']}`")
        lines.append("")

    lines.append("## Reproducible Command Sequence")
    for cmd in report["reproducibility_manifest"]["commands"]:
        lines.append(f"- `{cmd}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-pass LaTeX compile, error clustering, PDF audit, and preview generator.")
    parser.add_argument("--tex", required=True, help="Path to main .tex entry file.")
    parser.add_argument("--project-root", default=None, help="Optional project root directory containing sibling assets/figures/styles.")
    parser.add_argument("--work-dir", default="/app/work/build", help="Writable build directory.")
    parser.add_argument("--preview-dir", default="/app/work/previews", help="Directory for rendered PNG page previews.")
    parser.add_argument("--skill-dir", default=None, help="Optional Skill root directory.")
    parser.add_argument("--engine", default="auto", choices=["auto", "pdflatex", "xelatex", "lualatex"])
    parser.add_argument("--bib", default="auto", choices=["auto", "bibtex", "biber", "none"])
    parser.add_argument("--max-passes", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--no-auto-fallback", action="store_true", help="Disable non-destructive engine/font/package fallback diagnostics.")
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
        auto_fallback=not args.no_auto_fallback,
        project_root=Path(args.project_root) if args.project_root else None,
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
