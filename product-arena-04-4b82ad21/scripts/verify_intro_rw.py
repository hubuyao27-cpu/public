#!/usr/bin/env python3
"""Step 5: 10-Axis Mechanical Verification & Self-Correction Auditor.

Separates deterministic blocking ERRORS (exit code 1) from advisory WARNINGS:
Errors:
  1. Missing case_id or required output_contract fields.
  2. Missing core sections (Background, Related Work, Gaps, Contributions, References) when status=='completed'.
  3. Unresolved inline citations in prose/structured sections/claim_citation_map not matched to ledger or marked unverified.
  4. Fabricated/contradicted citation metadata without conflict disclosure.
  5. Research gap lacking both supporting_ref_ids and explicit author_judgment disclosure.
  6. Contribution lacking own_evidence_locators (unless author_confirm_required) or citing missing files.
  7. Ungrounded quantitative metrics in contributions/matrix/prose without [待作者确认]/[AUTHOR_CONFIRM].
  8. Undisclosed cross-source conflicts (CONF-XX from source_ledger).
  9. Broken GAP-XX bidirectional alignment between inline markers and author_confirmation_items[].
 10. Dishonest status ('completed' when literature & own-paper sources are empty/unreadable).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from common import (
    extract_first_author_surname,
    extract_numeric_tokens,
    load_json_safe,
    load_rubric_rules,
    normalize_ref_id_token,
    numbers_match,
    read_text_safe,
    resolve_manuscript_text,
    save_json,
    sync_payload_structures,
)

_NON_CITE_BRACKET_RE = re.compile(
    r"^(?:GAP|CONF|CONTRIB|CLAIM|RG|CH|CL|G|C)-?\d{1,3}$"
    r"|^(?:CO2|H2O|O2|N2|CH4|NH3|NO2|SO2|SiO2|TiO2|Fe2O3|Al2O3|Pt\d+|Ni-?N\d+|Cu\d+|FP16|BF16|FP32|INT8|INT4|L[0-2]|[1-4]D)$",
    re.I,
)
_NARRATIVE_STOP_WORDS = {
    "fig", "figure", "table", "tab", "section", "sec", "chapter", "eq", "equation",
    "model", "dataset", "version", "release", "since", "after", "before", "during",
    "until", "between", "from", "year", "years", "circa", "around", "about", "april",
    "may", "june", "july", "march", "january", "february", "august", "september",
    "october", "november", "december", "ieee", "acm", "nature", "science", "arxiv",
    "transformer", "bert", "gpt", "resnet", "vit", "clip", "alphafold", "imagenet",
    "coco", "mmlu", "chatgpt", "claude", "deepseek", "llama", "lora", "rag", "pinn",
    "cnki", "neurips", "icml", "iclr", "cvpr", "iccv", "acl", "emnlp", "aaai",
    "springer", "elsevier", "wiley", "china", "usa", "europe", "benchmark", "baseline",
    "standard", "policy", "guideline", "system", "framework", "corpus",
    "国家标准", "国标", "行业标准", "标准", "指南", "政策", "版本", "模型", "系统",
    "框架", "数据集", "数据", "基准", "工业界", "学术界", "截至目前", "目前",
}
_HYPERPARAM_CTX_RE = re.compile(
    r"(?:size|sizes|dim|dims|shape|kernel|kernels|layer|layers|channel|channels|hidden|head|heads|step|steps|epoch|epochs|batch|batches|range|interval|index|indices|array|tensor|matrix|vector|classes|values|维度|卷积核|步长|通道|批大小)\s*(?:of|=|:|is|are|in|为|包含)?\s*$",
    re.I,
)


def _split_locator_file(loc: str) -> str:
    if not loc:
        return ""
    s = loc.strip()
    if s.startswith("case.json"):
        return "case.json"
    if s.startswith("task.md"):
        return "task.md"
    return s.split(":", 1)[0].split("#", 1)[0].strip()


def _extract_prose_citations(
    text: str,
    known_keys: Optional[Set[str]] = None,
    known_surnames: Optional[Set[str]] = None,
) -> List[str]:
    found: List[str] = []
    known = {k.lower() for k in (known_keys or set())}
    surnames_set = {s.lower() for s in (known_surnames or set()) if len(s) >= 2}
    # Mask file/line locators like [lit_notes.md:L2] and markdown links [text](url)
    cleaned = re.sub(r"\[[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,5}:[^\]]+\]", " ", text or "")
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)

    # 1. Bracket citations: [1], [R01], [1, 2], [1-3], [Smith2023], [known_key]
    for m in re.finditer(r"\[([A-Za-z0-9_,\s;:\-–]+)\](?!\s*(?:%|％|mA|kPa|MPa|eV|ms|Hz|nm|μm|mm|cm|kg|mg|mol|W|V|K))", cleaned):
        inner = m.group(1).strip()
        if not inner:
            continue
        if any(tag in inner for tag in ("待作者确认", "AUTHOR_CONFIRM", "作者判断", "AUTHOR_JUDGMENT", "待补充")):
            continue
        if _NON_CITE_BRACKET_RE.match(inner):
            continue
        prefix_ctx = cleaned[max(0, m.start() - 45) : m.start()]
        if _HYPERPARAM_CTX_RE.search(prefix_ctx) and not re.search(r"\bR\d{1,3}\b", inner):
            continue
        # Skip intervals like [0, 1], [-1, 1], [0-1]
        if re.match(r"^-?0(?:\.\d+)?\s*[,;\-–]\s*", inner) or re.search(r"(?:^|[,;\s])-\d", inner):
            continue
        rm = re.match(r"^(R?)(\d+)\s*[-–]\s*(R?)(\d+)$", inner)
        if rm:
            a, b = int(rm.group(2)), int(rm.group(4))
            if 0 < a <= b <= a + 20:
                prefix = "R" if (rm.group(1) or rm.group(3)) else ""
                for k in range(a, b + 1):
                    found.append(f"{prefix}{k:02d}" if prefix else str(k))
            continue
        parts = [p.strip() for p in re.split(r"[,;]", inner) if p.strip()]
        if not parts:
            continue
        if any(p == "0" for p in parts):
            continue
        # Skip multi-element numeric hyperparameter lists like [3, 5, 7] or [64, 128]
        if len(parts) >= 2 and all(re.match(r"^[1-9]\d{0,3}$", p) for p in parts) and (len(parts) >= 3 or all(int(p) >= 32 for p in parts)):
            continue
        for p in parts:
            if _NON_CITE_BRACKET_RE.match(p):
                continue
            if re.match(r"^R\d{1,3}$", p) or re.match(r"^[1-9]\d{0,2}$", p):
                found.append(p)
            elif p.lower() in known:
                found.append(p)
            elif re.match(r"^[A-Za-z][A-Za-z0-9_:-]{2,36}$", p) and re.search(r"(?:19|20)\d{2}", p):
                found.append(p)

    # 2. LaTeX \cite{k1, k2} / \citep{} / \citet{}
    for m in re.finditer(r"\\cite[a-z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}", cleaned):
        for k in m.group(1).split(","):
            if k.strip():
                found.append(k.strip())

    # 3. Parenthetical Author-Year: (Smith, 2023), (Smith & Jones, 2023), (Smith, 2022; Jones, 2023), (张伟等, 2022)
    for pm in re.finditer(r"[（(]([^()（）]{4,180})[)）]", cleaned):
        inside = pm.group(1).strip()
        if not re.search(r"(?:19|20)\d{2}", inside):
            continue
        for seg in re.split(r"[;；]", inside):
            seg_c = re.sub(r"^(?:e\.g\.|i\.e\.|cf\.|see|also|见|如|参见)\s*,?\s*", "", seg.strip(), flags=re.I)
            em = re.match(
                r"^([A-Z][A-Za-z-]+(?:\s+(?:&|and)\s+[A-Z][A-Za-z-]+|\s+et\s+al\.?)?)\s*,?\s*((?:19|20)\d{2}[a-z]?)(?:\s*,.*)?$",
                seg_c,
            )
            if em:
                surname = em.group(1).split()[0].rstrip(",")
                if surname.lower() not in _NARRATIVE_STOP_WORDS:
                    found.append(f"{surname}{em.group(2)}")
                continue
            cm = re.match(r"^([\u4e00-\u9fff]{2,4})(?:等|、[\u4e00-\u9fff]{2,4}|和[\u4e00-\u9fff]{2,4})?\s*[,，]?\s*((?:19|20)\d{2}[a-z]?)$", seg_c)
            if cm and cm.group(1) not in _NARRATIVE_STOP_WORDS:
                found.append(f"{cm.group(1)}{cm.group(2)}")

    # 4. Narrative Author-Year: require explicit citation cue OR match against known surnames / exclude model/acronym tokens
    for nm in re.finditer(
        r"\b([A-Z][A-Za-z-]{2,})(\s+(?:&|and)\s+[A-Z][A-Za-z-]+|\s+et\s+al\.?)?\s*\(((?:19|20)\d{2}[a-z]?)\)",
        cleaned,
    ):
        surname = nm.group(1)
        has_cue = bool(nm.group(2))
        sl = surname.lower()
        if sl in _NARRATIVE_STOP_WORDS:
            continue
        # Guard against named architectures/acronyms (BERT (2018), ResNet (2015), AlphaFold (2021)) unless in library or accompanied by et al./&/and
        is_camel_or_acronym = surname.isupper() or bool(re.search(r"[a-z][A-Z]", surname))
        if has_cue or sl in surnames_set or (not is_camel_or_acronym and len(surname) <= 14):
            found.append(f"{surname}{nm.group(3)}")
    for cnm in re.finditer(r"([\u4e00-\u9fff]{2,4})(等)?\s*[（(]((?:19|20)\d{2}[a-z]?)[)）]", cleaned):
        cname = cnm.group(1)
        has_cue = bool(cnm.group(2))
        if cname in _NARRATIVE_STOP_WORDS:
            continue
        if has_cue or cname.lower() in surnames_set:
            found.append(f"{cname}{cnm.group(3)}")

    return found


def main() -> None:
    ap = argparse.ArgumentParser(description="10-axis mechanical verifier for Introduction & Related Work payload.")
    ap.add_argument("--payload", default="/app/work/section_payload.json")
    ap.add_argument("--manuscript", default=None, help="Optional external markdown manuscript path (overrides/supplements payload.manuscript_markdown)")
    ap.add_argument("--ledger", default="/app/work/source_ledger.json")
    ap.add_argument("--case-plan", default="/app/work/case_plan.json")
    ap.add_argument("--out", default="/app/work/verification_report.json")
    ap.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    args = ap.parse_args()

    payload_path = Path(args.payload)
    payload, p_err = load_json_safe(payload_path)
    ledger, l_err = load_json_safe(Path(args.ledger))
    plan, c_err = load_json_safe(Path(args.case_plan))

    if p_err or not isinstance(payload, dict):
        raise SystemExit(f"Cannot load payload: {p_err}")
    if l_err or not isinstance(ledger, dict):
        raise SystemExit(f"Cannot load ledger: {l_err}")
    if c_err or not isinstance(plan, dict):
        raise SystemExit(f"Cannot load case_plan: {c_err}")

    rubric = load_rubric_rules()
    cliche_patterns = list(rubric.get("cliche_openings_zh") or []) + list(rubric.get("cliche_openings_en") or [])
    overclaim_patterns = list(rubric.get("overclaim_patterns_zh") or []) + list(rubric.get("overclaim_patterns_en") or [])

    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    def add_err(code: str, detail: str, fix: str) -> None:
        errors.append({"code": code, "detail": detail, "fix_hint": fix})

    def add_warn(code: str, detail: str, fix: str = "") -> None:
        warnings.append({"code": code, "detail": detail, "fix_hint": fix})

    status = str(payload.get("status") or "").strip()
    catalog: List[Dict[str, Any]] = ledger.get("references_catalog") or []
    own_ev: List[Dict[str, Any]] = ledger.get("own_paper_evidence") or []
    fig_mentions: List[Dict[str, Any]] = ledger.get("figure_table_mentions") or []
    known_files: Set[str] = {f.get("rel_path", "") for f in (plan.get("inventory") or [])}
    if plan.get("case_json_path"):
        known_files.add("case.json")
    if plan.get("task_md_path"):
        known_files.add("task.md")
    known_basenames: Set[str] = {Path(p).name for p in known_files if p}

    manuscript, ms_warns, ms_err = resolve_manuscript_text(payload, payload_path, args.manuscript)
    for mw in ms_warns:
        add_warn("MANUSCRIPT_SOURCE_NOTICE", mw, "Prefer either manuscript_file or manuscript_markdown cleanly.")
    if ms_err:
        add_err("MANUSCRIPT_FILE_NOT_FOUND", ms_err, "Ensure manuscript_file exists or clear it and populate manuscript_markdown.")

    extracted_dir = Path(args.ledger).parent / "extracted"
    payload = sync_payload_structures(payload, manuscript, ledger=ledger, extracted_dir=extracted_dir)

    # Axis 1: case_id & output_contract check
    expected_cid = plan.get("case_id", "")
    if payload.get("case_id") != expected_cid:
        add_err("CASE_ID_MISMATCH", f"payload.case_id={payload.get('case_id')!r} != {expected_cid!r}", "Set payload.case_id to exact case_id from case_plan.json.")

    req_keys = (plan.get("output_contract") or {}).get("required_keys") or []
    req_roles = {
        fspec.get("semantic_role", "custom")
        for fspec in ((plan.get("output_contract") or {}).get("fields") or [])
        if fspec["name"] in req_keys
    }
    overrides = payload.get("contract_overrides") or {}
    payload_roles = {
        "case_id": payload.get("case_id"),
        "status": payload.get("status"),
        "manuscript_markdown": manuscript.strip() or None,
        "research_background": payload.get("research_background"),
        "related_work_groups": payload.get("related_work_groups"),
        "comparison_matrix": payload.get("comparison_matrix"),
        "research_gaps": payload.get("research_gaps"),
        "contributions": payload.get("contributions"),
        "references": payload.get("references"),
        "claim_citation_map": payload.get("claim_citation_map"),
        "conflicts": payload.get("conflicts"),
        "author_confirmation_items": payload.get("author_confirmation_items"),
        "change_records": payload.get("change_records"),
        "uncertainty": payload.get("uncertainty"),
        "sources": payload.get("references") or payload.get("claim_citation_map"),
        # Assembler deterministically produces these 3 roles
        "deliverable_files": True,
        "verification_summary": True,
        "summary": True,
    }
    for fspec in (plan.get("output_contract") or {}).get("fields") or []:
        fname = fspec["name"]
        if fname in req_keys:
            role = fspec.get("semantic_role", "custom")
            if fname in overrides or (fname in payload and payload[fname] not in ("", None)):
                continue
            if role in payload_roles and payload_roles[role] is not None:
                continue
            add_err("MISSING_CONTRACT_FIELD", f"Required output_contract key '{fname}' (role={role}) has no mapping.", f"Populate contract_overrides['{fname}'] in section_payload.json.")

    # Axis 10: Dishonest status when materials empty
    if status == "completed" and not catalog and not own_ev and not fig_mentions:
        add_err("DISHONEST_STATUS", "status='completed' but both literature catalog and own-paper evidence are empty.", "Change status to 'insufficient_material' or 'partial' and list missing items in uncertainty[].")

    # Axis 2: Core completeness when completed (mode-aware + manuscript-heading-aware when contract doesn't mandate internal JSON sub-objects)
    task_mode = str((plan.get("signals") or {}).get("task_mode") or "draft_intro_rw")
    bg = payload.get("research_background") or {}
    rw_groups = payload.get("related_work_groups") or []
    matrix = payload.get("comparison_matrix") or []
    gaps = payload.get("research_gaps") or []
    contribs = payload.get("contributions") or []
    refs = payload.get("references") or []
    claim_map = payload.get("claim_citation_map") or []

    has_intro_hdr = bool(re.search(r"(?m)^#{1,4}\s+.*(?:introduction|引言)", manuscript, re.I))
    has_rw_hdr = bool(re.search(r"(?m)^#{1,4}\s+.*(?:related\s+work|相关工作)", manuscript, re.I))
    has_gap_hdr = bool(re.search(r"(?m)^#{1,4}\s+.*(?:gap|空白|不足|贡献|contribution)", manuscript, re.I))

    if status == "completed":
        subtask_no_prose = (task_mode == "organize_related_work" and rw_groups and matrix) or (task_mode == "fill_and_audit_citations" and refs) or (task_mode == "align_contributions" and contribs)
        if not manuscript.strip() and not (subtask_no_prose and "manuscript_markdown" not in req_roles):
            add_err("EMPTY_MANUSCRIPT", "manuscript_markdown is empty while status=='completed'.", "Write publication-ready prose (or point manuscript_file to /app/work/manuscript.md).")
        if task_mode == "organize_related_work":
            if not rw_groups:
                add_err("MISSING_RELATED_WORK", "related_work_groups[] is empty.", "Group literature into methodological/problem routes.")
            if not gaps:
                add_warn("MISSING_RESEARCH_GAPS", "No research_gaps[] supplied in organize_related_work mode.", "Derive gaps from shared limitations across grouped routes.")
            if own_ev and not contribs:
                add_warn("MISSING_CONTRIBUTIONS", "Own-paper evidence exists but contributions[] is empty.", "Align contributions to own-paper evidence or confirm out-of-scope.")
        elif task_mode == "fill_and_audit_citations":
            if not refs:
                add_err("MISSING_REFERENCES", "references[] is empty in fill_and_audit_citations mode.", "Populate verified references from source_ledger.json.")
        elif task_mode == "align_contributions":
            if not contribs:
                add_err("MISSING_CONTRIBUTIONS", "contributions[] is empty in align_contributions mode.", "Provide evidence-bound contributions linked to methods/figures/tables/metrics.")
        else:
            if not isinstance(bg, dict) or not bg.get("concrete_problem"):
                if has_intro_hdr and "research_background" not in req_roles:
                    add_warn("MISSING_BACKGROUND", "research_background.concrete_problem omitted (covered via manuscript Introduction).", "Optionally populate research_background for machine readability.")
                else:
                    add_err("MISSING_BACKGROUND", "research_background.concrete_problem missing.", "Populate research_background with domain_lineage, concrete_problem, technical_constraints, why_now.")
            if not rw_groups:
                if has_rw_hdr and "related_work_groups" not in req_roles:
                    add_warn("MISSING_RELATED_WORK", "related_work_groups[] omitted (covered via manuscript Related Work).", "Populate related_work_groups[] for comparative route verification.")
                else:
                    add_err("MISSING_RELATED_WORK", "related_work_groups[] is empty.", "Group literature into at least 2 methodological/problem routes (or 1 explicit route if tiny corpus).")
            if not gaps:
                if has_gap_hdr and "research_gaps" not in req_roles:
                    add_warn("MISSING_RESEARCH_GAPS", "research_gaps[] omitted (covered in prose).", "Populate research_gaps[] for machine traceability.")
                else:
                    add_err("MISSING_RESEARCH_GAPS", "research_gaps[] is empty.", "Derive gaps from cited work limitations/conflicts or mark [作者判断]/[AUTHOR_JUDGMENT].")
            if not contribs:
                if own_ev or fig_mentions:
                    if has_gap_hdr and "contributions" not in req_roles and not own_ev:
                        add_warn("MISSING_CONTRIBUTIONS", "contributions[] omitted.", "Populate contributions[] with own_evidence_locators.")
                    else:
                        add_err("MISSING_CONTRIBUTIONS", "contributions[] is empty.", "Provide evidence-bound contributions linked to methods/figures/tables/metrics.")
                else:
                    add_warn("NO_OWN_EVIDENCE_FOR_CONTRIBUTIONS", "No own-paper evidence in input; contributions[] left empty or pending author confirmation.", "Confirm with author or provide own-paper experimental evidence.")

        # Validate nested subkeys on populated objects so blank rows ('- | - | -') fail cleanly
        for g in rw_groups:
            if isinstance(g, dict) and not str(g.get("route_name") or "").strip():
                add_err("MALFORMED_RELATED_WORK_GROUP", f"Group '{g.get('group_id', '?')}' lacks route_name.", "Specify non-empty route_name and works_covered[].")
        for g in gaps:
            if isinstance(g, dict):
                if not str(g.get("gap_statement") or "").strip():
                    add_err("MALFORMED_RESEARCH_GAP", f"Gap '{g.get('gap_id', '?')}' lacks gap_statement.", "Provide non-empty gap_statement and supporting_ref_ids[].")
                dt = str(g.get("derivation_type") or "")
                if dt and dt not in ("shared_limitation", "uncovered_scenario", "inter_study_conflict", "author_judgment"):
                    add_warn("INVALID_GAP_DERIVATION_ENUM", f"Gap '{g.get('gap_id', '?')}' derivation_type={dt!r} is non-standard.", "Use shared_limitation | uncovered_scenario | inter_study_conflict | author_judgment.")
        for c in contribs:
            if isinstance(c, dict) and not str(c.get("claim_text") or "").strip():
                add_err("MALFORMED_CONTRIBUTION", f"Contribution '{c.get('contrib_id', '?')}' lacks claim_text.", "Provide non-empty claim_text.")

        # Structural quality warnings for completed drafts
        if catalog and task_mode not in ("fill_and_audit_citations", "align_contributions"):
            if not matrix:
                add_warn("EMPTY_COMPARISON_MATRIX", "comparison_matrix[] is empty despite available literature.", "Populate multi-dimensional comparison_matrix rows covering each cited work.")
            if not claim_map:
                add_warn("EMPTY_CLAIM_CITATION_MAP", "claim_citation_map[] is empty.", "Add claim-to-citation traceability entries anchoring key prose claims.")
            if len(catalog) >= 3 and len(rw_groups) < 2:
                add_warn("INSUFFICIENT_ROUTE_GROUPING", f"Only {len(rw_groups)} related_work_groups for {len(catalog)} references.", "Group literature into >=2 methodological/problem routes to avoid flat summaries.")

    # Build lookup tables for ledger references & payload references
    ledger_by_ref_id = {r["ref_id"].lower(): r for r in catalog if r.get("ref_id")}
    ledger_by_key = {r["cite_key"].lower(): r for r in catalog if r.get("cite_key")}
    ledger_by_ay: Dict[str, Dict[str, Any]] = {}
    for r in catalog:
        surn = str(r.get("first_author_surname") or "").strip().lower()
        yr = str(r.get("year") or "").strip().lower()
        if surn and yr:
            ledger_by_ay[f"{surn}{yr}"] = r
    ledger_by_num = {str(idx): r for idx, r in enumerate(catalog, start=1)}

    payload_refs_by_id: Dict[str, Dict[str, Any]] = {}
    payload_refs_by_key: Dict[str, Dict[str, Any]] = {}
    payload_refs_by_ay: Dict[str, Dict[str, Any]] = {}
    payload_refs_by_num: Dict[str, Dict[str, Any]] = {}
    for idx, pr in enumerate(refs, start=1):
        if not isinstance(pr, dict):
            continue
        rid = str(pr.get("ref_id") or "").strip()
        ck = str(pr.get("cite_key") or "").strip()
        if rid:
            payload_refs_by_id[rid.lower()] = pr
        if ck:
            payload_refs_by_key[ck.lower()] = pr
        payload_refs_by_num[str(idx)] = pr
        auth = str(pr.get("authors") or "")
        yr = str(pr.get("year") or "").strip()
        surn = extract_first_author_surname(auth)
        if surn and yr:
            payload_refs_by_ay[f"{surn}{yr}".lower()] = pr

    def _strip_year_letter(tl: str) -> str:
        return re.sub(r"((?:19|20)\d{2})[a-z]$", r"\1", tl)

    def find_ledger_ref(token: str) -> Any:
        tl = token.strip().lower()
        tl_norm = normalize_ref_id_token(token).lower()
        tl_base = _strip_year_letter(tl)
        # For pure numeric tokens, resolve against payload_refs_by_num FIRST so order matches bibliography!
        if tl.isdigit():
            if payload_refs_by_num:
                pr = payload_refs_by_num.get(tl)
                if pr is None:
                    return None
                pr_ck = str(pr.get("cite_key") or "").strip().lower()
                pr_rid = normalize_ref_id_token(str(pr.get("ref_id") or "")).lower()
                return ledger_by_key.get(pr_ck) or ledger_by_ref_id.get(pr_rid)
            return ledger_by_num.get(tl)
        return (
            ledger_by_ref_id.get(tl)
            or ledger_by_ref_id.get(tl_norm)
            or ledger_by_key.get(tl)
            or ledger_by_ay.get(tl)
            or ledger_by_ay.get(tl_base)
        )

    def find_payload_ref(token: str) -> Any:
        tl = token.strip().lower()
        tl_norm = normalize_ref_id_token(token).lower()
        tl_base = _strip_year_letter(tl)
        return (
            payload_refs_by_id.get(tl)
            or payload_refs_by_id.get(tl_norm)
            or payload_refs_by_key.get(tl)
            or payload_refs_by_ay.get(tl)
            or payload_refs_by_ay.get(tl_base)
            or payload_refs_by_num.get(tl)
        )

    # Axis 4: Metadata verification against ledger
    disclosed_conflicts_text = json.dumps(payload.get("conflicts") or [], ensure_ascii=False).lower()
    for pr in refs:
        if not isinstance(pr, dict):
            continue
        vstat = str(pr.get("verification_status") or "")
        rid = str(pr.get("ref_id") or "")
        ck = str(pr.get("cite_key") or "")
        lref = ledger_by_key.get(ck.lower()) or ledger_by_ref_id.get(rid.lower())
        if lref is None:
            if vstat != "unverified_source":
                add_err(
                    "FABRICATED_REFERENCE",
                    f"Reference '{rid or ck}' does not exist in source_ledger.json yet has verification_status='{vstat}'.",
                    "Either remove ungrounded reference or set verification_status='unverified_source' and add an author_confirmation_items entry.",
                )
            continue
        pyear = str(pr.get("year") or "").strip()
        lyear = str(lref.get("year") or "").strip()
        if pyear and lyear and pyear != lyear and vstat != "metadata_conflict" and (ck.lower() not in disclosed_conflicts_text and rid.lower() not in disclosed_conflicts_text):
            add_err(
                "METADATA_YEAR_MISMATCH",
                f"Reference '{rid or ck}' claims year='{pyear}' while source_ledger records '{lyear}' without conflict disclosure.",
                "Align year to source_ledger or set verification_status='metadata_conflict' and document in conflicts[].",
            )
        lsurname = str(lref.get("first_author_surname") or "").lower()
        pauth = str(pr.get("authors") or "").lower()
        if lsurname and len(lsurname) >= 2 and pauth and lsurname not in pauth and vstat != "metadata_conflict":
            add_err(
                "METADATA_AUTHOR_MISMATCH",
                f"Reference '{rid or ck}' authors='{pr.get('authors')}' omits ledger first-author surname '{lref.get('first_author_surname')}'.",
                "Restore exact author names from source_ledger.json.",
            )

    # Axis 3: Prose inline citations & structured section ref_ids resolution
    all_known_keys = set(ledger_by_key.keys()) | set(payload_refs_by_key.keys())
    all_known_surnames = {str(r.get("first_author_surname") or "").strip().lower() for r in catalog if r.get("first_author_surname")}
    for pr in refs:
        if isinstance(pr, dict) and pr.get("authors"):
            s = extract_first_author_surname(str(pr.get("authors")))
            if s:
                all_known_surnames.add(s.lower())
    prose_cites = _extract_prose_citations(manuscript, known_keys=all_known_keys, known_surnames=all_known_surnames)
    for tok in prose_cites:
        lref = find_ledger_ref(tok)
        pref = find_payload_ref(tok)
        if lref is None and not (pref and pref.get("verification_status") == "unverified_source"):
            add_err(
                "UNRESOLVED_INLINE_CITATION",
                f"Inline citation '[{tok}]' in manuscript_markdown does not resolve to source_ledger or an explicitly marked unverified_source entry.",
                f"Replace [{tok}] with a verified ref_id/cite_key or declare it in references[] with verification_status='unverified_source' + author confirmation.",
            )

    def _check_ref_token(tok_val: Any, context_label: str) -> None:
        tok_s = str(tok_val or "").strip()
        if not tok_s:
            return
        if find_ledger_ref(tok_s) is None:
            pr = find_payload_ref(tok_s)
            if not (pr and pr.get("verification_status") == "unverified_source"):
                add_err(
                    "UNRESOLVED_CLAIM_CITATION",
                    f"{context_label} references unknown citation '{tok_s}'.",
                    "Cite only verified ref_ids/cite_keys from source_ledger.json.",
                )

    def _check_locator_file(loc_val: Any, context_label: str, as_error: bool = False) -> None:
        loc_s = str(loc_val or "").strip()
        if not loc_s or loc_s in ("-", "unknown", "N/A"):
            return
        fpart = _split_locator_file(loc_s)
        if fpart.startswith("case.json") or fpart in ("inline", "task.md", "extracted", "abstract"):
            return
        if fpart and known_files and (fpart not in known_files and Path(fpart).name not in known_basenames):
            msg = f"{context_label} references non-existent input file '{fpart}' (locator '{loc_s}')."
            hint = "Use only exact relative file paths listed in case_plan.json inventory."
            if as_error:
                add_err("INVALID_EVIDENCE_LOCATOR", msg, hint)
            else:
                add_warn("INVALID_SOURCE_LOCATOR", msg, hint)

    for cm in claim_map:
        if not isinstance(cm, dict):
            continue
        for cid in cm.get("citation_ids") or []:
            _check_ref_token(cid, f"claim_citation_map[{cm.get('claim_id')}]")
        for loc in cm.get("source_locators") or []:
            _check_locator_file(loc, f"claim_citation_map[{cm.get('claim_id')}]", as_error=False)

    if isinstance(bg, dict):
        for cid in bg.get("citation_ids") or []:
            _check_ref_token(cid, "research_background.citation_ids")
        for loc in bg.get("evidence_locators") or []:
            _check_locator_file(loc, "research_background.evidence_locators", as_error=False)

    for g in rw_groups:
        if not isinstance(g, dict):
            continue
        for w in g.get("works_covered") or []:
            if isinstance(w, dict):
                _check_ref_token(w.get("ref_id"), f"related_work_groups[{g.get('group_id')}]")
                _check_locator_file(w.get("source_locator"), f"related_work_groups[{g.get('group_id')}][{w.get('ref_id')}]", as_error=False)

    for row in matrix:
        if isinstance(row, dict):
            _check_ref_token(row.get("ref_id"), "comparison_matrix")
            _check_locator_file(row.get("source_locator"), f"comparison_matrix[{row.get('ref_id')}]", as_error=False)

    # Axis 5: Research gap backing
    for g in gaps:
        if not isinstance(g, dict):
            continue
        gid = g.get("gap_id", "?")
        attr = g.get("attribution", "")
        srefs = g.get("supporting_ref_ids") or []
        for sr in srefs:
            _check_ref_token(sr, f"research_gaps[{gid}].supporting_ref_ids")
        for loc in g.get("supporting_evidence_locators") or []:
            _check_locator_file(loc, f"research_gaps[{gid}].supporting_evidence_locators", as_error=False)
        if attr != "author_judgment" and not srefs:
            add_err(
                "UNBACKED_RESEARCH_GAP",
                f"Research gap '{gid}' has empty supporting_ref_ids and attribution!='author_judgment'.",
                "Link supporting_ref_ids from cited works or set attribution='author_judgment' with inline [作者判断]/[AUTHOR_JUDGMENT] marker.",
            )
        if attr == "author_judgment" and "[作者判断]" not in manuscript and "[AUTHOR_JUDGMENT]" not in manuscript:
            add_err(
                "UNDISCLOSED_AUTHOR_JUDGMENT",
                f"Research gap '{gid}' uses author_judgment but manuscript_markdown lacks explicit [作者判断]/[AUTHOR_JUDGMENT] tag.",
                "Insert explicit [作者判断] or [AUTHOR_JUDGMENT] tag next to that gap sentence in manuscript_markdown.",
            )

    # Axis 6: Contribution alignment to own-paper evidence (allow linked methods/figures/tables; warn instead of blocking on non-file locators)
    for c in contribs:
        if not isinstance(c, dict):
            continue
        cid = c.get("contrib_id", "?")
        vstat = c.get("verification_status", "")
        locs = c.get("own_evidence_locators") or []
        linked_ft = c.get("linked_figures_tables") or []
        linked_m = c.get("linked_methods") or []
        if vstat != "author_confirm_required" and not locs and not linked_ft and not linked_m:
            add_err(
                "UNALIGNED_CONTRIBUTION",
                f"Contribution '{cid}' lacks own_evidence_locators and linked_methods/linked_figures_tables while verification_status!='author_confirm_required'.",
                "Attach exact own-paper locators (methods/results/figures/tables) or mark verification_status='author_confirm_required' + [待作者确认: GAP-XX].",
            )
        elif vstat != "author_confirm_required" and not locs:
            add_warn(
                "CONTRIBUTION_MISSING_FILE_LOCATOR",
                f"Contribution '{cid}' has linked methods/figures but empty own_evidence_locators[].",
                "Add file:Lxx locators to own_evidence_locators[] for full provenance traceability.",
            )
        for loc in locs:
            loc_s = str(loc or "").strip()
            # Accept section/table/figure labels (e.g. 'Table 2', 'Fig. 1', 'Section 3.2') with a gentle warning; check actual file paths strictly
            if re.match(r"^(?:Table|Tab\.|Fig(?:ure)?\.?|Section|Sec\.|Eq\.?|图|表|节)\s+", loc_s, re.I):
                add_warn(
                    "LOCATOR_NOT_FILE_PATH",
                    f"Contribution '{cid}' locator '{loc_s}' is a manuscript label rather than an input file:line path.",
                    "Prefer input file:line locators alongside Table/Fig labels when available.",
                )
            else:
                _check_locator_file(loc, f"Contribution '{cid}'", as_error=False)

    # Axis 7: Grounded numeric metrics check (ERROR on structured fields, advisory WARNING on prose)
    grounded_tokens = list(ledger.get("grounded_numbers_index") or [])
    if extracted_dir.exists():
        seen_ext_n = {(round(float(gt["value"]), 6), str(gt.get("unit") or ""), bool(gt.get("has_percent"))) for gt in grounded_tokens}
        for ep in sorted(extracted_dir.glob("*.txt"))[:16]:
            etxt, _ = read_text_safe(ep)
            if not etxt:
                continue
            for ln in etxt.splitlines()[:240]:
                body_part = re.sub(r"^\[[^\]]+\]\s*", "", ln)
                is_tbl = any(ext_tok in ep.name.lower() for ext_tok in ("csv", "tsv", "json", "md", "table"))
                for ntok in extract_numeric_tokens(body_part, filter_structural=True):
                    nk = (round(float(ntok["value"]), 6), str(ntok.get("unit") or ""), bool(ntok.get("has_percent")))
                    if nk not in seen_ext_n:
                        seen_ext_n.add(nk)
                        ntok_copy = dict(ntok)
                        ntok_copy["source_locator"] = ep.name if not is_tbl else f"{ep.name}:table"
                        grounded_tokens.append(ntok_copy)
                        if len(grounded_tokens) >= 1800:
                            break
                if len(grounded_tokens) >= 1800:
                    break
            if len(grounded_tokens) >= 1800:
                break

    ungrounded_struct_hits: List[str] = []
    ungrounded_prose_hits: List[str] = []

    def _matches_grounded_or_delta(tok: Dict[str, Any]) -> bool:
        if any(numbers_match(tok, gt) for gt in grounded_tokens):
            return True
        tv = float(tok["value"])
        tu = str(tok.get("unit") or "").lower()
        t_pct = bool(tok.get("has_percent"))
        same_unit_vals: List[float] = []
        for gt in grounded_tokens:
            gu = str(gt.get("unit") or "").lower()
            g_pct = bool(gt.get("has_percent"))
            gv = float(gt["value"])
            # Controlled rounding tolerance ONLY within identical normalized units & percent flag (e.g. 94.6% vs 94.62%)
            if tu == gu and t_pct == g_pct and abs(tv - gv) <= 0.11:
                return True
            # Unitless CSV/table cell representing percentage value (e.g., 94.62 in results.csv vs 94.6% in prose)
            if t_pct and not gu and not g_pct and 0 < abs(tv) <= 100 and (gt.get("decimals", 0) > 0 or "table" in str(gt.get("source_locator") or "") or ".csv" in str(gt.get("source_locator") or "")):
                if abs(tv - gv) <= 0.11:
                    return True
            if (tu == gu) or (t_pct and g_pct):
                same_unit_vals.append(gv)
        if len(same_unit_vals) >= 2:
            pool = same_unit_vals[:60]
            tol = max(0.06, 0.01 * max(abs(tv), 1.0))
            for idx_a in range(len(pool)):
                va = pool[idx_a]
                for idx_b in range(idx_a + 1, len(pool)):
                    vb = pool[idx_b]
                    if abs(abs(va - vb) - abs(tv)) <= tol or abs((va + vb) - tv) <= tol:
                        return True
                    if t_pct and vb != 0 and abs(100.0 * abs(va - vb) / abs(vb) - abs(tv)) <= tol:
                        return True
                    if t_pct and va != 0 and abs(100.0 * abs(va - vb) / abs(va) - abs(tv)) <= tol:
                        return True
        return False

    def check_text_numbers(txt: str, context_label: str, bucket_hits: List[str]) -> None:
        if "[待作者确认" in txt or "[AUTHOR_CONFIRM" in txt:
            return
        for tok in extract_numeric_tokens(txt, filter_structural=True):
            if not (tok["has_percent"] or tok["unit"] or tok["decimals"] > 0 or abs(tok["value"]) >= 100):
                continue
            if not _matches_grounded_or_delta(tok):
                bucket_hits.append(f"{context_label}: '{tok['raw']}'")

    for c in contribs:
        if isinstance(c, dict):
            check_text_numbers(str(c.get("claim_text") or ""), f"contribution[{c.get('contrib_id')}]", ungrounded_struct_hits)
            for met in c.get("linked_metrics") or []:
                check_text_numbers(str(met), f"contribution_metric[{c.get('contrib_id')}]", ungrounded_struct_hits)
    for row in matrix:
        if isinstance(row, dict):
            check_text_numbers(str(row.get("key_result") or ""), f"matrix[{row.get('ref_id')}]", ungrounded_struct_hits)

    # Protect abbreviations (et al., Fig., Sec., e.g., i.e., vs.) from breaking sentences prematurely
    safe_ms = re.sub(r"\b(et\s+al|Fig|Tab|Sec|Eq|e\.g|i\.e|vs|Vol|No|Dr|Prof)\.", r"\1<DOT>", manuscript, flags=re.I)
    for s_idx, sent in enumerate(re.split(r"(?:(?<=[。！？!?])\s*|(?<!\d)\.(?:\s+|$)|\n+)", safe_ms), start=1):
        sent = sent.replace("<DOT>", ".")
        if re.match(r"^\s*#{1,6}\s+", sent):
            continue
        if any(w in sent.lower() for w in ("%", "％", "accuracy", "f1", "rmse", "mae", "auc", "speedup", "precision", "recall", "latency", "loss", "bleu", "提升", "降低", "达到", "准确率", "误差", "效率")):
            sent_check = re.sub(r"(?:[<>≤≥]\s*|above\s+|below\s+|under\s+|over\s+|不足\s*)?\d+(?:\.\d+)?(?:\s*[A-Za-zμ%％/0-9]+)?\s*(?:以上|以下|以内)", " ", sent, flags=re.I)
            sent_check = re.sub(r"(?:[<>≤≥]|above|below|under|over|不足)\s*\d+(?:\.\d+)?(?:\s*[A-Za-zμ%％/0-9]+)?", " ", sent_check, flags=re.I)
            check_text_numbers(sent_check, f"prose_S{s_idx}", ungrounded_prose_hits)

    if ungrounded_struct_hits:
        add_err(
            "UNGROUNDED_METRIC",
            f"Structured metrics not traceable to source_ledger.json: {ungrounded_struct_hits[:6]}",
            "Verify numbers against evidence_digest.md or annotate unverified figures with [待作者确认: GAP-XX]/[AUTHOR_CONFIRM: GAP-XX].",
        )
    if ungrounded_prose_hits:
        add_warn(
            "PROSE_METRIC_UNVERIFIED",
            f"Prose numbers not directly matched in source_ledger.json (may be derived/contextual): {ungrounded_prose_hits[:6]}",
            "Confirm prose numbers match extracted sources or add [待作者确认: GAP-XX] if unverified.",
        )

    # Axis 8: Conflict disclosure
    ledger_confs = [c["conflict_id"] for c in (ledger.get("conflicts") or [])]
    payload_confs = {str(c.get("conflict_id") or "") for c in (payload.get("conflicts") or []) if isinstance(c, dict)}
    payload_unc_blob = json.dumps(payload.get("uncertainty") or [], ensure_ascii=False)
    for cid in ledger_confs:
        if cid not in payload_confs and cid not in payload_unc_blob:
            add_err(
                "UNDISCLOSED_CONFLICT",
                f"Cross-source conflict '{cid}' detected in source_ledger.json is missing from payload.conflicts[] and uncertainty[].",
                f"Include {cid} in payload.conflicts[] with both source values and author confirmation flag.",
            )

    # Axis 9: Bidirectional GAP-XX alignment (allow notes after GAP-XX inside brackets + skeleton items)
    inline_gaps = set(re.findall(r"\[(?:待作者确认|AUTHOR_CONFIRM)\s*:\s*(GAP-\d+)\b[^\]]*\]", manuscript))
    item_gaps = {str(it.get("gap_id") or "").strip() for it in (payload.get("author_confirmation_items") or []) if isinstance(it, dict)}
    for ig in sorted(inline_gaps - item_gaps):
        add_err(
            "ORPHAN_INLINE_GAP",
            f"Inline marker '{ig}' in manuscript_markdown has no entry in author_confirmation_items[].",
            f"Add {ig} to author_confirmation_items[] with a closed-ended question.",
        )
    if status == "completed":
        for itg in sorted(item_gaps):
            if itg and itg not in inline_gaps and itg not in payload_unc_blob:
                item_obj = next((x for x in (payload.get("author_confirmation_items") or []) if isinstance(x, dict) and x.get("gap_id") == itg), {})
                loc_str = str(item_obj.get("location_in_draft") or "").lower()
                issue_str = str(item_obj.get("issue_type") or "").lower()
                if (
                    "global" not in loc_str
                    and "references" not in loc_str
                    and "format" not in loc_str
                    and "manuscript_draft" not in loc_str
                    and issue_str not in (
                        "missing_metadata", "missing_venue", "missing_year", "missing_doi",
                        "incomplete_reference_metadata", "unspecified_citation_style",
                        "missing_own_paper_evidence", "missing_referenced_file",
                        "unresolved_draft_citation", "empty_literature_library",
                    )
                ):
                    add_warn(
                        "MISSING_INLINE_GAP_TAG",
                        f"Confirmation item '{itg}' (location='{loc_str}') is not tagged inline in manuscript_markdown.",
                        f"Add [待作者确认: {itg}] or [AUTHOR_CONFIRM: {itg}] at '{loc_str}' in manuscript_markdown.",
                    )

    # Advisory Warnings (Quality & Style Rubric from rubric_rules.json)
    for pat in cliche_patterns:
        m = re.search(pat, manuscript, re.I)
        if m:
            add_warn(
                "HOLLOW_CLICHE_OPENING",
                f"Detected cliché phrase '{m.group(0)}' — weakens '立得住' background specificity.",
                "Replace with specific domain lineage, bottleneck mechanism, and cited constraint.",
            )
            break

    # Citation dump warning (true span >= 4 like [1-4], never [3-4]!)
    for dm in re.finditer(r"\[([^\]]+)\]", manuscript):
        inner = dm.group(1).strip()
        rm = re.match(r"^R?(\d+)\s*[-–]\s*R?(\d+)$", inner)
        if rm and (int(rm.group(2)) - int(rm.group(1)) + 1) >= 4:
            add_warn("CITATION_DUMP", f"Range citation dump '[{inner}]' detected.", "Split and anchor citations to specific methods or claims.")
        elif len([p for p in inner.split(",") if p.strip()]) >= 4 and not any(t in inner for t in ("待作者确认", "AUTHOR_CONFIRM")):
            add_warn("CITATION_DUMP", f"Multi-citation cluster '[{inner}]' detected.", "Ensure each cited work carries its specific finding or limitation.")

    # Sequential chronological stitching heuristic in Related Work (both line-start & mid-paragraph sequences)
    rw_m = re.search(r"(?:相关工作|Related\s+Work)(.*)", manuscript, re.I | re.S)
    if rw_m:
        rw_chunk = rw_m.group(1)
        seq_hits = re.findall(r"(?:\bIn\s+(?:19|20)\d{2}\b|(?:19|20)\d{2}年|\b[A-Z][a-z]+\s+et\s+al\.\s*\(\d{4}\))", rw_chunk)
        if len(seq_hits) >= 4 and len(rw_groups) < 2:
            add_warn("CHRONOLOGICAL_RELATED_WORK", "Related Work appears organized sequentially by year/author rather than grouped by route.", "Organize into >=2 technical route subsections comparing trade-offs.")

    # Overclaiming checks in contributions
    for c in contribs:
        if not isinstance(c, dict):
            continue
        ct = str(c.get("claim_text") or "")
        for opat in overclaim_patterns:
            om = re.search(opat, ct, re.I)
            if om:
                add_warn("CONTRIBUTION_OVERCLAIM", f"Contribution '{c.get('contrib_id')}' contains unqualified superlative '{om.group(0)}'.", "Qualify the claim with exact dataset/operating regime scope.")
        if not str(c.get("scope_qualification") or "").strip():
            add_warn("MISSING_SCOPE_QUALIFICATION", f"Contribution '{c.get('contrib_id')}' lacks scope_qualification.", "Specify tested conditions/datasets/regimes.")

    # Applicability/limitation check in related_work_groups
    for g in rw_groups:
        if not isinstance(g, dict):
            continue
        for w in g.get("works_covered") or []:
            if isinstance(w, dict) and (not str(w.get("applicability_conditions") or "").strip() or not str(w.get("limitations") or "").strip()):
                add_warn("MISSING_WORK_LIMITATION", f"Related work '{w.get('ref_id')}' in group '{g.get('group_id')}' omits applicability_conditions or limitations.", "State both applicable regime and concrete limitation.")

    # Related Work bridge synthesis check
    if status == "completed" and rw_groups and rw_m:
        rw_chunk_l = rw_m.group(1).lower()
        if not any(kw in rw_chunk_l for kw in ("this work", "our method", "our approach", "our study", "in contrast", "unlike prior", "bridge", "本文", "相比之下", "针对上述", "不同于现有", "弥补上述")):
            add_warn(
                "MISSING_BRIDGE_SYNTHESIS",
                "Related Work section does not explicitly bridge prior routes back to this paper's design choice.",
                "Add a concluding bridge paragraph showing how this work addresses the grouped limitations.",
            )

    # Coverage ratio check (real cited references vs skeleton padding)
    excluded = {str(e.get("ref_id") or "").lower() for e in (payload.get("excluded_references") or []) if isinstance(e, dict)}
    cited_in_prose_rids: Set[str] = set()
    for tok in prose_cites:
        lr = find_ledger_ref(tok)
        if lr and lr.get("ref_id"):
            cited_in_prose_rids.add(str(lr["ref_id"]).lower())
    for cm in claim_map:
        if isinstance(cm, dict):
            for cid in cm.get("citation_ids") or []:
                lr = find_ledger_ref(str(cid))
                if lr and lr.get("ref_id"):
                    cited_in_prose_rids.add(str(lr["ref_id"]).lower())
    payload_rids = {str(r.get("ref_id") or "").lower() for r in refs if isinstance(r, dict)}
    effective_ids = (cited_in_prose_rids | excluded) if (manuscript.strip() and task_mode != "fill_and_audit_citations") else (payload_rids | excluded)
    cov_ratio = (len(effective_ids) / len(catalog)) if catalog else 1.0
    if catalog and cov_ratio < 0.8:
        add_warn("LOW_CITATION_COVERAGE", f"Cited/excluded reference coverage is {cov_ratio:.0%} ({len(effective_ids)}/{len(catalog)}).", "Cite remaining relevant references in prose or document exclusions in excluded_references[].")
    if status == "completed" and len(payload_rids) >= 3 and manuscript.strip() and len(cited_in_prose_rids) / len(payload_rids) < 0.6:
        add_warn("UNCITED_PAYLOAD_REFERENCES", f"Only {len(cited_in_prose_rids)}/{len(payload_rids)} payload.references[] entries are cited in prose/claim_map.", "Cite active references in prose or move unused ones to excluded_references[].")

    if args.strict and warnings:
        errors.extend(warnings)
        warnings = []

    report = {
        "case_id": plan.get("case_id"),
        "passed": len(errors) == 0,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "metrics": {
            "ledger_references": len(catalog),
            "payload_references": len(refs),
            "coverage_ratio": round(cov_ratio, 3),
            "related_work_groups": len(rw_groups),
            "research_gaps": len(gaps),
            "contributions": len(contribs),
            "claim_citation_links": len(claim_map),
            "conflicts_disclosed": len(payload.get("conflicts") or []),
            "author_confirmation_items": len(payload.get("author_confirmation_items") or []),
        },
        "errors": errors,
        "warnings": warnings,
    }
    save_json(Path(args.out), report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
