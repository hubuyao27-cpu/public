#!/usr/bin/env python3
"""Step 1: Inspect /input/case.json, task.md, and all materials under /input.

Produces /app/work/case_plan.json with:
- case_id and parsed output_contract fields (mapped to canonical semantic roles)
- task_mode, language, citation_style, requested_formats, audience/venue
- file inventory categorized into literature/bibliography/own_paper/draft/control
- inline materials discovered inside case.json
- missing referenced files and access warnings
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from common import (
    infer_semantic_role,
    load_json_safe,
    parse_output_contract,
    posix_rel,
    read_text_safe,
    save_json,
)

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}
CONTROL_NAMES = {"case.json", "task.md", "case_plan.json", "result.json"}

BIB_EXTS = {".bib", ".ris", ".enw"}
TEXT_DOC_EXTS = {".md", ".markdown", ".txt", ".tex", ".rst", ".docx", ".pdf"}
TABLE_EXTS = {".csv", ".tsv"}
CODE_CONFIG_EXTS = {".py", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log"}
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pt", ".pth", ".ckpt",
    ".bin", ".npy", ".npz", ".zip", ".tar", ".gz", ".exe", ".dll",
}

LIT_DIR_NAMES = {"papers", "paper", "literature", "lit", "refs", "references", "prior", "prior_work", "related", "related_work", "corpus", "survey", "sources"}
OWN_DIR_NAMES = {"own_paper", "our_paper", "my_paper", "results", "experiments", "experiment", "evaluation", "ablation", "metrics", "figures", "tables", "methods"}
DRAFT_DIR_NAMES = {"draft", "drafts", "existing_draft", "manuscript_draft"}


def _peek_ambiguous_file(path: Path) -> str:
    """Inspect first ~2KB of an ambiguous top-level paper.pdf/main.tex to distinguish own-paper vs literature."""
    ext = path.suffix.lower()
    sample = ""
    if ext in (".tex", ".md", ".markdown", ".txt", ".rst"):
        txt, err = read_text_safe(path)
        if not err and txt:
            sample = txt[:2500]
    elif ext == ".pdf":
        # Prefer PyMuPDF for accurate multi-column reading order before falling back to pypdf
        try:
            fitz_mod: Any = None
            try:
                import pymupdf as fitz_mod  # type: ignore
            except ImportError:
                import fitz as fitz_mod  # type: ignore
            if hasattr(fitz_mod, "open"):
                doc = fitz_mod.open(str(path))
                if not getattr(doc, "needs_pass", False) and len(doc) > 0:
                    sample = (doc[0].get_text("text") or "")[:2500]
                doc.close()
        except Exception:
            sample = ""
        if not sample:
            try:
                import pypdf  # type: ignore

                reader = pypdf.PdfReader(str(path))
                if not reader.is_encrypted and reader.pages:
                    sample = (reader.pages[0].extract_text() or "")[:2500]
            except Exception:
                sample = ""
    if not sample:
        return "literature_source"
    low = sample.lower()
    own_cues = (
        "our method", "our model", "our experiments", "our results", "we propose",
        "本文方法", "本文提出", "本文实验", "实验结果", "实验设置",
        r"\section{method", r"\section{experiment",
    )
    if any(c in low for c in own_cues):
        if any(c in low for c in (r"\section{introduction", "# introduction", "## 1. 引言", "## 引言")):
            return "own_paper_draft"
        return "own_paper_evidence"
    return "literature_source"


def classify_file(path: Path, rel: str) -> str:
    name_low = path.name.lower()
    stem_low = path.stem.lower()
    rel_low = rel.lower()
    ext = path.suffix.lower()
    parent_parts = [pt.lower() for pt in Path(rel).parts[:-1]]

    if name_low in CONTROL_NAMES:
        return "control"
    if ext in BINARY_EXTS:
        return "binary_or_figure"

    # Check own-paper drafts BEFORE generic bibliography substrings (e.g. intro_draft_with_references.tex)
    if any(pt in DRAFT_DIR_NAMES for pt in parent_parts) or any(
        w in name_low for w in ("draft", "existing_intro", "old_intro", "manuscript_draft", "raw_draft")
    ):
        return "own_paper_draft"

    if ext in BIB_EXTS or any(w in name_low for w in ("bib", "citation", "references", "ref_list", "library")):
        if ext in (".bib", ".ris", ".enw", ".json", ".csv", ".tsv", ".md", ".txt", ".tex"):
            return "bibliography"

    # Directory-aware priority prevents papers/smith_methods.pdf from becoming own_paper_evidence
    if any(pt in LIT_DIR_NAMES for pt in parent_parts):
        return "literature_source"
    if any(pt in OWN_DIR_NAMES for pt in parent_parts):
        return "own_paper_evidence"

    # Top-level filename heuristics using word boundaries (avoid matching substrings inside e.g. self_evaluation_survey.pdf)
    is_own_name = bool(
        re.search(r"\b(?:own_paper|our_paper|own_result|our_result|results?|experiments?|evaluation|eval|metrics?|ablations?|figures?|tables?|methods?)\b", stem_low)
    )
    if is_own_name and not re.search(r"\b(?:survey|review|benchmark|retrieval|corpus|literature|related)\b", stem_low):
        return "own_paper_evidence"
    if name_low in ("paper.pdf", "main.tex", "manuscript.pdf", "manuscript.tex", "submission.pdf"):
        return _peek_ambiguous_file(path)
    if any(w in rel_low for w in ("paper", "lit", "prior", "related", "source", "ref", "survey", "corpus")):
        return "literature_source"
    if ext in TABLE_EXTS or ext in CODE_CONFIG_EXTS:
        return "own_paper_evidence"
    if ext in TEXT_DOC_EXTS or ext == ".json":
        return "literature_source"
    return "other_text"


def detect_signals(case_data: Dict[str, Any], task_text: str) -> Dict[str, Any]:
    meta_subset = {
        k: v
        for k, v in case_data.items()
        if k in ("title", "task", "instruction", "prompt", "language", "target_language", "citation_style", "reference_format", "style", "venue", "audience", "format", "output_format", "formats")
    }
    blob = f"{task_text}\n{json.dumps(meta_subset, ensure_ascii=False)}"
    low = blob.lower()

    # 1. Language: honor explicit case_data["language"] / target_language FIRST
    explicit_lang = str(case_data.get("language") or case_data.get("target_language") or "").strip().lower()
    if any(w in explicit_lang for w in ("bilingual", "双语", "zh+en", "en+zh", "zh-en", "en-zh")):
        lang = "bilingual"
    elif explicit_lang.startswith("zh") or explicit_lang in ("cn", "chinese", "中文"):
        lang = "zh"
    elif explicit_lang.startswith("en") or explicit_lang in ("english", "英文"):
        lang = "en"
    else:
        clean_lang_blob = re.sub(r"(?:无需|不需要|不用|非|no|not)\s*(?:双语|bilingual)", " ", blob, flags=re.I)
        if re.search(r"\bbilingual\b|双语|中英文|中英双语", clean_lang_blob, re.I):
            lang = "bilingual"
        elif re.search(
            r"(?:write|draft|output|compose)\s+(?:in\s+)?english|(?:请?用|以|使用)英文(?:完成|撰写|写作|编写|输出|润色|撰写引言)|英文(?:论文|引言|相关工作|写作)|目标语言\s*[:：]\s*英文",
            low,
        ) and not re.search(r"(?:不要|无需|非)英文", blob):
            lang = "en"
        elif re.search(r"[\u4e00-\u9fff]", task_text or blob):
            lang = "zh"
        else:
            lang = "en"

    # 2. Citation style: honor explicit fields first, require style context for 'nature'
    explicit_style = str(case_data.get("citation_style") or case_data.get("reference_format") or case_data.get("style") or "").strip().lower()
    if "apa" in explicit_style or "author" in explicit_style:
        cstyle = "apa_author_year"
    elif "7714" in explicit_style or "gbt" in explicit_style or "gb/t" in explicit_style:
        cstyle = "gbt7714"
    elif "ieee" in explicit_style:
        cstyle = "ieee_numeric"
    elif "nature" in explicit_style:
        cstyle = "nature_numeric"
    elif "bib" in explicit_style or "latex" in explicit_style:
        cstyle = "bibtex_latex"
    elif re.search(r"\bapa\b|author[- ]year|著者-出版年", low):
        cstyle = "apa_author_year"
    elif "gb/t" in low or "7714" in low or "gbt7714" in low:
        cstyle = "gbt7714"
    elif re.search(r"\bieee\b", low):
        cstyle = "ieee_numeric"
    elif re.search(r"\bnature\s*(?:style|format|citation|reference|journal|numeric|格式|引用|期刊)|(?:style|format|citation|格式|引用)[^.\n]{0,20}\bnature\b", low):
        cstyle = "nature_numeric"
    elif re.search(r"\bbibtex\b|\\cite|\blatex\b|\.tex\b", low) and not re.search(r"(?:无需|不用|不要求|no)\s*(?:latex|bibtex|\.tex)", low):
        cstyle = "bibtex_latex"
    else:
        cstyle = "unspecified"

    # 3. Task mode: strip '修改记录/revision log' before checking revision cues; prioritize explicit targeted actions
    mode_blob = re.sub(r"修改记录|修改日志|修改差异|change_records|revision\s+log", " ", low)
    has_intro_cue = bool(re.search(r"\bintroduction\b|引言", mode_blob))
    has_rw_cue = bool(re.search(r"related work|相关工作", mode_blob))
    if lang == "bilingual" or any(w in mode_blob for w in ("bilingual", "双语")):
        tmode = "bilingual_writing"
    elif re.search(r"(?:核对|补全|补齐|检查|审计)[^\n。]{0,12}引用|\b(?:fill|audit|check|verify)\b[^\n.]{0,18}\bcitation", mode_blob) and not re.search(r"撰写|写作|起草|draft|write", mode_blob):
        tmode = "fill_and_audit_citations"
    elif re.search(r"对齐[^\n。]{0,12}贡献|\balign\b[^\n.]{0,18}\bcontribution", mode_blob) and not re.search(r"撰写|写作|起草|draft|write", mode_blob):
        tmode = "align_contributions"
    elif re.search(r"\b(?:revise|rewrite|polish)\b|修改(?:草稿|现有|已有|原文|引言|相关工作)|改写|润色|重构", mode_blob):
        tmode = "revise_and_strengthen"
    elif any(w in mode_blob for w in ("audit", "verify", "check", "核对", "补引", "补全引用", "校对")) and not (has_intro_cue or has_rw_cue):
        tmode = "fill_and_audit_citations"
    elif (has_rw_cue or any(w in mode_blob for w in ("group", "matrix", "compare", "分组", "梳理相关工作", "对比路线"))) and not has_intro_cue:
        tmode = "organize_related_work"
    elif any(w in mode_blob for w in ("contribution", "贡献对齐", "贡献表述")) and not (has_intro_cue or has_rw_cue):
        tmode = "align_contributions"
    else:
        tmode = "draft_intro_rw"

    # 4. Requested output formats (guard against input file mentions and explicit negations)
    fmt_blob = re.sub(r"(?:无需|不用|不要求|不需要|no|without)\s*(?:latex|bibtex|docx|word|pdf|tex|diagram)", " ", low)
    fmt_blob = re.sub(r"(?:读取|基于|输入|依据|from|read|parse)\s+[A-Za-z0-9_./-]+\.(?:tex|bib|docx|pdf)\b", " ", fmt_blob)
    req_fmts = ["md"]
    if re.search(r"\b(?:latex|xelatex|pdflatex|tex)\b|\.tex\b|\\cite", fmt_blob):
        req_fmts.append("tex")
    if re.search(r"\bbibtex\b|\.bib\b", fmt_blob):
        req_fmts.append("bib")
    if re.search(r"\b(?:docx|ms\s*word|microsoft\s*word)\b|\.docx\b|word\s*文档", fmt_blob):
        req_fmts.append("docx")
    if re.search(r"\bpdf\b|导出\s*pdf", fmt_blob) and "tex" not in req_fmts and "docx" not in req_fmts:
        req_fmts.append("tex")
    if re.search(r"\bcsv\b|\.csv\b|\bexcel\b|comparison table|对比表|比较矩阵|矩阵", fmt_blob):
        req_fmts.append("csv")
    if re.search(r"\b(?:diagram|flowchart)\b|\btaxonomy\s+(?:diagram|figure|chart|plot)\b|路线图|关系图|演化图|结构图", fmt_blob):
        req_fmts.append("diagram")

    venue = str(case_data.get("venue") or case_data.get("audience") or case_data.get("journal") or "").strip()
    return {
        "task_mode": tmode,
        "language": lang,
        "citation_style": cstyle,
        "requested_formats": sorted(set(req_fmts)),
        "audience_or_venue": venue,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspect Harbor input case, output_contract, and files.")
    ap.add_argument("--input-dir", default="/input", help="Input directory containing case.json and artifacts")
    ap.add_argument("--case", default=None, help="Optional override path for case.json")
    ap.add_argument("--task", default=None, help="Optional override path for task.md")
    ap.add_argument("--out", default="/app/work/case_plan.json", help="Output JSON path")
    args = ap.parse_args()

    input_dir = Path(args.input_dir)
    case_path = Path(args.case) if args.case else input_dir / "case.json"
    task_path = Path(args.task) if args.task else input_dir / "task.md"
    if not task_path.exists() and Path("task.md").exists():
        task_path = Path("task.md")

    access_warnings: List[str] = []
    case_data, case_err = load_json_safe(case_path)
    if case_err:
        access_warnings.append(f"case_json:{case_err}")
        case_data = {}
    if not isinstance(case_data, dict):
        case_data = {}

    task_text, task_err = read_text_safe(task_path)
    if task_err:
        access_warnings.append(f"task_md:{task_err}")
        task_text = ""

    case_id = str(case_data.get("case_id") or case_data.get("id") or input_dir.name or "case_unknown")
    contract_raw = case_data.get("output_contract")
    parsed_contract = parse_output_contract(contract_raw)

    inventory: List[Dict[str, Any]] = []
    if input_dir.exists():
        for p in sorted(input_dir.rglob("*")):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            if not p.is_file():
                continue
            rel = posix_rel(p, input_dir)
            bucket = classify_file(p, rel)
            readable = True
            warn = None
            try:
                sz = p.stat().st_size
                if sz == 0:
                    warn = "empty_file"
            except OSError as exc:
                readable = False
                sz = 0
                warn = f"stat_error:{exc}"
            if warn:
                access_warnings.append(f"{rel}:{warn}")
            inventory.append(
                {
                    "path": str(p),
                    "rel_path": rel,
                    "ext": p.suffix.lower(),
                    "size_bytes": sz,
                    "bucket": bucket,
                    "readable": readable,
                }
            )

    # Check explicitly referenced files in case.json (list OR singular keys)
    missing_refs: List[str] = []
    for key in ("files", "materials", "artifacts", "documents", "papers", "input_files"):
        val = case_data.get(key)
        if isinstance(val, list):
            for item in val:
                cand = None
                if isinstance(item, str):
                    cand = item
                elif isinstance(item, dict):
                    cand = item.get("path") or item.get("file") or item.get("filename")
                if cand and isinstance(cand, str) and ("/" in cand or "." in cand):
                    cp = (input_dir / cand) if not Path(cand).is_absolute() else Path(cand)
                    if not cp.exists():
                        missing_refs.append(cand)
    for skey in ("input_file", "pdf_path", "draft_file", "manuscript_file", "bib_file"):
        sval = case_data.get(skey)
        if isinstance(sval, str) and ("/" in sval or "." in sval):
            cp = (input_dir / sval) if not Path(sval).is_absolute() else Path(sval)
            if not cp.exists():
                missing_refs.append(sval)

    # Identify inline material keys in case.json
    inline_keys: List[str] = []
    for k in (
        "references", "bibliography", "papers", "literature", "draft", "manuscript",
        "existing_draft", "intro_draft", "experiments", "results", "evaluation",
        "ablation", "metrics", "figures", "tables", "methods", "contributions",
        "own_paper", "materials", "documents", "notes", "context", "background",
        "abstract", "related_work",
    ):
        if k in case_data and case_data[k]:
            inline_keys.append(k)

    signals = detect_signals(case_data, task_text or "")
    non_control_files = [f for f in inventory if f["bucket"] != "control"]

    plan = {
        "case_id": case_id,
        "input_dir": str(input_dir),
        "case_json_path": str(case_path) if case_path.exists() else None,
        "task_md_path": str(task_path) if task_path.exists() else None,
        "task_instruction_excerpt": (task_text or str(case_data.get("instruction") or case_data.get("prompt") or ""))[:1500],
        "output_contract": parsed_contract,
        "signals": signals,
        "inline_material_keys": inline_keys,
        "inventory": inventory,
        "stats": {
            "total_files": len(inventory),
            "non_control_files": len(non_control_files),
            "bibliography_files": sum(1 for f in inventory if f["bucket"] == "bibliography"),
            "literature_files": sum(1 for f in inventory if f["bucket"] == "literature_source"),
            "own_paper_files": sum(1 for f in inventory if f["bucket"] in ("own_paper_draft", "own_paper_evidence")),
        },
        "missing_referenced_files": sorted(set(missing_refs)),
        "access_warnings": access_warnings,
    }
    save_json(Path(args.out), plan)
    print(json.dumps({"status": "ok", "case_id": case_id, "stats": plan["stats"], "signals": signals}, ensure_ascii=False))


if __name__ == "__main__":
    main()
