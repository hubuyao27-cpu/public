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
    "required": ["case_id", "status", "manuscript_markdown", "comparison_matrix", "research_gaps", "contributions", "references", "sources", "uncertainty", "change_records"],
    "properties": {
      "case_id": { "type": "string" },
      "status": { "type": "string" },
      "manuscript_markdown": { "type": "string" },
      "comparison_matrix": { "type": "array" },
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

## 2. 精简 4 步标准调用命令

```bash
SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output

# Step 1: 一键预准备（检查契约 -> 证据抽取与 skeleton 生成 -> 落盘 partial 断点）
python3 -B "$SKILL_DIR/scripts/prepare_workspace.py" \
  --input-dir /input --work-dir /app/work --out-dir /app/output

# Step 2: 阅读 /app/work/evidence_digest.md 与 section_payload_skeleton.json，
# 补全 /app/work/section_payload.json（长正文可选写为 /app/work/manuscript.md 并通过 manuscript_file 引用）

# Step 3: 一键机械核验与组装最终产物（自动将内文 [R01]/[R02] 按首次出现顺序渲染为目标格式 [1]/[2]）
python3 -B "$SKILL_DIR/scripts/finalize_workspace.py" \
  --work-dir /app/work --out-dir /app/output
```

---

## 2b. 多篇 PDF 与英文/修订场景要点（Synthetic Illustration）

- **英文目标提示（中文提问）**：如 `/input/task.md` 写明“请用英文完成引言与相关工作，采用 IEEE 格式”，`inspect_case.py` 自动置 `language: "en"`、`citation_style: "ieee_numeric"`；同时读取多篇两栏 PDF 的 `Abstract / Method / Experiments / Limitations` 分节卡写入 `/app/work/evidence_digest.md`。
- **缺料诚实短路**：当 `/input/` 仅含外部文献（无自研实验数据时）或为空时，不会因外部文献的 `Fig./Table` 强迫伪造 `contributions[]`；缺料则设置 `status: "insufficient_material"`。

---

## 3. 示意输出片段（ILLUSTRATIVE OUTPUT）

### 3.1 `introduction_and_related_work.md`（节选，已自动渲染为 GB/T 7714 数字引文）
> **1. 引言**  
> 化工精馏塔稳态严格模拟需迭代求解高维 MESH 方程组，在回流比宽范围变工况优化中单次求解耗时成为在线调度瓶颈[2]。早期研究采用固定操作条件下的降阶代理模型[1]，虽降低了计算开销，但在进料组成或回流比越界时误差显著增大；近期图神经网络代理方法[2]改善了拓扑表达，却缺乏质量守恒硬约束，在稀疏采样边界条件下仍可能违背相平衡关系。结合文献局限与工程观测[作者判断]，现有路线在**“跨回流比（1.5–3.0）泛化精度”**与**“物理守恒一致性”**之间仍存在明显缺口。  
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
