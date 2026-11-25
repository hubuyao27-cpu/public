# `sci-intro-rw-writer` — 引言与相关工作写作与证据审计 Skill

**Bohrium Playground 题目**：`product-arena-04-4b82ad21`（写作08｜引言与相关工作写作：学术写作：完成产物，也保留来源）  
**开源维护仓库**：`https://github.com/hubuyao27-cpu/public/tree/main/product-arena-04-4b82ad21`

---

## 1. 设计目标与四大核心检验标准

科研论文的引言（Introduction）与相关工作（Related Work）写作在通用大模型与文献综述工具中常见四类失真问题：空泛铺垫、流水账堆砌、缺乏依据的空白断言，以及贡献夸大与幻觉引用。`sci-intro-rw-writer` 通过**确定性材料抽取 + 结构化写作载荷 + 十轴机械校验 + 风格一致渲染**的 4 步精简流程，严格落实以下四项标准：

1. **立得住（研究背景 — CARS Move 1）**：拒绝“近年来受到广泛关注”“随着……快速发展”等空洞模板句，从领域真实研究脉络、具体技术约束与当前紧迫性切入，句句可追溯至文献或工程底稿。
2. **分得清（相关工作组织 — 对比综合）**：拒绝按年份/作者逐篇罗列摘要或无锚点堆砌 `[1-5]`，强制按**问题设定 / 技术路线 / 数据与评价方式**分为 2–4 个逻辑分组，输出统一维度的对比矩阵（`related_work_matrix.md/.csv`，含标题列），明确各工作核心结论、适用条件与局限性，并以桥接段收束至本文切入点。
3. **说得实（研究空白推导 — CARS Move 2）**：每条研究空白须由已引文献的**共同局限**、**未覆盖场景**或**结论冲突**推导而出，并对应说明本文方案如何填补；属于作者归纳推断而文献未直接支撑之处，必须显式标注 `[作者判断]` / `[AUTHOR_JUDGMENT]`。
4. **对得上（贡献表述与引用覆盖 — CARS Move 3）**：每条贡献必须 1-to-1 绑定本文实际方法、图表编号（`Fig. X` / `Table Y`）、量化结果（带单位兼容性与精度容差核验）及适用边界；每条引用可回溯至输入 `.bib`/`.ris`/`.json`/`.csv`/`.pdf`/`.docx`/`.md` 原文，终稿组装时自动将内部 `[R01]` 统一渲染为目标引用风格（`[1]`、`[1], [2]`、`(Author, Year)` 或 `\cite{key}`），元信息冲突（`CONF-XX`）与缺失项（`GAP-XX`）显式暴露。

---

## 2. 目录结构

```text
submission/
├── SKILL.md                                  # Harbor Agent 调用入口（标准 YAML frontmatter + 精简 4 步流程）
├── README.md                                 # 本说明文档
├── GITHUB_URL.txt                            # 开源维护地址
├── examples/
│   └── minimal_example.md                    # 最小可运行调用示例与示意输出
├── resources/
│   ├── payload_schema.json                   # section_payload.json 结构规范（支持外置 manuscript_file）
│   ├── rubric_rules.json                     # 动态加载的套话/夸大词库、五种引用格式模板、证据优先级
│   ├── edge_cases_and_fallbacks.md           # 七类异常场景处置规范
│   └── templates/
│       ├── intro_rw_template.md              # 中英文引言与相关工作写作模板
│       └── audit_report_template.md          # 证据、引用与来源审计报告模板
└── scripts/
    ├── common.py                             # PDF/DOCX/BibTeX/BibLaTeX/RIS 解析、数字单位校验、目标风格引文渲染与契约解析
    ├── inspect_case.py                       # Step 1a：契约解析、目录感知文件分类、模式与格式检测
    ├── ingest_materials.py                   # Step 1b：按证据优先级归并文献、原版 PDF 首页卡片抽取、冲突检测与 skeleton 生成
    ├── prepare_workspace.py                  # Step 1 一键入口：串联 inspect -> ingest -> partial 断点落盘
    ├── verify_intro_rw.py                    # Step 3：十轴模式感知机械防幻觉与结构完整性校验器
    └── assemble_deliverables.py              # Step 4：断点安全落盘、目标风格引文渲染与结构化 LaTeX/DOCX/MD/图谱组装
```

---

## 3. 依赖、环境与支持范围

- **核心依赖**：Python **3.9+** 纯标准库即可完整运行全流程（包括 `.bib`、`.ris`、`.json`、`.csv`/`.tsv`、`.md`、`.txt`、`.tex` 与基于 `zipfile + xml.etree` 的 `.docx` 段落+表格解析）。
- **可选增强工具**（存在时自动启用，缺失或超时自动降级并记录 `uncertainty[]`，绝不阻塞 `result.json` 落盘）：
  - PDF 提取与渲染：`pypdf`、`PyMuPDF (fitz)`、`pdfplumber` 或 `pdftotext`（支持低字数质量回退）；扫描件 OCR：`pdftoppm` + `tesseract`（带超时保护）。
  - 扩展格式导出与预览：`python-docx` + LibreOffice (`soffice`)、LaTeX (`xelatex`/`pdflatex` 双轮编译 + `-no-shell-escape`) + `pdftoppm`、`matplotlib`（生成路线图 `related_work_taxonomy.png/.svg`）。
- **支持语言**：中文（`zh`）、英文（`en`）、中英双语（`bilingual`）。
- **支持引用格式**：`GB/T 7714-2015`、`IEEE Numeric`、`APA 7th Author-Year`、`Nature Numeric`、`LaTeX \cite{key} + BibTeX`；未指定时自动匹配语言默认规范并显式声明。

---

## 4. 精简 4 步调用流程（Harbor 与本地通用）

1. **一键预准备、证据抽取与早期诚实断点**
   ```bash
   python3 -B "$SKILL_DIR/scripts/prepare_workspace.py" \
     --input-dir /input --work-dir /app/work --out-dir /app/output
   ```
   单命令串联 `inspect_case.py -> ingest_materials.py -> assemble_deliverables.py --checkpoint partial`，输出 `case_plan.json`、`source_ledger.json`、`evidence_digest.md`、`section_payload_skeleton.json` 与早期 `/app/output/result.json`。
2. **阅读 `/app/work/evidence_digest.md` 并完成 `/app/work/section_payload.json`**
   基于预填充的 `section_payload_skeleton.json` 补全背景、分组相关工作、对比矩阵、空白推导与贡献对齐；长文本正文既可内联写入 `manuscript_markdown`，也可单独写为 `/app/work/manuscript.md` 并通过 `manuscript_file` 引用，避免 JSON 转义开销。
3. **十轴机械校验与自修正闭环**
   ```bash
   python3 -B "$SKILL_DIR/scripts/verify_intro_rw.py" \
     --payload /app/work/section_payload.json \
     --ledger /app/work/source_ledger.json \
     --case-plan /app/work/case_plan.json \
     --out /app/work/verification_report.json
   ```
   若返回非零退出码（`error_count > 0`），按 `fix_hint` 修正后重跑直至 `"passed": true`。
4. **组装最终交付产物**
   ```bash
   python3 -B "$SKILL_DIR/scripts/assemble_deliverables.py" \
     --case-plan /app/work/case_plan.json \
     --ledger /app/work/source_ledger.json \
     --payload /app/work/section_payload.json \
     --verification /app/work/verification_report.json \
     --checkpoint final --out-dir /app/output
   ```

---

## 5. 输出产物说明

| 文件 | 用途 |
|---|---|
| `result.json` | 严格保留 `case_id` 与输入 `output_contract` 全部字段，附带目标风格渲染后的正文、`status`、`sources`、`uncertainty`、`change_records`、`verification_summary` |
| `introduction_and_related_work.md` | 可直接并入论文的引言、相关工作正文（含目标风格内文引文）与统一格式参考文献列表 |
| `related_work_matrix.md` / `.csv` | 按方法路线分组的多维对比矩阵（含 `Title`、问题设定、路线、数据/评测、结论、适用条件、局限、来源定位） |
| `evidence_and_citation_audit.md` | 7 段式独立复核报告（覆盖率与年代分布、排除文献、论断-引用映射、空白推导链与定位、贡献-证据对照、双源冲突明细、优先级分组确认表、机械校验指标） |
| `provenance_map.json` | 机器可读的论断、空白、贡献与原文件定位图谱 |
| 条件导出文件 | 仅当任务请求且在 `final` 阶段生成 `.tex`（双轮安全编译）/`.bib`/`.docx`/`.png` 路线图及渲染预览 |

---

## 6. 架构权衡与适用边界（Alternatives Considered & Limits）

- **为何采用 `section_payload_skeleton.json` + 可选外置 `manuscript_file`**：相比让模型从零手写整份 JSON 或自由输出纯文本，预填充骨架可防止作者/年份抄错导致的 `METADATA_YEAR_MISMATCH`，而可选外置 Markdown 稿件避免了长文 JSON 转义错误。
- **为何在组装阶段统一渲染 `[R01]` 为目标引文风格**：起草阶段使用稳定的内部 ID（`[R01]`）可防止序号漂移与格式错位；终稿组装阶段由确定性渲染器统一映射为 `[1]`、`[1], [2]`、`(Author et al., Year)` 或 `\cite{key}`，实现正文与文末列表 100% 一致。
- **客观边界说明**：
  - 本 Skill 不联网检索外部未提供文献库，输入为空或全部损坏时强制输出 `insufficient_material`。
  - 对纯图片扫描版 PDF，若宿主容器未安装 `pdftoppm + tesseract`，将如实记录 `scanned_or_low_text_page` 告警与待确认项，不进行臆测补全。
  - 跨文件元数据冲突严格按 `original_paper_or_pdf > bibtex_or_csl_metadata > own_paper_results_or_tables > structured_notes` 排序并保留双值供作者确认。
