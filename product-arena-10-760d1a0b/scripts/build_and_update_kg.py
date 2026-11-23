#!/usr/bin/env python3
"""Deterministic Stage-1 KG builder (--mode init) and Stage-2+ incremental updater (--mode update).

Guarantees:
1. 100% preservation of all existing node IDs (E###, C###) and edge IDs (R###) across updates.
2. Strict per-source metadata and attribute isolation (never blends conditions/samples/numbers across papers).
3. Two-tier alignment execution:
   - merge_alias: merges verified aliases onto existing node while keeping per-mention provenance.
   - keep_separate_distinct_instance: preserves separate scoped nodes; optionally creates/reuses an abstract
     Concept node (C###) + INSTANTIATES_CONCEPT edges without merging physical samples/conditions/results.
   - keep_separate_homonym / uncertain_no_merge: keeps separate nodes and logs explicit refusal rationale.
4. Full incremental_update_log.json diff record.

Pure Python 3.10+ standard library.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


VALID_VERDICTS = {
    "merge_alias",
    "keep_separate_distinct_instance",
    "keep_separate_homonym",
    "uncertain_no_merge",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def max_num_id(items: List[Dict[str, Any]], prefix: str) -> int:
    best = 0
    pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    for it in items:
        m = pat.match(str(it.get("id", "")))
        if m:
            best = max(best, int(m.group(1)))
    return best


def build_doc_registry(corpus_index: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    docs_clean: List[Dict[str, Any]] = []
    by_sid: Dict[str, Dict[str, Any]] = {}
    for d in corpus_index.get("documents", []):
        rec = {
            "source_id": d["source_id"],
            "role": d.get("role", "paper"),
            "paper_group_id": d.get("paper_group_id", "PG01"),
            "is_canonical_in_group": d.get("is_canonical_in_group", True),
            "format_duplicate_of": d.get("format_duplicate_of"),
            "doi": d.get("doi", ""),
            "title": d.get("title", ""),
            "authors": d.get("authors", []),
            "license": d.get("license", ""),
            "source_hash": d.get("source_hash", ""),
            "locator_type": d.get("locator_type", "jats_paragraph_id"),
            "has_pdf_pages": bool(d.get("has_pdf_pages", False)),
            "backing_files": d.get("backing_files", []),
            "publisher_xml_files": d.get("publisher_xml_files", []),
            "paragraph_count": d.get("paragraph_count", len(d.get("paragraphs", []))),
        }
        docs_clean.append(rec)
        by_sid[rec["source_id"]] = rec
    return docs_clean, by_sid


def normalize_mention(m: Dict[str, Any], default_sid: str, docs_by_sid: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    sid = str(m.get("source_id") or default_sid or "")
    doc_info = docs_by_sid.get(sid, {})
    default_loc = doc_info.get("locator_type", "jats_paragraph_id")
    out = {
        "source_id": sid,
        "paragraph_id": str(m.get("paragraph_id") or ""),
        "locator_type": str(m.get("locator_type") or default_loc),
        "section": str(m.get("section") or ""),
        "surface_form": str(m.get("surface_form") or ""),
        "quote": str(m.get("quote") or ""),
        "attribution": str(m.get("attribution") or "author_direct_result"),
    }
    if m.get("xml_verified_formula"):
        out["xml_verified_formula"] = str(m["xml_verified_formula"])
    if m.get("page") is not None:
        out["page"] = m["page"]
    return out


def normalize_evidence_list(ev_list: Any, default_sid: str, docs_by_sid: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    if isinstance(ev_list, dict):
        ev_list = [ev_list]
    if not isinstance(ev_list, list):
        return []
    out: List[Dict[str, Any]] = []
    for ev in ev_list:
        if not isinstance(ev, dict):
            continue
        sid = str(ev.get("source_id") or default_sid or "")
        doc_info = docs_by_sid.get(sid, {})
        default_loc = doc_info.get("locator_type", "jats_paragraph_id")
        item = {
            "source_id": sid,
            "paragraph_id": str(ev.get("paragraph_id") or ""),
            "locator_type": str(ev.get("locator_type") or default_loc),
            "quote": str(ev.get("quote") or ""),
        }
        if ev.get("section"):
            item["section"] = str(ev["section"])
        if ev.get("xml_verified_formula"):
            item["xml_verified_formula"] = str(ev["xml_verified_formula"])
        if ev.get("page") is not None:
            item["page"] = ev["page"]
        out.append(item)
    return out


def distinct_paper_groups(source_ids: List[str], docs_by_sid: Dict[str, Dict[str, Any]]) -> Set[str]:
    groups = set()
    for sid in source_ids:
        if sid in docs_by_sid:
            groups.add(docs_by_sid[sid].get("paper_group_id", sid))
        elif sid:
            groups.add(sid)
    return groups


def unique_sorted(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        s = str(x).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return sorted(out)


def build_node_from_extraction(
    ent: Dict[str, Any],
    node_id: str,
    default_sid: str,
    docs_by_sid: Dict[str, Dict[str, Any]],
    version: str,
) -> Dict[str, Any]:
    raw_mentions = ent.get("mentions") or []
    if not raw_mentions and ent.get("paragraph_id"):
        raw_mentions = [
            {
                "source_id": ent.get("source_id") or default_sid,
                "paragraph_id": ent.get("paragraph_id"),
                "locator_type": ent.get("locator_type"),
                "section": ent.get("section", ""),
                "surface_form": ent.get("surface_form") or ent.get("canonical_name") or ent.get("name") or "",
                "quote": ent.get("quote") or "",
                "attribution": ent.get("attribution", "author_direct_result"),
            }
        ]
    mentions = [normalize_mention(m, default_sid, docs_by_sid) for m in raw_mentions if isinstance(m, dict)]
    sids = unique_sorted(
        ([str(ent["source_id"])] if ent.get("source_id") else [])
        + [str(s) for s in (ent.get("source_ids") or [])]
        + [m["source_id"] for m in mentions if m.get("source_id")]
    )
    if not sids and default_sid:
        sids = [default_sid]

    cname = str(ent.get("canonical_name") or ent.get("name") or node_id).strip()
    aliases = unique_sorted(
        [cname]
        + [str(a) for a in (ent.get("aliases") or [])]
        + [m["surface_form"] for m in mentions if m.get("surface_form")]
    )

    attrs = ent.get("attributes") if isinstance(ent.get("attributes"), dict) else {}
    attrs_by_source: Dict[str, Dict[str, Any]] = {}
    if attrs:
        attr_sid = sids[0] if sids else (default_sid or "unknown")
        attrs_by_source[attr_sid] = attrs

    pg_count = len(distinct_paper_groups(sids, docs_by_sid))
    scope = str(
        ent.get("scope")
        or ("shared_canonical_entity" if pg_count >= 2 else "paper_scoped_instance")
    )

    return {
        "id": node_id,
        "canonical_name": cname,
        "entity_type": str(ent.get("entity_type") or "Method"),
        "scope": scope,
        "source_ids": sids,
        "aliases": aliases,
        "attributes_by_source": attrs_by_source,
        "mentions": mentions,
        "first_introduced_in_version": version,
    }


def resolve_endpoint(ref: str, ref_map: Dict[str, str], name_to_id: Dict[str, str]) -> Optional[str]:
    if not ref:
        return None
    s = str(ref).strip()
    if s in ref_map:
        return ref_map[s]
    if s in name_to_id:
        return name_to_id[s]
    low = s.lower()
    for k, v in name_to_id.items():
        if k.lower() == low:
            return v
    return None


def make_edge(
    raw: Dict[str, Any],
    edge_id: str,
    src_id: str,
    tgt_id: str,
    default_sid: str,
    docs_by_sid: Dict[str, Dict[str, Any]],
    version: str,
) -> Dict[str, Any]:
    ev_raw = raw.get("evidence")
    if not ev_raw and raw.get("paragraph_id"):
        ev_raw = [
            {
                "source_id": raw.get("source_id") or default_sid,
                "paragraph_id": raw.get("paragraph_id"),
                "locator_type": raw.get("locator_type"),
                "quote": raw.get("quote") or "",
                "xml_verified_formula": raw.get("xml_verified_formula"),
            }
        ]
    evidence = normalize_evidence_list(ev_raw, default_sid, docs_by_sid)
    ev_sids = unique_sorted([e["source_id"] for e in evidence if e.get("source_id")])
    is_cross = len(distinct_paper_groups(ev_sids, docs_by_sid)) >= 2

    return {
        "id": edge_id,
        "source": src_id,
        "target": tgt_id,
        "relation_type": str(raw.get("relation_type") or "ASSOCIATED_WITH"),
        "direction": "directed",
        "attribution": str(raw.get("attribution") or "author_direct_result"),
        "evidence_strength": str(raw.get("evidence_strength") or "strong_direct"),
        "conditions": raw.get("conditions") if raw.get("conditions") is not None else "",
        "evidence": evidence,
        "source_ids": ev_sids,
        "is_cross_paper": is_cross,
        "first_introduced_in_version": version,
    }


def init_kg(
    corpus_index: Dict[str, Any],
    extraction: Dict[str, Any],
    alignment_data: Optional[Dict[str, Any]],
    conflicts_data: Optional[List[Dict[str, Any]]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    docs_clean, docs_by_sid = build_doc_registry(corpus_index)
    default_sid = str(extraction.get("source_id") or (docs_clean[0]["source_id"] if docs_clean else "primary"))

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    ref_map: Dict[str, str] = {}
    name_to_id: Dict[str, str] = {}

    e_counter = 0
    for ent in extraction.get("entities") or extraction.get("nodes") or []:
        e_counter += 1
        nid = f"E{e_counter:03d}"
        node = build_node_from_extraction(ent, nid, default_sid, docs_by_sid, "v1")
        nodes.append(node)
        ref_map[nid] = nid
        if ent.get("temp_id"):
            ref_map[str(ent["temp_id"])] = nid
        if ent.get("id"):
            ref_map[str(ent["id"])] = nid
        name_to_id[node["canonical_name"]] = nid
        for a in node["aliases"]:
            name_to_id.setdefault(a, nid)

    r_counter = 0
    dropped_relations: List[Dict[str, Any]] = []
    for rel in extraction.get("relations") or extraction.get("edges") or []:
        src = resolve_endpoint(str(rel.get("source") or ""), ref_map, name_to_id)
        tgt = resolve_endpoint(str(rel.get("target") or ""), ref_map, name_to_id)
        if not src or not tgt:
            dropped_relations.append(
                {
                    "reason": "unresolved_endpoint",
                    "raw_source": rel.get("source"),
                    "raw_target": rel.get("target"),
                    "relation_type": rel.get("relation_type"),
                }
            )
            continue
        r_counter += 1
        edges.append(make_edge(rel, f"R{r_counter:03d}", src, tgt, default_sid, docs_by_sid, "v1"))

    align_decisions = []
    if isinstance(alignment_data, dict):
        align_decisions = list(alignment_data.get("decisions") or alignment_data.get("alignment_decisions") or [])
    elif isinstance(alignment_data, list):
        align_decisions = list(alignment_data)

    conflicts = list(extraction.get("conflicts") or [])
    if conflicts_data:
        conflicts.extend(conflicts_data)
    for idx, cf in enumerate(conflicts, start=1):
        cf.setdefault("conflict_id", f"CF{idx:03d}")

    uncertainty = list(extraction.get("uncertainty") or [])
    for dr in dropped_relations:
        uncertainty.append({"type": "dropped_unresolved_relation", "detail": dr})

    kg = {
        "schema_version": "1.0.0",
        "graph_version": "v1",
        "case_id": corpus_index.get("case_id", "local-case"),
        "has_pdf_pages": bool(corpus_index.get("has_pdf_pages", False)),
        "declared_input_boundaries": list(corpus_index.get("declared_input_boundaries", [])),
        "duplicate_format_groups": list(corpus_index.get("duplicate_format_groups", [])),
        "root_metadata_isolation": corpus_index.get("root_metadata_isolation", {}),
        "documents": docs_clean,
        "nodes": nodes,
        "edges": edges,
        "alignment_decisions": align_decisions,
        "conflicts": conflicts,
        "uncertainty": uncertainty,
        "incremental_update_history": [
            {
                "version": "v1",
                "ingested_source_id": default_sid,
                "preserved_node_ids": [],
                "added_node_ids": [n["id"] for n in nodes],
                "preserved_edge_ids": [],
                "added_edge_ids": [e["id"] for e in edges],
                "merged_aliases": [],
                "rejected_merges": [],
                "conflict_count": len(conflicts),
            }
        ],
    }
    return kg, kg["incremental_update_history"][0]


def update_kg(
    base_kg: Dict[str, Any],
    corpus_index: Dict[str, Any],
    new_extraction: Dict[str, Any],
    alignment_data: Optional[Dict[str, Any]],
    cross_relations_data: Optional[Any],
    extra_conflicts: Optional[List[Dict[str, Any]]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    kg = copy.deepcopy(base_kg)
    docs_clean, docs_by_sid = build_doc_registry(corpus_index)
    kg["documents"] = docs_clean
    kg["has_pdf_pages"] = bool(corpus_index.get("has_pdf_pages", False))
    kg["declared_input_boundaries"] = list(corpus_index.get("declared_input_boundaries", []))
    kg["duplicate_format_groups"] = list(corpus_index.get("duplicate_format_groups", []))
    kg["root_metadata_isolation"] = corpus_index.get("root_metadata_isolation", {})

    prev_ver = str(kg.get("graph_version") or "v1")
    m_ver = re.match(r"^v(\d+)$", prev_ver)
    next_ver = f"v{int(m_ver.group(1)) + 1}" if m_ver else "v2"
    kg["graph_version"] = next_ver

    preserved_node_ids = [n["id"] for n in kg["nodes"]]
    preserved_edge_ids = [e["id"] for e in kg["edges"]]

    e_counter = max_num_id(kg["nodes"], "E")
    c_counter = max_num_id(kg["nodes"], "C")
    r_counter = max_num_id(kg["edges"], "R")

    nodes_by_id: Dict[str, Dict[str, Any]] = {n["id"]: n for n in kg["nodes"]}
    concept_by_norm: Dict[str, str] = {
        n["canonical_name"].lower(): n["id"] for n in kg["nodes"] if n.get("entity_type") == "Concept"
    }

    # Build lookup for existing nodes
    ref_map: Dict[str, str] = {nid: nid for nid in nodes_by_id}
    existing_name_to_id: Dict[str, str] = {}
    for n in kg["nodes"]:
        existing_name_to_id[n["canonical_name"]] = n["id"]

    # Normalize alignment decisions
    raw_decisions: List[Dict[str, Any]] = []
    if isinstance(alignment_data, dict):
        raw_decisions = list(alignment_data.get("decisions") or alignment_data.get("alignment_decisions") or [])
    elif isinstance(alignment_data, list):
        raw_decisions = list(alignment_data)

    # Index decisions by candidate_b identifier/name
    merge_by_b: Dict[str, Dict[str, Any]] = {}
    separate_decisions: List[Dict[str, Any]] = []
    for idx, dec in enumerate(raw_decisions, start=1):
        dec_copy = copy.deepcopy(dec)
        dec_copy.setdefault("decision_id", f"A{len(kg.get('alignment_decisions', [])) + idx:03d}")
        verdict = str(dec_copy.get("verdict") or "uncertain_no_merge").strip()
        if verdict not in VALID_VERDICTS:
            verdict = "uncertain_no_merge"
            dec_copy["verdict"] = verdict
        cb = dec_copy.get("candidate_b") or {}
        cb_key = str(cb.get("node_id_or_name") or cb.get("id") or cb.get("canonical_name") or "").strip()
        if verdict == "merge_alias" and cb_key:
            merge_by_b[cb_key] = dec_copy
            merge_by_b[cb_key.lower()] = dec_copy
        else:
            separate_decisions.append(dec_copy)
        kg.setdefault("alignment_decisions", []).append(dec_copy)

    default_sid = str(new_extraction.get("source_id") or "secondary")
    new_ref_map: Dict[str, str] = {}
    new_name_to_id: Dict[str, str] = {}
    added_node_ids: List[str] = []
    merged_aliases_log: List[Dict[str, Any]] = []
    rejected_merges_log: List[Dict[str, Any]] = []
    added_edge_ids: List[str] = []

    # 1. Process entities from new_extraction
    for ent in new_extraction.get("entities") or new_extraction.get("nodes") or []:
        tid = str(ent.get("temp_id") or ent.get("id") or "").strip()
        cname = str(ent.get("canonical_name") or ent.get("name") or "").strip()
        dec = (
            merge_by_b.get(tid)
            or merge_by_b.get(tid.lower())
            or merge_by_b.get(cname)
            or merge_by_b.get(cname.lower())
        )

        merged_done = False
        if dec:
            target_id = str(dec.get("canonical_target_id") or "").strip()
            if not target_id:
                ca = dec.get("candidate_a") or {}
                ca_key = str(ca.get("node_id_or_name") or ca.get("id") or ca.get("canonical_name") or "").strip()
                target_id = ref_map.get(ca_key) or existing_name_to_id.get(ca_key) or ""
            if target_id in nodes_by_id:
                existing = nodes_by_id[target_id]
                tmp_node = build_node_from_extraction(ent, target_id, default_sid, docs_by_sid, next_ver)
                existing["source_ids"] = unique_sorted(existing["source_ids"] + tmp_node["source_ids"])
                existing["aliases"] = unique_sorted(existing["aliases"] + tmp_node["aliases"])
                # Append unique mentions
                existing_m_keys = {
                    (m.get("source_id"), m.get("paragraph_id"), m.get("quote")) for m in existing["mentions"]
                }
                for m in tmp_node["mentions"]:
                    mk = (m.get("source_id"), m.get("paragraph_id"), m.get("quote"))
                    if mk not in existing_m_keys:
                        existing["mentions"].append(m)
                        existing_m_keys.add(mk)
                # Keep attributes strictly isolated by source_id
                existing.setdefault("attributes_by_source", {})
                for asid, adict in tmp_node.get("attributes_by_source", {}).items():
                    existing["attributes_by_source"][asid] = adict
                if len(distinct_paper_groups(existing["source_ids"], docs_by_sid)) >= 2:
                    existing["scope"] = "shared_canonical_entity"

                if tid:
                    new_ref_map[tid] = target_id
                if cname:
                    new_name_to_id[cname] = target_id
                merged_aliases_log.append(
                    {
                        "decision_id": dec["decision_id"],
                        "preserved_target_node_id": target_id,
                        "merged_new_entity": cname or tid,
                        "new_source_id": default_sid,
                        "rationale": dec.get("rationale", ""),
                    }
                )
                merged_done = True

        if not merged_done:
            e_counter += 1
            nid = f"E{e_counter:03d}"
            node = build_node_from_extraction(ent, nid, default_sid, docs_by_sid, next_ver)
            kg["nodes"].append(node)
            nodes_by_id[nid] = node
            ref_map[nid] = nid
            added_node_ids.append(nid)
            if tid:
                new_ref_map[tid] = nid
                ref_map[tid] = nid
            if cname:
                new_name_to_id[cname] = nid

    # 2. Process non-merge decisions (create optional abstract Concept + INSTANTIATES_CONCEPT links without merging instances!)
    for dec in separate_decisions:
        verdict = dec.get("verdict", "uncertain_no_merge")
        ca = dec.get("candidate_a") or {}
        cb = dec.get("candidate_b") or {}
        ca_key = str(ca.get("node_id_or_name") or ca.get("id") or ca.get("canonical_name") or "").strip()
        cb_key = str(cb.get("node_id_or_name") or cb.get("id") or cb.get("canonical_name") or "").strip()
        node_a_id = resolve_endpoint(ca_key, ref_map, existing_name_to_id)
        node_b_id = resolve_endpoint(cb_key, new_ref_map, new_name_to_id) or resolve_endpoint(
            cb_key, ref_map, existing_name_to_id
        )

        concept_id_created: Optional[str] = None
        shared_cname = str(dec.get("shared_concept_name") or "").strip()
        if verdict == "keep_separate_distinct_instance" and shared_cname and node_a_id and node_b_id:
            if shared_cname.lower() in concept_by_norm:
                concept_id_created = concept_by_norm[shared_cname.lower()]
                cnode = nodes_by_id[concept_id_created]
            else:
                c_counter += 1
                concept_id_created = f"C{c_counter:03d}"
                dec_ev = normalize_evidence_list(dec.get("evidence"), default_sid, docs_by_sid)
                c_mentions = []
                for ev in dec_ev:
                    c_mentions.append(
                        {
                            "source_id": ev["source_id"],
                            "paragraph_id": ev["paragraph_id"],
                            "locator_type": ev["locator_type"],
                            "section": ev.get("section", ""),
                            "surface_form": shared_cname,
                            "quote": ev["quote"],
                            "attribution": "author_method_statement",
                        }
                    )
                if not c_mentions:
                    # Anchor concept mention to first mention of node_a & node_b
                    for nid_ref in (node_a_id, node_b_id):
                        ms = nodes_by_id[nid_ref].get("mentions", [])
                        if ms:
                            c_mentions.append(copy.deepcopy(ms[0]))
                c_sids = unique_sorted([m["source_id"] for m in c_mentions if m.get("source_id")])
                cnode = {
                    "id": concept_id_created,
                    "canonical_name": shared_cname,
                    "entity_type": "Concept",
                    "scope": "abstract_concept",
                    "source_ids": c_sids,
                    "aliases": [shared_cname],
                    "attributes_by_source": {},
                    "mentions": c_mentions,
                    "first_introduced_in_version": next_ver,
                }
                kg["nodes"].append(cnode)
                nodes_by_id[concept_id_created] = cnode
                concept_by_norm[shared_cname.lower()] = concept_id_created
                added_node_ids.append(concept_id_created)

            # Link node_a and node_b to Concept via INSTANTIATES_CONCEPT (without merging their instances!)
            dec_ev_norm = normalize_evidence_list(dec.get("evidence"), default_sid, docs_by_sid)
            for inst_id in (node_a_id, node_b_id):
                inst_node = nodes_by_id[inst_id]
                inst_sid = inst_node["source_ids"][0] if inst_node.get("source_ids") else default_sid
                matching_ev = [e for e in dec_ev_norm if e["source_id"] == inst_sid]
                if not matching_ev and inst_node.get("mentions"):
                    m0 = inst_node["mentions"][0]
                    matching_ev = [
                        {
                            "source_id": m0["source_id"],
                            "paragraph_id": m0["paragraph_id"],
                            "locator_type": m0["locator_type"],
                            "quote": m0["quote"],
                        }
                    ]
                r_counter += 1
                eid = f"R{r_counter:03d}"
                edge = {
                    "id": eid,
                    "source": inst_id,
                    "target": concept_id_created,
                    "relation_type": "INSTANTIATES_CONCEPT",
                    "direction": "directed",
                    "attribution": "author_method_statement",
                    "evidence_strength": "moderate_indirect",
                    "conditions": f"Scoped instance in {inst_sid}; kept strictly separate from other papers' samples/conditions ({dec.get('decision_id')}).",
                    "evidence": matching_ev,
                    "source_ids": unique_sorted([e["source_id"] for e in matching_ev]),
                    "is_cross_paper": False,
                    "first_introduced_in_version": next_ver,
                }
                kg["edges"].append(edge)
                added_edge_ids.append(eid)

        rejected_merges_log.append(
            {
                "decision_id": dec["decision_id"],
                "verdict": verdict,
                "node_a_id": node_a_id,
                "node_b_id": node_b_id,
                "shared_concept_id": concept_id_created,
                "rationale": dec.get("rationale", ""),
            }
        )
        if verdict == "uncertain_no_merge":
            kg.setdefault("uncertainty", []).append(
                {
                    "type": "uncertain_entity_alignment",
                    "decision_id": dec["decision_id"],
                    "node_a_id": node_a_id,
                    "node_b_id": node_b_id,
                    "note": dec.get("rationale", "Insufficient textual evidence to merge."),
                }
            )

    # Combined endpoint resolvers (prefer new paper's local map for new-paper relations!)
    combined_ref_map = dict(ref_map)
    combined_ref_map.update(new_ref_map)
    combined_name_map = dict(existing_name_to_id)
    combined_name_map.update(new_name_to_id)

    # 3. Ingest intra-new-paper relations
    dropped_relations: List[Dict[str, Any]] = []
    for rel in new_extraction.get("relations") or new_extraction.get("edges") or []:
        src = resolve_endpoint(str(rel.get("source") or ""), new_ref_map, new_name_to_id) or resolve_endpoint(
            str(rel.get("source") or ""), combined_ref_map, combined_name_map
        )
        tgt = resolve_endpoint(str(rel.get("target") or ""), new_ref_map, new_name_to_id) or resolve_endpoint(
            str(rel.get("target") or ""), combined_ref_map, combined_name_map
        )
        if not src or not tgt:
            dropped_relations.append(
                {
                    "scope": "new_extraction",
                    "raw_source": rel.get("source"),
                    "raw_target": rel.get("target"),
                    "relation_type": rel.get("relation_type"),
                }
            )
            continue
        r_counter += 1
        eid = f"R{r_counter:03d}"
        kg["edges"].append(make_edge(rel, eid, src, tgt, default_sid, docs_by_sid, next_ver))
        added_edge_ids.append(eid)

    # 4. Ingest explicit cross-paper relations (SUPPORTS / CONTRADICTS / COMPARES / DERIVES_FROM)
    cross_rels: List[Dict[str, Any]] = []
    if isinstance(cross_relations_data, dict):
        cross_rels = list(cross_relations_data.get("relations") or cross_relations_data.get("edges") or [])
    elif isinstance(cross_relations_data, list):
        cross_rels = list(cross_relations_data)

    cross_paper_edge_ids: List[str] = []
    conflict_edge_ids: List[str] = []
    for rel in cross_rels:
        src = resolve_endpoint(str(rel.get("source") or ""), combined_ref_map, combined_name_map)
        tgt = resolve_endpoint(str(rel.get("target") or ""), combined_ref_map, combined_name_map)
        if not src or not tgt:
            dropped_relations.append(
                {
                    "scope": "cross_paper_relations",
                    "raw_source": rel.get("source"),
                    "raw_target": rel.get("target"),
                    "relation_type": rel.get("relation_type"),
                }
            )
            continue
        r_counter += 1
        eid = f"R{r_counter:03d}"
        edge = make_edge(rel, eid, src, tgt, default_sid, docs_by_sid, next_ver)
        kg["edges"].append(edge)
        added_edge_ids.append(eid)
        if edge["is_cross_paper"]:
            cross_paper_edge_ids.append(eid)
        if edge["relation_type"] == "CONTRADICTS":
            conflict_edge_ids.append(eid)

    # 5. Merge conflicts & uncertainty
    all_new_conflicts = list(new_extraction.get("conflicts") or [])
    if isinstance(cross_relations_data, dict) and isinstance(cross_relations_data.get("conflicts"), list):
        all_new_conflicts.extend(cross_relations_data["conflicts"])
    if extra_conflicts:
        all_new_conflicts.extend(extra_conflicts)

    existing_cf_count = len(kg.get("conflicts", []))
    for idx, cf in enumerate(all_new_conflicts, start=1):
        cf_copy = copy.deepcopy(cf)
        cf_copy.setdefault("conflict_id", f"CF{existing_cf_count + idx:03d}")
        kg.setdefault("conflicts", []).append(cf_copy)

    for u in new_extraction.get("uncertainty") or []:
        kg.setdefault("uncertainty", []).append(u)
    for dr in dropped_relations:
        kg.setdefault("uncertainty", []).append({"type": "dropped_unresolved_relation", "detail": dr})

    update_record = {
        "version": next_ver,
        "previous_version": prev_ver,
        "ingested_source_id": default_sid,
        "preserved_node_count": len(preserved_node_ids),
        "preserved_node_ids": preserved_node_ids,
        "added_node_count": len(added_node_ids),
        "added_node_ids": added_node_ids,
        "preserved_edge_count": len(preserved_edge_ids),
        "preserved_edge_ids": preserved_edge_ids,
        "added_edge_count": len(added_edge_ids),
        "added_edge_ids": added_edge_ids,
        "merged_aliases": merged_aliases_log,
        "rejected_merges": rejected_merges_log,
        "cross_paper_edge_ids": cross_paper_edge_ids,
        "conflict_edge_ids": conflict_edge_ids,
        "dropped_relations": dropped_relations,
    }
    kg.setdefault("incremental_update_history", []).append(update_record)
    return kg, update_record


def main() -> None:
    parser = argparse.ArgumentParser(description="Build v1 KG (--mode init) or incrementally update KG (--mode update).")
    parser.add_argument("--mode", choices=["init", "update"], required=True)
    parser.add_argument("--corpus-index", required=True, help="Path to corpus_index.json")
    parser.add_argument("--extraction", required=True, help="Path to primary_extraction.json (init) or secondary_extraction.json (update)")
    parser.add_argument("--base-kg", default=None, help="Path to existing kg_v1.json (required for --mode update)")
    parser.add_argument("--alignment", default=None, help="Path to alignment_decisions.json")
    parser.add_argument("--cross-relations", default=None, help="Path to cross_paper_relations.json")
    parser.add_argument("--conflicts", default=None, help="Optional extra conflicts JSON file")
    parser.add_argument("--out", required=True, help="Output path for built/updated KG JSON")
    parser.add_argument("--log-out", default=None, help="Optional output path for incremental_update_log.json")
    args = parser.parse_args()

    corpus_index = load_json(Path(args.corpus_index))
    extraction = load_json(Path(args.extraction))
    alignment_data = load_json(Path(args.alignment)) if args.alignment and Path(args.alignment).exists() else None
    cross_data = load_json(Path(args.cross_relations)) if args.cross_relations and Path(args.cross_relations).exists() else None
    conflicts_data = load_json(Path(args.conflicts)) if args.conflicts and Path(args.conflicts).exists() else None
    if isinstance(conflicts_data, dict):
        conflicts_data = conflicts_data.get("conflicts", [])

    if args.mode == "init":
        kg, log_rec = init_kg(corpus_index, extraction, alignment_data, conflicts_data)
    else:
        if not args.base_kg or not Path(args.base_kg).exists():
            raise SystemExit("ERROR: --base-kg is required and must exist for --mode update.")
        base_kg = load_json(Path(args.base_kg))
        kg, log_rec = update_kg(base_kg, corpus_index, extraction, alignment_data, cross_data, conflicts_data)

    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(kg, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.log_out:
        log_p = Path(args.log_out)
        log_p.parent.mkdir(parents=True, exist_ok=True)
        log_p.write_text(json.dumps(log_rec, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "ok",
                "mode": args.mode,
                "graph_version": kg["graph_version"],
                "total_nodes": len(kg["nodes"]),
                "total_edges": len(kg["edges"]),
                "alignment_decisions": len(kg.get("alignment_decisions", [])),
                "conflicts": len(kg.get("conflicts", [])),
                "out": str(out_p),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
