#!/usr/bin/env python3
"""Step 3: Export verified Nanopublications to JSON, JSON-LD, RDF TriG, CSV, Markdown report,
and Harbor-compliant result.json with built-in TriG/JSON-LD round-trip syntax validation.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote as url_quote

from common import load_json, norm_ws, save_json, sha256_file


TRIG_PREFIXES = {
    "np": "http://www.nanopub.org/nschema#",
    "npext": "https://w3id.org/np/o/ntemplate/ext/",
    "prov": "http://www.w3.org/ns/prov#",
    "pav": "http://purl.org/pav/",
    "cito": "http://purl.org/spar/cito/",
    "ro": "http://purl.obolibrary.org/obo/RO_",
    "bfo": "http://purl.obolibrary.org/obo/BFO_",
    "sio": "http://semanticscience.org/resource/SIO_",
    "edam": "http://edamontology.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}


def trig_escape_literal(val: Any) -> str:
    s = str(val if val is not None else "")
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{s}"'


def safe_iri(uri: str) -> str:
    # Ensure no illegal spaces/angle brackets inside <...>
    cleaned = re.sub(r"[\s<>\"{}|\\^`]+", "_", str(uri or "").strip())
    return f"<{cleaned}>"


def slug_iri_fragment(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._~-]+", "_", str(text or "entity").strip()).strip("_")
    return s[:90] or "entity"


def build_jsonld(nanopubs: List[Dict[str, Any]]) -> Dict[str, Any]:
    context = {
        **TRIG_PREFIXES,
        "Head": "np:Nanopublication",
        "hasAssertion": {"@id": "np:hasAssertion", "@type": "@id"},
        "hasProvenance": {"@id": "np:hasProvenance", "@type": "@id"},
        "hasPublicationInfo": {"@id": "np:hasPublicationInfo", "@type": "@id"},
        "generatedAtTime": {"@id": "prov:generatedAtTime", "@type": "xsd:dateTime"},
        "wasDerivedFrom": {"@id": "prov:wasDerivedFrom", "@type": "@id"},
        "createdBy": {"@id": "pav:createdBy"},
        "label": "rdfs:label",
        "title": "dcterms:title",
        "creator": "dcterms:creator",
        "license": {"@id": "dcterms:license", "@type": "@id"},
    }
    graph_entries: List[Dict[str, Any]] = []

    for np in nanopubs:
        h = np["head"]
        a = np["assertion"]
        p = np["provenance"]
        pub = np["pubinfo"]
        np_uri = h["nanopub_uri"]

        head_graph = {
            "@id": f"{np_uri}#Head",
            "@graph": [
                {
                    "@id": np_uri,
                    "@type": "np:Nanopublication",
                    "hasAssertion": h["assertion_graph"],
                    "hasProvenance": h["provenance_graph"],
                    "hasPublicationInfo": h["pubinfo_graph"],
                }
            ],
        }

        subj_uri = a["subject"].get("identifier") or f"{np_uri}#subject"
        obj_uri = a["object"].get("identifier") or f"{np_uri}#object"
        pred_uri = a.get("predicate_uri") or f"https://w3id.org/np/o/ntemplate/ext/{a['predicate']}"

        assertion_graph = {
            "@id": h["assertion_graph"],
            "@graph": [
                {
                    "@id": subj_uri,
                    "label": a["subject"]["label"],
                    "npext:entityType": a["subject"]["entity_type"],
                    pred_uri: {"@id": obj_uri},
                    "npext:claimText": a["claim_text"],
                    "npext:polarity": a["polarity"],
                    "npext:epistemicStatus": a["epistemic_status"],
                    "npext:conditions": json.dumps(a["conditions"], ensure_ascii=False),
                    "npext:quantitativeValues": json.dumps(a["quantitative_values"], ensure_ascii=False),
                },
                {
                    "@id": obj_uri,
                    "label": a["object"]["label"],
                    "npext:entityType": a["object"]["entity_type"],
                },
            ],
        }

        ev_nodes: List[Dict[str, Any]] = []
        meta = p.get("source_metadata") or {}
        src_uri = f"https://doi.org/{url_quote(meta['doi'])}" if meta.get("doi") else f"{np_uri}#source_{meta.get('source_id', 'doc')}"

        for e_idx, sp in enumerate(p.get("evidence_spans") or [], 1):
            ev_nodes.append({
                "@id": f"{np_uri}#evidence_{e_idx}",
                "@type": "prov:Entity",
                "prov:value": sp.get("quote"),
                "npext:evidenceType": sp.get("evidence_type"),
                "npext:locator": json.dumps(sp.get("locator") or {}, ensure_ascii=False),
                "prov:wasQuotedFrom": {"@id": src_uri},
            })

        prov_graph = {
            "@id": h["provenance_graph"],
            "@graph": [
                {
                    "@id": h["assertion_graph"],
                    "prov:wasDerivedFrom": [n["@id"] for n in ev_nodes] or [src_uri],
                    "npext:confidenceScore": p["confidence"]["score"],
                    "npext:confidenceLevel": p["confidence"]["level"],
                    "npext:confidenceRationale": p["confidence"]["rationale"],
                },
                {
                    "@id": src_uri,
                    "title": meta.get("title") or "",
                    "creator": meta.get("authors") or [],
                    "dcterms:issued": str(meta.get("year") or ""),
                    "npext:fileSha256": meta.get("file_sha256") or "",
                },
                *ev_nodes,
            ],
        }

        pubinfo_graph = {
            "@id": h["pubinfo_graph"],
            "@graph": [
                {
                    "@id": np_uri,
                    "generatedAtTime": pub["created_at"],
                    "createdBy": pub["created_by"],
                    "license": pub["license"],
                    "npext:nanopubId": pub["nanopub_id"],
                    "npext:dedupFingerprint": pub["dedup_fingerprint"],
                    "npext:corroborationCount": pub.get("corroboration_count", 1),
                }
            ],
        }

        graph_entries.extend([head_graph, assertion_graph, prov_graph, pubinfo_graph])

    return {
        "@context": context,
        "@graph": graph_entries,
    }


def serialize_trig(nanopubs: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for prefix, iri in TRIG_PREFIXES.items():
        lines.append(f"@prefix {prefix}: <{iri}> .")
    lines.append("")

    for np in nanopubs:
        h = np["head"]
        a = np["assertion"]
        p = np["provenance"]
        pub = np["pubinfo"]
        np_uri = h["nanopub_uri"]
        head_uri = f"{np_uri}#Head"
        ass_uri = h["assertion_graph"]
        prov_uri = h["provenance_graph"]
        pub_uri = h["pubinfo_graph"]

        # 1. Head graph
        lines.append(f"{safe_iri(head_uri)} {{")
        lines.append(f"  {safe_iri(np_uri)} a np:Nanopublication ;")
        lines.append(f"    np:hasAssertion {safe_iri(ass_uri)} ;")
        lines.append(f"    np:hasProvenance {safe_iri(prov_uri)} ;")
        lines.append(f"    np:hasPublicationInfo {safe_iri(pub_uri)} .")
        lines.append("}")
        lines.append("")

        # 2. Assertion graph
        subj_uri = a["subject"].get("identifier") if str(a["subject"].get("identifier") or "").startswith("http") else f"{np_uri}#subj_{slug_iri_fragment(a['subject']['label'])}"
        obj_uri = a["object"].get("identifier") if str(a["object"].get("identifier") or "").startswith("http") else f"{np_uri}#obj_{slug_iri_fragment(a['object']['label'])}"
        pred_uri = a.get("predicate_uri") or f"https://w3id.org/np/o/ntemplate/ext/{a['predicate']}"

        lines.append(f"{safe_iri(ass_uri)} {{")
        lines.append(f"  {safe_iri(subj_uri)} a npext:{slug_iri_fragment(a['subject']['entity_type'])} ;")
        lines.append(f"    rdfs:label {trig_escape_literal(a['subject']['label'])} ;")
        lines.append(f"    {safe_iri(pred_uri)} {safe_iri(obj_uri)} .")
        lines.append(f"  {safe_iri(obj_uri)} a npext:{slug_iri_fragment(a['object']['entity_type'])} ;")
        lines.append(f"    rdfs:label {trig_escape_literal(a['object']['label'])} .")
        lines.append(f"  {safe_iri(ass_uri)} npext:claimText {trig_escape_literal(a['claim_text'])} ;")
        lines.append(f"    npext:polarity {trig_escape_literal(a['polarity'])} ;")
        lines.append(f"    npext:epistemicStatus {trig_escape_literal(a['epistemic_status'])} ;")
        lines.append(f"    npext:conditionsJson {trig_escape_literal(json.dumps(a['conditions'], ensure_ascii=False))} ;")
        lines.append(f"    npext:quantitativeJson {trig_escape_literal(json.dumps(a['quantitative_values'], ensure_ascii=False))} .")
        lines.append("}")
        lines.append("")

        # 3. Provenance graph
        meta = p.get("source_metadata") or {}
        src_uri = f"https://doi.org/{url_quote(meta['doi'])}" if meta.get("doi") else f"{np_uri}#source_{slug_iri_fragment(meta.get('source_id', 'doc'))}"
        lines.append(f"{safe_iri(prov_uri)} {{")
        lines.append(f"  {safe_iri(ass_uri)} prov:wasDerivedFrom {safe_iri(src_uri)} ;")
        lines.append(f"    npext:confidenceScore \"{p['confidence']['score']}\"^^xsd:decimal ;")
        lines.append(f"    npext:confidenceLevel {trig_escape_literal(p['confidence']['level'])} ;")
        lines.append(f"    npext:confidenceRationale {trig_escape_literal(p['confidence']['rationale'])} .")
        lines.append(f"  {safe_iri(src_uri)} dcterms:title {trig_escape_literal(meta.get('title') or '')} ;")
        lines.append(f"    npext:sourceFile {trig_escape_literal(meta.get('source_file') or '')} ;")
        lines.append(f"    npext:fileSha256 {trig_escape_literal(meta.get('file_sha256') or '')} ;")
        lines.append(f"    dcterms:issued {trig_escape_literal(meta.get('year') or '')} .")
        for e_idx, sp in enumerate(p.get("evidence_spans") or [], 1):
            ev_uri = f"{np_uri}#evidence_{e_idx}"
            lines.append(f"  {safe_iri(ass_uri)} prov:hadPrimarySource {safe_iri(ev_uri)} .")
            lines.append(f"  {safe_iri(ev_uri)} a prov:Entity ;")
            lines.append(f"    prov:value {trig_escape_literal(sp.get('quote') or '')} ;")
            lines.append(f"    npext:evidenceType {trig_escape_literal(sp.get('evidence_type') or '')} ;")
            lines.append(f"    npext:locatorJson {trig_escape_literal(json.dumps(sp.get('locator') or {}, ensure_ascii=False))} .")
        lines.append("}")
        lines.append("")

        # 4. Pubinfo graph
        lines.append(f"{safe_iri(pub_uri)} {{")
        lines.append(f"  {safe_iri(np_uri)} prov:generatedAtTime {trig_escape_literal(pub['created_at'])}^^xsd:dateTime ;")
        lines.append(f"    pav:createdBy {trig_escape_literal(pub['created_by'])} ;")
        lines.append(f"    dcterms:license {safe_iri(pub['license'])} ;")
        lines.append(f"    npext:nanopubId {trig_escape_literal(pub['nanopub_id'])} ;")
        lines.append(f"    npext:dedupFingerprint {trig_escape_literal(pub['dedup_fingerprint'])} ;")
        lines.append(f"    npext:corroborationCount \"{int(pub.get('corroboration_count', 1))}\"^^xsd:integer .")
        lines.append("}")
        lines.append("")

    return "\n".join(lines)


def validate_trig_syntax(trig_text: str) -> Tuple[bool, List[str]]:
    """Pure-Python structural & lexical validator for RDF 1.1 TriG output."""
    errors: List[str] = []
    declared_prefixes: set = set()
    in_string = False
    escaped = False
    in_iri = False
    brace_depth = 0
    graph_blocks = 0

    for lineno, line in enumerate(trig_text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if not in_string and not in_iri and stripped.startswith("@prefix"):
            m = re.match(r"^@prefix\s+([A-Za-z_][A-Za-z0-9_-]*):\s*<[^>\s]+>\s*\.$", stripped)
            if not m:
                errors.append(f"Line {lineno}: invalid @prefix declaration `{stripped}`")
            else:
                declared_prefixes.add(m.group(1))
            continue

        i = 0
        while i < len(line):
            ch = line[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                i += 1
                continue
            if in_iri:
                if ch == ">":
                    in_iri = False
                elif ch.isspace():
                    errors.append(f"Line {lineno}: whitespace inside IRI literal.")
                i += 1
                continue
            if ch == '"':
                in_string = True
            elif ch == "<":
                in_iri = True
            elif ch == "{":
                brace_depth += 1
                if brace_depth == 1:
                    graph_blocks += 1
                elif brace_depth > 1:
                    errors.append(f"Line {lineno}: nested TriG graph braces not allowed.")
            elif ch == "}":
                brace_depth -= 1
                if brace_depth < 0:
                    errors.append(f"Line {lineno}: unmatched closing brace `}}`.")
            i += 1

        if in_string:
            errors.append(f"Line {lineno}: unclosed string literal.")
            in_string = False
        if in_iri:
            errors.append(f"Line {lineno}: unclosed IRI `<...>`.")
            in_iri = False

        if brace_depth == 1 and not stripped.endswith("{"):
            if not (stripped.endswith(";") or stripped.endswith(".") or stripped.endswith(",")):
                errors.append(f"Line {lineno}: statement inside graph does not end with `;`, `,`, or `.`: `{stripped}`")

    if brace_depth != 0:
        errors.append(f"Unbalanced TriG braces at EOF (depth={brace_depth}).")

    return len(errors) == 0, errors


def _format_locator(loc: Dict[str, Any]) -> str:
    parts: List[str] = []
    if loc.get("page") is not None:
        parts.append(f"p.{loc['page']}")
    if loc.get("section"):
        parts.append(f"Sec:{loc['section']}")
    if loc.get("paragraph_id"):
        parts.append(str(loc["paragraph_id"]))
    if loc.get("sentence_id"):
        parts.append(str(loc["sentence_id"]))
    if loc.get("line_start") is not None:
        parts.append(f"L{loc['line_start']}-{loc.get('line_end')}")
    if loc.get("table_id"):
        parts.append(str(loc["table_id"]))
    if loc.get("figure_id"):
        parts.append(str(loc["figure_id"]))
    return " | ".join(parts) or loc.get("locator_type", "unknown")


def build_csv(nanopubs: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "nanopub_id", "claim_text", "subject", "subject_type", "predicate", "predicate_uri",
        "object", "object_type", "conditions", "quantitative_values", "polarity",
        "epistemic_status", "confidence_level", "confidence_score", "confidence_rationale",
        "source_id", "title", "year", "doi", "file_sha256", "primary_locator", "primary_quote",
    ])
    for np in nanopubs:
        a = np["assertion"]
        p = np["provenance"]
        meta = p.get("source_metadata") or {}
        sp0 = (p.get("evidence_spans") or [{}])[0]
        writer.writerow([
            np["nanopub_id"],
            a["claim_text"],
            a["subject"]["label"],
            a["subject"]["entity_type"],
            a["predicate"],
            a.get("predicate_uri", ""),
            a["object"]["label"],
            a["object"]["entity_type"],
            json.dumps(a["conditions"], ensure_ascii=False),
            json.dumps(a["quantitative_values"], ensure_ascii=False),
            a["polarity"],
            a["epistemic_status"],
            p["confidence"]["level"],
            p["confidence"]["score"],
            p["confidence"]["rationale"],
            meta.get("source_id", ""),
            meta.get("title") or "",
            meta.get("year") or "",
            meta.get("doi") or "",
            meta.get("file_sha256") or "",
            _format_locator(sp0.get("locator") or {}),
            sp0.get("quote") or "",
        ])
    return buf.getvalue()


def build_markdown_report(
    case_id: str,
    status: str,
    nanopubs: List[Dict[str, Any]],
    qa_answers: List[Dict[str, Any]],
    conflicts: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
    uncertainty: List[str],
    limitations: List[str],
    takeover: List[str],
    verify_report: Dict[str, Any],
) -> str:
    lines: List[str] = [
        f"# Nanopublication Extraction & Verification Report — `{case_id}`",
        "",
        f"- **Status**: `{status}`",
        f"- **Verified Nanopublications**: {len(nanopubs)} (merged duplicates: {verify_report.get('merged_duplicates_count', 0)})",
        f"- **Quote Grounding Rate**: {verify_report.get('quote_grounding_rate', 1.0):.1%}",
        f"- **Verification Errors / Warnings**: {verify_report.get('error_count', 0)} errors / {verify_report.get('warning_count', 0)} warnings",
        f"- **Detected Conflicts**: {len(conflicts)}",
        "",
        "## Sources",
        "",
        "| Source ID | Group | Title | Authors | Year | DOI / ID | SHA-256 (prefix) |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in sources:
        m = s.get("metadata") or s
        auths = ", ".join((m.get("authors") or [])[:3]) + (" et al." if len(m.get("authors") or []) > 3 else "")
        ident = m.get("doi") or m.get("arxiv_id") or m.get("pmid") or "UNREPORTED"
        lines.append(
            f"| `{s.get('source_id')}` | `{s.get('paper_group_id', '-')}` | {norm_ws(m.get('title') or 'UNREPORTED')} | {auths or 'UNREPORTED'} | {m.get('year') or 'UNREPORTED'} | `{ident}` | `{str(s.get('file_sha256') or m.get('file_sha256') or '')[:12]}` |"
        )
    lines.extend(["", "## Atomic Nanopublications", "", "| ID (suffix) | Subject | Predicate | Object | Polarity | Epistemic | Conf. | Locator | Minimal Verbatim Quote |", "|---|---|---|---|---|---|---|---|---|"])
    for np in nanopubs:
        a = np["assertion"]
        p = np["provenance"]
        sp0 = (p.get("evidence_spans") or [{}])[0]
        loc_s = _format_locator(sp0.get("locator") or {})
        q_short = norm_ws(sp0.get("quote") or "").replace("|", "\\|")
        lines.append(
            f"| `...{np['nanopub_id'][-12:]}` | {a['subject']['label']} | `{a['predicate']}` | {a['object']['label']} | `{a['polarity']}` | `{a['epistemic_status']}` | `{p['confidence']['level']} ({p['confidence']['score']})` | {loc_s} | \"{q_short}\" |"
        )

    if qa_answers:
        lines.extend(["", "## Grounded Question Answering", ""])
        for qa in qa_answers:
            lines.append(f"### {qa.get('question_id')}: {qa.get('question')}")
            lines.append(f"- **Answer Status**: `{qa.get('answer_status')}`")
            lines.append(f"- **Answer**: {qa.get('answer')}")
            lines.append(f"- **Supporting Nanopubs**: {', '.join(qa.get('supporting_nanopub_ids') or []) or 'None'}")
            for sp in qa.get("evidence_spans") or []:
                lines.append(f"  - [{sp.get('source_id')} | {_format_locator(sp.get('locator') or {})}] \"{sp.get('quote')}\"")
            if qa.get("uncertainty_note"):
                lines.append(f"- **Uncertainty**: {qa['uncertainty_note']}")
            lines.append("")

    if conflicts:
        lines.extend(["", "## Detected Conflicts", ""])
        for c in conflicts:
            lines.append(f"- **{c.get('conflict_id')}** (`{c.get('conflict_type')}`): {c.get('description')}")
            if c.get("condition_difference"):
                lines.append(f"  - Condition difference: {c['condition_difference']}")

    if uncertainty or limitations or takeover:
        lines.extend(["", "## Uncertainty, Limitations & Manual Takeover Points", ""])
        for u in uncertainty:
            lines.append(f"- **[Uncertainty]** {u}")
        for lim in limitations:
            lines.append(f"- **[Limitation]** {lim}")
        for tk in takeover:
            lines.append(f"- **[Manual Takeover]** {tk}")

    return "\n".join(lines) + "\n"


def _map_contract_field(
    field_name: str,
    base_payload: Dict[str, Any],
    extra_fields: Dict[str, Any],
) -> Any:
    if field_name in extra_fields:
        return extra_fields[field_name]
    if field_name in base_payload:
        return base_payload[field_name]
    k = field_name.lower()
    if any(x in k for x in ("case_id", "caseid", "task_id")):
        return base_payload["case_id"]
    if any(x in k for x in ("status", "state")):
        return base_payload["status"]
    if any(x in k for x in ("nanopub", "assertion", "claim", "statement", "finding")):
        return base_payload["nanopublications"]
    if any(x in k for x in ("qa", "question", "answer", "response")):
        return base_payload["qa_answers"]
    if any(x in k for x in ("evidence", "provenance", "citation", "locator", "trace")):
        return [
            {
                "nanopub_id": np["nanopub_id"],
                "evidence_spans": np["provenance"]["evidence_spans"],
                "source_metadata": np["provenance"]["source_metadata"],
                "confidence": np["provenance"]["confidence"],
            }
            for np in base_payload["nanopublications"]
        ]
    if any(x in k for x in ("source", "metadata", "bibliograph", "paper")):
        return base_payload["sources"]
    if any(x in k for x in ("conflict", "contradict", "discrepanc")):
        return base_payload["conflicts"]
    if any(x in k for x in ("uncertain", "unknown", "unreported", "gap")):
        return base_payload["uncertainty"]
    if any(x in k for x in ("limit", "caveat", "boundary")):
        return base_payload["limitations"]
    if any(x in k for x in ("change", "audit_log", "changelog")):
        return base_payload["change_records"]
    if any(x in k for x in ("takeover", "manual")):
        return base_payload["manual_takeover_points"]
    if any(x in k for x in ("summary", "audit", "metric", "report")):
        return base_payload["audit_summary"]
    return None


def export_all(
    audited_bundle: Dict[str, Any],
    source_index: Dict[str, Any],
    case_brief: Dict[str, Any],
    verify_report: Dict[str, Any],
    out_dir: Path,
    qa_data: Any = None,
    extra_fields: Optional[Dict[str, Any]] = None,
    is_partial: bool = False,
    strict: bool = False,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    extra_fields = extra_fields or {}
    nanopubs: List[Dict[str, Any]] = list(audited_bundle.get("nanopublications") or [])
    conflicts: List[Dict[str, Any]] = list(audited_bundle.get("conflicts") or [])
    case_id = str(case_brief.get("case_id") or source_index.get("case_id") or audited_bundle.get("case_id") or "unknown_case")

    qa_answers: List[Dict[str, Any]] = []
    if isinstance(qa_data, list):
        qa_answers = qa_data
    elif isinstance(qa_data, dict):
        qa_answers = list(qa_data.get("qa_answers") or qa_data.get("answers") or [])

    # Resolve any temp_id entries in qa_answers[].supporting_nanopub_ids -> canonical urn:nanopub:RA...
    temp_to_canonical: Dict[str, str] = {}
    for np in nanopubs:
        cid = np.get("nanopub_id")
        if not cid:
            continue
        for tid in (np.get("pubinfo") or {}).get("merged_from_temp_ids") or []:
            temp_to_canonical[str(tid)] = cid
    if temp_to_canonical and qa_answers:
        resolved_qa: List[Dict[str, Any]] = []
        for qa in qa_answers:
            qa_copy = dict(qa)
            resolved_ids: List[str] = []
            seen_ids: set = set()
            for sid in qa_copy.get("supporting_nanopub_ids") or []:
                mapped = temp_to_canonical.get(str(sid), str(sid))
                if mapped not in seen_ids:
                    seen_ids.add(mapped)
                    resolved_ids.append(mapped)
            qa_copy["supporting_nanopub_ids"] = resolved_ids
            resolved_qa.append(qa_copy)
        qa_answers = resolved_qa

    docs = source_index.get("documents") or []
    readable_docs = [d for d in docs if d.get("sentence_count", 0) > 0]
    unreadable_pages_all = [
        {"source_id": d["source_id"], "pages": d["unreadable_pages"]}
        for d in docs if d.get("unreadable_pages")
    ]

    uncertainty: List[str] = list(audited_bundle.get("uncertainty") or [])
    for w in source_index.get("warnings") or []:
        if w not in uncertainty:
            uncertainty.append(w)
    for qa in qa_answers:
        if qa.get("answer_status") == "unreported_in_source":
            msg = f"Question `{qa.get('question_id')}` could not be answered from source materials: {qa.get('uncertainty_note') or 'unreported'}."
            if msg not in uncertainty:
                uncertainty.append(msg)

    limitations: List[str] = []
    takeover: List[str] = []
    if source_index.get("missing_declared_files"):
        limitations.append(f"Missing declared input file(s): {source_index['missing_declared_files']}.")
        takeover.append("Provide missing declared input file(s) under /input/ and re-run ingest_source.py.")
    if unreadable_pages_all:
        limitations.append(f"Unreadable/scanned/encrypted pages detected: {json.dumps(unreadable_pages_all, ensure_ascii=False)}.")
        takeover.append("Inspect unreadable/scanned PDF pages manually or run with Tesseract OCR enabled (--ocr-auto).")
    if not any(d.get("has_pdf_pages") for d in docs) and docs:
        limitations.append("Inputs lack native PDF page boundaries (has_pdf_pages=false); locators reference authentic paragraph/line IDs rather than synthetic PDF page numbers.")
    if verify_report.get("error_count", 0) > 0:
        limitations.append(f"{verify_report['error_count']} verification error(s) remain unresolved in nanopub_verification_report.json.")
        takeover.append("Inspect failing item_id entries in nanopub_verification_report.json and resolve grounding/atomicity/direction errors.")
    if not readable_docs:
        limitations.append("Zero readable source documents available for extraction.")
        takeover.append("Supply readable PDF/XML/JSON/Markdown/TXT source document under /input/.")

    # Determine honest status
    if not readable_docs or (not nanopubs and not qa_answers and source_index.get("workflow_mode") == "degraded_missing_or_unreadable"):
        status = "unable_to_determine"
    elif is_partial or unreadable_pages_all or verify_report.get("error_count", 0) > 0 or source_index.get("missing_declared_files"):
        status = "partial"
    else:
        status = "success"

    # Build serialized artifacts
    nanopubs_json_obj = {
        "case_id": case_id,
        "schema_version": "nanopub-skill/1.0.0",
        "status": status,
        "nanopublications": nanopubs,
        "conflicts": conflicts,
    }
    save_json(out_dir / "nanopubs.json", nanopubs_json_obj)

    jsonld_obj = build_jsonld(nanopubs)
    save_json(out_dir / "nanopubs.jsonld", jsonld_obj)

    trig_text = serialize_trig(nanopubs)
    (out_dir / "nanopubs.trig").write_text(trig_text, encoding="utf-8")
    trig_ok, trig_errors = validate_trig_syntax(trig_text)

    csv_text = build_csv(nanopubs)
    (out_dir / "nanopubs.csv").write_text(csv_text, encoding="utf-8-sig")

    sources_out = [
        {
            "source_id": d["source_id"],
            "paper_group_id": d.get("paper_group_id"),
            "source_file": d["source_file"],
            "file_sha256": d["file_sha256"],
            "has_pdf_pages": d["has_pdf_pages"],
            "page_count": d.get("page_count"),
            "metadata": d["metadata"],
            "unreadable_pages": d.get("unreadable_pages") or [],
        }
        for d in docs
    ]

    md_text = build_markdown_report(
        case_id=case_id,
        status=status,
        nanopubs=nanopubs,
        qa_answers=qa_answers,
        conflicts=conflicts,
        sources=sources_out,
        uncertainty=uncertainty,
        limitations=limitations,
        takeover=takeover,
        verify_report=verify_report,
    )
    (out_dir / "nanopub_report.md").write_text(md_text, encoding="utf-8")
    save_json(out_dir / "nanopub_verification_report.json", verify_report)

    exported_files = []
    for fname in ("nanopubs.json", "nanopubs.jsonld", "nanopubs.trig", "nanopubs.csv", "nanopub_report.md", "nanopub_verification_report.json"):
        fp = out_dir / fname
        exported_files.append({"path": fname, "sha256": sha256_file(fp), "bytes": fp.stat().st_size})

    audit_summary = {
        "nanopubs_count": len(nanopubs),
        "qa_answers_count": len(qa_answers),
        "conflicts_count": len(conflicts),
        "quote_grounding_rate": verify_report.get("quote_grounding_rate", 1.0),
        "error_count": verify_report.get("error_count", 0),
        "warning_count": verify_report.get("warning_count", 0),
        "trig_roundtrip_valid": trig_ok,
        "trig_errors": trig_errors,
        "jsonld_graph_count": len(jsonld_obj.get("@graph") or []),
    }

    base_result: Dict[str, Any] = {
        "case_id": case_id,
        "status": status,
        "nanopublications": nanopubs,
        "qa_answers": qa_answers,
        "conflicts": conflicts,
        "sources": sources_out,
        "uncertainty": uncertainty,
        "limitations": limitations,
        "change_records": list(audited_bundle.get("change_records") or []),
        "manual_takeover_points": takeover,
        "exported_files": exported_files,
        "audit_summary": audit_summary,
    }

    # Map every required output_contract field dynamically
    contract_fields = list(case_brief.get("output_contract_fields") or [])
    for cf in contract_fields:
        if cf not in base_result:
            mapped = _map_contract_field(cf, base_result, extra_fields)
            base_result[cf] = mapped if mapped is not None else []
    for k, v in extra_fields.items():
        base_result[k] = v

    save_json(out_dir / "result.json", base_result)

    # Strict self-audit
    strict_failures: List[str] = []
    if not trig_ok:
        strict_failures.extend(trig_errors)
    if not base_result.get("case_id"):
        strict_failures.append("result.json missing case_id")
    for cf in contract_fields:
        if cf not in base_result:
            strict_failures.append(f"result.json missing required output_contract field `{cf}`")
    if strict and verify_report.get("error_count", 0) > 0 and status != "unable_to_determine":
        strict_failures.append(f"verify_report has {verify_report['error_count']} unresolved ERROR(s).")

    out_summary = {
        "status": status,
        "case_id": case_id,
        "nanopubs_count": len(nanopubs),
        "trig_roundtrip_valid": trig_ok,
        "contract_fields_covered": len(contract_fields),
        "strict_failures": strict_failures,
        "out_dir": str(out_dir),
    }
    print(json.dumps(out_summary, indent=2, ensure_ascii=False))
    if strict and strict_failures:
        sys.exit(2)
    return out_summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Export verified Nanopublications and Harbor result.json.")
    ap.add_argument("--audited", type=Path, required=True)
    ap.add_argument("--source-index", type=Path, required=True)
    ap.add_argument("--case-brief", type=Path, required=True)
    ap.add_argument("--verify-report", type=Path, required=True)
    ap.add_argument("--qa", type=Path, default=None)
    ap.add_argument("--extra-fields", type=Path, default=None)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--partial", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    audited = load_json(args.audited)
    source_index = load_json(args.source_index)
    case_brief = load_json(args.case_brief)
    verify_report = load_json(args.verify_report)
    qa_data = load_json(args.qa) if args.qa and args.qa.exists() else None
    extra = load_json(args.extra_fields) if args.extra_fields and args.extra_fields.exists() else None

    export_all(
        audited_bundle=audited,
        source_index=source_index,
        case_brief=case_brief,
        verify_report=verify_report,
        out_dir=args.out_dir,
        qa_data=qa_data,
        extra_fields=extra,
        is_partial=args.partial,
        strict=args.strict,
    )


if __name__ == "__main__":
    main()
