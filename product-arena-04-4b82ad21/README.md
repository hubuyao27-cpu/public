# `sci-intro-rw-writer` — 引言与相关工作写作与证据审计 Skill

**Bohrium Playground 题目**：`product-arena-04-4b82ad21`（写作08｜引言与相关工作写作：学术写作：完成产物，也保留来源）  
**开源维护仓库**：`https://github.com/hubuyao27-cpu/public/tree/main/product-arena-04-4b82ad21`

---

## 1. 设计目标与四大核心检验标准

科研论文的引言（Introduction）与相关工作（Related Work）写作在通用大模型与文献综述工具中常见四类失真问题：空泛铺垫、流水账堆砌、缺乏依据的空白断言，以及贡献夸大与幻觉引用。`sci-intro-rw-writer` 通过**确定性材料抽取 + 结构化写作载荷 + 十轴机械校验**流程，严格落实以下四项标准：

1. **立得住（研究背景）**：拒绝“近年来受到广泛关注”“随着……快速发展”等空洞模板句，从领域真实研究脉络、具体技术约束与当前紧迫性切入，句句可追溯至文献或工程底稿。
2. **分得清（相关工作组织）**：拒绝按年份/作者逐篇罗列摘要或无锚点堆砌 `[1-5]`，强制按**问题设定 / 技术路线 / 数据与评价方式**分为 2–4 个逻辑分组，输出统一维度的对比矩阵（`related_work_matrix.md/.csv`），明确各工作核心结论、适用条件与局限性。
3. **说得实（研究空白推导）**：每条研究空白须由已引文献的**共同局限**、**未覆盖场景**或**结论冲突**推导而出，并对应说明本文方案如何填补；属于作者归纳推断而文献未直接支撑之处，必须显式标注 `[作者判断]` / `[AUTHOR_JUDGMENT]`。
4. **对得上（贡献表述与引用覆盖）**：每条贡献必须 1-to-1 绑定本文实际方法、图表编号（`Fig. X` / `Table Y`）、量化结果（允许精度容差与百分比等效）及适用边界；每条引用可回溯至输入 `.bib`/`.ris`/`.json`/`.csv`/`.pdf`/`.docx`/`.md` 原文，元信息冲突（`CONF-XX`）与缺失项（`GAP-XX`）显式暴露。

---

## 2. 目录结构

```text
submission/
├── SKILL.md                                  # Harbor Agent 调用入口（标准 YAML frontmatter + 6 步流程）
├── README.md                                 # 本说明文档
├── GITHUB_URL.txt                            # 开源维护地址
├── examples/
│   └── minimal_example.md                    # 最小可运行调用示例与示意输出
├── resources/
│   ├── payload_schema.json                   # section_payload.json 结构规范
│   ├── rubric_rules.json                     # 套话/夸大词库、五种引用格式模板、证据优先级
│   ├── edge_cases_and_fallbacks.md           # 七类异常场景处置规范
│   └── templates/
│       ├── intro_rw_template.md              # 中英文引言与相关工作写作模板
│       └── audit_report_template.md          # 证据、引用与来源审计报告模板
└── scripts/
    ├── common.py                             # PDF/DOCX/BibTeX/RIS/Markdown 解析与数字单位校验（纯标准库核心）
    ├── inspect_case.py                       # Step 1：契约解析、模式与格式检测、目录扫描
    ├── ingest_materials.py                   # Step 2：多源文献归并、冲突检测、草稿预审、紧凑摘要与 skeleton 生成
    ├── prepare_workspace.py                  # 一键预准备入口：串联 inspect -> ingest -> partial 断点输出
    ├── verify_intro_rw.py                    # Step 3/5：十轴模式感知机械防幻觉与格式一致性校验器
    └── assemble_deliverables.py              # Step 4/6：断点续存与结构化 LaTeX/DOCX/MD/图谱最终组装
```

---

## 3. 依赖、环境与支持范围

- **核心依赖**：Python **3.9+** 纯标准库即可完整运行全流程（包括 `.bib`、`.ris`、`.json`、`.csv`/`.tsv`、`.md`、`.txt`、`.tex` 与基于 `zipfile + xml.etree` 的 `.docx` 解析）。
- **可选增强工具**（存在时自动启用，缺失时自动降级并记录 `uncertainty[]`）：
  - PDF 提取与渲染：`pypdf`、`PyMuPDF (fitz)`、`pdfplumber` 或 `pdftotext`；扫描件 OCR：`pdftoppm` + `tesseract`。
  - 扩展格式导出与预览：`python-docx` + LibreOffice (`soffice`)、LaTeX (`xelatex`/`pdflatex`) + `pdftoppm`、`matplotlib`（生成路线图 `related_work_taxonomy.png/.svg`）。
- **支持语言**：中文（`zh`）、英文（`en`）、中英双语（`bilingual`）。
- **支持引用格式**：`GB/T 7714-2015`、`IEEE Numeric`、`APA 7th Author-Year`、`Nature Numeric`、`LaTeX \cite{key} + BibTeX`；当目标期刊或引用格式未指定时，自动选用与语言匹配的规范格式并在 `uncertainty[]` 中显式声明。

---

## 4. 完整调用流程（Harbor 与本地通用）

1. **解析任务契约与输入清单**
   ```bash
   python3 "$SKILL_DIR/scripts/inspect_case.py" --input-dir /input --out /app/work/case_plan.json
   ```
2. **抽取多源文献、自身证据与冲突预审**
   ```bash
   python3 "$SKILL_DIR/scripts/ingest_materials.py" --case-plan /app/work/case_plan.json \
     --out-ledger /app/work/source_ledger.json --out-digest /app/work/evidence_digest.md
   ```
3. **保存早期诚实断点（防止高负载超时无产出）**
   ```bash
   python3 "$SKILL_DIR/scripts/assemble_deliverables.py" --case-plan /app/work/case_plan.json \
     --ledger /app/work/source_ledger.json --checkpoint partial --out-dir /app/output
   ```
4. **阅读 `/app/work/evidence_digest.md` 并撰写 `/app/work/section_payload.json`**（按 `resources/payload_schema.json` 填写背景、分组相关工作、对比矩阵、空白推导、证据对齐贡献、参考文献、冲突及待确认问题）。
5. **十轴机械校验与自修正闭环**
   ```bash
   python3 "$SKILL_DIR/scripts/verify_intro_rw.py" --payload /app/work/section_payload.json \
     --ledger /app/work/source_ledger.json --case-plan /app/work/case_plan.json \
     --out /app/work/verification_report.json
   ```
   若返回非零退出码（`error_count > 0`），按错误提示修正 `section_payload.json` 后重跑直至 `"passed": true`。
6. **组装最终交付产物**
   ```bash
   python3 "$SKILL_DIR/scripts/assemble_deliverables.py" --case-plan /app/work/case_plan.json \
     --ledger /app/work/source_ledger.json --payload /app/work/section_payload.json \
     --verification /app/work/verification_report.json --checkpoint final --out-dir /app/output
   ```

---

## 5. 输出产物说明

| 文件 | 用途 |
|---|---|
| `result.json` | 严格保留 `case_id` 与输入 `output_contract` 全部字段，附带 `status`、`sources`、`uncertainty`、`change_records`、`verification_summary` |
| `introduction_and_related_work.md` | 可直接并入论文的引言、相关工作正文与统一格式参考文献列表 |
| `related_work_matrix.md` / `.csv` | 按方法路线分组的多维对比矩阵（问题设定、路线、数据/评测、结论、适用条件、局限、来源定位） |
| `evidence_and_citation_audit.md` | 7 段式独立复核报告（引用元数据账本、论断-引用映射、空白推导链、贡献-证据对照、冲突与修改记录、闭环作者确认表、机械校验指标） |
| `provenance_map.json` | 机器可读的论断、空白、贡献与原文件定位图谱 |
| 条件导出文件 | 仅当任务请求时生成 `.tex`/`.bib`/`.docx`/`.png` 路线图及渲染预览 |

---

## 6. 异常处理与适用边界

- **文献库为空 / 缺失原文**：立即停止编造引用与数字，设 `status` 为 `insufficient_material` 或 `partial`，生成诚实大纲或缺口报告。
- **引用缺页码/年份或原文不可读（加密/损坏 PDF）**：保留已有 `cite_key`，将缺失字段标为 `unverified_source`/`partial_metadata` 并绑定 `[待作者确认: GAP-XX]`。
- **跨文件元数据或数字冲突（`CONF-XX`）**：遵循 `原论文 > BibTeX/CSL > 本文实验表 > 正文草稿` 优先级，同时在 `conflicts[]` 与审计报告中双值并列展示，交作者确认。
- **空白无引文直接支撑**：禁止伪装为文献既定结论，强制标注 `[作者判断]` / `[AUTHOR_JUDGMENT]` 并生成确认问题。
- **客观边界说明**：本 Skill 不联网检索外部未提供文献库；对仅靠纯图片且系统未安装 `pdftoppm + tesseract` 的扫描版 PDF 会如实报错提示，不进行猜测补全。
