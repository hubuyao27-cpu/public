# 最小调用示例 / Minimal Illustrative Example

> **声明 / Notice**: 以下输入、命令行和输出均为**最小示意示例**，仅用于说明 Skill 的接口格式与完整调用链路，**不构成且不冒充赛事平台正式测试案例或测评结果**。

---

## 1. 示例输入 / Illustrative Input

- 稿件底稿: [`sample_manuscript.md`](sample_manuscript.md)（含段落末尾堆叠引用 `[1, 2]`、图注引用 `[2]`、待补引占位 `[待引用]` 以及一条 arXiv 预印本条目）
- 文献库: [`sample_refs.bib`](sample_refs.bib)（含正式发表期刊版 `doe2024catalyst` 与 `wang2022dft`）
- 目标规范: `GB/T 7714—2015` 顺序编码制（上标方括号）

---

## 2. 完整调用命令 / CLI Workflow

```bash
SKILL_DIR="/skills/weekly-skill"
mkdir -p ./work ./output

# Step 1: 一键输入检查、稿件与文献解析去重、预核验与初始骨架生成
python3 -B "$SKILL_DIR/scripts/prepare_workspace.py" \
  --input-dir ./examples_in \
  --work-dir ./work \
  --out-dir ./output

# Step 2: 阅读 ./work/library/library_digest.md、./work/manuscript/manuscript_claims_digest.md、
#         ./work/verify/verification_worklist.md，编写 ./work/citation_plan.json（嵌入 [[CITE:REF_XXX]]）

# Step 3: 一键确定性重排同步、多格式导出、10 项机械自检与终版封装
python3 -B "$SKILL_DIR/scripts/finalize_workspace.py" \
  --work-dir ./work \
  --out-dir ./output \
  --plan ./work/citation_plan.json
```

---

## 3. 示意输出 / Illustrative Output (Simulated)

1. **去重合并结果**: `doe2024catalyst`（正式版 `Journal of Catalysis Informatics`, 2024）与原稿中预印本 `[2]`（`arXiv:2305.01234`, 2023）依据 `tier2_preprint_to_published` 自动归并为同一规范条目 `REF_002`，保留正式版优先的字段来源映射（且正式期刊版不会把 `arXiv` 编号错误拼到期刊名后）。
2. **句子重定位与顺序重排 (`revised_manuscript.md` 片段示意)**:
   - 原本堆叠在段末的 `[1, 2]` 被拆分并精准前移至对应论断分句：第一句自注意力机制论述挂接 Vaswani 等<sup>[1]</sup>，第二句图神经网络催化基准 14.2% 提升与注意力池化挂接 `doe2024catalyst`<sup>[2]</sup>；图注引用同步更新为 `<sup>[2]</sup>`；`[待引用]` 由 `wang2022dft` 补入成为 `<sup>[3]</sup>`。
3. **格式化文献表 (`references_formatted.md` 示意)**:
   ```text
   [1] VASWANI A, SHAZEER N, PARMAR N, et al. Attention is all you need[C]//Advances in Neural Information Processing Systems, 2017: 5998-6008.
   [2] DOE J, SMITH B. Graph catalyst screening[J]. Journal of Catalysis Informatics, 2024, 18(3): 101-115. DOI:10.1016/j.jcat.2024.01.008.
   [3] WANG L, ZHANG W. Multi-fidelity DFT features for yield prediction[J]. Chinese Journal of Chemical Engineering, 2022, 45: 88-96. DOI:10.1016/j.cjche.2022.02.004.
   ```
4. **机械自检报告 (`verification_report.json` 示意)**:
   `{"passed": true, "error_count": 0, "warning_count": 0}`
