#!/usr/bin/env python3
"""Step 3: Assemble deliverables, run 10-invariant mechanical verification, and emit /app/output/result.json."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    load_json_safe,
    normalize_arxiv,
    normalize_doi,
    normalize_title,
    read_text_safe,
    save_json,
    save_text,
    title_similarity,
)
from prepare_case import REF_HEADER_RE, _SCI_UNIT_PREFIX_RE, expand_numeric_spec, filter_valid_cite_nums

_UNVERIFIED_SPAN_RE = re.compile(
    r"\[(?:待核验引用|未核验引用|UNVERIFIED_CITE|证据不足/待人工核验|INSUFFICIENT_EVIDENCE)[^\]]*\]",
    re.I,
)


def _mask_non_prose_for_verify(body_text: str) -> str:
    """Strip code blocks, inline code, math blocks/spans, and unverified tags before scanning citations."""
    s = re.sub(r"```[\s\S]*?```", " ", body_text)
    s = re.sub(r"`[^`\n]+`", " ", s)
    s = re.sub(r"\$\$[\s\S]*?\$\$", " ", s)
    s = re.sub(r"(?<!\\)\$[^$\n]+\$", " ", s)
    s = _UNVERIFIED_SPAN_RE.sub("", s)
    return s


def _split_body_and_bib(revised_md: str) -> Tuple[str, str]:
    lines = revised_md.splitlines()
    for idx, ln in enumerate(lines):
        if REF_HEADER_RE.match(ln.strip()):
            return "\n".join(lines[:idx]), "\n".join(lines[idx + 1 :])
    return revised_md, ""


def _extract_rendered_numeric_citations(body_text: str) -> List[int]:
    cleaned = _mask_non_prose_for_verify(body_text)
    ordered_nums: List[int] = []
    pat = re.compile(
        r"<sup>\s*(\[?)([0-9,\s\-–—]+)(\]?)\s*</sup>"
        r"|\^\[([0-9,\s\-–—]+)\]"
        r"|(?<![\]\^\[])\[(\d+)\]\s*[-–—]\s*\[(\d+)\](?!\()"
        r"|(?<![\]\^\[])\[(\d+(?:\s*(?:[-–—,~]|,\s*)\s*\d+)*)\](?!\()"
        r"|([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])"
    )
    circled_map = {ch: i + 1 for i, ch in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")}
    for m in pat.finditer(cleaned):
        if m.group(2):
            has_brackets = bool(m.group(1) and m.group(3))
            if not has_brackets and _SCI_UNIT_PREFIX_RE.search(cleaned[max(0, m.start() - 12) : m.start()]):
                continue
            ordered_nums.extend(filter_valid_cite_nums(expand_numeric_spec(m.group(2))))
        elif m.group(4):
            ordered_nums.extend(filter_valid_cite_nums(expand_numeric_spec(m.group(4))))
        elif m.group(5) and m.group(6):
            a_n, b_n = int(m.group(5)), int(m.group(6))
            raw_list = list(range(a_n, b_n + 1)) if a_n <= b_n and (b_n - a_n) <= 100 else [a_n, b_n]
            ordered_nums.extend(filter_valid_cite_nums(raw_list))
        elif m.group(7):
            raw_spec = m.group(7)
            if re.search(r"\b(?:19|20)\d{2}\s*[-–—]\s*\d{2}\s*[-–—]\s*\d{2}\b", raw_spec):
                continue
            ordered_nums.extend(filter_valid_cite_nums(expand_numeric_spec(raw_spec)))
        elif m.group(8):
            ordered_nums.append(circled_map[m.group(8)])
    return ordered_nums


def _check_inverted_ranges(body_text: str) -> List[str]:
    cleaned = _mask_non_prose_for_verify(body_text)
    bad: List[str] = []
    cite_pat = re.compile(
        r"<sup>\s*\[?([0-9,\s\-–—]+)\]?\s*</sup>"
        r"|\^\[([0-9,\s\-–—]+)\]"
        r"|(?<![\]\^\[])\[(\d+)\]\s*[-–—]\s*\[(\d+)\](?!\()"
        r"|(?<![\]\^\[])\[(\d+(?:\s*(?:[-–—,~]|,\s*)\s*\d+)*)\](?!\()"
    )
    for cm in cite_pat.finditer(cleaned):
        if cm.group(3) and cm.group(4):
            a, b = int(cm.group(3)), int(cm.group(4))
            if a >= b and a < 1900 and b < 1900:
                bad.append(f"{a}-{b} in `{cm.group(0)}`")
            continue
        spec = cm.group(1) or cm.group(2) or cm.group(5) or ""
        for rm in re.finditer(r"(\d+)\s*[-–—]\s*(\d+)", spec):
            a, b = int(rm.group(1)), int(rm.group(2))
            if a < 1900 and b < 1900 and a >= b:
                bad.append(f"{a}-{b} in `{cm.group(0)}`")
    return bad


def _cjk_bigrams(s: str) -> Set[str]:
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", s))
    if len(cjk) < 2:
        return set(cjk)
    return {cjk[i : i + 2] for i in range(len(cjk) - 1)}


def _add_rich_runs(paragraph: Any, text: str, tok_split_re: re.Pattern) -> None:
    for part in tok_split_re.split(text):
        if not part:
            continue
        if part.startswith("<sup>") and part.endswith("</sup>"):
            r = paragraph.add_run(part[5:-6])
            r.font.superscript = True
        elif part.startswith("^[") and part.endswith("]"):
            r = paragraph.add_run(part[1:])
            r.font.superscript = True
        elif part.startswith("**") and part.endswith("**"):
            r = paragraph.add_run(part[2:-2])
            r.bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) >= 3:
            r = paragraph.add_run(part[1:-1])
            r.italic = True
        else:
            paragraph.add_run(part)


def _export_docx(revised_md: str, out_docx: Path, title: str) -> Tuple[bool, Optional[str]]:
    try:
        import docx  # type: ignore
    except ImportError:
        return False, "python_docx_not_installed"
    try:
        doc = docx.Document()
        lines = revised_md.splitlines()
        first_ne = next((ln.strip() for ln in lines if ln.strip()), "")
        if not first_ne.startswith("# "):
            doc.add_heading(title or "Revised Manuscript", level=1)
        tok_split_re = re.compile(r"(<sup>.*?</sup>|\^\[[^\]]+\]|\*\*[^*]+\*\*|\*[^*\n]+\*)")
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            if not s:
                i += 1
                continue
            if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|\-]+\|$", lines[i + 1].strip()):
                rows_raw: List[List[str]] = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    rs = lines[i].strip()
                    if not re.match(r"^\|[\s:|\-]+\|$", rs):
                        rows_raw.append([c.strip() for c in rs.strip("|").split("|")])
                    i += 1
                if rows_raw:
                    ncol = max(len(r) for r in rows_raw)
                    tbl = doc.add_table(rows=len(rows_raw), cols=ncol)
                    for ri, row in enumerate(rows_raw):
                        for ci, cell_val in enumerate(row[:ncol]):
                            cell = tbl.rows[ri].cells[ci]
                            cell.text = ""
                            _add_rich_runs(cell.paragraphs[0], cell_val, tok_split_re)
                continue
            if s.startswith("### "):
                doc.add_heading(s[4:], level=3)
            elif s.startswith("## "):
                doc.add_heading(s[3:], level=2)
            elif s.startswith("# "):
                doc.add_heading(s[2:], level=1)
            else:
                p = doc.add_paragraph()
                _add_rich_runs(p, s, tok_split_re)
            i += 1
        doc.save(str(out_docx))
        return True, None
    except Exception as exc:
        return False, f"docx_export_error:{exc}"


def _render_previews(artifact_path: Path, out_dir: Path, prefix: str) -> Tuple[List[str], List[str]]:
    previews: List[str] = []
    notes: List[str] = []
    pdf_path: Optional[Path] = None

    if artifact_path.suffix.lower() == ".docx":
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            try:
                with tempfile.TemporaryDirectory(prefix="lo_") as lo_dir:
                    lo_uri = Path(lo_dir).resolve().as_uri()
                    proc = subprocess.run(
                        [soffice, f"-env:UserInstallation={lo_uri}", "--headless", "--norestore", "--convert-to", "pdf", "--outdir", str(out_dir), str(artifact_path)],
                        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                    )
                cand = out_dir / f"{artifact_path.stem}.pdf"
                if proc.returncode == 0 and cand.exists() and cand.stat().st_size > 0:
                    pdf_path = cand
                    previews.append(cand.name)
                else:
                    notes.append(f"libreoffice_pdf_failed:rc={proc.returncode}")
            except Exception as exc:
                notes.append(f"libreoffice_pdf_error:{exc}")
        else:
            notes.append("preview_unavailable_no_libreoffice_soffice")
    elif artifact_path.suffix.lower() == ".tex":
        tex_bin = shutil.which("xelatex") or shutil.which("pdflatex")
        if tex_bin:
            try:
                cmd = [tex_bin, "-no-shell-escape", "-interaction=nonstopmode", "-output-directory", str(out_dir), str(artifact_path)]
                proc = None
                for _ in range(2):
                    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                cand = out_dir / f"{artifact_path.stem}.pdf"
                if cand.exists() and cand.stat().st_size > 0:
                    pdf_path = cand
                    previews.append(cand.name)
                else:
                    notes.append(f"latex_compile_failed:rc={proc.returncode if proc else -1}")
            except Exception as exc:
                notes.append(f"latex_compile_error:{exc}")
        else:
            notes.append("preview_unavailable_no_latex_compiler")

    if pdf_path and pdf_path.exists():
        rendered = False
        try:
            try:
                import pymupdf as fitz  # type: ignore
            except ImportError:
                import fitz  # type: ignore
            doc = fitz.open(str(pdf_path))
            tot = len(doc)
            idxs = list(dict.fromkeys(list(range(min(3, tot))) + list(range(max(0, tot - 2), tot))))[:6]
            for idx in idxs:
                pix = doc[idx].get_pixmap(dpi=150)
                png_name = f"{prefix}_preview_p{idx + 1}.png"
                pix.save(str(out_dir / png_name))
                previews.append(png_name)
            doc.close()
            rendered = True
        except Exception:
            pass
        if not rendered:
            pdftoppm = shutil.which("pdftoppm")
            if pdftoppm:
                try:
                    subprocess.run([pdftoppm, "-png", "-r", "150", "-f", "1", "-l", "3", str(pdf_path), str(out_dir / f"{prefix}_preview")], stdin=subprocess.DEVNULL, capture_output=True, timeout=25)
                    for png in sorted(out_dir.glob(f"{prefix}_preview*.png")):
                        previews.append(png.name)
                except Exception as exc:
                    notes.append(f"pdftoppm_error:{exc}")
    return previews, notes


def _coerce_contract_type(value: Any, expected_type: str) -> Any:
    et = (expected_type or "any").lower()
    if et in ("string", "str"):
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if et in ("array", "list"):
        return value if isinstance(value, list) else ([value] if value not in (None, "") else [])
    if et in ("object", "dict"):
        return value if isinstance(value, dict) else ({"value": value} if value not in (None, "") else {})
    if et in ("boolean", "bool"):
        return bool(value)
    if et in ("integer", "int"):
        try:
            return int(value)
        except Exception:
            return 0
    if et in ("number", "float"):
        try:
            return float(value)
        except Exception:
            return 0.0
    return value


def _normalize_ref_token(tok: str) -> str:
    s = str(tok or "").strip()
    m = re.match(r"^REF_(\d{1,4})$", s, re.I)
    if m:
        return f"REF_{int(m.group(1)):03d}"
    return s


def _resolve_effective_claim_verifications(
    plan: Dict[str, Any],
    pre_audit: Dict[str, Any],
    synced: Dict[str, Any],
) -> List[Dict[str, Any]]:
    raw_claims = [cv for cv in (plan.get("claim_verifications") or []) if isinstance(cv, dict)]
    if raw_claims:
        out: List[Dict[str, Any]] = []
        for idx, cv in enumerate(raw_claims, start=1):
            c = dict(cv)
            c.setdefault("claim_id", f"CL{idx:03d}")
            c["cited_ref_ids"] = [_normalize_ref_token(r) for r in (cv.get("cited_ref_ids") or [])]
            out.append(c)
        return out

    # Auto-hydrate baseline claim verifications from pre-matched sentence_links when omitted
    out_auto: List[Dict[str, Any]] = []
    seen_pairs = set()
    for lnk in pre_audit.get("sentence_links") or []:
        if not isinstance(lnk, dict):
            continue
        sid = str(lnk.get("sentence_id") or "")
        rid = _normalize_ref_token(str(lnk.get("ref_id") or ""))
        if not (sid and rid) or (sid, rid) in seen_pairs:
            continue
        seen_pairs.add((sid, rid))
        out_auto.append({
            "claim_id": f"CL{len(out_auto) + 1:03d}",
            "sentence_id": sid,
            "cited_ref_ids": [rid],
            "verdict": str(lnk.get("suggested_verdict") or "title_metadata_only_unverified_claim"),
            "evidence_locator": str(lnk.get("evidence_locator") or ""),
            "evidence_quote": str(lnk.get("evidence_quote") or ""),
            "mismatch_detail": "",
            "auto_hydrated": True,
        })
        if len(out_auto) >= 40:
            break
    return out_auto


def _build_audit_report(
    case_plan: Dict[str, Any],
    lib: Dict[str, Any],
    pre_audit: Dict[str, Any],
    plan: Dict[str, Any],
    synced: Dict[str, Any],
    verification: Dict[str, Any],
    uncertainty: List[Dict[str, Any]],
    human_items: List[Dict[str, Any]],
    checkpoint: str,
) -> str:
    recs = synced.get("updated_canonical_library") or lib.get("canonical_records", [])
    effective_claims = _resolve_effective_claim_verifications(plan, pre_audit, synced)
    lines = [
        f"# Citation & Reference Management Audit Report (`{case_plan.get('case_id', '')}`)",
        "",
        f"- **Checkpoint**: `{checkpoint}` | **Style**: `{synced.get('target_style') or case_plan.get('signals', {}).get('target_style')}` | **In-Text Format**: `{synced.get('in_text_format') or case_plan.get('signals', {}).get('in_text_format')}`",
        f"- **Canonical Refs**: {len(recs)} (from {lib.get('stats', {}).get('raw_record_count', 0)} raw; {lib.get('stats', {}).get('duplicates_removed', 0)} auto-merged)",
        f"- **Cited**: {synced.get('stats', {}).get('cited_ref_count', 0)} | **Uncited Library Entries**: {synced.get('stats', {}).get('uncited_library_count', 0)}",
        "",
        "## 1. Deduplication & Anti-False-Merge Decisions (收得净)",
    ]
    for mg in lib.get("merge_groups", []):
        mems = ", ".join(f"`{m['raw_id']}` ({m['source_locator']})" for m in mg.get("member_records", []))
        rules = "; ".join(f"`{r['tier']}`: {r['rule']}" for r in mg.get("pair_rationales", []))
        lines.append(f"- **`{mg['canonical_id']}`**: {mems} -> Primary `{mg['primary_raw_id']}` | {rules}")
    for em in synced.get("extra_tier3_merges", []):
        lines.append(f"- **Tier-3 Merge (`{em['canonical_id']}`)**: absorbed `{em['merged_secondary_ref_id']}` ({em['rationale']})")
    for pn in lib.get("protected_non_duplicates", []):
        lines.append(f"- **Protected Separate**: `{pn['raw_a']}` vs `{pn['raw_b']}` ({pn['rule']})")
    for rc in lib.get("recycled_doi_conflicts", []):
        lines.append(f"- **Recycled DOI Alert (NOT Merged)**: `{rc['raw_a']}` (*{rc['title_a']}*) vs `{rc['raw_b']}` (*{rc['title_b']}*) — `{rc['rule']}`")

    lines.extend([
        "",
        "## 2. Field-Level Source Evidence Ledger (字段级原文证据溯源)",
        "| Ref ID | Field | Chosen Value | Source Locator | Status | Alternates Seen |",
        "|---|---|---|---|---|---|",
    ])
    tracked_fp_fields = ("title", "authors", "year", "venue", "doi", "volume", "issue", "pages", "abstract")
    fp_rows = 0
    for r in recs:
        fp = r.get("field_provenance") or {}
        for fld in tracked_fp_fields:
            meta = fp.get(fld)
            if not isinstance(meta, dict):
                continue
            val_s = meta.get("value", "")
            if isinstance(val_s, list):
                val_s = ", ".join(str(x) for x in val_s)
            val_s = str(val_s)[:55].replace("|", " ").replace("\n", " ")
            loc_s = str(meta.get("source_locator") or "-").replace("|", " ")
            stat_s = str(meta.get("status") or "primary")
            alts = meta.get("alternate_values") or []
            if alts:
                alt_items = []
                for a in alts[:2]:
                    av = a.get("value", "")
                    if isinstance(av, list):
                        av = ", ".join(str(x) for x in av)
                    alt_items.append(f"{str(av)[:32]} @ {a.get('source_locator', '')}")
                alt_s = "; ".join(alt_items).replace("|", " ").replace("\n", " ")
            else:
                alt_s = "-"
            lines.append(f"| `{r.get('ref_id', '')}` | `{fld}` | {val_s} | `{loc_s}` | `{stat_s}` | {alt_s} |")
            fp_rows += 1
    if fp_rows == 0:
        lines.append("| - | - | *No field provenance recorded* | - | - | - |")

    lines.extend(["", "## 3. Renumbering & Cross-Zone Synchronization (编得顺)"])
    rmap = synced.get("renumbering_map", [])
    if rmap:
        lines.extend(["| # | Ref ID | Old # / Keys | New Marker | First Appeared | Title |", "|---|---|---|---|---|---|"])
        for rm in rmap:
            old_s = ", ".join([f"[{n}]" for n in rm.get("old_inline_numbers", [])] + rm.get("old_source_keys", [])) or "-"
            lines.append(f"| {rm['new_number']} | `{rm['ref_id']}` | {old_s} | `{rm['new_in_text_marker']}` | `{rm['first_appeared_sentence']}` ({rm['first_appeared_block_type']}) | {(rm.get('title') or '')[:65].replace('|', ' ')} |")
    uncited = synced.get("uncited_library_entries") or []
    if uncited:
        lines.append("### Uncited Library Entries (Excluded from Bibliography)")
        for u in uncited:
            lines.append(f"- `{u['ref_id']}` ({u.get('year')}): {u.get('title')}")

    lines.extend(["", "## 4. Claim Verification & Clause Placement (挂得对 & 对得上)"])
    for cv in effective_claims:
        det = (cv.get("mismatch_detail") or cv.get("evidence_quote") or "")[:120].replace("|", " ").replace("\n", " ")
        lines.append(f"- **`{cv.get('claim_id', '')}`** (`{cv.get('sentence_id', '')}`, `{','.join(cv.get('cited_ref_ids', []))}`): `{cv.get('verdict', '')}` | `{cv.get('evidence_locator', '')}` | {det}")

    diffs = synced.get("before_after_diffs") or []
    if diffs:
        lines.extend(["", "## 5. Before / After Sentence Diffs"])
        for d in diffs[:40]:
            lines.append(f"- **`{d['sentence_id']}`**:\n  - Before: {d['before']}\n  - After: {d['after']}")

    lines.extend(["", "## 6. Uncertainty & Human Confirmation Items"])
    for u in uncertainty:
        lines.append(f"- **[{u.get('item', '')}]**: {u.get('reason', '')} *(Scope: {u.get('scope', '')})*")
    for hi in human_items:
        lines.append(f"- **[{hi.get('issue_id', '')} | {hi.get('severity', '')}]** `{hi.get('location', '')}`: {hi.get('question', '')}")

    if verification:
        lines.extend(["", "## 7. Mechanical Verification", f"- **Passed**: `{verification.get('passed')}` | **Errors**: `{verification.get('error_count')}` | **Warnings**: `{verification.get('warning_count')}`"])
    return "\n".join(lines) + "\n"


def verify_deliverables(work_dir: Path, out_dir: Path, plan_path: Path) -> Dict[str, Any]:
    case_plan = load_json_safe(work_dir / "case_plan.json", {})
    lib = load_json_safe(work_dir / "library_canonical.json", {})
    ev_data = load_json_safe(work_dir / "evidence_corpus.json", {})
    ms = load_json_safe(work_dir / "manuscript_parsed.json", {})
    pre_audit = load_json_safe(work_dir / "pre_audit.json", {})
    plan = load_json_safe(plan_path, {}) if plan_path.exists() else {}
    synced = load_json_safe(work_dir / "synced" / "sync_result.json", {})

    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    def err(code: str, msg: str) -> None:
        errors.append({"code": code, "message": msg})

    def warn(code: str, msg: str) -> None:
        warnings.append({"code": code, "message": msg})

    result_json = load_json_safe(out_dir / "result.json", {})
    revised_md, _, _ = read_text_safe(out_dir / "revised_manuscript.md") if (out_dir / "revised_manuscript.md").exists() else ("", "", None)
    audit_md, _, _ = read_text_safe(out_dir / "citation_audit_report.md") if (out_dir / "citation_audit_report.md").exists() else ("", "", None)

    # 1. Residual internal tokens
    for fname in ("revised_manuscript.md", "revised_manuscript.tex", "references_formatted.md"):
        p = out_dir / fname
        if p.exists():
            txt, _, _ = read_text_safe(p)
            if "[[CITE" in txt:
                err("residual_internal_token", f"Unrendered [[CITE...]] token found in {fname}")

    # 2 & 3. Bidirectional consistency & sequential numbering continuity
    target_style = synced.get("target_style", "gbt7714_2015_numeric")
    in_text_format = synced.get("in_text_format", "bracket")
    formatted_refs = synced.get("formatted_references", [])
    bib_nums = {fr["assigned_number"] for fr in formatted_refs}
    library_only = len(ms.get("sentences", [])) == 0 and len(formatted_refs) > 0
    body_text, bib_text = _split_body_and_bib(revised_md)

    if library_only:
        if target_style not in ("apa7", "apa6", "gbt7714_2015_author_year", "chicago_author_date") and bib_nums:
            if bib_nums != set(range(1, len(bib_nums) + 1)):
                err("numbering_gap_or_noncontiguous", f"Library-only bibliography numbers {sorted(bib_nums)} != 1..{len(bib_nums)}")
    elif in_text_format == "latex_cite":
        tex_path = out_dir / "revised_manuscript.tex"
        tex_txt, _, _ = read_text_safe(tex_path) if tex_path.exists() else (revised_md, "", None)
        cited_keys: Set[str] = set()
        for m in re.finditer(r"\\(?:cite|citep|citet|parencite|textcite)\{([^}]+)\}", tex_txt):
            for k in m.group(1).split(","):
                if k.strip():
                    cited_keys.add(k.strip())
        bib_keys = set(re.findall(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}", tex_txt))
        if cited_keys - bib_keys:
            err("dangling_latex_cite", f"LaTeX keys missing from thebibliography: {sorted(cited_keys - bib_keys)}")
        if bib_keys - cited_keys:
            err("orphan_latex_bibitem", f"LaTeX bibitem entries never cited: {sorted(bib_keys - cited_keys)}")
    elif target_style in ("apa7", "apa6", "gbt7714_2015_author_year", "chicago_author_date"):
        occ_markers = {o["rendered_marker"] for o in synced.get("in_text_occurrences", [])}
        missing_mk = [mk for mk in occ_markers if mk not in body_text]
        if missing_mk:
            err("missing_author_year_marker", f"Expected author-year markers absent from revised_manuscript.md: {missing_mk}")
        for fr in formatted_refs:
            if fr["formatted_entry"] not in bib_text:
                err("missing_bib_entry_in_manuscript", f"Formatted entry missing from bibliography: {fr['ref_id']}")
    else:
        occ_seq = _extract_rendered_numeric_citations(body_text)
        occ_set = set(occ_seq)
        if occ_set != bib_nums:
            err("numeric_bidirectional_mismatch", f"In-text numbers {sorted(occ_set)} != bibliography numbers {sorted(bib_nums)}")
        if bib_nums and bib_nums != set(range(1, len(bib_nums) + 1)):
            err("numbering_gap_or_noncontiguous", f"Bibliography numbers {sorted(bib_nums)} != 1..{len(bib_nums)}")
        seen_first: List[int] = []
        for n in occ_seq:
            if n not in seen_first:
                seen_first.append(n)
        if seen_first != list(range(1, len(seen_first) + 1)):
            err("non_monotonic_first_appearance", f"First-appearance order {seen_first} != {list(range(1, len(seen_first) + 1))}")
        bad_ranges = _check_inverted_ranges(body_text)
        if bad_ranges:
            err("invalid_citation_range", f"Invalid numeric citation range(s): {bad_ranges}")

    # 4. Formatting cleanliness
    dirty_pats = [
        ("None", re.compile(r"\bNone\b")),
        ("NaN", re.compile(r"\bNaN\b")),
        ("null", re.compile(r"\bnull\b(?!\s+(?:hypothesis|hypotheses|model|models|space|distribution|result|results|effect|effects|set|vector|matrix|test))")),
        ("()", re.compile(r"(?<![A-Za-z0-9_])\(\s*\)")),
        ("[]", re.compile(r"\[\s*\]")),
        ("DOI:.", re.compile(r"DOI:\s*\.")),
    ]
    double_dot_re = re.compile(r"(?<!\.)\.\.(?!\.)")
    for fr in formatted_refs:
        entry = fr["formatted_entry"]
        for label, dpat in dirty_pats:
            if dpat.search(entry):
                err("dirty_formatted_reference", f"{fr['ref_id']} contains `{label}`: {entry}")
        if double_dot_re.search(entry):
            warn("double_period_in_reference", f"{fr['ref_id']} contains `..` (check abbreviation punctuation): {entry}")
        if target_style.startswith("gbt7714") and not re.search(r"\[(J|C|M|D|R|S|P|N|EB|DB|CP|Z)(?:/OL)?\]", entry):
            err("missing_gbt7714_type_code", f"{fr['ref_id']} missing GB/T 7714 doc-type tag: {entry}")

    # 5. Field-level provenance integrity
    for rec in synced.get("updated_canonical_library", []):
        fp = rec.get("field_provenance") or {}
        if not fp or not any(isinstance(v, dict) and v.get("source_locator") for v in fp.values()):
            err("missing_field_provenance", f"{rec['ref_id']} missing grounded field_provenance source_locator")

    # 6. Recycled DOI & Tier-3 disclosure
    merged_sets = [set(r.get("member_raw_ids") or []) for r in synced.get("updated_canonical_library", [])]
    for rc in lib.get("recycled_doi_conflicts", []):
        if rc["raw_a"] not in audit_md and rc["raw_b"] not in audit_md:
            err("undisclosed_recycled_doi", f"Recycled DOI conflict {rc['raw_a']} vs {rc['raw_b']} not disclosed in audit report")
        if any(rc["raw_a"] in ms_set and rc["raw_b"] in ms_set for ms_set in merged_sets):
            err("merged_recycled_doi_conflict", f"Recycled DOI conflict pair {rc['raw_a']} & {rc['raw_b']} was merged")
    reviewed_amb = {d.get("candidate_id") for d in (plan.get("dedup_decisions") or []) if isinstance(d, dict)}
    unresolved_amb = [ac for ac in lib.get("ambiguous_duplicate_candidates", []) if ac["candidate_id"] not in reviewed_amb]
    if unresolved_amb:
        warn("unresolved_tier3_ambiguous", f"{len(unresolved_amb)} Tier-3 ambiguous candidates had no explicit dedup_decisions entry")

    # 7. Claim verification grounding & quote anti-hallucination check (across ALL local evidence + abstracts)
    rec_by_id = {r["ref_id"]: r for r in synced.get("updated_canonical_library", [])}
    alias_map = dict(synced.get("alias_ref_map") or {})
    raw_to_canon = dict(lib.get("raw_to_canonical_map") or {})

    ev_papers_pre: List[Dict[str, Any]] = []
    for ev in ev_data.get("evidence_papers", []):
        raw_txt = "\n".join(p.get("text", "") for p in ev.get("pages", []))
        dehyphen_join = re.sub(r"-\s*\r?\n\s*", "", raw_txt)
        dehyphen_space = re.sub(r"-\s*\r?\n\s*", " ", raw_txt)
        ev_papers_pre.append({
            "txt_low": (dehyphen_join + " " + dehyphen_space).lower(),
            "norm": normalize_title(dehyphen_join) + " " + normalize_title(dehyphen_space),
            "doi": normalize_doi(ev.get("extracted_doi", "")),
            "arxiv": normalize_arxiv(ev.get("extracted_arxiv", "")),
            "title": ev.get("extracted_title", ""),
        })

    all_ev_norm = " ".join(e["norm"] for e in ev_papers_pre)
    ev_corpus_by_ref: Dict[str, str] = {}
    for rec in rec_by_id.values():
        parts = [rec.get("title", ""), rec.get("title_translated", ""), rec.get("abstract", "")]
        r_doi, r_ax, r_title = rec.get("doi") or "", rec.get("arxiv_id") or "", rec.get("title") or ""
        r_tnorm = normalize_title(r_title)
        for evp in ev_papers_pre:
            if (
                (r_doi and (r_doi == evp["doi"] or r_doi in evp["txt_low"]))
                or (r_ax and (r_ax == evp["arxiv"] or r_ax in evp["txt_low"]))
                or (r_title and evp["title"] and title_similarity(r_title, evp["title"]) >= 0.72)
                or (len(r_tnorm) >= 14 and r_tnorm[:28] in evp["norm"])
            ):
                parts.append(evp["norm"])
        ev_corpus_by_ref[rec["ref_id"]] = normalize_title(" ".join(parts))

    claim_verifs = _resolve_effective_claim_verifications(plan, pre_audit, synced)
    if pre_audit.get("sentence_links") and not claim_verifs:
        warn("missing_claim_verifications", "Manuscript has cited sentences but plan.claim_verifications is empty")

    for cv in claim_verifs:
        q = (cv.get("evidence_quote") or "").strip()
        raw_rids = cv.get("cited_ref_ids") or []
        rids = [alias_map.get(raw_to_canon.get(_normalize_ref_token(r), _normalize_ref_token(r)), raw_to_canon.get(_normalize_ref_token(r), _normalize_ref_token(r))) for r in raw_rids]
        if q and len(q) >= 12 and rids:
            nq = normalize_title(re.sub(r"-\s*\n\s*", "", q))
            # Check matched ref corpus first, then global local evidence pool
            matched = any(nq in ev_corpus_by_ref.get(rid, "") for rid in rids) or (nq in all_ev_norm if len(ev_papers_pre) == 1 else False)
            if not matched:
                if re.search(r"[\u4e00-\u9fff]", nq):
                    q_bg = _cjk_bigrams(nq)
                    ratio = max((len(q_bg & _cjk_bigrams(ev_corpus_by_ref.get(rid, ""))) / len(q_bg) for rid in rids), default=0.0) if q_bg else 0.0
                    if ratio >= 0.78:
                        matched = True
                    elif ratio >= 0.42:
                        warn("paraphrased_evidence_quote", f"{cv.get('claim_id')}: evidence_quote appears paraphrased (CJK overlap={ratio:.2f}) for {rids}")
                        matched = True
                else:
                    q_toks = set(nq.split())
                    ratio = max((len(q_toks & set(ev_corpus_by_ref.get(rid, "").split())) / len(q_toks) for rid in rids), default=0.0) if q_toks else 0.0
                    if ratio >= 0.80:
                        matched = True
                    elif ratio >= 0.45:
                        warn("paraphrased_evidence_quote", f"{cv.get('claim_id')}: evidence_quote appears paraphrased (token overlap={ratio:.2f}) for {rids}")
                        matched = True
            if not matched:
                err("ungrounded_evidence_quote", f"{cv.get('claim_id')}: evidence_quote not found in local evidence/abstract for {rids}")

    # 8. Raw placeholders & unreviewed stubs
    raw_ph_re = re.compile(r"\[(?:REF|CITE|CITATION(?:\s+NEEDED)?|\?|待引用|待补引|引用|TODO[^\]]*)\]", re.I)
    m_ph = raw_ph_re.search(_mask_non_prose_for_verify(body_text))
    if m_ph:
        err("raw_placeholder_left_behind", f"Raw placeholder `{m_ph.group(0)}` remains in revised_manuscript.md")
    if len(ms.get("sentences", [])) > 0 and (not revised_md.strip() or "Awaiting citation_plan" in revised_md):
        err("missing_final_manuscript", "revised_manuscript.md is empty or still contains partial stub")
    stub_re = re.compile(r"Pending (?:review|placement)|REVIEW_REQUIRED", re.I)
    for sk in ("dedup_decisions", "manuscript_edits", "claim_verifications"):
        for it in plan.get(sk) or []:
            if isinstance(it, dict) and stub_re.search(json.dumps(it, ensure_ascii=False)):
                err("unreviewed_plan_stub", f"Unreviewed placeholder stub left in citation_plan.json[{sk}]")
                break

    # 9. Harbor result.json contract compliance
    if not result_json:
        err("missing_result_json", "result.json missing in output directory")
    else:
        exp_cid = case_plan.get("case_id", "")
        if exp_cid and result_json.get("case_id") != exp_cid:
            err("case_id_mismatch", f"result.json case_id={result_json.get('case_id')} != {exp_cid}")
        validators = {
            "string": lambda v: isinstance(v, str), "str": lambda v: isinstance(v, str),
            "array": lambda v: isinstance(v, list), "list": lambda v: isinstance(v, list),
            "object": lambda v: isinstance(v, dict), "dict": lambda v: isinstance(v, dict),
            "boolean": lambda v: isinstance(v, bool), "bool": lambda v: isinstance(v, bool),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool), "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool), "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        }
        for cf in case_plan.get("output_contract_parsed", {}).get("fields", []):
            rk = cf["name"]
            if cf.get("required", True) and (rk not in result_json or result_json[rk] is None):
                err("missing_contract_field", f"Required output_contract key `{rk}` missing or null in result.json")
            elif rk in result_json and result_json[rk] is not None:
                chk = validators.get(str(cf.get("expected_type", "any")).lower())
                if chk and not chk(result_json[rk]):
                    err("contract_field_type_mismatch", f"output_contract key `{rk}` expected `{cf.get('expected_type')}`, got `{type(result_json[rk]).__name__}`")

    # 10. Editable artifact presence
    if (out_dir / "revised_manuscript.docx").exists():
        if (out_dir / "revised_manuscript.docx").stat().st_size == 0:
            err("empty_docx_output", "revised_manuscript.docx is 0 bytes")
        if not list(out_dir.glob("docx_preview_p*.png")):
            warn("docx_preview_unavailable", "No DOCX PNG preview generated (LibreOffice soffice unavailable)")

    return {
        "case_id": case_plan.get("case_id", ""),
        "passed": len(errors) == 0,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "cited_references": len(formatted_refs),
            "in_text_occurrences": len(synced.get("in_text_occurrences", [])),
            "uncited_library_entries": len(synced.get("uncited_library_entries", [])),
            "duplicates_removed": lib.get("stats", {}).get("duplicates_removed", 0),
            "dangling_citations_detected": len(pre_audit.get("dangling_citations", [])),
            "recycled_doi_conflicts": len(lib.get("recycled_doi_conflicts", [])),
        },
    }


def assemble_outputs(
    work_dir: Path,
    out_dir: Path,
    plan_path: Optional[Path] = None,
    checkpoint: str = "final",
    run_verify: bool = True,
) -> Dict[str, Any]:
    case_plan = load_json_safe(work_dir / "case_plan.json", {})
    lib = load_json_safe(work_dir / "library_canonical.json", {})
    ms = load_json_safe(work_dir / "manuscript_parsed.json", {})
    pre_audit = load_json_safe(work_dir / "pre_audit.json", {})
    plan = load_json_safe(plan_path, {}) if (plan_path and plan_path.exists()) else {}
    synced = load_json_safe(work_dir / "synced" / "sync_result.json", {})
    out_dir.mkdir(parents=True, exist_ok=True)
    sync_dir = work_dir / "synced"

    stats = case_plan.get("stats", {})
    has_any = (stats.get("total_material_files", 0) - stats.get("corrupt_or_unreadable_count", 0) > 0) or len(lib.get("canonical_records", [])) > 0 or len(ms.get("sentences", [])) > 0
    if not has_any:
        status = "unable_to_determine"
    elif checkpoint == "partial":
        status = "partial"
    else:
        status = "completed"

    uncertainty: List[Dict[str, Any]] = list(plan.get("uncertainty") or [])
    human_items: List[Dict[str, Any]] = list(plan.get("human_confirmation_items") or [])
    recs = synced.get("updated_canonical_library") or lib.get("canonical_records", [])
    existing_u = {u.get("item") for u in uncertainty if isinstance(u, dict)}
    offline_dois = [r["ref_id"] for r in recs if r.get("doi_verification_status") == "not_checked_offline"]
    if offline_dois and "offline_doi_resolution" not in existing_u:
        uncertainty.append({"item": "offline_doi_resolution", "reason": "Offline sandbox without external Crossref/OpenAlex access; DOI syntax checked only.", "scope": ", ".join(offline_dois), "recommended_action": "Verify DOI resolution online."})
    for w in case_plan.get("access_warnings", []) + lib.get("parse_warnings", []) + ms.get("parse_warnings", []):
        uncertainty.append({"item": "input_parse_or_access_warning", "reason": str(w), "scope": "input_files"})
    if case_plan.get("missing_referenced_files"):
        uncertainty.append({"item": "missing_referenced_files", "reason": f"Missing referenced files: {case_plan['missing_referenced_files']}", "scope": "input_files"})

    existing_h = {h.get("issue_id") for h in human_items if isinstance(h, dict)}
    for rc in lib.get("recycled_doi_conflicts", []):
        hid = f"HUM_RDOI_{rc['raw_a']}_{rc['raw_b']}"
        if hid not in existing_h:
            human_items.append({"issue_id": hid, "severity": "critical", "location": f"{rc['raw_a']} vs {rc['raw_b']}", "question": f"Shared DOI/ID ({rc['metrics'].get('shared_id', '')}) across unrelated titles '{rc['title_a']}' vs '{rc['title_b']}'.", "checked_sources": "library_canonical.json"})
    edited_sids = {ed.get("sentence_id") for ed in (plan.get("manuscript_edits") or plan.get("edits") or []) if isinstance(ed, dict) and ed.get("sentence_id") and str(ed.get("action", "")).lower() != "keep"}
    for dc in pre_audit.get("dangling_citations", []):
        if checkpoint == "final" and dc.get("sentence_id") in edited_sids:
            continue
        hid = f"HUM_DANGLE_{dc['sentence_id']}_{dc['missing_token']}"
        if hid not in existing_h:
            human_items.append({"issue_id": hid, "severity": "critical", "location": dc["sentence_id"], "question": f"Citation {dc['raw_marker']} ({dc['reason']}) missing from library.", "checked_sources": "library_canonical.json"})

    deliverable_files: List[str] = []
    if checkpoint == "final" and synced:
        for fname in ("revised_manuscript.md", "revised_manuscript.tex", "references_formatted.md", "references_clean.bib", "references_clean.ris", "references_clean.csl.json", "references_table.csv"):
            src = sync_dir / fname
            if src.exists():
                shutil.copy2(src, out_dir / fname)
                deliverable_files.append(fname)
    else:
        save_text(out_dir / "revised_manuscript.md", f"# Partial Checkpoint (`{case_plan.get('case_id', '')}`)\n\nInput inspection completed (`status={status}`). Awaiting citation_plan synchronization.\n")
        deliverable_files.append("revised_manuscript.md")

    revised_md_text, _, _ = read_text_safe(out_dir / "revised_manuscript.md")
    req_fmts = list(dict.fromkeys((case_plan.get("signals", {}).get("requested_output_formats") or []) + (plan.get("requested_output_formats") or [])))

    if checkpoint == "final" and "docx" in req_fmts:
        docx_path = out_dir / "revised_manuscript.docx"
        ok, derr = _export_docx(revised_md_text, docx_path, f"Manuscript ({case_plan.get('case_id', '')})")
        if ok:
            deliverable_files.append("revised_manuscript.docx")
            previews, pnotes = _render_previews(docx_path, out_dir, "docx")
            deliverable_files.extend(previews)
            for pn in pnotes:
                uncertainty.append({"item": "office_preview", "reason": pn, "scope": "revised_manuscript.docx"})
        elif derr:
            uncertainty.append({"item": "docx_export", "reason": derr, "scope": "revised_manuscript.docx"})

    if checkpoint == "final" and (out_dir / "revised_manuscript.tex").exists():
        previews, pnotes = _render_previews(out_dir / "revised_manuscript.tex", out_dir, "tex")
        deliverable_files.extend(previews)
        for pn in pnotes:
            uncertainty.append({"item": "latex_preview", "reason": pn, "scope": "revised_manuscript.tex"})

    effective_claims = _resolve_effective_claim_verifications(plan, pre_audit, synced)
    claim_evidence_anchors = [
        {
            "claim_id": cv.get("claim_id", ""),
            "sentence_id": cv.get("sentence_id", ""),
            "cited_ref_ids": cv.get("cited_ref_ids", []),
            "verdict": cv.get("verdict", ""),
            "evidence_locator": cv.get("evidence_locator", ""),
            "evidence_quote": cv.get("evidence_quote", ""),
        }
        for cv in effective_claims
    ]

    provenance_ledger = {
        "case_id": case_plan.get("case_id", ""),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "input_files": [f["rel_path"] for f in case_plan.get("files", [])],
        "raw_to_canonical_map": lib.get("raw_to_canonical_map", {}),
        "merge_groups": lib.get("merge_groups", []),
        "protected_non_duplicates": lib.get("protected_non_duplicates", []),
        "recycled_doi_conflicts": lib.get("recycled_doi_conflicts", []),
        "field_provenance_by_ref": {r["ref_id"]: r.get("field_provenance", {}) for r in recs},
        "claim_evidence_anchors": claim_evidence_anchors,
        "renumbering_map": synced.get("renumbering_map", []),
        "claim_verifications": effective_claims,
        "change_records": synced.get("change_records", []),
    }
    save_json(out_dir / "provenance_ledger.json", provenance_ledger)
    deliverable_files.append("provenance_ledger.json")

    audit_md = _build_audit_report(case_plan, lib, pre_audit, plan, synced, {}, uncertainty, human_items, checkpoint)
    save_text(out_dir / "citation_audit_report.md", audit_md)
    deliverable_files.append("citation_audit_report.md")
    deliverable_files = list(dict.fromkeys(["result.json"] + deliverable_files))

    refs_formatted_list = [fr["formatted_entry"] for fr in synced.get("formatted_references", [])]
    role_values: Dict[str, Any] = {
        "case_id": case_plan.get("case_id", ""),
        "status": status,
        "revised_manuscript": revised_md_text,
        "reference_list": refs_formatted_list,
        "renumbering_map": synced.get("renumbering_map", []),
        "dedup_report": {
            "raw_record_count": lib.get("stats", {}).get("raw_record_count", 0),
            "canonical_record_count": len(recs),
            "duplicates_removed": lib.get("stats", {}).get("duplicates_removed", 0),
            "merge_groups": lib.get("merge_groups", []),
            "extra_tier3_merges": synced.get("extra_tier3_merges", []),
            "ambiguous_candidates": lib.get("ambiguous_duplicate_candidates", []),
            "protected_non_duplicates": lib.get("protected_non_duplicates", []),
            "recycled_doi_conflicts": lib.get("recycled_doi_conflicts", []),
        },
        "claim_verification": effective_claims or pre_audit.get("sentence_links", []),
        "field_provenance": {
            "input_snapshot": [f["rel_path"] for f in case_plan.get("files", [])],
            "by_ref_id": {r["ref_id"]: r.get("field_provenance", {}) for r in recs},
            "claim_evidence_anchors": claim_evidence_anchors,
        },
        "change_records": synced.get("change_records", []),
        "uncertainty": uncertainty,
        "human_confirmation_items": human_items,
        "deliverable_files": deliverable_files,
        "summary": f"Processed {len(recs)} canonical refs ({lib.get('stats', {}).get('duplicates_removed', 0)} merged) in style `{synced.get('target_style') or case_plan.get('signals', {}).get('target_style')}`, {synced.get('stats', {}).get('cited_ref_count', 0)} cited; status={status}.",
    }

    result_obj: Dict[str, Any] = {
        "case_id": role_values["case_id"],
        "status": status,
        "checkpoint": checkpoint,
        "target_style": synced.get("target_style") or case_plan.get("signals", {}).get("target_style"),
        "in_text_format": synced.get("in_text_format") or case_plan.get("signals", {}).get("in_text_format"),
        "revised_manuscript": role_values["revised_manuscript"],
        "reference_list": role_values["reference_list"],
        "renumbering_map": role_values["renumbering_map"],
        "dedup_report": role_values["dedup_report"],
        "claim_verifications": role_values["claim_verification"],
        "sources": role_values["field_provenance"],
        "change_records": role_values["change_records"],
        "uncertainty": role_values["uncertainty"],
        "human_confirmation_items": role_values["human_confirmation_items"],
        "uncited_library_entries": synced.get("uncited_library_entries", []),
        "deliverable_files": deliverable_files,
        "verification_summary": {"passed": None, "checkpoint": checkpoint},
    }

    overrides = plan.get("contract_overrides") or {}
    for cf in case_plan.get("output_contract_parsed", {}).get("fields", []):
        fn = cf["name"]
        et = cf.get("expected_type", "any")
        if fn in overrides:
            result_obj[fn] = _coerce_contract_type(overrides[fn], et)
        elif fn in result_obj:
            result_obj[fn] = _coerce_contract_type(result_obj[fn], et)
        else:
            val = role_values.get(cf.get("semantic_role", "custom"), role_values["summary"])
            result_obj[fn] = _coerce_contract_type(val, et)

    save_json(out_dir / "result.json", result_obj)

    if run_verify and plan_path is not None:
        ver = verify_deliverables(work_dir, out_dir, plan_path)
        save_json(work_dir / "verification_report.json", ver)
        save_text(out_dir / "citation_audit_report.md", _build_audit_report(case_plan, lib, pre_audit, plan, synced, ver, uncertainty, human_items, checkpoint))
        if ver["error_count"] > 0 and status == "completed":
            result_obj["status"] = "partial"
        result_obj["verification_summary"] = ver
        save_json(out_dir / "result.json", result_obj)
        return ver

    return {"checkpoint": checkpoint, "status": status, "deliverable_files": deliverable_files}


def main() -> None:
    ap = argparse.ArgumentParser(description="Synchronize, verify 10 invariants, and assemble /app/output deliverables.")
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--out-dir", default="/app/output")
    ap.add_argument("--plan", default="/app/work/citation_plan.json")
    ap.add_argument("--allow-partial", action="store_true")
    args = ap.parse_args()

    work = Path(args.work_dir)
    out = Path(args.out_dir)
    plan_p = Path(args.plan)

    from manuscript_and_sync import run_sync
    run_sync(work, plan_p)
    ver = assemble_outputs(work, out, plan_path=plan_p, checkpoint="final", run_verify=True)
    print(json.dumps(ver, ensure_ascii=False, indent=2))
    if ver.get("error_count", 0) > 0 and not args.allow_partial:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
