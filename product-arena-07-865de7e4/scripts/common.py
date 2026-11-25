#!/usr/bin/env python3
"""Shared utilities for the latex-sci-composer Skill toolchain.

Pure Python 3 standard library implementation (optional PyMuPDF/fitz handled
gracefully by callers) supporting both Linux Harbor containers and Windows.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?%?")
SKIP_SCAN_ENVS = ("verbatim", "verbatim*", "lstlisting", "minted", "comment", "filecontents", "filecontents*")
_SHORTHAND_TYPES = {
    "string": "string",
    "str": "string",
    "text": "string",
    "array": "array",
    "list": "array",
    "object": "object",
    "dict": "object",
    "map": "object",
    "boolean": "boolean",
    "bool": "boolean",
    "integer": "integer",
    "int": "integer",
    "number": "number",
    "float": "number",
}


def resolve_skill_dir(hint: Optional[str] = None) -> Path:
    """Resolve Skill root directory cleanly across Harbor and local checkouts."""
    candidates: List[Path] = []
    if hint:
        candidates.append(Path(hint))
    env_dir = os.environ.get("SKILL_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path(__file__).resolve().parent.parent)
    candidates.append(Path("/skills/weekly-skill"))
    for cand in candidates:
        if (cand / "resources").is_dir() and (cand / "scripts").is_dir():
            return cand.resolve()
    return Path(__file__).resolve().parent.parent.resolve()


def ensure_dir(path: Path) -> Path:
    """Create directory if needed and return Path."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def posix_rel(path: Path, base: Path) -> str:
    """Return a clean POSIX-style relative path."""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_text_safe(path: Path) -> Tuple[str, Optional[str]]:
    """Read text file safely, returning (content, error_reason_or_none)."""
    try:
        raw = path.read_bytes()
    except PermissionError as exc:
        return "", f"permission_denied: {exc}"
    except FileNotFoundError:
        return "", "file_not_found"
    except OSError as exc:
        return "", f"os_error: {exc}"

    if not raw:
        return "", None

    # Detect binary blob quickly (NUL byte outside UTF-16)
    if b"\x00" in raw[:4096] and not (raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff")):
        return "", "binary_file_unreadable_as_text"

    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "encoding_lossy_fallback"


def load_json_safe(path: Path, default: Any = None) -> Any:
    """Load JSON safely, returning default on missing or malformed JSON."""
    text, err = read_text_safe(path)
    if err or not text.strip():
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def save_json(path: Path, obj: Any) -> None:
    """Write formatted UTF-8 JSON atomically."""
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_cmd(
    cmd: List[str],
    cwd: Optional[Path] = None,
    timeout: int = 60,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Run subprocess safely without shell injection and return structured output."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    resolved_exe = shutil.which(cmd[0], path=merged_env.get("PATH")) if cmd else None
    actual_cmd = [resolved_exe, *cmd[1:]] if resolved_exe else cmd
    try:
        proc = subprocess.run(
            actual_cmd,
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "timed_out": False,
            "not_found": False,
        }
    except FileNotFoundError:
        return {
            "cmd": cmd,
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command not found: {cmd[0]}",
            "timed_out": False,
            "not_found": True,
        }
    except subprocess.TimeoutExpired as exc:
        out_s = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err_s = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return {
            "cmd": cmd,
            "returncode": -2,
            "stdout": out_s,
            "stderr": err_s + f"\n[TIMEOUT after {timeout}s]",
            "timed_out": True,
            "not_found": False,
        }


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


def strip_tex_comments_preserve_lines(text: str) -> str:
    """Strip unescaped LaTeX % comments while preserving exact line count and offsets per line."""
    out_lines: List[str] = []
    for line in text.splitlines(keepends=True):
        nl = ""
        if line.endswith("\r\n"):
            core, nl = line[:-2], "\r\n"
        elif line.endswith("\n"):
            core, nl = line[:-1], "\n"
        else:
            core = line
        i = 0
        cut = len(core)
        while i < len(core):
            ch = core[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "%":
                cut = i
                break
            i += 1
        out_lines.append(core[:cut] + (" " * (len(core) - cut)) + nl)
    return "".join(out_lines)


def sanitize_tex_for_scan(text: str) -> str:
    """Mask verbatim/\\verb BEFORE stripping % comments so \\verb|100% {a}| never corrupts braces."""
    return strip_tex_comments_preserve_lines(mask_verbatim_and_verb(text))


def extract_braced_arg(text: str, open_idx: int) -> Tuple[str, int]:
    """Given index of '{', return (inner_content, index_after_closing_brace). Returns ('', -1) if unclosed."""
    if open_idx >= len(text) or text[open_idx] != "{":
        return "", -1
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : i], i + 1
        i += 1
    return text[open_idx + 1 :], -1


def line_number_at(text: str, offset: int) -> int:
    """1-based line number for a character offset."""
    return text.count("\n", 0, max(0, offset)) + 1


def extract_tex_commands(
    text: str,
    cmd_names: Tuple[str, ...],
    strip_comments: bool = True,
) -> List[Dict[str, Any]]:
    """Extract LaTeX commands \\name[opt1][opt2]{arg} with exact line numbers, verbatim-safe."""
    src = sanitize_tex_for_scan(text) if strip_comments else text
    alt = "|".join(re.escape(c) for c in cmd_names)
    pat = re.compile(rf"\\({alt})\b\*?")
    results: List[Dict[str, Any]] = []
    pos = 0
    n = len(src)
    for m in pat.finditer(src):
        if m.start() < pos:
            continue
        cmd = m.group(1)
        idx = m.end()
        while idx < n and src[idx] in " \t":
            idx += 1
        # Allow a single newline (not blank line) before optional/required args
        if idx < n and src[idx] in "\r\n":
            nl_end = idx + (2 if src[idx : idx + 2] == "\r\n" else 1)
            j_ws = nl_end
            while j_ws < n and src[j_ws] in " \t":
                j_ws += 1
            if j_ws < n and src[j_ws] in "[{":
                idx = j_ws

        opts: List[str] = []
        while idx < n and src[idx] == "[":
            end_opt = src.find("]", idx + 1)
            if end_opt != -1 and "\n\n" not in src[idx:end_opt]:
                opts.append(src[idx + 1 : end_opt].strip())
                idx = end_opt + 1
                while idx < n and src[idx] in " \t":
                    idx += 1
                if idx < n and src[idx] in "\r\n":
                    nl_end = idx + (2 if src[idx : idx + 2] == "\r\n" else 1)
                    j_ws = nl_end
                    while j_ws < n and src[j_ws] in " \t":
                        j_ws += 1
                    if j_ws < n and src[j_ws] in "[{":
                        idx = j_ws
            else:
                break
        opt = opts[0] if opts else None

        args: List[str] = []
        while idx < n and src[idx] == "{":
            arg, nxt = extract_braced_arg(src, idx)
            args.append(arg.strip())
            if nxt == -1:
                break
            idx = nxt
            while idx < n and src[idx] in " \t":
                idx += 1
            if len(args) >= 2:
                break
        results.append(
            {
                "command": cmd,
                "opt": opt,
                "opts": opts,
                "arg": args[0] if args else "",
                "args": args,
                "line": line_number_at(src, m.start()),
                "offset": m.start(),
            }
        )
        pos = idx
    return results


def has_cjk(text: str) -> bool:
    """Return True if text contains CJK Unified Ideographs."""
    return bool(CJK_RE.search(text))


def normalize_num_str(token: str) -> Optional[str]:
    """Normalize numeric token (strip commas and leading +) for source-to-target fidelity comparison."""
    t = token.strip().replace(",", "")
    if not t:
        return None
    is_pct = t.endswith("%")
    core = t[:-1] if is_pct else t
    try:
        val = float(core)
    except ValueError:
        return None
    if val == 0:
        norm = "0"
    else:
        norm = f"{val:g}"
    return norm + ("%" if is_pct else "")


def extract_numeric_tokens(text: str) -> List[str]:
    """Extract raw numeric strings from prose/tables."""
    return NUMBER_RE.findall(text)


def which_tool(name: str) -> Optional[str]:
    """Find executable path if installed."""
    return shutil.which(name)


def infer_semantic_role(field_name: str, desc: str = "") -> str:
    """Map an arbitrary output_contract field key to a canonical semantic role."""
    fn = field_name.lower().strip()
    k = f"{fn} {desc}".lower()
    if any(w in fn for w in ("case_id", "caseid", "task_id")) or any(w in k for w in ("case_id", "caseid", "task_id")):
        return "case_id"
    if fn in {"status", "run_status", "execution_status"} or any(w in fn for w in ("status",)):
        return "status"
    # Check file/path and error/warning/diagnostic suffixes BEFORE generic 'latex'/'manuscript'
    if fn.endswith(("_path", "_paths", "_file", "_files")) or fn in {"output_files", "deliverables", "deliverable_files", "artifacts", "files"}:
        if any(w in fn for w in ("pdf", "preview", "rendered")):
            return "rendered_outputs"
        return "output_files"
    if fn.endswith(("_error", "_errors", "_warning", "_warnings", "_log", "_logs", "_diagnostics")) or any(
        w in fn for w in ("compile", "build", "reproduc", "engine", "manifest", "diagnostic")
    ) or re.search(r"\blog\b", fn):
        return "compile_provenance"
    if fn.endswith(("_tex", "_latex")) or any(w in fn for w in ("latex", "tex_source", "main_tex", "manuscript", "compiled_tex", "revised_tex", "article", "content", "writing", "output_text")):
        return "primary_tex_content"
    if any(w in fn for w in ("pdf", "preview", "rendered")):
        return "rendered_outputs"
    if any(w in fn for w in ("bib", "citation", "reference_list")):
        return "bibliography"
    if any(w in fn for w in ("template", "compliance", "checklist")):
        return "template_compliance"
    if any(w in fn for w in ("source", "provenance", "evidence", "mapping", "traceability", "fact_map")):
        return "sources"
    if any(w in fn for w in ("change", "diff", "revision", "edit_log", "modification")):
        return "change_records"
    if any(w in fn for w in ("confirm", "question", "gap", "missing", "todo_author", "open_issue", "human_confirm")):
        return "author_confirmation_items"
    if any(w in fn for w in ("uncertain", "unknown", "risk", "manual_takeover")):
        return "uncertainty"
    if any(w in fn for w in ("limitation", "failure", "boundary", "caveat", "scope")):
        return "limitations"
    if any(w in fn for w in ("latex", "tex_source", "main_tex", "manuscript", "compiled_tex", "revised_tex", "article", "content", "writing", "output_text")):
        return "primary_tex_content"
    if any(w in fn for w in ("summary", "overview", "abstract", "report")):
        return "summary"
    # Secondary fallback using description text (word-boundary safe for 'log')
    if any(w in k for w in ("compile", "build", "warning", "error", "reproduc", "engine", "manifest")) or re.search(r"\blog\b", k):
        return "compile_provenance"
    if any(w in k for w in ("latex", "tex_source", "main_tex", "manuscript")):
        return "primary_tex_content"
    if any(w in k for w in ("pdf", "preview", "rendered")):
        return "rendered_outputs"
    if any(w in k for w in ("bib", "citation", "reference_list")):
        return "bibliography"
    if any(w in k for w in ("source", "provenance", "evidence")):
        return "sources"
    if any(w in k for w in ("change", "diff", "revision")):
        return "change_records"
    if any(w in k for w in ("confirm", "question", "gap")):
        return "author_confirmation_items"
    if any(w in k for w in ("uncertain", "risk")):
        return "uncertainty"
    if any(w in k for w in ("limitation", "caveat")):
        return "limitations"
    if any(w in k for w in ("file", "artifact", "deliverable", "path")):
        return "output_files"
    if any(w in k for w in ("summary", "overview", "report")):
        return "summary"
    return "custom"


def _infer_type_from_val(val: Any) -> Tuple[str, str]:
    if isinstance(val, str):
        s_low = val.strip().lower()
        if s_low in _SHORTHAND_TYPES:
            return _SHORTHAND_TYPES[s_low], ""
        return "string", val
    if isinstance(val, bool):
        return "boolean", ""
    if isinstance(val, int):
        return "integer", ""
    if isinstance(val, float):
        return "number", ""
    if isinstance(val, list):
        return "array", ""
    if isinstance(val, dict):
        return "object", ""
    return "any", ""


def parse_output_contract(contract: Any) -> Dict[str, Any]:
    """Normalize any output_contract shape (JSON Schema, dict template, list) into structured specs."""
    fields: List[Dict[str, Any]] = []
    if contract is None:
        return {"raw_type": "none", "fields": [], "required_keys": []}

    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            req_set = set(contract.get("required") or [])
            for k, spec in contract["properties"].items():
                sd = spec if isinstance(spec, dict) else {"example": spec}
                raw_t = sd.get("type")
                if raw_t:
                    ftype = _SHORTHAND_TYPES.get(str(raw_t).strip().lower(), str(raw_t))
                else:
                    ftype, _ = _infer_type_from_val(spec)
                desc = str(sd.get("description", ""))
                fields.append(
                    {
                        "name": k,
                        "expected_type": ftype,
                        "required": (k in req_set) if req_set else True,
                        "description": desc,
                        "semantic_role": infer_semantic_role(k, desc),
                    }
                )
            return {
                "raw_type": "json_schema",
                "fields": fields,
                "required_keys": [f["name"] for f in fields if f["required"]],
            }

        if "fields" in contract and isinstance(contract["fields"], (dict, list)):
            return parse_output_contract(contract["fields"])

        is_schema_wrapper = (
            "$schema" in contract
            or (contract.get("type") == "object" and isinstance(contract.get("required"), list))
        )
        schema_meta_keys = {"$schema", "additionalProperties"}
        if is_schema_wrapper:
            schema_meta_keys |= {"type", "description", "format", "required", "title"}
        req_list = contract.get("required") if isinstance(contract.get("required"), list) else None
        for k, val in contract.items():
            if k in schema_meta_keys and len(contract) > 1:
                continue
            if k == "required" and isinstance(val, list) and len(contract) > 1:
                continue
            if isinstance(val, dict) and ("type" in val or "description" in val):
                raw_t = str(val.get("type", "object")).strip().lower()
                ftype = _SHORTHAND_TYPES.get(raw_t, raw_t)
                desc = str(val.get("description", ""))
                req = bool(val.get("required", True))
            else:
                ftype, desc = _infer_type_from_val(val)
                req = (k in req_list) if req_list is not None else True
            fields.append(
                {
                    "name": k,
                    "expected_type": ftype,
                    "required": req,
                    "description": desc,
                    "semantic_role": infer_semantic_role(k, desc),
                }
            )
        if req_list:
            existing = {f["name"] for f in fields}
            for rk in req_list:
                if rk not in existing:
                    fields.append(
                        {
                            "name": rk,
                            "expected_type": "any",
                            "required": True,
                            "description": "",
                            "semantic_role": infer_semantic_role(rk, ""),
                        }
                    )
        return {
            "raw_type": "dict_contract",
            "fields": fields,
            "required_keys": [f["name"] for f in fields if f["required"]],
        }

    if isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields.append(
                    {
                        "name": item,
                        "expected_type": "any",
                        "required": True,
                        "description": "",
                        "semantic_role": infer_semantic_role(item, ""),
                    }
                )
            elif isinstance(item, dict):
                name = str(item.get("name") or item.get("field") or item.get("key") or "").strip()
                if not name:
                    continue
                ftype = str(item.get("type", "any"))
                desc = str(item.get("description", ""))
                fields.append(
                    {
                        "name": name,
                        "expected_type": ftype,
                        "required": bool(item.get("required", True)),
                        "description": desc,
                        "semantic_role": infer_semantic_role(name, desc),
                    }
                )
        return {
            "raw_type": "list_contract",
            "fields": fields,
            "required_keys": [f["name"] for f in fields if f["required"]],
        }

    return {"raw_type": type(contract).__name__, "fields": [], "required_keys": []}
