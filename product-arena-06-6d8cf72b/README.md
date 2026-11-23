# Scientific Structured Notes Skill (`scientific-structured-notes`)

一个面向科研阅读问答与文献知识沉淀的 **章节对齐、条件保留、细粒度回链、可机械复核** 结构化笔记 Skill（适配 Harbor DeepSeek Harness 及通用 Agent 环境）。

## 1. 解决的核心痛点（与基础 Agent 对照）

| 核心问题 | 基础 Agent 常见失败模式 | 本 Skill 解决方案 |
|---|---|---|
| **对得上（章节对齐）** | 套用通用三段式模板，打乱原论文论证顺序或遗漏子节 | `parse_paper.py` 提取真实多级章节树（`S01`, `S02.1`, ...），`verify_notes.py` 强制校核覆盖率与顺序单调性，并要求填写 `transition_from_prev` |
| **留得住（条件与边界）** | 只摘录“提高了 20%”，丢失实验前提、样本量与局限 | `paper_card` 强制设立独立 `preconditions`（结论成立前提）、`attribution`（作者明述/读者推断）与 `not_reported_and_conflicts` |
| **找得回（来源回链）** | 仅写“见实验部分”，页码/数值幻觉无法独立核查 | 统一 `[P042 \| p.5 \| S03.2]`、`[FIG:..]`、`[TAB:..]`、`[EQ:..]` 锚点 + 原文引文 + 定量数值机械溯源校验 |
| **用得上（可检索复用）** | 纯一段 Markdown 散文，难以跨论文对比或检索 | 同步产出 `result.json`（动态映射 `output_contract`）、YAML frontmatter `structured_notes.md`、`knowledge_items.jsonl` 与跨论文对比 `paper_card_row.csv` |

---

## 2. 目录结构

```text
submission/
├── SKILL.md                                 # Skill 本体与 5 步标准执行流
├── README.md                                # 本说明文档（接口、依赖、限制与自查）
├── GITHUB_URL.txt                           # 本作品公开 GitHub 仓库与入口文件链接
├── scripts/
│   ├── common.py                            # 文本归一化、锚点解析、数值与线索提取
│   ├── inspect_case.py                      # Step 1: 解析任务边界、问题列表与 output_contract
│   ├── parse_paper.py                       # Step 2: Band+双栏几何重排、章节树、OCR回退、分批计划
│   ├── merge_notes.py                       # Step 3b: 长文分批合并与知识点同义/缩写归并
│   ├── verify_notes.py                      # Step 4: 7 维机械审计（锚点/引文/数值/章节/去重/边界/契约）
│   ├── assemble_notes.py                    # Step 5: 早落盘（partial）与终版多格式组装
│   └── self_check.py                        # 打包规范与端到端最小示例自查
├── references/
│   ├── field_dictionary.md                  # 完整字段字典、锚点语法、机器/人工判定职责矩阵
│   └── edge_cases_and_recovery.md           # 扫描件/加密/双栏错序/公式缺失/超长文/冲突异常处理指南
├── templates/
│   ├── paper_card_schema.json               # Paper Card JSON Schema
│   └── notes_payload_template.json          # Agent 填写用的标准 JSON 模板
└── examples/
    ├── minimal_example.md                   # 最小调用说明与示意输出（标明为示例）
    ├── sample_paper.txt                     # 最小示例材料
    └── sample_notes_payload.json            # 最小示例载荷
```

---

## 3. 依赖与运行环境

- **Python**: Python 3.10+（核心核验、合并、组装、契约解析、DOCX/MD/TXT/JSON 读取均仅使用标准库）。
- **PDF 解析后端探测链**：
  1. `pymupdf` (`fitz`) — 首选，支持跨页页眉/页脚统计剔除、Band+双栏坐标重排、字号标题探测及风险页 PNG 渲染；
  2. `pdfplumber` — 次选，兼顾表格抽取；
  3. `pdftotext`（系统命令，如 `pdf-text-tool`）；
  4. `pypdf`。
- **OCR 可选后端**：`tesseract`（支持 `chi_sim+eng` 自动降级 `eng`），用于 `empty_or_scanned` 页面自动回退。

---

## 4. 适用范围与已知限制

- **适用场景**：单篇或多篇对比科研文献（中/英文/双语 PDF、JATS XML/NXML、Markdown、TXT、LaTeX、DOCX、结构化论文 JSON）的结构化笔记生成、科研阅读问答带依据、论文卡片横向比较与知识点抽取。
- **非 PDF 文档保真**：对 JATS XML、Markdown、TXT、DOCX 等无原始页码的输入材料，自动标记 `has_pdf_pages: false`，使用 `anchor_id`（及 XML 原生段落 `id`）回链而绝不虚构虚假 PDF 页码。
- **已知限制**：
  1. 若输入 PDF 为密码加密文档或无文本层扫描件且当前运行环境无 OCR 工具，Skill 不会猜测缺失内容，而是如实输出 `partial` 或 `unable_to_determine` 状态并在 `manual_takeover_points` 给出精确人工接管页码；
  2. 未在正文/图注中以文本列出具体数字的纯折线图/光谱图，机器核验无法直接校验图中未转录读数，此类条目需标为 `human_review_required` 或建议视觉复核。

---

## 5. 本地自查方法

运行一键自查脚本验证 Harbor 合规性（UTF-8、文件数量/体积上限、无软链接）以及最小示例端到端闭环：

```bash
python3 scripts/self_check.py --skill-dir .
```
