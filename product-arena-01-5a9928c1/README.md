# Scientific Literature Translator Skill (`scientific-literature-translator` v2)

面向 Bohrium Playground **「阅读03｜文献翻译」** 赛题构建的**版面感知、术语锁定、段落对齐、七维机械核验**科研文献双语翻译 Skill（适用于 Harbor DeepSeek Harness 及支持 `SKILL.md` 的 Agent 环境）。

---

## 一、核心升级亮点（针对科研论文高频翻译痛点）

1. **版面还原与跨页跨栏防串行（`extract_and_segment.py`）**：
   - 抛弃破坏双栏顺序的 `get_text("text", sort=True)`，改用**自适应 XY-Cut 分栏排序 + 行内窄沟槽拆分**，自动剥离跨页重复页眉/页脚/边注并归档到 `headers_footers.json`。
   - **Booktabs 学术三线表完整保留**：基于矢量水平线重建 Markdown 表格（`kind="table"`），表头与单元格不被打散，译文保留相同行列结构。
   - **公式与化学/物理上下标保真**：利用字号比例与基线偏移还原 `CHCl_{3}`、`10^{-6}`、`g_{CC}`，并将右对齐公式编号 `(1)` 合并回方程块；修复跨行连字符断词并标注跨栏/跨页句子接续上下文 `prev_context_tail`。
   - **扫描/乱码页检测与 OCR 降级（`ocr_fallback.py`）**：计算每页图像占比与 PUA/乱码字符率 `garble_ratio`，自动触发 300 DPI PNG 渲染与 Tesseract TSV 置信度识别；绝不编造无法识别的内容。
2. **术语自动挖掘与强制锁定（`glossary_candidates.json` + `glossary.json`）**：
   - 从 Title、Abstract、Headings 及正文 `Full Term (ACR)` 定义中自动挖掘候选术语与频次，助力 Agent 一轮生成 `glossary.json` 并由校验脚本强制全篇执行。
3. **七维机械译文审计（`verify_translation.py`）**：
   - 核验：段落覆盖率与重复/错序、术语一致性、数值与比较符（`±`, `≤`, `≥`, `≈`）、**物理单位保留**（`kJ/mol`, `nm`, `mg/mL`, `K`, `°C`, `%` 等）、文献引用与图表/公式编号、**双向否定/转折/respectively 逻辑极性**，以及**译文 Markdown 表格行列结构匹配**。
4. **病例合约对齐与闭环交付（`inspect_case.py` + `assemble_output.py`）**：
   - 自动识别 `/input/case.json` 的 `case_id`、`output_contract` 字段，保留 `sources`、`uncertainty`、`change_records`、`limitations`，支持 `--strict` 自检防止缺失合约字段。

---

## 二、目录结构

```text
submission/
├── SKILL.md                      # Skill YAML frontmatter 与 6 步预算感知工作流
├── README.md                     # 架构、用法、依赖矩阵与异常处理说明
├── GITHUB_URL.txt                # 公开 GitHub 仓库链接
├── scripts/
│   ├── inspect_case.py           # /input/case.json 与 task.md 合约解析
│   ├── extract_and_segment.py    # XY-Cut 多栏抽取、三线表重建、上下标与术语挖掘
│   ├── ocr_fallback.py           # 扫描/高乱码页 300 DPI 渲染与 Tesseract OCR 降级
│   ├── verify_translation.py     # 七维译文机械核验
│   └── assemble_output.py        # 汇总 result.json（含 change_records）、双语译文与术语表
├── resources/
│   ├── symbol_map.json           # 连字归一化、Symbol 字体表与乱码判定阈值
│   └── translation_cues.json     # 物理单位等价表与英汉否定/转折/分别对应词表
└── examples/
    └── minimal_example.md        # 清晰标明为示意示例的最小调用流程
```

---

## 三、依赖矩阵与回退机制

| 模块 | 首选工具 | 缺失回退机制 |
| :--- | :--- | :--- |
| PDF 版面抽取 | `pymupdf` (`fitz`) | 回退至 `pdfplumber` → `pdftotext -layout`；同时支持 `.txt`/`.md` 纯文本 |
| 扫描页渲染/OCR | `pymupdf`/`pdftoppm` + `tesseract` + `PIL` | 无 `tesseract` 时仍输出 300 DPI PNG 并标注 `rendered_png_no_tesseract` 与 `uncertainty` |
| 校验与汇总 | Python 3.8+ 标准库 | 零外部依赖 |
