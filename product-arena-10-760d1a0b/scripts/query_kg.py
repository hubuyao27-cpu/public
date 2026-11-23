#!/usr/bin/env python3
"""Executable Graph Query Engine for verifiable multi-paper Literature Knowledge Graphs.

Executes declarative structured queries directly over kg.json using 6 graph operators:
  1. entity_neighborhood
  2. multi_hop_chain
  3. cross_paper_comparison
  4. conflict_and_boundary_audit
  5. evidence_filter
  6. gap_or_unanswerable_check

Always returns matched_subgraph (node_ids, edge_ids), verbatim supporting_evidence[],
per-paper separated results (never blending metrics across papers), uncertain_or_unanswerable_relations[],
and honest status ('answered' | 'partially_answered' | 'unable_to_determine').

Pure Python 3.10+ standard library.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


STRENGTH_RANK = {
    "weak_speculative": 1,
    "cited_secondary": 2,
    "moderate_indirect": 3,
    "strong_direct": 4,
}


def dedup_evidence(ev_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for ev in ev_list:
        if not isinstance(ev, dict):
            continue
        key = (ev.get("source_id"), ev.get("paragraph_id"), ev.get("quote"))
        if key not in seen and ev.get("quote"):
            seen.add(key)
            out.append(
                {
                    "source_id": ev.get("source_id", ""),
                    "paragraph_id": ev.get("paragraph_id", ""),
                    "locator_type": ev.get("locator_type", "jats_paragraph_id"),
                    "quote": ev.get("quote", ""),
                }
            )
    return out


class KGQueryEngine:
    def __init__(self, kg: Dict[str, Any]) -> None:
        self.kg = kg
        self.nodes: Dict[str, Dict[str, Any]] = {n["id"]: n for n in kg.get("nodes", [])}
        self.edges: Dict[str, Dict[str, Any]] = {e["id"]: e for e in kg.get("edges", [])}
        self.out_adj: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.in_adj: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for e in kg.get("edges", []):
            self.out_adj[e["source"]].append(e)
            self.in_adj[e["target"]].append(e)

    def resolve_entities(self, query_term: str) -> List[str]:
        if not query_term:
            return []
        q = query_term.strip()
        if q in self.nodes:
            return [q]
        ql = q.lower()
        exact = []
        partial = []
        for nid, n in self.nodes.items():
            names = [n.get("canonical_name", "")] + list(n.get("aliases", []))
            if any(nm.lower() == ql for nm in names if nm):
                exact.append(nid)
            elif any(ql in nm.lower() for nm in names if nm):
                partial.append(nid)
        return exact or partial

    def summarize_subgraph(self, node_ids: Set[str], edge_ids: Set[str]) -> Dict[str, Any]:
        sn = sorted(node_ids)
        se = sorted(edge_ids)
        return {
            "node_ids": sn,
            "edge_ids": se,
            "nodes_summary": [
                {
                    "id": nid,
                    "canonical_name": self.nodes[nid]["canonical_name"],
                    "entity_type": self.nodes[nid]["entity_type"],
                    "scope": self.nodes[nid].get("scope"),
                    "source_ids": self.nodes[nid].get("source_ids", []),
                }
                for nid in sn
                if nid in self.nodes
            ],
            "edges_summary": [
                {
                    "id": eid,
                    "source": self.edges[eid]["source"],
                    "source_name": self.nodes.get(self.edges[eid]["source"], {}).get("canonical_name", ""),
                    "relation_type": self.edges[eid]["relation_type"],
                    "target": self.edges[eid]["target"],
                    "target_name": self.nodes.get(self.edges[eid]["target"], {}).get("canonical_name", ""),
                    "attribution": self.edges[eid].get("attribution"),
                    "evidence_strength": self.edges[eid].get("evidence_strength"),
                    "conditions": self.edges[eid].get("conditions"),
                    "source_ids": self.edges[eid].get("source_ids", []),
                }
                for eid in se
                if eid in self.edges
            ],
        }

    def edge_passes_filter(self, e: Dict[str, Any], params: Dict[str, Any]) -> bool:
        rtypes = params.get("relation_types") or []
        if rtypes and e.get("relation_type") not in rtypes:
            return False
        sid = params.get("source_id")
        if sid and sid not in (e.get("source_ids") or []):
            return False
        excl_attr = set(params.get("exclude_attributions") or [])
        if excl_attr and e.get("attribution") in excl_attr:
            return False
        min_str = params.get("min_evidence_strength")
        if min_str and STRENGTH_RANK.get(e.get("evidence_strength", ""), 0) < STRENGTH_RANK.get(min_str, 0):
            return False
        return True

    def op_entity_neighborhood(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        params = spec.get("params") or {}
        term = str(params.get("entity") or "")
        hops = int(params.get("hops") or 1)
        seeds = self.resolve_entities(term)
        if not seeds:
            return {
                "query_id": spec["query_id"],
                "question": spec["question"],
                "operator": "entity_neighborhood",
                "params": params,
                "status": "unable_to_determine",
                "matched_subgraph": self.summarize_subgraph(set(), set()),
                "answer": f"No entity matching '{term}' exists in the verified knowledge graph.",
                "supporting_evidence": [],
                "uncertain_or_unanswerable_relations": [
                    f"Entity '{term}' is not grounded in the provided corpus."
                ],
            }

        visited_nodes: Set[str] = set(seeds)
        matched_edges: Set[str] = set()
        ev_pool: List[Dict[str, Any]] = []
        uncertain_rels: List[str] = []

        frontier = set(seeds)
        for _ in range(max(1, min(hops, 3))):
            next_frontier: Set[str] = set()
            for nid in frontier:
                for e in self.out_adj.get(nid, []) + self.in_adj.get(nid, []):
                    if not self.edge_passes_filter(e, params):
                        continue
                    matched_edges.add(e["id"])
                    visited_nodes.add(e["source"])
                    visited_nodes.add(e["target"])
                    next_frontier.add(e["source"])
                    next_frontier.add(e["target"])
                    ev_pool.extend(e.get("evidence", []))
                    if e.get("attribution") in ("cited_prior_work", "speculation_or_hypothesis"):
                        uncertain_rels.append(
                            f"{e['id']} ({e['source']} -{e['relation_type']}-> {e['target']}) is {e['attribution']} ({e['evidence_strength']}), not direct author proof."
                        )
            frontier = next_frontier

        for nid in seeds:
            for m in self.nodes[nid].get("mentions", [])[:2]:
                ev_pool.append(m)

        ev_clean = dedup_evidence(ev_pool)
        status = "answered" if matched_edges else "partially_answered"
        seed_names = [f"{s} ({self.nodes[s]['canonical_name']})" for s in seeds]
        answer = (
            f"Found {len(matched_edges)} verified edge(s) and {len(visited_nodes)} node(s) within {hops}-hop neighborhood of "
            + ", ".join(seed_names)
            + "."
        )
        return {
            "query_id": spec["query_id"],
            "question": spec["question"],
            "operator": "entity_neighborhood",
            "params": params,
            "status": status,
            "matched_subgraph": self.summarize_subgraph(visited_nodes, matched_edges),
            "answer": answer,
            "supporting_evidence": ev_clean,
            "uncertain_or_unanswerable_relations": uncertain_rels,
        }

    def op_multi_hop_chain(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        params = spec.get("params") or {}
        start_types = set(params.get("start_types") or ["Material", "Method"])
        end_types = set(params.get("end_types") or ["Metric", "Conclusion"])
        max_hops = int(params.get("max_hops") or 3)
        start_entity = params.get("start_entity")

        if start_entity:
            starts = self.resolve_entities(str(start_entity))
        else:
            starts = [nid for nid, n in self.nodes.items() if n.get("entity_type") in start_types]

        chains: List[List[str]] = []
        node_ids: Set[str] = set()
        edge_ids: Set[str] = set()
        ev_pool: List[Dict[str, Any]] = []
        uncertain_rels: List[str] = []

        for s in starts:
            q: deque = deque([(s, [], [s])])
            while q and len(chains) < 25:
                curr, epath, npath = q.popleft()
                if epath and self.nodes[curr].get("entity_type") in end_types:
                    chains.append(epath)
                    for nid in npath:
                        node_ids.add(nid)
                    for eid in epath:
                        edge_ids.add(eid)
                        ev_pool.extend(self.edges[eid].get("evidence", []))
                        if self.edges[eid].get("attribution") in ("cited_prior_work", "speculation_or_hypothesis"):
                            uncertain_rels.append(f"{eid}: {self.edges[eid]['attribution']}")
                if len(epath) >= max_hops:
                    continue
                for e in self.out_adj.get(curr, []):
                    if not self.edge_passes_filter(e, params):
                        continue
                    nxt = e["target"]
                    if nxt in npath:
                        continue
                    q.append((nxt, epath + [e["id"]], npath + [nxt]))

        chain_descriptions = []
        for ep in chains[:10]:
            parts = []
            for eid in ep:
                e = self.edges[eid]
                sn = self.nodes[e["source"]]["canonical_name"]
                tn = self.nodes[e["target"]]["canonical_name"]
                parts.append(f"{sn} --[{e['relation_type']}|{e['id']}]--> {tn}")
            chain_descriptions.append(" ; ".join(parts))

        if not chains:
            return {
                "query_id": spec["query_id"],
                "question": spec["question"],
                "operator": "multi_hop_chain",
                "params": params,
                "status": "unable_to_determine",
                "matched_subgraph": self.summarize_subgraph(set(), set()),
                "chains": [],
                "answer": f"No verified directed chain found from {sorted(start_types)} to {sorted(end_types)} within {max_hops} hops.",
                "supporting_evidence": [],
                "uncertain_or_unanswerable_relations": [
                    "No multi-hop directed evidence path satisfied the query constraints."
                ],
            }

        return {
            "query_id": spec["query_id"],
            "question": spec["question"],
            "operator": "multi_hop_chain",
            "params": params,
            "status": "answered",
            "matched_subgraph": self.summarize_subgraph(node_ids, edge_ids),
            "chains": chain_descriptions,
            "answer": f"Traced {len(chains)} verified multi-hop provenance chain(s) across {len(node_ids)} node(s) and {len(edge_ids)} edge(s).",
            "supporting_evidence": dedup_evidence(ev_pool),
            "uncertain_or_unanswerable_relations": sorted(set(uncertain_rels)),
        }

    def op_cross_paper_comparison(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        params = spec.get("params") or {}
        term = str(params.get("concept_or_term") or "").strip()

        focal_nodes: Set[str] = set()
        if term:
            focal_nodes.update(self.resolve_entities(term))

        # Expand from Concept nodes via INSTANTIATES_CONCEPT and include all shared/cross-paper aligned nodes
        if not focal_nodes:
            for nid, n in self.nodes.items():
                if n.get("entity_type") == "Concept" or n.get("scope") == "shared_canonical_entity":
                    focal_nodes.add(nid)

        expanded_nodes: Set[str] = set(focal_nodes)
        matched_edges: Set[str] = set()
        ev_pool: List[Dict[str, Any]] = []

        # Pull in instances linked to Concepts via INSTANTIATES_CONCEPT
        for nid in list(focal_nodes):
            for e in self.in_adj.get(nid, []) + self.out_adj.get(nid, []):
                if e["relation_type"] in ("INSTANTIATES_CONCEPT", "COMPARES", "SUPPORTS", "CONTRADICTS"):
                    matched_edges.add(e["id"])
                    expanded_nodes.add(e["source"])
                    expanded_nodes.add(e["target"])
                    ev_pool.extend(e.get("evidence", []))

        # Also pull in relevant alignment decisions
        relevant_decisions = []
        for dec in self.kg.get("alignment_decisions", []):
            ca_id = str((dec.get("candidate_a") or {}).get("node_id_or_name") or "")
            cb_id = str((dec.get("candidate_b") or {}).get("node_id_or_name") or "")
            cname = str(dec.get("shared_concept_name") or "")
            if (
                not term
                or term.lower() in cname.lower()
                or term.lower() in ca_id.lower()
                or term.lower() in cb_id.lower()
                or ca_id in expanded_nodes
                or cb_id in expanded_nodes
            ):
                relevant_decisions.append(dec)
                ev_pool.extend(dec.get("evidence", []))
                for candidate_ref in (ca_id, cb_id):
                    for rn in self.resolve_entities(candidate_ref):
                        expanded_nodes.add(rn)

        # Gather per-paper 1-hop experimental evidence for each expanded instance (strictly separated by source_id!)
        per_paper: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for nid in list(expanded_nodes):
            if nid not in self.nodes or self.nodes[nid].get("entity_type") == "Concept":
                continue
            for e in self.out_adj.get(nid, []) + self.in_adj.get(nid, []):
                matched_edges.add(e["id"])
                expanded_nodes.add(e["source"])
                expanded_nodes.add(e["target"])
                ev_pool.extend(e.get("evidence", []))
                for sid in e.get("source_ids", []):
                    per_paper[sid].append(
                        {
                            "edge_id": e["id"],
                            "triple": f"{self.nodes[e['source']]['canonical_name']} -{e['relation_type']}-> {self.nodes[e['target']]['canonical_name']}",
                            "conditions": e.get("conditions", ""),
                            "attribution": e.get("attribution", ""),
                            "evidence_strength": e.get("evidence_strength", ""),
                        }
                    )

        non_merge_notes = [
            f"{d.get('decision_id')}: {d.get('verdict')} ({(d.get('candidate_a') or {}).get('node_id_or_name')} vs {(d.get('candidate_b') or {}).get('node_id_or_name')}) — {d.get('rationale')}"
            for d in relevant_decisions
            if d.get("verdict") != "merge_alias"
        ]

        if not expanded_nodes:
            return {
                "query_id": spec["query_id"],
                "question": spec["question"],
                "operator": "cross_paper_comparison",
                "params": params,
                "status": "unable_to_determine",
                "matched_subgraph": self.summarize_subgraph(set(), set()),
                "per_paper_breakdown": {},
                "alignment_decisions_applied": [],
                "answer": f"No cross-paper concepts or entities matched '{term}'.",
                "supporting_evidence": [],
                "uncertain_or_unanswerable_relations": [f"No cross-paper nodes found for '{term}'."],
            }

        status = "answered" if len(per_paper) >= 2 else "partially_answered"
        answer = (
            f"Compared {len(per_paper)} source paper(s) across {len(expanded_nodes)} node(s) and {len(matched_edges)} edge(s), "
            f"applying {len(relevant_decisions)} alignment decision(s) ({len(non_merge_notes)} explicit non-merge boundary/boundaries). "
            "Per-paper samples, conditions, and metrics are preserved separately without blending."
        )
        return {
            "query_id": spec["query_id"],
            "question": spec["question"],
            "operator": "cross_paper_comparison",
            "params": params,
            "status": status,
            "matched_subgraph": self.summarize_subgraph(expanded_nodes, matched_edges),
            "per_paper_breakdown": dict(per_paper),
            "alignment_decisions_applied": relevant_decisions,
            "answer": answer,
            "supporting_evidence": dedup_evidence(ev_pool),
            "uncertain_or_unanswerable_relations": non_merge_notes,
        }

    def op_conflict_and_boundary_audit(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        params = spec.get("params") or {}
        node_ids: Set[str] = set()
        edge_ids: Set[str] = set()
        ev_pool: List[Dict[str, Any]] = []
        uncertain_rels: List[str] = []

        conflict_edges = []
        secondary_edges = []
        for eid, e in self.edges.items():
            if e["relation_type"] == "CONTRADICTS":
                conflict_edges.append(e)
                edge_ids.add(eid)
                node_ids.add(e["source"])
                node_ids.add(e["target"])
                ev_pool.extend(e.get("evidence", []))
            elif params.get("include_cited_or_speculative", True) and e.get("attribution") in (
                "cited_prior_work",
                "speculation_or_hypothesis",
            ):
                secondary_edges.append(e)
                edge_ids.add(eid)
                node_ids.add(e["source"])
                node_ids.add(e["target"])
                ev_pool.extend(e.get("evidence", []))
                uncertain_rels.append(
                    f"{eid} ({self.nodes[e['source']]['canonical_name']} -{e['relation_type']}-> {self.nodes[e['target']]['canonical_name']}): {e['attribution']} ({e['evidence_strength']})"
                )

        non_merges = [
            d for d in self.kg.get("alignment_decisions", []) if d.get("verdict") != "merge_alias"
        ]
        for d in non_merges:
            ev_pool.extend(d.get("evidence", []))
            uncertain_rels.append(
                f"Non-merge {d.get('decision_id')} [{d.get('verdict')}]: {d.get('rationale')}"
            )

        conflicts = self.kg.get("conflicts", [])
        for cf in conflicts:
            ev_pool.extend(cf.get("evidence", []))

        answer = (
            f"Audited {len(conflicts)} documented conflict record(s), {len(conflict_edges)} CONTRADICTS edge(s), "
            f"{len(non_merges)} non-merge alignment boundary decision(s), and {len(secondary_edges)} cited/speculative edge(s)."
        )
        return {
            "query_id": spec["query_id"],
            "question": spec["question"],
            "operator": "conflict_and_boundary_audit",
            "params": params,
            "status": "answered",
            "matched_subgraph": self.summarize_subgraph(node_ids, edge_ids),
            "conflicts": conflicts,
            "non_merge_decisions": non_merges,
            "answer": answer,
            "supporting_evidence": dedup_evidence(ev_pool),
            "uncertain_or_unanswerable_relations": uncertain_rels,
        }

    def op_evidence_filter(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        params = spec.get("params") or {}
        keyword = str(params.get("keyword") or "").lower().strip()
        node_ids: Set[str] = set()
        edge_ids: Set[str] = set()
        ev_pool: List[Dict[str, Any]] = []

        for eid, e in self.edges.items():
            if not self.edge_passes_filter(e, params):
                continue
            if keyword:
                blob = " ".join(
                    [
                        self.nodes.get(e["source"], {}).get("canonical_name", ""),
                        self.nodes.get(e["target"], {}).get("canonical_name", ""),
                        str(e.get("conditions", "")),
                        " ".join(ev.get("quote", "") for ev in e.get("evidence", [])),
                    ]
                ).lower()
                if keyword not in blob:
                    continue
            edge_ids.add(eid)
            node_ids.add(e["source"])
            node_ids.add(e["target"])
            ev_pool.extend(e.get("evidence", []))

        status = "answered" if edge_ids else "unable_to_determine"
        return {
            "query_id": spec["query_id"],
            "question": spec["question"],
            "operator": "evidence_filter",
            "params": params,
            "status": status,
            "matched_subgraph": self.summarize_subgraph(node_ids, edge_ids),
            "answer": f"Matched {len(edge_ids)} verified edge(s) across {len(node_ids)} node(s) matching filter criteria.",
            "supporting_evidence": dedup_evidence(ev_pool),
            "uncertain_or_unanswerable_relations": (
                [] if edge_ids else ["No verified edges in the graph satisfy the specified evidence filter."]
            ),
        }

    def op_gap_or_unanswerable_check(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        params = spec.get("params") or {}
        ent_a = str(params.get("entity_a") or "")
        ent_b = str(params.get("entity_b") or "")
        req_rels = set(params.get("required_relation_types") or ["OUTPERFORMS", "COMPARES"])
        require_direct = bool(params.get("require_direct_author_evidence", True))
        mod_checks = params.get("missing_modality_check") or []

        ids_a = set(self.resolve_entities(ent_a)) if ent_a else set()
        ids_b = set(self.resolve_entities(ent_b)) if ent_b else set()

        matched_edges: Set[str] = set()
        matched_nodes: Set[str] = set(ids_a | ids_b)
        ev_pool: List[Dict[str, Any]] = []

        if ids_a and ids_b:
            for eid, e in self.edges.items():
                if e["relation_type"] not in req_rels:
                    continue
                if require_direct and e.get("attribution") != "author_direct_result":
                    continue
                if (e["source"] in ids_a and e["target"] in ids_b) or (
                    e["source"] in ids_b and e["target"] in ids_a
                ):
                    matched_edges.add(eid)
                    matched_nodes.add(e["source"])
                    matched_nodes.add(e["target"])
                    ev_pool.extend(e.get("evidence", []))

        # Check declared missing input boundaries
        active_boundaries = self.kg.get("declared_input_boundaries", [])
        triggered_boundaries = [
            b for b in active_boundaries if any(mc.lower() in b.lower() for mc in mod_checks)
        ]

        reasons: List[str] = []
        if not matched_edges:
            if ent_a and ent_b:
                reasons.append(
                    f"No verified direct {sorted(req_rels)} edge links '{ent_a}' and '{ent_b}' under controlled identical conditions in the corpus."
                )
            else:
                reasons.append("Requested direct comparative or unseen-modality evidence is not reported in the provided texts.")
        for tb in triggered_boundaries:
            reasons.append(f"Input boundary: {tb}")

        # Attach node mentions as provenance showing what IS present vs what is missing
        for nid in sorted(matched_nodes):
            for m in self.nodes[nid].get("mentions", [])[:1]:
                ev_pool.append(m)

        if matched_edges and not triggered_boundaries:
            status = "answered"
            answer = f"Found {len(matched_edges)} direct verified edge(s) answering the comparison."
        else:
            status = "unable_to_determine"
            answer = "Unable to determine from the provided corpus: " + " ".join(reasons)

        return {
            "query_id": spec["query_id"],
            "question": spec["question"],
            "operator": "gap_or_unanswerable_check",
            "params": params,
            "status": status,
            "matched_subgraph": self.summarize_subgraph(matched_nodes, matched_edges),
            "answer": answer,
            "supporting_evidence": dedup_evidence(ev_pool),
            "uncertain_or_unanswerable_relations": reasons,
        }

    def execute(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        op = str(spec.get("operator") or "evidence_filter")
        dispatch = {
            "entity_neighborhood": self.op_entity_neighborhood,
            "multi_hop_chain": self.op_multi_hop_chain,
            "cross_paper_comparison": self.op_cross_paper_comparison,
            "conflict_and_boundary_audit": self.op_conflict_and_boundary_audit,
            "evidence_filter": self.op_evidence_filter,
            "gap_or_unanswerable_check": self.op_gap_or_unanswerable_check,
        }
        fn = dispatch.get(op, self.op_evidence_filter)
        return fn(spec)

    def build_auto_queries(self, existing_count: int = 0) -> List[Dict[str, Any]]:
        """Generate grounded executable queries covering comparison, multi-hop chains, conflicts, and unanswerable gaps."""
        auto: List[Dict[str, Any]] = []
        idx = existing_count

        # 1. Cross-paper comparison (if concepts or multiple papers exist)
        concepts = [n for n in self.nodes.values() if n.get("entity_type") == "Concept"]
        shared = [n for n in self.nodes.values() if n.get("scope") == "shared_canonical_entity"]
        target_term = (
            concepts[0]["canonical_name"]
            if concepts
            else (shared[0]["canonical_name"] if shared else "")
        )
        idx += 1
        auto.append(
            {
                "query_id": f"Q{idx}",
                "question": (
                    f"Compare evidence across papers related to '{target_term or 'shared concepts'}' "
                    "while preserving distinct samples, experimental conditions, and non-merge boundaries."
                ),
                "operator": "cross_paper_comparison",
                "params": {"concept_or_term": target_term},
            }
        )

        # 2. Multi-hop provenance chain
        idx += 1
        auto.append(
            {
                "query_id": f"Q{idx}",
                "question": "Trace verifiable direct author evidence chains from Materials/Methods to reported Metrics and Conclusions.",
                "operator": "multi_hop_chain",
                "params": {
                    "start_types": ["Material", "Method"],
                    "end_types": ["Metric", "Conclusion"],
                    "max_hops": 3,
                    "exclude_attributions": ["cited_prior_work", "speculation_or_hypothesis"],
                },
            }
        )

        # 3. Conflict, non-merge, and secondary-citation boundary audit
        idx += 1
        auto.append(
            {
                "query_id": f"Q{idx}",
                "question": "Identify all cross-paper/intra-paper conflicts, non-merged distinct instances/homonyms, and cited-vs-direct claim boundaries.",
                "operator": "conflict_and_boundary_audit",
                "params": {"include_non_merges": True, "include_cited_or_speculative": True},
            }
        )

        # 4. Gap / unanswerable boundary probe across two distinct scoped paper nodes (if available)
        docs = [d["source_id"] for d in self.kg.get("documents", []) if d.get("is_canonical_in_group", True)]
        ent_a = ""
        ent_b = ""
        if len(docs) >= 2:
            for n in self.nodes.values():
                if not ent_a and n.get("source_ids") == [docs[0]] and n.get("entity_type") in ("Material", "Method", "Dataset_Or_Sample"):
                    ent_a = n["id"]
                if not ent_b and n.get("source_ids") == [docs[1]] and n.get("entity_type") in ("Material", "Method", "Dataset_Or_Sample"):
                    ent_b = n["id"]
        idx += 1
        auto.append(
            {
                "query_id": f"Q{idx}",
                "question": (
                    "Is there direct controlled experimental evidence comparing Paper 1 and Paper 2 instances head-to-head "
                    "under identical conditions, or raw data from figures/supplementary files?"
                ),
                "operator": "gap_or_unanswerable_check",
                "params": {
                    "entity_a": ent_a,
                    "entity_b": ent_b,
                    "required_relation_types": ["OUTPERFORMS", "COMPARES"],
                    "require_direct_author_evidence": True,
                    "missing_modality_check": [
                        "figure_pixels_unavailable",
                        "supplementary_unavailable",
                        "raw_data_unavailable",
                    ],
                },
            }
        )
        return auto


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute structured queries directly over kg.json.")
    parser.add_argument("--kg", required=True, help="Path to kg.json")
    parser.add_argument("--query-specs", default=None, help="Optional JSON file containing queries[] specs")
    parser.add_argument("--auto-fill-min", type=int, default=3, help="Auto-generate grounded queries if fewer than N specs are supplied")
    parser.add_argument("--out", required=True, help="Output path for kg_queries.json")
    args = parser.parse_args()

    kg = json.loads(Path(args.kg).read_text(encoding="utf-8"))
    engine = KGQueryEngine(kg)

    specs: List[Dict[str, Any]] = []
    if args.query_specs and Path(args.query_specs).exists():
        raw = json.loads(Path(args.query_specs).read_text(encoding="utf-8"))
        if isinstance(raw, list):
            specs = raw
        elif isinstance(raw, dict):
            specs = list(raw.get("queries") or raw.get("specs") or [])

    if len(specs) < args.auto_fill_min:
        needed = engine.build_auto_queries(existing_count=len(specs))
        existing_ops = {s.get("operator") for s in specs}
        for cand in needed:
            if len(specs) >= max(args.auto_fill_min, 4):
                break
            if cand["operator"] not in existing_ops or len(specs) < args.auto_fill_min:
                specs.append(cand)
                existing_ops.add(cand["operator"])

    results = [engine.execute(s) for s in specs]
    payload = {
        "schema_version": "1.0.0",
        "case_id": kg.get("case_id", "local-case"),
        "graph_version": kg.get("graph_version", "v1"),
        "executed_query_count": len(results),
        "queries": results,
    }

    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "ok",
                "executed_query_count": len(results),
                "statuses": {r["query_id"]: r["status"] for r in results},
                "out": str(out_p),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
