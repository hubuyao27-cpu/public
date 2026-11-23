# 最小调用示例（Minimal Invocation Example — 示意输出 / ILLUSTRATIVE EXAMPLE）

> **重要声明**：本文件构造了一组**合成科研材料（Synthetic Toy Case）**用于完整演示 `sci-intro-rw-writer` 的输入接口、命令行调用步骤与预期输出结构；**以下示例不是竞赛官方测试案例结果，也不代表官方评分数据**。

---

## 1. 示例输入目录（`/input`）

### `case.json`
```json
{
  "case_id": "demo-mem-distill-01",
  "language": "zh",
  "citation_style": "gbt7714",
  "output_contract": {
    "required": ["case_id", "status", "manuscript_markdown", "related_work_matrix", "research_gaps", "contributions", "references", "sources", "uncertainty", "change_records"],
    "properties": {
      "case_id": { "type": "string" },
      "status": { "type": "string" },
      "manuscript_markdown": { "type": "string" },
      "related_work_matrix": { "type": "array" },
      "research_gaps": { "type": "array" },
      "contributions": { "type": "array" },
      "references": { "type": "array" },
      "sources": { "type": "array" },
      "uncertainty": { "type": "array" },
      "change_records": { "type": "array" }
    }
  }
}
```

### `task.md`
> 请依据 `refs.bib`、`lit_notes.md` 与 `own_experiments.md`，用中文撰写可并入论文的“引言与相关工作”草稿（采用 GB/T 7714-2015 数字引用），对相关工作按技术路线分组对比，明确研究空白并核对本文贡献与实验证据，保留全部来源定位。

### `refs.bib`
```bibtex
@article{Zhang2022FixedMem,
  author = {Zhang, Wei and Li, Ming},
  title = {Fixed-Temperature Distillation for Thermal Separation},
  journal = {Journal of Process Engineering},
  year = {2022},
  doi = {10.1000/jpe.2022.01}
}

@inproceedings{Chen2024GraphSurrogate,
  author = {Chen, Hao and Wang, Lin},
  title = {Graph Surrogate Modeling of Distillation Columns},
  booktitle = {ICML Workshop on Scientific ML},
  year = {2024},
  doi = {10.1000/icmlw.2024.19}
}
```

### `own_experiments.md`
```markdown
## 本文方法与实验结果
- 方法：提出物理约束自适应代理模型（PC-Surrogate），对应 Method 节 `Section 3.2`。
- 图表：系统流程见 `Fig. 1`，塔板温度分布误差对比见 `Table 2`。
- 实验指标：在 12 块理论板甲醇-水分离工况（回流比 1.5–3.0）下，塔顶纯度预测 MAE 降至 0.42 mol%，推理耗时相比严格稳态求解加速 38.5x。
```

---

## 2. 标准调用命令

```bash
SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output

# Step 1 & 2: 检查契约并生成精炼证据摘要
python3 "$SKILL_DIR/scripts/inspect_case.py" --input-dir /input --out /app/work/case_plan.json
python3 "$SKILL_DIR/scripts/ingest_materials.py" --case-plan /app/work/case_plan.json \
  --out-ledger /app/work/source_ledger.json --out-digest /app/work/evidence_digest.md

# Step 3: 尽早落盘 partial 断点
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" --case-plan /app/work/case_plan.json \
  --ledger /app/work/source_ledger.json --checkpoint partial --out-dir /app/output

# Step 4: 阅读 /app/work/evidence_digest.md，写入 /app/work/section_payload.json
# （此处由 Agent 基于 digest 与模板生成结构化 JSON）

# Step 5: 十轴机械核验
python3 "$SKILL_DIR/scripts/verify_intro_rw.py" --payload /app/work/section_payload.json \
  --ledger /app/work/source_ledger.json --case-plan /app/work/case_plan.json \
  --out /app/work/verification_report.json

# Step 6: 组装最终产物
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" --case-plan /app/work/case_plan.json \
  --ledger /app/work/source_ledger.json --payload /app/work/section_payload.json \
  --verification /app/work/verification_report.json --checkpoint final --out-dir /app/output
```

---

## 3. 示意输出片段（ILLUSTRATIVE OUTPUT）

### 3.1 `introduction_and_related_work.md`（节选）
> **1. 引言**  
> 化工精馏塔稳态严格模拟需迭代求解高维 MESH 方程组，在回流比宽范围变工况优化中单次求解耗时成为在线调度瓶颈[R02]。早期研究采用固定操作条件下的降阶代理模型[R01]，虽降低了计算开销，但在进料组成或回流比越界时误差显著增大；近期图神经网络代理方法[R02]改善了拓扑表达，却缺乏质量守恒硬约束，在稀疏采样边界条件下仍可能违背相平衡关系。结合文献局限与工程观测[作者判断]，现有路线在**“跨回流比（1.5–3.0）泛化精度”**与**“物理守恒一致性”**之间仍存在明显缺口。  
> 为此，本文提出物理约束自适应代理模型 PC-Surrogate（见 `Section 3.2` 与 `Fig. 1`），在 12 块理论板甲醇-水分离工况（回流比 1.5–3.0）下将塔顶纯度预测 MAE 控制在 0.42 mol%，并将推理速度较严格求解提升 38.5x（见 `Table 2`）。

### 3.2 `result.json` 结构示意（节选）
```json
{
  "case_id": "demo-mem-distill-01",
  "status": "completed",
  "research_gaps": [
    {
      "gap_id": "G01",
      "derivation_type": "shared_limitation",
      "supporting_ref_ids": ["R01", "R02"],
      "supporting_evidence_locators": ["refs.bib:L1", "refs.bib:L9"],
      "attribution": "literature_derived"
    }
  ],
  "contributions": [
    {
      "contrib_id": "C01",
      "claim_text": "提出物理约束自适应代理模型 PC-Surrogate，在 12 块理论板甲醇-水分离工况下将 MAE 控制在 0.42 mol%，较严格求解提速 38.5x。",
      "scope_qualification": "适用于 12 块理论板甲醇-水二元分离、回流比 1.5–3.0 工况。",
      "linked_methods": ["Section 3.2"],
      "linked_figures_tables": ["Fig. 1", "Table 2"],
      "linked_metrics": ["MAE = 0.42 mol%", "38.5x speedup"],
      "own_evidence_locators": ["own_experiments.md:L2"],
      "verification_status": "verified"
    }
  ],
  "verification_summary": {
    "passed": true,
    "error_count": 0,
    "warning_count": 0
  }
}
```
