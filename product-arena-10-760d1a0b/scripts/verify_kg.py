#!/usr/bin/env python3
"""Zero-hallucination mechanical auditor for verifiable multi-paper Literature Knowledge Graphs.

Audits 10 dimensions:
  1. Controlled schema & structural integrity (valid types, unique IDs, no dangling edges)
  2. Exact/normalized verbatim quote grounding against (source_id, paragraph_id) in corpus_index.json
  3. Locator coordinate honesty (hard-blocks fake PDF page 1 when has_pdf_pages == false)
  4. Per-paper metadata isolation (blocks root DOI/author bleed onto secondary papers)
  5. Same-paper multi-format guard (blocks same paper_group_id claimed as cross-paper evidence)
  6. Epistemic & condition discipline (no cited/speculative as strong_direct; mandatory conditions;
     no blending two papers' experimental results into a single non-comparative edge)
  7. Alignment coverage & rationale completeness (including non-merge decisions)
  8. Incremental update ID preservation (--prev-kg)
  9. Query verifiability (--queries: >=3 queries, real subgraph node/edge IDs, grounded quotes)
 10. Boundary/uncertainty hygiene

Pure Python 3.10+ standard library. Exits non-zero if error_count > 0.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


VALID_ENTITY_TYPES = {
    "Researcher",
    "Material",
    "Method",
    "Task",
    "Metric",
    "Conclusion",
    "Dataset_Or_Sample",
    "Concept",
}

VALID_RELATION_TYPES = {
    "USES",
    "EVALUATED_ON",
    "MEASURES",
    "REPORTS_METRIC",
    "COMPARES",
    "OUTPERFORMS",
    "SUPPORTS",
    "CONTRADICTS",
    "DERIVES_FROM",
    "PART_OF",
    "APPLIED_TO",
    "ASSOCIATED_WITH",
    "INSTANTIATES_CONCEPT",
    "CITES_PRIOR_CLAIM",
}

CONDITIONAL_RELATIONS = {"OUTPERFORMS", "SUPPORTS", "CONTRADICTS", "COMPARES", "REPORTS_METRIC"}
SINGLE_STUDY_ONLY_RELATIONS = {"REPORTS_METRIC", "MEASURES", "EVALUATED_ON", "OUTPERFORMS"}
VALID_ATTRIBUTIONS = {
    "author_direct_result",
    "author_method_statement",
    "cited_prior_work",
    "speculation_or_hypothesis",
}
VALID_STRENGTHS = {"strong_direct", "moderate_indirect", "cited_secondary", "weak_speculative"}
VALID_VERDICTS = {
    "merge_alias",
    "keep_separate_distinct_instance",
    "keep_separate_homonym",
    "uncertain_no_merge",
}
FAKE_PAGE_RE = re.compile(r"\b(pdf[\s_-]*page|page[\s_-]*\d+|p\.\s*\d+)\b", re.IGNORECASE)


def norm_match(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").lower()
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    s = s.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_compact(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", norm_match(text))


def norm_doi(doi: Optional[str]) -> str:
    if not doi:
        return ""
    s = str(doi).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s.strip()


def quote_matches_text(quote: str, para_text: str, xml_text: str = "") -> Tuple[bool, str]:
    q = (quote or "").strip()
    if len(q) < 4:
        return False, "quote_too_short"

    # Support ellipsis-split short quotes ("part A ... part B") where all parts >=4 chars match in order
    fragments = [f.strip() for f in re.split(r"\s*(?:\.\.\.|…)\s*", q) if len(f.strip()) >= 4]
    if not fragments:
        fragments = [q]

    for label, target in (("exact", para_text), ("exact_xml", xml_text)):
        if not target:
            continue
        pos = 0
        ok = True
        for frag in fragments:
            idx = target.find(frag, pos)
            if idx == -1:
                ok = False
                break
            pos = idx + len(frag)
        if ok:
            return True, label

    for label, target in (("normalized", para_text), ("normalized_xml", xml_text)):
        if not target:
            continue
        nt = norm_match(target)
        pos = 0
        ok = True
        for frag in fragments:
            nf = norm_match(frag)
            idx = nt.find(nf, pos)
            if idx == -1:
                ok = False
                break
            pos = idx + len(nf)
        if ok:
            return True, label

    # Linearized formula tolerance (alphanumeric compact match for >=10 alnum chars)
    for label, target in (("compact_formula", para_text), ("compact_xml", xml_text)):
        if not target:
            continue
        ct = norm_compact(target)
        pos = 0
        ok = True
        for frag in fragments:
            cf = norm_compact(frag)
            if len(cf) < 8:
                ok = False
                break
            idx = ct.find(cf, pos)
            if idx == -1:
                ok = False
                break
            pos = idx + len(cf)
        if ok:
            return True, label

    return False, "mismatch"


def find_best_paragraph_hint(quote: str, paras: Dict[str, str]) -> Optional[Dict[str, Any]]:
    nq = norm_match(quote)
    best_pid = None
    best_score = 0.0
    for pid, txt in paras.items():
        nt = norm_match(txt)
        if nq and nq in nt:
            return {"paragraph_id": pid, "score": 1.0, "snippet": txt[:140]}
        score = SequenceMatcher(None, nq[:200], nt[:600]).ratio() if nq else 0.0
        if score > best_score:
            best_score = score
            best_pid = pid
    if best_pid and best_score >= 0.25:
        return {"paragraph_id": best_pid, "score": round(best_score, 3), "snippet": paras[best_pid][:140]}
    return None


def verify_kg(
    kg: Dict[str, Any],
    corpus_index: Dict[str, Any],
    xml_recon: Optional[Dict[str, Any]] = None,
    prev_kg: Optional[Dict[str, Any]] = None,
    candidates_data: Optional[Dict[str, Any]] = None,
    queries_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []

    def add_issue(severity: str, code: str, location: str, message: str, hint: Any = None) -> None:
        rec: Dict[str, Any] = {
            "severity": severity,
            "code": code,
            "location": location,
            "message": message,
        }
        if hint is not None:
            rec["hint"] = hint
        issues.append(rec)

    # Index corpus paragraphs, XML structured text, metadata, and paper groups
    docs_by_sid: Dict[str, Dict[str, Any]] = {}
    paras_by_sid: Dict[str, Dict[str, str]] = {}
    xml_text_by_sid_pid: Dict[Tuple[str, str], str] = {}
    xml_tables_by_sid: Dict[str, Dict[str, str]] = {}
    paper_group_by_sid: Dict[str, str] = {}

    for doc in corpus_index.get("documents", []):
        sid = doc["source_id"]
        docs_by_sid[sid] = doc
        paper_group_by_sid[sid] = doc.get("paper_group_id", sid)
        paras_by_sid[sid] = {p["paragraph_id"]: p["text"] for p in doc.get("paragraphs", [])}
        # Also include title/doi/authors as manifest_metadata searchable text
        meta_blob = " | ".join(
            x
            for x in [
                doc.get("title", ""),
                doc.get("doi", ""),
                ", ".join(doc.get("authors", [])),
                doc.get("license", ""),
            ]
            if x
        )
        paras_by_sid[sid]["__manifest_metadata__"] = meta_blob

    if isinstance(xml_recon, dict):
        for xf in xml_recon.get("xml_files", []):
            lsid = xf.get("linked_source_id")
            if not lsid:
                continue
            for fr in xf.get("formula_reconciliations", []):
                xml_text_by_sid_pid[(lsid, fr["paragraph_id"])] = fr.get("xml_structured_text", "")
            xml_tables_by_sid.setdefault(lsid, {})
            for tbl in xf.get("tables", []):
                tid = tbl.get("table_id", "")
                blob = " ".join(
                    [tbl.get("label", ""), tbl.get("caption", "")]
                    + [" ".join(r) for r in tbl.get("rows", [])]
                )
                xml_tables_by_sid[lsid][tid] = blob

    # Metrics counters
    total_quotes = 0
    grounded_quotes = 0
    exact_quotes = 0
    fake_page_violations = 0
    same_paper_format_violations = 0
    metadata_bleed_violations = 0

    def check_evidence_item(ev: Dict[str, Any], loc_label: str) -> None:
        nonlocal total_quotes, grounded_quotes, exact_quotes, fake_page_violations
        total_quotes += 1
        sid = str(ev.get("source_id") or "").strip()
        pid = str(ev.get("paragraph_id") or "").strip()
        loc_type = str(ev.get("locator_type") or "").strip()
        quote = str(ev.get("quote") or "").strip()

        if sid not in docs_by_sid:
            add_issue("ERROR", "unknown_source_id", loc_label, f"source_id '{sid}' not found in corpus_index.json.")
            return

        doc = docs_by_sid[sid]
        has_pdf = bool(doc.get("has_pdf_pages", False))

        # Check fake PDF page numbers
        if not has_pdf:
            if loc_type == "pdf_page" or ev.get("page") is not None or FAKE_PAGE_RE.search(pid):
                fake_page_violations += 1
                add_issue(
                    "ERROR",
                    "fake_pdf_page_attribution",
                    loc_label,
                    f"Source '{sid}' has no real PDF pages (has_pdf_pages=false), but evidence claims pdf_page/page='{ev.get('page')}' or pid='{pid}'. Use JATS paragraph_id instead.",
                )

        if not doc.get("is_canonical_in_group", True):
            add_issue(
                "WARNING",
                "non_canonical_format_duplicate_source",
                loc_label,
                f"Evidence references format-duplicate source_id '{sid}' (canonical is '{doc.get('format_duplicate_of')}').",
            )

        # Resolve target text
        para_text = paras_by_sid[sid].get(pid)
        xml_txt = xml_text_by_sid_pid.get((sid, pid), "")
        if para_text is None and loc_type == "manifest_metadata":
            para_text = paras_by_sid[sid].get("__manifest_metadata__", "")
        if para_text is None and sid in xml_tables_by_sid and pid in xml_tables_by_sid[sid]:
            para_text = xml_tables_by_sid[sid][pid]

        if para_text is None:
            hint = find_best_paragraph_hint(quote, paras_by_sid[sid])
            add_issue(
                "ERROR",
                "unknown_paragraph_id",
                loc_label,
                f"paragraph_id '{pid}' does not exist in source_id '{sid}'.",
                hint=hint,
            )
            return

        matched, match_kind = quote_matches_text(quote, para_text, xml_txt)
        if matched:
            grounded_quotes += 1
            if match_kind in ("exact", "exact_xml"):
                exact_quotes += 1
        else:
            hint = find_best_paragraph_hint(quote, paras_by_sid[sid])
            add_issue(
                "ERROR",
                "quote_not_grounded_in_paragraph",
                loc_label,
                f"Quote '{quote[:90]}' not found in ({sid}, {pid}). Replace with verbatim excerpt from that paragraph.",
                hint=hint,
            )

    # 1. Check metadata isolation across documents
    root_iso = corpus_index.get("root_metadata_isolation") or {}
    root_doi_norm = norm_doi(root_iso.get("case_root_doi"))
    primary_sid = root_iso.get("applies_exclusively_to_source_id")

    for doc in kg.get("documents", []):
        sid = doc.get("source_id")
        if sid not in docs_by_sid:
            continue
        expected_doc = docs_by_sid[sid]
        kg_doi = norm_doi(doc.get("doi"))
        exp_doi = norm_doi(expected_doc.get("doi"))
        if sid != primary_sid and root_doi_norm and kg_doi == root_doi_norm and exp_doi != root_doi_norm:
            metadata_bleed_violations += 1
            add_issue(
                "ERROR",
                "root_metadata_bleed_to_secondary",
                f"documents[{sid}]",
                f"Secondary document '{sid}' erroneously carries root/primary DOI '{doc.get('doi')}' instead of '{expected_doc.get('doi')}'.",
            )

    # 2. Check nodes
    nodes = kg.get("nodes") or []
    node_ids: Set[str] = set()
    nodes_by_id: Dict[str, Dict[str, Any]] = {}
    for idx, n in enumerate(nodes):
        nid = str(n.get("id") or f"node_idx_{idx}")
        loc = f"node:{nid}"
        if nid in node_ids:
            add_issue("ERROR", "duplicate_node_id", loc, f"Duplicate node id '{nid}'.")
        node_ids.add(nid)
        nodes_by_id[nid] = n

        etype = n.get("entity_type")
        if etype not in VALID_ENTITY_TYPES:
            add_issue("ERROR", "invalid_entity_type", loc, f"Invalid entity_type '{etype}'. Allowed: {sorted(VALID_ENTITY_TYPES)}")

        if not str(n.get("canonical_name") or "").strip():
            add_issue("ERROR", "missing_canonical_name", loc, "Node has empty canonical_name.")

        mentions = n.get("mentions") or []
        if not mentions:
            add_issue("ERROR", "ungrounded_node", loc, f"Node '{nid}' ({n.get('canonical_name')}) has 0 mentions.")
        for m_idx, m in enumerate(mentions):
            check_evidence_item(m, f"{loc}.mentions[{m_idx}]")
            # Check for primary DOI leaked into secondary node attributes/quotes
            if root_doi_norm and m.get("source_id") != primary_sid:
                sec_doi_expected = norm_doi(docs_by_sid.get(m.get("source_id", ""), {}).get("doi"))
                if sec_doi_expected != root_doi_norm:
                    attrs_str = json.dumps(n.get("attributes_by_source", {}).get(m.get("source_id", ""), {}), ensure_ascii=False).lower()
                    if root_doi_norm in attrs_str:
                        metadata_bleed_violations += 1
                        add_issue(
                            "ERROR",
                            "root_metadata_bleed_in_node",
                            loc,
                            f"Secondary source '{m.get('source_id')}' attributes contain primary root DOI '{root_doi_norm}'.",
                        )

    # 3. Check edges
    edges = kg.get("edges") or []
    edge_ids: Set[str] = set()
    for idx, e in enumerate(edges):
        eid = str(e.get("id") or f"edge_idx_{idx}")
        loc = f"edge:{eid}"
        if eid in edge_ids:
            add_issue("ERROR", "duplicate_edge_id", loc, f"Duplicate edge id '{eid}'.")
        edge_ids.add(eid)

        src = e.get("source")
        tgt = e.get("target")
        if src not in node_ids:
            add_issue("ERROR", "dangling_edge_source", loc, f"Edge source '{src}' does not exist in nodes[].")
        if tgt not in node_ids:
            add_issue("ERROR", "dangling_edge_target", loc, f"Edge target '{tgt}' does not exist in nodes[].")

        rtype = str(e.get("relation_type") or "")
        if rtype not in VALID_RELATION_TYPES:
            add_issue("ERROR", "invalid_relation_type", loc, f"Invalid relation_type '{rtype}'.")

        attr = str(e.get("attribution") or "")
        if attr not in VALID_ATTRIBUTIONS:
            add_issue("ERROR", "invalid_attribution", loc, f"Invalid attribution '{attr}'.")

        strength = str(e.get("evidence_strength") or "")
        if strength not in VALID_STRENGTHS:
            add_issue("ERROR", "invalid_evidence_strength", loc, f"Invalid evidence_strength '{strength}'.")

        if attr in ("cited_prior_work", "speculation_or_hypothesis") and strength == "strong_direct":
            add_issue(
                "ERROR",
                "epistemic_strength_violation",
                loc,
                f"Edge with attribution='{attr}' CANNOT have evidence_strength='strong_direct'.",
            )

        cond = e.get("conditions")
        cond_empty = (cond is None) or (isinstance(cond, str) and not cond.strip()) or (isinstance(cond, dict) and not cond)
        if rtype in CONDITIONAL_RELATIONS and cond_empty:
            add_issue(
                "ERROR",
                "missing_relation_conditions",
                loc,
                f"Relation '{rtype}' requires non-empty 'conditions' specifying scope/sample/setup/baseline.",
            )

        ev_list = e.get("evidence") or []
        if not ev_list:
            add_issue("ERROR", "ungrounded_edge", loc, f"Edge '{eid}' ({src} -{rtype}-> {tgt}) has 0 evidence items.")

        ev_sids = []
        for ev_idx, ev in enumerate(ev_list):
            check_evidence_item(ev, f"{loc}.evidence[{ev_idx}]")
            if ev.get("source_id"):
                ev_sids.append(str(ev["source_id"]))

        ev_groups = {paper_group_by_sid.get(s, s) for s in ev_sids if s}
        if e.get("is_cross_paper") and len(ev_groups) < 2:
            same_paper_format_violations += 1
            add_issue(
                "ERROR",
                "same_paper_format_claimed_cross_paper",
                loc,
                f"Edge '{eid}' claims is_cross_paper=true, but evidence source_ids {ev_sids} map to only {len(ev_groups)} paper_group_id ({sorted(ev_groups)}).",
            )
        if rtype in SINGLE_STUDY_ONLY_RELATIONS and len(ev_groups) >= 2:
            add_issue(
                "ERROR",
                "blended_cross_paper_experimental_edge",
                loc,
                f"Single-study experimental relation '{rtype}' blends evidence from multiple papers ({sorted(ev_groups)}). Keep per-paper conditions/metrics separate and use COMPARES/SUPPORTS/CONTRADICTS for cross-paper links.",
            )

    # 4. Check alignment decisions & candidate coverage
    decisions = kg.get("alignment_decisions") or []
    decided_pairs: Set[Tuple[str, str]] = set()
    for d_idx, dec in enumerate(decisions):
        did = str(dec.get("decision_id") or f"dec_{d_idx}")
        loc = f"alignment:{did}"
        verdict = str(dec.get("verdict") or "")
        if verdict not in VALID_VERDICTS:
            add_issue("ERROR", "invalid_alignment_verdict", loc, f"Invalid verdict '{verdict}'.")
        if not str(dec.get("rationale") or "").strip():
            add_issue("ERROR", "missing_alignment_rationale", loc, f"Alignment decision '{did}' lacks rationale.")

        ca = dec.get("candidate_a") or {}
        cb = dec.get("candidate_b") or {}
        sa = str(ca.get("source_id") or "")
        sb = str(cb.get("source_id") or "")
        if sa and sb and sa != sb and paper_group_by_sid.get(sa) == paper_group_by_sid.get(sb):
            same_paper_format_violations += 1
            add_issue(
                "ERROR",
                "same_paper_format_alignment_illusion",
                loc,
                f"Alignment decision '{did}' treats '{sa}' and '{sb}' as distinct papers, but both share paper_group_id '{paper_group_by_sid.get(sa)}'.",
            )

        ka = str(ca.get("node_id_or_name") or ca.get("id") or ca.get("canonical_name") or "").lower()
        kb = str(cb.get("node_id_or_name") or cb.get("id") or cb.get("canonical_name") or "").lower()
        if ka and kb:
            decided_pairs.add((ka, kb))
            decided_pairs.add((kb, ka))

        for ev_idx, ev in enumerate(dec.get("evidence") or []):
            check_evidence_item(ev, f"{loc}.evidence[{ev_idx}]")

    if corpus_index.get("unique_paper_count", 1) >= 2 and not decisions:
        add_issue(
            "WARNING",
            "no_alignment_decisions_in_multipaper_kg",
            "alignment_decisions",
            "Corpus has >=2 unique papers but alignment_decisions[] is empty. Document explicit merge and non-merge rationales.",
        )

    if isinstance(candidates_data, dict):
        for cand in candidates_data.get("candidates", []):
            if not cand.get("risk_flags"):
                continue
            ca = cand.get("candidate_a", {})
            cb = cand.get("candidate_b", {})
            keys_a = {str(ca.get("id") or "").lower(), str(ca.get("canonical_name") or "").lower()} - {""}
            keys_b = {str(cb.get("id") or "").lower(), str(cb.get("canonical_name") or "").lower()} - {""}
            covered = any((a, b) in decided_pairs for a in keys_a for b in keys_b)
            if not covered:
                add_issue(
                    "WARNING",
                    "unreviewed_high_risk_alignment_candidate",
                    f"candidate:{cand.get('candidate_id')}",
                    f"High-risk alignment candidate ({ca.get('canonical_name')} vs {cb.get('canonical_name')}, flags={cand.get('risk_flags')}) has no explicit record in alignment_decisions[].",
                )

    # 5. Check conflicts evidence
    for c_idx, cf in enumerate(kg.get("conflicts") or []):
        cid = str(cf.get("conflict_id") or f"cf_{c_idx}")
        for ev_idx, ev in enumerate(cf.get("evidence") or []):
            check_evidence_item(ev, f"conflict:{cid}.evidence[{ev_idx}]")

    # 6. Check incremental ID preservation against prev_kg
    v1_preserved_rate = 1.0
    if isinstance(prev_kg, dict):
        prev_nids = {n["id"] for n in prev_kg.get("nodes", []) if n.get("id")}
        prev_eids = {e["id"] for e in prev_kg.get("edges", []) if e.get("id")}
        missing_nids = sorted(prev_nids - node_ids)
        missing_eids = sorted(prev_eids - edge_ids)
        total_prev = len(prev_nids) + len(prev_eids)
        preserved = total_prev - len(missing_nids) - len(missing_eids)
        v1_preserved_rate = (preserved / total_prev) if total_prev else 1.0
        for mn in missing_nids:
            add_issue("ERROR", "incremental_node_id_lost", f"prev_kg:{mn}", f"Previous node ID '{mn}' was lost or renumbered during incremental update.")
        for me in missing_eids:
            add_issue("ERROR", "incremental_edge_id_lost", f"prev_kg:{me}", f"Previous edge ID '{me}' was lost or renumbered during incremental update.")
        # Verify primary source_ids stayed intact on preserved nodes
        prev_nodes_map = {n["id"]: n for n in prev_kg.get("nodes", []) if n.get("id")}
        for pid, pnode in prev_nodes_map.items():
            if pid in nodes_by_id:
                old_sids = set(pnode.get("source_ids", []))
                new_sids = set(nodes_by_id[pid].get("source_ids", []))
                if not old_sids.issubset(new_sids):
                    add_issue(
                        "ERROR",
                        "incremental_source_provenance_lost",
                        f"node:{pid}",
                        f"Preserved node '{pid}' lost prior source_ids {sorted(old_sids - new_sids)}.",
                    )

    # 7. Check executed queries if supplied
    query_count = 0
    if isinstance(queries_data, dict):
        qlist = queries_data.get("queries") or []
        query_count = len(qlist)
        if query_count < 3:
            add_issue(
                "ERROR",
                "insufficient_executed_queries",
                "queries",
                f"Only {query_count} queries executed; at least 3 real executed queries are required.",
            )
        for q_idx, q in enumerate(qlist):
            qid = str(q.get("query_id") or f"Q{q_idx + 1}")
            sub = q.get("matched_subgraph") or {}
            for nid in sub.get("node_ids", []):
                if nid not in node_ids:
                    add_issue("ERROR", "query_unknown_node_id", f"query:{qid}", f"Query references non-existent node_id '{nid}'.")
            for eid in sub.get("edge_ids", []):
                if eid not in edge_ids:
                    add_issue("ERROR", "query_unknown_edge_id", f"query:{qid}", f"Query references non-existent edge_id '{eid}'.")
            for ev_idx, ev in enumerate(q.get("supporting_evidence") or []):
                check_evidence_item(ev, f"query:{qid}.supporting_evidence[{ev_idx}]")

    error_count = sum(1 for x in issues if x["severity"] == "ERROR")
    warning_count = sum(1 for x in issues if x["severity"] == "WARNING")
    grounding_rate = (grounded_quotes / total_quotes) if total_quotes else 1.0

    report = {
        "schema_version": "1.0.0",
        "passed": error_count == 0,
        "metrics": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "alignment_decision_count": len(decisions),
            "non_merge_decision_count": sum(1 for d in decisions if d.get("verdict") != "merge_alias"),
            "conflict_count": len(kg.get("conflicts") or []),
            "total_evidence_checks": total_quotes,
            "grounded_evidence_count": grounded_quotes,
            "exact_quote_count": exact_quotes,
            "quote_grounding_rate": round(grounding_rate, 4),
            "fake_pdf_page_violations": fake_page_violations,
            "root_metadata_bleed_violations": metadata_bleed_violations,
            "same_paper_format_violations": same_paper_format_violations,
            "incremental_id_preservation_rate": round(v1_preserved_rate, 4),
            "executed_query_count": query_count,
            "error_count": error_count,
            "warning_count": warning_count,
        },
        "issues": issues,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Mechanically verify KG grounding, metadata isolation, alignment, and epistemic discipline.")
    parser.add_argument("--kg", required=True, help="Path to kg.json (kg_v1.json or kg_v2.json)")
    parser.add_argument("--corpus-index", required=True, help="Path to corpus_index.json")
    parser.add_argument("--xml-recon", default=None, help="Optional path to xml_math_reconciliation.json")
    parser.add_argument("--prev-kg", default=None, help="Optional previous kg_v1.json to assert 100% ID preservation")
    parser.add_argument("--candidates", default=None, help="Optional alignment_candidates.json to audit high-risk candidate coverage")
    parser.add_argument("--queries", default=None, help="Optional kg_queries.json to verify >=3 executed queries")
    parser.add_argument("--out", required=True, help="Output path for kg_verification_report.json")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings as well as errors")
    parser.add_argument("--allow-partial", action="store_true", help="Record verification errors honestly in report and exit 0 for partial checkpoint export")
    args = parser.parse_args()

    kg = json.loads(Path(args.kg).read_text(encoding="utf-8"))
    corpus_index = json.loads(Path(args.corpus_index).read_text(encoding="utf-8"))
    xml_recon = json.loads(Path(args.xml_recon).read_text(encoding="utf-8")) if args.xml_recon and Path(args.xml_recon).exists() else None
    prev_kg = json.loads(Path(args.prev_kg).read_text(encoding="utf-8")) if args.prev_kg and Path(args.prev_kg).exists() else None
    cands = json.loads(Path(args.candidates).read_text(encoding="utf-8")) if args.candidates and Path(args.candidates).exists() else None
    queries = json.loads(Path(args.queries).read_text(encoding="utf-8")) if args.queries and Path(args.queries).exists() else None

    report = verify_kg(kg, corpus_index, xml_recon, prev_kg, cands, queries)
    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    if report["issues"]:
        print(f"Issues ({len(report['issues'])}):")
        for iss in report["issues"][:25]:
            print(f"  [{iss['severity']}] {iss['code']} @ {iss['location']}: {iss['message']}")

    if report["metrics"]["error_count"] > 0 and not args.allow_partial:
        sys.exit(1)
    if args.strict and report["metrics"]["warning_count"] > 0 and not args.allow_partial:
        sys.exit(2)


if __name__ == "__main__":
    main()
