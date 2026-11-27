# `sci-intro-rw-writer` — 引言与相关工作写作与证据审计 Skill

**Bohrium Playground 题目**：`product-arena-04-4b82ad21`（写作08｜引言与相关工作写作：学术写作：完成产物，也保留来源）  
**开源维护仓库**：`https://github.com/hubuyao27-cpu/public/tree/main/product-arena-04-4b82ad21`

---

## 1. 设计目标与四大核心检验标准

科研论文的引言（Introduction）与相关工作（Related Work）写作在通用大模型与文献综述工具中常见四类失真问题：空泛铺垫、流水账堆砌、缺乏依据的空白断言，以及贡献夸大与幻觉引用。`sci-intro-rw-writer` 通过**确定性无损抽取 + 按需检索 + 结构化写作载荷 + 十轴机械校验 + 非破坏性断点组装**流程，严格落实以下四项标准：

1. **立得住（研究背景 — CARS Move 1）**：拒绝“近年来受到广泛关注”“随着……快速发展”等空洞模板句，从领域真实研究脉络、具体技术约束与当前紧迫性切入，句句可追溯至文献或工程底稿。
2. **分得清（相关工作组织 — 二维对比综合 + 桥接段）**：拒绝按年份/作者逐篇罗列摘要或无锚点堆砌 `[1-5]`，强制按**问题设定 / 技术路线 / 数据与评价方式**分为 2–4 个逻辑分组，输出统一维度的对比矩阵（`related_work_matrix.md/.csv`，含标题列），明确各工作核心结论、适用条件与局限性，并以桥接段（Bridge Paragraph）收束至本文切入点。
3. **说得实（研究空白推导 — CARS Move 2）**：每条研究空白须由已引文献的**共同局限**、**未覆盖场景**或**结论冲突**推导而出，并对应说明本文方案如何填补；属于作者归纳推断而文献未直接支撑之处，必须显式标注 `[作者判断]` / `[AUTHOR_JUDGMENT]`。
4. **对得上（贡献表述与引用覆盖 — CARS Move 3）**：每条贡献以“方法定位 -> 指标定位 -> 工况/数据集范围 -> 适用边界”四要素对齐本文实际方法、图表与实验结果；每条引用可回溯至输入 `.bib`/`.ris`/`.json`/`.csv`/`.pdf`/`.docx`/`.md` 原文，终稿组装时自动将内部 `[R01]` 统一渲染为目标引用风格（`[1]`、`[1], [2]`、`(Author, Year)` 或 `\cite{key}`），并结合期刊缩写规范化表过滤伪冲突，显式暴露真实元信息冲突（`CONF-XX`）与缺失项（`GAP-XX`）。

---

## 2. 目录结构

```text
submission/
├── SKILL.md                                  # Harbor Agent 调用入口（CARS 写作准则 + 3 步精简流程）
├── README.md                                 # 本说明文档
├── GITHUB_URL.txt                            # 开源维护地址
├── examples/
│   └── minimal_example.md                    # 最小可运行调用示例与 Base-vs-Skill 对照示意输出
├── resources/
│   ├── payload_schema.json                   # section_payload.json 结构规范（支持外置 manuscript_file）
│   ├── rubric_rules.json                     # 套话/夸大词库、期刊缩写规范化映射、五种引用模板与证据优先级
│   ├── edge_cases_and_fallbacks.md           # 七类异常场景处置规范
│   └── templates/
│       ├── intro_rw_template.md              # Base 反模式 vs Gold 范式对照 + 中英文写作模板
│       └── audit_report_template.md          # 证据、引用与来源审计报告模板
└── scripts/
    ├── common.py                             # PDF/DOCX/BibTeX/RIS 解析、期刊名规范化、数字单位校验、引文渲染
    ├── inspect_case.py                       # Step 1a：契约解析、精准输出格式识别（避免误触发 LaTeX）、目录扫描
    ├── ingest_materials.py                   # Step 1b：无损 [locator] 文本保存、文献归并、冲突过滤与 skeleton 生成
    ├── prepare_workspace.py                  # Step 1 一键入口：inspect -> ingest -> partial 断点落盘
    ├── query_sources.py                      # Step 2 按需检索：针对 /app/work/extracted/ 的精准段落/局限/表格检索
    ├── verify_intro_rw.py                    # Step 3a：十轴模式感知机械校验（结构化数值硬门控 + 正文柔性告警）
    ├── assemble_deliverables.py              # Step 3b：非破坏性断点保存、首次出现顺序重排与多格式组装
    ├── finalize_workspace.py                 # Step 3 一键收尾：校验通过输出 final，失败则保留稿件刷新 partial 并提示修复
    └── self_test.py                          # 内置零残留端到端自检脚本（< 2 秒）
```

---

## 3. 依赖、环境与支持范围

- **核心依赖**：Python **3.9+** 纯标准库即可完整运行全流程（包括 `.bib`、`.ris`、`.json`、`.csv`/`.tsv`、`.md`、`.txt`、`.tex` 与基于 `zipfile + xml.etree` 的 `.docx` 段落+表格解析）。
- **可选增强工具**（存在时自动启用，缺失或超时自动降级并记录 `uncertainty[]`，绝不阻塞 `result.json` 落盘）：
  - PDF 提取与渲染：`pypdf`、`PyMuPDF (fitz)`、`pdfplumber` 或 `pdftotext`；扫描件 OCR：`pdftoppm` + `tesseract`（带超时保护）。
  - 扩展格式导出与预览：`python-docx` + LibreOffice (`soffice`)、LaTeX (`xelatex`/`pdflatex` 双轮安全编译) + `pdftoppm`、`matplotlib`（生成路线图 `related_work_taxonomy.png/.svg`）。
- **支持语言**：中文（`zh`）、英文（`en`）、中英双语（`bilingual`）。
- **支持引用格式**：`GB/T 7714-2015`、`IEEE Numeric`、`APA 7th Author-Year`、`Nature Numeric`、`LaTeX \cite{key} + BibTeX`；未指定时自动匹配语言默认规范并显式声明。

---

## 4. 精简 3 步调用与自检流程

1. **一键预准备、无损抽取与早期诚实断点**
   ```bash
   python3 -B "$SKILL_DIR/scripts/prepare_workspace.py" \
     --input-dir /input --work-dir /app/work --out-dir /app/output
   ```
2. **阅读 `/app/work/evidence_digest.md`（按需用 `query_sources.py` 深查原文），撰写 `/app/work/manuscript.md` 与 `/app/work/section_payload.json`**
   ```bash
   python3 -B "$SKILL_DIR/scripts/query_sources.py" \
     --extracted-dir /app/work/extracted --section Limitations --limit 25
   ```
3. **一键校验与非破坏性终稿组装**
   ```bash
   python3 -B "$SKILL_DIR/scripts/finalize_workspace.py" \
     --work-dir /app/work --out-dir /app/output
   ```
   若返回非零退出码（`error_count > 0`），`finalize_workspace.py` 会**完整保留已写正文**并写入诚实 `status: "partial"` 断点，同时输出精准 `fix_hint` 供修复重跑直至 `"verification_passed": true`。
4. **独立环境自检**
   ```bash
   python3 -B "$SKILL_DIR/scripts/self_test.py"
   ```

---

## 5. 输出产物与异常处理

| 文件 | 用途 |
|---|---|
| `result.json` | 严格保留 `case_id` 与输入 `output_contract` 全部字段，附带目标风格渲染正文、`status`、`sources`、`uncertainty`、`change_records`、`verification_summary` |
| `introduction_and_related_work.md` | 可直接并入论文的引言、相关工作正文（含目标风格内文引文）与统一格式参考文献列表 |
| `related_work_matrix.md` / `.csv` | 按方法路线分组的多维对比矩阵（含 `Title`、问题设定、路线、数据/评测、结论、适用条件、局限、来源定位） |
| `evidence_and_citation_audit.md` | 7 段式独立复核报告（覆盖率与年代分布、排除文献、论断-引用映射、空白推导链与定位、贡献-证据对照、双源冲突明细、优先级确认表、机械校验指标） |
| `provenance_map.json` | 机器可读的论断、空白、贡献与原文件定位图谱 |
| 条件导出文件 | 仅当任务**明确请求输出** `.tex`/`.bib`/`.docx`/`.png` 时在 `final` 阶段生成（不会因输入包含 PDF 文献而误触发 LaTeX 编译） |

- **异常边界**：文献库为空或全部文件损坏/加密时强制输出 `insufficient_material`；跨源期刊缩写（如 `JACS` 与 `Journal of the American Chemical Society`）自动归一化消除误报，真实年份/DOI/作者冲突按 `original_paper_or_pdf > bibtex_or_csl_metadata > own_paper_results_or_tables > structured_notes` 排序并保留双值交作者确认。
