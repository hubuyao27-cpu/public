#!/usr/bin/env python3
"""Step 1: Inspect case, stage /input & inline sources, audit TeX/Bib/facts, optionally apply safe fixes,
and save an immediate partial checkpoint under /app/output/result.json.

Exports reusable `inspect_case()`, `audit_tex_tree()`, and `apply_safe_fixes()` for verify_and_assemble.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    CATEGORY_BY_EXT,
    CONTROL_FILES,
    SKIP_DIRS,
    ensure_dir,
    extract_braced_arg,
    extract_numeric_tokens,
    extract_tex_commands,
    fandol_is_available,
    has_cjk,
    line_number_at,
    load_json_safe,
    normalize_num_str,
    parse_output_contract,
    pick_best_cjk_font,
    posix_rel,
    probe_tex_environment,
    read_text_safe,
    resolve_image_target,
    resolve_skill_dir,
    resolve_tex_target,
    run_cmd,
    sanitize_tex_for_scan,
    save_json,
    which_tool,
)

CITE_CMDS = ("cite", "citep", "citet", "citealp", "citealt", "citeauthor", "citeyear", "autocite", "parencite", "textcite", "footcite", "fullcite", "nocite")
REF_CMDS = ("ref", "eqref", "pageref", "autoref", "cref", "Cref", "nameref", "vref")
URL_LIKE_BIB_FIELDS = {"url", "doi", "eprint", "file", "howpublished", "issn", "isbn", "link"}
EXPECTED_OUTPUT_FILES = {
    "case.json", "task.md", "result.json", "SKILL.md", "build.sh",
    "reproducibility_manifest.json", "compilation_and_provenance_report.md",
    "initial_audit_digest.md", "deliverable_payload.json", "compile_report.json",
    "compile_summary.md", "verification_report.json", "case_plan.json",
    "latex_audit.json", "latex_digest.md",
}

BIB_REQUIRED_FIELDS = {
    "article": ("author", "title", "journal", "year"),
    "inproceedings": ("author", "title", "booktitle", "year"),
    "conference": ("author", "title", "booktitle", "year"),
    "book": ("title", "publisher", "year"),
    "incollection": ("author", "title", "booktitle", "year"),
    "phdthesis": ("author", "title", "school", "year"),
    "mastersthesis": ("author", "title", "school", "year"),
    "techreport": ("author", "title", "institution", "year"),
}

WINDOWS_ONLY_CJK_FONTS = {"SimSun", "SimHei", "FangSong", "KaiTi", "NSimSun", "Microsoft YaHei", "STSong", "STHeiti", "STKaiti", "PingFang SC"}
PATH_ARG_CMD_RE = re.compile(r"\\(?:includegraphics|input|include|subfile|bibliography|addbibresource|bibliographystyle|url|href|label|ref|eqref|pageref|autoref|cref|Cref|cite|citep|citet|autocite|parencite|textcite|texttt|detokenize)\*?(?:\[[^\]]*\])?\{[^}]*\}")
INLINE_MATH_OR_DISPLAY_RE = re.compile(r"(?<!\\)\$\$.*?(?<!\\)\$\$|(?<!\\)\$[^$\n]+?(?<!\\)\$|\\\[.*?\\\]|\\\(.*?\\\)", re.S)


def check_template_requirement(req: str, clean_tex: str) -> bool:
    """Evaluate a template_profiles.json requirement string accurately on sanitized TeX."""
    if " or " in req:
        return any(check_template_requirement(part.strip(), clean_tex) for part in req.split(" or "))
    m_dc = re.match(r"^\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}$", req.strip())
    if m_dc:
        return bool(re.search(rf"\\documentclass(?:\[[^\]]*\])?\{{{re.escape(m_dc.group(1))}\}}", clean_tex))
    m_up = re.match(r"^\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}$", req.strip())
    if m_up:
        pkg_pat = m_up.group(1)
        if pkg_pat.endswith("..."):
            return bool(re.search(rf"\\usepackage(?:\[[^\]]*\])?\{{[^}}]*{re.escape(pkg_pat[:-3])}", clean_tex))
        return bool(re.search(rf"\\usepackage(?:\[[^\]]*\])?\{{[^}}]*\b{re.escape(pkg_pat)}\b", clean_tex))
    m_env = re.match(r"^\\begin\{([^}]+)\}", req.strip())
    if m_env:
        return f"\\begin{{{m_env.group(1)}}}" in clean_tex
    m_end = re.match(r"^\\end\{([^}]+)\}$", req.strip())
    if m_end:
        return f"\\end{{{m_end.group(1)}}}" in clean_tex
    m_cmd = re.match(r"^\\([A-Za-z@]+)", req.strip())
    if m_cmd:
        return bool(re.search(rf"\\{re.escape(m_cmd.group(1))}\b", clean_tex))
    return req.strip() in clean_tex


def _count_tabular_spec_cols(spec: str) -> Optional[int]:
    """Count declared columns in a tabular column spec (handles *{N}{...} and p{...}/S[...])."""
    s = re.sub(r"\\arraybackslash|>\{[^}]*\}|<\{[^}]*\}|@\{[^}]*\}|!\{[^}]*\}", "", spec)
    # Expand simple *{N}{cols}
    for _ in range(4):
        m = re.search(r"\*\{(\d+)\}\{([^{}]*)\}", s)
        if not m:
            break
        s = s[: m.start()] + (m.group(2) * int(m.group(1))) + s[m.end() :]
    # Strip bracket/brace options of S[...], p{...}, m{...}, b{...}
    s = re.sub(r"\[[^\]]*\]|\{[^}]*\}", "", s)
    cols = re.findall(r"[lcrXpSmbd]", s)
    return len(cols) if cols else None


def _count_row_cells(row: str) -> int:
    """Count effective cells in a single LaTeX tabular row (respecting \\multicolumn{k} and \\&)."""
    # Strip trailing \\[...] and rule macros
    r = re.sub(r"\\\\\s*(?:\[[^\]]*\])?\s*$", "", row).strip()
    r = re.sub(r"\\(?:toprule|midrule|bottomrule|hline|cline\{[^}]*\}|cmidrule(?:\([^)]*\))?\{[^}]*\})\s*", "", r).strip()
    if not r:
        return 0
    # Split on unescaped '&' outside braces
    cells: List[str] = []
    cur: List[str] = []
    depth = 0
    i = 0
    while i < len(r):
        ch = r[i]
        if ch == "\\" and i + 1 < len(r):
            cur.append(r[i : i + 2])
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == "&" and depth == 0:
            cells.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())

    total = 0
    for c in cells:
        mc = re.match(r"^\\multicolumn\{(\d+)\}\s*\{", c)
        total += int(mc.group(1)) if mc else 1
    return total


def check_brace_and_env_balance(rel_path: str, raw_text: str) -> List[Dict[str, Any]]:
    """Detect unclosed '{', extra '}', mismatched \\begin/\\end, and per-paragraph unbalanced '$'."""
    findings: List[Dict[str, Any]] = []
    clean = sanitize_tex_for_scan(raw_text)

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
                findings.append({"id": "LINT-EXTRA-RBRACE", "severity": "error", "file": rel_path, "line": line_number_at(clean, i), "message": "Unmatched closing brace '}' (no matching opening '{')."})
            else:
                brace_stack.pop()
        i += 1

    for open_line, snippet in brace_stack:
        findings.append({"id": "LINT-UNCLOSED-BRACE", "severity": "error", "file": rel_path, "line": open_line, "message": f"Unclosed opening brace '{{' opened here (snippet: {snippet!r})."})

    env_stack: List[Tuple[str, int]] = []
    for m in re.finditer(r"\\(begin|end)\{([^}\s]+)\}", clean):
        kind, env_name = m.group(1), m.group(2)
        line = line_number_at(clean, m.start())
        if kind == "begin":
            env_stack.append((env_name, line))
        else:
            if not env_stack:
                findings.append({"id": "LINT-STRAY-END", "severity": "error", "file": rel_path, "line": line, "message": f"\\end{{{env_name}}} has no matching \\begin{{{env_name}}}."})
            elif env_stack[-1][0] == env_name:
                env_stack.pop()
            else:
                found_idx = next((k for k in range(len(env_stack) - 1, -1, -1) if env_stack[k][0] == env_name), None)
                if found_idx is not None:
                    while len(env_stack) - 1 > found_idx:
                        u_env, u_line = env_stack.pop()
                        findings.append({"id": "LINT-UNCLOSED-ENV", "severity": "error", "file": rel_path, "line": u_line, "message": f"\\begin{{{u_env}}} opened at line {u_line} was never closed before \\end{{{env_name}}} at line {line}."})
                    env_stack.pop()
                else:
                    t_env, t_line = env_stack[-1]
                    findings.append({"id": "LINT-ENV-MISMATCH", "severity": "error", "file": rel_path, "line": line, "message": f"\\end{{{env_name}}} does not match open \\begin{{{t_env}}} from line {t_line}."})

    for u_env, u_line in env_stack:
        findings.append({"id": "LINT-UNCLOSED-ENV", "severity": "error", "file": rel_path, "line": u_line, "message": f"\\begin{{{u_env}}} opened at line {u_line} reaches EOF without \\end{{{u_env}}}."})

    # Per-paragraph unbalanced '$' check inside document body
    m_doc = re.search(r"\\begin\{document\}(.*?)(?:\\end\{document\}|\Z)", clean, re.S)
    if m_doc:
        body_clean = m_doc.group(1)
        body_start_line = line_number_at(clean, m_doc.start(1))
        cur_line = body_start_line
        for para in re.split(r"\n\s*\n", body_clean):
            p_no_dd = re.sub(r"(?<!\\)\$\$", "", para)
            single_dollars = re.findall(r"(?<!\\)\$", p_no_dd)
            if len(single_dollars) % 2 == 1:
                findings.append({"id": "LINT-UNBALANCED-MATH", "severity": "error", "file": rel_path, "line": cur_line, "message": "Odd number of unescaped '$' inline math delimiters in paragraph starting near this line."})
            cur_line += para.count("\n") + 1

    return findings


def _mask_subfloat_regions(body: str) -> str:
    chars = list(body)

    def blank_span(start: int, end: int) -> None:
        for idx in range(start, end):
            if chars[idx] != "\n":
                chars[idx] = " "

    for m in re.finditer(r"\\begin\{(subfigure|subtable)\*?\}.*?\\end\{\1\*?\}", body, re.S):
        blank_span(m.start(), m.end())
    for m in re.finditer(r"\\subfloat\b", body):
        idx = m.end()
        n = len(body)
        while idx < n and body[idx] in " \t\r\n":
            idx += 1
        while idx < n and body[idx] == "[":
            eb = body.find("]", idx + 1)
            if eb == -1:
                break
            idx = eb + 1
            while idx < n and body[idx] in " \t\r\n":
                idx += 1
        if idx < n and body[idx] == "{":
            _, nxt = extract_braced_arg(body, idx)
            blank_span(m.start(), nxt if nxt != -1 else n)
    return "".join(chars)


def check_tables_math_and_antipatterns(
    rel_path: str,
    raw_text: str,
    conflict_rules: Dict[str, Any],
    recommended_engine: str,
) -> List[Dict[str, Any]]:
    """Check tables, column counts, captions, label order, deprecated macros, and unescaped text underscores."""
    findings: List[Dict[str, Any]] = []
    clean = sanitize_tex_for_scan(raw_text)

    for anti in conflict_rules.get("deprecated_or_antipattern_macros", []):
        pat = anti.get("pattern", "")
        if pat:
            for m in re.finditer(pat, clean, re.S):
                findings.append({"id": "LINT-ANTIPATTERN", "severity": "warning", "file": rel_path, "line": line_number_at(clean, m.start()), "message": f"{anti.get('reason')} Suggested fix: {anti.get('replacement')}"})

    for float_kind in ("figure", "figure*", "table", "table*"):
        pat = re.compile(rf"\\begin\{{{re.escape(float_kind)}\}}(.*?)\\end\{{{re.escape(float_kind)}\}}", re.S)
        for fm in pat.finditer(clean):
            body = fm.group(1)
            outer_body = _mask_subfloat_regions(body)
            base_offset = fm.start(1)
            cap_m = re.search(r"\\caption(?:\[[^\]]*\])?\{", outer_body)
            lbl_m = re.search(r"\\label\{([^}]+)\}", outer_body)
            if lbl_m and cap_m and lbl_m.start() < cap_m.start():
                findings.append({"id": "LINT-LABEL-BEFORE-CAPTION", "severity": "error", "file": rel_path, "line": line_number_at(clean, base_offset + lbl_m.start()), "message": f"\\label{{{lbl_m.group(1)}}} appears BEFORE \\caption inside {float_kind}."})
            if float_kind.startswith("table") and cap_m:
                tab_m = re.search(r"\\begin\{(?:tabular|tabularx|tabular\*|longtable)\}", body)
                if tab_m and cap_m.start() > tab_m.start():
                    findings.append({"id": "LINT-TABLE-CAPTION-BOTTOM", "severity": "warning", "file": rel_path, "line": line_number_at(clean, base_offset + cap_m.start()), "message": "Table \\caption appears below tabular; place table captions ABOVE tabular."})

    # Tabular booktabs & column-count mismatch check
    tab_pat = re.compile(r"\\begin\{(tabular|tabularx|longtable)\}(?:\[[^\]]*\])?(?:\{[^}]*\})?(\{[^}]*\})(.*?)\\end\{\1\}", re.S)
    for tm in tab_pat.finditer(clean):
        env_kind, col_arg, tbody = tm.group(1), tm.group(2), tm.group(3)
        if env_kind == "tabularx":
            # tabularx has {width}{colspec}
            m_two = re.match(r"\\begin\{tabularx\}(?:\{[^}]*\})\s*\{([^}]*)\}", tm.group(0))
            spec_str = m_two.group(1) if m_two else col_arg[1:-1]
        else:
            spec_str = col_arg[1:-1]
        if r"\hline" in tbody and not any(bt in tbody for bt in (r"\toprule", r"\midrule", r"\bottomrule")):
            findings.append({"id": "LINT-NO-BOOKTABS", "severity": "info", "file": rel_path, "line": line_number_at(clean, tm.start()), "message": "Table uses raw \\hline rules without booktabs (\\toprule/\\midrule/\\bottomrule)."})
        expected_cols = _count_tabular_spec_cols(spec_str)
        if expected_cols:
            row_start = tm.start(3)
            for rm in re.finditer(r"([^\n]*?\\\\)", tbody):
                row_txt = rm.group(1).strip()
                if row_txt.startswith("\\end"):
                    continue
                cnt = _count_row_cells(row_txt)
                if cnt > expected_cols:
                    findings.append({"id": "LINT-TABULAR-COL-MISMATCH", "severity": "error", "file": rel_path, "line": line_number_at(clean, row_start + rm.start()), "message": f"Tabular row has {cnt} cells but column spec declares {expected_cols} columns ('{spec_str}'); will trigger 'Extra alignment tab'."})

    # Unescaped '_' outside math/URL/path/label commands in document body
    m_doc = re.search(r"\\begin\{document\}(.*?)(?:\\end\{document\}|\Z)", clean, re.S)
    if m_doc:
        body_str = m_doc.group(1)
        body_offset = m_doc.start(1)
        chars = list(body_str)
        for pat_mask in (INLINE_MATH_OR_DISPLAY_RE, PATH_ARG_CMD_RE, re.compile(r"\\begin\{(?:equation|align|gather|multline|flalign|alignat|eqnarray)\*?\}.*?\\end\{(?:equation|align|gather|multline|flalign|alignat|eqnarray)\*?\}", re.S)):
            for mm in pat_mask.finditer(body_str):
                for idx in range(mm.start(), mm.end()):
                    if chars[idx] != "\n":
                        chars[idx] = " "
        masked_body = "".join(chars)
        for um in re.finditer(r"(?<!\\)_", masked_body):
            findings.append({"id": "LINT-UNESCAPED-UNDERSCORE", "severity": "error", "file": rel_path, "line": line_number_at(clean, body_offset + um.start()), "message": "Unescaped '_' in text mode triggers 'Missing $ inserted'; escape as '\\_' or wrap in math/\\texttt{\\_}."})

    if recommended_engine == "pdflatex":
        for idx, line_str in enumerate(clean.splitlines(), start=1):
            bad_chars = re.findall(r"[≤≥±×÷≈≠αβγδεζηθλμπρστφψωΔΣΩ°]", line_str)
            if bad_chars:
                findings.append({"id": "LINT-UNICODE-IN-PDFLATEX", "severity": "warning", "file": rel_path, "line": idx, "message": f"Raw Unicode math/symbol characters ({''.join(sorted(set(bad_chars)))}) detected with pdflatex."})

    return findings


def parse_bib_file(rel_path: str, raw_text: str) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse .bib database entries with balanced brace/quote tokenizer and syntax/field defect detection."""
    entries: Dict[str, Dict[str, Any]] = {}
    findings: List[Dict[str, Any]] = []
    n = len(raw_text)
    i = 0

    while i < n:
        while i < n and raw_text[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        if raw_text[i] == "%":
            nl = raw_text.find("\n", i)
            i = (nl + 1) if nl != -1 else n
            continue
        if raw_text[i] != "@":
            nxt_at = raw_text.find("@", i)
            nxt_nl = raw_text.find("\n", i)
            if nxt_at == -1:
                break
            i = (nxt_nl + 1) if (nxt_nl != -1 and nxt_nl < nxt_at) else nxt_at
            continue

        at = i
        m = re.match(r"@([A-Za-z]+)\s*([({])", raw_text[at:])
        if not m:
            i = at + 1
            continue
        etype, opener = m.group(1).lower(), m.group(2)
        start_line = line_number_at(raw_text, at)
        depth, in_quote, j = 1, False, at + m.end()

        while j < n and depth > 0:
            ch = raw_text[j]
            if ch == "\\" and j + 1 < n:
                j += 2
                continue
            if ch == '"' and depth == 1:
                in_quote = not in_quote
            elif not in_quote:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth = (depth - 1) if opener == "{" else max(1, depth - 1)
                elif opener == "(" and ch == ")" and depth == 1:
                    depth = 0
            j += 1

        if etype in {"comment", "preamble", "string"}:
            i = j
            continue
        if depth != 0:
            findings.append({"id": "BIB-UNCLOSED-ENTRY", "severity": "error", "file": rel_path, "line": start_line, "message": f"BibTeX @{etype} entry at line {start_line} has unclosed braces/delimiters."})
            m_next = re.search(r"\n\s*@[A-Za-z]+\s*[({]", raw_text[at + m.end() :])
            if m_next:
                i = at + m.end() + m_next.start() + 1
                continue
            break

        body = raw_text[at + m.end() : j - 1]
        comma_idx = body.find(",")
        if comma_idx == -1:
            findings.append({"id": "BIB-MISSING-COMMA", "severity": "error", "file": rel_path, "line": start_line, "message": f"BibTeX @{etype} entry at line {start_line} missing comma after citation key."})
            i = j
            continue
        key = body[:comma_idx].strip()
        if not key:
            findings.append({"id": "BIB-EMPTY-KEY", "severity": "error", "file": rel_path, "line": start_line, "message": f"BibTeX @{etype} entry at line {start_line} has an empty citation key."})
            i = j
            continue

        fields_text = body[comma_idx + 1 :]
        fields: Dict[str, str] = {}
        k, fn_len, expect_comma = 0, len(fields_text), False
        while k < fn_len:
            while k < fn_len and fields_text[k] in " \t\r\n":
                k += 1
            if k >= fn_len:
                break
            if fields_text[k] == ",":
                expect_comma = False
                k += 1
                continue
            if expect_comma:
                findings.append({"id": "BIB-MISSING-COMMA", "severity": "error", "file": rel_path, "line": start_line + line_number_at(fields_text, k) - 1, "message": f"Missing comma before BibTeX field in entry '{key}'."})
                expect_comma = False
            mf = re.match(r"([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*", fields_text[k:])
            if not mf:
                nxt_nl = fields_text.find("\n", k)
                k = (nxt_nl + 1) if nxt_nl != -1 else fn_len
                continue
            fstart = k
            fname = mf.group(1).lower()
            k += mf.end()
            val_parts: List[str] = []
            while k < fn_len:
                while k < fn_len and fields_text[k] in " \t\r\n":
                    k += 1
                if k >= fn_len:
                    break
                ch = fields_text[k]
                if ch == "{":
                    vseg, nxt = extract_braced_arg(fields_text, k)
                    val_parts.append(vseg)
                    k = nxt if nxt != -1 else fn_len
                elif ch == '"':
                    endq = k + 1
                    while endq < fn_len:
                        if fields_text[endq] == "\\" and endq + 1 < fn_len:
                            endq += 2
                            continue
                        if fields_text[endq] == '"':
                            break
                        endq += 1
                    val_parts.append(fields_text[k + 1 : endq])
                    k = endq + 1 if endq < fn_len else fn_len
                else:
                    mtok = re.match(r"([A-Za-z0-9_.\-:]+)", fields_text[k:])
                    if mtok:
                        val_parts.append(mtok.group(1))
                        k += mtok.end()
                    else:
                        break
                while k < fn_len and fields_text[k] in " \t\r\n":
                    k += 1
                if k < fn_len and fields_text[k] == "#":
                    k += 1
                else:
                    break
            raw_val = "".join(val_parts)
            fields[fname] = " ".join(raw_val.split())
            expect_comma = True
            if fname not in URL_LIKE_BIB_FIELDS and re.search(r"(?<!\\)[&%]", raw_val):
                findings.append({"id": "BIB-UNESCAPED-CHAR", "severity": "warning", "file": rel_path, "line": start_line + line_number_at(fields_text, fstart) - 1, "message": f"Unescaped '&' or '%' in BibTeX field '{fname}' of key '{key}'."})

        req_f = BIB_REQUIRED_FIELDS.get(etype, ())
        missing_req = [rf for rf in req_f if not fields.get(rf)]
        if missing_req:
            findings.append({"id": "BIB-MISSING-REQUIRED-FIELD", "severity": "warning", "file": rel_path, "line": start_line, "message": f"BibTeX @{etype} entry '{key}' missing recommended field(s): {', '.join(missing_req)}."})

        if key in entries:
            findings.append({"id": "BIB-DUPLICATE-KEY", "severity": "error", "file": rel_path, "line": start_line, "message": f"Duplicate BibTeX key '{key}' (first at {entries[key]['file']}:{entries[key]['line']})."})
        else:
            entries[key] = {"key": key, "type": etype, "file": rel_path, "line": start_line, "fields": fields}
        i = j

    return entries, findings


def audit_tex_tree(
    root_dir: Path,
    skill_dir: Path,
    env_info: Optional[Dict[str, Any]] = None,
    target_templates: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Audit ALL .tex and .bib files under root_dir (used by BOTH prepare_and_audit and verify_and_assemble)."""
    conflict_rules = load_json_safe(skill_dir / "resources" / "package_conflicts.json", default={}) or {}
    template_profiles = load_json_safe(skill_dir / "resources" / "template_profiles.json", default={}) or {}

    tex_paths = [p for p in sorted(root_dir.rglob("*.tex")) + sorted(root_dir.rglob("*.ltx")) if not any(part.startswith("_fallback") or part in SKIP_DIRS for part in p.parts)]
    bib_paths = [p for p in sorted(root_dir.rglob("*.bib")) if not any(part.startswith("_fallback") or part in SKIP_DIRS for part in p.parts)]
    local_cls = {p.name for p in root_dir.rglob("*.cls") if not any(part.startswith("_fallback") or part in SKIP_DIRS for part in p.parts)}

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
    tex_texts: Dict[str, str] = {}

    for bp in bib_paths:
        txt, err = read_text_safe(bp)
        if err:
            continue
        rel_b = posix_rel(bp, root_dir)
        entries, bf = parse_bib_file(rel_b, txt)
        findings.extend(bf)
        for k, ent in entries.items():
            if k in bib_entries:
                findings.append({"id": "BIB-DUPLICATE-KEY", "severity": "error", "file": ent["file"], "line": ent["line"], "message": f"Duplicate BibTeX key '{k}' across files (also in {bib_entries[k]['file']}:{bib_entries[k]['line']})."})
            else:
                bib_entries[k] = ent

    for tp in tex_paths:
        txt, err = read_text_safe(tp)
        if err:
            continue
        rel = posix_rel(tp, root_dir)
        tex_texts[rel] = txt
        if has_cjk(txt):
            tex_contains_cjk = True
        for dc in extract_tex_commands(txt, ("documentclass",)):
            doc_classes.append({"class": dc["arg"], "options": dc["opt"], "file": rel, "line": dc["line"]})
        for pk in extract_tex_commands(txt, ("usepackage", "RequirePackage")):
            for item in pk["arg"].split(","):
                if item.strip():
                    packages_used.append({"package": item.strip(), "options": pk["opt"], "file": rel, "line": pk["line"]})
        for gp in extract_tex_commands(txt, ("graphicspath",)):
            gp_in = gp["arg"]
            ig_i = 0
            while ig_i < len(gp_in):
                if gp_in[ig_i] == "{":
                    sub, nxt_g = extract_braced_arg(gp_in, ig_i)
                    if sub.strip() and sub.strip() not in graphics_paths:
                        graphics_paths.append(sub.strip())
                    ig_i = nxt_g if nxt_g != -1 else len(gp_in)
                else:
                    ig_i += 1

    pkg_names_lower = {p["package"].lower(): p for p in packages_used}
    cls_names = [d["class"] for d in doc_classes]
    xelatex_pkgs = {x.lower() for x in conflict_rules.get("engine_requirements", {}).get("xelatex_or_lualatex_required", [])}
    recommended_engine = "xelatex" if (tex_contains_cjk or any(c.startswith("ctex") for c in cls_names) or any(p in xelatex_pkgs for p in pkg_names_lower)) else "pdflatex"

    for tp in tex_paths:
        rel = posix_rel(tp, root_dir)
        if rel not in tex_texts:
            continue
        txt = tex_texts[rel]
        findings.extend(check_brace_and_env_balance(rel, txt))
        findings.extend(check_tables_math_and_antipatterns(rel, txt, conflict_rules, recommended_engine))

        children: List[str] = []
        for inc in extract_tex_commands(txt, ("input", "include", "subfile")):
            if inc["arg"]:
                resolved = resolve_tex_target(inc["arg"], tp, root_dir)
                if resolved is None:
                    findings.append({"id": "LINT-MISSING-INCLUDE", "severity": "error", "file": rel, "line": inc["line"], "message": f"Included file '{inc['arg']}' not found under {root_dir.name}/."})
                else:
                    children.append(posix_rel(resolved, root_dir))
        include_graph[rel] = children

        for lb in extract_tex_commands(txt, ("label",)):
            if lb["arg"]:
                all_labels.setdefault(lb["arg"], []).append({"file": rel, "line": lb["line"]})
        for rf in extract_tex_commands(txt, REF_CMDS):
            for rk in rf["arg"].split(","):
                if rk.strip():
                    all_refs.append({"key": rk.strip(), "cmd": rf["command"], "file": rel, "line": rf["line"]})
        for ct in extract_tex_commands(txt, CITE_CMDS):
            for ck in ct["arg"].split(","):
                if ck.strip():
                    all_cites.append({"key": ck.strip(), "cmd": ct["command"], "file": rel, "line": ct["line"]})
        for br in extract_tex_commands(txt, ("bibliography", "addbibresource")):
            bib_resources.extend(it.strip() for it in br["arg"].split(",") if it.strip())
        for bs in extract_tex_commands(txt, ("bibliographystyle",)):
            if bs["arg"]:
                bib_styles.append(bs["arg"])
        for ig in extract_tex_commands(txt, ("includegraphics",)):
            if ig["arg"] and resolve_image_target(ig["arg"], tp, root_dir, graphics_paths) is None:
                findings.append({"id": "LINT-MISSING-FIGURE", "severity": "error", "file": rel, "line": ig["line"], "message": f"Figure asset '{ig['arg']}' referenced in \\includegraphics was not found."})

    # Include cycle DFS
    visited, stack_set = set(), set()

    def _dfs(node: str) -> None:
        visited.add(node)
        stack_set.add(node)
        for nxt in include_graph.get(node, []):
            if nxt in stack_set:
                findings.append({"id": "LINT-INCLUDE-CYCLE", "severity": "error", "file": node, "line": 1, "message": f"Circular \\input/\\include cycle detected: {node} -> {nxt}."})
            elif nxt not in visited:
                _dfs(nxt)
        stack_set.remove(node)

    for node in include_graph:
        if node not in visited:
            _dfs(node)

    combined_clean_tex = "\n".join(sanitize_tex_for_scan(t) for t in tex_texts.values())
    has_engine_guard = bool(re.search(r"\\if(?:PDFTeX|pdfTeX|xetex|XeTeX|luatex|LuaTeX|tutex)\b", combined_clean_tex))
    for pair in conflict_rules.get("incompatible_pairs", []):
        pa, pb = pair["pkg_a"].lower(), pair["pkg_b"].lower()
        if pa in pkg_names_lower and pb in pkg_names_lower:
            if {pa, pb} == {"inputenc", "fontspec"} and has_engine_guard:
                continue
            findings.append({"id": "LINT-PKG-CONFLICT", "severity": "error", "file": pkg_names_lower[pb]["file"], "line": pkg_names_lower[pb]["line"], "message": f"Incompatible packages '{pair['pkg_a']}' and '{pair['pkg_b']}': {pair['reason']} Fix: {pair['resolution']}"})

    pkgs_by_file: Dict[str, List[Dict[str, Any]]] = {}
    for p in packages_used:
        pkgs_by_file.setdefault(p["file"], []).append(p)
    for frel, plist in pkgs_by_file.items():
        idx_map = {p["package"].lower(): idx for idx, p in enumerate(plist)}
        for rule in conflict_rules.get("load_order_rules", []):
            fk, sk = rule["first"].lower(), rule["second"].lower()
            if fk in idx_map and sk in idx_map and idx_map[sk] < idx_map[fk]:
                findings.append({"id": "LINT-PKG-ORDER", "severity": "warning", "file": frel, "line": plist[idx_map[sk]]["line"], "message": f"Package '{rule['second']}' loaded before '{rule['first']}': {rule['reason']}"})

    env = env_info or {}
    kpse_available = bool(env.get("tools", {}).get("kpsewhich"))
    for dc in doc_classes:
        cls_file = dc["class"] + ".cls"
        if cls_file not in local_cls:
            found = env.get("kpse_checks", {}).get(cls_file)
            if found is None and kpse_available:
                res = run_cmd(["kpsewhich", cls_file], timeout=10)
                out_p = (res.get("stdout") or "").strip()
                found = out_p if (res["returncode"] == 0 and out_p) else None
            if kpse_available and not found:
                findings.append({"id": "LINT-MISSING-CLS", "severity": "error", "file": dc["file"], "line": dc["line"], "message": f"Document class '{cls_file}' not found in /input or kpsewhich TEXMF."})

    for lkey, locs in all_labels.items():
        if len(locs) > 1:
            findings.append({"id": "LINT-DUPLICATE-LABEL", "severity": "warning", "file": locs[1]["file"], "line": locs[1]["line"], "message": f"Duplicate \\label{{{lkey}}} (first at {locs[0]['file']}:{locs[0]['line']})."})
    for rf in all_refs:
        if rf["key"] not in all_labels:
            findings.append({"id": "LINT-UNRESOLVED-REF", "severity": "error", "file": rf["file"], "line": rf["line"], "message": f"\\{rf['cmd']}{{{rf['key']}}} has no matching \\label{{{rf['key']}}} in project."})

    inline_bib_keys = {bm["arg"] for txt in tex_texts.values() for bm in extract_tex_commands(txt, ("bibitem",)) if bm["arg"]}
    all_known_bib_keys = set(bib_entries.keys()) | inline_bib_keys
    cited_keys: Set[str] = set()
    for ct in all_cites:
        ck = ct["key"]
        if ct["cmd"] == "nocite" and ck == "*":
            cited_keys.update(bib_entries.keys())
            continue
        cited_keys.add(ck)
        if (all_known_bib_keys or bib_paths or bib_resources) and ck not in all_known_bib_keys:
            findings.append({"id": "LINT-MISSING-CITE-KEY", "severity": "error", "file": ct["file"], "line": ct["line"], "message": f"Citation key '{ck}' in \\{ct['cmd']} is missing from project .bib / \\bibitem entries."})

    if "biblatex" in pkg_names_lower:
        bl_opt = str(pkg_names_lower["biblatex"].get("options") or "").lower()
        recommended_bib = "bibtex" if "backend=bibtex" in bl_opt else "biber"
    elif bib_resources or bib_styles or bib_paths:
        recommended_bib = "bibtex"
    else:
        recommended_bib = "none"

    profiles = template_profiles.get("profiles", {})
    template_checks: List[Dict[str, Any]] = []
    for tpl_name in set(cls_names) | set(target_templates or []):
        prof = profiles.get(tpl_name)
        if prof and combined_clean_tex:
            reqs = [{"requirement": req, "present_in_tex": check_template_requirement(req, combined_clean_tex)} for req in prof.get("required_elements", [])]
            template_checks.append({"template": tpl_name, "checks": reqs, "pitfalls": prof.get("pitfalls", [])})

    return {
        "recommended_engine": recommended_engine,
        "recommended_bib_tool": recommended_bib,
        "tex_contains_cjk": tex_contains_cjk,
        "document_classes": doc_classes,
        "packages": packages_used,
        "include_graph": include_graph,
        "bib_resources": bib_resources,
        "bib_styles": bib_styles,
        "bib_entries": bib_entries,
        "inline_bib_keys": sorted(inline_bib_keys),
        "bib_keys_count": len(all_known_bib_keys),
        "cited_keys_count": len(cited_keys),
        "orphan_bib_keys": sorted(k for k in bib_entries if k not in cited_keys),
        "labels_count": len(all_labels),
        "findings": findings,
        "error_count": sum(1 for x in findings if x["severity"] == "error"),
        "warning_count": sum(1 for x in findings if x["severity"] == "warning"),
        "template_checks": template_checks,
        "tex_texts": tex_texts,
    }


def _csv_to_booktabs_snippet(rel: str, rows: List[List[str]]) -> Optional[str]:
    """Generate a ready-to-paste booktabs table snippet from CSV/TSV rows (zero numeric drift)."""
    clean_rows = [r for r in rows if any(c.strip() for c in r)]
    if len(clean_rows) < 2:
        return None
    ncols = max(len(r) for r in clean_rows)
    col_spec = "l" + ("c" * max(0, ncols - 1))
    esc = lambda s: s.strip().replace("&", "\\&").replace("%", "\\%").replace("_", "\\_")
    lines = [
        f"% [SRC: {rel}:R1-R{min(len(clean_rows), 25)}]",
        "\\begin{table}[htbp]",
        "\\centering",
        f"\\caption{{Summary from {esc(Path(rel).name)}.}}",
        f"\\label{{tab:{re.sub(r'[^a-z0-9]+', '_', Path(rel).stem.lower()).strip('_') or 'data'}}}",
        f"\\begin{{tabular}}{{{col_spec}}}",
        "\\toprule",
        " & ".join(esc(clean_rows[0][i] if i < len(clean_rows[0]) else "") for i in range(ncols)) + " \\\\",
        "\\midrule",
    ]
    for r in clean_rows[1:25]:
        lines.append(" & ".join(esc(r[i] if i < len(r) else "") for i in range(ncols)) + " \\\\")
    lines.extend(["\\bottomrule", "\\end{tabular}", "\\end{table}"])
    return "\n".join(lines)


def extract_non_tex_facts(files: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """Extract grounded text snippets, numbers, and booktabs table drafts from inputs."""
    facts: List[Dict[str, Any]] = []
    available_norm_numbers: Set[str] = set()
    table_drafts: List[str] = []

    non_tex_cnt = sum(1 for f in files if f.get("readable") and f.get("category") in {"draft_or_doc", "tabular_data", "structured_data", "word_doc", "pdf_doc"})
    per_file_cap = max(30, 240 // max(1, non_tex_cnt))

    for f in files:
        if not f["readable"]:
            continue
        cat, rel, fpath = f["category"], f["rel_path"], Path(f["abs_path"])
        if cat in {"tex_source", "bib_database"}:
            txt, _ = read_text_safe(fpath)
            for tok in extract_numeric_tokens(txt):
                norm = normalize_num_str(tok)
                if norm:
                    available_norm_numbers.add(norm)
            continue
        if cat not in {"draft_or_doc", "tabular_data", "structured_data", "word_doc", "pdf_doc"}:
            continue

        file_facts: List[Dict[str, Any]] = []
        if cat in {"draft_or_doc", "structured_data"}:
            txt, _ = read_text_safe(fpath)
            for tok in extract_numeric_tokens(txt):
                norm = normalize_num_str(tok)
                if norm:
                    available_norm_numbers.add(norm)
            for idx, line in enumerate(txt.splitlines()[:300], start=1):
                s = line.strip()
                if s and (s.startswith("#") or extract_numeric_tokens(s) or "|" in s or re.search(r"\[[0-9,\s-]+\]|doi:|10\.\d{4,}", s, re.I)):
                    file_facts.append({"file": rel, "locator": f"{rel}:L{idx}", "kind": "line", "text": s[:240]})
        elif cat == "tabular_data":
            txt, _ = read_text_safe(fpath)
            rows = list(csv.reader(txt.splitlines(), delimiter="\t" if f["ext"] == ".tsv" else ","))
            for tok in extract_numeric_tokens(txt):
                norm = normalize_num_str(tok)
                if norm:
                    available_norm_numbers.add(norm)
            for idx, row in enumerate(rows[:60], start=1):
                if any(c.strip() for c in row):
                    file_facts.append({"file": rel, "locator": f"{rel}:R{idx}", "kind": "table_row", "text": " | ".join(c.strip() for c in row)[:240]})
            snip = _csv_to_booktabs_snippet(rel, rows)
            if snip:
                table_drafts.append(snip)
        elif cat == "word_doc":
            try:
                with zipfile.ZipFile(fpath) as zf:
                    if "word/document.xml" in zf.namelist():
                        root = ET.fromstring(zf.read("word/document.xml"))
                        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                        body = root.find("w:body", ns)
                        p_idx, t_idx = 0, 0
                        for child in (list(body) if body is not None else list(root.findall(".//w:p", ns))):
                            tag = child.tag.split("}")[-1]
                            if tag == "p":
                                p_idx += 1
                                text = "".join(t.text or "" for t in child.findall(".//w:t", ns)).strip()
                                if text:
                                    for tok in extract_numeric_tokens(text):
                                        n = normalize_num_str(tok)
                                        if n:
                                            available_norm_numbers.add(n)
                                    if p_idx <= 120:
                                        file_facts.append({"file": rel, "locator": f"{rel}:P{p_idx}", "kind": "docx_paragraph", "text": text[:240]})
                            elif tag == "tbl":
                                t_idx += 1
                                for r_i, tr in enumerate(child.findall(".//w:tr", ns), start=1):
                                    row_txt = " | ".join("".join(t.text or "" for t in tc.findall(".//w:t", ns)).strip() for tc in tr.findall("./w:tc", ns)).strip()
                                    if row_txt:
                                        for tok in extract_numeric_tokens(row_txt):
                                            n = normalize_num_str(tok)
                                            if n:
                                                available_norm_numbers.add(n)
                                        file_facts.append({"file": rel, "locator": f"{rel}:T{t_idx}_R{r_i}", "kind": "docx_table_row", "text": row_txt[:240]})
            except Exception as exc:
                file_facts.append({"file": rel, "locator": rel, "kind": "parse_error", "text": f"Failed to parse DOCX: {exc}"})
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
                doc.close()
            except Exception:
                if which_tool("pdftotext"):
                    res = run_cmd(["pdftotext", str(fpath), "-"], timeout=20)
                    if res["returncode"] == 0 and res["stdout"].strip():
                        extracted_pages = [(pno, c) for pno, c in enumerate(res["stdout"].split("\f"), start=1) if c.strip()]
            if not extracted_pages:
                file_facts.append({"file": rel, "locator": rel, "kind": "pdf_unextracted", "text": "No text extracted from PDF (needs OCR or PyMuPDF/pdftotext)."})
            else:
                for pno, ptxt in extracted_pages:
                    for tok in extract_numeric_tokens(ptxt):
                        n = normalize_num_str(tok)
                        if n:
                            available_norm_numbers.add(n)
                    for lno, line in enumerate(ptxt.splitlines()[:80], start=1):
                        s = line.strip()
                        if s and (lno <= 8 or extract_numeric_tokens(s)):
                            file_facts.append({"file": rel, "locator": f"{rel}:p{pno}_L{lno}", "kind": "pdf_line", "text": s[:240]})
        facts.extend(file_facts[:per_file_cap])

    return facts[:260], sorted(available_norm_numbers), table_drafts[:8]


def _inventory_inputs(input_dir: Path) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    files, warnings, tex_candidates = [], [], []
    if not input_dir.exists():
        warnings.append(f"input_dir_missing: {input_dir}")
        return files, warnings, tex_candidates
    for root, dirs, filenames in os.walk(input_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for fname in sorted(filenames):
            fpath = Path(root) / fname
            rel = posix_rel(fpath, input_dir)
            if rel in CONTROL_FILES:
                continue
            ext = fpath.suffix.lower()
            category = CATEGORY_BY_EXT.get(ext, "other")
            try:
                size = fpath.stat().st_size
            except OSError as exc:
                warnings.append(f"stat_failed: {rel} ({exc})")
                size = -1
            entry: Dict[str, Any] = {"rel_path": rel, "abs_path": str(fpath.resolve()), "ext": ext, "category": category, "size_bytes": size, "readable": True, "read_error": None, "contains_cjk": False, "is_entry_tex": False}
            if size == 0:
                warnings.append(f"empty_file: {rel}")
            if category in {"tex_source", "bib_database", "draft_or_doc", "tabular_data", "structured_data", "tex_log", "bib_log", "tex_class_or_style"}:
                text, err = read_text_safe(fpath)
                if err:
                    entry["readable"], entry["read_error"] = False, err
                    warnings.append(f"read_error: {rel} ({err})")
                else:
                    entry["contains_cjk"] = has_cjk(text)
                    entry["line_count"] = len(text.splitlines())
                    if category == "tex_source":
                        clean_tex = sanitize_tex_for_scan(text)
                        entry["has_documentclass"] = bool(re.search(r"^\s*\\documentclass\b", clean_tex, re.M))
                        entry["has_begin_document"] = bool(re.search(r"\\begin\{document\}", clean_tex))
                        if entry["has_documentclass"] and entry["has_begin_document"]:
                            entry["is_entry_tex"] = True
                            tex_candidates.append(rel)
            elif ext in {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".eps"}:
                try:
                    with fpath.open("rb") as fh:
                        head = fh.read(16)
                    if ext == ".pdf" and not head.startswith(b"%PDF"):
                        entry["readable"], entry["read_error"] = False, "invalid_pdf_header"
                        warnings.append(f"invalid_pdf_header: {rel}")
                except OSError as exc:
                    entry["readable"], entry["read_error"] = False, f"os_error: {exc}"
                    warnings.append(f"os_error: {rel}")
            files.append(entry)
    return files, warnings, tex_candidates


def _infer_signals(task_text: str, case_json: Dict[str, Any], files: List[Dict[str, Any]]) -> Dict[str, Any]:
    combined = f"{task_text}\n{case_json.get('prompt', '')}\n{case_json.get('instruction', '')}\n{case_json.get('task', '')}".lower()
    has_tex = any(f["category"] == "tex_source" for f in files)
    has_bib = any(f["category"] == "bib_database" for f in files)
    has_draft = any(f["category"] in {"draft_or_doc", "word_doc", "pdf_doc", "tabular_data"} for f in files)
    modes: List[str] = []
    for mode, keys in (
        ("template_migration", ("template", "migrate", "ieee", "acm", "elsevier", "elsarticle", "llncs", "springer", "neurips", "icml", "模板", "迁移", "期刊", "会议")),
        ("compile_and_repair", ("compile", "error", "fix", "debug", "repair", "broken", "编译", "报错", "修复", "排错")),
        ("citation_bib_alignment", ("bib", "citation", "reference", "cite", "引用", "参考文献")),
        ("formula_table_refinement", ("formula", "equation", "table", "tabular", "booktabs", "float", "公式", "表格", "三线表")),
        ("bilingual_latex_writing", ("bilingual", "chinese", "cjk", "翻译", "双语", "中文")),
        ("draft_to_latex", ("draft", "write", "author", "convert", "from markdown", "撰写", "生成", "改写")),
    ):
        if any(k in combined for k in keys):
            modes.append(mode)
    if not modes:
        modes.append("compile_and_repair" if has_tex else "draft_to_latex")

    cjk_in_files = any(f.get("contains_cjk") for f in files)
    explicit_zh = any(k in combined for k in ("中文论文", "中文版", "用中文撰写", "中文排版", "chinese paper", "ctexart", "ctexbook", "xecjk"))
    explicit_en = any(k in combined for k in ("english", "en-us", "英文论文", "英文版", "ieeetran", "acmart", "elsarticle", "llncs", "neurips", "icml"))
    if any(k in combined for k in ("bilingual", "双语", "中英对照", "中英文")):
        language = "bilingual"
    elif cjk_in_files or explicit_zh:
        language = "en" if (explicit_en and not cjk_in_files and not explicit_zh) else "zh"
    elif has_cjk(combined) and not has_tex and not explicit_en:
        language = "zh"
    else:
        language = "en"

    templates = [tpl for tpl in ("IEEEtran", "acmart", "elsarticle", "llncs", "sn-jnl", "neurips", "icml", "ctexart", "ctexbook", "revtex4-2", "beamer") if re.search(rf"(?<![A-Za-z0-9]){re.escape(tpl)}(?![A-Za-z0-9])", combined, re.I)]
    if re.search(r"\\documentclass(?:\[[^\]]*\])?\{article\}|\barticle\s+(?:class|template)\b", combined, re.I):
        templates.append("article")
    for f in files:
        if f["category"] == "tex_class_or_style":
            stem = Path(f["rel_path"]).stem
            if stem not in templates:
                templates.append(stem)

    return {"primary_mode": modes[0], "detected_modes": modes, "language": language, "target_templates": templates, "has_tex_inputs": has_tex, "has_bib_inputs": has_bib, "has_non_tex_sources": has_draft}


def _detect_missing_refs(task_text: str, case_json: Dict[str, Any], input_dir: Path, files: List[Dict[str, Any]]) -> List[str]:
    existing = {f["rel_path"] for f in files} | {Path(f["rel_path"]).name for f in files}
    missing: List[str] = []
    for key in ("files", "inputs", "artifacts", "materials", "attachments"):
        val = case_json.get(key)
        if isinstance(val, list):
            for item in val:
                raw_cand = (item.get("path") or item.get("file") or item.get("rel_path")) if isinstance(item, dict) else (item if isinstance(item, str) else None)
                if not isinstance(raw_cand, str) or not raw_cand.strip():
                    continue
                cand = raw_cand.strip().lstrip("./")
                if Path(cand).name not in EXPECTED_OUTPUT_FILES and not (input_dir / cand).exists() and cand not in existing and cand not in missing:
                    missing.append(cand)
    out_cue_re = re.compile(r"(?:输出|生成|交付|创建|保存为|写入|output|generate|deliver|create|produce|write\s+to)\s*[:：]?\s*$", re.I)
    for m in re.finditer(r"`([A-Za-z0-9_./-]+\.(?:tex|bib|cls|sty|bst|md|csv|tsv|pdf|docx|png|jpg|eps|json))`", task_text):
        ref = m.group(1).lstrip("./")
        base = Path(ref).name
        if base in EXPECTED_OUTPUT_FILES or ref.startswith(("app/output/", "/app/output/", "app/work/", "/app/work/", "output/")) or out_cue_re.search(task_text[max(0, m.start() - 32) : m.start()]):
            continue
        if not (input_dir / ref).exists() and base not in existing and ref not in missing:
            missing.append(ref)
    return missing


def inspect_case(input_dir: Path, task_path_override: Optional[Path] = None) -> Dict[str, Any]:
    """Inspect Harbor /input case.json, task.md, files, and live TeX/font environment."""
    case_path = input_dir / "case.json"
    case_json = load_json_safe(case_path, default={})
    if not isinstance(case_json, dict):
        case_json = {}

    task_text, task_source = "", None
    for tp in [p for p in [task_path_override, input_dir / "task.md", Path("task.md")] if p is not None]:
        if tp.exists():
            txt, err = read_text_safe(tp)
            if not err:
                task_text, task_source = txt, str(tp)
                break

    case_id = str(case_json.get("case_id") or case_json.get("id") or input_dir.name or "unknown_case")
    files, access_warnings, tex_candidates = _inventory_inputs(input_dir)
    inline_keys = [k for k in ("source_tex", "tex", "tex_source", "latex", "latex_source", "manuscript", "draft", "draft_text", "bib_text", "bibtex", "references") if isinstance(case_json.get(k), str) and case_json.get(k).strip()]

    return {
        "schema_version": "1.0",
        "input_dir": str(input_dir.resolve()),
        "case_id": case_id,
        "case_json_present": case_path.exists(),
        "case_json_raw": case_json,
        "task_md_path": task_source,
        "task_text_preview": task_text[:4000],
        "output_contract": parse_output_contract(case_json.get("output_contract")),
        "signals": _infer_signals(task_text, case_json, files),
        "inline_material_keys": inline_keys,
        "candidate_entry_tex": tex_candidates,
        "files": files,
        "stats": {
            "total_files": len(files),
            "readable_files": sum(1 for f in files if f["readable"]),
            "tex_files": sum(1 for f in files if f["category"] == "tex_source"),
            "bib_files": sum(1 for f in files if f["category"] == "bib_database"),
            "class_or_style_files": sum(1 for f in files if f["category"] == "tex_class_or_style"),
            "non_tex_sources": sum(1 for f in files if f["category"] in {"draft_or_doc", "word_doc", "pdf_doc", "tabular_data", "structured_data"}),
            "inline_material_count": len(inline_keys),
        },
        "access_warnings": access_warnings,
        "missing_referenced_files": _detect_missing_refs(task_text, case_json, input_dir, files),
        "environment": probe_tex_environment(),
    }


def _stage_sources(input_dir: Path, src_dir: Path, baseline_dir: Path, case_plan: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Copy /input tree + materialize inline case.json fields into src_dir & baseline_dir."""
    staged, skipped = [], []

    def _copy_to(dst_dir: Path, record: bool) -> None:
        ensure_dir(dst_dir)
        if not input_dir.is_dir():
            return
        for root, dirs, filenames in os.walk(input_dir, onerror=lambda e: skipped.append(f"walk_error: {e}") if record else None):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            rel_root = Path(root).relative_to(input_dir)
            for fname in sorted(filenames):
                rel = (rel_root / fname).as_posix()
                if rel in CONTROL_FILES:
                    continue
                dst_f = dst_dir / rel_root / fname
                try:
                    ensure_dir(dst_f.parent)
                    shutil.copy2(Path(root) / fname, dst_f)
                    if record:
                        staged.append(rel)
                except (OSError, shutil.Error) as exc:
                    if record:
                        skipped.append(f"stage_skipped: {rel} ({exc})")

    _copy_to(src_dir, True)
    _copy_to(baseline_dir, False)

    raw = case_plan.get("case_json_raw") or {}
    created: List[Dict[str, Any]] = []

    def _emit(rel_name: str, text: str, category: str, origin_key: str) -> None:
        for tr in (src_dir, baseline_dir):
            p = tr / rel_name
            ensure_dir(p.parent)
            p.write_text(text, encoding="utf-8")
        p_src = src_dir / rel_name
        ent: Dict[str, Any] = {
            "rel_path": rel_name, "abs_path": str(p_src.resolve()), "ext": p_src.suffix.lower(),
            "category": category, "size_bytes": len(text.encode("utf-8")), "readable": True,
            "read_error": None, "contains_cjk": has_cjk(text), "line_count": len(text.splitlines()),
            "is_entry_tex": False, "origin": f"case.json#{origin_key}",
        }
        if category == "tex_source":
            clean = sanitize_tex_for_scan(text)
            ent["has_documentclass"] = bool(re.search(r"^\s*\\documentclass\b", clean, re.M))
            ent["has_begin_document"] = bool(re.search(r"\\begin\{document\}", clean))
            ent["is_entry_tex"] = ent["has_documentclass"] and ent["has_begin_document"]
        created.append(ent)

    if not any(src_dir.rglob("*.tex")):
        for k in ("source_tex", "tex", "tex_source", "tex_content", "latex", "latex_source", "latex_content", "main_tex"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                _emit("main.tex", v, "tex_source", k)
                break
    if not any(src_dir.rglob("*.bib")):
        for k in ("bib_text", "bibtex", "bib_content", "bibtex_content"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                _emit("refs.bib", v, "bib_database", k)
                break
    if not any(f.get("category") in {"draft_or_doc", "word_doc", "pdf_doc"} for f in case_plan.get("files", [])):
        for k in ("manuscript", "draft", "draft_text"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                if ("\\documentclass" in v or "\\begin{document}" in v) and not any(src_dir.rglob("*.tex")):
                    _emit("main.tex", v, "tex_source", k)
                else:
                    _emit("draft_inline.md", v, "draft_or_doc", k)
                break
    ref_v = raw.get("references")
    if isinstance(ref_v, str) and ref_v.strip() and not any(src_dir.rglob("*.bib")):
        _emit("refs.bib" if re.search(r"@\w+\s*[({]", ref_v) else "references_inline.txt", ref_v, "bib_database" if re.search(r"@\w+\s*[({]", ref_v) else "draft_or_doc", "references")

    if created:
        existing_rels = {f["rel_path"] for f in case_plan.get("files", [])}
        for ent in created:
            staged.append(f"{ent['rel_path']} (from {ent['origin']})")
            if ent["rel_path"] not in existing_rels:
                case_plan.setdefault("files", []).append(ent)
            if ent.get("is_entry_tex") and ent["rel_path"] not in case_plan.get("candidate_entry_tex", []):
                case_plan.setdefault("candidate_entry_tex", []).append(ent["rel_path"])
        files_all = case_plan.get("files", [])
        case_plan["stats"] = {
            "total_files": len(files_all),
            "readable_files": sum(1 for f in files_all if f.get("readable")),
            "tex_files": sum(1 for f in files_all if f.get("category") == "tex_source"),
            "bib_files": sum(1 for f in files_all if f.get("category") == "bib_database"),
            "class_or_style_files": sum(1 for f in files_all if f.get("category") == "tex_class_or_style"),
            "non_tex_sources": sum(1 for f in files_all if f.get("category") in {"draft_or_doc", "word_doc", "pdf_doc", "tabular_data", "structured_data"}),
            "inline_material_count": len(case_plan.get("inline_material_keys", [])),
        }
    return staged, skipped


def apply_safe_fixes(src_dir: Path, case_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Apply deterministic, behavior-preserving mechanical fixes in /app/work/src and log every change."""
    records: List[Dict[str, Any]] = []
    env = case_plan.get("environment", {})
    cjk_fonts = env.get("cjk_fonts", [])
    best_cjk = pick_best_cjk_font(cjk_fonts)
    fandol_ok = fandol_is_available(cjk_fonts)

    for tp in sorted(src_dir.rglob("*.tex")) + sorted(src_dir.rglob("*.ltx")):
        if any(part.startswith("_fallback") or part in SKIP_DIRS for part in tp.parts):
            continue
        raw_bytes = tp.read_bytes()
        rel = posix_rel(tp, src_dir)
        txt, err = read_text_safe(tp)
        if err:
            continue
        orig_txt = txt

        # 1. Strip UTF-8 BOM
        if raw_bytes.startswith(b"\xef\xbb\xbf"):
            records.append({"file": rel, "locator": "L1", "change_type": "strip_utf8_bom", "description": "Stripped UTF-8 BOM that can trigger preamble errors.", "reason": "TeX expects BOM-free UTF-8."})

        # 2. Swap \label{...} placed directly before \caption{...} inside floats
        def _swap_lbl_cap(m: re.Match) -> str:
            return f"{m.group(2)}\n{m.group(1)}"

        txt_swapped, n_swap = re.subn(
            r"(\\label\{[^}]+\})\s*\n([ \t]*\\caption(?:\[[^\]]*\])?\{[^\n]*\})",
            _swap_lbl_cap,
            txt,
        )
        if n_swap:
            txt = txt_swapped
            records.append({"file": rel, "locator": "float_label_order", "change_type": "fix_label_caption_order", "description": f"Moved {n_swap} \\label{{}} after \\caption{{}}.", "reason": "\\label before \\caption binds to enclosing section instead of figure/table number."})

        # 3. Replace Windows/macOS-only CJK font names missing on Linux with verified fc-list font
        if best_cjk:
            cjk_set = set(cjk_fonts)

            def _rep_cjk_font(m: re.Match) -> str:
                fname = m.group(2).strip()
                if fname in WINDOWS_ONLY_CJK_FONTS and fname not in cjk_set:
                    return f"{m.group(1)}{best_cjk}}}"
                return m.group(0)

            txt_font, n_font = re.subn(r"(\\setCJK(?:main|sans|mono)font(?:\[[^\]]*\])?\{)([^}]+)\}", _rep_cjk_font, txt)
            if n_font and txt_font != txt:
                txt = txt_font
                records.append({"file": rel, "locator": "preamble", "change_type": "cjk_font_portability", "description": f"Replaced missing OS-specific CJK font with verified installed '{best_cjk}'.", "reason": "Prevent fontspec Font-not-found failure."})
            if not fandol_ok and "fontset=fandol" in txt and best_cjk:
                txt = re.sub(r"fontset=fandol", "fontset=none", txt)
                if "\\setCJKmainfont" not in txt:
                    txt = txt.replace("\\begin{document}", f"\\setCJKmainfont{{{best_cjk}}}\n\\begin{{document}}", 1)
                records.append({"file": rel, "locator": "preamble", "change_type": "cjk_fontset_fallback", "description": f"Switched unavailable fontset=fandol to fontset=none + \\setCJKmainfont{{{best_cjk}}}.", "reason": "Fandol OTF fonts absent in environment."})

        if txt != orig_txt or raw_bytes.startswith(b"\xef\xbb\xbf"):
            tp.write_text(txt, encoding="utf-8")

    # 4. Escape bare '&' and '%' in non-URL BibTeX fields
    for bp in sorted(src_dir.rglob("*.bib")):
        if any(part.startswith("_fallback") or part in SKIP_DIRS for part in bp.parts):
            continue
        rel_b = posix_rel(bp, src_dir)
        btxt, berr = read_text_safe(bp)
        if berr:
            continue

        def _esc_bib_field(m: re.Match) -> str:
            fname, sep, opener, val, closer = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            if fname.lower() in URL_LIKE_BIB_FIELDS:
                return m.group(0)
            new_val = re.sub(r"(?<!\\)([&%])", r"\\\1", val)
            return f"{fname}{sep}{opener}{new_val}{closer}"

        new_btxt = re.sub(r"(\b[A-Za-z_][A-Za-z0-9_-]*\b)(\s*=\s*)(\{)([^{}\n]*?)(\})", _esc_bib_field, btxt)
        if new_btxt != btxt:
            bp.write_text(new_btxt, encoding="utf-8")
            records.append({"file": rel_b, "locator": "fields", "change_type": "bib_escape_special_chars", "description": "Escaped bare '&' / '%' in non-URL BibTeX fields.", "reason": "Prevent TeX alignment tab and comment truncation errors."})

    return records


def render_digest_md(audit: Dict[str, Any], case_plan: Dict[str, Any], table_drafts: List[str], auto_fixes: List[Dict[str, Any]]) -> str:
    lines = [
        f"# LaTeX Project & Source Audit Digest — `{audit['case_id']}`",
        "",
        f"- **Task mode**: `{case_plan['signals']['primary_mode']}` | **Language**: `{case_plan['signals']['language']}`",
        f"- **Recommended engine**: `{audit['recommended_engine']}` | **Bibliography tool**: `{audit['recommended_bib_tool']}`",
        f"- **Entry `.tex` candidates**: {', '.join(case_plan.get('candidate_entry_tex') or ['(none yet)'])}",
        f"- **Available CJK system fonts**: {', '.join(case_plan['environment'].get('cjk_fonts', [])[:8]) or '(none reported by fc-list)'} | `fandol_available`: `{case_plan['environment'].get('fandol_available')}`",
        f"- **Static findings**: {audit['error_count']} errors, {audit['warning_count']} warnings",
        "",
    ]
    if auto_fixes:
        lines.append("## Safe Mechanical Pre-Fixes Applied in `/app/work/src`")
        for af in auto_fixes:
            lines.append(f"- `{af['file']}` [{af['change_type']}]: {af['description']}")
        lines.append("")
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
                lines.append(f"- [{'OK' if chk['present_in_tex'] else 'MISSING'}] `{chk['requirement']}`")
        lines.append("")
    if audit.get("source_facts"):
        lines.append("## Grounded Non-TeX Source Facts (Preserve Exact Numbers & Locators)")
        for sf in audit["source_facts"][:60]:
            lines.append(f"- `{sf['locator']}` ({sf['kind']}): {sf['text']}")
        lines.append("")
    if table_drafts:
        lines.append("## Auto-Generated Booktabs Drafts from CSV/TSV")
        for td in table_drafts:
            lines.extend(["```latex", td, "```", ""])
    return "\n".join(lines) + "\n"


def _write_partial_checkpoint(case_plan: Dict[str, Any], audit: Dict[str, Any], out_dir: Path) -> Path:
    ensure_dir(out_dir)
    auto_unc = [{"id": "UNC-ACCESS", "category": "input_access", "description": w, "impact": "Input file unreadable or empty."} for w in case_plan.get("access_warnings", [])]
    auto_unc.extend({"id": "UNC-MISSING-REF", "category": "missing_input", "description": f"Referenced file missing: {m}", "impact": "Cannot verify or include missing file."} for m in case_plan.get("missing_referenced_files", []))
    if not auto_unc:
        auto_unc.append({"id": "UNC-CHECKPOINT", "category": "in_progress", "description": "Early partial checkpoint saved before authoring/build loop completed.", "impact": "Pending final compilation and verification."})

    partial_res: Dict[str, Any] = {
        "case_id": case_plan["case_id"],
        "status": "partial",
        "checkpoint": "partial",
        "summary": f"Early checkpoint for case '{case_plan['case_id']}' ({case_plan['signals']['primary_mode']}); detected {audit.get('error_count', 0)} static errors and {audit.get('warning_count', 0)} warnings.",
        "primary_tex_path": (case_plan.get("candidate_entry_tex") or [""])[0],
        "output_files": [],
        "sources": [{"id": f"SRC-{i+1:02d}", "fact_or_target": sf["text"][:100], "source_locator": sf["locator"], "verification_status": "verified"} for i, sf in enumerate(audit.get("source_facts", [])[:20])],
        "change_records": [],
        "uncertainty": auto_unc,
        "author_confirmation_items": [],
        "limitations": [{"scenario": "Early partial checkpoint", "cause": "Authoring/compile verification loop has not run yet.", "scope": "Replace via verify_and_assemble.py once verification passes."}],
    }
    for fspec in case_plan.get("output_contract", {}).get("fields", []):
        fn = fspec["name"]
        if fn not in partial_res:
            et = fspec.get("expected_type", "any").lower()
            partial_res[fn] = [] if et in {"array", "list"} else ({} if et in {"object", "dict"} else (False if et in {"boolean", "bool"} else (0 if et in {"integer", "int", "number", "float"} else partial_res["summary"])))
    out_json = out_dir / "result.json"
    save_json(out_json, partial_res)
    return out_json


def prepare_and_audit(
    input_dir: Path,
    work_dir: Path,
    out_dir: Path,
    skill_dir: Path,
    do_safe_fixes: bool = False,
    task_md: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run full Step 1 pipeline in-process."""
    src_dir = ensure_dir(work_dir / "src")
    baseline_dir = ensure_dir(work_dir / "baseline_src")
    ensure_dir(work_dir / "build")
    ensure_dir(work_dir / "previews")
    ensure_dir(out_dir)

    case_plan = inspect_case(input_dir, task_md)
    staged, skipped = _stage_sources(input_dir, src_dir, baseline_dir, case_plan)
    if skipped:
        case_plan.setdefault("access_warnings", []).extend(skipped)
    case_plan["baseline_dir"] = str(baseline_dir.resolve())
    case_plan["staged_src_dir"] = str(src_dir.resolve())
    save_json(work_dir / "case_plan.json", case_plan)

    auto_fixes: List[Dict[str, Any]] = []
    if do_safe_fixes:
        auto_fixes = apply_safe_fixes(src_dir, case_plan)
        save_json(work_dir / "auto_change_records.json", auto_fixes)

    # Audit staged src_dir (if tex exists) + extract non-TeX facts from input files
    tex_audit = audit_tex_tree(src_dir if any(src_dir.rglob("*.tex")) else input_dir, skill_dir, env_info=case_plan["environment"], target_templates=case_plan["signals"].get("target_templates"))
    source_facts, source_numbers, table_drafts = extract_non_tex_facts(case_plan.get("files", []))

    audit_out = {
        "schema_version": "1.0",
        "case_id": case_plan["case_id"],
        **{k: tex_audit[k] for k in ("recommended_engine", "recommended_bib_tool", "tex_contains_cjk", "document_classes", "packages", "include_graph", "bib_resources", "bib_styles", "bib_keys_count", "cited_keys_count", "orphan_bib_keys", "labels_count", "findings", "error_count", "warning_count", "template_checks")},
        "source_facts": source_facts,
        "source_numbers": source_numbers,
        "table_drafts_count": len(table_drafts),
        "auto_fixes_applied": len(auto_fixes),
    }
    save_json(work_dir / "latex_audit.json", audit_out)
    digest_p = work_dir / "latex_digest.md"
    digest_p.write_text(render_digest_md(audit_out, case_plan, table_drafts, auto_fixes), encoding="utf-8")
    ckpt = _write_partial_checkpoint(case_plan, audit_out, out_dir)
    return {"case_plan": case_plan, "audit": audit_out, "staged": staged, "auto_fixes": auto_fixes, "partial_result": str(ckpt), "digest": str(digest_p)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspect case, stage sources, run static audit, optional safe autofix, and write early partial checkpoint.")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--task-md", default=None)
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--out-dir", default="/app/output")
    ap.add_argument("--skill-dir", default=None)
    ap.add_argument("--apply-safe-fixes", action="store_true", help="Apply deterministic safe fixes (BOM, label/caption order, OS font portability, bib escaping).")
    args = ap.parse_args()

    res = prepare_and_audit(
        input_dir=Path(args.input_dir),
        work_dir=Path(args.work_dir),
        out_dir=Path(args.out_dir),
        skill_dir=resolve_skill_dir(args.skill_dir),
        do_safe_fixes=args.apply_safe_fixes,
        task_md=Path(args.task_md) if args.task_md else None,
    )
    print(json.dumps({
        "status": "ok",
        "case_id": res["case_plan"]["case_id"],
        "mode": res["case_plan"]["signals"]["primary_mode"],
        "engine": res["audit"]["recommended_engine"],
        "bib": res["audit"]["recommended_bib_tool"],
        "static_errors": res["audit"]["error_count"],
        "static_warnings": res["audit"]["warning_count"],
        "auto_fixes": len(res["auto_fixes"]),
        "staged_count": len(res["staged"]),
        "digest": res["digest"],
        "partial_result": res["partial_result"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
