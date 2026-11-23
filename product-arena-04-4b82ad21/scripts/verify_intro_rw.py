#!/usr/bin/env python3
"""Step 5: 10-Axis Mechanical Verification & Self-Correction Auditor.

Separates deterministic blocking ERRORS (exit code 1) from advisory WARNINGS:
Errors:
  1. Missing case_id or required output_contract fields.
  2. Missing core sections (Background, Related Work, Gaps, Contributions, References) when status=='completed'.
  3. Unresolved inline citations in prose/claim_citation_map not matched to ledger or marked unverified.
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
from typing import Any, Dict, List, Set

from common import extract_numeric_tokens, load_json_safe, numbers_match, save_json

DEFAULT_CLICHES_ZH = [
    r"近年来[^。\n]{0,25}(?:受到|引起)[^。\n]{0,15}广泛关注",
    r"随着[^。\n]{0,30}(?:快速发展|迅猛发展|不断进步|日新月异)",
    r"在当今[^。\n]{0,25}时代",
    r"具有重要(?:理论和实际|科学|现实|研究)?意义",
    r"已成为(?:当前|学术界|工业界)的研究热点",
]
DEFAULT_CLICHES_EN = [
    r"in recent years,?.{0,40}attracted\s+(?:widespread|increasing|great)\s+attention",
    r"with the rapid\s+(?:development|advancement|growth)\s+of",
    r"has become a hot\s+(?:topic|research\s+area)",
]
DEFAULT_OVERCLAIMS = [
    r"首次彻底解决",
    r"全面超越所有",
    r"完美解决",
    r"适用于所有(?:场景|任务|条件)",
    r"first\s+ever\s+to\s+completely",
    r"universally\s+outperforms\s+all",
    r"completely\s+solves",
]


def _split_locator_file(loc: str) -> str:
    if not loc:
        return ""
    if loc.startswith("case.json"):
        return "case.json"
    return loc.split(":", 1)[0].strip()


def _extract_prose_citations(text: str) -> List[str]:
    found: List[str] = []
    # Mask file/line locators like [lit_notes.md:L2] before scanning citations
    cleaned = re.sub(r"\[[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,5}:[^\]]+\]", " ", text or "")
    # [1], [R01], [1, 2], [Smith2023]
    for m in re.finditer(r"\[([A-Za-z0-9_,\s;:-]+)\]", cleaned):
        inner = m.group(1).strip()
        if any(tag in inner for tag in ("待作者确认", "AUTHOR_CONFIRM", "作者判断", "AUTHOR_JUDGMENT", "待补充")):
            continue
        # Range like [1-3] -> expand numeric or keep
        rm = re.match(r"^R?(\d+)\s*[-–]\s*R?(\d+)$", inner)
        if rm:
            a, b = int(rm.group(1)), int(rm.group(2))
            if 0 < a <= b <= a + 20:
                prefix = "R" if "R" in inner else ""
                for k in range(a, b + 1):
                    found.append(f"{prefix}{k:02d}" if prefix else str(k))
            continue
        for part in re.split(r"[,;]", inner):
            p = part.strip()
            # Match R01 / 1 / Smith2023 / Alpha2022Fluid (not plain English/unit prose like [作者判断] or [vs. RHE])
            if p and (re.match(r"^R?\d{1,3}$", p) or re.match(r"^[A-Za-z][A-Za-z0-9_:-]{2,32}$", p) and re.search(r"\d", p)):
                found.append(p)
    # \cite{k1, k2}
    for m in re.finditer(r"\\cite[a-z]*\{([^}]+)\}", cleaned):
        for k in m.group(1).split(","):
            if k.strip():
                found.append(k.strip())
    # (Surname, 2023) or (Surname et al., 2023)
    for m in re.finditer(r"\(([A-Z][A-Za-z-]+(?:\s+et\s+al\.)?),?\s*((?:19|20)\d{2}[a-z]?)\)", cleaned):
        surname = m.group(1).split()[0].rstrip(",")
        found.append(f"{surname}{m.group(2)}")
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description="10-axis mechanical verifier for Introduction & Related Work payload.")
    ap.add_argument("--payload", default="/app/work/section_payload.json")
    ap.add_argument("--ledger", default="/app/work/source_ledger.json")
    ap.add_argument("--case-plan", default="/app/work/case_plan.json")
    ap.add_argument("--out", default="/app/work/verification_report.json")
    ap.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    args = ap.parse_args()

    payload, p_err = load_json_safe(Path(args.payload))
    ledger, l_err = load_json_safe(Path(args.ledger))
    plan, c_err = load_json_safe(Path(args.case_plan))

    if p_err or not isinstance(payload, dict):
        raise SystemExit(f"Cannot load payload: {p_err}")
    if l_err or not isinstance(ledger, dict):
        raise SystemExit(f"Cannot load ledger: {l_err}")
    if c_err or not isinstance(plan, dict):
        raise SystemExit(f"Cannot load case_plan: {c_err}")

    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    def add_err(code: str, detail: str, fix: str) -> None:
        errors.append({"code": code, "detail": detail, "fix_hint": fix})

    def add_warn(code: str, detail: str, fix: str) -> None:
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

    # Axis 1: case_id & output_contract check
    expected_cid = plan.get("case_id", "")
    if payload.get("case_id") != expected_cid:
        add_err("CASE_ID_MISMATCH", f"payload.case_id={payload.get('case_id')!r} != {expected_cid!r}", "Set payload.case_id to exact case_id from case_plan.json.")

    req_keys = (plan.get("output_contract") or {}).get("required_keys") or []
    overrides = payload.get("contract_overrides") or {}
    payload_roles = {
        "case_id": payload.get("case_id"),
        "status": payload.get("status"),
        "manuscript_markdown": payload.get("manuscript_markdown"),
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
    }
    for fspec in (plan.get("output_contract") or {}).get("fields") or []:
        fname = fspec["name"]
        if fname in req_keys:
            role = fspec.get("semantic_role", "custom")
            if fname in overrides or fname in payload:
                continue
            if role in payload_roles and payload_roles[role] is not None:
                continue
            add_err("MISSING_CONTRACT_FIELD", f"Required output_contract key '{fname}' (role={role}) has no mapping.", f"Populate contract_overrides['{fname}'] in section_payload.json.")

    # Axis 10: Dishonest status when materials empty
    if status == "completed" and not catalog and not own_ev and not fig_mentions:
        add_err("DISHONEST_STATUS", "status='completed' but both literature catalog and own-paper evidence are empty.", "Change status to 'insufficient_material' or 'partial' and list missing items in uncertainty[].")

    # Axis 2: Core completeness when completed (mode-aware for targeted subtasks)
    task_mode = str((plan.get("signals") or {}).get("task_mode") or "draft_intro_rw")
    manuscript = str(payload.get("manuscript_markdown") or "")
    bg = payload.get("research_background") or {}
    rw_groups = payload.get("related_work_groups") or []
    matrix = payload.get("comparison_matrix") or []
    gaps = payload.get("research_gaps") or []
    contribs = payload.get("contributions") or []
    refs = payload.get("references") or []

    if status == "completed":
        if not manuscript.strip() and not (task_mode == "organize_related_work" and rw_groups and matrix):
            add_err("EMPTY_MANUSCRIPT", "manuscript_markdown is empty while status=='completed'.", "Write publication-ready prose matching the requested scope.")
        if task_mode == "organize_related_work":
            if not rw_groups:
                add_err("MISSING_RELATED_WORK", "related_work_groups[] is empty.", "Group literature into methodological/problem routes.")
            if not gaps:
                add_warn("MISSING_RESEARCH_GAPS", "No research_gaps[] supplied in organize_related_work mode.")
            if own_ev and not contribs:
                add_warn("MISSING_CONTRIBUTIONS", "Own-paper evidence exists but contributions[] is empty.")
        elif task_mode == "fill_and_audit_citations":
            if not refs:
                add_err("MISSING_REFERENCES", "references[] is empty in fill_and_audit_citations mode.", "Populate verified references from source_ledger.json.")
        elif task_mode == "align_contributions":
            if not contribs:
                add_err("MISSING_CONTRIBUTIONS", "contributions[] is empty in align_contributions mode.", "Provide evidence-bound contributions linked to methods/figures/tables/metrics.")
        else:
            if not isinstance(bg, dict) or not bg.get("concrete_problem"):
                add_err("MISSING_BACKGROUND", "research_background.concrete_problem missing.", "Populate research_background with domain_lineage, concrete_problem, technical_constraints, why_now.")
            if not rw_groups:
                add_err("MISSING_RELATED_WORK", "related_work_groups[] is empty.", "Group literature into at least 2 methodological/problem routes (or 1 explicit route if tiny corpus).")
            if not gaps:
                add_err("MISSING_RESEARCH_GAPS", "research_gaps[] is empty.", "Derive gaps from cited work limitations/conflicts or mark [作者判断]/[AUTHOR_JUDGMENT].")
            if not contribs:
                if own_ev or fig_mentions:
                    add_err("MISSING_CONTRIBUTIONS", "contributions[] is empty.", "Provide evidence-bound contributions linked to methods/figures/tables/metrics.")
                else:
                    add_warn("NO_OWN_EVIDENCE_FOR_CONTRIBUTIONS", "No own-paper evidence in input; contributions[] left empty or pending author confirmation.")

    # Build lookup tables for ledger references & payload references
    ledger_by_ref_id = {r["ref_id"].lower(): r for r in catalog}
    ledger_by_key = {r["cite_key"].lower(): r for r in catalog if r.get("cite_key")}
    ledger_by_ay = {
        f"{r['first_author_surname']}{r['year']}".lower(): r
        for r in catalog
        if r.get("first_author_surname") and r.get("year")
    }
    ledger_by_num = {str(idx): r for idx, r in enumerate(catalog, start=1)}

    def find_ledger_ref(token: str) -> Any:
        tl = token.strip().lower()
        return ledger_by_ref_id.get(tl) or ledger_by_key.get(tl) or ledger_by_ay.get(tl) or ledger_by_num.get(tl)

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
        # Extract surname+year
        auth = str(pr.get("authors") or "")
        yr = str(pr.get("year") or "")
        if auth and yr:
            first = re.split(r"\band\b|;|,|、", auth)[0].strip().split()[-1]
            payload_refs_by_ay[f"{first}{yr}".lower()] = pr

    def find_payload_ref(token: str) -> Any:
        tl = token.strip().lower()
        return payload_refs_by_id.get(tl) or payload_refs_by_key.get(tl) or payload_refs_by_ay.get(tl) or payload_refs_by_num.get(tl)

    # Axis 4: Metadata verification against ledger
    disclosed_conflicts_text = json.dumps(payload.get("conflicts") or [], ensure_ascii=False).lower()
    for pr in refs:
        if not isinstance(pr, dict):
            continue
        vstat = str(pr.get("verification_status") or "")
        rid = str(pr.get("ref_id") or "")
        ck = str(pr.get("cite_key") or "")
        lref = find_ledger_ref(ck) or find_ledger_ref(rid)
        if lref is None:
            if vstat != "unverified_source":
                add_err(
                    "FABRICATED_REFERENCE",
                    f"Reference '{rid or ck}' does not exist in source_ledger.json yet has verification_status='{vstat}'.",
                    "Either remove ungrounded reference or set verification_status='unverified_source' and add an author_confirmation_items entry.",
                )
            continue
        # Compare year if both present
        pyear = str(pr.get("year") or "").strip()
        lyear = str(lref.get("year") or "").strip()
        if pyear and lyear and pyear != lyear and vstat != "metadata_conflict" and (ck.lower() not in disclosed_conflicts_text and rid.lower() not in disclosed_conflicts_text):
            add_err(
                "METADATA_YEAR_MISMATCH",
                f"Reference '{rid or ck}' claims year='{pyear}' while source_ledger records '{lyear}' without conflict disclosure.",
                "Align year to source_ledger or set verification_status='metadata_conflict' and document in conflicts[].",
            )
        # Compare first author surname if both present
        lsurname = str(lref.get("first_author_surname") or "").lower()
        pauth = str(pr.get("authors") or "").lower()
        if lsurname and len(lsurname) >= 2 and pauth and lsurname not in pauth and vstat != "metadata_conflict":
            add_err(
                "METADATA_AUTHOR_MISMATCH",
                f"Reference '{rid or ck}' authors='{pr.get('authors')}' omits ledger first-author surname '{lref.get('first_author_surname')}'.",
                "Restore exact author names from source_ledger.json.",
            )

    # Axis 3: Prose inline citations & claim_citation_map resolution
    prose_cites = _extract_prose_citations(manuscript)
    for tok in prose_cites:
        lref = find_ledger_ref(tok)
        pref = find_payload_ref(tok)
        if lref is None and not (pref and pref.get("verification_status") == "unverified_source"):
            add_err(
                "UNRESOLVED_INLINE_CITATION",
                f"Inline citation '[{tok}]' in manuscript_markdown does not resolve to source_ledger or an explicitly marked unverified_source entry.",
                f"Replace [{tok}] with a verified ref_id/cite_key or declare it in references[] with verification_status='unverified_source' + author confirmation.",
            )

    for cm in payload.get("claim_citation_map") or []:
        if not isinstance(cm, dict):
            continue
        for cid in cm.get("citation_ids") or []:
            if find_ledger_ref(str(cid)) is None:
                pr = find_payload_ref(str(cid))
                if not (pr and pr.get("verification_status") == "unverified_source"):
                    add_err(
                        "UNRESOLVED_CLAIM_CITATION",
                        f"claim_citation_map[{cm.get('claim_id')}] cites unknown '{cid}'.",
                        "Cite only verified ref_ids/cite_keys from source_ledger.json.",
                    )

    # Axis 5: Research gap backing
    for g in gaps:
        if not isinstance(g, dict):
            continue
        gid = g.get("gap_id", "?")
        attr = g.get("attribution", "")
        srefs = g.get("supporting_ref_ids") or []
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

    # Axis 6: Contribution alignment to own-paper evidence
    for c in contribs:
        if not isinstance(c, dict):
            continue
        cid = c.get("contrib_id", "?")
        vstat = c.get("verification_status", "")
        locs = c.get("own_evidence_locators") or []
        if vstat != "author_confirm_required" and not locs:
            add_err(
                "UNALIGNED_CONTRIBUTION",
                f"Contribution '{cid}' lacks own_evidence_locators while verification_status!='author_confirm_required'.",
                "Attach exact own-paper locators (methods/results/figures/tables) or mark verification_status='author_confirm_required' + [待作者确认: GAP-XX].",
            )
        for loc in locs:
            fpart = _split_locator_file(str(loc))
            if fpart and known_files and fpart not in known_files:
                add_err(
                    "INVALID_EVIDENCE_LOCATOR",
                    f"Contribution '{cid}' references non-existent input file '{fpart}' (locator '{loc}').",
                    "Use only exact relative file paths listed in case_plan.json inventory.",
                )

    # Axis 7: Grounded numeric metrics check (contributions, comparison_matrix, prose metric contexts)
    grounded_tokens = ledger.get("grounded_numbers_index") or []
    ungrounded_hits: List[str] = []

    def check_text_numbers(txt: str, context_label: str) -> None:
        if "[待作者确认" in txt or "[AUTHOR_CONFIRM" in txt:
            return
        for tok in extract_numeric_tokens(txt, filter_structural=True):
            # Require checking floats, percentages, or tokens with physical units
            if not (tok["has_percent"] or tok["unit"] or tok["decimals"] > 0 or abs(tok["value"]) >= 100):
                continue
            if not any(numbers_match(tok, gt) for gt in grounded_tokens):
                ungrounded_hits.append(f"{context_label}: '{tok['raw']}'")

    for c in contribs:
        if isinstance(c, dict):
            check_text_numbers(str(c.get("claim_text") or ""), f"contribution[{c.get('contrib_id')}]")
            for met in c.get("linked_metrics") or []:
                check_text_numbers(str(met), f"contribution_metric[{c.get('contrib_id')}]")
    for row in payload.get("comparison_matrix") or []:
        if isinstance(row, dict):
            check_text_numbers(str(row.get("key_result") or ""), f"matrix[{row.get('ref_id')}]")
    # Check prose sentences containing numeric metrics (split on sentence-ending punctuation + whitespace or newline, NOT inside 92.4%)
    for s_idx, sent in enumerate(re.split(r"(?:(?<=[。！？!?])\s*|(?<!\d)\.(?:\s+|$)|\n+)", manuscript), start=1):
        if any(w in sent.lower() for w in ("%", "％", "accuracy", "f1", "rmse", "mae", "auc", "speedup", "提升", "降低", "达到", "准确率", "误差", "效率")):
            check_text_numbers(sent, f"prose_S{s_idx}")

    if ungrounded_hits:
        add_err(
            "UNGROUNDED_METRIC",
            f"Numbers not traceable to source_ledger.json: {ungrounded_hits[:6]}",
            "Verify numbers against evidence_digest.md or annotate unverified figures with [待作者确认: GAP-XX]/[AUTHOR_CONFIRM: GAP-XX].",
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

    # Axis 9: Bidirectional GAP-XX alignment
    inline_gaps = set(re.findall(r"\[(?:待作者确认|AUTHOR_CONFIRM)\s*:\s*(GAP-\d+)\]", manuscript))
    item_gaps = {str(it.get("gap_id") or "").strip() for it in (payload.get("author_confirmation_items") or []) if isinstance(it, dict)}
    for ig in sorted(inline_gaps - item_gaps):
        add_err(
            "ORPHAN_INLINE_GAP",
            f"Inline marker '{ig}' in manuscript_markdown has no entry in author_confirmation_items[].",
            f"Add {ig} to author_confirmation_items[] with a closed-ended question.",
        )
    for itg in sorted(item_gaps):
        if itg and itg not in inline_gaps and itg not in json.dumps(payload.get("uncertainty") or [], ensure_ascii=False):
            # Check if it's a global format/style item allowed in questionnaire; warn instead if global, error only if specific draft gap
            item_obj = next((x for x in (payload.get("author_confirmation_items") or []) if isinstance(x, dict) and x.get("gap_id") == itg), {})
            loc_str = str(item_obj.get("location_in_draft") or "").lower()
            issue_str = str(item_obj.get("issue_type") or "").lower()
            if (
                "global" not in loc_str
                and "references" not in loc_str
                and "format" not in loc_str
                and issue_str not in (
                    "missing_metadata", "missing_venue", "missing_year", "missing_doi",
                    "incomplete_reference_metadata", "unspecified_citation_style", "missing_own_paper_evidence",
                )
            ):
                add_err(
                    "MISSING_INLINE_GAP_TAG",
                    f"Confirmation item '{itg}' (location='{loc_str}') is not tagged inline in manuscript_markdown.",
                    f"Add [待作者确认: {itg}] or [AUTHOR_CONFIRM: {itg}] at '{loc_str}' in manuscript_markdown.",
                )

    # Advisory Warnings (Quality & Style Rubric)
    for pat in DEFAULT_CLICHES_ZH + DEFAULT_CLICHES_EN:
        m = re.search(pat, manuscript, re.I)
        if m:
            add_warn(
                "HOLLOW_CLICHE_OPENING",
                f"Detected cliché phrase '{m.group(0)}' — weakens '立得住' background specificity.",
                "Replace with specific domain lineage, bottleneck mechanism, and cited constraint.",
            )
            break

    # Citation dump warning ([1-4] or 4+ citations on one bracket)
    for dm in re.finditer(r"\[([^\]]+)\]", manuscript):
        inner = dm.group(1)
        rm = re.match(r"^\d+\s*[-–]\s*(\d+)$", inner.strip())
        if rm and int(rm.group(1)) >= 4:
            add_warn("CITATION_DUMP", f"Range citation dump '[{inner}]' detected.", "Split and anchor citations to specific methods or claims.")
        elif len([p for p in inner.split(",") if p.strip()]) >= 4:
            add_warn("CITATION_DUMP", f"Multi-citation cluster '[{inner}]' detected.", "Ensure each cited work carries its specific finding or limitation.")

    # Sequential chronological stitching heuristic in Related Work
    rw_chunk = ""
    rw_m = re.search(r"(?:相关工作|Related\s+Work)(.*)", manuscript, re.I | re.S)
    if rw_m:
        rw_chunk = rw_m.group(1)
        seq_hits = re.findall(r"(?:^|\n)\s*(?:In\s+(?:19|20)\d{2}|20\d{2}年|[A-Z][a-z]+\s+et\s+al\.\s*\(\d{4}\))", rw_chunk)
        if len(seq_hits) >= 3 and len(rw_groups) < 2:
            add_warn("CHRONOLOGICAL_RELATED_WORK", "Related Work appears organized sequentially by year/author rather than grouped by route.", "Organize into >=2 technical route subsections comparing trade-offs.")

    # Overclaiming checks in contributions
    for c in contribs:
        if not isinstance(c, dict):
            continue
        ct = str(c.get("claim_text") or "")
        for opat in DEFAULT_OVERCLAIMS:
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

    # Coverage ratio check
    excluded = {str(e.get("ref_id") or "").lower() for e in (payload.get("excluded_references") or []) if isinstance(e, dict)}
    covered_ids = {str(r.get("ref_id") or "").lower() for r in refs if isinstance(r, dict)} | excluded
    cov_ratio = (len(covered_ids) / len(catalog)) if catalog else 1.0
    if catalog and cov_ratio < 0.8:
        add_warn("LOW_CITATION_COVERAGE", f"Reference coverage is {cov_ratio:.0%} ({len(covered_ids)}/{len(catalog)}).", "Include remaining relevant references or document exclusions in excluded_references[].")

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
            "claim_citation_links": len(payload.get("claim_citation_map") or []),
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
