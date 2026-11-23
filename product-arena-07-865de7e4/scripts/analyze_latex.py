#!/usr/bin/env python3
"""Step 2: Static LaTeX project graph analysis, bug/quality audit, and non-TeX source fact extraction.

Produces:
- /app/work/latex_audit.json (machine-readable findings, include graph, bib/ref graph, source facts)
- /app/work/latex_digest.md  (compact token-efficient summary for LLM authoring/repair)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    extract_braced_arg,
    extract_numeric_tokens,
    extract_tex_commands,
    has_cjk,
    line_number_at,
    load_json_safe,
    normalize_num_str,
    posix_rel,
    read_text_safe,
    resolve_skill_dir,
    run_cmd,
    save_json,
    strip_tex_comments_preserve_lines,
    which_tool,
)

SKIP_SCAN_ENVS = ("verbatim", "verbatim*", "lstlisting", "minted", "comment", "filecontents", "filecontents*")
MATH_ENVS = {"equation", "equation*", "align", "align*", "gather", "gather*", "multline", "multline*", "flalign", "flalign*", "alignat", "alignat*", "eqnarray", "eqnarray*", "split", "cases"}
CITE_CMDS = ("cite", "citep", "citet", "citealp", "citealt", "citeauthor", "citeyear", "autocite", "parencite", "textcite", "footcite", "fullcite", "nocite")
REF_CMDS = ("ref", "eqref", "pageref", "autoref", "cref", "Cref", "nameref", "vref")


def mask_verbatim_and_verb(src: str) -> str:
    """Replace verbatim environments and \\verb|...| bodies with spaces while preserving line numbers."""
    chars = list(src)

    def blank_span(start: int, end: int) -> None:
        for idx in range(start, end):
            if chars[idx] != "\n":
                chars[idx] = " "

    env_alt = "|".join(re.escape(e) for e in SKIP_SCAN_ENVS)
    for m in re.finditer(rf"\\begin\{{({env_alt})\}}.*?\\end\{{\1\}}", src, re.S):
        blank_span(m.start(), m.end())

    for m in re.finditer(r"\\verb\*?([^\s\w])(.*?)\1", src):
        blank_span(m.start(), m.end())

    return "".join(chars)


def check_brace_and_env_balance(rel_path: str, raw_text: str) -> List[Dict[str, Any]]:
    """Detect unclosed '{', extra '}', mismatched \\begin/\\end, and unclosed math delimiters."""
    findings: List[Dict[str, Any]] = []
    clean = mask_verbatim_and_verb(strip_tex_comments_preserve_lines(raw_text))

    # 1. Brace stack
    brace_stack: List[Tuple[int, str]] = []
    n = len(clean)
    i = 0
    while i < n:
        ch = clean[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            line = line_number_at(clean, i)
            snippet = raw_text.splitlines()[line - 1].strip()[:100] if line - 1 < len(raw_text.splitlines()) else ""
            brace_stack.append((line, snippet))
        elif ch == "}":
            if not brace_stack:
                line = line_number_at(clean, i)
                findings.append(
                    {
                        "id": "LINT-EXTRA-RBRACE",
                        "severity": "error",
                        "file": rel_path,
                        "line": line,
                        "message": "Unmatched closing brace '}' (no matching opening '{').",
                    }
                )
            else:
                brace_stack.pop()
        i += 1

    for open_line, snippet in brace_stack:
        findings.append(
            {
                "id": "LINT-UNCLOSED-BRACE",
                "severity": "error",
                "file": rel_path,
                "line": open_line,
                "message": f"Unclosed opening brace '{{' opened here (snippet: {snippet!r}).",
            }
        )

    # 2. Environment nesting stack
    env_stack: List[Tuple[str, int]] = []
    for m in re.finditer(r"\\(begin|end)\{([^}\s]+)\}", clean):
        kind, env_name = m.group(1), m.group(2)
        line = line_number_at(clean, m.start())
        if kind == "begin":
            env_stack.append((env_name, line))
        else:
            if not env_stack:
                findings.append(
                    {
                        "id": "LINT-STRAY-END",
                        "severity": "error",
                        "file": rel_path,
                        "line": line,
                        "message": f"\\end{{{env_name}}} has no matching \\begin{{{env_name}}}.",
                    }
                )
            elif env_stack[-1][0] == env_name:
                env_stack.pop()
            else:
                # Search up the stack for recovery
                found_idx = None
                for k in range(len(env_stack) - 1, -1, -1):
                    if env_stack[k][0] == env_name:
                        found_idx = k
                        break
                if found_idx is not None:
                    while len(env_stack) - 1 > found_idx:
                        unclosed_env, unclosed_line = env_stack.pop()
                        findings.append(
                            {
                                "id": "LINT-UNCLOSED-ENV",
                                "severity": "error",
                                "file": rel_path,
                                "line": unclosed_line,
                                "message": f"\\begin{{{unclosed_env}}} opened at line {unclosed_line} was never closed before \\end{{{env_name}}} at line {line}.",
                            }
                        )
                    env_stack.pop()
                else:
                    top_env, top_line = env_stack[-1]
                    findings.append(
                        {
                            "id": "LINT-ENV-MISMATCH",
                            "severity": "error",
                            "file": rel_path,
                            "line": line,
                            "message": f"\\end{{{env_name}}} does not match open \\begin{{{top_env}}} from line {top_line}.",
                        }
                    )

    for unclosed_env, unclosed_line in env_stack:
        findings.append(
            {
                "id": "LINT-UNCLOSED-ENV",
                "severity": "error",
                "file": rel_path,
                "line": unclosed_line,
                "message": f"\\begin{{{unclosed_env}}} opened at line {unclosed_line} reaches EOF without \\end{{{unclosed_env}}}.",
            }
        )

    return findings


def check_tables_math_and_antipatterns(
    rel_path: str,
    raw_text: str,
    conflict_rules: Dict[str, Any],
    recommended_engine: str,
) -> List[Dict[str, Any]]:
    """Check tables, captions, label order, deprecated macros, and unescaped hazards."""
    findings: List[Dict[str, Any]] = []
    clean = mask_verbatim_and_verb(strip_tex_comments_preserve_lines(raw_text))

    # Deprecated / anti-pattern macros from package_conflicts.json
    for anti in conflict_rules.get("deprecated_or_antipattern_macros", []):
        pat = anti.get("pattern", "")
        if not pat:
            continue
        for m in re.finditer(pat, clean, re.S):
            findings.append(
                {
                    "id": "LINT-ANTIPATTERN",
                    "severity": "warning",
                    "file": rel_path,
                    "line": line_number_at(clean, m.start()),
                    "message": f"{anti.get('reason')} Suggested fix: {anti.get('replacement')}",
                }
            )

    # Float caption / label order and table caption placement
    for float_kind in ("figure", "figure*", "table", "table*"):
        pat = re.compile(rf"\\begin\{{{re.escape(float_kind)}\}}(.*?)\\end\{{{re.escape(float_kind)}\}}", re.S)
        for fm in pat.finditer(clean):
            body = fm.group(1)
            base_offset = fm.start(1)
            cap_m = re.search(r"\\caption(?:\[[^\]]*\])?\{", body)
            lbl_m = re.search(r"\\label\{([^}]+)\}", body)
            if lbl_m and cap_m and lbl_m.start() < cap_m.start():
                findings.append(
                    {
                        "id": "LINT-LABEL-BEFORE-CAPTION",
                        "severity": "error",
                        "file": rel_path,
                        "line": line_number_at(clean, base_offset + lbl_m.start()),
                        "message": f"\\label{{{lbl_m.group(1)}}} appears BEFORE \\caption inside {float_kind}; cross-references will point to the enclosing section number instead of the {float_kind.rstrip('*')} number.",
                    }
                )
            if float_kind.startswith("table") and cap_m:
                tab_m = re.search(r"\\begin\{(?:tabular|tabularx|tabular\*|longtable)\}", body)
                if tab_m and cap_m.start() > tab_m.start():
                    findings.append(
                        {
                            "id": "LINT-TABLE-CAPTION-BOTTOM",
                            "severity": "warning",
                            "file": rel_path,
                            "line": line_number_at(clean, base_offset + cap_m.start()),
                            "message": "Table \\caption appears below tabular; academic templates (IEEE/ACM/Springer) expect table captions ABOVE tabular.",
                        }
                    )

    # Raw \hline tables without booktabs
    for tm in re.finditer(r"\\begin\{(?:tabular|tabularx|longtable)\}(.*?)\\end\{(?:tabular|tabularx|longtable)\}", clean, re.S):
        tbody = tm.group(1)
        if r"\hline" in tbody and not any(bt in tbody for bt in (r"\toprule", r"\midrule", r"\bottomrule")):
            findings.append(
                {
                    "id": "LINT-NO-BOOKTABS",
                    "severity": "info",
                    "file": rel_path,
                    "line": line_number_at(clean, tm.start()),
                    "message": "Table uses raw \\hline rules without booktabs (\\toprule/\\midrule/\\bottomrule).",
                }
            )

    # Unicode symbols under pdflatex
    if recommended_engine == "pdflatex":
        for idx, line_str in enumerate(clean.splitlines(), start=1):
            bad_chars = re.findall(r"[≤≥±×÷≈≠αβγδεζηθλμπρστφψωΔΣΩ°]", line_str)
            if bad_chars:
                uniq = "".join(sorted(set(bad_chars)))
                findings.append(
                    {
                        "id": "LINT-UNICODE-IN-PDFLATEX",
                        "severity": "warning",
                        "file": rel_path,
                        "line": idx,
                        "message": f"Raw Unicode math/symbol characters ({uniq}) detected while pdflatex is selected; replace with LaTeX math commands ($\\le$, $\\pm$, $\\alpha$) or switch to xelatex.",
                    }
                )

    return findings


def parse_bib_file(rel_path: str, raw_text: str) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse .bib database entries and catch duplicate keys or syntax defects."""
    entries: Dict[str, Dict[str, Any]] = {}
    findings: List[Dict[str, Any]] = []

    n = len(raw_text)
    i = 0
    while i < n:
        at = raw_text.find("@", i)
        if at == -1:
            break
        m = re.match(r"@([A-Za-z]+)\s*([({])", raw_text[at:])
        if not m:
            i = at + 1
            continue
        etype = m.group(1).lower()
        opener = m.group(2)
        closer = ")" if opener == "(" else "}"
        start_line = line_number_at(raw_text, at)
        if etype in {"comment", "preamble", "string"}:
            # Skip balanced block
            depth = 0
            j = at + m.end() - 1
            while j < n:
                if raw_text[j] == opener:
                    depth += 1
                elif raw_text[j] == closer:
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            i = j + 1
            continue

        # Balanced scan for entry body
        depth = 0
        j = at + m.end() - 1
        while j < n:
            ch = raw_text[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and closer == "}":
                    break
            elif ch == ")" and closer == ")" and depth == 0:
                break
            j += 1

        if j >= n and depth != 0:
            findings.append(
                {
                    "id": "BIB-UNCLOSED-ENTRY",
                    "severity": "error",
                    "file": rel_path,
                    "line": start_line,
                    "message": f"BibTeX @{etype} entry starting at line {start_line} has unclosed braces.",
                }
            )
            break

        body = raw_text[at + m.end() : j]
        comma_idx = body.find(",")
        if comma_idx == -1:
            findings.append(
                {
                    "id": "BIB-MISSING-COMMA",
                    "severity": "error",
                    "file": rel_path,
                    "line": start_line,
                    "message": f"BibTeX @{etype} entry at line {start_line} is missing a comma after citation key.",
                }
            )
            i = j + 1
            continue

        key = body[:comma_idx].strip()
        if not key:
            findings.append(
                {
                    "id": "BIB-EMPTY-KEY",
                    "severity": "error",
                    "file": rel_path,
                    "line": start_line,
                    "message": f"BibTeX @{etype} entry at line {start_line} has an empty citation key.",
                }
            )
            i = j + 1
            continue

        fields_text = body[comma_idx + 1 :]
        fields: Dict[str, str] = {}
        for fm in re.finditer(r"([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*", fields_text):
            fname = fm.group(1).lower()
            pos = fm.end()
            while pos < len(fields_text) and fields_text[pos] in " \t\r\n":
                pos += 1
            if pos >= len(fields_text):
                break
            if fields_text[pos] == "{":
                val, _ = extract_braced_arg(fields_text, pos)
            elif fields_text[pos] == '"':
                endq = pos + 1
                while endq < len(fields_text):
                    if fields_text[endq] == "\\" and endq + 1 < len(fields_text):
                        endq += 2
                        continue
                    if fields_text[endq] == '"':
                        break
                    endq += 1
                val = fields_text[pos + 1 : endq]
            else:
                endm = re.search(r"[,\n}]", fields_text[pos:])
                val = fields_text[pos : pos + endm.start()] if endm else fields_text[pos:]
            fields[fname] = " ".join(val.split())

            # Detect unescaped '&' or '%' inside value
            if re.search(r"(?<!\\)[&%]", val):
                findings.append(
                    {
                        "id": "BIB-UNESCAPED-CHAR",
                        "severity": "warning",
                        "file": rel_path,
                        "line": start_line + line_number_at(fields_text, fm.start()) - 1,
                        "message": f"Unescaped '&' or '%' in BibTeX field '{fname}' of key '{key}' may break compilation.",
                    }
                )

        if key in entries:
            findings.append(
                {
                    "id": "BIB-DUPLICATE-KEY",
                    "severity": "error",
                    "file": rel_path,
                    "line": start_line,
                    "message": f"Duplicate BibTeX key '{key}' (previously defined at {entries[key]['file']}:{entries[key]['line']}).",
                }
            )
        else:
            entries[key] = {
                "key": key,
                "type": etype,
                "file": rel_path,
                "line": start_line,
                "fields": fields,
            }
        i = j + 1

    return entries, findings


def resolve_tex_target(raw_target: str, current_tex: Path, root_dir: Path) -> Optional[Path]:
    """Resolve \\input/\\include target relative to current tex or root dir."""
    cand_names = [raw_target]
    if not Path(raw_target).suffix:
        cand_names.append(raw_target + ".tex")
    for base in (current_tex.parent, root_dir):
        for name in cand_names:
            p = (base / name).resolve()
            if p.is_file():
                return p
    return None


def resolve_image_target(raw_target: str, current_tex: Path, root_dir: Path, graphics_paths: List[str]) -> Optional[Path]:
    """Resolve \\includegraphics target with extension and graphicspath fallback."""
    exts = [""] if Path(raw_target).suffix else ["", ".pdf", ".png", ".jpg", ".jpeg", ".eps"]
    search_bases = [current_tex.parent, root_dir]
    for gp in graphics_paths:
        search_bases.append((root_dir / gp).resolve())
        search_bases.append((current_tex.parent / gp).resolve())

    for base in search_bases:
        for ext in exts:
            p = (base / (raw_target + ext)).resolve()
            if p.is_file():
                return p
    return None


def extract_non_tex_facts(input_dir: Path, files: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Extract grounded text snippets, headings, tables, and numbers from non-TeX inputs with locators."""
    facts: List[Dict[str, Any]] = []
    available_norm_numbers: Set[str] = set()

    for f in files:
        if not f["readable"]:
            continue
        cat = f["category"]
        rel = f["rel_path"]
        fpath = Path(f["abs_path"])
        if cat not in {"draft_or_doc", "tabular_data", "structured_data", "word_doc", "pdf_doc", "tex_source", "bib_database"}:
            continue

        if cat in {"tex_source", "bib_database"}:
            txt, _ = read_text_safe(fpath)
            for tok in extract_numeric_tokens(txt):
                norm = normalize_num_str(tok)
                if norm:
                    available_norm_numbers.add(norm)
            continue

        if cat in {"draft_or_doc", "structured_data"}:
            txt, _ = read_text_safe(fpath)
            lines = txt.splitlines()
            for tok in extract_numeric_tokens(txt):
                norm = normalize_num_str(tok)
                if norm:
                    available_norm_numbers.add(norm)
            for idx, line in enumerate(lines[:300], start=1):
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#") or extract_numeric_tokens(s) or "|" in s or re.search(r"\[[0-9,\s-]+\]|doi:|10\.\d{4,}", s, re.I):
                    facts.append({"file": rel, "locator": f"{rel}:L{idx}", "kind": "line", "text": s[:240]})

        elif cat == "tabular_data":
            txt, _ = read_text_safe(fpath)
            delim = "\t" if f["ext"] == ".tsv" else ","
            rows = list(csv.reader(txt.splitlines(), delimiter=delim))
            for tok in extract_numeric_tokens(txt):
                norm = normalize_num_str(tok)
                if norm:
                    available_norm_numbers.add(norm)
            for idx, row in enumerate(rows[:60], start=1):
                if any(c.strip() for c in row):
                    facts.append({"file": rel, "locator": f"{rel}:R{idx}", "kind": "table_row", "text": " | ".join(c.strip() for c in row)[:240]})

        elif cat == "word_doc":
            try:
                with zipfile.ZipFile(fpath) as zf:
                    if "word/document.xml" in zf.namelist():
                        xml_bytes = zf.read("word/document.xml")
                        root = ET.fromstring(xml_bytes)
                        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                        p_idx = 0
                        for p in root.findall(".//w:p", ns):
                            p_idx += 1
                            text = "".join(t.text or "" for t in p.findall(".//w:t", ns)).strip()
                            if not text:
                                continue
                            for tok in extract_numeric_tokens(text):
                                norm = normalize_num_str(tok)
                                if norm:
                                    available_norm_numbers.add(norm)
                            if p_idx <= 120:
                                facts.append({"file": rel, "locator": f"{rel}:P{p_idx}", "kind": "docx_paragraph", "text": text[:240]})
            except Exception as exc:
                facts.append({"file": rel, "locator": rel, "kind": "parse_error", "text": f"Failed to parse DOCX: {exc}"})

        elif cat == "pdf_doc":
            extracted_pages: List[Tuple[int, str]] = []
            try:
                try:
                    import pymupdf as fitz  # type: ignore
                except ImportError:
                    import fitz  # type: ignore

                doc = fitz.open(str(fpath))
                for pno, page in enumerate(doc, start=1):
                    extracted_pages.append((pno, page.get_text("text")))
            except Exception:
                if which_tool("pdftotext"):
                    res = run_cmd(["pdftotext", str(fpath), "-"], timeout=20)
                    if res["returncode"] == 0 and res["stdout"].strip():
                        for pno, chunk in enumerate(res["stdout"].split("\f"), start=1):
                            if chunk.strip():
                                extracted_pages.append((pno, chunk))
            if not extracted_pages:
                facts.append({"file": rel, "locator": rel, "kind": "pdf_unextracted", "text": "No text extracted from PDF (needs OCR or PyMuPDF/pdftotext)."})
            else:
                for pno, ptxt in extracted_pages:
                    for tok in extract_numeric_tokens(ptxt):
                        norm = normalize_num_str(tok)
                        if norm:
                            available_norm_numbers.add(norm)
                    for lno, line in enumerate(ptxt.splitlines()[:80], start=1):
                        s = line.strip()
                        if s and (lno <= 8 or extract_numeric_tokens(s)):
                            facts.append({"file": rel, "locator": f"{rel}:p{pno}_L{lno}", "kind": "pdf_line", "text": s[:240]})

    return facts[:200], sorted(available_norm_numbers)


def analyze_case(case_plan: Dict[str, Any], skill_dir: Path) -> Dict[str, Any]:
    """Perform static LaTeX project audit + source fact extraction."""
    input_dir = Path(case_plan["input_dir"])
    conflict_rules = load_json_safe(skill_dir / "resources" / "package_conflicts.json", default={}) or {}
    template_profiles = load_json_safe(skill_dir / "resources" / "template_profiles.json", default={}) or {}

    files = case_plan.get("files", [])
    tex_files = [f for f in files if f["category"] == "tex_source" and f["readable"]]
    bib_files = [f for f in files if f["category"] == "bib_database" and f["readable"]]

    findings: List[Dict[str, Any]] = []
    all_labels: Dict[str, List[Dict[str, Any]]] = {}
    all_refs: List[Dict[str, Any]] = []
    all_cites: List[Dict[str, Any]] = []
    packages_used: List[Dict[str, Any]] = []
    doc_classes: List[Dict[str, Any]] = []
    bib_resources: List[str] = []
    bib_styles: List[str] = []
    graphics_paths: List[str] = []
    include_graph: Dict[str, List[str]] = {}
    tex_contains_cjk = False
    bib_entries: Dict[str, Dict[str, Any]] = {}

    # 1. Parse all .bib files
    for bf in bib_files:
        txt, _ = read_text_safe(Path(bf["abs_path"]))
        entries, b_findings = parse_bib_file(bf["rel_path"], txt)
        findings.extend(b_findings)
        for k, entry in entries.items():
            if k in bib_entries:
                findings.append(
                    {
                        "id": "BIB-DUPLICATE-KEY",
                        "severity": "error",
                        "file": entry["file"],
                        "line": entry["line"],
                        "message": f"Duplicate BibTeX key '{k}' across files (also in {bib_entries[k]['file']}:{bib_entries[k]['line']}).",
                    }
                )
            else:
                bib_entries[k] = entry

    # 2. First pass over .tex files: collect preamble metadata to determine engine
    tex_texts: Dict[str, str] = {}
    for tf in tex_files:
        txt, _ = read_text_safe(Path(tf["abs_path"]))
        tex_texts[tf["rel_path"]] = txt
        if has_cjk(txt):
            tex_contains_cjk = True

        for dc in extract_tex_commands(txt, ("documentclass",)):
            doc_classes.append({"class": dc["arg"], "options": dc["opt"], "file": tf["rel_path"], "line": dc["line"]})
        for pk in extract_tex_commands(txt, ("usepackage", "RequirePackage")):
            for p_item in pk["arg"].split(","):
                pname = p_item.strip()
                if pname:
                    packages_used.append({"package": pname, "options": pk["opt"], "file": tf["rel_path"], "line": pk["line"]})

    pkg_names_lower = {p["package"].lower(): p for p in packages_used}
    cls_names = [d["class"] for d in doc_classes]
    engine_reqs = conflict_rules.get("engine_requirements", {})
    xelatex_pkgs = {x.lower() for x in engine_reqs.get("xelatex_or_lualatex_required", [])}

    if tex_contains_cjk or any(c.startswith("ctex") for c in cls_names) or any(p in xelatex_pkgs for p in pkg_names_lower):
        recommended_engine = "xelatex"
    else:
        recommended_engine = "pdflatex"

    # 3. Deep per-.tex checks
    for tf in tex_files:
        rel = tf["rel_path"]
        fpath = Path(tf["abs_path"])
        txt = tex_texts[rel]

        findings.extend(check_brace_and_env_balance(rel, txt))
        findings.extend(check_tables_math_and_antipatterns(rel, txt, conflict_rules, recommended_engine))

        # Graphicspath
        for gp_m in re.finditer(r"\\graphicspath\{\s*(.*?)\s*\}", strip_tex_comments_preserve_lines(txt), re.S):
            for sub in re.findall(r"\{([^}]+)\}", gp_m.group(1)):
                if sub not in graphics_paths:
                    graphics_paths.append(sub)

        # Include graph
        children: List[str] = []
        for inc in extract_tex_commands(txt, ("input", "include", "subfile")):
            target = inc["arg"]
            if not target:
                continue
            resolved = resolve_tex_target(target, fpath, input_dir)
            if resolved is None:
                findings.append(
                    {
                        "id": "LINT-MISSING-INCLUDE",
                        "severity": "error",
                        "file": rel,
                        "line": inc["line"],
                        "message": f"Included file '{target}' not found under {input_dir.name}/.",
                    }
                )
            else:
                child_rel = posix_rel(resolved, input_dir)
                children.append(child_rel)
        include_graph[rel] = children

        # Labels & refs
        for lb in extract_tex_commands(txt, ("label",)):
            lkey = lb["arg"]
            if lkey:
                all_labels.setdefault(lkey, []).append({"file": rel, "line": lb["line"]})
        for rf in extract_tex_commands(txt, REF_CMDS):
            for rkey in rf["arg"].split(","):
                rk = rkey.strip()
                if rk:
                    all_refs.append({"key": rk, "cmd": rf["command"], "file": rel, "line": rf["line"]})

        # Citations & bibliographies
        for ct in extract_tex_commands(txt, CITE_CMDS):
            for ckey in ct["arg"].split(","):
                ck = ckey.strip()
                if ck:
                    all_cites.append({"key": ck, "cmd": ct["command"], "file": rel, "line": ct["line"]})
        for br in extract_tex_commands(txt, ("bibliography", "addbibresource")):
            for item in br["arg"].split(","):
                it = item.strip()
                if it:
                    bib_resources.append(it)
        for bs in extract_tex_commands(txt, ("bibliographystyle",)):
            if bs["arg"]:
                bib_styles.append(bs["arg"])

        # Graphics files
        for ig in extract_tex_commands(txt, ("includegraphics",)):
            img = ig["arg"]
            if not img:
                continue
            if resolve_image_target(img, fpath, input_dir, graphics_paths) is None:
                findings.append(
                    {
                        "id": "LINT-MISSING-FIGURE",
                        "severity": "error",
                        "file": rel,
                        "line": ig["line"],
                        "message": f"Figure asset '{img}' referenced in \\includegraphics was not found.",
                    }
                )

    # 4. Include cycle detection
    visited: Set[str] = set()
    stack_set: Set[str] = set()

    def dfs(node: str) -> None:
        visited.add(node)
        stack_set.add(node)
        for nxt in include_graph.get(node, []):
            if nxt in stack_set:
                findings.append(
                    {
                        "id": "LINT-INCLUDE-CYCLE",
                        "severity": "error",
                        "file": node,
                        "line": 1,
                        "message": f"Circular \\input/\\include cycle detected: {node} -> {nxt}.",
                    }
                )
            elif nxt not in visited:
                dfs(nxt)
        stack_set.remove(node)

    for node in include_graph:
        if node not in visited:
            dfs(node)

    # 5. Package conflict and order checks
    for pair in conflict_rules.get("incompatible_pairs", []):
        pa, pb = pair["pkg_a"].lower(), pair["pkg_b"].lower()
        if pa in pkg_names_lower and pb in pkg_names_lower:
            findings.append(
                {
                    "id": "LINT-PKG-CONFLICT",
                    "severity": "error",
                    "file": pkg_names_lower[pb]["file"],
                    "line": pkg_names_lower[pb]["line"],
                    "message": f"Incompatible packages '{pair['pkg_a']}' and '{pair['pkg_b']}': {pair['reason']} Fix: {pair['resolution']}",
                }
            )

    # Check load order within same file
    pkgs_by_file: Dict[str, List[Dict[str, Any]]] = {}
    for p in packages_used:
        pkgs_by_file.setdefault(p["file"], []).append(p)
    for fpath_rel, plist in pkgs_by_file.items():
        idx_map = {p["package"].lower(): i for i, p in enumerate(plist)}
        for rule in conflict_rules.get("load_order_rules", []):
            first_k, second_k = rule["first"].lower(), rule["second"].lower()
            if first_k in idx_map and second_k in idx_map and idx_map[second_k] < idx_map[first_k]:
                findings.append(
                    {
                        "id": "LINT-PKG-ORDER",
                        "severity": "warning",
                        "file": fpath_rel,
                        "line": plist[idx_map[second_k]]["line"],
                        "message": f"Package '{rule['second']}' loaded before '{rule['first']}': {rule['reason']}",
                    }
                )

    # 6. Missing class check against local / kpsewhich
    env = case_plan.get("environment", {})
    kpse_available = bool(env.get("tools", {}).get("kpsewhich"))
    local_cls = {Path(f["rel_path"]).name for f in files if f["category"] == "tex_class_or_style"}
    for dc in doc_classes:
        cls_file = dc["class"] + ".cls"
        if cls_file not in local_cls:
            kpse_cache = env.get("kpse_checks", {})
            found = kpse_cache.get(cls_file)
            if found is None and kpse_available:
                res = run_cmd(["kpsewhich", cls_file], timeout=10)
                out_path = (res.get("stdout") or "").strip()
                found = out_path if (res["returncode"] == 0 and out_path) else None
            if kpse_available and not found:
                findings.append(
                    {
                        "id": "LINT-MISSING-CLS",
                        "severity": "error",
                        "file": dc["file"],
                        "line": dc["line"],
                        "message": f"Document class '{cls_file}' not found in /input or kpsewhich TEXMF.",
                    }
                )

    # 7. Label/Ref and Cite/Bib cross-checks
    for lkey, locs in all_labels.items():
        if len(locs) > 1:
            findings.append(
                {
                    "id": "LINT-DUPLICATE-LABEL",
                    "severity": "warning",
                    "file": locs[1]["file"],
                    "line": locs[1]["line"],
                    "message": f"Duplicate \\label{{{lkey}}} (first defined at {locs[0]['file']}:{locs[0]['line']}).",
                }
            )
    for rf in all_refs:
        if rf["key"] not in all_labels:
            findings.append(
                {
                    "id": "LINT-UNRESOLVED-REF",
                    "severity": "error",
                    "file": rf["file"],
                    "line": rf["line"],
                    "message": f"\\{rf['cmd']}{{{rf['key']}}} has no matching \\label{{{rf['key']}}} in project.",
                }
            )

    # Check inline thebibliography keys too
    inline_bib_keys: Set[str] = set()
    for rel, txt in tex_texts.items():
        for bm in extract_tex_commands(txt, ("bibitem",)):
            if bm["arg"]:
                inline_bib_keys.add(bm["arg"])

    all_known_bib_keys = set(bib_entries.keys()) | inline_bib_keys
    cited_keys: Set[str] = set()
    for ct in all_cites:
        ck = ct["key"]
        cited_keys.add(ck)
        if all_known_bib_keys or bib_files or bib_resources:
            if ck not in all_known_bib_keys:
                findings.append(
                    {
                        "id": "LINT-MISSING-CITE-KEY",
                        "severity": "error",
                        "file": ct["file"],
                        "line": ct["line"],
                        "message": f"Citation key '{ck}' in \\{ct['cmd']} is missing from project .bib / \\bibitem entries.",
                    }
                )
    orphan_bib_keys = sorted(k for k in bib_entries if k not in cited_keys)

    # Determine bib tool
    if "biblatex" in pkg_names_lower:
        recommended_bib = "biber"
    elif bib_resources or bib_styles or bib_files:
        recommended_bib = "bibtex"
    else:
        recommended_bib = "none"

    # 8. Template compliance preview
    profiles = template_profiles.get("profiles", {})
    template_checks: List[Dict[str, Any]] = []
    combined_tex = "\n".join(tex_texts.values())
    for tpl_name in set(cls_names) | set(case_plan.get("signals", {}).get("target_templates", [])):
        prof = profiles.get(tpl_name)
        if prof and combined_tex:
            reqs = []
            for req in prof.get("required_elements", []):
                # Extract command/env stem to check presence flexibly
                stem = req.split("{")[0].split("[")[0]
                present = stem in combined_tex
                reqs.append({"requirement": req, "present_in_tex": present})
            template_checks.append({"template": tpl_name, "checks": reqs, "pitfalls": prof.get("pitfalls", [])})

    # 9. Non-TeX source fact extraction
    source_facts, source_numbers = extract_non_tex_facts(input_dir, files)

    return {
        "schema_version": "1.0",
        "case_id": case_plan["case_id"],
        "recommended_engine": recommended_engine,
        "recommended_bib_tool": recommended_bib,
        "tex_contains_cjk": tex_contains_cjk,
        "document_classes": doc_classes,
        "packages": packages_used,
        "include_graph": include_graph,
        "bib_resources": bib_resources,
        "bib_styles": bib_styles,
        "bib_keys_count": len(all_known_bib_keys),
        "cited_keys_count": len(cited_keys),
        "orphan_bib_keys": orphan_bib_keys,
        "labels_count": len(all_labels),
        "findings": findings,
        "error_count": sum(1 for x in findings if x["severity"] == "error"),
        "warning_count": sum(1 for x in findings if x["severity"] == "warning"),
        "template_checks": template_checks,
        "source_facts": source_facts,
        "source_numbers": source_numbers,
    }


def render_digest_md(audit: Dict[str, Any], case_plan: Dict[str, Any]) -> str:
    """Render concise Markdown audit digest for LLM consumption."""
    lines = [
        f"# LaTeX Project & Source Audit Digest — `{audit['case_id']}`",
        "",
        f"- **Task mode**: `{case_plan['signals']['primary_mode']}` | **Language**: `{case_plan['signals']['language']}`",
        f"- **Recommended engine**: `{audit['recommended_engine']}` | **Bibliography tool**: `{audit['recommended_bib_tool']}`",
        f"- **Entry `.tex` candidates**: {', '.join(case_plan.get('candidate_entry_tex') or ['(none yet)']) }",
        f"- **Available CJK system fonts**: {', '.join(case_plan['environment'].get('cjk_fonts', [])[:8]) or '(none reported by fc-list)'}",
        f"- **Static findings**: {audit['error_count']} errors, {audit['warning_count']} warnings",
        "",
    ]

    if case_plan.get("access_warnings") or case_plan.get("missing_referenced_files"):
        lines.append("## Input Warnings & Missing Referenced Files")
        for w in case_plan.get("access_warnings", []):
            lines.append(f"- Access warning: `{w}`")
        for m in case_plan.get("missing_referenced_files", []):
            lines.append(f"- Missing referenced file: `{m}`")
        lines.append("")

    if audit["findings"]:
        lines.append("## Static Findings (Fix Root Causes First)")
        for f in audit["findings"][:60]:
            lines.append(f"- **[{f['severity'].upper()} {f['id']}]** `{f['file']}:{f['line']}` — {f['message']}")
        lines.append("")

    if audit["template_checks"]:
        lines.append("## Template Compliance Snapshot")
        for tc in audit["template_checks"]:
            lines.append(f"### `{tc['template']}`")
            for chk in tc["checks"]:
                mark = "OK" if chk["present_in_tex"] else "MISSING"
                lines.append(f"- [{mark}] `{chk['requirement']}`")
        lines.append("")

    if audit["source_facts"]:
        lines.append("## Grounded Non-TeX Source Facts (Preserve Exact Numbers & Locators)")
        for sf in audit["source_facts"][:60]:
            lines.append(f"- `{sf['locator']}` ({sf['kind']}): {sf['text']}")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze LaTeX project structure, bugs, citations, and source facts.")
    parser.add_argument("--case-plan", default="/app/work/case_plan.json", help="Path to case_plan.json")
    parser.add_argument("--skill-dir", default=None, help="Optional Skill root directory")
    parser.add_argument("--out-audit", default="/app/work/latex_audit.json", help="Output audit JSON path")
    parser.add_argument("--out-digest", default="/app/work/latex_digest.md", help="Output digest Markdown path")
    args = parser.parse_args()

    skill_dir = resolve_skill_dir(args.skill_dir)
    case_plan = load_json_safe(Path(args.case_plan))
    if not case_plan:
        raise SystemExit(f"Failed to load case_plan from {args.case_plan}")

    audit = analyze_case(case_plan, skill_dir)
    save_json(Path(args.out_audit), audit)
    digest_path = Path(args.out_digest)
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(render_digest_md(audit, case_plan), encoding="utf-8")

    print(
        f"[analyze_latex] engine={audit['recommended_engine']} bib={audit['recommended_bib_tool']} "
        f"errors={audit['error_count']} warnings={audit['warning_count']} "
        f"source_facts={len(audit['source_facts'])} -> {args.out_audit}"
    )


if __name__ == "__main__":
    main()
