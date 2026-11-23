#!/usr/bin/env python3
"""Detect intra-paper and cross-paper entity alignment & collision candidates.

Produces a structured candidate list with explicit non-merge guardrails for generic
material/method/sample terms and potential homonyms so the Agent never blindly merges
distinct physical samples, model instances, datasets, or experimental conditions.
Pure Python 3.10+ standard library.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


INSTANCE_SENSITIVE_TYPES = {"Material", "Method", "Dataset_Or_Sample", "Task"}
STOPWORDS = {
    "the", "of", "and", "in", "for", "with", "on", "to", "a", "an", "by", "from", "using", "via", "based", "method", "model"
}


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    return re.sub(r"\s+", " ", s).strip()


def norm_alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", norm_text(s).lower())


def content_tokens(s: str) -> Set[str]:
    toks = re.findall(r"[a-z0-9\u4e00-\u9fff]+", norm_text(s).lower())
    return {t for t in toks if len(t) > 1 and t not in STOPWORDS}


def extract_initials(s: str) -> Set[str]:
    words = [w for w in re.split(r"[\s\-_/]+", norm_text(s)) if w]
    inits: Set[str] = set()
    if len(words) >= 2:
        inits.add("".join(w[0].lower() for w in words if w[0].isalnum()))
        non_stop = [w for w in words if w.lower() not in STOPWORDS and w[0].isalnum()]
        if len(non_stop) >= 2:
            inits.add("".join(w[0].lower() for w in non_stop))
    return {x for x in inits if 2 <= len(x) <= 8}


def extract_paren_abbrs(s: str) -> Set[str]:
    found = set()
    for m in re.finditer(r"\(([A-Za-z0-9\-/]{2,12})\)", s or ""):
        found.add(norm_alnum(m.group(1)))
    return found


def all_surface_forms(ent: Dict[str, Any]) -> List[str]:
    forms: List[str] = []
    for k in ("canonical_name", "name"):
        if ent.get(k):
            forms.append(str(ent[k]))
    for a in ent.get("aliases", []) or []:
        if a and str(a) not in forms:
            forms.append(str(a))
    for m in ent.get("mentions", []) or []:
        if isinstance(m, dict) and m.get("surface_form"):
            sf = str(m["surface_form"])
            if sf not in forms:
                forms.append(sf)
    return forms


def primary_source_id(ent: Dict[str, Any]) -> str:
    if ent.get("source_id"):
        return str(ent["source_id"])
    sids = ent.get("source_ids") or []
    if sids:
        return str(sids[0])
    mentions = ent.get("mentions") or []
    if mentions and isinstance(mentions[0], dict) and mentions[0].get("source_id"):
        return str(mentions[0]["source_id"])
    return "unknown"


def compare_pair(ent_a: Dict[str, Any], ent_b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    forms_a = all_surface_forms(ent_a)
    forms_b = all_surface_forms(ent_b)
    if not forms_a or not forms_b:
        return None

    alnums_a = {norm_alnum(f) for f in forms_a if norm_alnum(f)}
    alnums_b = {norm_alnum(f) for f in forms_b if norm_alnum(f)}
    inits_a: Set[str] = set()
    for f in forms_a:
        inits_a |= extract_initials(f) | extract_paren_abbrs(f)
    inits_b: Set[str] = set()
    for f in forms_b:
        inits_b |= extract_initials(f) | extract_paren_abbrs(f)

    signals: List[str] = []
    score = 0.0

    # 1. Exact normalized surface match
    exact_overlap = alnums_a & alnums_b
    if exact_overlap:
        signals.append(f"exact_normalized_match:{sorted(exact_overlap)[0]}")
        score = max(score, 1.0)

    # 2. Acronym / initialism match
    abbr_overlap = (alnums_a & inits_b) | (alnums_b & inits_a)
    if abbr_overlap:
        signals.append(f"acronym_or_abbreviation_match:{sorted(abbr_overlap)[0]}")
        score = max(score, 0.92)

    # 3. Token Jaccard & SequenceMatcher fuzzy similarity
    best_fuzzy = 0.0
    best_tok_jacc = 0.0
    for fa in forms_a:
        na = norm_text(fa).lower()
        ta = content_tokens(fa)
        for fb in forms_b:
            nb = norm_text(fb).lower()
            tb = content_tokens(fb)
            if min(len(na), len(nb)) >= 4:
                r = SequenceMatcher(None, na, nb).ratio()
                if r > best_fuzzy:
                    best_fuzzy = r
            if ta and tb:
                j = len(ta & tb) / len(ta | tb)
                if j > best_tok_jacc:
                    best_tok_jacc = j
            # Substring containment for sufficiently specific multi-char names
            if min(len(na), len(nb)) >= 6 and (na in nb or nb in na) and na != nb:
                if "substring_containment" not in signals:
                    signals.append("substring_containment")
                score = max(score, 0.80)

    if best_tok_jacc >= 0.60:
        signals.append(f"token_jaccard:{best_tok_jacc:.2f}")
        score = max(score, round(best_tok_jacc, 3))
    if best_fuzzy >= 0.78:
        signals.append(f"fuzzy_ratio:{best_fuzzy:.2f}")
        score = max(score, round(best_fuzzy, 3))

    if not signals:
        return None

    type_a = str(ent_a.get("entity_type") or "Unknown")
    type_b = str(ent_b.get("entity_type") or "Unknown")
    sid_a = primary_source_id(ent_a)
    sid_b = primary_source_id(ent_b)
    cross_paper = sid_a != sid_b

    risk_flags: List[str] = []
    if type_a != type_b:
        risk_flags.append(f"entity_type_mismatch:{type_a}_vs_{type_b}")
    if exact_overlap and min(len(x) for x in exact_overlap) <= 5:
        risk_flags.append("short_acronym_homonym_risk")
    if cross_paper and (type_a in INSTANCE_SENSITIVE_TYPES or type_b in INSTANCE_SENSITIVE_TYPES):
        risk_flags.append("cross_paper_generic_term_check_distinct_instance")

    if "entity_type_mismatch" in " ".join(risk_flags) or "short_acronym_homonym_risk" in risk_flags:
        guidance = (
            "Potential homonym or cross-type collision: verify definitions in both paragraphs. "
            "If they denote different objects, set verdict='keep_separate_homonym'."
        )
    elif "cross_paper_generic_term_check_distinct_instance" in risk_flags:
        guidance = (
            "Cross-paper shared generic material/method/task/sample term: DO NOT blindly merge physical samples, "
            "model instances, datasets, or experimental conditions. If instances differ across papers, set "
            "verdict='keep_separate_distinct_instance' (optionally set shared_concept_name to link via Concept "
            "without merging samples/conditions/results). Only use verdict='merge_alias' if both refer to the "
            "exact same canonical entity/benchmark/standard method with no conflated experimental results."
        )
    else:
        guidance = (
            "Verify exact definitions and textual context. Merge via verdict='merge_alias' only if clearly the same "
            "canonical concept/researcher/metric; otherwise keep separate or mark 'uncertain_no_merge'."
        )

    return {
        "similarity_score": round(score, 3),
        "signals": signals,
        "cross_paper": cross_paper,
        "risk_flags": risk_flags,
        "guidance": guidance,
        "candidate_a": {
            "id": ent_a.get("id") or ent_a.get("temp_id"),
            "canonical_name": ent_a.get("canonical_name") or ent_a.get("name"),
            "entity_type": type_a,
            "source_id": sid_a,
            "aliases": ent_a.get("aliases", []),
        },
        "candidate_b": {
            "id": ent_b.get("id") or ent_b.get("temp_id"),
            "canonical_name": ent_b.get("canonical_name") or ent_b.get("name"),
            "entity_type": type_b,
            "source_id": sid_b,
            "aliases": ent_b.get("aliases", []),
        },
    }


def detect_candidates(base_nodes: List[Dict[str, Any]], new_entities: List[Dict[str, Any]]) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    cid = 0

    # Cross-set comparison (existing KG nodes vs newly extracted entities)
    if new_entities:
        for na in base_nodes:
            if na.get("entity_type") == "Concept":
                continue
            for nb in new_entities:
                res = compare_pair(na, nb)
                if res:
                    cid += 1
                    res["candidate_id"] = f"CAND_{cid:03d}"
                    res["comparison_scope"] = "existing_vs_new"
                    candidates.append(res)

        # Also check intra-new duplicates
        for i in range(len(new_entities)):
            for j in range(i + 1, len(new_entities)):
                res = compare_pair(new_entities[i], new_entities[j])
                if res:
                    cid += 1
                    res["candidate_id"] = f"CAND_{cid:03d}"
                    res["comparison_scope"] = "within_new_extraction"
                    candidates.append(res)
    else:
        # Intra-KG / single-file check
        for i in range(len(base_nodes)):
            for j in range(i + 1, len(base_nodes)):
                if base_nodes[i].get("entity_type") == "Concept" or base_nodes[j].get("entity_type") == "Concept":
                    continue
                res = compare_pair(base_nodes[i], base_nodes[j])
                if res:
                    cid += 1
                    res["candidate_id"] = f"CAND_{cid:03d}"
                    res["comparison_scope"] = "within_base_kg"
                    candidates.append(res)

    candidates.sort(key=lambda x: (-x["similarity_score"], not x["cross_paper"], x["candidate_id"]))

    decision_skeleton = []
    for idx, c in enumerate(candidates, start=1):
        decision_skeleton.append(
            {
                "decision_id": f"A{idx:03d}",
                "candidate_id": c["candidate_id"],
                "candidate_a": {
                    "node_id_or_name": c["candidate_a"]["id"] or c["candidate_a"]["canonical_name"],
                    "source_id": c["candidate_a"]["source_id"],
                },
                "candidate_b": {
                    "node_id_or_name": c["candidate_b"]["id"] or c["candidate_b"]["canonical_name"],
                    "source_id": c["candidate_b"]["source_id"],
                },
                "verdict": "TODO: merge_alias | keep_separate_distinct_instance | keep_separate_homonym | uncertain_no_merge",
                "canonical_target_id": c["candidate_a"]["id"] if c["candidate_a"].get("id") else None,
                "shared_concept_name": None,
                "rationale": "TODO: explain merge or non-merge basis grounded in paper quotes",
                "evidence": [],
            }
        )

    return {
        "schema_version": "1.0.0",
        "base_node_count": len(base_nodes),
        "new_entity_count": len(new_entities),
        "candidate_count": len(candidates),
        "high_risk_non_merge_check_count": sum(1 for c in candidates if c["risk_flags"]),
        "candidates": candidates,
        "decision_template": decision_skeleton,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect cross-paper & intra-paper entity alignment/collision candidates.")
    parser.add_argument("--base-kg", required=True, help="Path to existing kg_v1.json (or extraction JSON with nodes/entities)")
    parser.add_argument("--new-extraction", default=None, help="Path to new paper extraction JSON (optional for intra-KG check)")
    parser.add_argument("--out", required=True, help="Output path for alignment_candidates.json")
    args = parser.parse_args()

    base_data = json.loads(Path(args.base_kg).read_text(encoding="utf-8"))
    base_nodes = base_data.get("nodes") or base_data.get("entities") or []

    new_entities: List[Dict[str, Any]] = []
    if args.new_extraction and Path(args.new_extraction).exists():
        new_data = json.loads(Path(args.new_extraction).read_text(encoding="utf-8"))
        new_entities = new_data.get("entities") or new_data.get("nodes") or []

    report = detect_candidates(base_nodes, new_entities)
    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "ok",
                "candidate_count": report["candidate_count"],
                "high_risk_non_merge_check_count": report["high_risk_non_merge_check_count"],
                "out": str(out_p),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
