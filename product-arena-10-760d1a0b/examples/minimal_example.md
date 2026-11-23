# 最小调用示例（Synthetic Illustrative Example）

> **声明**：本示例使用完全虚构的合成双论文迷你语料 `examples/synthetic_corpus/`（`SYNTH-EX-001`），仅用于演示接口规范、调用命令和示意输出结构，**绝不冒充正式评测案例或正式答案**。

---

## 1. 示意输入目录（合成双论文 JATS + XML 结构）

`examples/synthetic_corpus/` 包含：
- `case.json`：声明 `case_id: "SYNTH-EX-001"`、根级 `doi/title/authors`（**仅属第一篇** `paper_primary`）及 `output_contract.required` 字段列表；
- `corpus-manifest.json`：独立列出 `paper_primary`（`10.9999/synth.primary.2025.001`）与 `paper_secondary`（`10.9999/synth.secondary.2026.002`）的各自 DOI、作者、哈希与许可证；
- `primary-material.json` 与 `secondary-material.json`：使用 JATS `paragraph_id`（无 PDF 页码）；
- `material.json`：两篇正文段落的汇总索引（脚本自动去重，不误计为第三篇）；
- `secondary-publisher.xml`：含 `<sub>`、`<sup>`、`<inline-formula><tex-math>` 与 `<table-wrap>`，供核对线性化公式。

---

## 2. 完整 CLI 调用序列

```bash
SKILL_DIR=.
WORK=/tmp/kg_demo_work
OUT=/tmp/kg_demo_out

# Step 1: 语料索引、元信息隔离、同篇多格式检测与 XML 公式核对
python3 "$SKILL_DIR/scripts/inspect_corpus.py" \
  --input-dir "$SKILL_DIR/examples/synthetic_corpus" \
  --out-dir "$WORK/inspect"

# Step 2: (Agent 阅读 $WORK/inspect/digest_paper_primary.md 写出 primary_extraction.json)
#         构建 v1 图谱（锁定 E001.. / R001.. 稳定编号）
python3 "$SKILL_DIR/scripts/build_and_update_kg.py" \
  --mode init \
  --corpus-index "$WORK/inspect/corpus_index.json" \
  --extraction "$WORK/primary_extraction.json" \
  --out "$WORK/kg_v1.json"

# Step 3: (Agent 阅读 $WORK/inspect/digest_paper_secondary.md 写出 secondary_extraction.json)
#         运行跨篇实体对齐与非合并碰撞探测
python3 "$SKILL_DIR/scripts/detect_alignment_candidates.py" \
  --base-kg "$WORK/kg_v1.json" \
  --new-extraction "$WORK/secondary_extraction.json" \
  --out "$WORK/alignment_candidates.json"

# Step 4: (Agent 审核候选对，写出 alignment_decisions.json 与 cross_paper_relations.json)
#         执行保留 v1 编号的增量更新 -> kg_v2.json
python3 "$SKILL_DIR/scripts/build_and_update_kg.py" \
  --mode update \
  --corpus-index "$WORK/inspect/corpus_index.json" \
  --base-kg "$WORK/kg_v1.json" \
  --extraction "$WORK/secondary_extraction.json" \
  --alignment "$WORK/alignment_decisions.json" \
  --cross-relations "$WORK/cross_paper_relations.json" \
  --out "$WORK/kg_v2.json" \
  --log-out "$WORK/incremental_update_log.json"

# Step 5: 在 kg_v2.json 上真实执行 >=3 个结构化图谱查询
python3 "$SKILL_DIR/scripts/query_kg.py" \
  --kg "$WORK/kg_v2.json" \
  --query-specs "$WORK/query_specs.json" \
  --auto-fill-min 3 \
  --out "$WORK/kg_queries.json"

# Step 6: 10 维度零幻觉机械核验门禁（含 v1 ID 保留与引用逐字命中）
python3 "$SKILL_DIR/scripts/verify_kg.py" \
  --kg "$WORK/kg_v2.json" \
  --corpus-index "$WORK/inspect/corpus_index.json" \
  --xml-recon "$WORK/inspect/xml_math_reconciliation.json" \
  --prev-kg "$WORK/kg_v1.json" \
  --candidates "$WORK/alignment_candidates.json" \
  --queries "$WORK/kg_queries.json" \
  --out "$WORK/kg_verification_report.json"

# Step 7: 导出 GraphML、Markdown 报告、视觉预览图与动态契约 result.json
python3 "$SKILL_DIR/scripts/export_and_assemble.py" \
  --case "$SKILL_DIR/examples/synthetic_corpus/case.json" \
  --kg "$WORK/kg_v2.json" \
  --queries "$WORK/kg_queries.json" \
  --verification "$WORK/kg_verification_report.json" \
  --xml-recon "$WORK/inspect/xml_math_reconciliation.json" \
  --out-dir "$OUT"
```

> 也可以直接运行 `python3 scripts/run_selftest.py` 一键复现上述全流程及 7 项负例门禁测试。

---

## 3. 示意输出片段（Illustrative Example Output Only）

### 3.1 跨篇对齐与「不得合并」决策示意 (`alignment_decisions`)

```json
[
  {
    "decision_id": "A002",
    "candidate_a": {"node_id_or_name": "E004", "source_id": "paper_primary"},
    "candidate_b": {"node_id_or_name": "P2_E4", "source_id": "paper_secondary"},
    "verdict": "merge_alias",
    "canonical_target_id": "E004",
    "rationale": "Paper 1 defines 'Faradaic efficiency (FE)' and Paper 2 reports the same standard metric 'FE'; numeric values remain on separate per-paper edges."
  },
  {
    "decision_id": "A003",
    "candidate_a": {"node_id_or_name": "E001", "source_id": "paper_primary"},
    "candidate_b": {"node_id_or_name": "P2_E1", "source_id": "paper_secondary"},
    "verdict": "keep_separate_distinct_instance",
    "canonical_target_id": null,
    "shared_concept_name": "Ni-N-C single-atom catalyst family",
    "rationale": "DO NOT MERGE: distinct physical catalysts & test setups (HP-Ni-N-C 900 C H-cell 0.5 M KHCO3 vs P-Ni-N-C 800 C flow cell 1.0 M KOH). Linked via abstract Concept C001 without merging samples or metrics."
  },
  {
    "decision_id": "A004",
    "candidate_a": {"node_id_or_name": "E003", "source_id": "paper_primary"},
    "candidate_b": {"node_id_or_name": "P2_E3", "source_id": "paper_secondary"},
    "verdict": "keep_separate_homonym",
    "canonical_target_id": null,
    "rationale": "DO NOT MERGE HOMONYM: 'MS' means Mass Spectrometry in Paper 1 but Membrane Separator in Paper 2."
  }
]
```

### 3.2 诚实不可判定查询示意 (`kg_queries.json` — `Q4`)

```json
{
  "query_id": "Q4",
  "question": "Is there direct controlled experimental evidence comparing HP-Ni-N-C and P-Ni-N-C head-to-head in the same flow cell, or raw Figure S2 impedance values?",
  "operator": "gap_or_unanswerable_check",
  "status": "unable_to_determine",
  "matched_subgraph": {"node_ids": ["E001", "E007"], "edge_ids": []},
  "answer": "Unable to determine from the provided corpus: No verified direct ['COMPARES', 'OUTPERFORMS'] edge links 'HP-Ni-N-C (paper_primary)' and 'P-Ni-N-C (paper_secondary)' under controlled identical conditions in the corpus. Input boundary: figure_pixels_unavailable / supplementary_unavailable.",
  "uncertain_or_unanswerable_relations": [
    "No verified direct ['COMPARES', 'OUTPERFORMS'] edge links 'HP-Ni-N-C (paper_primary)' and 'P-Ni-N-C (paper_secondary)' under controlled identical conditions in the corpus."
  ]
}
```

### 3.3 机械核验指标示意 (`kg_verification_report.json`)

```json
{
  "passed": true,
  "metrics": {
    "node_count": 11,
    "edge_count": 12,
    "quote_grounding_rate": 1.0,
    "fake_pdf_page_violations": 0,
    "root_metadata_bleed_violations": 0,
    "same_paper_format_violations": 0,
    "incremental_id_preservation_rate": 1.0,
    "executed_query_count": 4,
    "error_count": 0,
    "warning_count": 0
  }
}
```
