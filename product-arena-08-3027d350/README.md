# 科研文献 PDF 版面与结构解析 Skill（`sci-pdf-parser`）

面向 Bohrium Playground **「阅读01｜文献文档解析」** 赛题构建的科研文献文档解析 Skill。核心设计目标是在多栏论文、复杂学术三线表/跨页续表、公式与上下标/特殊字符、以及扫描/乱码页场景下，产出**提得全、排得对、拆得清、核得回**的结构化解析结果，同时显著降低 Agent 在有限请求与时间预算下的 Token 消耗和试错成本。

---

## 一、目录结构与文件清单

```text
submission/
├── SKILL.md                  # Agent 触发元信息与 5 步标准执行 SOP
├── README.md                 # 完整使用说明、方法原理、依赖矩阵与已知边界
├── GITHUB_URL.txt            # 本题公开 GitHub 作品链接
├── scripts/
│   ├── inspect_case.py       # /input/case.json 与 task.md 合约梳理与 PDF 发现
│   ├── preflight.py          # 依赖探测、加密/损坏检查、逐页扫描与乱码率体检
│   ├── parse_pdf.py          # 核心版面解析器（XY-Cut 分栏、三线表重建、上下标、结构树）
│   ├── ocr_fallback.py       # 扫描页/高乱码页 300 DPI PNG 渲染与 Tesseract OCR 降级
│   └── audit_output.py       # 输出合约完整性与页码/区块真实性闭环审计
├── resources/
│   ├── symbol_map.json       # 连字修复、Symbol 字体映射与乱码判定阈值词表
│   └── output_schema.json    # parsed_document.json 与 result.json 标准校验规范
└── examples/
    └── minimal_example.md    # 最小完整调用示例与示意输出（非正式案例结果）
```

全部文件均为纯 UTF-8 文本文件，不依赖个人私有路径或预制测试答案。

---

## 二、核心能力与方法说明

### 1. 环境自适与异常处理（`preflight.py`）
- 自动探测 Python 后端（`pymupdf/fitz`、`pdfplumber`、`pypdf`、`PIL`）和命令行后端（`pdftohtml`、`pdftotext`、`pdfinfo`、`pdftoppm`、`tesseract`）。
- 对输入 PDF 预先核查文件头 `%PDF-`、空文件、加密需密码（`needs_pass`）及每页非空白字符数、嵌入式光栅图面积占比 `image_area_ratio`、乱码/私有区/CID 替换符占比 `garble_ratio`，按页分类为 `text`、`scanned_or_image`、`garbled_font_layer`、`empty`。
- 遇加密或损坏文件明确退出并返回结构化诊断 JSON，不靠猜补齐。

### 2. 多栏版面排序与跨页页眉页脚剥离（`parse_pdf.py`）
- **跨页页眉/页脚/边注指纹剥离**：将页面顶部 `11%`、底部 `11%` 及两侧 `8%` 窄带中的短文本数字折叠为 `#` 进行跨页频次统计，识别重复页眉、页脚、单独页码及侧边纵向 `arXiv` 时间戳并单独归档到 `headers_footers`，避免混入正文。
- **Line-Internal Column Bleed 拆分**：针对 PyMuPDF 偶发将狭窄双栏行合并进同一行/同一块的问题，按横向大空白间距（`> max(14pt, 2.0 * font_size)`）先拆原子片段再做 XY聚类。
- **自适应跨栏分割 + XY-Cut 阅读顺序**：基于窄文本块 X 轴投影零占空沟槽定位两栏或三栏分隔线，识别跨栏通栏元素（Title、Abstract、跨栏表格/图注/章节标题），将页面切分为垂直带，按“带内从左栏到右栏、栏内从上到下”排序，脚注统一归置于页末 `footnote` 区，并对跨行连字符断词执行修复。

### 3. 学术三线表 / 网格表重建与跨页续表合并（`parse_pdf.py`）
- 提取页面水平/垂直矢量线段（`get_drawings()` 中的线与细矩形），聚类形成学术论文常见的 Booktabs 三线表与带框表格区域，并匹配相邻 `Table X` / `表 X` 题注。
- 利用表内 `midrule` 区分表头行与数据行，基于数据行原子 span 的 X 轴投影零占空间隙重建列边界，识别跨列合并表头（`colspan > 1`），输出二维 `grid`、单元格 `cells` 与标准 Markdown 表格。
- 对相邻页出现 `continued` / `续表` 标识或顶部无题注且列数一致的表格自动生成 `merged_tables`，同时保留原分表 `page` 与 `bbox`。

### 4. 上下标还原、公式编号与符号归一化（`parse_pdf.py` + `symbol_map.json`）
- 结合字体字号比例（`size <= 0.84 * body_size`）与基线偏移量（`origin_y`）及 MuPDF superscript 标志，自动还原化学式、指数与索引上下标（如 `CHCl_{3}`、`10^{-6}`、`g_{CC}(r)`），同时保留未改写 `raw_text` 便于逐字对照。
- 识别 `ﬁ/ﬂ/ﬀ` 连字并归一化，内置常见 `Symbol` 字体映射；对不可映射的 `U+FFFD`、私有区 PUA 字符或 `(cid:xxx)` 保留占位标记并计入 `garble_ratio`，触发警示。
- 识别行末/右对齐编号公式 `(1)`、`[2]` 等写入 `equations`，识别章节层级写入 `sections`，识别 `Fig.`/`图` 题注写入 `figures`，识别参考文献条目写入 `references`。

### 5. 扫描件 / 高乱码页 OCR 降级与闭环审计（`ocr_fallback.py` + `audit_output.py`）
- 自动将 `ocr_recommended_pages` 渲染为 300 DPI PNG，借助 Pillow 做对比度增强，并调用 `tesseract ... tsv` 提取文本、平均置信度与 `<60%` 低置信词表。若容器缺失 `tesseract`，仍保留 300 DPI 渲染 PNG 路径供人工或视觉排查。
- `audit_output.py` 在提交前核验 `result.json` 是否覆盖 `/input/case.json` 中 `output_contract` 的全部字段、`case_id` 是否一致、`sources` 中引用的 `page` 与 `block_id/table_id/eq_id` 是否全部真实存在于 `parsed_document.json`，以及扫描/乱码页是否已在 `uncertainty` 如实披露。

---

## 三、依赖矩阵与回退机制

| 层级 | 推荐依赖 | 缺失时的自动回退策略 |
| :--- | :--- | :--- |
| 主解析引擎 | `pymupdf` (`fitz`) | 回退至 `pdftohtml -xml`（保留坐标并运行同一套 XY-Cut）→ `pdftotext -layout` |
| 页面渲染 | `pymupdf` (`get_pixmap`) | 回退至 Poppler `pdftoppm -png -r 300` |
| OCR 降级 | `tesseract` + `PIL` | 无 `PIL` 时跳过预处理直传 PNG；无 `tesseract` 时输出 PNG 并标明 `rendered_png_no_tesseract` |
| 审计与病例检查 | Python 标准库 (`>= 3.8`) | 零外部依赖 |

---

## 四、快速调用命令

```bash
# 1. 梳理当前运行案例契约
python3 scripts/inspect_case.py --case /input/case.json --task /input/task.md --out /app/work/case_brief.json

# 2. PDF 体检与工具探测
python3 scripts/preflight.py /input/paper.pdf --out /app/work/preflight.json

# 3. 执行结构化解析
python3 scripts/parse_pdf.py /input/paper.pdf --out-dir /app/work/parsed

# 4. 仅针对扫描/高乱码页渲染并执行 OCR
python3 scripts/ocr_fallback.py /input/paper.pdf --from-parsed /app/work/parsed/parsed_document.json --out-dir /app/work/ocr

# 5. 输出结果闭环审计
python3 scripts/audit_output.py --result /app/output/result.json --case /input/case.json --parsed /app/work/parsed/parsed_document.json --out /app/work/audit_report.json
```

---

## 五、已知限制与异常处置原则

1. **无文字层数学公式**：当公式以内嵌矢量路径或低分辨率光栅图片存在时，`parse_pdf.py` 无法直接解析字符；需通过 `ocr_fallback.py` 渲染高分辨率 PNG 供人工或视觉核验，严禁自行编造公式。
2. **极窄无横线空白表**：当表格既无任何水平/垂直矢量线条、又缺少 `Table` 题注锚点时，启发式表格检测可能将其作为普通多栏块提取，可结合 `parsed_document.md` 中对齐行核对并在 `change_records` 记录修正。
3. **跨栏/跨页边界冲突**：若论文页内存在极少见的局部三栏与图片交错，仍可通过 `parsed_document.json` 中每个块的 `bbox` 坐标核验原文准确位置。
