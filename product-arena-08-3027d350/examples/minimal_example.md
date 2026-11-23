# 最小调用示例（Minimal Usage Example）

> [!NOTE]
> 以下全部输入、路径与输出内容均为**仅用于接口和流程说明的最小示意示例**，并非正式评测案例或正式测评结果。

---

## 1. 示意输入环境

假设某次本地测试案例目录包含以下文件：
- `/input/case.json`：
```json
{
  "case_id": "demo_paper_001",
  "input_file": "/input/demo_paper.pdf",
  "prompt": "提取表 1 中的催化反应温度与产率，并给出原文页码、表格编号及公式 (1) 的表述；如扫描页存在无法确认的数字，请明确标注。",
  "output_contract": {
    "required": [
      "case_id",
      "extracted_table",
      "equation_1",
      "status",
      "sources",
      "uncertainty",
      "change_records"
    ]
  }
}
```

---

## 2. 标准 5 步调用命令

```bash
SKILL_DIR="/skills/weekly-skill"

# Step 1: 读取 case.json 与发现 PDF
python3 "$SKILL_DIR/scripts/inspect_case.py" \
  --case /input/case.json --task /input/task.md --input-dir /input \
  --out /app/work/case_brief.json

# Step 2: 环境探测与 PDF 逐页体检
python3 "$SKILL_DIR/scripts/preflight.py" /input/demo_paper.pdf \
  --out /app/work/preflight.json

# Step 3: 版面顺序重构、三线表与公式提取
python3 "$SKILL_DIR/scripts/parse_pdf.py" /input/demo_paper.pdf \
  --out-dir /app/work/parsed

# Step 4: 若 preflight 或 parsed_document 标记需要 OCR，则渲染并识别对应页
python3 "$SKILL_DIR/scripts/ocr_fallback.py" /input/demo_paper.pdf \
  --from-parsed /app/work/parsed/parsed_document.json \
  --out-dir /app/work/ocr --dpi 300

# Step 5: 生成 /app/output/result.json 后进行闭环审计
python3 "$SKILL_DIR/scripts/audit_output.py" \
  --result /app/output/result.json \
  --case /input/case.json \
  --parsed /app/work/parsed/parsed_document.json \
  --out /app/work/audit_report.json
```

---

## 3. 示意输出（`result.json`，仅做字段结构演示）

```json
{
  "case_id": "demo_paper_001",
  "extracted_table": {
    "table_id": "p02_t01",
    "caption": "Table 1. Catalytic reaction conditions and yields.",
    "markdown": "| Entry | Temp (K) | Yield (%) |\n| --- | --- | --- |\n| 1 | 353 | 88.4 |"
  },
  "equation_1": {
    "eq_id": "p01_eq01",
    "label": "(1)",
    "text": "r = k_{obs} [A]^{2} exp(-E_{a}/RT) (1)"
  },
  "status": "completed",
  "sources": [
    {
      "claim_or_field": "extracted_table",
      "page": 2,
      "printed_page": "2",
      "block_id": "p02_t01",
      "section": "3. Results and Discussion",
      "quote": "Table 1. Catalytic reaction conditions and yields."
    },
    {
      "claim_or_field": "equation_1",
      "page": 1,
      "printed_page": "1",
      "block_id": "p01_eq01",
      "section": "2. Kinetic Model",
      "quote": "r = k_{obs} [A]^{2} exp(-E_{a}/RT) (1)"
    }
  ],
  "uncertainty": [],
  "change_records": [
    "Stripped 2 repeating header/footer blocks across 2 pages via fingerprint matching.",
    "Sorted 2-column body blocks with XY-Cut while keeping full-width abstract in order."
  ]
}
```
