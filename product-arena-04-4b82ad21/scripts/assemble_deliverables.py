#!/usr/bin/env python3
"""Step 3 (Early Partial Checkpoint) & Step 6 (Final Verified Deliverables Assembly).

Outputs under --out-dir (default /app/output):
- result.json                        (Harbor-compliant with case_id + ALL output_contract keys + provenance)
- introduction_and_related_work.md   (Clean publication-ready manuscript + target-style citations + bibliography)
- related_work_matrix.md & .csv      (Multi-dimensional grouped comparison matrix with Title column)
- evidence_and_citation_audit.md     (Independent 7-section verification & author confirmation report)
- provenance_map.json                (Machine-verifiable claim/gap/contribution source links)
- Conditional exports + rendered PDF/PNG previews when requested on final checkpoint (tex, bib, docx, diagram)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    format_citation_entry,
    load_json_safe,
    order_refs_by_appearance,
    render_inline_citations,
    resolve_manuscript_text,
    save_json,
    sync_payload_structures,
)


def _effective_style(payload: Dict[str, Any]) -> str:
    style = str(payload.get("citation_style") or "gbt7714").strip()
    if style == "unspecified" or not style:
        return "gbt7714" if payload.get("language") == "zh" else "ieee_numeric"
    return style


def _enrich_matrix_with_titles(matrix: List[Dict[str, Any]], refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    title_by_id = {
        str(r.get("ref_id") or "").strip().lower(): str(r.get("title") or "")
        for r in refs
        if isinstance(r, dict)
    }
    enriched: List[Dict[str, Any]] = []
    for r in matrix:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        if not row.get("title"):
            rid = str(row.get("ref_id") or "").strip().lower()
            row["title"] = title_by_id.get(rid, "")
        enriched.append(row)
    return enriched


def _render_matrix_md(matrix: List[Dict[str, Any]]) -> str:
    lines = [
        "# Related Work Multi-Dimensional Comparison Matrix (相关工作多维对照表)",
        "",
        "| Ref ID | Title | Route / Group | Problem Setting | Method Route | Dataset & Eval | Key Result | Applicability Conditions | Limitations | Source Locator |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in matrix:
        if not isinstance(r, dict):
            continue
        cells = [
            str(r.get("ref_id") or "-"),
            str(r.get("title") or "-"),
            str(r.get("route") or "-"),
            str(r.get("problem_setting") or "-"),
            str(r.get("method_route") or "-"),
            str(r.get("dataset_and_eval") or "-"),
            str(r.get("key_result") or "-"),
            str(r.get("applicability_conditions") or "-"),
            str(r.get("limitations") or "-"),
            f"`{r.get('source_locator') or '-'}`",
        ]
        lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    if len(lines) == 4:
        lines.append("| - | - | *No structured comparisons populated yet* | - | - | - | - | - | - | - |")
    return "\n".join(lines) + "\n"


def _write_matrix_csv(matrix: List[Dict[str, Any]], out_path: Path) -> None:
    cols = [
        "ref_id",
        "title",
        "route",
        "problem_setting",
        "method_route",
        "dataset_and_eval",
        "key_result",
        "applicability_conditions",
        "limitations",
        "source_locator",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in matrix:
        if isinstance(r, dict):
            writer.writerow(r)
    out_path.write_text(buf.getvalue(), encoding="utf-8-sig", newline="\n")


def _fill_reference_citations(refs: List[Dict[str, Any]], style: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx, r in enumerate(refs, start=1):
        if not isinstance(r, dict):
            continue
        item = dict(r)
        if not str(item.get("formatted_citation") or "").strip():
            item["formatted_citation"] = format_citation_entry(item, style, idx)
        out.append(item)
    return out


def _render_manuscript_md(payload: Dict[str, Any], rendered_md: str, refs: List[Dict[str, Any]], uncited_ids: Optional[set] = None) -> str:
    lines: List[str] = []
    if payload.get("title"):
        lines.extend([f"# {payload['title']}", ""])
    lines.append(rendered_md.rstrip())
    unc = uncited_ids or set()
    body_refs = [r for r in refs if str(r.get("ref_id") or "").lower() not in unc] if unc and len(unc) < len(refs) else refs
    lines.extend(["", "## References (参考文献)", ""])
    for r in body_refs:
        fmt = str(r.get("formatted_citation") or "").strip()
        lines.append(f"- {fmt}")
    if not body_refs:
        lines.append("- *No verified references supplied in input materials.*")
    return "\n".join(lines) + "\n"


def _render_audit_md(
    payload: Dict[str, Any],
    refs: List[Dict[str, Any]],
    ledger: Dict[str, Any],
    verification: Dict[str, Any],
    uncited_ids: Optional[set] = None,
) -> str:
    catalog_count = len(ledger.get("references_catalog") or [])
    excluded = [e for e in (payload.get("excluded_references") or []) if isinstance(e, dict)]
    recency_counts: Dict[str, int] = {"foundational": 0, "representative": 0, "recent": 0}
    for r in refs:
        rr = str(r.get("recency_role") or "representative")
        recency_counts[rr] = recency_counts.get(rr, 0) + 1
    unc = uncited_ids or set()

    lines = [
        f"# Evidence, Citation & Provenance Audit Report (`{payload.get('case_id', '')}`)",
        "",
        "## 1. Citation Coverage & Metadata Verification Ledger",
        f"- **Coverage**: `{len(refs)}` included + `{len(excluded)}` excluded / `{catalog_count}` ledger entries | "
        f"**Recency Breakdown**: foundational=`{recency_counts.get('foundational', 0)}`, "
        f"representative=`{recency_counts.get('representative', 0)}`, recent=`{recency_counts.get('recent', 0)}`",
        "",
        "| Ref ID | Cite Key | Title | Authors | Year | Venue | DOI/ID | Cited? | Status | Source |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in refs:
        rid_l = str(r.get("ref_id") or "").lower()
        cited_flag = "no (audit only)" if rid_l in unc else "yes"
        cells = [
            str(r.get("ref_id") or "-"),
            f"`{r.get('cite_key') or '-'}`",
            str(r.get("title") or "-")[:70],
            str(r.get("authors") or "-"),
            str(r.get("year") or "-"),
            str(r.get("venue") or "-"),
            str(r.get("doi_or_id") or "-"),
            cited_flag,
            f"`{r.get('verification_status') or '-'}`",
            f"`{r.get('source_locator') or '-'}`",
        ]
        lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    if not refs:
        lines.append("| - | - | *Empty literature catalog* | - | - | - | - | - | `unverified_source` | - |")

    if excluded:
        lines.extend(["", "### Excluded References"])
        for ex in excluded:
            lines.append(f"- **{ex.get('ref_id', '-')} (`{ex.get('cite_key', '-')}`)**: {ex.get('reason', 'Excluded without stated reason')}")

    lines.extend(["", "## 2. Claim-to-Citation Mapping (`claim_citation_map`)"])
    for cm in payload.get("claim_citation_map") or []:
        if isinstance(cm, dict):
            lines.append(
                f"- **{cm.get('claim_id', 'C')}** [`{cm.get('section', '')}` | `{cm.get('attribution_type', '')}`]: "
                f"{cm.get('claim_excerpt', '')} -> Citations: `{cm.get('citation_ids', [])}` | Sources: `{cm.get('source_locators', [])}`"
            )

    lines.extend(["", "## 3. Research Gap Derivation Chain"])
    for g in payload.get("research_gaps") or []:
        if isinstance(g, dict):
            lines.append(
                f"- **{g.get('gap_id', 'G')}** (`{g.get('derivation_type', '')}` | `{g.get('attribution', '')}`): "
                f"{g.get('gap_statement', '')} | Cited Refs: `{g.get('supporting_ref_ids', [])}` | "
                f"Evidence Locators: `{g.get('supporting_evidence_locators', [])}` | Addressed by: {g.get('how_this_work_addresses', '')}"
            )

    lines.extend(["", "## 4. Contribution-to-Evidence Alignment Ledger"])
    for c in payload.get("contributions") or []:
        if isinstance(c, dict):
            lines.append(
                f"- **{c.get('contrib_id', 'CT')}** [`{c.get('verification_status', '')}`]: {c.get('claim_text', '')}\n"
                f"  - Scope: {c.get('scope_qualification', '')}\n"
                f"  - Methods: `{c.get('linked_methods', [])}` | Figs/Tables: `{c.get('linked_figures_tables', [])}` | Metrics: `{c.get('linked_metrics', [])}` | Locators: `{c.get('own_evidence_locators', [])}`"
            )

    lines.extend(["", "## 5. Cross-Source Conflicts & Revision Change Records"])
    for cf in payload.get("conflicts") or []:
        if isinstance(cf, dict):
            lines.append(f"- **{cf.get('conflict_id')}**: {cf.get('item')} -> {cf.get('rationale')} (Adopted: `{cf.get('adopted_value_or_status')}`)")
            for sc in cf.get("sources_compared") or []:
                if isinstance(sc, dict):
                    lines.append(f"  - Source `{sc.get('source_locator', '-')}` (`{sc.get('source_tier', '-')}`): `{sc.get('value', '')}`")
    for ch in payload.get("change_records") or []:
        if isinstance(ch, dict):
            lines.append(f"- **{ch.get('change_id')}** [`{ch.get('target')}` / `{ch.get('action')}`]: `{ch.get('before')}` -> `{ch.get('after')}` (Basis: `{ch.get('basis_locator')}`)")

    lines.extend(["", "## 6. Author Confirmation Questionnaire (Priority-Grouped)"])
    confirm_items = [q for q in (payload.get("author_confirmation_items") or []) if isinstance(q, dict)]
    sev_order = {"critical": 0, "important": 1, "recommended": 2}
    confirm_items.sort(key=lambda q: sev_order.get(str(q.get("severity") or "important").lower(), 1))
    for q in confirm_items:
        lines.append(
            f"- **[{q.get('gap_id', 'GAP')}]** (`{str(q.get('severity', 'important')).upper()}` | `{q.get('location_in_draft', '')}`): "
            f"{q.get('question', '')} *(Checked: {q.get('what_was_checked', '')})*"
        )

    if verification:
        lines.extend(
            [
                "",
                "## 7. Mechanical Verification Summary",
                f"- **Passed**: `{verification.get('passed')}` | **Errors**: `{verification.get('error_count')}` | **Warnings**: `{verification.get('warning_count')}`",
                f"- **Metrics**: `{json.dumps(verification.get('metrics') or {}, ensure_ascii=False)}`",
            ]
        )
    return "\n".join(lines) + "\n"


_UNICODE_MATH_TEX = {
    "α": r"$\alpha$", "β": r"$\beta$", "γ": r"$\gamma$", "δ": r"$\delta$", "ε": r"$\epsilon$",
    "λ": r"$\lambda$", "μ": r"$\mu$", "σ": r"$\sigma$", "Δ": r"$\Delta$", "Ω": r"$\Omega$",
    "≤": r"$\le$", "≥": r"$\ge$", "±": r"$\pm$", "×": r"$\times$", "→": r"$\to$", "−": "-",
    "°": r"$^\circ$", "≈": r"$\approx$", "≠": r"$\neq$",
}


def _latex_escape(txt: str, ascii_unicode_safe: bool = False) -> str:
    bs_tok = "ZZZBACKSLASHZZZ"
    out = txt.replace("\\", bs_tok)
    if ascii_unicode_safe:
        uni_holds: Dict[str, str] = {}
        for uch, tex_rep in _UNICODE_MATH_TEX.items():
            if uch in out:
                hk = f"ZZZUNIMATH{len(uni_holds)}ZZZ"
                uni_holds[hk] = tex_rep
                out = out.replace(uch, hk)
    else:
        uni_holds = {}
    for k, v in (("&", r"\&"), ("%", r"\%"), ("$", r"\$"), ("#", r"\#"), ("_", r"\_"), ("{", r"\{"), ("}", r"\}"), ("~", r"\textasciitilde{}"), ("^", r"\textasciicircum{}")):
        out = out.replace(k, v)
    out = out.replace(bs_tok, r"\textbackslash{}")
    for hk, tex_rep in uni_holds.items():
        out = out.replace(hk, tex_rep)
    return out


def _bibtex_escape(txt: str) -> str:
    out = str(txt or "")
    out = re.sub(r"(?<!\\)&", r"\&", out)
    out = re.sub(r"(?<!\\)%", r"\%", out)
    return out


def _infer_bib_type(ref: Dict[str, Any]) -> Tuple[str, str]:
    raw_t = str(ref.get("entry_type") or "").strip().lower()
    venue_l = str(ref.get("venue") or "").lower()
    if raw_t in ("inproceedings", "conference", "proceedings", "conf") or any(
        w in venue_l for w in ("proceedings", "conference", "workshop", "symposium", "icml", "neurips", "cvpr", "iccv", "acl", "emnlp", "iclr", "aaai")
    ):
        return "inproceedings", "booktitle"
    if raw_t in ("book", "inbook"):
        return raw_t, "publisher"
    if raw_t in ("phdthesis", "mastersthesis"):
        return raw_t, "school"
    if raw_t == "misc":
        return "misc", "howpublished"
    return "article", "journal"


def _markdown_to_latex_body(
    md: str,
    ref_by_id: Dict[str, str],
    ref_idx_by_id: Dict[str, int],
    use_cite_cmd: bool,
    ascii_unicode_safe: bool = False,
) -> str:
    """Convert structured Markdown prose into Python 3.9+ safe LaTeX body preserving citations, math, lists, and tables."""
    lines_out: List[str] = []
    list_env: Optional[str] = None
    in_disp_math = False
    cite_cmd_re = re.compile(r"\\cite[a-z]*\*?(?:\[[^\]]*\])*\{[^}]+\}")
    math_inline_re = re.compile(r"(?<!\\)\$([^$\n]{1,400})(?<!\\)\$")
    valid_toks = set(ref_by_id.keys()) | set(ref_idx_by_id.keys())

    def close_list() -> None:
        nonlocal list_env
        if list_env:
            lines_out.append(f"\\end{{{list_env}}}")
            list_env = None

    def convert_inline(s: str) -> str:
        saved: Dict[str, str] = {}

        def hold(tok: str) -> str:
            key = f"@@CITEHOLD{len(saved)}@@"
            saved[key] = tok
            return key

        tmp = cite_cmd_re.sub(lambda m: hold(m.group(0)), s)
        tmp = math_inline_re.sub(lambda m: hold(m.group(0)), tmp)

        def sub_bracket_cites(m: re.Match[str]) -> str:
            inner = m.group(1).strip()
            if any(t in inner for t in ("待作者确认", "AUTHOR_CONFIRM", "作者判断", "AUTHOR_JUDGMENT", ":")):
                return m.group(0)
            rm = re.match(r"^R(\d{1,3})\s*[-–]\s*R(\d{1,3})$", inner)
            if rm:
                a, b = int(rm.group(1)), int(rm.group(2))
                rids = [f"R{k:02d}" for k in range(a, min(b + 1, a + 21))]
            else:
                parts = [p.strip() for p in re.split(r"[,;]", inner) if p.strip()]
                if not parts or not all(re.match(r"^R\d{1,3}$", p) or p.lower() in valid_toks for p in parts):
                    return m.group(0)
                rids = parts
            if use_cite_cmd:
                keys = [ref_by_id.get(r.lower(), r) for r in rids]
                return hold(f"\\cite{{{', '.join(keys)}}}")
            nums = [str(ref_idx_by_id.get(r.lower(), r)) for r in rids]
            return f"[{', '.join(nums)}]"

        tmp = re.sub(r"\[([^\[\]]+)\]", sub_bracket_cites, tmp)
        spans: Dict[str, str] = {}

        def hold_fmt(tex_cmd: str, inner: str) -> str:
            key = f"@@FMTHOLD{len(spans)}@@"
            spans[key] = f"\\{tex_cmd}{{{_latex_escape(inner, ascii_unicode_safe=ascii_unicode_safe)}}}"
            return key

        tmp = re.sub(r"\*\*(.+?)\*\*", lambda m: hold_fmt("textbf", m.group(1)), tmp)
        tmp = re.sub(r"\*(.+?)\*", lambda m: hold_fmt("emph", m.group(1)), tmp)
        tmp = re.sub(r"`([^`]+)`", lambda m: hold_fmt("texttt", m.group(1)), tmp)
        esc = _latex_escape(tmp, ascii_unicode_safe=ascii_unicode_safe)
        for k, v in spans.items():
            esc = esc.replace(k, v)
        for k, v in saved.items():
            esc = esc.replace(k, v)
        return esc

    raw_lines = (md or "").splitlines()
    idx_ln = 0
    num_re = re.compile(r"^\d+(?:\.\d+)*\.?\s*")
    bullet_re = re.compile(r"^[-*]\s+")
    enum_re = re.compile(r"^\d+\.\s+")
    while idx_ln < len(raw_lines):
        s = raw_lines[idx_ln].strip()
        if s == "$$" or (s.startswith("$$") and s.endswith("$$") and len(s) > 4):
            close_list()
            if s.startswith("$$") and s.endswith("$$") and len(s) > 4:
                lines_out.append(r"\[" + s[2:-2].strip() + r"\]")
            else:
                lines_out.append(r"\[" if not in_disp_math else r"\]")
                in_disp_math = not in_disp_math
            idx_ln += 1
            continue
        if in_disp_math:
            lines_out.append(s)
            idx_ln += 1
            continue
        if not s:
            close_list()
            lines_out.append("")
            idx_ln += 1
            continue
        if s.startswith("#### "):
            close_list()
            hdr = num_re.sub("", s[5:].strip())
            lines_out.append("\\paragraph{" + convert_inline(hdr) + "}")
        elif s.startswith("### "):
            close_list()
            hdr = num_re.sub("", s[4:].strip())
            lines_out.append("\\subsection{" + convert_inline(hdr) + "}")
        elif s.startswith("## "):
            close_list()
            hdr = num_re.sub("", s[3:].strip())
            lines_out.append("\\section{" + convert_inline(hdr) + "}")
        elif s.startswith("# "):
            close_list()
            lines_out.append("\\section*{" + convert_inline(s[2:].strip()) + "}")
        elif bullet_re.match(s):
            if list_env != "itemize":
                close_list()
                lines_out.append(r"\begin{itemize}")
                list_env = "itemize"
            lines_out.append("  \\item " + convert_inline(bullet_re.sub("", s)))
        elif enum_re.match(s):
            if list_env != "enumerate":
                close_list()
                lines_out.append(r"\begin{enumerate}")
                list_env = "enumerate"
            lines_out.append("  \\item " + convert_inline(enum_re.sub("", s)))
        elif s.startswith("|") and s.endswith("|") and idx_ln + 1 < len(raw_lines) and re.match(r"^\|[\s:|-]+\|$", raw_lines[idx_ln + 1].strip()):
            close_list()
            tbl_rows: List[List[str]] = []
            while idx_ln < len(raw_lines) and raw_lines[idx_ln].strip().startswith("|"):
                row_s = raw_lines[idx_ln].strip()
                if not re.match(r"^\|[\s:|-]+\|$", row_s):
                    tbl_rows.append([c.strip() for c in row_s.strip("|").split("|")])
                idx_ln += 1
            if tbl_rows:
                ncols = max(len(r) for r in tbl_rows)
                lines_out.append("\\begin{center}\n\\begin{tabular}{" + ("l" * ncols) + "}\n\\toprule")
                for ri, row in enumerate(tbl_rows):
                    padded = row + [""] * (ncols - len(row))
                    lines_out.append(" & ".join(convert_inline(c) for c in padded) + r" \\")
                    if ri == 0:
                        lines_out.append(r"\midrule")
                lines_out.append("\\bottomrule\n\\end{tabular}\n\\end{center}")
            continue
        else:
            close_list()
            lines_out.append(convert_inline(s))
        idx_ln += 1
    close_list()
    return "\n".join(lines_out)


def _render_pdf_png_previews(pdf_path: Path, out_dir: Path, prefix: str) -> List[str]:
    pngs: List[str] = []
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        return pngs
    try:
        try:
            import pymupdf as fitz  # type: ignore
        except ImportError:
            import fitz  # type: ignore
        doc = fitz.open(str(pdf_path))
        for idx in range(min(3, len(doc))):
            p_out = out_dir / f"{prefix}_preview_p{idx + 1}.png"
            doc[idx].get_pixmap(dpi=150).save(str(p_out))
            pngs.append(p_out.name)
        doc.close()
        if pngs:
            return pngs
    except Exception:
        pass
    if shutil.which("pdftoppm"):
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", "3", str(pdf_path), str(out_dir / f"{prefix}_preview")],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )
            for png in sorted(out_dir.glob(f"{prefix}_preview*.png")):
                pngs.append(png.name)
        except (subprocess.TimeoutExpired, OSError):
            pass
    return pngs


def _maybe_export_requested(
    payload: Dict[str, Any],
    manuscript_raw: str,
    rendered_md: str,
    refs_list: List[Dict[str, Any]],
    requested_formats: List[str],
    out_dir: Path,
) -> Tuple[List[str], List[str]]:
    extra_files: List[str] = []
    notes: List[str] = []
    ref_by_id: Dict[str, str] = {}
    ref_idx_by_id: Dict[str, int] = {}
    for i, r in enumerate(refs_list, start=1):
        ck_v = str(r.get("cite_key") or r.get("ref_id") or f"ref{i}")
        rid_l = str(r.get("ref_id") or "").lower()
        ck_l = str(r.get("cite_key") or "").lower()
        if rid_l:
            ref_by_id[rid_l] = ck_v
            ref_idx_by_id[rid_l] = i
        if ck_l:
            ref_by_id[ck_l] = ck_v
            ref_idx_by_id[ck_l] = i
    both_tex_and_docx = ("tex" in requested_formats) and ("docx" in requested_formats)

    if "bib" in requested_formats or "tex" in requested_formats:
        bib_path = out_dir / "references.bib"
        bib_entries: List[str] = []
        for idx, r in enumerate(refs_list, start=1):
            ck = str(r.get("cite_key") or f"ref{idx}")
            btype, venue_field = _infer_bib_type(r)
            field_lines: List[str] = []
            if r.get("authors"):
                field_lines.append(f"  author = {{{_bibtex_escape(r['authors'])}}}")
            if r.get("title"):
                field_lines.append(f"  title = {{{{{_bibtex_escape(r['title'])}}}}}")
            if r.get("venue"):
                field_lines.append(f"  {venue_field} = {{{_bibtex_escape(r['venue'])}}}")
            yr = str(r.get("year") or "").strip()
            if yr and re.match(r"^(19|20)\d{2}$", yr):
                field_lines.append(f"  year = {{{yr}}}")
            if r.get("volume"):
                field_lines.append(f"  volume = {{{_bibtex_escape(r['volume'])}}}")
            if r.get("pages"):
                field_lines.append(f"  pages = {{{_bibtex_escape(r['pages'])}}}")
            doi_val = str(r.get("doi_or_id") or "").strip()
            if doi_val.startswith("10."):
                field_lines.append(f"  doi = {{{doi_val}}}")
            elif doi_val:
                field_lines.append(f"  note = {{{_bibtex_escape(doi_val)}}}")
            if not field_lines:
                field_lines.append("  note = {Incomplete metadata; requires author confirmation}")
            bib_entries.append(f"@{btype}{{{ck},\n" + ",\n".join(field_lines) + "\n}")
        bib_path.write_text("\n\n".join(bib_entries) + "\n", encoding="utf-8", newline="\n")
        extra_files.append("references.bib")

    if "tex" in requested_formats:
        tex_path = out_dir / "introduction_and_related_work.tex"
        title_raw = str(payload.get("title") or "Introduction and Related Work")
        matrix = [m for m in (payload.get("comparison_matrix") or []) if isinstance(m, dict)]
        full_tex_blob = manuscript_raw + title_raw + json.dumps(matrix, ensure_ascii=False) + json.dumps(refs_list, ensure_ascii=False)
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", full_tex_blob))
        compiler = (shutil.which("xelatex") or shutil.which("pdflatex")) if has_cjk else (shutil.which("pdflatex") or shutil.which("xelatex"))
        ascii_safe = (compiler is not None and Path(compiler).name.lower().startswith("pdflatex")) or (not has_cjk and compiler is None)
        use_cite_cmd = str(payload.get("citation_style") or "") == "bibtex_latex" or "\\cite" in manuscript_raw
        body_tex = _markdown_to_latex_body(manuscript_raw, ref_by_id, ref_idx_by_id, use_cite_cmd=use_cite_cmd, ascii_unicode_safe=ascii_safe)

        table_tex = ""
        if matrix:
            trows = []
            for m in matrix:
                trows.append(
                    " & ".join(
                        _latex_escape(str(m.get(col) or "")[:90], ascii_unicode_safe=ascii_safe)
                        for col in ("ref_id", "route", "method_route", "key_result", "limitations")
                    )
                    + r" \\"
                )
            table_tex = (
                "\n\\begin{table*}[htbp]\n\\centering\n\\small\n"
                "\\caption{Structured Related Work Comparison Matrix}\n"
                "\\begin{tabular}{p{1.2cm}p{2.4cm}p{2.8cm}p{3.0cm}p{3.8cm}}\n\\toprule\n"
                "Ref & Route & Method & Key Result & Limitations \\\\\n\\midrule\n"
                + "\n".join(trows)
                + "\n\\bottomrule\n\\end{tabular}\n\\end{table*}\n"
            )

        bibitems = []
        for idx, r in enumerate(refs_list, start=1):
            ck = str(r.get("cite_key") or f"ref{idx}")
            fmt = str(r.get("formatted_citation") or f"{r.get('authors', '')} ({r.get('year', '')}). {r.get('title', '')}. {r.get('venue', '')}.").strip()
            # Avoid duplicate [1] [1] in LaTeX numeric bibliography (\bibitem already renders [idx])
            fmt_clean = re.sub(r"^\s*(?:\[\d+\]|\d+\.)\s+", "", fmt)
            bibitems.append(f"\\bibitem{{{ck}}} {_latex_escape(fmt_clean, ascii_unicode_safe=ascii_safe)}")
        bib_block = (
            "\n\\begin{thebibliography}{99}\n" + "\n".join(bibitems) + "\n\\end{thebibliography}\n"
            if bibitems
            else ""
        )

        if has_cjk:
            doc_cls = "\\documentclass[UTF8]{ctexart}\n"
        else:
            doc_cls = "\\documentclass[11pt]{article}\n\\usepackage[utf8]{inputenc}\n\\usepackage[T1]{fontenc}\n"
        tex_src = (
            doc_cls
            + "\\usepackage[margin=1in]{geometry}\n"
            + "\\usepackage{booktabs}\n"
            + f"\\title{{{_latex_escape(title_raw, ascii_unicode_safe=ascii_safe)}}}\n"
            + "\\date{}\n"
            + "\\begin{document}\n"
            + "\\maketitle\n\n"
            + body_tex
            + "\n"
            + table_tex
            + bib_block
            + "\\end{document}\n"
        )
        tex_path.write_text(tex_src, encoding="utf-8", newline="\n")
        extra_files.append("introduction_and_related_work.tex")
        if compiler:
            compile_ok = False
            last_rc = -1
            # Compile inside isolated temp build dir so .aux/.log/.out never pollute /app/output/
            with tempfile.TemporaryDirectory() as tex_tmp:
                tmp_dir = Path(tex_tmp)
                try:
                    for _pass in range(2):
                        proc = subprocess.run(
                            [compiler, "-no-shell-escape", "-interaction=nonstopmode", "-output-directory", str(tmp_dir), str(tex_path)],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            stdin=subprocess.DEVNULL,
                            timeout=45,
                        )
                        last_rc = proc.returncode
                        if proc.returncode != 0:
                            break
                    pdf_raw = tmp_dir / "introduction_and_related_work.pdf"
                    if last_rc == 0 and pdf_raw.exists() and pdf_raw.stat().st_size > 0:
                        compile_ok = True
                        final_tex_pdf = out_dir / ("introduction_and_related_work_tex.pdf" if both_tex_and_docx else "introduction_and_related_work.pdf")
                        shutil.copy2(str(pdf_raw), str(final_tex_pdf))
                        extra_files.append(final_tex_pdf.name)
                        extra_files.extend(_render_pdf_png_previews(final_tex_pdf, out_dir, "tex"))
                except subprocess.TimeoutExpired:
                    notes.append("latex_compile_timeout:45s")
                except OSError as exc:
                    notes.append(f"latex_compile_oserror:{exc}")
            if not compile_ok and not any(n.startswith("latex_compile_") for n in notes):
                notes.append(f"latex_compile_failed:{last_rc}")
        else:
            notes.append("latex_compiler_not_installed:exported_structured_tex_and_bib")

    if "docx" in requested_formats:
        docx_path = out_dir / "introduction_and_related_work.docx"
        try:
            import docx  # type: ignore

            doc = docx.Document()
            doc.add_heading(payload.get("title") or "Introduction and Related Work", level=1)
            fmt_split = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)")

            def _add_inline_runs(par: Any, txt: str) -> None:
                for part in fmt_split.split(txt):
                    if part.startswith("**") and part.endswith("**"):
                        r = par.add_run(part[2:-2])
                        r.bold = True
                    elif part.startswith("*") and part.endswith("*"):
                        r = par.add_run(part[1:-1])
                        r.italic = True
                    elif part.startswith("`") and part.endswith("`"):
                        r = par.add_run(part[1:-1])
                        r.font.name = "Consolas"
                    elif part:
                        par.add_run(part)

            md_lns = rendered_md.splitlines()
            li = 0
            while li < len(md_lns):
                s = md_lns[li].strip()
                if not s:
                    li += 1
                    continue
                if s.startswith("### "):
                    doc.add_heading(s[4:], level=3)
                elif s.startswith("## "):
                    doc.add_heading(s[3:], level=2)
                elif s.startswith("# "):
                    doc.add_heading(s[2:], level=1)
                elif re.match(r"^[-*]\s+", s):
                    p = doc.add_paragraph(style="List Bullet")
                    _add_inline_runs(p, re.sub(r"^[-*]\s+", "", s))
                elif s.startswith("|") and s.endswith("|") and li + 1 < len(md_lns) and re.match(r"^\|[\s:|-]+\|$", md_lns[li + 1].strip()):
                    trows_md: List[List[str]] = []
                    while li < len(md_lns) and md_lns[li].strip().startswith("|"):
                        rs = md_lns[li].strip()
                        if not re.match(r"^\|[\s:|-]+\|$", rs):
                            trows_md.append([c.strip() for c in rs.strip("|").split("|")])
                        li += 1
                    if trows_md:
                        nc = max(len(r) for r in trows_md)
                        dtbl = doc.add_table(rows=len(trows_md), cols=nc)
                        for r_i, row_vals in enumerate(trows_md):
                            for c_i, val in enumerate(row_vals):
                                dtbl.rows[r_i].cells[c_i].text = val
                    continue
                else:
                    p = doc.add_paragraph()
                    _add_inline_runs(p, s)
                li += 1
            matrix = [m for m in (payload.get("comparison_matrix") or []) if isinstance(m, dict)]
            if matrix:
                doc.add_heading("Related Work Comparison Matrix", level=2)
                cols = ["ref_id", "title", "route", "method_route", "key_result", "applicability_conditions", "limitations"]
                tbl = doc.add_table(rows=1, cols=len(cols))
                hdr = tbl.rows[0].cells
                for ci, col in enumerate(cols):
                    hdr[ci].text = col
                for m in matrix:
                    row_cells = tbl.add_row().cells
                    for ci, col in enumerate(cols):
                        row_cells[ci].text = str(m.get(col) or "")
            if refs_list:
                doc.add_heading("References", level=2)
                for r in refs_list:
                    doc.add_paragraph(str(r.get("formatted_citation") or ""))
            doc.save(str(docx_path))
            extra_files.append("introduction_and_related_work.docx")
            soffice = shutil.which("soffice") or shutil.which("libreoffice")
            if soffice:
                with tempfile.TemporaryDirectory() as lo_tmp:
                    lo_profile = Path(lo_tmp).as_uri()
                    try:
                        subprocess.run(
                            [soffice, f"-env:UserInstallation={lo_profile}", "--headless", "--convert-to", "pdf", "--outdir", str(lo_tmp), str(docx_path)],
                            capture_output=True,
                            stdin=subprocess.DEVNULL,
                            timeout=45,
                        )
                        pdf_p = Path(lo_tmp) / "introduction_and_related_work.pdf"
                        if pdf_p.exists() and pdf_p.stat().st_size > 0:
                            final_docx_pdf = out_dir / ("introduction_and_related_work_docx.pdf" if both_tex_and_docx else "introduction_and_related_work.pdf")
                            shutil.copy2(str(pdf_p), str(final_docx_pdf))
                            extra_files.append(final_docx_pdf.name)
                            extra_files.extend(_render_pdf_png_previews(final_docx_pdf, out_dir, "docx"))
                    except subprocess.TimeoutExpired:
                        notes.append("soffice_convert_timeout:45s")
                    except OSError as exc:
                        notes.append(f"soffice_convert_error:{exc}")
            else:
                notes.append("soffice_not_installed:docx_written_without_pdf_preview")
        except ImportError:
            notes.append("python_docx_not_installed:markdown_deliverables_written")
        except Exception as exc:
            notes.append(f"docx_export_error:{exc}")

    if "diagram" in requested_formats:
        try:
            import matplotlib  # type: ignore

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt  # type: ignore
            from matplotlib import font_manager  # type: ignore
            from matplotlib.patches import FancyBboxPatch  # type: ignore

            avail_fonts = {f.name for f in font_manager.fontManager.ttflist}
            cjk_prefs = ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "Microsoft YaHei", "SimHei", "PingFang SC", "Arial Unicode MS", "DejaVu Sans"]
            chosen_fonts = [fn for fn in cjk_prefs if fn in avail_fonts]
            if chosen_fonts:
                plt.rcParams["font.sans-serif"] = chosen_fonts + ["DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False

            groups = payload.get("related_work_groups") or []
            gaps = payload.get("research_gaps") or []
            contribs = payload.get("contributions") or []
            fig, ax = plt.subplots(figsize=(10.5, 5.8), dpi=150)
            ax.set_xlim(0, 10)
            ax.set_ylim(0, 6)
            ax.axis("off")
            ax.text(5, 5.65, f"Related Work -> Research Gaps -> Contributions ({payload.get('case_id', '')})", ha="center", va="center", fontsize=11, fontweight="bold")

            def draw_col(x: float, title: str, items: List[str], color: str) -> None:
                ax.text(x + 1.4, 5.1, title, ha="center", va="center", fontsize=10, fontweight="bold")
                shown = items[:4]
                if len(items) > 4:
                    shown[-1] = f"{shown[-1][:45]} (+{len(items) - 3} more)"
                for i, it in enumerate(shown):
                    y = 4.1 - i * 1.05
                    box = FancyBboxPatch((x, y), 2.8, 0.85, boxstyle="round,pad=0.06", facecolor=color, edgecolor="#334155", linewidth=1.0)
                    ax.add_patch(box)
                    wrapped = "\n".join(textwrap.wrap(it, width=26)[:3])
                    ax.text(x + 1.4, y + 0.42, wrapped, ha="center", va="center", fontsize=7.5)

            g_labels = [f"{g.get('group_id', '')}: {g.get('route_name', '')}" for g in groups] or ["No routes"]
            gap_labels = [f"{g.get('gap_id', '')}: {g.get('gap_statement', '')}" for g in gaps] or ["Pending gaps"]
            c_labels = [f"{c.get('contrib_id', '')}: {c.get('claim_text', '')}" for c in contribs] or ["Pending contribs"]
            draw_col(0.3, "Methodological Routes", g_labels, "#E0F2FE")
            draw_col(3.6, "Derived Research Gaps", gap_labels, "#FEF3C7")
            draw_col(6.9, "Aligned Contributions", c_labels, "#DCFCE7")
            ax.annotate("", xy=(3.55, 3.6), xytext=(3.15, 3.6), arrowprops=dict(arrowstyle="->", lw=1.5))
            ax.annotate("", xy=(6.85, 3.6), xytext=(6.45, 3.6), arrowprops=dict(arrowstyle="->", lw=1.5))

            png_p = out_dir / "related_work_taxonomy.png"
            svg_p = out_dir / "related_work_taxonomy.svg"
            fig.savefig(png_p, bbox_inches="tight")
            fig.savefig(svg_p, bbox_inches="tight")
            plt.close(fig)
            extra_files.extend(["related_work_taxonomy.png", "related_work_taxonomy.svg"])
        except Exception as exc:
            notes.append(f"diagram_export_skipped:{exc}")

    return extra_files, notes


def _coerce_type(val: Any, expected_type: str, role: str = "") -> Any:
    et = (expected_type or "").lower()
    if et in ("string", "str"):
        if isinstance(val, str):
            return val
        if role == "comparison_matrix" and isinstance(val, list):
            return _render_matrix_md(val)
        if role == "related_work_groups" and isinstance(val, list):
            lines = []
            for g in val:
                if isinstance(g, dict):
                    lines.append(f"### {g.get('route_name', g.get('group_id', 'Group'))}\n{g.get('route_tradeoff_summary', '')}")
            return "\n\n".join(lines) if lines else json.dumps(val, ensure_ascii=False)
        if isinstance(val, list) and all(isinstance(x, str) for x in val):
            return "\n".join(f"- {x}" for x in val)
        return json.dumps(val, ensure_ascii=False)
    if et in ("array", "list"):
        if isinstance(val, list):
            return val
        if val is None or val == "":
            return []
        return [val]
    if et in ("object", "dict"):
        if isinstance(val, dict):
            return val
        if val is None or val == "":
            return {}
        return {"value": val}
    if et in ("boolean", "bool"):
        return bool(val)
    if et in ("integer", "int"):
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0
    if et in ("number", "float"):
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0
    return val


def build_partial_payload(plan: Dict[str, Any], ledger: Dict[str, Any]) -> Dict[str, Any]:
    cid = plan.get("case_id", "")
    sigs = plan.get("signals") or {}
    refs_out = []
    for r in ledger.get("references_catalog") or []:
        refs_out.append(
            {
                "ref_id": r.get("ref_id"),
                "cite_key": r.get("cite_key"),
                "entry_type": r.get("entry_type") or "article",
                "authors": r.get("authors") or "[待补充作者]",
                "year": r.get("year") or "[待补充年份]",
                "title": r.get("title") or "[待补充标题]",
                "venue": r.get("venue") or "[待补充来源]",
                "doi_or_id": r.get("doi_or_id") or "",
                "recency_role": r.get("recency_role", "representative"),
                "source_locator": (r.get("source_locators") or ["-"])[0],
                "verification_status": "partial_metadata" if r.get("missing_metadata_fields") else "verified",
                "formatted_citation": "",
            }
        )
    gaps_confirm = [
        {
            "gap_id": g.get("gap_id"),
            "severity": "important",
            "location_in_draft": "global",
            "issue_type": g.get("issue"),
            "question": f"Please clarify or supply material for: {g.get('detail')}",
            "what_was_checked": "Automated ingestion of /input/",
        }
        for g in (ledger.get("pre_gaps") or [])
    ]
    return {
        "case_id": cid,
        "status": "insufficient_material" if not refs_out and not (ledger.get("own_paper_evidence") or []) else "partial",
        "language": sigs.get("language", "zh"),
        "citation_style": sigs.get("citation_style", "unspecified"),
        "title": f"Partial Checkpoint ({cid})",
        "manuscript_markdown": f"[Partial checkpoint saved before drafting. Discovered {len(refs_out)} candidate references and {len(ledger.get('own_paper_evidence') or [])} own-paper snippets.]",
        "research_background": {"domain_lineage": "", "concrete_problem": "", "technical_constraints": [], "why_now": "", "evidence_locators": [], "citation_ids": []},
        "related_work_groups": [],
        "comparison_matrix": [],
        "research_gaps": [],
        "contributions": [],
        "claim_citation_map": [],
        "references": refs_out,
        "excluded_references": [],
        "conflicts": ledger.get("conflicts") or [],
        "author_confirmation_items": gaps_confirm,
        "change_records": [],
        "uncertainty": [w for w in (ledger.get("access_warnings") or [])] + [g.get("detail", "") for g in (ledger.get("pre_gaps") or [])],
    }


def _build_result_json(
    plan: Dict[str, Any],
    payload: Dict[str, Any],
    rendered_md: str,
    matrix: List[Dict[str, Any]],
    refs_filled: List[Dict[str, Any]],
    sources_list: List[Dict[str, Any]],
    deliverables: List[str],
    uncertainty: List[str],
    verification: Dict[str, Any],
    checkpoint: str,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "case_id": plan.get("case_id"),
        "status": payload.get("status", "partial"),
        "checkpoint": checkpoint,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manuscript_markdown": rendered_md,
        "research_background": payload.get("research_background", {}),
        "related_work_groups": payload.get("related_work_groups", []),
        "comparison_matrix": matrix,
        "research_gaps": payload.get("research_gaps", []),
        "contributions": payload.get("contributions", []),
        "references": refs_filled,
        "excluded_references": payload.get("excluded_references", []),
        "claim_citation_map": payload.get("claim_citation_map", []),
        "sources": sources_list,
        "conflicts": payload.get("conflicts", []),
        "author_confirmation_items": payload.get("author_confirmation_items", []),
        "change_records": payload.get("change_records", []),
        "uncertainty": uncertainty,
        "deliverable_files": deliverables,
        "verification_summary": verification or {"passed": False, "note": "partial_checkpoint_or_not_verified"},
    }

    role_map = {
        "case_id": result["case_id"],
        "status": result["status"],
        "manuscript_markdown": result["manuscript_markdown"],
        "research_background": result["research_background"],
        "related_work_groups": result["related_work_groups"],
        "comparison_matrix": result["comparison_matrix"],
        "research_gaps": result["research_gaps"],
        "contributions": result["contributions"],
        "references": result["references"],
        "claim_citation_map": result["claim_citation_map"],
        "conflicts": result["conflicts"],
        "author_confirmation_items": result["author_confirmation_items"],
        "change_records": result["change_records"],
        "uncertainty": result["uncertainty"],
        "sources": result["sources"],
        "deliverable_files": result["deliverable_files"],
        "verification_summary": result["verification_summary"],
        "summary": f"Case {result['case_id']} ({result['status']}): {len(result['references'])} refs, {len(result['related_work_groups'])} routes, {len(result['research_gaps'])} gaps, {len(result['contributions'])} contributions.",
    }
    overrides = payload.get("contract_overrides") or {}
    for fspec in (plan.get("output_contract") or {}).get("fields") or []:
        fname = fspec["name"]
        etype = fspec.get("expected_type", "any")
        role = fspec.get("semantic_role", "custom")
        if fname in overrides:
            result[fname] = _coerce_type(overrides[fname], etype, role)
        elif fname == "manuscript_markdown":
            result[fname] = _coerce_type(rendered_md, etype, role)
        elif fname == "references":
            result[fname] = _coerce_type(refs_filled, etype, role)
        elif fname == "comparison_matrix":
            result[fname] = _coerce_type(matrix, etype, role)
        elif fname in payload and fname not in ("contract_overrides", "manuscript_file"):
            result[fname] = _coerce_type(payload[fname], etype, role)
        elif role in role_map:
            result[fname] = _coerce_type(role_map[role], etype, role)
        elif fname not in result:
            result[fname] = _coerce_type("", etype, role)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Assemble final or partial Harbor deliverables under /app/output.")
    ap.add_argument("--case-plan", default="/app/work/case_plan.json")
    ap.add_argument("--ledger", default="/app/work/source_ledger.json")
    ap.add_argument("--payload", default=None)
    ap.add_argument("--manuscript", default=None, help="Optional external markdown manuscript path")
    ap.add_argument("--verification", default=None)
    ap.add_argument("--checkpoint", choices=["partial", "final"], default="final")
    ap.add_argument("--out-dir", default="/app/output")
    args = ap.parse_args()

    plan, p_err = load_json_safe(Path(args.case_plan))
    ledger, l_err = load_json_safe(Path(args.ledger))
    if p_err or not isinstance(plan, dict):
        raise SystemExit(f"Cannot load case_plan: {p_err}")
    if l_err or not isinstance(ledger, dict):
        raise SystemExit(f"Cannot load ledger: {l_err}")

    payload_path: Optional[Path] = None
    if args.checkpoint == "partial" or not args.payload:
        payload = build_partial_payload(plan, ledger)
    else:
        payload_path = Path(args.payload)
        payload, pay_err = load_json_safe(payload_path)
        if pay_err or not isinstance(payload, dict):
            raise SystemExit(f"Cannot load payload: {pay_err}")

    verification: Dict[str, Any] = {}
    if args.verification and Path(args.verification).exists():
        vdata, _ = load_json_safe(Path(args.verification))
        if isinstance(vdata, dict):
            verification = vdata

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    eff_style = _effective_style(payload)
    lang = str(payload.get("language") or (plan.get("signals") or {}).get("language") or "zh")
    manuscript_raw, ms_warns, ms_err = resolve_manuscript_text(payload, payload_path, args.manuscript)
    payload = sync_payload_structures(payload, manuscript_raw)
    if args.checkpoint == "final" and verification and verification.get("passed") is False and payload.get("status") == "completed":
        payload = dict(payload)
        payload["status"] = "partial"
        payload["uncertainty"] = list(payload.get("uncertainty") or []) + ["verification_not_passed:demoted_from_completed_to_partial"]

    ordered_refs, uncited_list = order_refs_by_appearance(manuscript_raw, payload.get("references") or [], eff_style)
    uncited_ids = {str(r.get("ref_id") or "").lower() for r in uncited_list if isinstance(r, dict)}
    refs_filled = _fill_reference_citations(ordered_refs, eff_style)
    rendered_md = render_inline_citations(manuscript_raw, refs_filled, eff_style, language=lang)
    matrix = _enrich_matrix_with_titles(payload.get("comparison_matrix") or [], refs_filled)

    # Uniform sources_list schema across references, background, gaps, claims, matrix, and contributions
    sources_list: List[Dict[str, Any]] = []
    for r in refs_filled:
        loc = str(r.get("source_locator") or "")
        sources_list.append(
            {
                "id": r.get("ref_id"),
                "kind": "reference",
                "cite_key": r.get("cite_key"),
                "locators": [loc] if loc else [],
                "status": r.get("verification_status"),
            }
        )
    bg = payload.get("research_background") or {}
    if isinstance(bg, dict) and bg.get("evidence_locators"):
        sources_list.append(
            {"id": "BG", "kind": "research_background", "cite_key": "", "locators": list(bg.get("evidence_locators") or []), "status": "verified"}
        )
    for g in payload.get("research_gaps") or []:
        if isinstance(g, dict) and g.get("supporting_evidence_locators"):
            sources_list.append(
                {"id": g.get("gap_id"), "kind": "research_gap", "cite_key": "", "locators": list(g.get("supporting_evidence_locators") or []), "status": str(g.get("attribution") or "verified")}
            )
    for cm in payload.get("claim_citation_map") or []:
        if isinstance(cm, dict) and cm.get("source_locators"):
            sources_list.append(
                {"id": cm.get("claim_id"), "kind": "claim_citation", "cite_key": "", "locators": list(cm.get("source_locators") or []), "status": str(cm.get("attribution_type") or "cited_fact")}
            )
    for c in payload.get("contributions") or []:
        if isinstance(c, dict):
            sources_list.append(
                {
                    "id": c.get("contrib_id"),
                    "kind": "contribution",
                    "cite_key": "",
                    "locators": list(c.get("own_evidence_locators") or []),
                    "status": c.get("verification_status"),
                }
            )

    # Write core human-readable deliverables FIRST
    (out_dir / "introduction_and_related_work.md").write_text(_render_manuscript_md(payload, rendered_md, refs_filled, uncited_ids=uncited_ids), encoding="utf-8", newline="\n")
    (out_dir / "related_work_matrix.md").write_text(_render_matrix_md(matrix), encoding="utf-8", newline="\n")
    _write_matrix_csv(matrix, out_dir / "related_work_matrix.csv")
    (out_dir / "evidence_and_citation_audit.md").write_text(_render_audit_md(payload, refs_filled, ledger, verification, uncited_ids=uncited_ids), encoding="utf-8", newline="\n")

    provenance_map = {
        "case_id": plan.get("case_id"),
        "claim_citation_map": payload.get("claim_citation_map") or [],
        "research_gaps": payload.get("research_gaps") or [],
        "contributions": payload.get("contributions") or [],
        "references": refs_filled,
        "excluded_references": payload.get("excluded_references") or [],
        "conflicts": payload.get("conflicts") or [],
        "change_records": payload.get("change_records") or [],
    }
    save_json(out_dir / "provenance_map.json", provenance_map)

    core_deliverables = [
        "result.json",
        "introduction_and_related_work.md",
        "related_work_matrix.md",
        "related_work_matrix.csv",
        "evidence_and_citation_audit.md",
        "provenance_map.json",
    ]

    base_uncertainty = list(payload.get("uncertainty") or []) + list(ms_warns) + ([ms_err] if ms_err else [])
    result = _build_result_json(
        plan, payload, rendered_md, matrix, refs_filled, sources_list, core_deliverables, base_uncertainty, verification, args.checkpoint
    )
    save_json(out_dir / "result.json", result)

    # Skip heavy binary exports on partial checkpoint; run safely on final checkpoint
    extra_files: List[str] = []
    export_notes: List[str] = []
    if args.checkpoint == "final":
        extra_files, export_notes = _maybe_export_requested(
            payload,
            manuscript_raw,
            rendered_md,
            refs_filled,
            (plan.get("signals") or {}).get("requested_formats") or ["md"],
            out_dir,
        )
        if extra_files or export_notes:
            deliverables = core_deliverables + extra_files
            uncertainty = base_uncertainty + export_notes
            result = _build_result_json(
                plan, payload, rendered_md, matrix, refs_filled, sources_list, deliverables, uncertainty, verification, args.checkpoint
            )
            save_json(out_dir / "result.json", result)
    else:
        deliverables = core_deliverables

    print(json.dumps({"status": "ok", "checkpoint": args.checkpoint, "out_dir": str(out_dir), "deliverables": result["deliverable_files"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
