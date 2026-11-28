#!/usr/bin/env python3
"""Shared core utilities for the latex-sci-composer Skill toolchain.

Single source of truth for safe I/O, TeX/CJK environment probing, comment/verbatim
sanitization, balanced brace/command parsing, path resolution, and output_contract mapping.
Pure Python 3 standard library (optional PyMuPDF/fitz handled gracefully by callers).
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
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}
CONTROL_FILES = {"case.json", "task.md"}

SOURCE_COPY_EXTS = {
    ".tex", ".ltx", ".bib", ".cls", ".sty", ".bst", ".def", ".clo",
    ".bbx", ".cbx", ".lbx", ".cfg", ".fd", ".ttf", ".otf",
    ".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg", ".csv", ".dat", ".tikz", ".pgf",
}

CATEGORY_BY_EXT = {
    ".tex": "tex_source", ".ltx": "tex_source", ".bib": "bib_database",
    ".cls": "tex_class_or_style", ".sty": "tex_class_or_style", ".def": "tex_class_or_style", ".clo": "tex_class_or_style",
    ".bst": "bib_style",
    ".md": "draft_or_doc", ".markdown": "draft_or_doc", ".txt": "draft_or_doc", ".rst": "draft_or_doc",
    ".docx": "word_doc", ".pdf": "pdf_doc",
    ".csv": "tabular_data", ".tsv": "tabular_data",
    ".json": "structured_data", ".yaml": "structured_data", ".yml": "structured_data",
    ".log": "tex_log", ".blg": "bib_log", ".aux": "tex_aux",
    ".png": "figure_asset", ".jpg": "figure_asset", ".jpeg": "figure_asset", ".eps": "figure_asset", ".svg": "figure_asset",
}

_SHORTHAND_TYPES = {
    "string": "string", "str": "string", "text": "string",
    "array": "array", "list": "array",
    "object": "object", "dict": "object", "map": "object",
    "boolean": "boolean", "bool": "boolean",
    "integer": "integer", "int": "integer",
    "number": "number", "float": "number",
}


def resolve_skill_dir(hint: Optional[str] = None) -> Path:
    """Resolve Skill root directory cleanly across Harbor and local checkouts."""
    candidates: List[Path] = []
    if hint:
        candidates.append(Path(hint))
    if os.environ.get("SKILL_DIR"):
        candidates.append(Path(os.environ["SKILL_DIR"]))
    candidates.extend([Path(__file__).resolve().parent.parent, Path("/skills/weekly-skill"), Path("/app/submission")])
    for cand in candidates:
        if (cand / "resources").is_dir() and (cand / "scripts").is_dir():
            return cand.resolve()
    return Path(__file__).resolve().parent.parent.resolve()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def posix_rel(path: Path, base: Path) -> str:
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
    if b"\x00" in raw[:4096] and not (raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff")):
        return "", "binary_file_unreadable_as_text"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "encoding_lossy_fallback"


def load_json_safe(path: Path, default: Any = None) -> Any:
    text, err = read_text_safe(path)
    if err or not text.strip():
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def save_json(path: Path, obj: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def which_tool(name: str) -> Optional[str]:
    return shutil.which(name)


def run_cmd(
    cmd: List[str],
    cwd: Optional[Path] = None,
    timeout: int = 60,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Run subprocess safely without shell injection; resolves PATH wrappers cleanly on Windows/Linux."""
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
        return {"cmd": cmd, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "timed_out": False, "not_found": False}
    except FileNotFoundError:
        return {"cmd": cmd, "returncode": -1, "stdout": "", "stderr": f"Command not found: {cmd[0]}", "timed_out": False, "not_found": True}
    except subprocess.TimeoutExpired as exc:
        out_s = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err_s = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return {"cmd": cmd, "returncode": -2, "stdout": out_s, "stderr": err_s + f"\n[TIMEOUT after {timeout}s]", "timed_out": True, "not_found": False}


def mask_verbatim_and_verb(src: str) -> str:
    """Replace verbatim/lstlisting/minted environments and \\verb|...| with spaces preserving line numbers."""
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
    """Strip unescaped % comments while preserving exact line count and offsets."""
    out_lines: List[str] = []
    for line in text.splitlines(keepends=True):
        nl = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
        core = line[: len(line) - len(nl)] if nl else line
        i = 0
        cut = len(core)
        while i < len(core):
            if core[i] == "\\":
                i += 2
                continue
            if core[i] == "%":
                cut = i
                break
            i += 1
        out_lines.append(core[:cut] + (" " * (len(core) - cut)) + nl)
    return "".join(out_lines)


def sanitize_tex_for_scan(text: str) -> str:
    """Mask verbatim/\\verb BEFORE stripping % comments so \\verb|100% {a}| never corrupts braces."""
    return strip_tex_comments_preserve_lines(mask_verbatim_and_verb(text))


def extract_braced_arg(text: str, open_idx: int) -> Tuple[str, int]:
    """Given index of '{', return (inner_content, index_after_closing_brace). Returns (rest, -1) if unclosed."""
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
    return text.count("\n", 0, max(0, offset)) + 1


def extract_tex_commands(text: str, cmd_names: Tuple[str, ...], strip_comments: bool = True) -> List[Dict[str, Any]]:
    """Extract LaTeX commands \\name[opt1][opt2]{arg} with exact line numbers, verbatim-safe."""
    src = sanitize_tex_for_scan(text) if strip_comments else text
    alt = "|".join(re.escape(c) for c in cmd_names)
    pat = re.compile(rf"\\({alt})\b\*?")
    results: List[Dict[str, Any]] = []
    pos = 0
    n = len(src)

    def _skip_ws_and_single_nl(idx: int) -> int:
        while idx < n and src[idx] in " \t":
            idx += 1
        if idx < n and src[idx] in "\r\n":
            nl_end = idx + (2 if src[idx : idx + 2] == "\r\n" else 1)
            j_ws = nl_end
            while j_ws < n and src[j_ws] in " \t":
                j_ws += 1
            if j_ws < n and src[j_ws] in "[{":
                return j_ws
        return idx

    for m in pat.finditer(src):
        if m.start() < pos:
            continue
        cmd = m.group(1)
        idx = _skip_ws_and_single_nl(m.end())
        opts: List[str] = []
        while idx < n and src[idx] == "[":
            end_opt = src.find("]", idx + 1)
            if end_opt != -1 and "\n\n" not in src[idx:end_opt]:
                opts.append(src[idx + 1 : end_opt].strip())
                idx = _skip_ws_and_single_nl(end_opt + 1)
            else:
                break
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
                "opt": opts[0] if opts else None,
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
    return bool(CJK_RE.search(text))


def normalize_num_str(token: str) -> Optional[str]:
    t = token.strip().replace(",", "")
    if not t:
        return None
    is_pct = t.endswith("%")
    try:
        val = float(t[:-1] if is_pct else t)
    except ValueError:
        return None
    norm = "0" if val == 0 else f"{val:g}"
    return norm + ("%" if is_pct else "")


def extract_numeric_tokens(text: str) -> List[str]:
    return NUMBER_RE.findall(text)


def resolve_tex_target(raw_target: str, current_tex: Path, root_dir: Path) -> Optional[Path]:
    cand_names = [raw_target] + ([] if Path(raw_target).suffix else [raw_target + ".tex"])
    for base in (current_tex.parent, root_dir):
        for name in cand_names:
            p = (base / name).resolve()
            if p.is_file():
                return p
    return None


def resolve_image_target(raw_target: str, current_tex: Path, root_dir: Path, graphics_paths: List[str]) -> Optional[Path]:
    exts = [""] if Path(raw_target).suffix else ["", ".pdf", ".png", ".jpg", ".jpeg", ".eps"]
    bases = [current_tex.parent, root_dir]
    for gp in graphics_paths:
        bases.extend([(root_dir / gp).resolve(), (current_tex.parent / gp).resolve()])
    for base in bases:
        for ext in exts:
            p = (base / (raw_target + ext)).resolve()
            if p.is_file():
                return p
    return None


def query_cjk_fonts() -> List[str]:
    if not which_tool("fc-list"):
        return []
    res = run_cmd(["fc-list", ":lang=zh", "family"], timeout=15)
    if res["returncode"] != 0:
        return []
    seen = set()
    fonts: List[str] = []
    for raw_line in res["stdout"].splitlines():
        for fam in raw_line.split(","):
            fc = fam.strip()
            if fc and fc not in seen:
                seen.add(fc)
                fonts.append(fc)
    return sorted(fonts)


def pick_best_cjk_font(cjk_fonts: Optional[List[str]] = None) -> Optional[str]:
    fonts = cjk_fonts if cjk_fonts is not None else query_cjk_fonts()
    if not fonts:
        return None
    fset = set(fonts)
    for pref in (
        "Noto Serif CJK SC", "FandolSong", "AR PL UMing CN", "Source Han Serif SC",
        "Noto Sans CJK SC", "FandolHei", "WenQuanYi Micro Hei", "SimSun", "Microsoft YaHei",
    ):
        if pref in fset:
            return pref
    return fonts[0]


def fandol_is_available(cjk_fonts: Optional[List[str]] = None) -> bool:
    if which_tool("kpsewhich"):
        res = run_cmd(["kpsewhich", "FandolSong-Regular.otf"], timeout=10)
        if res["returncode"] == 0 and (res.get("stdout") or "").strip():
            return True
    fonts = cjk_fonts if cjk_fonts is not None else query_cjk_fonts()
    return any("fandol" in f.lower() for f in fonts)


def probe_tex_environment() -> Dict[str, Any]:
    """Probe installed TeX compilers, bib tools, PDF utilities, and CJK fonts."""
    tools = {
        t: which_tool(t)
        for t in ("pdflatex", "xelatex", "lualatex", "latexmk", "bibtex", "biber", "kpsewhich", "chktex", "fc-list", "pdftoppm", "pdffonts", "pdftotext")
    }
    has_fitz = False
    try:
        try:
            import pymupdf  # noqa: F401
        except ImportError:
            import fitz  # noqa: F401
        has_fitz = True
    except Exception:
        has_fitz = False

    engine_versions: Dict[str, str] = {}
    for eng in ("pdflatex", "xelatex", "lualatex", "bibtex", "biber"):
        if tools.get(eng):
            res = run_cmd([eng, "--version"], timeout=10)
            first = (res.get("stdout") or res.get("stderr") or "").splitlines()[:1]
            if first:
                engine_versions[eng] = first[0].strip()

    cjk_fonts = query_cjk_fonts()
    kpse_checks: Dict[str, Optional[str]] = {}
    if tools.get("kpsewhich"):
        for req in ("ctexart.cls", "xeCJK.sty", "FandolSong-Regular.otf", "IEEEtran.cls", "acmart.cls", "elsarticle.cls", "booktabs.sty", "biblatex.sty"):
            res = run_cmd(["kpsewhich", req], timeout=10)
            out = (res.get("stdout") or "").strip()
            kpse_checks[req] = out if (res["returncode"] == 0 and out) else None

    return {
        "platform": sys.platform,
        "tools": tools,
        "python_libs": {"fitz": has_fitz},
        "engine_versions": engine_versions,
        "cjk_fonts": cjk_fonts,
        "fandol_available": bool(kpse_checks.get("FandolSong-Regular.otf")) or any("fandol" in f.lower() for f in cjk_fonts),
        "kpse_checks": kpse_checks,
    }


def infer_semantic_role(field_name: str, desc: str = "") -> str:
    """Map an arbitrary output_contract field key to a canonical semantic role."""
    fn = field_name.lower().strip()
    k = f"{fn} {desc}".lower()
    if any(w in fn for w in ("case_id", "caseid", "task_id")) or any(w in k for w in ("case_id", "caseid", "task_id")):
        return "case_id"
    if "status" in fn:
        return "status"
    if fn.endswith(("_path", "_paths", "_file", "_files")) or fn in {"output_files", "deliverables", "deliverable_files", "artifacts", "files"}:
        return "rendered_outputs" if any(w in fn for w in ("pdf", "preview", "rendered")) else "output_files"
    if fn.endswith(("_error", "_errors", "_warning", "_warnings", "_log", "_logs", "_diagnostics")) or any(
        w in fn for w in ("compile", "build", "reproduc", "engine", "manifest", "diagnostic")
    ) or re.search(r"\blog\b", fn):
        return "compile_provenance"
    if fn.endswith(("_tex", "_latex")) or any(w in fn for w in ("latex", "tex_source", "main_tex", "manuscript", "compiled_tex", "revised_tex", "article", "content", "writing", "output_text")):
        return "primary_tex_content"

    for role, keys in (
        ("rendered_outputs", ("pdf", "preview", "rendered")),
        ("bibliography", ("bib", "citation", "reference_list")),
        ("template_compliance", ("template", "compliance", "checklist")),
        ("sources", ("source", "provenance", "evidence", "mapping", "traceability", "fact_map")),
        ("change_records", ("change", "diff", "revision", "edit_log", "modification")),
        ("author_confirmation_items", ("confirm", "question", "gap", "missing", "todo_author", "open_issue", "human_confirm")),
        ("uncertainty", ("uncertain", "unknown", "risk", "manual_takeover")),
        ("limitations", ("limitation", "failure", "boundary", "caveat", "scope")),
        ("summary", ("summary", "overview", "abstract", "report")),
    ):
        if any(w in fn for w in keys):
            return role

    # Fallback on description text
    if any(w in k for w in ("compile", "build", "warning", "error", "reproduc", "engine", "manifest")) or re.search(r"\blog\b", k):
        return "compile_provenance"
    for role, keys in (
        ("primary_tex_content", ("latex", "tex_source", "main_tex", "manuscript")),
        ("rendered_outputs", ("pdf", "preview", "rendered")),
        ("bibliography", ("bib", "citation", "reference_list")),
        ("sources", ("source", "provenance", "evidence")),
        ("change_records", ("change", "diff", "revision")),
        ("author_confirmation_items", ("confirm", "question", "gap")),
        ("uncertainty", ("uncertain", "risk")),
        ("limitations", ("limitation", "caveat")),
        ("output_files", ("file", "artifact", "deliverable", "path")),
        ("summary", ("summary", "overview", "report")),
    ):
        if any(w in k for w in keys):
            return role
    return "custom"


def _infer_type_from_val(val: Any) -> Tuple[str, str]:
    if isinstance(val, str):
        s_low = val.strip().lower()
        return (_SHORTHAND_TYPES[s_low], "") if s_low in _SHORTHAND_TYPES else ("string", val)
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
    """Normalize JSON Schema / dict / list output_contract into uniform field specs."""
    fields: List[Dict[str, Any]] = []
    if contract is None:
        return {"raw_type": "none", "fields": [], "required_keys": []}

    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            req_set = set(contract.get("required") or [])
            for k, spec in contract["properties"].items():
                sd = spec if isinstance(spec, dict) else {"example": spec}
                raw_t = sd.get("type")
                ftype = _SHORTHAND_TYPES.get(str(raw_t).strip().lower(), str(raw_t)) if raw_t else _infer_type_from_val(spec)[0]
                desc = str(sd.get("description", ""))
                fields.append({"name": k, "expected_type": ftype, "required": (k in req_set) if req_set else True, "description": desc, "semantic_role": infer_semantic_role(k, desc)})
            return {"raw_type": "json_schema", "fields": fields, "required_keys": [f["name"] for f in fields if f["required"]]}

        if "fields" in contract and isinstance(contract["fields"], (dict, list)):
            return parse_output_contract(contract["fields"])

        is_wrapper = "$schema" in contract or (contract.get("type") == "object" and isinstance(contract.get("required"), list))
        meta_keys = {"$schema", "additionalProperties"} | ({"type", "description", "format", "required", "title"} if is_wrapper else set())
        req_list = contract.get("required") if isinstance(contract.get("required"), list) else None
        for k, val in contract.items():
            if (k in meta_keys and len(contract) > 1) or (k == "required" and isinstance(val, list) and len(contract) > 1):
                continue
            if isinstance(val, dict) and ("type" in val or "description" in val):
                raw_t = str(val.get("type", "object")).strip().lower()
                ftype, desc, req = _SHORTHAND_TYPES.get(raw_t, raw_t), str(val.get("description", "")), bool(val.get("required", True))
            else:
                ftype, desc = _infer_type_from_val(val)
                req = (k in req_list) if req_list is not None else True
            fields.append({"name": k, "expected_type": ftype, "required": req, "description": desc, "semantic_role": infer_semantic_role(k, desc)})
        for rk in (req_list or []):
            if not any(f["name"] == rk for f in fields):
                fields.append({"name": rk, "expected_type": "any", "required": True, "description": "", "semantic_role": infer_semantic_role(rk, "")})
        return {"raw_type": "dict_contract", "fields": fields, "required_keys": [f["name"] for f in fields if f["required"]]}

    if isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields.append({"name": item, "expected_type": "any", "required": True, "description": "", "semantic_role": infer_semantic_role(item, "")})
            elif isinstance(item, dict):
                name = str(item.get("name") or item.get("field") or item.get("key") or "").strip()
                if name:
                    desc = str(item.get("description", ""))
                    fields.append({"name": name, "expected_type": str(item.get("type", "any")), "required": bool(item.get("required", True)), "description": desc, "semantic_role": infer_semantic_role(name, desc)})
        return {"raw_type": "list_contract", "fields": fields, "required_keys": [f["name"] for f in fields if f["required"]]}

    return {"raw_type": type(contract).__name__, "fields": [], "required_keys": []}


def coerce_contract_value(val: Any, etype: str, status: str, summary: str) -> Any:
    """Coerce a contract field value safely to the schema's expected type."""
    et = (etype or "any").lower()
    if val is None:
        if et in {"array", "list"}:
            return []
        if et in {"object", "dict"}:
            return {}
        if et in {"boolean", "bool"}:
            return status == "completed"
        if et in {"integer", "int"}:
            return 0
        if et in {"number", "float"}:
            return 0.0
        return str(summary or "")
    if et in {"string", "str"}:
        return val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
    if et in {"array", "list"}:
        return val if isinstance(val, list) else ([val] if val not in (None, "") else [])
    if et in {"object", "dict"}:
        return val if isinstance(val, dict) else ({"value": val} if val not in (None, "") else {})
    if et in {"boolean", "bool"}:
        return bool(val)
    if et in {"integer", "int"}:
        try:
            return int(val)
        except Exception:
            return 0
    if et in {"number", "float"}:
        try:
            return float(val)
        except Exception:
            return 0.0
    return val
