# Verifiable Multi-Paper Literature Knowledge Graph Builder (`literature-kg-builder`)

面向科研文献知识图谱构建任务的 **可核验、防幻觉、支持跨篇实体对齐（含非合并守卫）与增量更新** 的通用 Agent Skill（兼容 Harbor DeepSeek Harness 及标准 `SKILL.md` 规范）。

---

## 核心解决的四大痛点与六大能力

| 痛点 / 考察维度 | 本 Skill 的确定性工程方案 |
|---|---|
| **抽得准（零幻觉实体与短引）** | `verify_kg.py` 对每个节点 mention 与每条边/对齐/冲突/查询的 `(source_id, paragraph_id, quote)` 执行逐字/规范化子串机械核验，杜绝常识补造与编造引用；对 `secondary-publisher.xml` 等 JATS XML 解析 `<sub>`/`<sup>`/`<tex-math>`/`<table-wrap>`，防止线性化公式误读。 |
| **连得对（有向条件与认识论分层）** | 严格控制 14 类有向关系与 4 类陈述归属（`author_direct_result` / `author_method_statement` / `cited_prior_work` / `speculation_or_hypothesis`），机械拦截「转述/推测标为直接强证据」、「比较/优于/矛盾缺适用条件」以及「把两篇不同条件实验结果揉进同一条非比较边」。 |
| **对得齐（双层对齐与防误合守卫）** | `detect_alignment_candidates.py` 自动探测大小写/标点/缩写/词形/跨篇通用术语碰撞，区分四态裁决：`merge_alias`、`keep_separate_distinct_instance`（可经抽象 `Concept` + `INSTANTIATES_CONCEPT` 关联而不合并物理样品/实验条件/数值）、`keep_separate_homonym`、`uncertain_no_merge`。 |
| **查得到、回得去（真实可执行查询 + 元数据/坐标严控）** | `query_kg.py` 在 `kg.json` 上真实运行 6 类图查询算子，返回子图节点/边 ID、分篇不混合答案、原文短引以及诚实的 `unable_to_determine`；`inspect_corpus.py` 强制隔离单篇 DOI/作者/许可，去重同篇多格式副本，无真实 PDF 时严禁伪造 `page 1`。 |
| **增量更新（稳定 ID 保留）** | `build_and_update_kg.py` 支持 `v1 -> v2 -> ...` 顺序增量接入新论文，100% 保留既有 `E###`/`C###`/`R###` 编号与原始溯源，输出完整 `incremental_update_log.json`。 |
| **多格式导出与视觉预览** | `export_and_assemble.py` 同步生成动态满足 `output_contract` 的 `result.json`、`kg.json`、`kg.graphml`（Gephi/Cytoscape/NetworkX 通用）、`kg_report.md`（含 Mermaid）及分栏多篇配色图谱 `kg_preview.svg`/`.png`/`.pdf`。 |

---

## 目录结构

```text
submission/
├── SKILL.md                           # Skill 本体（YAML frontmatter + 7 步执行流 + 7 条硬红线 + 异常处理）
├── README.md                          # 使用说明、本体定义、依赖、限制与自查指南
├── schema/
│   ├── kg_schema.json                 # 8 类实体、14 类关系、四态对齐与溯源/认识论 Schema
│   └── query_templates.json           # 5 套可复用声明式图谱查询模板
├── scripts/
│   ├── inspect_corpus.py              # 步骤 1：多篇语料解析、元数据隔离、同篇多格式去重、XML 公式/表格核对
│   ├── detect_alignment_candidates.py # 步骤 3：别名/缩写/模糊/同名异义/通用术语非合并碰撞探测
│   ├── build_and_update_kg.py         # 步骤 2 & 4：v1 图谱构建与 v2+ 保留旧编号的增量更新引擎
│   ├── verify_kg.py                   # 步骤 6：10 维度零幻觉机械核验门禁（有 ERROR 返回非零退出码）
│   ├── query_kg.py                    # 步骤 5：6 算子可执行图谱查询引擎（含自动补齐与无法确定探测）
│   ├── export_and_assemble.py         # 步骤 7：GraphML/Markdown/SVG/PNG/PDF 导出与动态契约 result.json 封装
│   └── run_selftest.py                # 一键端到端正向 + 7 类对抗负例回归自检脚本
└── examples/
    ├── minimal_example.md             # 最小调用步骤与合成示意输出（明确标明为示例）
    └── synthetic_corpus/              # 轻量合成双论文测试夹具（非正式评测案例或答案）
```

---

## 依赖与安装条件

- **核心运行环境**：**Python 3.10+ 纯标准库零第三方依赖**（`json`, `re`, `xml.etree.ElementTree`, `hashlib`, `difflib`, `pathlib`, `argparse`, `collections`, `unicodedata`, `html`），无需 `pip install` 即可在任何 Linux/Windows/macOS 或 Harbor 镜像中运行全部核心功能。
- **可选增强库（存在则自动启用，缺失则优雅降级）**：
  - `matplotlib`：渲染 `kg_preview.png` 与 `kg_preview.pdf`；缺失时自动保留纯标准库生成的 `kg_preview.svg`。
  - `pymupdf` (`fitz`) / `pdfplumber` / `pypdf` / 系统 `pdftotext`：仅当输入包含 `.pdf` 文件时按序调用；对加密或无文字层扫描件诚实标记 `empty_or_scanned_pages` 并降级为 `partial`。

---

## 快速自检（独立验证）

在 Skill 根目录下运行一键回归测试（全程在临时目录执行，无副作用）：

```bash
python3 scripts/run_selftest.py
```

预期输出：
- 全流程正向断言通过：`quote_grounding_rate = 1.0`、`incremental_id_preservation_rate = 1.0`、`error_count = 0`、`warning_count = 0`、4 个真实查询（含 1 个诚实 `unable_to_determine`）、`kg.graphml` 合法、`output_contract` 字段 100% 覆盖；
- 7/7 对抗负例拦截通过：伪造引用、伪造 PDF 第 1 页、根 DOI 串篇到第二篇、同篇多格式伪跨篇、转述误标直接强证据、比较关系缺适用条件、增量更新丢失 v1 节点 ID 均被 `verify_kg.py` 100% 精准拦截。

---

## 适用范围与已知限制

- **适用场景**：单篇、双篇或多篇科研论文的实体关系结构化抽取、跨篇对齐与冲突审计、增量图谱维护及可回溯证据问答（支持材料/化学、生物医学、物理、计算机与工程等多学科文献）。
- **明确限制**：
  1. 本 Skill 仅基于实际提供的文本、结构化 JSON、JATS XML 或可读 PDF 文本层抽取信息，不解析缺失的图像像素、补充附件或外部底层原始实验/临床数据表；
  2. 对无文字层的纯扫描 PDF，如宿主环境未提供 OCR 文本层，脚本会诚实标注不可读页与 `status: "partial"`，交由人工接管，绝不凭常识猜补；
  3. 实体/关系的深度语义抽取与科学对齐裁决仍依赖调用本 Skill 的 LLM 完成，本工具链提供结构化索引、碰撞提示与 10 维度机械核验门禁以最大化准确率并最小化返工。
