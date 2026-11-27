#!/usr/bin/env python3
"""Targeted retrieval over lossless extracted source files under /app/work/extracted/.

Lets the authoring agent inspect exact conclusions, applicability conditions,
limitations, methods, and table metrics with line-level [locator] tags without
dumping entire multi-page PDFs into context.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List


_SECTION_CUES = {
    "abstract": re.compile(r"\b(?:abstract|summary|摘要)\b", re.I),
    "method": re.compile(r"\b(?:method(?:ology|s)?|approach|architecture|model|algorithm|方法)\b", re.I),
    "experiments": re.compile(r"\b(?:experiment(?:s|al)?|evaluation|benchmark|ablation|dataset|实验|评测)\b", re.I),
    "results": re.compile(r"\b(?:results?|performance|accuracy|mae|rmse|f1|auc|outperform|speedup|结果|性能|准确率)\b", re.I),
    "limitations": re.compile(r"\b(?:limit(?:ed|ation)?s?|drawback|weakness|fail(?:ure|s)?|future\s+work|however|cannot|caveat|overhead|shortcoming|瓶颈|局限|不足|然而|难以)\b", re.I),
    "table": re.compile(r"\b(?:table|tab\.|表)\s*\d|\|", re.I),
    "figure": re.compile(r"\b(?:fig(?:ure)?\.?|图)\s*\d", re.I),
}


def main() -> None:
    ap = argparse.ArgumentParser(description="Search lossless [locator]-tagged extracted sources.")
    ap.add_argument("--extracted-dir", default="/app/work/extracted", help="Directory containing extracted *.md files")
    ap.add_argument("--file", default=None, help="Optional filename substring filter")
    ap.add_argument("--section", default=None, help="Optional cue filter: Abstract|Method|Experiments|Results|Limitations|Table|Figure")
    ap.add_argument("--query", default=None, help="Optional space-separated keywords (OR/AND substring match)")
    ap.add_argument("--context", type=int, default=1, help="Context lines around each match")
    ap.add_argument("--limit", type=int, default=30, help="Max matched lines returned")
    ap.add_argument("--json", action="store_true", help="Output JSON instead of plain text")
    args = ap.parse_args()

    edir = Path(args.extracted_dir)
    if not edir.exists():
        raise SystemExit(f"Extracted directory not found: {edir}")

    files = sorted(edir.glob("*.md"))
    if args.file:
        f_sub = args.file.lower()
        files = [f for f in files if f_sub in f.name.lower()]

    sec_re = _SECTION_CUES.get(args.section.strip().lower()) if args.section else None
    q_terms = [t.strip().lower() for t in re.split(r"\s+", args.query or "") if t.strip()]

    hits: List[Dict[str, Any]] = []
    for fp in files:
        raw_lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        for idx, ln in enumerate(raw_lines):
            if idx < 2 and ln.startswith("# Extracted Source:"):
                continue
            low = ln.lower()
            sec_ok = True if sec_re is None else bool(sec_re.search(ln))
            q_ok = True if not q_terms else any(t in low for t in q_terms)
            if sec_ok and q_ok:
                lo = max(0, idx - max(0, args.context))
                hi = min(len(raw_lines), idx + max(0, args.context) + 1)
                snippet = "\n".join(raw_lines[lo:hi])
                hits.append({"file": fp.name, "line_index": idx + 1, "match": ln, "context": snippet})
                if len(hits) >= args.limit:
                    break
        if len(hits) >= args.limit:
            break

    if args.json:
        print(json.dumps({"count": len(hits), "files_scanned": [f.name for f in files], "hits": hits}, indent=2, ensure_ascii=False))
    else:
        print(f"# Query Results ({len(hits)} hits across {len(files)} file(s))")
        for h in hits:
            print(f"\n--- {h['file']} (line {h['line_index']}) ---")
            print(h["context"])


if __name__ == "__main__":
    main()
