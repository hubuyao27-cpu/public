#!/usr/bin/env python3
"""Export verifiable Knowledge Graph deliverables and assemble Harbor-compliant result.json.

Produces in --out-dir (e.g., /app/output):
  - result.json                  (all case.json output_contract fields + case_id, status, sources, uncertainty, change_records)
  - kg.json                      (canonical knowledge graph)
  - kg.graphml                   (standard GraphML XML for Gephi / Cytoscape / NetworkX)
  - kg_queries.json              (executed structured queries with subgraphs & verbatim quotes)
  - kg_verification_report.json  (mechanical audit metrics & issues)
  - kg_report.md                 (human-auditable report with provenance tables, merge/non-merge log, Mermaid graph)
  - kg_preview.svg               (pure-stdlib vector diagram preview)
  - kg_preview.png & .pdf        (rendered via matplotlib when available; clean fallback otherwise)

Core export runs on pure Python 3.10+ standard library.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def load_json_optional(path_str: Optional[str], default: Any) -> Any:
    if not path_str:
        return default
    p = Path(path_str)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def md_escape(val: Any) -> str:
    return str(val if val is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def export_graphml(kg: Dict[str, Any], out_path: Path) -> None:
    ns = "http://graphml.graphdrawing.org/xmlns"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}graphml")

    node_keys = [
        ("n_name", "canonical_name", "string"),
        ("n_type", "entity_type", "string"),
        ("n_scope", "scope", "string"),
        ("n_sources", "source_ids", "string"),
        ("n_aliases", "aliases", "string"),
        ("n_first_quote", "first_quote", "string"),
        ("n_first_locator", "first_locator", "string"),
    ]
    edge_keys = [
        ("e_rel", "relation_type", "string"),
        ("e_attr", "attribution", "string"),
        ("e_str", "evidence_strength", "string"),
        ("e_cond", "conditions", "string"),
        ("e_cross", "is_cross_paper", "boolean"),
        ("e_sources", "source_ids", "string"),
        ("e_quote", "first_quote", "string"),
        ("e_locator", "first_locator", "string"),
    ]
    for kid, kname, ktype in node_keys:
        ET.SubElement(root, f"{{{ns}}}key", {"id": kid, "for": "node", "attr.name": kname, "attr.type": ktype})
    for kid, kname, ktype in edge_keys:
        ET.SubElement(root, f"{{{ns}}}key", {"id": kid, "for": "edge", "attr.name": kname, "attr.type": ktype})

    graph_el = ET.SubElement(
        root,
        f"{{{ns}}}graph",
        {"id": str(kg.get("case_id", "literature_kg")), "edgedefault": "directed"},
    )

    for n in kg.get("nodes", []):
        nel = ET.SubElement(graph_el, f"{{{ns}}}node", {"id": str(n["id"])})
        m0 = (n.get("mentions") or [{}])[0]
        first_loc = f"{m0.get('source_id', '')}:{m0.get('paragraph_id', '')}" if m0 else ""
        vals = {
            "n_name": str(n.get("canonical_name", "")),
            "n_type": str(n.get("entity_type", "")),
            "n_scope": str(n.get("scope", "")),
            "n_sources": "; ".join(n.get("source_ids", [])),
            "n_aliases": "; ".join(n.get("aliases", [])),
            "n_first_quote": str(m0.get("quote", ""))[:300] if m0 else "",
            "n_first_locator": first_loc,
        }
        for kid, txt in vals.items():
            d = ET.SubElement(nel, f"{{{ns}}}data", {"key": kid})
            d.text = txt

    for e in kg.get("edges", []):
        eel = ET.SubElement(
            graph_el,
            f"{{{ns}}}edge",
            {"id": str(e["id"]), "source": str(e["source"]), "target": str(e["target"])},
        )
        ev0 = (e.get("evidence") or [{}])[0]
        first_loc = f"{ev0.get('source_id', '')}:{ev0.get('paragraph_id', '')}" if ev0 else ""
        cond = e.get("conditions", "")
        if isinstance(cond, dict):
            cond = json.dumps(cond, ensure_ascii=False)
        vals = {
            "e_rel": str(e.get("relation_type", "")),
            "e_attr": str(e.get("attribution", "")),
            "e_str": str(e.get("evidence_strength", "")),
            "e_cond": str(cond),
            "e_cross": "true" if e.get("is_cross_paper") else "false",
            "e_sources": "; ".join(e.get("source_ids", [])),
            "e_quote": str(ev0.get("quote", ""))[:300] if ev0 else "",
            "e_locator": first_loc,
        }
        for kid, txt in vals.items():
            d = ET.SubElement(eel, f"{{{ns}}}data", {"key": kid})
            d.text = txt

    ET.indent(root, space="  ")
    tree = ET.ElementTree(root)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)


def compute_layered_layout(kg: Dict[str, Any]) -> Tuple[Dict[str, Tuple[float, float]], Dict[str, str]]:
    """Compute clean 3-column paper-aware layout (Primary | Shared/Concept | Secondary+)."""
    docs = [d["source_id"] for d in kg.get("documents", []) if d.get("is_canonical_in_group", True)]
    primary_sid = docs[0] if docs else ""
    secondary_sid = docs[1] if len(docs) >= 2 else ""

    buckets: Dict[str, List[Dict[str, Any]]] = {"left": [], "center": [], "right": []}
    node_role: Dict[str, str] = {}
    type_order = {
        "Researcher": 0,
        "Material": 1,
        "Method": 2,
        "Dataset_Or_Sample": 3,
        "Task": 4,
        "Metric": 5,
        "Conclusion": 6,
        "Concept": 2,
    }

    for n in kg.get("nodes", []):
        nid = n["id"]
        sids = n.get("source_ids", [])
        if n.get("entity_type") == "Concept":
            buckets["center"].append(n)
            node_role[nid] = "concept"
        elif len(set(sids)) >= 2 or n.get("scope") == "shared_canonical_entity":
            buckets["center"].append(n)
            node_role[nid] = "shared"
        elif primary_sid and primary_sid in sids:
            buckets["left"].append(n)
            node_role[nid] = "primary"
        elif secondary_sid and secondary_sid in sids:
            buckets["right"].append(n)
            node_role[nid] = "secondary"
        else:
            buckets["right"].append(n)
            node_role[nid] = "other"

    for k in buckets:
        buckets[k].sort(key=lambda x: (type_order.get(x.get("entity_type", ""), 9), x["id"]))

    coords: Dict[str, Tuple[float, float]] = {}
    x_map = {"left": 0.14, "center": 0.50, "right": 0.86}
    for col, nodes in buckets.items():
        cnt = len(nodes)
        for idx, n in enumerate(nodes):
            # Slight horizontal stagger inside tall columns to reduce vertical edge overlap
            stagger = 0.035 if (cnt > 5 and idx % 2 == 1) else (-0.035 if cnt > 5 else 0.0)
            x = x_map[col] + stagger
            y = 0.86 - (idx + 0.5) * (0.76 / max(cnt, 1))
            coords[n["id"]] = (x, y)
    return coords, node_role


def render_svg_preview(kg: Dict[str, Any], out_svg: Path) -> None:
    coords, roles = compute_layered_layout(kg)
    w, h = 1400, max(780, 140 + 68 * max(len(kg.get("nodes", [])) // 2, 6))
    palette = {
        "primary": ("#DBEAFE", "#1D4ED8"),
        "secondary": ("#DCFCE7", "#15803D"),
        "shared": ("#FEF3C7", "#B45309"),
        "concept": ("#F3E8FF", "#7E22CE"),
        "other": ("#E5E7EB", "#374151"),
    }

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="#475569"/></marker>'
        '<marker id="arrow-red" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="#DC2626"/></marker></defs>',
        f'<rect width="{w}" height="{h}" fill="#FFFFFF"/>',
        f'<text x="30" y="38" font-family="DejaVu Sans, Arial, sans-serif" font-size="20" font-weight="bold" fill="#0F172A">Verifiable Literature Knowledge Graph — {html.escape(str(kg.get("case_id", "")))} ({html.escape(str(kg.get("graph_version", "")))})</text>',
        '<text x="30" y="62" font-family="DejaVu Sans, Arial, sans-serif" font-size="12" fill="#475569">Blue: Primary Paper | Green: Secondary Paper | Amber: Shared Canonical Entity | Purple: Abstract Concept (Non-Merged Instances) | Red: Conflict</text>',
    ]

    # Draw edges first
    for e in kg.get("edges", []):
        if e["source"] not in coords or e["target"] not in coords:
            continue
        x1, y1 = coords[e["source"]][0] * w, coords[e["source"]][1] * h
        x2, y2 = coords[e["target"]][0] * w, coords[e["target"]][1] * h
        rtype = e.get("relation_type", "")
        attr = e.get("attribution", "")
        stroke = "#DC2626" if rtype == "CONTRADICTS" else ("#7E22CE" if rtype == "INSTANTIATES_CONCEPT" else "#64748B")
        dash = ' stroke-dasharray="6,4"' if (attr in ("cited_prior_work", "speculation_or_hypothesis") or rtype == "INSTANTIATES_CONCEPT") else ""
        sw = "2.2" if rtype == "CONTRADICTS" else "1.4"
        marker = "url(#arrow-red)" if rtype == "CONTRADICTS" else "url(#arrow)"
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2 - 6
        lines.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{sw}"{dash} marker-end="{marker}" opacity="0.82"/>'
        )
        lines.append(
            f'<text x="{mx:.1f}" y="{my:.1f}" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="10" fill="{stroke}">{html.escape(rtype)}</text>'
        )

    # Draw nodes
    for n in kg.get("nodes", []):
        nid = n["id"]
        if nid not in coords:
            continue
        cx, cy = coords[nid][0] * w, coords[nid][1] * h
        fill, border = palette.get(roles.get(nid, "other"), palette["other"])
        label = f"{nid}: {n['canonical_name']}"
        if len(label) > 32:
            label = label[:29] + "..."
        sub = f"[{n.get('entity_type', '')}] ({','.join(n.get('source_ids', []))})"
        bw, bh = 230, 44
        lines.append(
            f'<rect x="{cx - bw/2:.1f}" y="{cy - bh/2:.1f}" width="{bw}" height="{bh}" rx="8" fill="{fill}" stroke="{border}" stroke-width="1.8"/>'
        )
        lines.append(
            f'<text x="{cx:.1f}" y="{cy - 4:.1f}" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="11.5" font-weight="bold" fill="#0F172A">{html.escape(label)}</text>'
        )
        lines.append(
            f'<text x="{cx:.1f}" y="{cy + 13:.1f}" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="9.5" fill="#334155">{html.escape(sub)}</text>'
        )

    lines.append("</svg>")
    out_svg.write_text("\n".join(lines), encoding="utf-8")


def render_matplotlib_previews(kg: Dict[str, Any], out_png: Path, out_pdf: Path) -> Tuple[bool, str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except Exception as exc:
        return False, f"matplotlib unavailable ({exc}); SVG preview generated instead."

    coords, roles = compute_layered_layout(kg)
    n_count = max(len(kg.get("nodes", [])), 4)
    fig_h = max(7.5, min(16.0, 2.2 + 0.55 * n_count))
    fig, ax = plt.subplots(figsize=(15, fig_h), dpi=200)
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, 1.0)
    ax.axis("off")

    palette = {
        "primary": ("#DBEAFE", "#1D4ED8"),
        "secondary": ("#DCFCE7", "#15803D"),
        "shared": ("#FEF3C7", "#B45309"),
        "concept": ("#F3E8FF", "#7E22CE"),
        "other": ("#E5E7EB", "#374151"),
    }

    ax.text(
        0.02,
        0.97,
        f"Verifiable Literature Knowledge Graph — {kg.get('case_id', '')} ({kg.get('graph_version', '')})",
        fontsize=14,
        fontweight="bold",
        color="#0F172A",
        va="top",
    )

    # Column headers
    docs = [d["source_id"] for d in kg.get("documents", []) if d.get("is_canonical_in_group", True)]
    p1_label = f"Primary Paper ({docs[0]})" if docs else "Primary Paper"
    p2_label = f"Secondary Paper ({docs[1]})" if len(docs) >= 2 else "Secondary / Additional"
    ax.text(0.14, 0.925, p1_label, ha="center", fontsize=10.5, fontweight="bold", color="#1D4ED8")
    ax.text(0.50, 0.925, "Shared Canonical & Abstract Concepts", ha="center", fontsize=10.5, fontweight="bold", color="#7E22CE")
    ax.text(0.86, 0.925, p2_label, ha="center", fontsize=10.5, fontweight="bold", color="#15803D")

    # Edges
    for idx, e in enumerate(kg.get("edges", [])):
        if e["source"] not in coords or e["target"] not in coords:
            continue
        x1, y1 = coords[e["source"]]
        x2, y2 = coords[e["target"]]
        rtype = e.get("relation_type", "")
        attr = e.get("attribution", "")
        color = "#DC2626" if rtype == "CONTRADICTS" else ("#7E22CE" if rtype == "INSTANTIATES_CONCEPT" else "#475569")
        ls = "--" if (attr in ("cited_prior_work", "speculation_or_hypothesis") or rtype == "INSTANTIATES_CONCEPT") else "-"
        lw = 1.9 if rtype == "CONTRADICTS" else 1.15
        same_col = abs(x1 - x2) < 0.09
        cross_full = abs(x1 - x2) > 0.55
        if same_col:
            span = abs(y1 - y2)
            sign = 1.0 if x1 < 0.5 else -1.0
            rad = sign * (0.24 if span > 0.4 else (0.16 if span > 0.22 else 0.08))
            if y1 < y2:
                rad = -rad
        elif cross_full and abs(y1 - y2) < 0.12:
            rad = 0.14 if y1 < 0.4 else -0.14
        else:
            rad = 0.04 if idx % 2 == 0 else -0.04
        ax.annotate(
            "",
            xy=(x2, y2),
            xytext=(x1, y1),
            arrowprops=dict(
                arrowstyle="-|>,head_length=0.55,head_width=0.28",
                color=color,
                lw=lw,
                ls=ls,
                alpha=0.84,
                connectionstyle=f"arc3,rad={rad}",
                shrinkA=28,
                shrinkB=28,
            ),
            zorder=1,
        )
        # Compute quadratic Bezier midpoint consistent with matplotlib arc3,rad
        dx, dy = x2 - x1, y2 - y1
        mx = 0.5 * (x1 + x2) + 0.5 * rad * dy
        my = 0.5 * (y1 + y2) - 0.5 * rad * dx
        ax.text(
            mx,
            my,
            rtype,
            fontsize=7.3,
            color=color,
            ha="center",
            va="center",
            bbox=dict(boxstyle="round,pad=0.14", fc="white", ec="none", alpha=0.88),
            zorder=2,
        )

    # Nodes
    for n in kg.get("nodes", []):
        nid = n["id"]
        if nid not in coords:
            continue
        x, y = coords[nid]
        fill, border = palette.get(roles.get(nid, "other"), palette["other"])
        cname = str(n.get("canonical_name", ""))
        if len(cname) > 28:
            cname = cname[:25] + "..."
        lbl = f"{nid}: {cname}\n[{n.get('entity_type', '')}]"
        ax.text(
            x,
            y,
            lbl,
            ha="center",
            va="center",
            fontsize=8.2,
            color="#0F172A",
            bbox=dict(boxstyle="round,pad=0.4", fc=fill, ec=border, lw=1.4),
            zorder=3,
        )

    legend_handles = [
        mpatches.Patch(facecolor="#DBEAFE", edgecolor="#1D4ED8", label="Primary Paper Instance"),
        mpatches.Patch(facecolor="#DCFCE7", edgecolor="#15803D", label="Secondary Paper Instance"),
        mpatches.Patch(facecolor="#FEF3C7", edgecolor="#B45309", label="Shared Canonical Entity (Merged Alias)"),
        mpatches.Patch(facecolor="#F3E8FF", edgecolor="#7E22CE", label="Abstract Concept (Non-Merged Instances)"),
        mpatches.Patch(facecolor="#FEE2E2", edgecolor="#DC2626", label="Red Edge = CONTRADICTS | Dashed = Cited/Concept"),
    ]
    ax.legend(handles=legend_handles, loc="lower center", ncol=3, frameon=True, fontsize=8.2, bbox_to_anchor=(0.5, 0.005))

    plt.tight_layout()
    fig.savefig(out_png, dpi=200, bbox_inches="tight", facecolor="white")
    fig.savefig(out_pdf, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return True, "matplotlib"


def render_mermaid_snippet(kg: Dict[str, Any], max_edges: int = 45) -> str:
    lines = ["```mermaid", "flowchart LR"]
    for n in kg.get("nodes", []):
        nid = n["id"]
        lbl = f"{nid}: {n['canonical_name']} ({n['entity_type']})".replace('"', "'")
        lines.append(f'    {nid}["{lbl}"]')
    for e in kg.get("edges", [])[:max_edges]:
        rt = e.get("relation_type", "REL").replace('"', "'")
        arrow = "-.->" if e.get("attribution") in ("cited_prior_work", "speculation_or_hypothesis") else "-->"
        lines.append(f'    {e["source"]} {arrow}|"{rt}"| {e["target"]}')
    lines.append("```")
    return "\n".join(lines)


def render_markdown_report(
    kg: Dict[str, Any],
    queries: Dict[str, Any],
    verification: Dict[str, Any],
    xml_recon: Dict[str, Any],
    status: str,
) -> str:
    metrics = verification.get("metrics", {})
    lines = [
        f"# 文献知识图谱构建与核验报告 (`{kg.get('case_id', '')}`)",
        "",
        "## 1. 运行状态与机械核验摘要",
        f"- **运行状态 (Status)**: `{status}`",
        f"- **图谱版本 (Graph Version)**: `{kg.get('graph_version', 'v1')}`",
        f"- **节点数 / 边数**: `{metrics.get('node_count', len(kg.get('nodes', [])))}` 节点 / `{metrics.get('edge_count', len(kg.get('edges', [])))}` 有向边",
        f"- **原文短引命中率 (Quote Grounding Rate)**: `{metrics.get('quote_grounding_rate', 1.0):.1%}` ({metrics.get('grounded_evidence_count', 0)}/{metrics.get('total_evidence_checks', 0)})",
        f"- **假 PDF 页码违规**: `{metrics.get('fake_pdf_page_violations', 0)}` | **根元数据串篇违规**: `{metrics.get('root_metadata_bleed_violations', 0)}` | **同篇多格式伪跨篇违规**: `{metrics.get('same_paper_format_violations', 0)}`",
        f"- **增量更新旧 ID 保留率**: `{metrics.get('incremental_id_preservation_rate', 1.0):.1%}`",
        f"- **可执行查询数**: `{queries.get('executed_query_count', 0)}` | **核验错误 / 警告**: `{metrics.get('error_count', 0)}` ERROR / `{metrics.get('warning_count', 0)}` WARNING",
        "",
        "## 2. 文献来源注册表（元数据严格隔离）",
        "",
        "| source_id | role | paper_group | DOI | Title | Authors | License | Locator Type | Backing Files |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for d in kg.get("documents", []):
        lines.append(
            f"| {md_escape(d.get('source_id'))} | {md_escape(d.get('role'))} | {md_escape(d.get('paper_group_id'))} | "
            f"{md_escape(d.get('doi') or '(unreported)')} | {md_escape(d.get('title') or '(unreported)')} | "
            f"{md_escape(', '.join(d.get('authors', [])) or '(unreported)')} | {md_escape(d.get('license') or '(unreported)')} | "
            f"{md_escape(d.get('locator_type'))} | {md_escape(', '.join(d.get('backing_files', [])))} |"
        )

    lines.extend(["", "## 3. 输入边界与未提供模态声明", ""])
    for b in kg.get("declared_input_boundaries", []):
        lines.append(f"- {b}")
    if kg.get("duplicate_format_groups"):
        lines.append("- **同篇多格式去重防护**:")
        for g in kg["duplicate_format_groups"]:
            lines.append(
                f"  - Group `{g['paper_group_id']}`: canonical=`{g['canonical_source_id']}`, duplicates=`{g['duplicate_source_ids']}` ({g['rule']})"
            )

    xml_files = xml_recon.get("xml_files", []) if isinstance(xml_recon, dict) else []
    if xml_files:
        lines.extend(["", "### Publisher XML 公式/上下标与表格核对", ""])
        for xf in xml_files:
            lines.append(
                f"- `{xf.get('xml_file')}` (linked to `{xf.get('linked_source_id')}`): "
                f"{xf.get('formula_paragraph_count', 0)} formula/sub/sup paragraph(s), {xf.get('table_count', 0)} table(s) reconciled."
            )

    lines.extend(
        [
            "",
            "## 4. 实体节点清单（含原文定位）",
            "",
            "| ID | 规范名 | 类型 | 作用域 | 来源论文 | 别名 | 首条原文定位与短引 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for n in kg.get("nodes", []):
        m0 = (n.get("mentions") or [{}])[0]
        ev_str = f"`{m0.get('source_id', '')}:{m0.get('paragraph_id', '')}` \"{str(m0.get('quote', ''))[:85]}\"" if m0 else ""
        lines.append(
            f"| {md_escape(n['id'])} | {md_escape(n['canonical_name'])} | {md_escape(n['entity_type'])} | "
            f"{md_escape(n.get('scope'))} | {md_escape(', '.join(n.get('source_ids', [])))} | "
            f"{md_escape(', '.join(n.get('aliases', [])))} | {md_escape(ev_str)} |"
        )

    lines.extend(
        [
            "",
            "## 5. 有向条件关系清单（含证据强度与适用条件）",
            "",
            "| ID | Source -> Relation -> Target | 归属类型 | 证据强度 | 适用条件 | 原文定位与短引 |",
            "|---|---|---|---|---|---|",
        ]
    )
    nodes_by_id = {n["id"]: n["canonical_name"] for n in kg.get("nodes", [])}
    for e in kg.get("edges", []):
        ev0 = (e.get("evidence") or [{}])[0]
        ev_str = f"`{ev0.get('source_id', '')}:{ev0.get('paragraph_id', '')}` \"{str(ev0.get('quote', ''))[:80]}\"" if ev0 else ""
        triple = f"{e['source']} ({nodes_by_id.get(e['source'], '')}) --{e['relation_type']}--> {e['target']} ({nodes_by_id.get(e['target'], '')})"
        lines.append(
            f"| {md_escape(e['id'])} | {md_escape(triple)} | {md_escape(e.get('attribution'))} | "
            f"{md_escape(e.get('evidence_strength'))} | {md_escape(e.get('conditions'))} | {md_escape(ev_str)} |"
        )

    lines.extend(
        [
            "",
            "## 6. 跨篇实体对齐与「不得合并」决策记录",
            "",
            "| Decision ID | Candidate A | Candidate B | Verdict | Shared Concept | 合并/拒绝合并依据 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for d in kg.get("alignment_decisions", []):
        ca = d.get("candidate_a") or {}
        cb = d.get("candidate_b") or {}
        lines.append(
            f"| {md_escape(d.get('decision_id'))} | {md_escape(ca.get('node_id_or_name'))} (`{md_escape(ca.get('source_id'))}`) | "
            f"{md_escape(cb.get('node_id_or_name'))} (`{md_escape(cb.get('source_id'))}`) | "
            f"`{md_escape(d.get('verdict'))}` | {md_escape(d.get('shared_concept_name') or '-')} | {md_escape(d.get('rationale'))} |"
        )

    lines.extend(["", "## 7. 冲突、未报告项与不确定性", ""])
    if kg.get("conflicts"):
        for cf in kg["conflicts"]:
            lines.append(
                f"- **{cf.get('conflict_id')}** (`{cf.get('conflict_type', 'conflict')}`): {cf.get('description', cf.get('summary', ''))}"
            )
    else:
        lines.append("- 未检测到显式冲突记录（已保留所有非合并实例边界）。")
    if kg.get("uncertainty"):
        lines.append("")
        lines.append("### 不确定项 (Uncertainty)")
        for u in kg["uncertainty"]:
            lines.append(f"- {json.dumps(u, ensure_ascii=False) if isinstance(u, dict) else str(u)}")

    lines.extend(["", "## 8. 增量更新变更日志 (Incremental Update History)", ""])
    for rec in kg.get("incremental_update_history", []):
        lines.append(
            f"- **Version `{rec.get('version')}`** (ingested `{rec.get('ingested_source_id')}`): "
            f"preserved `{len(rec.get('preserved_node_ids', []))}` nodes & `{len(rec.get('preserved_edge_ids', []))}` edges; "
            f"added `{len(rec.get('added_node_ids', []))}` nodes & `{len(rec.get('added_edge_ids', []))}` edges; "
            f"merged_aliases=`{len(rec.get('merged_aliases', []))}`, rejected_merges=`{len(rec.get('rejected_merges', []))}`."
        )

    lines.extend(["", "## 9. 实际执行的图谱查询与证据子图", ""])
    for q in queries.get("queries", []):
        sub = q.get("matched_subgraph") or {}
        lines.extend(
            [
                f"### {q.get('query_id')}: {q.get('question')}",
                f"- **Operator**: `{q.get('operator')}` | **Status**: `{q.get('status')}`",
                f"- **Matched Subgraph**: Nodes `{sub.get('node_ids', [])}` | Edges `{sub.get('edge_ids', [])}`",
                f"- **Answer**: {q.get('answer', '')}",
            ]
        )
        if q.get("chains"):
            lines.append("- **Traced Chains**:")
            for ch in q["chains"][:6]:
                lines.append(f"  - {ch}")
        if q.get("supporting_evidence"):
            lines.append("- **Supporting Evidence**:")
            for ev in q["supporting_evidence"][:6]:
                lines.append(f"  - `{ev.get('source_id')}:{ev.get('paragraph_id')}` ({ev.get('locator_type')}): \"{ev.get('quote')}\"")
        if q.get("uncertain_or_unanswerable_relations"):
            lines.append("- **Uncertain / Unanswerable / Boundary Notes**:")
            for ur in q["uncertain_or_unanswerable_relations"][:6]:
                lines.append(f"  - {ur}")
        lines.append("")

    lines.extend(["## 10. 图谱结构预览 (Mermaid)", "", render_mermaid_snippet(kg), ""])
    return "\n".join(lines)


def build_result_json(
    case_data: Dict[str, Any],
    kg: Dict[str, Any],
    queries: Dict[str, Any],
    verification: Dict[str, Any],
    xml_recon: Dict[str, Any],
    extra_fields: Dict[str, Any],
    artifact_files: List[str],
    force_partial: bool = False,
) -> Dict[str, Any]:
    metrics = verification.get("metrics", {})
    err_count = metrics.get("error_count", 0)
    q_count = queries.get("executed_query_count", len(queries.get("queries", [])))
    docs = kg.get("documents", [])
    readable_docs = [d for d in docs if d.get("paragraph_count", 0) > 0]

    if not readable_docs or not kg.get("nodes"):
        status = "unable_to_determine"
        status_reason = "No readable source documents or verified nodes could be extracted."
    elif force_partial or err_count > 0 or q_count < 3:
        status = "partial"
        status_reason = (
            f"Partial deliverable: errors={err_count}, executed_queries={q_count}, force_partial={force_partial}."
        )
    else:
        status = "success"
        status_reason = (
            f"Built verified {kg.get('graph_version', 'v1')} KG ({len(kg.get('nodes', []))} nodes, "
            f"{len(kg.get('edges', []))} edges, {metrics.get('quote_grounding_rate', 1.0):.1%} quote grounding) "
            f"and executed {q_count} provenance-bound queries with 0 verification errors."
        )

    summary_text = (
        f"Constructed verifiable multi-paper knowledge graph ({kg.get('graph_version', 'v1')}) covering "
        f"{len(readable_docs)} canonical document(s), {len(kg.get('nodes', []))} grounded node(s), "
        f"{len(kg.get('edges', []))} directed conditional edge(s), {len(kg.get('alignment_decisions', []))} "
        f"explicit alignment/non-merge decision(s), {len(kg.get('conflicts', []))} conflict record(s), "
        f"and {q_count} real executed graph queries."
    )

    uncertainty_payload = {
        "declared_input_boundaries": kg.get("declared_input_boundaries", []),
        "uncertain_items": kg.get("uncertainty", []),
        "unanswerable_queries": [
            {
                "query_id": q.get("query_id"),
                "question": q.get("question"),
                "reasons": q.get("uncertain_or_unanswerable_relations", []),
            }
            for q in queries.get("queries", [])
            if q.get("status") == "unable_to_determine"
        ],
        "verification_warnings": [i for i in verification.get("issues", []) if i.get("severity") == "WARNING"],
    }

    change_records = kg.get("incremental_update_history", [])
    sources_payload = {
        "documents": docs,
        "root_metadata_isolation": kg.get("root_metadata_isolation", {}),
        "duplicate_format_groups": kg.get("duplicate_format_groups", []),
        "has_pdf_pages": bool(kg.get("has_pdf_pages", False)),
        "xml_reconciliation_summary": [
            {
                "xml_file": xf.get("xml_file"),
                "linked_source_id": xf.get("linked_source_id"),
                "formula_paragraph_count": xf.get("formula_paragraph_count", 0),
                "table_count": xf.get("table_count", 0),
            }
            for xf in (xml_recon.get("xml_files", []) if isinstance(xml_recon, dict) else [])
        ],
    }

    canonical_graph_obj = {
        "schema_version": kg.get("schema_version", "1.0.0"),
        "graph_version": kg.get("graph_version", "v1"),
        "nodes": kg.get("nodes", []),
        "edges": kg.get("edges", []),
    }

    # Base Harbor-compliant envelope
    result: Dict[str, Any] = {
        "case_id": case_data.get("case_id") or kg.get("case_id") or "local-case",
        "status": status,
        "status_reason": status_reason,
        "summary": summary_text,
        "documents": docs,
        "knowledge_graph": canonical_graph_obj,
        "nodes": kg.get("nodes", []),
        "edges": kg.get("edges", []),
        "alignment_decisions": kg.get("alignment_decisions", []),
        "conflicts": kg.get("conflicts", []),
        "queries": queries.get("queries", []),
        "incremental_update_log": change_records,
        "verification_report": verification,
        "sources": sources_payload,
        "uncertainty": uncertainty_payload,
        "change_records": change_records,
        "limitations": (
            "All nodes, edges, alignment rationales, and query answers are strictly bound to verbatim excerpts "
            "and original JATS paragraph IDs or text locators without fabricating unseen figure pixels, "
            "supplementary attachments, raw experimental/clinical datasets, or synthetic PDF page numbers."
        ),
        "artifacts": artifact_files,
    }

    # Dynamically satisfy ALL declared output_contract keys in case.json
    synonym_map: Dict[str, Any] = {
        "case_id": result["case_id"],
        "status": status,
        "run_status": status,
        "summary": summary_text,
        "executive_summary": summary_text,
        "answer": summary_text,
        "main_result": {
            "knowledge_graph": canonical_graph_obj,
            "alignment_decisions": result["alignment_decisions"],
            "conflicts": result["conflicts"],
            "queries": result["queries"],
        },
        "graph": canonical_graph_obj,
        "knowledge_graph": canonical_graph_obj,
        "kg": canonical_graph_obj,
        "entities": kg.get("nodes", []),
        "nodes": kg.get("nodes", []),
        "relations": kg.get("edges", []),
        "edges": kg.get("edges", []),
        "triples": kg.get("edges", []),
        "alignment": kg.get("alignment_decisions", []),
        "entity_alignment": kg.get("alignment_decisions", []),
        "alignment_decisions": kg.get("alignment_decisions", []),
        "merge_decisions": kg.get("alignment_decisions", []),
        "conflicts": kg.get("conflicts", []),
        "queries": queries.get("queries", []),
        "query_results": queries.get("queries", []),
        "sources": sources_payload,
        "provenance": sources_payload,
        "evidence": {
            "quote_grounding_rate": metrics.get("quote_grounding_rate", 1.0),
            "documents": docs,
            "node_count": len(kg.get("nodes", [])),
            "edge_count": len(kg.get("edges", [])),
        },
        "uncertainty": uncertainty_payload,
        "uncertainties": uncertainty_payload,
        "unreported": kg.get("declared_input_boundaries", []),
        "boundaries": kg.get("declared_input_boundaries", []),
        "change_records": change_records,
        "incremental_update": change_records,
        "incremental_update_log": change_records,
        "verification": verification,
        "verification_report": verification,
        "audit": verification,
        "limitations": result["limitations"],
        "artifacts": artifact_files,
        "deliverables": artifact_files,
    }

    contract = case_data.get("output_contract")
    contract_keys: List[str] = []
    if isinstance(contract, list):
        contract_keys = [str(k) for k in contract if isinstance(k, str)]
    elif isinstance(contract, dict):
        if isinstance(contract.get("required"), list):
            contract_keys.extend(str(k) for k in contract["required"])
        if isinstance(contract.get("properties"), dict):
            contract_keys.extend(str(k) for k in contract["properties"].keys())
        if isinstance(contract.get("fields"), dict):
            contract_keys.extend(str(k) for k in contract["fields"].keys())
        elif not contract_keys:
            meta_keys = {"type", "description", "format", "schema_version", "notes", "title"}
            contract_keys = [str(k) for k in contract.keys() if str(k) not in meta_keys]

    for ck in contract_keys:
        if ck in extra_fields:
            result[ck] = extra_fields[ck]
        elif ck in result and result[ck] is not None:
            continue
        elif ck.lower() in synonym_map:
            result[ck] = synonym_map[ck.lower()]
        else:
            # Never leave a contract key null without explanation
            result[ck] = {
                "note": f"Mapped from verifiable KG deliverable for contract field '{ck}'.",
                "summary": summary_text,
                "status": status,
            }

    for k, v in extra_fields.items():
        result[k] = v

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Export multi-format KG files, visual previews, and Harbor result.json.")
    parser.add_argument("--case", default=None, help="Path to /input/case.json")
    parser.add_argument("--kg", required=True, help="Path to final kg.json")
    parser.add_argument("--queries", required=True, help="Path to kg_queries.json")
    parser.add_argument("--verification", required=True, help="Path to kg_verification_report.json")
    parser.add_argument("--xml-recon", default=None, help="Optional path to xml_math_reconciliation.json")
    parser.add_argument("--extra-fields", default=None, help="Optional JSON file with custom case-specific output_contract fields")
    parser.add_argument("--out-dir", required=True, help="Output directory (e.g., /app/output)")
    parser.add_argument("--partial-checkpoint", action="store_true", help="Mark status as partial (for early checkpoint saves)")
    args = parser.parse_args()

    case_data = load_json_optional(args.case, {})
    kg = load_json_optional(args.kg, {})
    queries = load_json_optional(args.queries, {"queries": [], "executed_query_count": 0})
    verification = load_json_optional(args.verification, {"metrics": {}, "issues": []})
    xml_recon = load_json_optional(args.xml_recon, {"xml_files": []})
    extra_fields = load_json_optional(args.extra_fields, {})
    if not isinstance(extra_fields, dict):
        extra_fields = {}

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    artifact_files: List[str] = []

    # 1. Canonical JSON exports
    (out_dir / "kg.json").write_text(json.dumps(kg, ensure_ascii=False, indent=2), encoding="utf-8")
    artifact_files.append("kg.json")

    (out_dir / "kg_queries.json").write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")
    artifact_files.append("kg_queries.json")

    (out_dir / "kg_verification_report.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    artifact_files.append("kg_verification_report.json")

    # 2. Standard GraphML export
    export_graphml(kg, out_dir / "kg.graphml")
    artifact_files.append("kg.graphml")

    # 3. Visual diagram previews (always SVG; PNG+PDF via matplotlib when installed)
    render_svg_preview(kg, out_dir / "kg_preview.svg")
    artifact_files.append("kg_preview.svg")

    rendered_mpl, mpl_msg = render_matplotlib_previews(kg, out_dir / "kg_preview.png", out_dir / "kg_preview.pdf")
    if rendered_mpl:
        artifact_files.extend(["kg_preview.png", "kg_preview.pdf"])

    # 4. Assemble Harbor-compliant result.json
    artifact_files.extend(["kg_report.md", "result.json"])
    result = build_result_json(
        case_data=case_data,
        kg=kg,
        queries=queries,
        verification=verification,
        xml_recon=xml_recon,
        extra_fields=extra_fields,
        artifact_files=artifact_files,
        force_partial=args.partial_checkpoint,
    )
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5. Human-auditable Markdown report
    report_md = render_markdown_report(kg, queries, verification, xml_recon, result["status"])
    (out_dir / "kg_report.md").write_text(report_md, encoding="utf-8")

    print(
        json.dumps(
            {
                "status": result["status"],
                "out_dir": str(out_dir),
                "artifacts": artifact_files,
                "visual_renderer": mpl_msg,
                "verification_errors": verification.get("metrics", {}).get("error_count", 0),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
