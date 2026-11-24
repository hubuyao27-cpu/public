#!/usr/bin/env python3
"""Step 1: Inspect Harbor /input/case.json, task.md, and all materials under /input.

Produces /app/work/case_plan.json with:
- case_id and parsed output_contract fields (with semantic roles & expected types)
- task_mode, target_language, requested output formats, audience/venue hints
- content-grounded discipline hint (never blindly assumed from labels)
- full file inventory categorized by evidence tier + missing referenced files
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import load_json_safe, posix_rel, read_text_safe, save_json

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}
CONTROL_FILES = {"case.json", "task.md", "case_plan.json", "result.json"}

CONFIG_EXTS = {".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env"}
CODE_EXTS = {".py", ".r", ".m", ".jl", ".cpp", ".c", ".h", ".cu", ".java", ".js", ".ts", ".ipynb"}
SHELL_EXTS = {".sh", ".bash", ".zsh", ".bat", ".ps1", ".slurm", ".sbatch"}
LOG_EXTS = {".log", ".out", ".err", ".jsonl"}
TABLE_EXTS = {".csv", ".tsv"}
PAPER_EXTS = {".pdf", ".tex", ".bib"}
DOC_EXTS = {".md", ".markdown", ".txt", ".docx", ".rst"}
BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pt", ".pth", ".ckpt", ".bin", ".h5", ".npy", ".npz", ".zip", ".tar", ".gz", ".whl", ".exe", ".dll", ".so"}


def infer_semantic_role(field_name: str, desc: str = "") -> str:
    """Map an arbitrary output_contract field key to a canonical semantic role."""
    k = f"{field_name} {desc}".lower()
    if any(w in k for w in ("case_id", "caseid", "task_id")):
        return "case_id"
    if any(w in k for w in ("status", "run_status", "execution_status")):
        return "status"
    if any(w in k for w in ("manuscript", "chapter", "draft", "revised_text", "methods_section", "methods_and_experiments", "text", "markdown", "article", "content", "writing", "output_text", "paper_section")):
        return "manuscript_markdown"
    if any(w in k for w in ("param", "hyperparam", "config_table", "setting_table")):
        return "parameter_table"
    if any(w in k for w in ("step", "procedure", "protocol", "workflow", "algorithm")):
        return "method_steps"
    if any(w in k for w in ("setup", "environment", "hardware", "software", "runtime")):
        return "experimental_setup"
    if any(w in k for w in ("dataset", "split", "sample", "material", "reagent", "corpus")):
        return "datasets_and_splits"
    if any(w in k for w in ("baseline", "control", "comparison", "ablation", "benchmark")):
        return "baseline_comparisons"
    if any(w in k for w in ("reproduc", "checklist", "artifact_access")):
        return "reproducibility_checklist"
    if any(w in k for w in ("limitation", "failure", "boundary", "caveat", "scope")):
        return "limitations"
    if any(w in k for w in ("conflict", "discrepanc", "inconsisten", "mismatch")):
        return "conflicts"
    if any(w in k for w in ("confirm", "question", "gap", "missing", "todo_author", "open_issue", "human_confirm")):
        return "author_confirmation_items"
    if any(w in k for w in ("change", "diff", "revision", "edit_log", "modification")):
        return "change_records"
    if any(w in k for w in ("uncertain", "unknown", "warning", "risk", "manual_takeover")):
        return "uncertainty"
    if any(w in k for w in ("source", "provenance", "evidence", "citation", "mapping", "traceability", "fact_map")):
        return "sources"
    if any(w in k for w in ("file", "artifact", "deliverable", "preview", "path")):
        return "deliverable_files"
    if any(w in k for w in ("audit", "verif", "metric", "check")):
        return "verification_summary"
    if any(w in k for w in ("summary", "overview", "abstract")):
        return "summary"
    return "custom"


def parse_output_contract(contract: Any) -> Dict[str, Any]:
    """Normalize any output_contract shape (JSON Schema, dict template, list) into structured specs."""
    fields: List[Dict[str, Any]] = []
    if contract is None:
        return {"raw_type": "none", "fields": [], "required_keys": []}

    if isinstance(contract, dict):
        # Shape A: JSON Schema with properties
        if isinstance(contract.get("properties"), dict):
            req_set = set(contract.get("required") or [])
            for k, spec in contract["properties"].items():
                sd = spec if isinstance(spec, dict) else {"example": spec}
                ftype = str(sd.get("type", type(spec).__name__))
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

        # Shape B: nested "fields" dict or list
        if "fields" in contract and isinstance(contract["fields"], (dict, list)):
            return parse_output_contract(contract["fields"])

        # Shape C: direct key -> spec/example dict
        meta_keys = {"type", "description", "format", "required", "$schema", "title"}
        req_list = contract.get("required") if isinstance(contract.get("required"), list) else None
        for k, val in contract.items():
            if k in meta_keys and len(contract) > 1:
                continue
            if isinstance(val, dict) and ("type" in val or "description" in val):
                ftype = str(val.get("type", "object"))
                desc = str(val.get("description", ""))
                req = bool(val.get("required", True))
            else:
                ftype = type(val).__name__ if val is not None else "any"
                desc = val if isinstance(val, str) else ""
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
                            "semantic_role": infer_semantic_role(rk),
                        }
                    )
        return {
            "raw_type": "dict_template",
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
                        "semantic_role": infer_semantic_role(item),
                    }
                )
            elif isinstance(item, dict) and ("name" in item or "key" in item):
                name = str(item.get("name") or item.get("key"))
                desc = str(item.get("description", ""))
                fields.append(
                    {
                        "name": name,
                        "expected_type": str(item.get("type", "any")),
                        "required": bool(item.get("required", True)),
                        "description": desc,
                        "semantic_role": infer_semantic_role(name, desc),
                    }
                )
        return {
            "raw_type": "list",
            "fields": fields,
            "required_keys": [f["name"] for f in fields if f["required"]],
        }

    return {"raw_type": type(contract).__name__, "fields": [], "required_keys": []}


def classify_file(path: Path, rel: str) -> str:
    """Classify an input file into its authoritative evidence tier."""
    name_low = path.name.lower()
    rel_low = rel.lower()
    ext = path.suffix.lower()

    if name_low in CONTROL_FILES:
        return "control"
    if ext in BINARY_EXTS:
        return "binary_or_other"
    if ext in SHELL_EXTS or name_low in ("makefile", "dockerfile", "run", "train.sh", "eval.sh"):
        return "cli_script"
    if ext in LOG_EXTS or (ext == ".txt" and any(w in rel_low for w in ("log", "stdout", "stderr", "output", "trace", "eval", "train", "metric", "run"))):
        return "runtime_log"
    if ext in CONFIG_EXTS or (ext == ".json" and not any(w in name_low for w in ("case", "result"))):
        return "exp_config"
    if ext in CODE_EXTS:
        return "code_source"
    if ext in TABLE_EXTS:
        return "data_table"
    if ext in PAPER_EXTS:
        return "paper_source"
    if ext in DOC_EXTS:
        if any(w in rel_low for w in ("draft", "manuscript", "section", "chapter", "methods_draft", "初稿", "底稿", "稿件")):
            return "narrative_draft"
        if any(w in rel_low for w in ("readme", "data", "dataset", "note", "lab", "protocol", "sop", "experiment", "record", "说明", "记录", "实验")):
            return "lab_note"
        return "narrative_draft"
    return "lab_note"


def detect_task_signals(task_text: str, case_data: Dict[str, Any], sample_texts: List[str]) -> Dict[str, Any]:
    """Detect language, task action mode, requested formats, and content-grounded domain."""
    signal_meta = {k: v for k, v in case_data.items() if k not in ("case_id", "id", "task_id", "output_contract", "output_schema")}
    combined_task = f"{task_text}\n{json.dumps(signal_meta, ensure_ascii=False)}"
    low_task = combined_task.lower()

    # Target language
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", task_text or ""))
    en_words = len(re.findall(r"[A-Za-z]{3,}", task_text or ""))
    if any(k in low_task for k in ("bilingual", "双语", "中英对照", "chinese and english")):
        language = "bilingual"
    elif any(k in low_task for k in ("write in english", "english manuscript", "英文撰写", "用英文")):
        language = "en"
    elif any(k in low_task for k in ("用中文", "中文撰写", "中文输出")) or zh_chars >= 40:
        language = "zh"
    elif en_words > zh_chars * 2:
        language = "en"
    else:
        language = "zh"

    # Task mode
    if any(k in low_task for k in ("revise", "rewrite", "align draft", "修改", "改写", "修订", "润色", "对齐底稿")):
        task_mode = "revise_and_align_draft"
    elif any(k in low_task for k in ("proofread", "audit", "check", "校对", "核查", "审稿")):
        task_mode = "proofread_and_audit"
    elif language == "bilingual":
        task_mode = "bilingual_writing"
    elif any(k in low_task for k in ("extract table", "structured only", "仅提取", "参数表")):
        task_mode = "structured_extraction"
    else:
        task_mode = "draft_from_materials"

    # Requested editable formats / preview rendering (avoid triggering latex compilation merely on 'IEEE'/'Springer' style names)
    requested_formats = ["markdown", "json", "csv"]
    if re.search(r"\bdocx\b|\.docx\b|word文档|word\s+format", low_task):
        requested_formats.append("docx")
    if re.search(r"\b(?:latex|xelatex|pdflatex)\b|\.tex\b|\\section|\\begin\{document\}|acm\s+tex", low_task):
        requested_formats.append("latex")
    if any(k in low_task for k in ("diagram", "flowchart", "workflow figure", "流程图", "示意图")):
        requested_formats.append("diagram")

    # Content-grounded domain classification (ONLY from actual input materials + task text)
    corpus = ("\n".join(sample_texts) + "\n" + task_text).lower()
    ml_hits = sum(
        1
        for kw in (
            "pytorch", "torch", "tensorflow", "epoch", "batch_size", "learning_rate", "adam", "adamw",
            "cuda", "gpu", "transformer", "bert", "llm", "f1", "auroc", "train_test_split", "dataloader",
            "optimizer", "dropout", "checkpoint", "tokenizer", "huggingface", "sklearn",
        )
        if kw in corpus
    )
    wetlab_hits = sum(
        1
        for kw in (
            "reagent", "catalyst", "calcination", "autoclave", "xrd", "xps", "sem", "tem", "ftir",
            "hplc", "gc-ms", "nmr", "molarity", "wt%", "ml/min", "ghsv", "whsv", "sigma-aldrich",
            "centrifuge", "incubat", "pcr", "elisa", "cell line", "buffer", "ph ", "°c", "催化剂",
            "煅烧", "试剂", "纯度", "反应釜", "表征", "离心", "滴定", "烧杯",
        )
        if kw in corpus
    )
    sim_hits = sum(
        1
        for kw in (
            "lammps", "vasp", "gromacs", "openfoam", "ansys", "comsol", "cantera", "navier-stokes",
            "reynolds", "mesh", "finite element", "molecular dynamics", "dft", "force field", "nvt",
            "npt", "timestep", "boundary condition", "pde", "ode", "网格", "力场", "边界条件", "系综",
        )
        if kw in corpus
    )

    scores = {
        "ml_computational": ml_hits,
        "wetlab_chem_bio_materials": wetlab_hits,
        "simulation_engineering": sim_hits,
    }
    best_domain, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score < 2:
        detected_domain = "general_scientific"
    else:
        detected_domain = best_domain

    return {
        "language": language,
        "task_mode": task_mode,
        "requested_formats": requested_formats,
        "detected_domain": detected_domain,
        "domain_evidence_scores": scores,
    }


def find_referenced_missing_files(case_data: Dict[str, Any], task_text: str, input_dir: Path, discovered_rels: set[str]) -> List[str]:
    """Detect explicit file paths referenced in case.json or task.md that do not exist under /input/."""
    missing: List[str] = []
    candidates: List[str] = []

    path_like_re = re.compile(
        r"^[A-Za-z0-9_./\\\-]+\.(?:yaml|yml|json|toml|ini|cfg|conf|env|py|sh|bash|r|m|ipynb|log|out|csv|tsv|xlsx|md|markdown|txt|docx|pdf|tex|bib|png|jpg|jpeg)$",
        re.I,
    )

    def maybe_add_path(raw_s: str) -> None:
        s = raw_s.strip()
        if not s or "\n" in s or len(s) > 180:
            return
        if path_like_re.match(s):
            candidates.append(s)

    def walk_vals(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if any(w in k.lower() for w in ("file", "path", "input", "material", "artifact", "paper", "doc", "config", "log")):
                    if isinstance(v, str):
                        maybe_add_path(v)
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, str):
                                maybe_add_path(item)
                            elif isinstance(item, dict):
                                walk_vals(item)
                else:
                    walk_vals(v)
        elif isinstance(obj, list):
            for item in obj:
                walk_vals(item)

    walk_vals(case_data)

    # Also catch explicit filename references in task.md (e.g., `configs/train.yaml`, `train.log`)
    for m in re.finditer(r"`([A-Za-z0-9_./\-]+\.(?:yaml|yml|json|toml|ini|py|sh|log|csv|tsv|md|txt|docx|pdf|tex))`", task_text or ""):
        candidates.append(m.group(1))

    discovered_names = {Path(r).name for r in discovered_rels}
    seen = set()
    for c in candidates:
        if c in seen or c.startswith(("http://", "https://", "/app/output", "output/", "result.json")):
            continue
        seen.add(c)
        c_norm = c.lstrip("./")
        if c_norm.startswith("/input/"):
            c_norm = c_norm[len("/input/"):]
        if c_norm in CONTROL_FILES or Path(c_norm).name in CONTROL_FILES:
            continue
        if (input_dir / c_norm).exists() or Path(c).exists() or c_norm in discovered_rels or Path(c_norm).name in discovered_names:
            continue
        missing.append(c)

    return missing


def inspect_case(input_dir: Path, case_path: Optional[Path], task_path: Optional[Path]) -> Dict[str, Any]:
    """Build complete case_plan.json structure."""
    c_path = case_path or (input_dir / "case.json")
    t_path = task_path or (input_dir / "task.md")
    if not t_path.exists():
        for cand in (Path("task.md"), Path("/app/task.md")):
            if cand.exists():
                t_path = cand
                break

    case_data, case_err = load_json_safe(c_path) if c_path.exists() else ({}, f"missing_case_json:{c_path}")
    if not isinstance(case_data, dict):
        case_data = {}

    task_text, task_warn, task_err = read_text_safe(t_path) if t_path.exists() else ("", "missing_task_md", None)

    case_id = str(
        case_data.get("case_id")
        or case_data.get("id")
        or case_data.get("task_id")
        or input_dir.name
        or "unknown_case"
    )

    output_contract = parse_output_contract(
        case_data.get("output_contract")
        or case_data.get("output_schema")
        or case_data.get("expected_output")
    )

    inventory: List[Dict[str, Any]] = []
    sample_texts: List[str] = []
    access_warnings: List[str] = []

    if input_dir.exists():
        for root, dirs, files in os.walk(input_dir):
            dirs[:] = sorted([d for d in dirs if d not in SKIP_DIRS])
            for fn in sorted(files):
                fp = Path(root) / fn
                rel = posix_rel(fp, input_dir)
                category = classify_file(fp, rel)
                if category == "control":
                    continue
                try:
                    size = fp.stat().st_size
                except OSError as exc:
                    size = -1
                    access_warnings.append(f"stat_failed:{rel}:{exc}")
                entry = {
                    "rel_path": rel,
                    "abs_path": str(fp.resolve()),
                    "filename": fn,
                    "ext": fp.suffix.lower(),
                    "size_bytes": size,
                    "category": category,
                }
                inventory.append(entry)
                if category != "binary_or_other" and size > 0 and len(sample_texts) < 12:
                    txt, _, err = read_text_safe(fp, max_bytes=32_000, head_bytes=16_000, tail_bytes=4_000)
                    if txt and not err:
                        sample_texts.append(txt[:8000])
    else:
        access_warnings.append(f"input_dir_not_found:{input_dir}")

    discovered_rels = {item["rel_path"] for item in inventory}
    missing_refs = find_referenced_missing_files(case_data, task_text, input_dir, discovered_rels)
    # Include inline case.json material text in domain detection sample corpus
    inline_sample = json.dumps(
        {k: v for k, v in case_data.items() if k not in ("output_contract", "output_schema")},
        ensure_ascii=False,
    )
    if len(inline_sample) > 80:
        sample_texts.append(inline_sample[:12000])
    signals = detect_task_signals(task_text, case_data, sample_texts)

    # Extract explicit instructions/requirements lines from task.md & case.json
    requirements: List[str] = []
    for k in ("instruction", "instructions", "prompt", "task", "requirements", "constraints", "audience", "genre", "style", "venue"):
        v = case_data.get(k)
        if isinstance(v, str) and v.strip():
            requirements.append(f"[{k}] {v.strip()}")
        elif isinstance(v, list):
            for it in v:
                if str(it).strip():
                    requirements.append(f"[{k}] {str(it).strip()}")

    return {
        "case_id": case_id,
        "input_dir": str(input_dir.resolve()) if input_dir.exists() else str(input_dir),
        "case_json_path": str(c_path) if c_path.exists() else None,
        "case_json_error": case_err,
        "task_md_path": str(t_path) if t_path.exists() else None,
        "task_md_warning": task_warn,
        "task_md_error": task_err,
        "task_md_excerpt": task_text[:4000],
        "case_metadata": {k: v for k, v in case_data.items() if k not in ("output_contract", "output_schema")},
        "requirements": requirements,
        "output_contract": output_contract,
        "signals": signals,
        "file_inventory": inventory,
        "file_counts_by_category": {
            cat: sum(1 for x in inventory if x["category"] == cat)
            for cat in sorted({x["category"] for x in inventory})
        },
        "missing_referenced_files": missing_refs,
        "access_warnings": access_warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect /input/case.json, task.md, and all experiment materials.")
    parser.add_argument("--input-dir", default="/input", help="Input directory containing case.json, task.md, and materials")
    parser.add_argument("--case", default=None, help="Optional explicit path to case.json")
    parser.add_argument("--task", default=None, help="Optional explicit path to task.md")
    parser.add_argument("--out", default="/app/work/case_plan.json", help="Output path for case_plan.json")
    args = parser.parse_args()

    plan = inspect_case(
        Path(args.input_dir),
        Path(args.case) if args.case else None,
        Path(args.task) if args.task else None,
    )
    save_json(Path(args.out), plan)
    print(
        json.dumps(
            {
                "case_id": plan["case_id"],
                "detected_domain": plan["signals"]["detected_domain"],
                "task_mode": plan["signals"]["task_mode"],
                "language": plan["signals"]["language"],
                "requested_formats": plan["signals"]["requested_formats"],
                "materials_count": len(plan["file_inventory"]),
                "file_counts_by_category": plan["file_counts_by_category"],
                "required_contract_keys": plan["output_contract"]["required_keys"],
                "missing_referenced_files": plan["missing_referenced_files"],
                "out": args.out,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
