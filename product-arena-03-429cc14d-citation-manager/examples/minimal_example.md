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

python3 "$SKILL_DIR/scripts/inspect_case.py" --input-dir ./examples_in --out ./work/case_plan.json
python3 "$SKILL_DIR/scripts/parse_manuscript.py" --case-plan ./work/case_plan.json --out-dir ./work/manuscript
python3 "$SKILL_DIR/scripts/parse_and_dedup_library.py" \
  --case-plan ./work/case_plan.json \
  --manuscript ./work/manuscript/manuscript_parsed.json \
  --rules "$SKILL_DIR/resources/dedup_rules.json" \
  --style-rules "$SKILL_DIR/resources/style_rules.json" \
  --out-dir ./work/library
python3 "$SKILL_DIR/scripts/verify_sources_pre.py" \
  --case-plan ./work/case_plan.json \
  --library ./work/library/library_canonical.json \
  --evidence ./work/library/evidence_fulltexts.json \
  --manuscript ./work/manuscript/manuscript_parsed.json \
  --out-dir ./work/verify

# Step 4: Author ./work/citation_plan.json with [[CITE:REF_XXX]] tokens
python3 "$SKILL_DIR/scripts/sync_and_format.py" \
  --case-plan ./work/case_plan.json \
  --library ./work/library/library_canonical.json \
  --manuscript ./work/manuscript/manuscript_parsed.json \
  --pre-audit ./work/verify/pre_audit.json \
  --plan ./work/citation_plan.json \
  --style-rules "$SKILL_DIR/resources/style_rules.json" \
  --out-dir ./work/synced
python3 "$SKILL_DIR/scripts/assemble_deliverables.py" \
  --case-plan ./work/case_plan.json \
  --library ./work/library/library_canonical.json \
  --manuscript ./work/manuscript/manuscript_parsed.json \
  --pre-audit ./work/verify/pre_audit.json \
  --plan ./work/citation_plan.json \
  --synced ./work/synced/sync_result.json \
  --checkpoint final \
  --out-dir ./output
python3 "$SKILL_DIR/scripts/verify_deliverables.py" \
  --case-plan ./work/case_plan.json \
  --library ./work/library/library_canonical.json \
  --evidence ./work/library/evidence_fulltexts.json \
  --manuscript ./work/manuscript/manuscript_parsed.json \
  --pre-audit ./work/verify/pre_audit.json \
  --plan ./work/citation_plan.json \
  --synced ./work/synced/sync_result.json \
  --output-dir ./output \
  --out ./work/verification_report.json
```

---

## 3. 示意输出 / Illustrative Output (Simulated)

1. **去重合并结果**: `doe2024catalyst`（正式版 `Journal of Catalysis Informatics`, 2024）与原稿中预印本 `[2]`（`arXiv:2305.01234`, 2023）依据 `tier2_preprint_to_published` 自动归并为同一规范条目 `REF_001`，保留正式版优先的字段来源映射。
2. **句子重定位与顺序重排 (`revised_manuscript.md` 片段示意)**:
   - 原本堆叠在句末的 `[1, 2]` 被拆分并前移至真正需要支撑的量化/机制分句：`Graph neural networks outperform classical descriptors by 14.2% on benchmark datasets<sup>[1]</sup>, and attention pooling reduces variance across molecular scaffolds<sup>[1]</sup>.`
   - 原文第 1 条（Vaswani 等）未被任何修正后句子引用，自动从正文参考文献表中移出并记录在 `uncited_library_entries`，而 `doe2024catalyst` 顺位重编为 `[1]`，图注中引用同步更新为 `<sup>[1]</sup>`，`[待引用]` 由 `wang2022dft` 补入成为 `[2]`。
3. **格式化文献表 (`references_formatted.md` 示意)**:
   ```text
   [1] DOE J, SMITH B. Graph catalyst screening[J]. Journal of Catalysis Informatics, 2024, 18(3): 101-115. DOI:10.1016/j.jcat.2024.01.008.
   [2] WANG L, ZHANG W. Multi-fidelity DFT features for yield prediction[J]. Chinese Journal of Chemical Engineering, 2022, 45: 88-96. DOI:10.1016/j.cjche.2022.02.004.
   ```
4. **机械自检报告 (`verification_report.json` 示意)**:
   `{"passed": true, "error_count": 0, "warning_count": 0}`
