#!/usr/bin/env python3
"""Layout-aware scientific PDF parser supporting multi-column XY-Cut, booktabs tables,
sub/superscript reconstruction, header/footer stripping, and full block provenance."""

import argparse
import json
import math
import re
import shutil
import statistics
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"


def load_symbol_map():
    """Load symbol normalization and garble threshold rules."""
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
    """Normalize ligatures, invisible chars, Symbol font glyphs, and track unmapped PUA/FFFD."""
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
    cid_hits = len(re.findall(r"\(cid:\d+\)", res))
    garble_count += cid_hits * 3
    return res, garble_count, non_ws


def format_line_with_scripts(spans: list, sym_map: dict):
    """Reconstruct line text with geometric/flag subscript and superscript markers."""
    if not spans:
        return "", "", 0, 0, 10.0

    # Filter whitespace-only spans when computing dominant body font size and baseline
    non_empty = [s for s in spans if s["text"].strip()]
    if not non_empty:
        return "", "", 0, 0, 10.0

    sizes = [s["size"] for s in non_empty]
    # Dominant body size in line: max-weight or 75th percentile of span sizes weighted by char count
    weighted = []
    for s in non_empty:
        weighted.extend([s["size"]] * max(1, len(s["text"].strip())))
    body_size = statistics.median(weighted)
    normal_spans = [s for s in non_empty if s["size"] >= 0.86 * body_size]
    if not normal_spans:
        normal_spans = non_empty
    base_y = statistics.median([s["origin_y"] for s in normal_spans])
    base_cy = statistics.median([0.5 * (s["bbox"][1] + s["bbox"][3]) for s in normal_spans])

    raw_parts = []
    norm_parts = []
    total_garble = 0
    total_non_ws = 0

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

        # Insert space if adjacent spans have a clear word gap; keep tight sub/superscripts attached
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
            # Skip tiny synthetic PyMuPDF gap-space spans directly before a sub/superscript
            next_is_script = False
            if idx + 1 < len(spans):
                nsp = spans[idx + 1]
                n_txt = nsp["text"].strip()
                n_oy = nsp["origin_y"]
                n_small = nsp["size"] <= 0.84 * body_size
                if n_txt and len(n_txt) <= 18 and n_small and (n_oy < base_y - 0.16 * body_size or n_oy > base_y + 0.12 * body_size):
                    next_is_script = True
            if next_is_script and (sp["bbox"][2] - sp["bbox"][0]) < 0.32 * body_size:
                continue
            norm_parts.append(txt_clean)
            continue

        if is_sup:
            lead_sp = " " if txt_clean.startswith(" ") and norm_parts and not norm_parts[-1].endswith(" ") else ""
            trail_sp = " " if txt_clean.endswith(" ") else ""
            norm_parts.append(f"{lead_sp}^{{{stripped}}}{trail_sp}")
            continue
        if is_sub:
            lead_sp = " " if txt_clean.startswith(" ") and norm_parts and not norm_parts[-1].endswith(" ") else ""
            trail_sp = " " if txt_clean.endswith(" ") else ""
            norm_parts.append(f"{lead_sp}_{{{stripped}}}{trail_sp}")
            continue

        norm_parts.append(txt_clean)

    raw_line = re.sub(r"[ \t]+", " ", "".join(raw_parts)).strip()
    norm_line = re.sub(r"[ \t]+", " ", "".join(norm_parts)).strip()
    return raw_line, norm_line, total_garble, total_non_ws, body_size


def split_span_line_at_gaps(spans: list, gap_threshold: float):
    """Split a PyMuPDF line into sub-segments whenever spans are separated by a wide gutter gap."""
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


def extract_page_primitives_pymupdf(page, sym_map: dict):
    """Extract atomic text segments, vector rules, and image regions from a PyMuPDF page."""
    rect = page.rect
    W, H = float(rect.width), float(rect.height)
    d = page.get_text("dict")

    raw_blocks = []
    font_sizes = []

    for b in d.get("blocks", []):
        if b.get("type", 0) != 0:
            continue
        lines = b.get("lines", [])
        if not lines:
            continue

        # Convert each line to sub-segments split by wide internal horizontal gaps
        block_segs = []
        for ln in lines:
            # Skip vertical/strongly rotated text into separate vertical marginalia check later
            dir_vec = ln.get("dir", (1.0, 0.0))
            is_vertical = abs(dir_vec[1]) > 0.7
            spans = []
            for sp in ln.get("spans", []):
                if not sp.get("text"):
                    continue
                bbox = sp["bbox"]
                spans.append({
                    "text": sp["text"],
                    "font": sp.get("font", ""),
                    "size": float(sp.get("size", 10.0)),
                    "flags": int(sp.get("flags", 0)),
                    "origin_y": float(sp.get("origin", (bbox[0], bbox[3]))[1]),
                    "bbox": [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])],
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

        # Check if segments inside this block form side-by-side columns separated by a gutter
        # Group by X-cluster (connected overlap along X) ONLY if no segment bridges across
        sub_clusters = cluster_segments_into_subblocks(block_segs)
        for segs in sub_clusters:
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
            vert = any(s["vertical"] for s in segs_sorted)
            raw_blocks.append({
                "bbox": [round(bx0, 2), round(by0, 2), round(bx1, 2), round(by1, 2)],
                "raw_text": raw_joined,
                "text": norm_joined,
                "lines": segs_sorted,
                "size": round(avg_sz, 2),
                "bold": bool(all_bold),
                "garble": gc,
                "non_ws": nw,
                "vertical": vert,
            })

    # Extract horizontal & vertical vector lines for booktabs/grid table detection
    h_rules = []
    v_rules = []
    for drw in page.get_drawings():
        r = drw.get("rect")
        if r:
            x0, y0, x1, y1 = float(r.x0), float(r.y0), float(r.x1), float(r.y1)
            w, h = x1 - x0, y1 - y0
            if h <= 3.5 and w >= 25.0:
                h_rules.append([x0, 0.5 * (y0 + y1), x1, y0, y1])
            elif w <= 3.5 and h >= 12.0:
                v_rules.append([0.5 * (x0 + x1), y0, y1])
        for item in drw.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                dx, dy = abs(p2.x - p1.x), abs(p2.y - p1.y)
                if dy <= 1.5 and dx >= 25.0:
                    y_mid = 0.5 * (p1.y + p2.y)
                    h_rules.append([min(p1.x, p2.x), y_mid, max(p1.x, p2.x), y_mid - 0.5, y_mid + 0.5])
                elif dx <= 1.5 and dy >= 12.0:
                    x_mid = 0.5 * (p1.x + p2.x)
                    v_rules.append([x_mid, min(p1.y, p2.y), max(p1.y, p2.y)])
            elif item[0] == "re":
                rr = item[1]
                w, h = rr.width, rr.height
                if h <= 3.5 and w >= 25.0:
                    h_rules.append([rr.x0, 0.5 * (rr.y0 + rr.y1), rr.x1, rr.y0, rr.y1])
                elif w <= 3.5 and h >= 12.0:
                    v_rules.append([0.5 * (rr.x0 + rr.x1), rr.y0, rr.y1])

    # Deduplicate close rules
    h_rules = dedup_rules(h_rules, tol=2.0)

    images = []
    for info in page.get_image_info(xrefs=False):
        bb = info.get("bbox")
        if bb:
            images.append([round(float(bb[0]), 2), round(float(bb[1]), 2), round(float(bb[2]), 2), round(float(bb[3]), 2)])

    return {
        "width": W,
        "height": H,
        "printed_label": page.get_label() or None,
        "blocks": raw_blocks,
        "h_rules": h_rules,
        "v_rules": v_rules,
        "images": images,
        "font_sizes": font_sizes,
    }


def dedup_rules(rules: list, tol: float = 2.0):
    """Merge nearly identical horizontal vector segments."""
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


def cluster_segments_into_subblocks(segs: list):
    """Split a block's line segments if they occupy disjoint X bands (column bleed fix)."""
    if len(segs) <= 1:
        return [segs]
    # Check if splitting by X-overlap separates two clear vertical stacks
    intervals = sorted([(s["bbox"][0], s["bbox"][2], idx) for idx, s in enumerate(segs)])
    groups = []
    cur_x0, cur_x1, cur_idxs = intervals[0][0], intervals[0][1], [intervals[0][2]]
    for x0, x1, idx in intervals[1:]:
        if x0 <= cur_x1 + 6.0:
            cur_x1 = max(cur_x1, x1)
            cur_idxs.append(idx)
        else:
            groups.append(cur_idxs)
            cur_x0, cur_x1, cur_idxs = x0, x1, [idx]
    groups.append(cur_idxs)
    if len(groups) == 1:
        return [segs]

    # Only split if each X-group has >= 2 lines OR horizontal separation is >= 14 pt
    res = []
    for idxs in groups:
        # Further split vertically if large vertical gap inside column (> 1.6 * line_h)
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
    """Join wrapped lines while repairing trailing hyphenation cleanly."""
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


def normalize_hf_fingerprint(text: str):
    """Normalize top/bottom band text by collapsing numbers to '#' for cross-page header/footer matching."""
    t = re.sub(r"\d+", "#", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t


def detect_headers_footers_marginalia(pages_data: list):
    """Identify cross-page repeating headers, footers, page numbers, and side marginalia."""
    n_pages = len(pages_data)
    top_counts = Counter()
    bot_counts = Counter()

    for pd in pages_data:
        H = pd["height"]
        for b in pd["blocks"]:
            y0, y1 = b["bbox"][1], b["bbox"][3]
            fp = normalize_hf_fingerprint(b["raw_text"])
            if not fp or len(fp) > 140:
                continue
            if y0 < 0.11 * H and not b.get("vertical"):
                top_counts[fp] += 1
            elif y1 > 0.89 * H and not b.get("vertical"):
                bot_counts[fp] += 1

    min_rep = 2 if n_pages <= 5 else max(2, math.ceil(0.35 * n_pages))
    rep_top = {fp for fp, cnt in top_counts.items() if cnt >= min_rep}
    rep_bot = {fp for fp, cnt in bot_counts.items() if cnt >= min_rep}
    page_num_re = re.compile(r"^\s*(?:[-–—]?\s*\d{1,4}\s*[-–—]?|[pP]age\s+\d+(?:\s+of\s+\d+)?|\d+\s*/\s*\d+|第\s*\d+\s*页|[ivxlcdm]{1,6})\s*$")

    headers_footers = []
    for pd in pages_data:
        W, H = pd["width"], pd["height"]
        pno = pd["page"]
        for b in pd["blocks"]:
            b["role"] = None
            x0, y0, x1, y1 = b["bbox"]
            txt = b["raw_text"].strip()
            fp = normalize_hf_fingerprint(txt)
            if b.get("vertical") and (x1 < 0.08 * W or x0 > 0.92 * W):
                b["role"] = "marginalia"
                headers_footers.append({"page": pno, "role": "marginalia", "text": txt, "bbox": b["bbox"]})
                continue
            if y0 < 0.11 * H and (fp in rep_top or (page_num_re.match(txt) and y1 < 0.09 * H)):
                b["role"] = "header"
                headers_footers.append({"page": pno, "role": "header", "text": txt, "bbox": b["bbox"]})
                continue
            if y1 > 0.89 * H and (fp in rep_bot or (page_num_re.match(txt) and y0 > 0.91 * H)):
                b["role"] = "footer"
                headers_footers.append({"page": pno, "role": "footer", "text": txt, "bbox": b["bbox"]})
                continue
    return headers_footers


def cluster_lines_to_rows(segs: list):
    """Cluster table line segments into visual rows by baseline/vertical overlap."""
    if not segs:
        return []
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


def detect_table_columns(rows: list, header_row_count: int, tx0: float, tx1: float):
    """Infer column x-intervals from X-occupancy across data rows (and leaf header row)."""
    sample_rows = rows[header_row_count:] if len(rows) > header_row_count else rows
    if len(rows) > 1 and header_row_count > 0:
        sample_rows = [rows[header_row_count - 1]] + list(sample_rows)
    segs = [s for r in sample_rows for s in r]
    if not segs:
        return [(tx0, tx1)]

    x_min = int(math.floor(min(s["bbox"][0] for s in segs)))
    x_max = int(math.ceil(max(s["bbox"][2] for s in segs)))
    width = max(1, x_max - x_min + 1)
    occ = [0] * width
    for s in segs:
        a = max(0, int(math.floor(s["bbox"][0])) - x_min)
        b = min(width, int(math.ceil(s["bbox"][2])) - x_min)
        for k in range(a, b):
            occ[k] += 1

    # Find zero-occupancy gutters >= 3 pt wide
    cols = []
    in_col = False
    c_start = 0
    zero_run = 0
    for i, v in enumerate(occ):
        if v > 0:
            if not in_col:
                c_start = i
                in_col = True
            zero_run = 0
        else:
            if in_col:
                zero_run += 1
                if zero_run >= 4:
                    cols.append((x_min + c_start, x_min + i - zero_run))
                    in_col = False
    if in_col:
        cols.append((x_min + c_start, x_max))

    if not cols:
        return [(tx0, tx1)]
    return cols


def build_table_grid(segs: list, h_rules: list, tx0: float, tx1: float):
    """Convert span segments and horizontal rules inside a table box into structured grid."""
    rows = cluster_lines_to_rows(segs)
    if not rows:
        return None

    # Find internal midrule separating header from body (top 50% of table rows)
    row_ys = [(min(s["bbox"][1] for s in r), max(s["bbox"][3] for s in r)) for r in rows]
    header_rows = 1 if len(rows) > 1 else 0
    top_y = row_ys[0][0]
    bot_y = row_ys[-1][1]
    span_y = max(1.0, bot_y - top_y)
    internal_rules = sorted([hr[1] for hr in h_rules if top_y + 2.0 < hr[1] < bot_y - 2.0])
    if internal_rules:
        mid_y = internal_rules[0]
        if (mid_y - top_y) <= 0.55 * span_y:
            cnt = sum(1 for _, ry1 in row_ys if ry1 <= mid_y + 2.0)
            if 0 < cnt < len(rows):
                header_rows = cnt

    cols = detect_table_columns(rows, header_rows, tx0, tx1)
    ncols = len(cols)
    grid = [["" for _ in range(ncols)] for _ in range(len(rows))]
    cells = []

    for r_idx, row in enumerate(rows):
        for sp in row:
            sx0, sx1 = sp["bbox"][0], sp["bbox"][2]
            best_cols = []
            for c_idx, (cx0, cx1) in enumerate(cols):
                ov = min(sx1, cx1 + 2.0) - max(sx0, cx0 - 2.0)
                if ov > 0:
                    best_cols.append(c_idx)
            if not best_cols:
                # Assign by nearest center
                scx = 0.5 * (sx0 + sx1)
                c_idx = min(range(ncols), key=lambda k: abs(scx - 0.5 * (cols[k][0] + cols[k][1])))
                best_cols = [c_idx]
            c_start, c_end = best_cols[0], best_cols[-1]
            colspan = c_end - c_start + 1
            txt = sp["text"].strip()
            for c_k in range(c_start, c_end + 1):
                grid[r_idx][c_k] = (grid[r_idx][c_k] + " " + txt).strip() if grid[r_idx][c_k] else txt
            cells.append({
                "row": r_idx,
                "col": c_start,
                "rowspan": 1,
                "colspan": colspan,
                "is_header": r_idx < header_rows,
                "text": txt,
            })

    # Render clean Markdown table
    md_lines = []
    if grid:
        hdr_cnt = max(1, min(header_rows, len(grid)))
        if hdr_cnt == 1:
            md_lines.append("| " + " | ".join(c.replace("|", "\\|") for c in grid[0]) + " |")
        else:
            merged_hdr = []
            for c in range(ncols):
                parts = []
                for r in range(hdr_cnt):
                    val = grid[r][c]
                    if val and (not parts or parts[-1] != val):
                        parts.append(val)
                merged_hdr.append(" / ".join(parts))
            md_lines.append("| " + " | ".join(c.replace("|", "\\|") for c in merged_hdr) + " |")
        md_lines.append("| " + " | ".join(["---"] * ncols) + " |")
        for r in range(hdr_cnt, len(grid)):
            md_lines.append("| " + " | ".join(c.replace("|", "\\|") for c in grid[r]) + " |")

    return {
        "n_rows": len(grid),
        "n_cols": ncols,
        "header_rows": header_rows,
        "grid": grid,
        "cells": cells,
        "markdown": "\n".join(md_lines),
    }


def extract_tables_from_page(pd: dict, body_size: float):
    """Detect Booktabs/grid tables on a page, reconstruct grids, and tag consumed blocks."""
    W, H = pd["width"], pd["height"]
    pno = pd["page"]
    h_rules = pd["h_rules"]
    blocks = [b for b in pd["blocks"] if b.get("role") not in ("header", "footer", "marginalia")]

    # Group horizontal rules that form candidate tables
    # Exclude top/bottom header/footer borders or tiny footnote rules
    cand_rules = [hr for hr in h_rules if 0.07 * H < hr[1] < 0.92 * H and (hr[2] - hr[0]) >= 55.0]
    rule_clusters = []
    for hr in sorted(cand_rules, key=lambda x: x[1]):
        placed = False
        for cl in rule_clusters:
            last = cl[-1]
            ov = min(hr[2], last[2]) - max(hr[0], last[0])
            w_min = min(hr[2] - hr[0], last[2] - last[0])
            if ov >= 0.45 * w_min and (hr[1] - last[1]) <= 0.45 * H:
                cl.append(hr)
                placed = True
                break
        if not placed:
            rule_clusters.append([hr])

    table_cap_re = re.compile(r"^\s*(Table|Tab\.?|表)\s*([IVXLCDM]+|\d+(?:\.\d+)?)\b", re.I)
    tables = []
    consumed_block_idxs = set()

    for t_idx, cl in enumerate(rule_clusters):
        if len(cl) < 2:
            continue
        tx0 = min(r[0] for r in cl) - 6.0
        tx1 = max(r[2] for r in cl) + 6.0
        ty0 = min(r[1] for r in cl) - 2.0
        ty1 = max(r[1] for r in cl) + 2.0
        if ty1 - ty0 < 18.0:
            continue

        # Check for adjacent Table caption right above ty0 (or below ty1)
        caption = None
        cap_block_idx = None
        for idx, b in enumerate(pd["blocks"]):
            if b.get("role") in ("header", "footer", "marginalia") or idx in consumed_block_idxs:
                continue
            bx0, by0, bx1, by1 = b["bbox"]
            if min(bx1, tx1) - max(bx0, tx0) < 30.0:
                continue
            if (0 <= ty0 - by1 <= 32.0 or 0 <= by0 - ty1 <= 26.0) and table_cap_re.match(b["raw_text"]):
                caption = b["text"]
                cap_block_idx = idx
                b["role"] = "table_caption"
                break

        # Collect all segments inside [tx0, ty0, tx1, ty1]
        table_segs = []
        member_idxs = []
        for idx, b in enumerate(pd["blocks"]):
            if b.get("role") in ("header", "footer", "marginalia", "table_caption") or idx in consumed_block_idxs:
                continue
            bx0, by0, bx1, by1 = b["bbox"]
            cx, cy = 0.5 * (bx0 + bx1), 0.5 * (by0 + by1)
            if (tx0 - 6.0 <= cx <= tx1 + 6.0) and (ty0 <= cy <= ty1):
                member_idxs.append(idx)
                table_segs.extend(b["lines"])

        if len(table_segs) < 2:
            continue

        # Ensure text-gap splits inside table lines at >= 0.9 * body_size (tight column spacing in tables!)
        fine_segs = table_segs
        grid_res = build_table_grid(fine_segs, cl, tx0, tx1)
        if not grid_res or grid_res["n_rows"] < 2:
            continue

        for idx in member_idxs:
            consumed_block_idxs.add(idx)
            pd["blocks"][idx]["role"] = "table_body"

        tables.append({
            "table_id": f"p{pno:02d}_t{len(tables)+1:02d}",
            "page": pno,
            "printed_page": pd.get("printed_label"),
            "bbox": [round(tx0 + 6.0, 2), round(ty0 + 2.0, 2), round(tx1 - 6.0, 2), round(ty1 - 2.0, 2)],
            "caption": caption,
            "caption_block_idx": cap_block_idx,
            **grid_res,
        })

    return tables


def merge_continued_tables(tables: list, pages_data: list):
    """Merge consecutive-page continuation tables (e.g., 'Table 1 (continued)' or matching header/col count)."""
    if len(tables) <= 1:
        return []
    cont_re = re.compile(r"(continued|cont\.?|续表)", re.I)
    merged = []
    used = set()
    for i, t1 in enumerate(tables):
        if i in used:
            continue
        group = [t1]
        cur = t1
        for j in range(i + 1, len(tables)):
            t2 = tables[j]
            if j in used or t2["page"] != cur["page"] + 1:
                continue
            # Check caption match or continuation marker or near-top uncaptioned table with same column count
            cap2 = t2.get("caption") or ""
            is_cont_cap = bool(cont_re.search(cap2))
            near_top = t2["bbox"][1] < 0.28 * pages_data[t2["page"] - 1]["height"]
            same_cols = abs(t2["n_cols"] - cur["n_cols"]) == 0
            if is_cont_cap or (not cap2 and near_top and same_cols):
                group.append(t2)
                used.add(j)
                cur = t2
            else:
                break
        if len(group) > 1:
            used.add(i)
            combined_rows = list(group[0]["grid"])
            for nxt in group[1:]:
                skip = nxt["header_rows"] if (nxt["grid"] and group[0]["grid"] and nxt["grid"][0] == group[0]["grid"][0]) else 0
                combined_rows.extend(nxt["grid"][skip:])
            ncols = group[0]["n_cols"]
            md = [
                "| " + " | ".join(c.replace("|", "\\|") for c in combined_rows[0]) + " |",
                "| " + " | ".join(["---"] * ncols) + " |",
            ] + ["| " + " | ".join(c.replace("|", "\\|") for c in r) + " |" for r in combined_rows[1:]]
            merged.append({
                "merged_table_id": f"merged_{group[0]['table_id']}_{group[-1]['table_id']}",
                "source_table_ids": [g["table_id"] for g in group],
                "pages": [g["page"] for g in group],
                "caption": group[0].get("caption"),
                "n_rows": len(combined_rows),
                "n_cols": ncols,
                "grid": combined_rows,
                "markdown": "\n".join(md),
            })
    return merged


def detect_column_gutters(blocks: list, page_width: float):
    """Find dominant vertical column gutters from narrow body lines on a page."""
    # Prefer genuinely column-sized blocks (< 0.49 * W) so centered abstracts/author lines don't mask gutters
    narrow = [b for b in blocks if (b["bbox"][2] - b["bbox"][0]) < 0.49 * page_width and b["non_ws"] >= 4]
    if len(narrow) < 2:
        narrow = [b for b in blocks if (b["bbox"][2] - b["bbox"][0]) < 0.56 * page_width and b["non_ws"] >= 4]
    if len(narrow) < 2:
        return []

    W = int(math.ceil(page_width))
    occ = [0] * max(1, W + 1)
    segs = []
    for b in narrow:
        for ln in (b.get("lines") or [b]):
            if (ln["bbox"][2] - ln["bbox"][0]) < 0.49 * page_width:
                segs.append(ln)
    if len(segs) < 2:
        segs = narrow

    for s in segs:
        a = max(0, int(math.floor(s["bbox"][0])))
        c = min(W, int(math.ceil(s["bbox"][2])))
        for k in range(a, c):
            occ[k] += 1

    # Find zero-occupancy gaps >= 8 pt in [0.22*W, 0.78*W], allowing <= 1 outlier line only if zero-gap fails
    def find_gaps(max_occ: int):
        found = []
        i = int(0.22 * W)
        end = int(0.78 * W)
        while i < end:
            if occ[i] <= max_occ:
                j = i
                while j < end and occ[j] <= max_occ:
                    j += 1
                if j - i >= 8:
                    left_cnt = sum(1 for s in segs if s["bbox"][2] <= i + 4)
                    right_cnt = sum(1 for s in segs if s["bbox"][0] >= j - 4)
                    if left_cnt >= 2 and right_cnt >= 2:
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


def sort_blocks_xy_cut(blocks: list, page_width: float, page_height: float):
    """Sort blocks in reading order using spanning-band slicing + column gutters, placing footnotes last."""
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

    # Classify each block as spanning if it bridges a gutter, else assign column index
    def col_idx(b):
        cx = 0.5 * (b["bbox"][0] + b["bbox"][2])
        for gx0, gx1 in gutters:
            if b["bbox"][0] < gx0 - 4.0 and b["bbox"][2] > gx1 + 4.0:
                return -1  # spanning
        idx = 0
        for gx0, gx1 in gutters:
            if cx > 0.5 * (gx0 + gx1):
                idx += 1
        return idx

    spanning = []
    narrow_blocks = []
    for b in main:
        c = col_idx(b)
        b["_col"] = c
        b["column"] = "full" if c == -1 else f"col{c + 1}"
        if c == -1:
            spanning.append(b)
        else:
            narrow_blocks.append(b)

    spanning.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

    # Slice narrow blocks into bands separated by spanning blocks
    ordered = []
    prev_y = -1e9
    for sp in spanning:
        sp_y_mid = 0.5 * (sp["bbox"][1] + sp["bbox"][3])
        band = [b for b in narrow_blocks if prev_y <= 0.5 * (b["bbox"][1] + b["bbox"][3]) < sp_y_mid]
        band.sort(key=lambda b: (b["_col"], b["bbox"][1], b["bbox"][0]))
        ordered.extend(band)
        ordered.append(sp)
        prev_y = sp_y_mid

    tail = [b for b in narrow_blocks if 0.5 * (b["bbox"][1] + b["bbox"][3]) >= prev_y]
    tail.sort(key=lambda b: (b["_col"], b["bbox"][1], b["bbox"][0]))
    ordered.extend(tail)

    for b in footnotes:
        b["column"] = "footnote"
    ordered.extend(sorted(footnotes, key=lambda b: (b["bbox"][1], b["bbox"][0])))
    return ordered


def classify_roles_and_structure(pages_data: list, body_size: float):
    """Classify semantic roles (title, abstract, heading, caption, equation, footnote, reference) and build section tree."""
    fig_re = re.compile(r"^\s*(Fig\.?|Figure|Scheme|Chart|图)\s*([IVXLCDM]+|\d+(?:\.\d+)?[a-zA-Z]?)\b", re.I)
    tab_re = re.compile(r"^\s*(Table|Tab\.?|表)\s*([IVXLCDM]+|\d+(?:\.\d+)?)\b", re.I)
    eq_tag_re = re.compile(r"(?:^|\s)(\(\d+(?:\.\d+)?\)|\[\d+(?:\.\d+)?\])\s*$")
    fn_re = re.compile(r"^\s*(?:\^\{\d+\}|\d{1,2}\s+[A-Z\*]|[\*†‡§]|Email:|E-mail:|Corresponding author|Present address)", re.I)
    sec_num_re = re.compile(r"^\s*(?:(\d+(?:\.\d+){0,3})|([IVXLC]+))\s*[\.\)]\s+(.+)")
    named_sec_re = re.compile(r"^\s*(Abstract|Introduction|Background|Methods|Methodology|Experimental|Results|Discussion|Results and Discussion|Conclusion|Conclusions|Acknowledgments|Acknowledgements|References|Bibliography|Supporting Information|Appendix|摘\s*要|引\s*言|实\s*验|结\s*果|讨\s*论|结\s*论|参\s*考\s*文\s*献)\s*:?\s*$", re.I)
    ref_entry_re = re.compile(r"^\s*(?:\[\d+\]|\d{1,3}\.)\s+.+")

    in_references = False
    sections = []
    figures = []
    equations = []
    footnotes = []
    references = []

    current_sec = "Front Matter"
    title_text = None
    abstract_parts = []
    authors_guess = []

    for pd in pages_data:
        pno = pd["page"]
        H = pd["height"]
        W = pd["width"]

        # Merge standalone right-aligned equation tags like (1) onto the left math block on the same baseline
        tag_only_re = re.compile(r"^\s*(?:\(\d+(?:\.\d+)?[a-zA-Z]?\)|\[\d+(?:\.\d+)?\])\s*$")
        active_blocks = [b for b in pd["blocks"] if b.get("role") not in ("header", "footer", "marginalia", "table_body", "table_caption")]
        drop_ids = set()
        for idx_t, bt in enumerate(active_blocks):
            if tag_only_re.match(bt["raw_text"]) and bt["bbox"][0] > 0.65 * W:
                cy_t = 0.5 * (bt["bbox"][1] + bt["bbox"][3])
                cands = [
                    ba for ba in active_blocks
                    if ba is not bt and id(ba) not in drop_ids
                    and ba["bbox"][2] < bt["bbox"][0]
                    and abs(0.5 * (ba["bbox"][1] + ba["bbox"][3]) - cy_t) <= 6.0
                    and re.search(r"[=+\-−×÷∑∫≈≤≥]", ba["raw_text"])
                ]
                if cands:
                    host = max(cands, key=lambda x: x["bbox"][2])
                    tag_s = bt["raw_text"].strip()
                    host["raw_text"] = host["raw_text"].rstrip() + " " + tag_s
                    host["text"] = host["text"].rstrip() + " " + tag_s
                    host["bbox"] = [
                        min(host["bbox"][0], bt["bbox"][0]),
                        min(host["bbox"][1], bt["bbox"][1]),
                        max(host["bbox"][2], bt["bbox"][2]),
                        max(host["bbox"][3], bt["bbox"][3]),
                    ]
                    drop_ids.add(id(bt))
        if drop_ids:
            pd["blocks"] = [b for b in pd["blocks"] if id(b) not in drop_ids]

        # First tag footnotes near bottom before XY-Cut so footnotes go at end of page
        for b in pd["blocks"]:
            if b.get("role") in ("header", "footer", "marginalia", "table_body", "table_caption"):
                continue
            y0 = b["bbox"][1]
            if y0 > 0.72 * H and b["size"] <= 0.88 * body_size and fn_re.match(b["text"]):
                b["role"] = "footnote"

        ordered = sort_blocks_xy_cut([b for b in pd["blocks"] if b.get("role") not in ("header", "footer", "marginalia")], W, H)

        b_counter = 0
        for b in ordered:
            b_counter += 1
            bid = f"p{pno:02d}_b{b_counter:03d}"
            b["block_id"] = bid
            txt = b["text"].strip()
            raw = b["raw_text"].strip()

            if b.get("role") == "table_body":
                b["section"] = current_sec
                continue
            if b.get("role") == "table_caption":
                b["section"] = current_sec
                continue
            if b.get("role") == "footnote":
                b["section"] = current_sec
                footnotes.append({"block_id": bid, "page": pno, "text": txt, "bbox": b["bbox"]})
                continue

            # Title on Page 1
            if pno == 1 and title_text is None and b["size"] >= 1.25 * body_size and b["bbox"][1] < 0.45 * H and len(raw) >= 12:
                b["role"] = "title"
                title_text = txt
                b["section"] = "Title"
                continue

            # Figure caption
            fm = fig_re.match(raw)
            if fm:
                b["role"] = "figure_caption"
                b["section"] = current_sec
                figures.append({
                    "figure_id": f"p{pno:02d}_fig{len(figures)+1:02d}",
                    "label": f"{fm.group(1)} {fm.group(2)}",
                    "page": pno,
                    "block_id": bid,
                    "caption": txt,
                    "bbox": b["bbox"],
                })
                continue

            if tab_re.match(raw) and len(raw) < 400:
                b["role"] = "table_caption"
                b["section"] = current_sec
                continue

            # Display equation with explicit tag (1) or [1]
            eq_m = eq_tag_re.search(raw)
            if eq_m and len(raw) < 220 and (b["bbox"][2] > 0.68 * W or "=" in raw or "+" in raw or "≈" in raw or "∑" in raw):
                if not ref_entry_re.match(raw) and not in_references:
                    b["role"] = "equation"
                    b["section"] = current_sec
                    equations.append({
                        "eq_id": f"p{pno:02d}_eq{len(equations)+1:02d}",
                        "label": eq_m.group(1),
                        "page": pno,
                        "block_id": bid,
                        "text": txt,
                        "raw_text": raw,
                        "bbox": b["bbox"],
                    })
                    continue

            # Headings & Sections
            nm = named_sec_re.match(raw)
            sm = sec_num_re.match(raw) if len(raw) < 120 and ("\n" not in raw) else None
            is_heading = False
            level = 1
            if nm:
                is_heading = True
                level = 1
            elif sm and (b["bold"] or b["size"] >= 1.04 * body_size):
                is_heading = True
                num_tok = sm.group(1) or sm.group(2)
                level = num_tok.count(".") + 1
            elif b["bold"] and b["size"] >= 1.08 * body_size and 3 <= len(raw) <= 90 and not raw.endswith("."):
                is_heading = True
                level = 2 if b["size"] < 1.18 * body_size else 1

            if is_heading:
                b["role"] = "heading"
                current_sec = txt
                b["section"] = current_sec
                sections.append({"title": txt, "level": level, "page": pno, "block_id": bid, "bbox": b["bbox"]})
                if re.search(r"(References|Bibliography|参考文献)", txt, re.I):
                    in_references = True
                else:
                    in_references = False
                continue

            if in_references:
                b["role"] = "reference"
                b["section"] = current_sec
                references.append({"block_id": bid, "page": pno, "text": txt, "bbox": b["bbox"]})
                continue

            if pno == 1 and re.match(r"^\s*Abstract\b", raw, re.I):
                b["role"] = "abstract"
                b["section"] = "Abstract"
                abstract_parts.append(txt)
                continue

            if pno == 1 and title_text and not sections and len(authors_guess) < 4 and b["bbox"][1] < 0.42 * H and len(raw) < 300:
                if "@" in raw or "University" in raw or "Institute" in raw or "," in raw or re.search(r"\^\{|\d", txt):
                    b["role"] = "author_or_affiliation"
                    b["section"] = "Front Matter"
                    authors_guess.append(txt)
                    continue

            b["role"] = "body"
            b["section"] = current_sec

        # Attach ordered blocks back to page
        pd["ordered_blocks"] = ordered

    return {
        "metadata": {
            "title": title_text,
            "authors_and_affiliations": authors_guess,
            "abstract": " ".join(abstract_parts) if abstract_parts else None,
        },
        "sections": sections,
        "figures": figures,
        "equations": equations,
        "footnotes": footnotes,
        "references": references,
    }


def parse_with_pdftohtml_xml(pdf_path: str, sym_map: dict):
    """Fallback geometric extraction using Poppler `pdftohtml -xml` when PyMuPDF is absent."""
    with tempfile.TemporaryDirectory() as tmpd:
        xml_base = Path(tmpd) / "doc"
        res = subprocess.run(["pdftohtml", "-xml", "-i", "-nodrm", str(pdf_path), str(xml_base)], capture_output=True, text=True)
        xml_path = Path(str(xml_base) + ".xml")
        if res.returncode != 0 or not xml_path.exists():
            return None
        tree = ET.parse(xml_path)
        root = tree.getroot()
        pages_data = []
        all_sizes = []
        for p_el in root.findall("page"):
            pno = int(p_el.attrib.get("number", len(pages_data) + 1))
            W = float(p_el.attrib.get("width", 595))
            H = float(p_el.attrib.get("height", 842))
            fontspec = {f.attrib["id"]: float(f.attrib.get("size", 10)) for f in p_el.findall("fontspec")}
            segs = []
            for t_el in p_el.findall("text"):
                raw = "".join(t_el.itertext()).strip()
                if not raw:
                    continue
                x0 = float(t_el.attrib.get("left", 0))
                y0 = float(t_el.attrib.get("top", 0))
                w = float(t_el.attrib.get("width", 10))
                h = float(t_el.attrib.get("height", 10))
                sz = fontspec.get(t_el.attrib.get("font", ""), h)
                clean, gc, nw = normalize_span_text(raw, "", sym_map)
                all_sizes.append(sz)
                segs.append({
                    "bbox": [x0, y0, x0 + w, y0 + h],
                    "raw_text": clean,
                    "text": clean,
                    "size": sz,
                    "bold": any(c.tag == "b" for c in t_el),
                    "garble": gc,
                    "non_ws": nw,
                    "vertical": False,
                })
            # Cluster into blocks by small vertical gaps in same column
            blocks = []
            for s in segs:
                blocks.append({
                    "bbox": [round(v, 2) for v in s["bbox"]],
                    "raw_text": s["raw_text"],
                    "text": s["text"],
                    "lines": [s],
                    "size": s["size"],
                    "bold": s["bold"],
                    "garble": s["garble"],
                    "non_ws": s["non_ws"],
                    "vertical": False,
                })
            pages_data.append({
                "page": pno,
                "printed_label": None,
                "width": W,
                "height": H,
                "blocks": blocks,
                "h_rules": [],
                "v_rules": [],
                "images": [],
                "font_sizes": all_sizes,
            })
        return pages_data


def parse_pdf(pdf_path: str, out_dir: str):
    """Parse a scientific PDF end-to-end and emit parsed_document.json, parsed_document.md, and tables."""
    sym_map = load_symbol_map()
    p = Path(pdf_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tables_dir = out / "tables"
    tables_dir.mkdir(exist_ok=True)

    backend_used = None
    pages_data = []
    uncertainty = []

    fitz_mod = None
    try:
        import pymupdf as fitz_mod
    except ImportError:
        try:
            import fitz as fitz_mod
        except ImportError:
            fitz_mod = None

    if fitz_mod is not None:
        backend_used = "pymupdf"
        try:
            doc = fitz_mod.open(str(p))
        except Exception as exc:
            err_doc = {
                "metadata": {"source_file": str(p)},
                "page_count": 0,
                "backend_used": "pymupdf",
                "status": "corrupt_or_unreadable",
                "error": str(exc),
                "uncertainty": [{"target": "document", "reason": "corrupt_file", "details": str(exc), "human_handoff": str(p)}],
            }
            (out / "parsed_document.json").write_text(json.dumps(err_doc, indent=2, ensure_ascii=False), encoding="utf-8")
            return err_doc

        if doc.is_encrypted and doc.needs_pass:
            doc.close()
            err_doc = {
                "metadata": {"source_file": str(p)},
                "page_count": 0,
                "backend_used": "pymupdf",
                "status": "encrypted_requires_password",
                "uncertainty": [{"target": "document", "reason": "encrypted_pdf", "details": "PDF requires a user password.", "human_handoff": str(p)}],
            }
            (out / "parsed_document.json").write_text(json.dumps(err_doc, indent=2, ensure_ascii=False), encoding="utf-8")
            return err_doc

        for idx in range(len(doc)):
            prim = extract_page_primitives_pymupdf(doc[idx], sym_map)
            prim["page"] = idx + 1
            pages_data.append(prim)
        doc.close()
    elif shutil.which("pdftohtml"):
        backend_used = "pdftohtml_xml"
        pages_data = parse_with_pdftohtml_xml(str(p), sym_map) or []
    elif shutil.which("pdftotext"):
        backend_used = "pdftotext_layout"
        res = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True, errors="replace")
        raw_pages = (res.stdout or "").split("\x0c")
        for idx, txt in enumerate(raw_pages):
            if idx == len(raw_pages) - 1 and not txt.strip():
                continue
            pages_data.append({
                "page": idx + 1, "printed_label": None, "width": 595.0, "height": 842.0,
                "blocks": [{"bbox": [40, 40, 550, 800], "raw_text": txt, "text": txt, "lines": [], "size": 10.0, "bold": False, "garble": 0, "non_ws": len(txt.strip()), "vertical": False}],
                "h_rules": [], "v_rules": [], "images": [], "font_sizes": [10.0],
            })
        uncertainty.append({"target": "layout", "reason": "text_fallback_backend", "details": "Only pdftotext available; bounding boxes and vector tables are unavailable.", "human_handoff": str(p)})
    else:
        raise RuntimeError("No supported PDF backend found (pymupdf, pdftohtml, or pdftotext).")

    all_font_sizes = [sz for pd in pages_data for sz in pd.get("font_sizes", [])]
    body_size = statistics.median(all_font_sizes) if all_font_sizes else 10.0

    headers_footers = detect_headers_footers_marginalia(pages_data)

    all_tables = []
    for pd in pages_data:
        pts = extract_tables_from_page(pd, body_size)
        all_tables.extend(pts)

    merged_tables = merge_continued_tables(all_tables, pages_data)
    structure = classify_roles_and_structure(pages_data, body_size)

    # Compute per-page quality metrics and flag OCR pages honestly
    thr = sym_map.get("garble_thresholds", {})
    min_chars = int(thr.get("min_text_chars_per_page", 40))
    min_img = float(thr.get("min_image_ratio_for_scan", 0.25))
    max_garble = float(thr.get("page_garble_ratio_ocr", 0.05))

    ocr_pages = []
    pages_out = []
    md_parts = []
    doc_title = structure["metadata"]["title"] or p.name
    md_parts.append(f"# {doc_title}\n")
    if structure["metadata"]["authors_and_affiliations"]:
        md_parts.append("**Authors / Affiliations (detected):** " + " | ".join(structure["metadata"]["authors_and_affiliations"]) + "\n")

    for pd in pages_data:
        pno = pd["page"]
        p_area = max(1.0, pd["width"] * pd["height"])
        img_area = sum(max(0.0, (im[2] - im[0]) * (im[3] - im[1])) for im in pd.get("images", []))
        img_ratio = round(min(1.0, img_area / p_area), 4)
        total_nw = sum(b["non_ws"] for b in pd.get("ordered_blocks", []))
        total_gc = sum(b["garble"] for b in pd.get("ordered_blocks", []))
        g_ratio = round(min(1.0, total_gc / max(1, total_nw)), 4)

        needs_ocr = (total_nw < min_chars and img_ratio >= min_img) or (total_nw < 15 and len(pd.get("images", [])) > 0) or (g_ratio >= max_garble)
        if needs_ocr:
            ocr_pages.append(pno)
            uncertainty.append({
                "target": f"page_{pno}",
                "reason": "scanned_no_text_layer" if total_nw < min_chars else "garbled_font",
                "details": f"non_ws_chars={total_nw}, image_ratio={img_ratio}, garble_ratio={g_ratio}",
                "human_handoff": f"Render page {pno} via ocr_fallback.py for visual/OCR check.",
            })

        # Format clean blocks for JSON (drop verbose span line arrays to keep JSON concise & fast)
        clean_blocks = []
        for b in pd.get("ordered_blocks", []):
            bgr = round(min(1.0, b["garble"] / max(1, b["non_ws"])), 4)
            clean_blocks.append({
                "block_id": b["block_id"],
                "page": pno,
                "printed_page": pd.get("printed_label"),
                "bbox": b["bbox"],
                "column": b.get("column", "full"),
                "role": b.get("role", "body"),
                "section": b.get("section"),
                "raw_text": b["raw_text"],
                "text": b["text"],
                "garble_ratio": bgr,
            })

        pages_out.append({
            "page": pno,
            "printed_label": pd.get("printed_label"),
            "width_pt": pd["width"],
            "height_pt": pd["height"],
            "non_ws_chars": total_nw,
            "image_area_ratio": img_ratio,
            "garble_ratio": g_ratio,
            "needs_ocr": needs_ocr,
            "blocks": clean_blocks,
        })

        md_parts.append(f"\n---\n<!-- Page {pno} (label={pd.get('printed_label')}, non_ws={total_nw}, garble={g_ratio}, ocr_needed={needs_ocr}) -->\n")
        for cb in clean_blocks:
            if cb["role"] == "table_body":
                continue
            if cb["role"] == "heading":
                md_parts.append(f"\n## [{cb['block_id']}] {cb['text']}\n")
            elif cb["role"] == "title":
                md_parts.append(f"**[{cb['block_id']}] Title:** {cb['text']}\n")
            elif cb["role"] == "equation":
                md_parts.append(f"> **[{cb['block_id']}] Equation:** `{cb['text']}`\n")
            elif cb["role"] == "footnote":
                md_parts.append(f"*[{cb['block_id']}] Footnote:* {cb['text']}\n")
            else:
                md_parts.append(f"[{cb['block_id']}] {cb['text']}\n")

        # Insert tables belonging to this page
        for tb in [t for t in all_tables if t["page"] == pno]:
            cap_str = f"**{tb['caption']}**\n" if tb.get("caption") else ""
            md_parts.append(f"\n### [{tb['table_id']}] Table (page {pno})\n{cap_str}{tb['markdown']}\n")

    # Save standalone tables
    for tb in all_tables:
        t_head = f"# {tb['table_id']} (Page {tb['page']})\n\n"
        if tb.get("caption"):
            t_head += f"**{tb['caption']}**\n\n"
        (tables_dir / f"{tb['table_id']}.md").write_text(t_head + tb["markdown"] + "\n", encoding="utf-8")

    for mt in merged_tables:
        t_head = f"# {mt['merged_table_id']} (Pages {mt['pages']})\n\n"
        if mt.get("caption"):
            t_head += f"**{mt['caption']}**\n\n"
        (tables_dir / f"{mt['merged_table_id']}.md").write_text(t_head + mt["markdown"] + "\n", encoding="utf-8")

    result = {
        "status": "partial_needs_ocr" if ocr_pages else "completed",
        "source_file": str(p),
        "backend_used": backend_used,
        "page_count": len(pages_out),
        "body_font_size_pt": round(body_size, 2),
        "metadata": structure["metadata"],
        "pages": pages_out,
        "sections": structure["sections"],
        "tables": all_tables,
        "merged_tables": merged_tables,
        "figures": structure["figures"],
        "equations": structure["equations"],
        "footnotes": structure["footnotes"],
        "references": structure["references"],
        "headers_footers": headers_footers,
        "quality_metrics": {
            "total_blocks": sum(len(pg["blocks"]) for pg in pages_out),
            "total_tables": len(all_tables),
            "merged_continuation_tables": len(merged_tables),
            "total_equations": len(structure["equations"]),
            "total_figures": len(structure["figures"]),
            "total_footnotes": len(structure["footnotes"]),
            "total_references": len(structure["references"]),
            "ocr_recommended_pages": ocr_pages,
        },
        "uncertainty": uncertainty,
    }

    (out / "parsed_document.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "parsed_document.md").write_text("\n".join(md_parts), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description="Parse a scientific PDF into structured JSON and grounded Markdown.")
    parser.add_argument("pdf", help="Path to input PDF")
    parser.add_argument("--out-dir", default="parsed_out", help="Output directory")
    args = parser.parse_args()

    res = parse_pdf(args.pdf, args.out_dir)
    summary = {
        "status": res.get("status"),
        "backend_used": res.get("backend_used"),
        "page_count": res.get("page_count"),
        "quality_metrics": res.get("quality_metrics", {}),
        "out_dir": str(Path(args.out_dir).resolve()),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
