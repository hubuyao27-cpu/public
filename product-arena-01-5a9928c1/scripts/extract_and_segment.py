#!/usr/bin/env python3
"""Layout-aware scientific PDF/text extractor and segmenter for verifiable translation.

Features:
- Multi-backend support: PyMuPDF span/vector extraction -> pdftohtml -xml -> pdfplumber -> pypdf -> pdftotext -> plaintext/md
- Cross-page header/footer/marginalia fingerprinting and isolation
- Adaptive XY-Cut column ordering + line-internal gutter bleed splitting
- Academic booktabs/grid table reconstruction (kind='table') + multi-page continuation linking
- Baseline-offset subscript/superscript reconstruction (CHCl_{3}, 10^{-6}, g_{CC}) & equation tag attachment
- Cross-column / cross-page continuation context tagging (prev_context_tail)
- Automatic domain glossary candidate extraction (glossary_candidates.json)
"""

import argparse
import json
import math
import re
import shutil
import statistics
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"

NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?(?:\s*%|\^\{[^}]+\}|[eE][+-]?\d+)?")
UNIT_RE = re.compile(
    r"(?<![A-Za-z])(kJ/mol|kcal/mol|mg/mL|mg/L|mmol/L|mol/L|m2/g|m\^\{?2\}?/g|g/cm3|g/cm\^\{?3\}?|wt%|vol%|nm|μm|um|mm|cm|MPa|kPa|GPa|atm|bar|eV|MHz|Hz|°C|℃|K|min|h|%)"
)
CITE_RE = re.compile(r"\[\d+(?:\s*[-–,]\s*\d+)*\]|\([A-Z][A-Za-z\-]+(?:\s+et\s+al\.)?,?\s*\d{4}[a-z]?\)")
FIG_REF_RE = re.compile(r"\b(?:Fig(?:ure)?s?|Table|Tables|Eq(?:uation)?s?|Scheme|Chart)\s*\.?\s*\(?\d+[a-zA-Z]?\)?", re.I)
STOP_PHRASES = {
    "abstract", "introduction", "methods", "results", "discussion", "conclusion", "conclusions",
    "references", "acknowledgments", "acknowledgements", "in this", "on the", "of the", "for the",
    "this work", "this study", "et al", "table", "figure", "fig",
}


def load_symbol_map() -> Dict[str, Any]:
    p = RESOURCES_DIR / "symbol_map.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {
        "ligatures": {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl"},
        "invisible_or_soft_hyphens": ["\u00ad", "\u200b", "\u200c", "\u200d", "\ufeff"],
        "dash_normalization": {"\u2010": "-", "\u2011": "-"},
        "symbol_font_ascii_to_unicode": {},
        "symbol_font_names": ["Symbol", "StandardSymbolsPS"],
        "garble_thresholds": {"page_garble_ratio_ocr": 0.05, "min_text_chars_per_page": 40, "min_image_ratio_for_scan": 0.25},
    }


def normalize_span_text(text: str, font_name: str, sym_map: dict):
    if not text:
        return "", 0, 0
    for ch in sym_map.get("invisible_or_soft_hyphens", []):
        text = text.replace(ch, "")
    for k, v in sym_map.get("ligatures", {}).items():
        text = text.replace(k, v)
    for k, v in sym_map.get("dash_normalization", {}).items():
        text = text.replace(k, v)

    is_sym_font = any(s.lower() in (font_name or "").lower() for s in sym_map.get("symbol_font_names", []))
    sym_table = sym_map.get("symbol_font_ascii_to_unicode", {})

    out_chars = []
    garble_count = 0
    non_ws = 0
    for ch in text:
        if is_sym_font and ch in sym_table:
            ch = sym_table[ch]
        cp = ord(ch)
        if not ch.isspace():
            non_ws += 1
        if cp == 0xFFFD or (0xE000 <= cp <= 0xF8FF) or (cp < 32 and ch not in "\n\r\t"):
            garble_count += 1
            out_chars.append(f"[U+{cp:04X}]" if 0xE000 <= cp <= 0xF8FF else ch)
        else:
            out_chars.append(ch)
    res = "".join(out_chars)
    garble_count += len(re.findall(r"\(cid:\d+\)", res)) * 3
    return res, garble_count, non_ws


def format_line_with_scripts(spans: list, sym_map: dict):
    if not spans:
        return "", "", 0, 0, 10.0
    non_empty = [s for s in spans if s["text"].strip()]
    if not non_empty:
        return "", "", 0, 0, 10.0

    weighted = []
    for s in non_empty:
        weighted.extend([s["size"]] * max(1, len(s["text"].strip())))
    body_size = statistics.median(weighted)
    normal_spans = [s for s in non_empty if s["size"] >= 0.86 * body_size] or non_empty
    base_y = statistics.median([s["origin_y"] for s in normal_spans])
    base_cy = statistics.median([0.5 * (s["bbox"][1] + s["bbox"][3]) for s in normal_spans])

    raw_parts, norm_parts = [], []
    total_garble, total_non_ws = 0, 0

    for idx, sp in enumerate(spans):
        txt_clean, gc, nw = normalize_span_text(sp["text"], sp.get("font", ""), sym_map)
        total_garble += gc
        total_non_ws += nw
        stripped = txt_clean.strip()
        cy = 0.5 * (sp["bbox"][1] + sp["bbox"][3])
        oy = sp["origin_y"]
        is_small = sp["size"] <= 0.84 * body_size
        is_sup_flag = bool(sp.get("flags", 0) & 1)
        is_sup = bool(stripped and len(stripped) <= 18 and (is_small or is_sup_flag) and ((oy < base_y - 0.16 * body_size) or (is_sup_flag and cy < base_cy - 0.08 * body_size)))
        is_sub = bool(stripped and len(stripped) <= 18 and is_small and not is_sup and (oy > base_y + 0.12 * body_size))

        if idx > 0 and norm_parts:
            prev = spans[idx - 1]
            gap = sp["bbox"][0] - prev["bbox"][2]
            prev_ends_ws = norm_parts[-1].endswith((" ", "\t", "{", "("))
            curr_starts_ws = txt_clean.startswith((" ", "\t", ")", "}", ",", ".", ";", ":"))
            min_gap = (0.55 * body_size) if (is_sup or is_sub) else max(1.2, 0.22 * body_size)
            if gap > min_gap and not prev_ends_ws and not curr_starts_ws:
                norm_parts.append(" ")
                raw_parts.append(" ")

        raw_parts.append(txt_clean)
        if not stripped:
            next_is_script = False
            if idx + 1 < len(spans):
                nsp = spans[idx + 1]
                n_txt = nsp["text"].strip()
                n_oy = nsp["origin_y"]
                if n_txt and len(n_txt) <= 18 and nsp["size"] <= 0.84 * body_size and (n_oy < base_y - 0.16 * body_size or n_oy > base_y + 0.12 * body_size):
                    next_is_script = True
            if next_is_script and (sp["bbox"][2] - sp["bbox"][0]) < 0.32 * body_size:
                continue
            norm_parts.append(txt_clean)
            continue

        if is_sup:
            lead = " " if txt_clean.startswith(" ") and norm_parts and not norm_parts[-1].endswith(" ") else ""
            trail = " " if txt_clean.endswith(" ") else ""
            norm_parts.append(f"{lead}^{{{stripped}}}{trail}")
            continue
        if is_sub:
            lead = " " if txt_clean.startswith(" ") and norm_parts and not norm_parts[-1].endswith(" ") else ""
            trail = " " if txt_clean.endswith(" ") else ""
            norm_parts.append(f"{lead}_{{{stripped}}}{trail}")
            continue
        norm_parts.append(txt_clean)

    raw_line = re.sub(r"[ \t]+", " ", "".join(raw_parts)).strip()
    norm_line = re.sub(r"[ \t]+", " ", "".join(norm_parts)).strip()
    return raw_line, norm_line, total_garble, total_non_ws, body_size


def split_span_line_at_gaps(spans: list, gap_threshold: float):
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: s["bbox"][0])
    groups = [[ordered[0]]]
    for sp in ordered[1:]:
        prev_x1 = max(s["bbox"][2] for s in groups[-1])
        if sp["bbox"][0] - prev_x1 > gap_threshold:
            groups.append([sp])
        else:
            groups[-1].append(sp)
    return groups


def cluster_segments_into_subblocks(segs: list):
    if len(segs) <= 1:
        return [segs]
    intervals = sorted([(s["bbox"][0], s["bbox"][2], idx) for idx, s in enumerate(segs)])
    groups = []
    cur_x1, cur_idxs = intervals[0][1], [intervals[0][2]]
    for x0, x1, idx in intervals[1:]:
        if x0 <= cur_x1 + 6.0:
            cur_x1 = max(cur_x1, x1)
            cur_idxs.append(idx)
        else:
            groups.append(cur_idxs)
            cur_x1, cur_idxs = x1, [idx]
    groups.append(cur_idxs)
    if len(groups) == 1:
        return [segs]

    res = []
    for idxs in groups:
        sub = sorted([segs[i] for i in idxs], key=lambda s: s["bbox"][1])
        v_chunks = [[sub[0]]]
        for s in sub[1:]:
            prev = v_chunks[-1][-1]
            lh = max(8.0, prev["bbox"][3] - prev["bbox"][1])
            if s["bbox"][1] - prev["bbox"][3] > 0.85 * lh:
                v_chunks.append([s])
            else:
                v_chunks[-1].append(s)
        res.extend(v_chunks)
    return res


def join_lines_smart(lines: list):
    if not lines:
        return ""
    buf = lines[0].rstrip()
    for nxt in lines[1:]:
        nxt_s = nxt.strip()
        if not nxt_s:
            continue
        if re.search(r"[A-Za-z]{2}-$", buf) and re.match(r"^[a-z]", nxt_s):
            buf = buf[:-1] + nxt_s
        elif re.search(r"[\u4e00-\u9fff]$", buf) and re.match(r"^[\u4e00-\u9fff]", nxt_s):
            buf = buf + nxt_s
        else:
            buf = buf + " " + nxt_s
    return re.sub(r"[ \t]+", " ", buf).strip()


def dedup_rules(rules: list, tol: float = 2.0):
    if not rules:
        return []
    ordered = sorted(rules, key=lambda r: (r[1], r[0]))
    out = []
    for r in ordered:
        merged = False
        for o in out:
            if abs(r[1] - o[1]) <= tol and not (r[2] < o[0] - 4.0 or r[0] > o[2] + 4.0):
                o[0] = min(o[0], r[0])
                o[2] = max(o[2], r[2])
                o[1] = 0.5 * (o[1] + r[1])
                merged = True
                break
        if not merged:
            out.append(list(r))
    return out


def extract_page_primitives_pymupdf(page, sym_map: dict):
    rect = page.rect
    W, H = float(rect.width), float(rect.height)
    d = page.get_text("dict")
    raw_blocks, font_sizes = [], []

    for b in d.get("blocks", []):
        if b.get("type", 0) != 0:
            continue
        lines = b.get("lines", [])
        if not lines:
            continue
        block_segs = []
        for ln in lines:
            dir_vec = ln.get("dir", (1.0, 0.0))
            is_vertical = abs(dir_vec[1]) > 0.7
            spans = []
            for sp in ln.get("spans", []):
                if not sp.get("text"):
                    continue
                bb = sp["bbox"]
                spans.append({
                    "text": sp["text"],
                    "font": sp.get("font", ""),
                    "size": float(sp.get("size", 10.0)),
                    "flags": int(sp.get("flags", 0)),
                    "origin_y": float(sp.get("origin", (bb[0], bb[3]))[1]),
                    "bbox": [float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])],
                })
            if not spans:
                continue
            median_sz = statistics.median([s["size"] for s in spans])
            gap_thr = max(14.0, 2.0 * median_sz)
            groups = [spans] if is_vertical else split_span_line_at_gaps(spans, gap_thr)
            for grp in groups:
                bx0 = min(s["bbox"][0] for s in grp)
                by0 = min(s["bbox"][1] for s in grp)
                bx1 = max(s["bbox"][2] for s in grp)
                by1 = max(s["bbox"][3] for s in grp)
                raw_ln, norm_ln, gc, nw, lsz = format_line_with_scripts(grp, sym_map)
                if not raw_ln:
                    continue
                is_bold = any(("bold" in s["font"].lower()) or (s["flags"] & 16) for s in grp)
                font_sizes.extend([lsz] * max(1, len(raw_ln)))
                block_segs.append({
                    "bbox": [bx0, by0, bx1, by1],
                    "raw_text": raw_ln,
                    "text": norm_ln,
                    "size": lsz,
                    "bold": is_bold,
                    "garble": gc,
                    "non_ws": nw,
                    "vertical": is_vertical,
                })
        if not block_segs:
            continue
        for segs in cluster_segments_into_subblocks(block_segs):
            segs_sorted = sorted(segs, key=lambda s: (s["bbox"][1], s["bbox"][0]))
            bx0 = min(s["bbox"][0] for s in segs_sorted)
            by0 = min(s["bbox"][1] for s in segs_sorted)
            bx1 = max(s["bbox"][2] for s in segs_sorted)
            by1 = max(s["bbox"][3] for s in segs_sorted)
            raw_joined = "\n".join(s["raw_text"] for s in segs_sorted)
            norm_joined = join_lines_smart([s["text"] for s in segs_sorted])
            gc = sum(s["garble"] for s in segs_sorted)
            nw = sum(s["non_ws"] for s in segs_sorted)
            avg_sz = statistics.median([s["size"] for s in segs_sorted])
            all_bold = all(s["bold"] for s in segs_sorted[:1]) and (sum(1 for s in segs_sorted if s["bold"]) >= 0.7 * len(segs_sorted))
            raw_blocks.append({
                "bbox": [round(bx0, 2), round(by0, 2), round(bx1, 2), round(by1, 2)],
                "raw_text": raw_joined,
                "text": norm_joined,
                "lines": segs_sorted,
                "size": round(avg_sz, 2),
                "bold": bool(all_bold),
                "garble": gc,
                "non_ws": nw,
                "vertical": any(s["vertical"] for s in segs_sorted),
            })

    h_rules = []
    for drw in page.get_drawings():
        r = drw.get("rect")
        if r and (r.height <= 3.5 and r.width >= 25.0):
            h_rules.append([float(r.x0), 0.5 * float(r.y0 + r.y1), float(r.x1), float(r.y0), float(r.y1)])
        for item in drw.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p2.y - p1.y) <= 1.5 and abs(p2.x - p1.x) >= 25.0:
                    ym = 0.5 * (p1.y + p2.y)
                    h_rules.append([min(p1.x, p2.x), ym, max(p1.x, p2.x), ym - 0.5, ym + 0.5])
            elif item[0] == "re":
                rr = item[1]
                if rr.height <= 3.5 and rr.width >= 25.0:
                    h_rules.append([rr.x0, 0.5 * (rr.y0 + rr.y1), rr.x1, rr.y0, rr.y1])
    h_rules = dedup_rules(h_rules, tol=2.0)

    images = []
    for info in page.get_image_info(xrefs=False):
        bb = info.get("bbox")
        if bb:
            images.append([round(float(bb[0]), 2), round(float(bb[1]), 2), round(float(bb[2]), 2), round(float(bb[3]), 2)])

    return {
        "width": W, "height": H, "printed_label": page.get_label() or None,
        "blocks": raw_blocks, "h_rules": h_rules, "images": images, "font_sizes": font_sizes,
    }


def detect_headers_footers_marginalia(pages_data: list):
    n_pages = len(pages_data)
    top_counts, bot_counts = Counter(), Counter()
    for pd in pages_data:
        H = pd["height"]
        for b in pd["blocks"]:
            y0, y1 = b["bbox"][1], b["bbox"][3]
            fp = re.sub(r"\s+", " ", re.sub(r"\d+", "#", b["raw_text"].lower())).strip()
            if not fp or len(fp) > 140 or b.get("vertical"):
                continue
            if y0 < 0.11 * H:
                top_counts[fp] += 1
            elif y1 > 0.89 * H:
                bot_counts[fp] += 1

    min_rep = 2 if n_pages <= 5 else max(2, math.ceil(0.35 * n_pages))
    rep_top = {fp for fp, cnt in top_counts.items() if cnt >= min_rep}
    rep_bot = {fp for fp, cnt in bot_counts.items() if cnt >= min_rep}
    page_num_re = re.compile(r"^\s*(?:[-–—]?\s*\d{1,4}\s*[-–—]?|[pP]age\s+\d+(?:\s+of\s+\d+)?|\d+\s*/\s*\d+|第\s*\d+\s*页|[ivxlcdm]{1,6})\s*$")

    hf = []
    for pd in pages_data:
        W, H, pno = pd["width"], pd["height"], pd["page"]
        for b in pd["blocks"]:
            b["role"] = None
            x0, y0, x1, y1 = b["bbox"]
            txt = b["raw_text"].strip()
            fp = re.sub(r"\s+", " ", re.sub(r"\d+", "#", txt.lower())).strip()
            if b.get("vertical") and (x1 < 0.08 * W or x0 > 0.92 * W):
                b["role"] = "marginalia"
                hf.append({"page": pno, "role": "marginalia", "text": txt, "bbox": b["bbox"]})
            elif y0 < 0.11 * H and (fp in rep_top or (page_num_re.match(txt) and y1 < 0.09 * H)):
                b["role"] = "header"
                hf.append({"page": pno, "role": "header", "text": txt, "bbox": b["bbox"]})
            elif y1 > 0.89 * H and (fp in rep_bot or (page_num_re.match(txt) and y0 > 0.91 * H)):
                b["role"] = "footer"
                hf.append({"page": pno, "role": "footer", "text": txt, "bbox": b["bbox"]})
    return hf


def cluster_lines_to_rows(segs: list):
    ordered = sorted(segs, key=lambda s: (0.5 * (s["bbox"][1] + s["bbox"][3]), s["bbox"][0]))
    heights = [max(4.0, s["bbox"][3] - s["bbox"][1]) for s in ordered]
    tol = 0.48 * statistics.median(heights)
    rows = [[ordered[0]]]
    row_cy = 0.5 * (ordered[0]["bbox"][1] + ordered[0]["bbox"][3])
    for s in ordered[1:]:
        cy = 0.5 * (s["bbox"][1] + s["bbox"][3])
        if abs(cy - row_cy) <= tol:
            rows[-1].append(s)
            row_cy = statistics.mean([0.5 * (x["bbox"][1] + x["bbox"][3]) for x in rows[-1]])
        else:
            rows.append([s])
            row_cy = cy
    for r in rows:
        r.sort(key=lambda s: s["bbox"][0])
    return rows


def detect_table_columns(rows: list, hdr_rows: int, tx0: float, tx1: float):
    sample = rows[hdr_rows:] if len(rows) > hdr_rows else rows
    if len(rows) > 1 and hdr_rows > 0:
        sample = [rows[hdr_rows - 1]] + list(sample)
    segs = [s for r in sample for s in r]
    if not segs:
        return [(tx0, tx1)]
    x_min = int(math.floor(min(s["bbox"][0] for s in segs)))
    x_max = int(math.ceil(max(s["bbox"][2] for s in segs)))
    w = max(1, x_max - x_min + 1)
    occ = [0] * w
    for s in segs:
        for k in range(max(0, int(math.floor(s["bbox"][0])) - x_min), min(w, int(math.ceil(s["bbox"][2])) - x_min)):
            occ[k] += 1
    cols, in_c, cs, zr = [], False, 0, 0
    for i, v in enumerate(occ):
        if v > 0:
            if not in_c:
                cs, in_c = i, True
            zr = 0
        elif in_c:
            zr += 1
            if zr >= 4:
                cols.append((x_min + cs, x_min + i - zr))
                in_c = False
    if in_c:
        cols.append((x_min + cs, x_max))
    return cols or [(tx0, tx1)]


def build_table_grid(segs: list, h_rules: list, tx0: float, tx1: float):
    rows = cluster_lines_to_rows(segs)
    if not rows:
        return None
    row_ys = [(min(s["bbox"][1] for s in r), max(s["bbox"][3] for s in r)) for r in rows]
    hdr_rows = 1 if len(rows) > 1 else 0
    top_y, bot_y = row_ys[0][0], row_ys[-1][1]
    internal = sorted([hr[1] for hr in h_rules if top_y + 2.0 < hr[1] < bot_y - 2.0])
    if internal and (internal[0] - top_y) <= 0.55 * max(1.0, bot_y - top_y):
        cnt = sum(1 for _, ry1 in row_ys if ry1 <= internal[0] + 2.0)
        if 0 < cnt < len(rows):
            hdr_rows = cnt

    cols = detect_table_columns(rows, hdr_rows, tx0, tx1)
    ncols = len(cols)
    grid = [["" for _ in range(ncols)] for _ in range(len(rows))]
    for r_idx, row in enumerate(rows):
        for sp in row:
            sx0, sx1 = sp["bbox"][0], sp["bbox"][2]
            best = [c_i for c_i, (cx0, cx1) in enumerate(cols) if min(sx1, cx1 + 2.0) - max(sx0, cx0 - 2.0) > 0]
            if not best:
                scx = 0.5 * (sx0 + sx1)
                best = [min(range(ncols), key=lambda k: abs(scx - 0.5 * (cols[k][0] + cols[k][1])))]
            txt = sp["text"].strip()
            for ck in range(best[0], best[-1] + 1):
                grid[r_idx][ck] = (grid[r_idx][ck] + " " + txt).strip() if grid[r_idx][ck] else txt

    md = []
    hdr_cnt = max(1, min(hdr_rows, len(grid)))
    if hdr_cnt == 1:
        md.append("| " + " | ".join(c.replace("|", "\\|") for c in grid[0]) + " |")
    else:
        mh = [" / ".join(dict.fromkeys(grid[r][c] for r in range(hdr_cnt) if grid[r][c])) for c in range(ncols)]
        md.append("| " + " | ".join(c.replace("|", "\\|") for c in mh) + " |")
    md.append("| " + " | ".join(["---"] * ncols) + " |")
    for r in range(hdr_cnt, len(grid)):
        md.append("| " + " | ".join(c.replace("|", "\\|") for c in grid[r]) + " |")

    return {"n_rows": len(grid), "n_cols": ncols, "header_rows": hdr_rows, "grid": grid, "markdown": "\n".join(md)}


def extract_tables_from_page(pd: dict):
    H, pno = pd["height"], pd["page"]
    cand_rules = [hr for hr in pd["h_rules"] if 0.07 * H < hr[1] < 0.92 * H and (hr[2] - hr[0]) >= 55.0]
    clusters = []
    for hr in sorted(cand_rules, key=lambda x: x[1]):
        placed = False
        for cl in clusters:
            last = cl[-1]
            ov = min(hr[2], last[2]) - max(hr[0], last[0])
            if ov >= 0.45 * min(hr[2] - hr[0], last[2] - last[0]) and (hr[1] - last[1]) <= 0.45 * H:
                cl.append(hr)
                placed = True
                break
        if not placed:
            clusters.append([hr])

    tab_cap_re = re.compile(r"^\s*(Table|Tab\.?|表)\s*([IVXLCDM]+|\d+(?:\.\d+)?)\b", re.I)
    tables = []
    consumed = set()
    for cl in clusters:
        if len(cl) < 2:
            continue
        tx0, tx1 = min(r[0] for r in cl) - 6.0, max(r[2] for r in cl) + 6.0
        ty0, ty1 = min(r[1] for r in cl) - 2.0, max(r[1] for r in cl) + 2.0
        if ty1 - ty0 < 18.0:
            continue
        cap = None
        for idx, b in enumerate(pd["blocks"]):
            if b.get("role") in ("header", "footer", "marginalia") or idx in consumed:
                continue
            bx0, by0, bx1, by1 = b["bbox"]
            if min(bx1, tx1) - max(bx0, tx0) >= 30.0 and (0 <= ty0 - by1 <= 32.0 or 0 <= by0 - ty1 <= 26.0) and tab_cap_re.match(b["raw_text"]):
                cap = b["text"]
                b["role"] = "caption"
                break
        t_segs, mem = [], []
        for idx, b in enumerate(pd["blocks"]):
            if b.get("role") in ("header", "footer", "marginalia", "caption") or idx in consumed:
                continue
            bx0, by0, bx1, by1 = b["bbox"]
            cx, cy = 0.5 * (bx0 + bx1), 0.5 * (by0 + by1)
            if (tx0 - 6.0 <= cx <= tx1 + 6.0) and (ty0 <= cy <= ty1):
                mem.append(idx)
                t_segs.extend(b["lines"])
        if len(t_segs) < 2:
            continue
        grid_res = build_table_grid(t_segs, cl, tx0, tx1)
        if not grid_res or grid_res["n_rows"] < 2:
            continue
        for idx in mem:
            consumed.add(idx)
            pd["blocks"][idx]["role"] = "table_body"

        # Insert synthetic table block into pd["blocks"] so XY-Cut preserves table position in reading stream!
        tid = f"p{pno:02d}_t{len(tables)+1:02d}"
        tab_obj = {
            "table_id": tid,
            "page": pno,
            "printed_page": pd.get("printed_label"),
            "bbox": [round(tx0 + 6.0, 2), round(ty0 + 2.0, 2), round(tx1 - 6.0, 2), round(ty1 - 2.0, 2)],
            "caption": cap,
            **grid_res,
        }
        tables.append(tab_obj)
        pd["blocks"].append({
            "bbox": tab_obj["bbox"],
            "raw_text": grid_res["markdown"],
            "text": grid_res["markdown"],
            "lines": [{"bbox": tab_obj["bbox"], "raw_text": grid_res["markdown"], "text": grid_res["markdown"]}],
            "size": 10.0,
            "bold": False,
            "garble": 0,
            "non_ws": len(grid_res["markdown"].strip()),
            "vertical": False,
            "role": "table",
            "table_meta": {"table_id": tid, "n_rows": grid_res["n_rows"], "n_cols": grid_res["n_cols"], "caption": cap},
        })
    return tables


def detect_column_gutters(blocks: list, page_width: float):
    narrow = [b for b in blocks if (b["bbox"][2] - b["bbox"][0]) < 0.49 * page_width and b["non_ws"] >= 4]
    if len(narrow) < 2:
        narrow = [b for b in blocks if (b["bbox"][2] - b["bbox"][0]) < 0.56 * page_width and b["non_ws"] >= 4]
    if len(narrow) < 2:
        return []
    W = int(math.ceil(page_width))
    occ = [0] * max(1, W + 1)
    segs = [ln for b in narrow for ln in (b.get("lines") or [b]) if (ln["bbox"][2] - ln["bbox"][0]) < 0.49 * page_width]
    if len(segs) < 2:
        segs = narrow
    for s in segs:
        for k in range(max(0, int(math.floor(s["bbox"][0]))), min(W, int(math.ceil(s["bbox"][2])))):
            occ[k] += 1

    def find_gaps(max_occ: int):
        found, i, end = [], int(0.22 * W), int(0.78 * W)
        while i < end:
            if occ[i] <= max_occ:
                j = i
                while j < end and occ[j] <= max_occ:
                    j += 1
                if j - i >= 8:
                    lc = sum(1 for s in segs if s["bbox"][2] <= i + 4)
                    rc = sum(1 for s in segs if s["bbox"][0] >= j - 4)
                    if lc >= 2 and rc >= 2:
                        found.append((float(i), float(j)))
                i = j
            else:
                i += 1
        return found

    gutters = find_gaps(0)
    if not gutters and len(segs) >= 6:
        gutters = find_gaps(1)
    if len(gutters) > 2:
        gutters.sort(key=lambda g: g[1] - g[0], reverse=True)
        gutters = sorted(gutters[:2])
    return gutters


def sort_blocks_xy_cut(blocks: list, page_width: float):
    footnotes = [b for b in blocks if b.get("role") == "footnote"]
    main = [b for b in blocks if b.get("role") != "footnote"]
    if not main:
        return sorted(footnotes, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    gutters = detect_column_gutters(main, page_width)
    if not gutters:
        ordered = sorted(main, key=lambda b: (round(b["bbox"][1] / 4.0), b["bbox"][0]))
        for b in ordered:
            b["column"] = "full"
        for b in footnotes:
            b["column"] = "footnote"
        return ordered + sorted(footnotes, key=lambda b: (b["bbox"][1], b["bbox"][0]))

    def col_idx(b):
        cx = 0.5 * (b["bbox"][0] + b["bbox"][2])
        for gx0, gx1 in gutters:
            if b["bbox"][0] < gx0 - 4.0 and b["bbox"][2] > gx1 + 4.0:
                return -1
        return sum(1 for gx0, gx1 in gutters if cx > 0.5 * (gx0 + gx1))

    spanning, narrow = [], []
    for b in main:
        c = col_idx(b)
        b["_col"] = c
        b["column"] = "full" if c == -1 else f"col{c + 1}"
        (spanning if c == -1 else narrow).append(b)
    spanning.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

    ordered, prev_y = [], -1e9
    for sp in spanning:
        mid = 0.5 * (sp["bbox"][1] + sp["bbox"][3])
        band = [b for b in narrow if prev_y <= 0.5 * (b["bbox"][1] + b["bbox"][3]) < mid]
        band.sort(key=lambda b: (b["_col"], b["bbox"][1], b["bbox"][0]))
        ordered.extend(band)
        ordered.append(sp)
        prev_y = mid
    tail = [b for b in narrow if 0.5 * (b["bbox"][1] + b["bbox"][3]) >= prev_y]
    tail.sort(key=lambda b: (b["_col"], b["bbox"][1], b["bbox"][0]))
    ordered.extend(tail)
    for b in footnotes:
        b["column"] = "footnote"
    ordered.extend(sorted(footnotes, key=lambda b: (b["bbox"][1], b["bbox"][0])))
    return ordered


def extract_cues(text: str) -> Dict[str, List[str]]:
    return {
        "numbers": NUMBER_RE.findall(text),
        "units": UNIT_RE.findall(text),
        "citations": CITE_RE.findall(text),
        "refs": FIG_REF_RE.findall(text),
    }


def mine_glossary_candidates(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministically extract candidate domain terms and Full Term (ACR) pairs."""
    acr_re = re.compile(r"\b([A-Za-z][A-Za-z0-9\- ]{3,48})\s+\(([A-Z][A-Z0-9\-]{1,10})\)")
    candidates = {}

    for seg in segments:
        txt = seg.get("text", "")
        if not txt:
            continue
        for full, acr in acr_re.findall(txt):
            term = re.sub(r"^(?:the|a|an|this|our)\s+", "", full.strip(), flags=re.I)
            if len(term) < 4 or term.lower() in STOP_PHRASES:
                continue
            key = term.lower()
            if key not in candidates:
                candidates[key] = {"source": term, "acronym": acr, "count": 0, "first_segment": seg["id"], "priority": "high"}
            candidates[key]["count"] += 1

    # Frequent bigrams/trigrams from headings, title, abstract, and body
    phrase_re = re.compile(r"\b([a-z][a-z\-]{2,}(?:\s+[a-z][a-z\-]{2,}){1,2})\b")
    phrase_counts = Counter()
    phrase_first = {}
    for seg in segments:
        if seg.get("kind") in ("reference", "unreadable_page", "table"):
            continue
        for ph in phrase_re.findall(seg.get("text", "").lower()):
            words = ph.split()
            if words[0] in {"the", "this", "that", "these", "with", "from", "into", "between", "under", "using", "across", "which"} or words[-1] in {"of", "in", "to", "and", "or", "for", "with", "by", "on", "at"}:
                continue
            if ph in STOP_PHRASES:
                continue
            phrase_counts[ph] += 1
            phrase_first.setdefault(ph, seg["id"])

    for ph, cnt in phrase_counts.most_common(30):
        if cnt >= 2 and ph not in candidates:
            candidates[ph] = {"source": ph, "acronym": None, "count": cnt, "first_segment": phrase_first[ph], "priority": "medium"}

    out = sorted(candidates.values(), key=lambda x: (0 if x["priority"] == "high" else 1, -x["count"], x["first_segment"]))
    return out[:40]


def segment_pdf_advanced(pdf_path: Path, sym_map: dict):
    fitz_mod = None
    try:
        import pymupdf as fitz_mod
    except ImportError:
        try:
            import fitz as fitz_mod
        except ImportError:
            fitz_mod = None

    if fitz_mod is None:
        return segment_pdf_fallback(pdf_path, sym_map)

    try:
        doc = fitz_mod.open(str(pdf_path))
    except Exception as exc:
        return [], {"input_file": str(pdf_path), "backend": "pymupdf", "status": "corrupt_or_unreadable", "error": str(exc), "total_pages": 0, "total_segments": 0, "ocr_recommended_pages": [], "pages": [], "warnings": [str(exc)]}, [], []

    if doc.is_encrypted and doc.needs_pass:
        doc.close()
        return [], {"input_file": str(pdf_path), "backend": "pymupdf", "status": "encrypted_requires_password", "total_pages": 0, "total_segments": 0, "ocr_recommended_pages": [], "pages": [], "warnings": ["PDF requires password."]}, [], []

    pages_data = []
    for idx in range(len(doc)):
        prim = extract_page_primitives_pymupdf(doc[idx], sym_map)
        prim["page"] = idx + 1
        pages_data.append(prim)
    doc.close()

    all_sz = [sz for pd in pages_data for sz in pd.get("font_sizes", [])]
    body_size = statistics.median(all_sz) if all_sz else 10.0
    headers_footers = detect_headers_footers_marginalia(pages_data)

    all_tables = []
    for pd in pages_data:
        all_tables.extend(extract_tables_from_page(pd))

    fig_cap_re = re.compile(r"^\s*(Fig\.?|Figure|Scheme|Chart|Table|Tab\.?|图|表)\s*([IVXLCDM]+|\d+(?:\.\d+)?[a-zA-Z]?)\b", re.I)
    eq_tag_re = re.compile(r"(?:^|\s)(\(\d+(?:\.\d+)?\)|\[\d+(?:\.\d+)?\])\s*$")
    fn_re = re.compile(r"^\s*(?:\^\{\d+\}|\d{1,2}\s+[A-Z\*]|[\*†‡§]|Email:|E-mail:|Corresponding author|Present address)", re.I)
    sec_num_re = re.compile(r"^\s*(?:(\d+(?:\.\d+){0,3})|([IVXLC]+))\s*[\.\)]\s+(.+)")
    named_sec_re = re.compile(r"^\s*(Abstract|Introduction|Background|Methods|Methodology|Experimental|Results|Discussion|Results and Discussion|Conclusion|Conclusions|Acknowledgments|Acknowledgements|References|Bibliography|Supporting Information|Appendix|摘\s*要|引\s*言|参\s*考\s*文\s*献)\s*:?\s*$", re.I)
    tag_only_re = re.compile(r"^\s*(?:\(\d+(?:\.\d+)?[a-zA-Z]?\)|\[\d+(?:\.\d+)?\])\s*$")

    thr = sym_map.get("garble_thresholds", {})
    min_chars = int(thr.get("min_text_chars_per_page", 40))
    min_img = float(thr.get("min_image_ratio_for_scan", 0.25))
    max_garble = float(thr.get("page_garble_ratio_ocr", 0.05))

    segments = []
    page_statuses = []
    ocr_pages = []
    warnings = []
    current_section = "Front Matter"
    in_references = False
    title_seen = False
    seq = 0

    for pd in pages_data:
        pno, W, H = pd["page"], pd["width"], pd["height"]
        # Merge standalone right-aligned equation tags (1) onto left equation line
        active = [b for b in pd["blocks"] if b.get("role") not in ("header", "footer", "marginalia", "table_body", "table", "caption")]
        drop_ids = set()
        for bt in active:
            if tag_only_re.match(bt["raw_text"]) and bt["bbox"][0] > 0.65 * W:
                cy_t = 0.5 * (bt["bbox"][1] + bt["bbox"][3])
                cands = [ba for ba in active if ba is not bt and id(ba) not in drop_ids and ba["bbox"][2] < bt["bbox"][0] and abs(0.5 * (ba["bbox"][1] + ba["bbox"][3]) - cy_t) <= 6.0 and re.search(r"[=+\-−×÷∑∫≈≤≥]", ba["raw_text"])]
                if cands:
                    host = max(cands, key=lambda x: x["bbox"][2])
                    ts = bt["raw_text"].strip()
                    host["raw_text"] = host["raw_text"].rstrip() + " " + ts
                    host["text"] = host["text"].rstrip() + " " + ts
                    host["bbox"] = [min(host["bbox"][0], bt["bbox"][0]), min(host["bbox"][1], bt["bbox"][1]), max(host["bbox"][2], bt["bbox"][2]), max(host["bbox"][3], bt["bbox"][3])]
                    drop_ids.add(id(bt))
        if drop_ids:
            pd["blocks"] = [b for b in pd["blocks"] if id(b) not in drop_ids]

        for b in pd["blocks"]:
            if b.get("role") in ("header", "footer", "marginalia", "table_body", "table", "caption"):
                continue
            if b["bbox"][1] > 0.72 * H and b["size"] <= 0.88 * body_size and fn_re.match(b["text"]):
                b["role"] = "footnote"

        ordered = sort_blocks_xy_cut([b for b in pd["blocks"] if b.get("role") not in ("header", "footer", "marginalia", "table_body")], W)

        p_area = max(1.0, W * H)
        img_area = sum(max(0.0, (im[2] - im[0]) * (im[3] - im[1])) for im in pd.get("images", []))
        img_ratio = round(min(1.0, img_area / p_area), 4)
        total_nw = sum(b["non_ws"] for b in ordered)
        total_gc = sum(b["garble"] for b in ordered)
        g_ratio = round(min(1.0, total_gc / max(1, total_nw)), 4)

        needs_ocr = (total_nw < min_chars and img_ratio >= min_img) or (total_nw < 15 and len(pd.get("images", [])) > 0) or (g_ratio >= max_garble)
        p_status = "ok"
        if total_nw < 15 and not pd.get("images"):
            p_status = "empty"
            warnings.append(f"Page {pno} is empty.")
        elif needs_ocr:
            p_status = "empty_or_scanned" if total_nw < min_chars else "garbled_font_layer"
            ocr_pages.append(pno)
            warnings.append(f"Page {pno} flagged {p_status} (non_ws={total_nw}, img_ratio={img_ratio}, garble={g_ratio}).")

        page_statuses.append({"page": pno, "printed_label": pd.get("printed_label"), "char_count": total_nw, "image_area_ratio": img_ratio, "garble_ratio": g_ratio, "status": p_status})

        if p_status == "empty_or_scanned":
            seq += 1
            segments.append({
                "id": f"P{seq:03d}", "page": pno, "printed_page": pd.get("printed_label"),
                "section_hint": current_section, "kind": "unreadable_page", "column": "full",
                "bbox": [0.0, 0.0, W, H], "raw_text": "", "text": "",
                "extracted_cues": {"numbers": [], "units": [], "citations": [], "refs": []},
                "garble_ratio": g_ratio,
            })
            continue

        for b in ordered:
            txt = b["text"].strip()
            raw = b["raw_text"].strip()
            role = b.get("role")
            if not txt:
                continue

            kind = "paragraph"
            if role == "table":
                kind = "table"
            elif role == "caption" or fig_cap_re.match(raw):
                kind = "caption"
            elif role == "footnote":
                kind = "footnote"
            elif pno == 1 and not title_seen and b["size"] >= 1.25 * body_size and b["bbox"][1] < 0.45 * H and len(raw) >= 12:
                kind = "title"
                title_seen = True
                current_section = "Title"
            elif eq_tag_re.search(raw) and len(raw) < 220 and not in_references and (b["bbox"][2] > 0.68 * W or "=" in raw):
                kind = "equation"
            elif named_sec_re.match(raw) or (len(raw) < 120 and "\n" not in raw and sec_num_re.match(raw) and (b["bold"] or b["size"] >= 1.04 * body_size)) or (b["bold"] and b["size"] >= 1.08 * body_size and 3 <= len(raw) <= 90 and not raw.endswith(".")):
                kind = "heading"
                current_section = txt[:120]
                in_references = bool(re.search(r"(References|Bibliography|参考文献)", txt, re.I))
            elif pno == 1 and re.match(r"^\s*Abstract\b", raw, re.I):
                kind = "abstract"
                current_section = "Abstract"
            elif in_references:
                kind = "reference"

            seq += 1
            seg_obj = {
                "id": f"P{seq:03d}",
                "page": pno,
                "printed_page": pd.get("printed_label"),
                "section_hint": current_section,
                "kind": kind,
                "column": b.get("column", "full"),
                "bbox": b["bbox"],
                "raw_text": raw,
                "text": txt,
                "extracted_cues": extract_cues(txt),
                "garble_ratio": round(min(1.0, b["garble"] / max(1, b["non_ws"])), 4),
            }
            if b.get("table_meta"):
                seg_obj["table_meta"] = b["table_meta"]
            segments.append(seg_obj)

    # Mark cross-column / cross-page sentence continuations across intervening footnotes/captions/tables (stopping at headings)
    for i, cur in enumerate(segments):
        if cur["kind"] != "paragraph" or re.search(r"[.!?。？！:：;；\"\')\]\d]$", cur["text"]):
            continue
        for j in range(i + 1, len(segments)):
            nxt = segments[j]
            if nxt["kind"] in ("heading", "title", "abstract", "reference", "unreadable_page"):
                break
            if nxt["kind"] == "paragraph":
                cur["continues_to_next"] = True
                nxt["continued_from_prev"] = True
                nxt["prev_context_tail"] = cur["text"][-160:]
                break

    report = {
        "input_file": str(pdf_path),
        "backend": "pymupdf_xycut_booktabs",
        "status": "partial_needs_ocr" if ocr_pages else "ok",
        "total_pages": len(pages_data),
        "total_segments": len(segments),
        "headers_footers_stripped": len(headers_footers),
        "tables_extracted": len(all_tables),
        "ocr_recommended_pages": ocr_pages,
        "pages": page_statuses,
        "warnings": warnings,
    }
    return segments, report, all_tables, headers_footers


def segment_pdf_fallback(pdf_path: Path, sym_map: dict):
    """Fallback when PyMuPDF is unavailable (pdfplumber -> pypdf -> pdftotext)."""
    pages, backend, warnings = [], None, []
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            pages = [{"page": i, "text": p.extract_text() or ""} for i, p in enumerate(pdf.pages, start=1)]
        backend = "pdfplumber"
    except Exception:
        pdftotext = shutil.which("pdftotext")
        if pdftotext:
            proc = subprocess.run([pdftotext, "-layout", str(pdf_path), "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.returncode == 0:
                raw_pages = proc.stdout.split("\f")
                if raw_pages and not raw_pages[-1].strip():
                    raw_pages.pop()
                pages = [{"page": i, "text": t} for i, t in enumerate(raw_pages, start=1)]
                backend = "pdftotext"
    if backend is None:
        raise RuntimeError("No PDF extraction backend available.")

    segments, page_statuses, ocr_pages = [], [], []
    seq = 0
    for pi in pages:
        pno, raw = pi["page"], re.sub(r"([A-Za-z])-\n([a-z])", r"\1\2", pi["text"].replace("\r\n", "\n"))
        nw = len(raw.strip())
        if nw < 15:
            ocr_pages.append(pno)
            page_statuses.append({"page": pno, "char_count": nw, "status": "empty_or_scanned"})
            seq += 1
            segments.append({"id": f"P{seq:03d}", "page": pno, "section_hint": "Unknown", "kind": "unreadable_page", "text": "", "extracted_cues": {"numbers": [], "units": [], "citations": [], "refs": []}})
            continue
        page_statuses.append({"page": pno, "char_count": nw, "status": "ok"})
        for blk in re.split(r"\n\s*\n+", raw):
            txt = re.sub(r"\s+", " ", blk).strip()
            if txt:
                seq += 1
                segments.append({"id": f"P{seq:03d}", "page": pno, "section_hint": "Body", "kind": "paragraph", "text": txt, "extracted_cues": extract_cues(txt)})

    report = {"input_file": str(pdf_path), "backend": backend, "status": "partial_needs_ocr" if ocr_pages else "ok", "total_pages": len(pages), "total_segments": len(segments), "ocr_recommended_pages": ocr_pages, "pages": page_statuses, "warnings": warnings}
    return segments, report, [], []


def segment_plaintext(input_path: Path):
    raw = input_path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    raw = re.sub(r"([A-Za-z])-\n([a-z])", r"\1\2", raw)
    segments = []
    cur_sec = "Front Matter"
    for seq, blk in enumerate([b.strip() for b in re.split(r"\n\s*\n+", raw) if b.strip()], start=1):
        txt = re.sub(r"\s+", " ", blk).strip()
        kind = "heading" if (len(txt) < 100 and re.match(r"^(?:#{1,6}\s+|\d+\.\s+[A-Z]|Abstract\b|Introduction\b)", txt, re.I)) else "paragraph"
        if kind == "heading":
            cur_sec = txt[:120]
        segments.append({"id": f"P{seq:03d}", "page": 1, "section_hint": cur_sec, "kind": kind, "text": txt, "extracted_cues": extract_cues(txt)})
    rep = {"input_file": str(input_path), "backend": "plaintext", "status": "ok", "total_pages": 1, "total_segments": len(segments), "ocr_recommended_pages": [], "pages": [{"page": 1, "char_count": len(raw.strip()), "status": "ok"}], "warnings": []}
    return segments, rep, [], []


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract and segment scientific paper into layout-ordered bilingual-ready blocks.")
    parser.add_argument("--input", required=True, help="Path to PDF, Markdown, or text file")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sym_map = load_symbol_map()

    if in_path.suffix.lower() == ".pdf":
        segments, report, tables, headers_footers = segment_pdf_advanced(in_path, sym_map)
    else:
        segments, report, tables, headers_footers = segment_plaintext(in_path)

    glossary_cands = mine_glossary_candidates(segments)
    (out_dir / "segments.json").write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "extraction_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "glossary_candidates.json").write_text(json.dumps({"candidates": glossary_cands}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "headers_footers.json").write_text(json.dumps(headers_footers, ensure_ascii=False, indent=2), encoding="utf-8")
    if tables:
        (out_dir / "tables.json").write_text(json.dumps(tables, ensure_ascii=False, indent=2), encoding="utf-8")

    preview_lines = [f"# Source Segments Preview ({in_path.name})\n"]
    for s in segments:
        ctx = f"\n> *(continues from prev: ...{s['prev_context_tail']})*" if s.get("continued_from_prev") else ""
        preview_lines.append(f"### [{s['id']}] Page {s['page']} | {s['kind']} | {s['section_hint']}{ctx}\n{s['text']}\n")
    (out_dir / "source_preview.md").write_text("\n".join(preview_lines), encoding="utf-8")

    print(json.dumps({
        "status": report.get("status"),
        "backend": report.get("backend"),
        "total_pages": report.get("total_pages"),
        "total_segments": report.get("total_segments"),
        "tables_extracted": report.get("tables_extracted", 0),
        "headers_footers_stripped": report.get("headers_footers_stripped", 0),
        "glossary_candidates": len(glossary_cands),
        "ocr_recommended_pages": report.get("ocr_recommended_pages", []),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
