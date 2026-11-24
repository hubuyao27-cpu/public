#!/usr/bin/env python3
"""Step 2: Deterministic multi-artifact Fact Ledger, Precedence Conflict Engine,
Unsourced Draft Claim Detector, and 6-Pillar Pre-Audit.

Reads /app/work/case_plan.json and all real files under /input/, producing:
  - /app/work/fact_ledger.json (machine-readable evidence & grounded-number index)
  - /app/work/fact_digest.md   (compact, source-anchored briefing for the writing Agent)
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    build_alias_lookup,
    canonicalize_param_key,
    load_json_safe,
    load_resource_json,
    parse_docx_paragraphs,
    parse_json_with_lines,
    parse_numeric_and_unit,
    parse_pdf_pages,
    parse_scalar,
    parse_toml_or_ini_with_lines,
    parse_yaml_with_lines,
    read_text_safe,
    save_json,
    values_equivalent,
)

AUTHORITATIVE_TIERS = {
    "runtime_log",
    "cli_script",
    "exp_config",
    "data_table",
    "paper_source",
    "lab_note",
    "code_default",
}

ENV_PATTERNS = [
    ("gpu_hardware", re.compile(r"(?:GPU|NVIDIA|RTX\s*\d+|A100|H100|V100|T4|L40|CUDA\s+Device|AMD\s+MI\d+)[^\n]{0,120}", re.I)),
    ("cpu_hardware", re.compile(r"(?:CPU|Intel\s+Xeon|AMD\s+EPYC|cores?\s*[:=]\s*\d+|RAM\s*[:=]\s*\d+\s*(?:GB|TB))[^\n]{0,100}", re.I)),
    ("instrument", re.compile(r"(?:XRD|XPS|SEM|TEM|FTIR|HPLC|GC-MS|GC|ICP-OES|ICP-MS|NMR|BET|TGA|DSC|Autoclave|Bruker|Rigaku|Agilent|Shimadzu|Thermo\s+Fisher|FEI|JEOL|PerkinElmer)[^\n]{0,120}", re.I)),
    ("software_version", re.compile(r"(?:Python|PyTorch|torch|TensorFlow|CUDA|cuDNN|NumPy|SciPy|scikit-learn|transformers|LAMMPS|VASP|GROMACS|OpenFOAM|Cantera)\s*(?:version|v|==|[:=])?\s*v?\d+\.\d+(?:\.\d+)?(?:\+[A-Za-z0-9.]+)?", re.I)),
    ("git_commit", re.compile(r"(?:git\s*(?:commit|rev|hash|sha)|commit\s*[:=])\s*([0-9a-f]{7,40})", re.I)),
]

# Prose/log key=value and parameter phrasing patterns
KV_INLINE_RE = re.compile(
    r"(?:^|[\s,;(])([A-Za-z][A-Za-z0-9_.\-]{1,35})\s*(?:=|:)\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?:\s*(?:°C|℃|K|MPa|kPa|Pa|bar|atm|mL/min|mL|L|mg|g|kg|mol/L|M|wt%|%|h|hr|min|s|ms|nm|μm|mm|cm|m|rpm|V|mV|mA|A|W|Hz|GB|MB))?|[A-Za-z][A-Za-z0-9_.\-/+]{0,30}|\[[0-9,\s]+\])"
)

PROSE_PARAM_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("learning_rate", re.compile(r"(?:learning\s+rate|学习率)\s*(?:of|is|was|=|:|为|设为|设置为)?\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+|\s*[×xX*]\s*10\s*\^?\s*\{?[+-]?\d+\}?))", re.I)),
    ("batch_size", re.compile(r"(?:batch\s+size|批大小|批次大小)\s*(?:of|is|was|=|:|为|设为|设置为)?\s*(\d+)", re.I)),
    ("epochs", re.compile(r"(?:(\d+)\s*(?:epochs?|训练轮次|轮|个\s*epoch)|(?:epochs?|训练轮数)\s*(?:of|is|was|=|:|为|设为)?\s*(\d+))", re.I)),
    ("random_seed", re.compile(r"(?:random\s+seeds?|seeds?|随机种子)\s*(?:of|is|was|=|:|为|设为)?\s*(\[?[0-9]+(?:\s*,\s*[0-9]+)*\]?)", re.I)),
    ("dropout", re.compile(r"(?:dropout(?:\s+rate)?)\s*(?:of|is|was|=|:|为|设为)?\s*(0?\.\d+|\d+(?:\.\d+)?%)", re.I)),
    ("weight_decay", re.compile(r"(?:weight\s+decay|权重衰减)\s*(?:of|is|was|=|:|为|设为)?\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+|\s*[×xX*]\s*10\s*\^?\s*\{?[+-]?\d+\}?)?)", re.I)),
    ("split_ratio", re.compile(r"(\d+(?:\.\d+)?\s*[:/]\s*\d+(?:\.\d+)?(?:\s*[:/]\s*\d+(?:\.\d+)?)?)\s*(?:split|ratio|train[\-/]val|划分|比例)?", re.I)),
    ("temperature", re.compile(r"(?:temperature|temp\.?|calcin\w*\s+at|anneal\w*\s+at|heated\s+at|温度|煅烧温度|反应温度)\s*(?:of|at|is|was|=|:|为)?\s*([+-]?\d+(?:\.\d+)?\s*(?:°C|℃|K))", re.I)),
    ("pressure", re.compile(r"(?:pressure|压力|反应压力)\s*(?:of|at|is|was|=|:|为)?\s*([+-]?\d+(?:\.\d+)?\s*(?:MPa|kPa|Pa|bar|atm|Torr))", re.I)),
    ("duration", re.compile(r"(?:for|duration|time|反应时间|保温|煅烧|持续)\s*(?:of|is|was|=|:|为)?\s*(\d+(?:\.\d+)?\s*(?:hours?|hrs?|h|mins?|minutes?|min|seconds?|secs?|s|小时|分钟|秒))", re.I)),
    ("heating_rate", re.compile(r"(?:heating\s+rate|ramp\s+rate|升温速率)\s*(?:of|is|was|=|:|为)?\s*(\d+(?:\.\d+)?\s*(?:°C/min|℃/min|K/min|°C\s*min\^-1))", re.I)),
    ("flow_rate", re.compile(r"(?:flow\s+rate|流速|气速|空速)\s*(?:of|is|was|=|:|为)?\s*(\d+(?:\.\d+)?\s*(?:mL/min|ml/min|L/min|sccm|h\^-1|mL\s*min\^-1))", re.I)),
]

# General quantitative token extractor for grounded-number pool
QUANT_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+|\s*[×xX*]\s*10\s*\^?\s*\{?[+-]?\d+\}?)?)\s*(°C/min|℃/min|K/min|mL/min|ml/min|L/min|m/s|cm/s|mol/L|wt%|at%|vol%|°C|℃|MPa|kPa|Pa|bar|atm|Torr|mmHg|mL|ml|μL|uL|L|kg|mg|μg|ug|g|km|cm|mm|μm|um|nm|Å|hours?|hrs?|h|mins?|minutes?|min|secs?|seconds?|ms|μs|us|ns|ps|fs|s|rpm|kV|mV|V|mA|μA|uA|A|kW|mW|W|GHz|MHz|kHz|Hz|TB|GB|MB|KB|%|epochs?|steps?|runs?|folds?|replicates?|samples?|tokens?|layers?|heads?|小时|分钟|秒|℃|克|毫克|毫升|升)?(?![A-Za-z0-9_])"
)


def extract_numbers_from_text(
    text: str,
    rel_path: str,
    locator: str,
    source_tier: str,
) -> List[Dict[str, Any]]:
    """Extract numeric literals + optional units from text into the grounded number index."""
    results: List[Dict[str, Any]] = []
    seen = set()
    for m in QUANT_TOKEN_RE.finditer(text):
        raw_num = m.group(1).strip()
        unit = (m.group(2) or "").strip()
        val, parsed_u = parse_numeric_and_unit(f"{raw_num} {unit}".strip())
        if val is None:
            continue
        final_unit = unit or parsed_u
        key = (round(val, 10), final_unit.lower(), locator)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "value": val,
                "raw": f"{raw_num}{(' ' + final_unit) if final_unit else ''}",
                "unit": final_unit,
                "file": rel_path,
                "locator": locator,
                "source_tier": source_tier,
            }
        )
    return results


def add_param(
    params: List[Dict[str, Any]],
    alias_lookup: Dict[str, str],
    raw_key: str,
    value: Any,
    rel_path: str,
    locator: str,
    source_tier: str,
    verbatim_quote: str,
    unit: str = "",
    stage: str = "unspecified",
    target_object: str = "",
) -> None:
    """Append a normalized parameter record."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return
    ckey = canonicalize_param_key(raw_key, alias_lookup)
    num_val, parsed_unit = parse_numeric_and_unit(value)
    final_unit = unit or parsed_unit
    clean_val = num_val if (num_val is not None and final_unit and isinstance(value, str)) else value
    params.append(
        {
            "raw_key": raw_key,
            "canonical_key": ckey,
            "value": clean_val,
            "raw_value": value,
            "unit": final_unit,
            "stage": stage,
            "target_object": target_object,
            "file": rel_path,
            "source_locator": locator,
            "source_tier": source_tier,
            "verbatim_quote": verbatim_quote[:240],
        }
    )


def infer_stage_from_context(key_or_line: str) -> str:
    low = key_or_line.lower()
    if any(w in low for w in ("test", "infer", "predict", "测试", "推理")):
        return "testing"
    if any(w in low for w in ("val", "valid", "dev", "验证")):
        return "validation"
    if any(w in low for w in ("train", "fit", "optim", "epoch", "lr", "warmup", "训练")):
        return "training"
    if any(w in low for w in ("calcin", "synthes", "prep", "preprocess", "split", "合成", "制备", "预处理")):
        return "preparation"
    if any(w in low for w in ("xrd", "xps", "sem", "tem", "ftir", "gc", "hplc", "charact", "表征")):
        return "characterization"
    return "unspecified"


def extract_from_config(
    fp: Path,
    rel: str,
    alias_lookup: Dict[str, str],
    params: List[Dict[str, Any]],
    grounded_nums: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    excerpts: List[Dict[str, Any]],
) -> None:
    text, warn, err = read_text_safe(fp)
    if err:
        warnings.append({"file": rel, "issue": warn or "read_error", "detail": err})
        return
    if warn:
        warnings.append({"file": rel, "issue": warn, "detail": f"File read with warning: {warn}"})
    if not text.strip():
        return

    ext = fp.suffix.lower()
    entries: List[Dict[str, Any]] = []
    perr: Optional[str] = None
    if ext in (".yaml", ".yml"):
        entries, perr = parse_yaml_with_lines(text)
    elif ext == ".json":
        entries, perr = parse_json_with_lines(text)
    elif ext == ".toml":
        entries, perr = parse_toml_or_ini_with_lines(text, is_toml=True)
    else:
        entries, perr = parse_toml_or_ini_with_lines(text, is_toml=False)

    if perr:
        warnings.append({"file": rel, "issue": "config_parse_warning", "detail": perr})

    for ent in entries:
        loc = f"{rel}:L{ent['line']}"
        kp = ent["key_path"]
        val = ent["value"]
        stage = infer_stage_from_context(kp)
        add_param(params, alias_lookup, kp, val, rel, loc, "exp_config", ent["raw_line"], stage=stage)
        grounded_nums.extend(extract_numbers_from_text(ent["raw_line"], rel, loc, "exp_config"))

    excerpts.append({"file": rel, "category": "exp_config", "excerpt": text[:2500]})


def extract_from_shell(
    fp: Path,
    rel: str,
    alias_lookup: Dict[str, str],
    params: List[Dict[str, Any]],
    commands: List[Dict[str, Any]],
    grounded_nums: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    excerpts: List[Dict[str, Any]],
) -> None:
    text, warn, err = read_text_safe(fp)
    if err:
        warnings.append({"file": rel, "issue": warn or "read_error", "detail": err})
        return
    if not text.strip():
        return

    raw_lines = text.splitlines()
    # Join backslash-continued lines while preserving start line numbers
    logical_cmds: List[Tuple[int, str]] = []
    buf = ""
    start_ln = 1
    for idx, line in enumerate(raw_lines, start=1):
        s = line.strip()
        if not buf:
            if not s or s.startswith("#"):
                continue
            start_ln = idx
        if s.endswith("\\"):
            buf += " " + s[:-1].strip()
        else:
            buf = (buf + " " + s).strip() if buf else s
            if buf:
                logical_cmds.append((start_ln, buf))
            buf = ""
    if buf:
        logical_cmds.append((start_ln, buf))

    for ln, cmd in logical_cmds:
        loc = f"{rel}:L{ln}"
        grounded_nums.extend(extract_numbers_from_text(cmd, rel, loc, "cli_script"))
        if any(tok in cmd for tok in ("python", "torchrun", "accelerate", "bash", "lmp", "mpirun", "srun", "pytest", "Rscript", "eval", "train")):
            commands.append({"command": cmd, "source_locator": loc})

        # Extract env var assignments (e.g. CUDA_VISIBLE_DEVICES=0,1 SEED=42)
        for m in re.finditer(r"(?:^|\s)(?:export\s+)?([A-Z][A-Z0-9_]{1,30})=([^\s;|&]+)", cmd):
            k, v = m.group(1), parse_scalar(m.group(2))
            add_param(params, alias_lookup, k, v, rel, loc, "cli_script", m.group(0).strip(), stage=infer_stage_from_context(cmd))

        # Extract --flag value or --flag=value
        for m in re.finditer(r"--([A-Za-z0-9][A-Za-z0-9_\-]{0,35})(?:=|\s+)([^\s;|&\"']+|\"[^\"]+\"|'[^']+')", cmd):
            fk, fv = m.group(1), m.group(2)
            if fv.startswith("--"):
                continue
            add_param(
                params,
                alias_lookup,
                fk,
                parse_scalar(fv),
                rel,
                loc,
                "cli_script",
                f"--{fk} {fv}",
                stage=infer_stage_from_context(cmd),
            )

    excerpts.append({"file": rel, "category": "cli_script", "excerpt": text[:2500]})


def _ast_const_value(node: ast.AST) -> Tuple[bool, Any]:
    """Safely evaluate simple AST literal/unary/list/tuple nodes."""
    if isinstance(node, ast.Constant):
        return True, node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        if isinstance(node.operand.value, (int, float)):
            return True, -node.operand.value
    if isinstance(node, (ast.List, ast.Tuple)):
        vals = []
        for el in node.elts:
            ok, v = _ast_const_value(el)
            if not ok:
                return False, None
            vals.append(v)
        return True, vals
    return False, None


def extract_from_code(
    fp: Path,
    rel: str,
    alias_lookup: Dict[str, str],
    params: List[Dict[str, Any]],
    code_steps: List[Dict[str, Any]],
    grounded_nums: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    excerpts: List[Dict[str, Any]],
) -> None:
    text, warn, err = read_text_safe(fp)
    if err:
        warnings.append({"file": rel, "issue": warn or "read_error", "detail": err})
        return
    if not text.strip():
        return

    # Unpack Jupyter notebooks into plain python/markdown lines
    if fp.suffix.lower() == ".ipynb":
        try:
            nb = json.loads(text)
            cells = []
            for ci, cell in enumerate(nb.get("cells", []), start=1):
                src = "".join(cell.get("source", []))
                cells.append(f"# --- Cell {ci} ({cell.get('cell_type')}) ---\n{src}")
            text = "\n\n".join(cells)
        except Exception as exc:
            warnings.append({"file": rel, "issue": "ipynb_parse_error", "detail": str(exc)})
            return

    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        grounded_nums.extend(extract_numbers_from_text(line, rel, f"{rel}:L{idx}", "code_default"))

    if fp.suffix.lower() in (".py", ".ipynb"):
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                ln = getattr(node, "lineno", 1)
                raw_line = lines[ln - 1].strip() if 1 <= ln <= len(lines) else ""
                loc = f"{rel}:L{ln}"

                # Top-level function steps
                if isinstance(node, ast.FunctionDef) and getattr(node, "col_offset", 1) == 0:
                    doc = ast.get_docstring(node) or ""
                    code_steps.append(
                        {
                            "function": node.name,
                            "summary": doc.splitlines()[0] if doc else "",
                            "source_locator": f"{rel}:L{ln}-L{getattr(node, 'end_lineno', ln)}",
                        }
                    )

                # argparse add_argument("--lr", default=1e-3)
                if isinstance(node, ast.Call):
                    func_name = ""
                    if isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr
                    elif isinstance(node.func, ast.Name):
                        func_name = node.func.id

                    if func_name == "add_argument" and node.args:
                        arg_names = []
                        for a in node.args:
                            ok, v = _ast_const_value(a)
                            if ok and isinstance(v, str) and v.startswith("-"):
                                arg_names.append(v.lstrip("-"))
                        if arg_names:
                            best_name = max(arg_names, key=len)
                            for kw in node.keywords:
                                if kw.arg == "default":
                                    ok, dv = _ast_const_value(kw.value)
                                    if ok and dv is not None:
                                        add_param(params, alias_lookup, best_name, dv, rel, loc, "code_default", raw_line, stage=infer_stage_from_context(best_name))

                    # Seed calls: manual_seed(42), seed(42), seed_everything(42)
                    elif func_name in ("manual_seed", "manual_seed_all", "seed", "seed_everything") and node.args:
                        ok, sv = _ast_const_value(node.args[0])
                        if ok:
                            add_param(params, alias_lookup, "random_seed", sv, rel, loc, "code_default", raw_line, stage="global")

                    # train_test_split / KFold / Optimizer keyword args
                    elif func_name in ("train_test_split", "KFold", "StratifiedKFold", "GroupKFold", "Adam", "AdamW", "SGD", "RMSprop", "DataLoader"):
                        for kw in node.keywords:
                            if kw.arg:
                                ok, kv = _ast_const_value(kw.value)
                                if ok and kv is not None:
                                    add_param(
                                        params,
                                        alias_lookup,
                                        kw.arg,
                                        kv,
                                        rel,
                                        loc,
                                        "code_default",
                                        raw_line,
                                        stage=infer_stage_from_context(func_name + " " + raw_line),
                                        target_object=func_name,
                                    )

                # Uppercase constants or simple config assignments
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    var = node.targets[0].id
                    ok, val = _ast_const_value(node.value)
                    if ok and (var.isupper() or canonicalize_param_key(var, alias_lookup) in set(alias_lookup.values())):
                        add_param(params, alias_lookup, var, val, rel, loc, "code_default", raw_line, stage=infer_stage_from_context(var))

        except SyntaxError as exc:
            warnings.append({"file": rel, "issue": "python_ast_syntax_fallback", "detail": str(exc)})

    excerpts.append({"file": rel, "category": "code_source", "excerpt": text[:2800]})


def extract_from_log(
    fp: Path,
    rel: str,
    alias_lookup: Dict[str, str],
    params: List[Dict[str, Any]],
    env_facts: List[Dict[str, Any]],
    grounded_nums: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    excerpts: List[Dict[str, Any]],
) -> None:
    text, warn, err = read_text_safe(fp)
    if err:
        warnings.append({"file": rel, "issue": warn or "read_error", "detail": err})
        return
    if warn:
        warnings.append({"file": rel, "issue": warn, "detail": f"Log read with warning: {warn}"})
    if not text.strip():
        return

    lines = text.splitlines()
    high_signal_lines: List[str] = []
    known_canonical = set(alias_lookup.values())
    mid_sampled = 0
    total_lines = len(lines)

    for idx, line in enumerate(lines, start=1):
        s = line.strip()
        if not s:
            continue
        loc = f"{rel}:L{idx}"
        low = s.lower()

        is_head_tail = idx <= 150 or idx > total_lines - 120
        is_priority_meta = any(
            k in low
            for k in (
                "config", "args", "namespace", "seed", "split", "gpu", "cuda", "python", "torch",
                "commit", "baseline", "final", "best", "summary", "macro-f1", "test ", "[eval]", "[cfg]", "[env]", "[data]",
            )
        )
        if not is_head_tail and not is_priority_meta:
            if mid_sampled >= 120:
                continue
            if not any(k in low for k in ("lr", "batch", "epoch", "metric", "f1", "acc", "rmse", "mae", "auc", "temp", "pressure", "flow", "error", "warning")):
                continue
            mid_sampled += 1

        grounded_nums.extend(extract_numbers_from_text(s, rel, loc, "runtime_log"))

        if len(high_signal_lines) < 90:
            high_signal_lines.append(f"L{idx}: {s[:240]}")

        # Environment & hardware facts
        for kind, pat in ENV_PATTERNS:
            for m in pat.finditer(s):
                env_facts.append({"kind": kind, "text": m.group(0).strip(), "source_locator": loc})

        # JSON line inside .jsonl or structured log
        if s.startswith("{") and s.endswith("}"):
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if isinstance(v, (int, float, str, bool)) and canonicalize_param_key(str(k), alias_lookup) in known_canonical:
                            add_param(params, alias_lookup, str(k), v, rel, loc, "runtime_log", s[:240], stage=infer_stage_from_context(str(k) + " " + s))
            except Exception:
                pass

        # Key=value / Namespace(...) / Effective config lines
        for m in KV_INLINE_RE.finditer(s):
            rk, rv = m.group(1), m.group(2).strip()
            ck = canonicalize_param_key(rk, alias_lookup)
            if ck in known_canonical:
                add_param(params, alias_lookup, rk, parse_scalar(rv), rel, loc, "runtime_log", s[:240], stage=infer_stage_from_context(rk + " " + s))

        # Explicit prose parameter patterns in logs
        for ck, pat in PROSE_PARAM_PATTERNS:
            for m in pat.finditer(s):
                val_str = next((g for g in m.groups() if g), None)
                if val_str:
                    add_param(params, alias_lookup, ck, parse_scalar(val_str), rel, loc, "runtime_log", s[:240], stage=infer_stage_from_context(s))

    excerpts.append({"file": rel, "category": "runtime_log", "excerpt": "\n".join(high_signal_lines)[:3200]})


def extract_from_table(
    fp: Path,
    rel: str,
    alias_lookup: Dict[str, str],
    params: List[Dict[str, Any]],
    tables: List[Dict[str, Any]],
    grounded_nums: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
) -> None:
    text, warn, err = read_text_safe(fp)
    if err:
        warnings.append({"file": rel, "issue": warn or "read_error", "detail": err})
        return
    if not text.strip():
        return

    delimiter = "\t" if fp.suffix.lower() == ".tsv" else ","
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except Exception as exc:
        warnings.append({"file": rel, "issue": "csv_parse_error", "detail": str(exc)})
        return

    if not rows:
        return
    headers = [h.strip() for h in rows[0]]
    data_rows = rows[1:]
    tables.append(
        {
            "file": rel,
            "headers": headers,
            "row_count": len(data_rows),
            "sample_rows": data_rows[:20],
        }
    )

    known_canonical = set(alias_lookup.values())
    low_headers = [h.lower() for h in headers]
    is_kv_sheet = any(h in low_headers for h in ("parameter", "param", "name", "key", "参数", "名称")) and any(
        h in low_headers for h in ("value", "val", "setting", "数值", "取值")
    )

    for r_idx, row in enumerate(data_rows[:300], start=2):
        loc = f"{rel}:L{r_idx}"
        joined = ", ".join(row)
        grounded_nums.extend(extract_numbers_from_text(joined, rel, loc, "data_table"))

        if is_kv_sheet and len(row) >= 2:
            # Find key, value, unit columns
            k_i = next((i for i, h in enumerate(low_headers) if h in ("parameter", "param", "name", "key", "参数", "名称")), 0)
            v_i = next((i for i, h in enumerate(low_headers) if h in ("value", "val", "setting", "数值", "取值")), 1)
            u_i = next((i for i, h in enumerate(low_headers) if h in ("unit", "units", "单位")), None)
            if k_i < len(row) and v_i < len(row):
                rk = row[k_i].strip()
                rv = row[v_i].strip()
                ru = row[u_i].strip() if (u_i is not None and u_i < len(row)) else ""
                if rk and rv:
                    add_param(params, alias_lookup, rk, parse_scalar(rv), rel, loc, "data_table", joined, unit=ru)
        else:
            for c_idx, cell in enumerate(row):
                if c_idx < len(headers):
                    ck = canonicalize_param_key(headers[c_idx], alias_lookup)
                    if ck in known_canonical and cell.strip():
                        add_param(params, alias_lookup, headers[c_idx], parse_scalar(cell.strip()), rel, loc, "data_table", joined)


def extract_from_document(
    fp: Path,
    rel: str,
    category: str,
    alias_lookup: Dict[str, str],
    params: List[Dict[str, Any]],
    env_facts: List[Dict[str, Any]],
    grounded_nums: List[Dict[str, Any]],
    doc_blocks: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    excerpts: List[Dict[str, Any]],
) -> None:
    ext = fp.suffix.lower()
    source_tier = category  # "paper_source" | "lab_note" | "narrative_draft"
    is_authoritative = source_tier in AUTHORITATIVE_TIERS

    blocks: List[Tuple[str, str]] = []  # (locator, text)
    if ext == ".pdf":
        pages, pdf_warns = parse_pdf_pages(fp)
        for pw in pdf_warns:
            warnings.append({"file": rel, "issue": "pdf_extraction_warning", "detail": pw})
        for pg in pages:
            pno = pg["page"]
            if pg["text"]:
                paras = [p.strip() for p in re.split(r"\n\s*\n", pg["text"]) if p.strip()]
                for bi, para in enumerate(paras, start=1):
                    blocks.append((f"{rel}:p{pno}_b{bi:02d}", para))
    elif ext == ".docx":
        dblocks, derr = parse_docx_paragraphs(fp)
        if derr:
            warnings.append({"file": rel, "issue": "docx_parse_warning", "detail": derr})
        for db in dblocks:
            blocks.append((f"{rel}:{db['block_id']}", db["text"]))
    else:
        text, warn, err = read_text_safe(fp)
        if err:
            warnings.append({"file": rel, "issue": warn or "read_error", "detail": err})
            return
        if warn:
            warnings.append({"file": rel, "issue": warn, "detail": f"Doc read with warning: {warn}"})
        if not text.strip():
            return
        lines = text.splitlines()
        # Group into line-anchored paragraphs
        start = 1
        buf: List[str] = []
        for idx, line in enumerate(lines, start=1):
            if line.strip():
                if not buf:
                    start = idx
                buf.append(line.strip())
            else:
                if buf:
                    loc = f"{rel}:L{start}" if start == idx - 1 else f"{rel}:L{start}-L{idx - 1}"
                    blocks.append((loc, "\n".join(buf)))
                    buf = []
        if buf:
            loc = f"{rel}:L{start}" if start == len(lines) else f"{rel}:L{start}-L{len(lines)}"
            blocks.append((loc, "\n".join(buf)))

    known_canonical = set(alias_lookup.values())
    excerpt_parts: List[str] = []

    for loc, btext in blocks:
        doc_blocks.append({"locator": loc, "file": rel, "source_tier": source_tier, "text": btext[:1500]})
        if len("\n".join(excerpt_parts)) < 3200:
            excerpt_parts.append(f"[{loc}] {btext[:500]}")

        if is_authoritative:
            grounded_nums.extend(extract_numbers_from_text(btext, rel, loc, source_tier))
            for kind, pat in ENV_PATTERNS:
                for m in pat.finditer(btext):
                    env_facts.append({"kind": kind, "text": m.group(0).strip(), "source_locator": loc})

        # Extract parameters mentioned in prose/notes/drafts
        for ck, pat in PROSE_PARAM_PATTERNS:
            for m in pat.finditer(btext):
                val_str = next((g for g in m.groups() if g), None)
                if val_str:
                    # Guard against bogus split_ratio matches like dates 2024/05/01
                    if ck == "split_ratio" and re.match(r"^(?:19|20)\d{2}", val_str.strip()):
                        continue
                    add_param(params, alias_lookup, ck, parse_scalar(val_str), rel, loc, source_tier, m.group(0).strip(), stage=infer_stage_from_context(btext))

        for m in KV_INLINE_RE.finditer(btext):
            rk, rv = m.group(1), m.group(2).strip()
            ck = canonicalize_param_key(rk, alias_lookup)
            if ck in known_canonical:
                add_param(params, alias_lookup, rk, parse_scalar(rv), rel, loc, source_tier, m.group(0).strip(), stage=infer_stage_from_context(btext))

    excerpts.append({"file": rel, "category": source_tier, "excerpt": "\n".join(excerpt_parts)[:3200]})


def detect_conflicts_and_draft_claims(
    params: List[Dict[str, Any]],
    alias_cfg: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Group parameters by canonical key, detect cross-artifact conflicts with precedence,
    and identify uncorroborated draft-only claims.
    """
    precedence = alias_cfg.get("source_precedence") or {}
    unit_conv = alias_cfg.get("unit_conversions") or {}
    known_canonical = set((alias_cfg.get("canonical_aliases") or {}).keys())

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in params:
        ck = p["canonical_key"]
        if ck:
            grouped[ck].append(p)

    conflicts: List[Dict[str, Any]] = []
    unsourced_draft: List[Dict[str, Any]] = []
    consolidated: List[Dict[str, Any]] = []

    conf_idx = 1
    for ck, items in sorted(grouped.items()):
        # Deduplicate identical (file, locator, raw_value)
        deduped: List[Dict[str, Any]] = []
        seen_k = set()
        for it in items:
            dk = (it["file"], it["source_locator"], str(it["raw_value"]))
            if dk not in seen_k:
                seen_k.add(dk)
                deduped.append(it)

        # Separate authoritative vs draft-only
        auth_items = [x for x in deduped if x["source_tier"] in AUTHORITATIVE_TIERS]
        draft_items = [x for x in deduped if x["source_tier"] == "narrative_draft"]

        if not auth_items and draft_items:
            for d in draft_items:
                unsourced_draft.append(
                    {
                        "canonical_key": ck,
                        "value": d["raw_value"],
                        "unit": d["unit"],
                        "source_locator": d["source_locator"],
                        "verbatim_quote": d["verbatim_quote"],
                        "reason": "draft_only_no_config_log_code_corroboration",
                    }
                )
            continue

        # Sort by precedence descending
        ranked = sorted(
            deduped,
            key=lambda x: (precedence.get(x["source_tier"], 10), x["file"]),
            reverse=True,
        )
        top = ranked[0]
        consolidated.append(
            {
                "canonical_key": ck,
                "recommended_value": top["raw_value"],
                "unit": top["unit"],
                "authoritative_locator": top["source_locator"],
                "authoritative_tier": top["source_tier"],
                "all_sources_count": len(ranked),
            }
        )

        # Only check conflicts when >= 2 distinct files report this parameter
        distinct_files = {x["file"] for x in ranked}
        if len(distinct_files) < 2:
            continue

        # File-level value-set disjointness check:
        # Multi-step SOPs naturally contain multiple valid durations/temperatures (e.g., 30 min stir, 12 h dry, 2 h calcine).
        # A file conflicts with the highest-precedence file ONLY if NONE of its values match ANY value in the authoritative file.
        top_file = ranked[0]["file"]
        top_file_items = [x for x in ranked if x["file"] == top_file]
        by_file: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for item in ranked:
            by_file[item["file"]].append(item)

        conflicting_files: List[List[Dict[str, Any]]] = []
        top_is_baseline = "baseline" in top_file.lower()
        for fname, f_items in by_file.items():
            if fname == top_file:
                continue
            if ("baseline" in fname.lower()) != top_is_baseline:
                continue
            has_overlap = any(
                values_equivalent(fi["value"], fi["unit"], ti["value"], ti["unit"], unit_conv)
                for fi in f_items
                for ti in top_file_items
            )
            if not has_overlap:
                conflicting_files.append(f_items)

        if conflicting_files:
            clusters: List[List[Dict[str, Any]]] = [top_file_items]
            for f_items in conflicting_files:
                rep_f = f_items[0]
                placed = False
                for cl in clusters[1:]:
                    if values_equivalent(rep_f["value"], rep_f["unit"], cl[0]["value"], cl[0]["unit"], unit_conv):
                        cl.extend(f_items)
                        placed = True
                        break
                if not placed:
                    clusters.append(list(f_items))

            top_tier = precedence.get(clusters[0][0]["source_tier"], 10)
            second_tier = max(precedence.get(cl[0]["source_tier"], 10) for cl in clusters[1:])
            conflict_kind = "same_tier_unresolved" if top_tier == second_tier else "higher_tier_override"

            sources_summary = []
            for cl in clusters:
                rep = cl[0]
                sources_summary.append(
                    {
                        "value": rep["raw_value"],
                        "unit": rep["unit"],
                        "source_tier": rep["source_tier"],
                        "source_locator": rep["source_locator"],
                        "verbatim_quote": rep["verbatim_quote"],
                    }
                )

            conflicts.append(
                {
                    "conflict_id": f"CONF-{conf_idx:02d}",
                    "canonical_key": ck,
                    "conflict_kind": conflict_kind,
                    "severity": "critical" if conflict_kind == "same_tier_unresolved" else "important",
                    "recommended_authoritative": {
                        "value": clusters[0][0]["raw_value"],
                        "unit": clusters[0][0]["unit"],
                        "source_tier": clusters[0][0]["source_tier"],
                        "source_locator": clusters[0][0]["source_locator"],
                    },
                    "sources_compared": sources_summary,
                    "resolution_guidance": (
                        "Same-tier conflict: do NOT silently pick one; present both values with locators and add a critical author-confirmation question."
                        if conflict_kind == "same_tier_unresolved"
                        else f"Higher-tier '{clusters[0][0]['source_tier']}' overrides lower-tier source(s); adopt authoritative value, record in change_records & conflicts, and ask author to confirm."
                    ),
                }
            )
            conf_idx += 1

    return conflicts, unsourced_draft, consolidated


def run_six_pillar_preaudit(
    domain: str,
    rubric: Dict[str, Any],
    all_auth_text: str,
    params: List[Dict[str, Any]],
    commands: List[Dict[str, Any]],
    env_facts: List[Dict[str, Any]],
    language: str,
) -> List[Dict[str, Any]]:
    """Evaluate domain-specific 6-pillar slots against authoritative extracted text."""
    slots = (rubric.get("domain_slots") or {}).get(domain) or (rubric.get("domain_slots") or {}).get("general_scientific") or []
    corpus = (
        all_auth_text
        + "\n"
        + " ".join(f"{p['raw_key']} {p['canonical_key']} {p['raw_value']}" for p in params if p["source_tier"] in AUTHORITATIVE_TIERS)
        + "\n"
        + " ".join(c["command"] for c in commands)
        + "\n"
        + " ".join(e["text"] for e in env_facts)
    ).lower()

    gaps: List[Dict[str, Any]] = []
    gap_idx = 1
    for slot in slots:
        hits = [kw for kw in slot.get("keywords", []) if kw.lower() in corpus]
        if len(hits) < 2:
            gaps.append(
                {
                    "gap_id": f"GAP-{gap_idx:02d}",
                    "slot_id": slot["slot_id"],
                    "pillar": slot["pillar"],
                    "severity": slot["severity"],
                    "missing_item": slot["name"],
                    "evidence_hits_found": hits,
                    "suggested_question": slot["question_en"] if language == "en" else slot["question_zh"],
                }
            )
            gap_idx += 1
    return gaps


def render_fact_digest_md(
    case_plan: Dict[str, Any],
    ledger: Dict[str, Any],
) -> str:
    """Build a compact, high-signal Markdown digest for the writing Agent."""
    lines: List[str] = []
    lines.append(f"# Fact Digest & Evidence Ledger (`{case_plan['case_id']}`)")
    lines.append("")
    sig = case_plan["signals"]
    lines.append(f"- **Detected Domain**: `{sig['detected_domain']}` | **Task Mode**: `{sig['task_mode']}` | **Language**: `{sig['language']}`")
    lines.append(f"- **Requested Formats**: `{', '.join(sig['requested_formats'])}`")
    req_keys = case_plan["output_contract"].get("required_keys") or []
    lines.append(f"- **`output_contract` Required Keys**: `{req_keys}`")
    lines.append(f"- **Authoritative Materials Count**: {len(case_plan['file_inventory'])} ({case_plan['file_counts_by_category']})")
    lines.append("")

    if case_plan.get("missing_referenced_files") or ledger.get("ingestion_warnings"):
        lines.append("## 0. Missing Files & Ingestion Warnings (Handle Honestly — Never Fabricate)")
        for mf in case_plan.get("missing_referenced_files", []):
            lines.append(f"- **[MISSING_FILE]** Referenced in task/case but absent from `/input/`: `{mf}`")
        for iw in ledger.get("ingestion_warnings", []):
            lines.append(f"- **[{iw['issue']}]** `{iw['file']}`: {iw['detail']}")
        lines.append("")

    lines.append("## 1. Cross-Artifact Conflicts Detected (Must Disclose in `conflicts` & `uncertainty`)")
    if ledger["conflicts"]:
        for c in ledger["conflicts"]:
            comp = "; ".join(f"`{s['source_locator']}` ({s['source_tier']}) = `{s['value']}`" for s in c["sources_compared"])
            lines.append(f"- **{c['conflict_id']}** (`{c['canonical_key']}`, `{c['conflict_kind']}`): {comp}")
            lines.append(f"  - Guidance: {c['resolution_guidance']}")
    else:
        lines.append("- None automatically detected.")
    lines.append("")

    lines.append("## 2. Uncorroborated Draft-Only Claims (Do Not Present as Verified Fact)")
    if ledger["unsourced_draft_claims"]:
        for u in ledger["unsourced_draft_claims"]:
            lines.append(f"- `{u['canonical_key']}` = `{u['value']}` in `{u['source_locator']}` (`{u['verbatim_quote']}`) — no config/log/code backing; tag `[待作者确认]`.")
    else:
        lines.append("- None detected.")
    lines.append("")

    lines.append("## 3. Preliminary 6-Pillar Gap Pre-Audit")
    if ledger["preliminary_gaps"]:
        for g in ledger["preliminary_gaps"]:
            lines.append(f"- **{g['gap_id']}** [{g['severity']}] (`{g['pillar']}`): {g['missing_item']} -> _{g['suggested_question']}_")
    else:
        lines.append("- All domain rubric slots have at least initial evidence hits; verify depth during drafting.")
    lines.append("")

    lines.append("## 4. Extracted Authoritative Parameters (Ranked by Precedence)")
    auth_params = [p for p in ledger["parameters"] if p["source_tier"] in AUTHORITATIVE_TIERS]
    if auth_params:
        lines.append("| Canonical Key | Raw Key | Value | Unit | Stage | Source Locator | Tier |")
        lines.append("|---|---|---|---|---|---|---|")
        for p in auth_params[:80]:
            lines.append(
                f"| `{p['canonical_key']}` | `{p['raw_key']}` | `{p['raw_value']}` | `{p['unit']}` | `{p['stage']}` | `{p['source_locator']}` | `{p['source_tier']}` |"
            )
    else:
        lines.append("- No structured parameters automatically extracted.")
    lines.append("")

    if ledger["commands"]:
        lines.append("## 5. Extracted CLI / Reproduction Commands")
        for c in ledger["commands"][:20]:
            lines.append(f"- `{c['source_locator']}`: `{c['command']}`")
        lines.append("")

    if ledger["environment_facts"]:
        lines.append("## 6. Hardware, Software, Instruments & Commit Anchors")
        seen_e = set()
        for e in ledger["environment_facts"]:
            k = (e["kind"], e["text"])
            if k in seen_e:
                continue
            seen_e.add(k)
            lines.append(f"- [{e['kind']}] `{e['source_locator']}`: {e['text']}")
            if len(seen_e) >= 35:
                break
        lines.append("")

    if ledger["code_steps"]:
        lines.append("## 7. Code Functions / Pipeline Structure")
        for cs in ledger["code_steps"][:30]:
            lines.append(f"- `{cs['source_locator']}`: `def {cs['function']}()` — {cs['summary']}")
        lines.append("")

    if ledger["tables"]:
        lines.append("## 8. Structured CSV/TSV Data & Metric Tables")
        for tb in ledger["tables"][:8]:
            lines.append(f"### `{tb['file']}` ({tb['row_count']} rows)")
            lines.append("Headers: `" + " | ".join(tb["headers"]) + "`")
            for row in tb["sample_rows"][:10]:
                lines.append("  - " + " | ".join(row))
        lines.append("")

    lines.append("## 9. Source File Excerpts (Anchored)")
    for ex in ledger["excerpts"]:
        lines.append(f"### `{ex['file']}` (`{ex['category']}`)")
        lines.append("```text")
        lines.append(ex["excerpt"][:2400])
        lines.append("```")
        lines.append("")

    digest = "\n".join(lines)
    if len(digest) > 32000:
        digest = digest[:31500] + "\n\n... [DIGEST BOUNDED AT 32KB; SEE /app/work/fact_ledger.json FOR COMPLETE INDEX]\n"
    return digest


def _infer_inline_tier(key_path: str) -> str:
    low = key_path.lower()
    if any(w in low for w in ("log", "stdout", "stderr", "run_output", "trace", "eval_out")):
        return "runtime_log"
    if any(w in low for w in ("cmd", "command", "script", "cli", "shell")):
        return "cli_script"
    if any(w in low for w in ("config", "hyperparam", "param", "setting", "yaml", "toml", "args")):
        return "exp_config"
    if any(w in low for w in ("draft", "manuscript", "rough", "chapter", "初稿", "底稿", "原文稿")):
        return "narrative_draft"
    if any(w in low for w in ("paper", "article", "pdf", "reference", "原文")):
        return "paper_source"
    return "lab_note"


def extract_from_inline_case_and_task(
    plan: Dict[str, Any],
    alias_lookup: Dict[str, str],
    params: List[Dict[str, Any]],
    commands: List[Dict[str, Any]],
    env_facts: List[Dict[str, Any]],
    grounded_nums: List[Dict[str, Any]],
    doc_blocks: List[Dict[str, Any]],
    excerpts: List[Dict[str, Any]],
) -> int:
    """Extract embedded scientific materials/configs/logs/drafts directly inside case.json or task.md."""
    known_canonical = set(alias_lookup.values())
    has_external_files = len(plan.get("file_inventory") or []) > 0
    skip_top = {"case_id", "id", "task_id", "title", "language", "genre", "audience", "style", "venue", "referenced_files"}
    if has_external_files:
        skip_top |= {"task", "instruction", "instructions", "prompt", "requirements", "constraints"}
    inline_blocks = 0

    def process_text_blob(rel_name: str, loc: str, tier: str, text: str) -> None:
        nonlocal inline_blocks
        s = (text or "").strip()
        if len(s) < 15:
            return
        inline_blocks += 1
        doc_blocks.append({"locator": loc, "file": rel_name, "source_tier": tier, "text": s[:2000]})
        excerpts.append({"file": loc, "category": tier, "excerpt": s[:2400]})
        is_auth = tier in AUTHORITATIVE_TIERS
        if is_auth:
            grounded_nums.extend(extract_numbers_from_text(s, rel_name, loc, tier))
            for kind, pat in ENV_PATTERNS:
                for m in pat.finditer(s):
                    env_facts.append({"kind": kind, "text": m.group(0).strip(), "source_locator": loc})
        if tier == "cli_script":
            for ln in s.splitlines():
                ls = ln.strip()
                if any(tok in ls for tok in ("python", "torchrun", "bash", "lmp", "mpirun", "srun")):
                    commands.append({"command": ls, "source_locator": loc})
                for m in re.finditer(r"--([A-Za-z0-9][A-Za-z0-9_\-]{0,35})(?:=|\s+)([^\s;|&\"']+)", ls):
                    add_param(params, alias_lookup, m.group(1), parse_scalar(m.group(2)), rel_name, loc, "cli_script", m.group(0), stage=infer_stage_from_context(ls))
        for ck, pat in PROSE_PARAM_PATTERNS:
            for m in pat.finditer(s):
                val_str = next((g for g in m.groups() if g), None)
                if val_str and not (ck == "split_ratio" and re.match(r"^(?:19|20)\d{2}", val_str.strip())):
                    add_param(params, alias_lookup, ck, parse_scalar(val_str), rel_name, loc, tier, m.group(0).strip(), stage=infer_stage_from_context(s))
        for m in KV_INLINE_RE.finditer(s):
            rk, rv = m.group(1), m.group(2).strip()
            ck = canonicalize_param_key(rk, alias_lookup)
            if ck in known_canonical:
                add_param(params, alias_lookup, rk, parse_scalar(rv), rel_name, loc, tier, m.group(0).strip(), stage=infer_stage_from_context(rk + " " + s))

    def walk_meta(obj: Any, path: str) -> None:
        if isinstance(obj, dict):
            # If dict looks like a material card {name/path, content/text, type/kind}
            if any(k in obj for k in ("content", "text", "body")) and isinstance(obj.get("content") or obj.get("text") or obj.get("body"), str):
                name_hint = str(obj.get("name") or obj.get("filename") or obj.get("path") or obj.get("type") or path)
                tier = _infer_inline_tier(f"{path} {name_hint}")
                body = str(obj.get("content") or obj.get("text") or obj.get("body"))
                process_text_blob(f"case.json::{path}", f"case.json::{path}", tier, body)
                return
            for k, v in obj.items():
                if not path and k in skip_top:
                    continue
                sub = f"{path}.{k}" if path else str(k)
                if isinstance(v, (int, float, bool)):
                    tier = _infer_inline_tier(sub)
                    loc = f"case.json::{sub}"
                    add_param(params, alias_lookup, sub, v, f"case.json::{path or k}", loc, tier, f"{k}: {v}", stage=infer_stage_from_context(sub))
                    if tier in AUTHORITATIVE_TIERS:
                        grounded_nums.extend(extract_numbers_from_text(f"{k}: {v}", f"case.json::{path or k}", loc, tier))
                elif isinstance(v, str):
                    tier = _infer_inline_tier(sub)
                    if len(v.strip()) >= 35 or "\n" in v:
                        process_text_blob(f"case.json::{sub}", f"case.json::{sub}", tier, v)
                    elif canonicalize_param_key(k, alias_lookup) in known_canonical:
                        loc = f"case.json::{sub}"
                        add_param(params, alias_lookup, sub, parse_scalar(v), f"case.json::{path or k}", loc, tier, f"{k}: {v}", stage=infer_stage_from_context(sub))
                        if tier in AUTHORITATIVE_TIERS:
                            grounded_nums.extend(extract_numbers_from_text(f"{k}: {v}", f"case.json::{path or k}", loc, tier))
                else:
                    walk_meta(v, sub)
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                sub = f"{path}[{idx}]"
                if isinstance(item, str) and len(item.strip()) >= 25:
                    process_text_blob(f"case.json::{sub}", f"case.json::{sub}", _infer_inline_tier(path), item)
                else:
                    walk_meta(item, sub)

    walk_meta(plan.get("case_metadata") or {}, "")

    # If external materials are sparse/absent, also index task.md content as lab_note evidence
    task_excerpt = str(plan.get("task_md_excerpt") or "").strip()
    if task_excerpt and (len(plan.get("file_inventory") or []) == 0 or inline_blocks == 0):
        # Always index grounded numbers in task.md when no standalone files exist
        if len(plan.get("file_inventory") or []) == 0:
            process_text_blob("task.md", "task.md:L1", "lab_note", task_excerpt)

    return inline_blocks


def extract_facts(
    case_plan_path: Path,
    out_ledger_path: Path,
    out_digest_path: Path,
    out_skeleton_path: Optional[Path] = None,
) -> Dict[str, Any]:
    plan, err = load_json_safe(case_plan_path)
    if err or not isinstance(plan, dict):
        raise SystemExit(f"Failed loading case_plan.json: {err}")

    alias_cfg = load_resource_json("parameter_aliases.json")
    rubric = load_resource_json("six_pillar_rubric.json")
    alias_lookup = build_alias_lookup(alias_cfg)

    params: List[Dict[str, Any]] = []
    commands: List[Dict[str, Any]] = []
    env_facts: List[Dict[str, Any]] = []
    code_steps: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    doc_blocks: List[Dict[str, Any]] = []
    grounded_nums: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    excerpts: List[Dict[str, Any]] = []

    for aw in plan.get("access_warnings", []):
        warnings.append({"file": "input_dir", "issue": "access_warning", "detail": aw})

    for item in plan.get("file_inventory", []):
        fp = Path(item["abs_path"])
        rel = item["rel_path"]
        cat = item["category"]

        if cat == "binary_or_other":
            warnings.append({"file": rel, "issue": "binary_file_skipped", "detail": f"Binary/non-text artifact ({item['size_bytes']} bytes); metadata recorded only."})
            continue
        if cat == "exp_config":
            extract_from_config(fp, rel, alias_lookup, params, grounded_nums, warnings, excerpts)
        elif cat == "cli_script":
            extract_from_shell(fp, rel, alias_lookup, params, commands, grounded_nums, warnings, excerpts)
        elif cat == "code_source":
            extract_from_code(fp, rel, alias_lookup, params, code_steps, grounded_nums, warnings, excerpts)
        elif cat == "runtime_log":
            extract_from_log(fp, rel, alias_lookup, params, env_facts, grounded_nums, warnings, excerpts)
        elif cat == "data_table":
            extract_from_table(fp, rel, alias_lookup, params, tables, grounded_nums, warnings)
        elif cat in ("paper_source", "lab_note", "narrative_draft"):
            extract_from_document(fp, rel, cat, alias_lookup, params, env_facts, grounded_nums, doc_blocks, warnings, excerpts)

    # Also harvest any inline materials/configs/logs/drafts embedded directly in case.json or task.md
    inline_blocks = extract_from_inline_case_and_task(
        plan, alias_lookup, params, commands, env_facts, grounded_nums, doc_blocks, excerpts
    )

    # Deduplicate params & grounded_nums to bound ledger size and O(M^2) checks on huge logs
    dedup_params: List[Dict[str, Any]] = []
    seen_p_keys = set()
    for p in params:
        pk = (p.get("file"), p.get("canonical_key"), str(p.get("raw_value")), p.get("unit"), p.get("stage"))
        if pk not in seen_p_keys:
            seen_p_keys.add(pk)
            dedup_params.append(p)
        if len(dedup_params) >= 600:
            break
    params = dedup_params

    dedup_gnums: List[Dict[str, Any]] = []
    seen_g_keys = set()
    for gn in grounded_nums:
        gk = (round(float(gn["value"]), 6), str(gn.get("unit") or "").lower(), gn.get("source_tier"))
        if gk not in seen_g_keys:
            seen_g_keys.add(gk)
            dedup_gnums.append(gn)
        if len(dedup_gnums) >= 3500:
            break
    grounded_nums = dedup_gnums

    conflicts, unsourced_draft, consolidated = detect_conflicts_and_draft_claims(params, alias_cfg)

    # Authoritative text corpus for 6-pillar pre-audit
    auth_text = "\n".join(
        ex["excerpt"] for ex in excerpts if ex["category"] in AUTHORITATIVE_TIERS
    ) + "\n" + "\n".join(
        " ".join(tb["headers"]) + " " + " ".join(" ".join(r) for r in tb["sample_rows"])
        for tb in tables
    )
    preliminary_gaps = run_six_pillar_preaudit(
        plan["signals"]["detected_domain"],
        rubric,
        auth_text,
        params,
        commands,
        env_facts,
        plan["signals"]["language"],
    )

    valid_files = sorted({item["rel_path"] for item in plan.get("file_inventory", [])})
    valid_locators = sorted(
        {p["source_locator"] for p in params}
        | {c["source_locator"] for c in commands}
        | {e["source_locator"] for e in env_facts}
        | {d["locator"] for d in doc_blocks}
        | {n["locator"] for n in grounded_nums}
    )

    ledger = {
        "case_id": plan["case_id"],
        "detected_domain": plan["signals"]["detected_domain"],
        "task_mode": plan["signals"]["task_mode"],
        "language": plan["signals"]["language"],
        "valid_files": valid_files,
        "valid_locators_count": len(valid_locators),
        "valid_locators_sample": valid_locators[:500],
        "consolidated_parameters": consolidated,
        "parameters": params,
        "conflicts": conflicts,
        "unsourced_draft_claims": unsourced_draft,
        "preliminary_gaps": preliminary_gaps,
        "commands": commands,
        "environment_facts": env_facts,
        "code_steps": code_steps,
        "tables": tables,
        "doc_blocks": doc_blocks,
        "grounded_numbers": grounded_nums,
        "ingestion_warnings": warnings,
        "excerpts": excerpts,
        "stats": {
            "total_files": len(valid_files),
            "authoritative_files": sum(1 for x in plan.get("file_inventory", []) if x["category"] in AUTHORITATIVE_TIERS),
            "inline_material_blocks": inline_blocks,
            "total_extracted_params": len(params),
            "conflicts_detected": len(conflicts),
            "unsourced_draft_claims": len(unsourced_draft),
            "preliminary_gaps": len(preliminary_gaps),
            "grounded_numbers_count": len(grounded_nums),
            "ingestion_warnings_count": len(warnings),
        },
    }

    save_json(out_ledger_path, ledger)
    out_digest_path.parent.mkdir(parents=True, exist_ok=True)
    out_digest_path.write_text(render_fact_digest_md(plan, ledger), encoding="utf-8")

    # Emit pre-grounded chapter_payload_skeleton.json so the LLM avoids hand-typing boilerplate & missing conflicts
    skel_path = out_skeleton_path if out_skeleton_path else (out_digest_path.parent / "chapter_payload_skeleton.json")
    conf_key_set = {c["canonical_key"] for c in conflicts}
    skel_params: List[Dict[str, Any]] = []
    for idx, cp in enumerate(consolidated[:60], start=1):
        ck = cp["canonical_key"]
        skel_params.append(
            {
                "param_id": f"P{idx:02d}",
                "name": ck,
                "symbol_or_key": ck,
                "canonical_key": ck,
                "value": cp["recommended_value"],
                "unit": cp["unit"] or "",
                "target_object": "",
                "stage": "unspecified",
                "source_locator": cp["authoritative_locator"],
                "source_type": cp["authoritative_tier"],
                "verification_status": "conflict_resolved_with_note" if ck in conf_key_set else "verified",
                "verbatim_quote": "",
                "note": "Pre-populated from highest-precedence authoritative artifact.",
            }
        )
    for ud in unsourced_draft[:20]:
        idx = len(skel_params) + 1
        skel_params.append(
            {
                "param_id": f"P{idx:02d}",
                "name": ud["canonical_key"],
                "symbol_or_key": ud.get("raw_key") or ud["canonical_key"],
                "canonical_key": ud["canonical_key"],
                "value": ud["value"],
                "unit": ud.get("unit") or "",
                "target_object": "",
                "stage": "unspecified",
                "source_locator": ud["source_locator"],
                "source_type": "narrative_draft",
                "verification_status": "draft_only_unverified",
                "verbatim_quote": ud.get("verbatim_quote") or "",
                "note": "Draft-only uncorroborated value; tag with [待作者确认] or remove.",
            }
        )
    skel_uncertainty: List[Dict[str, str]] = []
    for mf in plan.get("missing_referenced_files", []):
        skel_uncertainty.append({"target": mf, "reason": "missing_file", "details": f"Referenced file '{mf}' missing under /input/.", "human_handoff": f"Supply '{mf}'."})
    for iw in warnings:
        skel_uncertainty.append({"target": iw.get("file", ""), "reason": str(iw.get("issue", "ingestion_warning")), "details": str(iw.get("detail", "")), "human_handoff": "Check or replace unreadable input artifact."})
    for c in conflicts:
        skel_uncertainty.append({"target": f"{c['conflict_id']}:{c['canonical_key']}", "reason": "conflict_between_materials", "details": c["resolution_guidance"], "human_handoff": "Confirm authoritative parameter selection."})

    skeleton = {
        "case_id": plan["case_id"],
        "task_mode": plan["signals"]["task_mode"],
        "language": plan["signals"]["language"],
        "domain": plan["signals"]["detected_domain"],
        "title": str((plan.get("case_metadata") or {}).get("title") or "Methods and Experiments"),
        "manuscript_markdown": "",
        "method_steps": [],
        "experimental_setup": {
            "training_or_preparation_protocol": "",
            "validation_or_characterization_protocol": "",
            "testing_or_evaluation_protocol": "",
            "stage_separation_verified": True,
            "hardware_or_instruments": [{"name": e["text"], "source_locator": e["source_locator"]} for e in env_facts if e["kind"] in ("gpu_hardware", "cpu_hardware", "instrument")][:10],
            "software_or_firmware": [{"name": e["text"], "source_locator": e["source_locator"]} for e in env_facts if e["kind"] in ("software_version", "git_commit")][:10],
            "random_seeds": [],
            "repeat_counts": "",
            "source_locators": [],
        },
        "parameter_table": skel_params,
        "datasets_and_splits": [],
        "baseline_comparisons": [],
        "reproducibility_checklist": {
            "code_repository_and_version": "",
            "environment_specification": "",
            "reproduction_commands": [c["command"] for c in commands[:10]],
            "data_or_material_access": "",
            "random_seeds_and_determinism": "",
            "expected_compute_or_resources": "",
            "missing_items": [],
            "source_locators": [c["source_locator"] for c in commands[:10]],
        },
        "limitations": [],
        "conflicts": [
            {
                "conflict_id": c["conflict_id"],
                "item": c["canonical_key"],
                "sources_compared": c["sources_compared"],
                "adopted_value_or_status": f"Adopted {c['recommended_authoritative']['value']} {c['recommended_authoritative']['unit']} ({c['recommended_authoritative']['source_locator']})".strip(),
                "rationale": c["resolution_guidance"],
                "needs_author_confirmation": True,
            }
            for c in conflicts
        ],
        "author_confirmation_items": [
            {
                "gap_id": g["gap_id"],
                "pillar": g["pillar"],
                "severity": g["severity"],
                "location_in_draft": "Methods and Experiments",
                "issue_type": "missing_in_materials",
                "question": g["suggested_question"],
                "what_was_checked": f"Scanned {len(valid_files)} input file(s).",
                "blocking_reproducibility": g["severity"] == "critical",
            }
            for g in preliminary_gaps
        ],
        "sources": [
            {"claim_or_item": f"{cp['canonical_key']}={cp['recommended_value']}", "source_locator": cp["authoritative_locator"], "source_type": cp["authoritative_tier"], "verbatim_quote": ""}
            for cp in consolidated[:30]
        ],
        "uncertainty": skel_uncertainty,
        "change_records": [],
        "contract_overrides": {},
    }
    save_json(skel_path, skeleton)
    return ledger


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract anchored Fact Ledger and detect cross-source conflicts & 6-pillar gaps.")
    parser.add_argument("--case-plan", default="/app/work/case_plan.json", help="Path to case_plan.json from inspect_case.py")
    parser.add_argument("--out-ledger", default="/app/work/fact_ledger.json", help="Output path for fact_ledger.json")
    parser.add_argument("--out-digest", default="/app/work/fact_digest.md", help="Output path for compact fact_digest.md")
    parser.add_argument("--out-skeleton", default=None, help="Optional output path for chapter_payload_skeleton.json")
    args = parser.parse_args()

    out_dig = Path(args.out_digest)
    out_skel = Path(args.out_skeleton) if args.out_skeleton else (out_dig.parent / "chapter_payload_skeleton.json")
    ledger = extract_facts(Path(args.case_plan), Path(args.out_ledger), out_dig, out_skel)
    print(json.dumps({"case_id": ledger["case_id"], "stats": ledger["stats"], "conflicts": [c["conflict_id"] + ":" + c["canonical_key"] for c in ledger["conflicts"]], "skeleton": str(out_skel)}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
