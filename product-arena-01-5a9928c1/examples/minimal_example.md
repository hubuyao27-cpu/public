# 最小调用示例（示意示例，非正式测试案例结果）

> **声明**：以下输入、命令与输出仅为说明 Skill 接口与完整流程的**示意示例**，不代表平台正式测试案例的运行结果。

## 1. 示例输入（模拟 `/input/case.json` 与科研 PDF）

假设输入包含双栏科研段落、化学式 `CHCl_{3}`、单位 `kJ/mol` 与三线表 `Table 1`：
```text
[P001] We simulated liquid CHCl_{3} using a Stockmayer potential (SP) and obtained U = -25.65 ± 0.05 kJ/mol [1]. No significant phase separation was observed at 298 K (Table 1).
```

## 2. 执行命令（6 步标准流水线）

```bash
SKILL_DIR="/skills/weekly-skill"

# Step 1: 梳理 case.json 合约
python3 "$SKILL_DIR/scripts/inspect_case.py" --case /input/case.json --task /input/task.md --out /app/work/case_brief.json

# Step 2: XY-Cut 抽取分段与候选术语挖掘
python3 "$SKILL_DIR/scripts/extract_and_segment.py" --input /input/paper.pdf --out-dir /app/work/extracted

# Step 2b (若 extraction_report.json 中 ocr_recommended_pages 非空):
python3 "$SKILL_DIR/scripts/ocr_fallback.py" --input /input/paper.pdf --from-report /app/work/extracted/extraction_report.json --out-dir /app/work/ocr

# Step 3-4: 锁定 /app/work/glossary.json 并生成 /app/work/translated_segments.json

# Step 5: 七维机械核验
python3 "$SKILL_DIR/scripts/verify_translation.py" \
  --segments /app/work/extracted/segments.json \
  --translated /app/work/translated_segments.json \
  --glossary /app/work/glossary.json \
  --out /app/work/audit_report.json

# Step 6: 组装输出与严格校验
python3 "$SKILL_DIR/scripts/assemble_output.py" \
  --case /input/case.json \
  --translated /app/work/translated_segments.json \
  --glossary /app/work/glossary.json \
  --audit /app/work/audit_report.json \
  --extraction /app/work/extracted/extraction_report.json \
  --out-dir /app/output --strict
```

## 3. 示意输出片段（`/app/output/bilingual_translation.md`）

```markdown
### [P001] (Page 1)
**原文 (Source)**: We simulated liquid CHCl_{3} using a Stockmayer potential (SP) and obtained U = -25.65 ± 0.05 kJ/mol [1]. No significant phase separation was observed at 298 K (Table 1).

**译文 (Translation)**: 我们采用 Stockmayer 势（Stockmayer potential, SP）对液态 CHCl_{3} 进行了模拟，测得 U = -25.65 ± 0.05 kJ/mol [1]。在 298 K 下未观察到显著相分离（Table 1）。
- *译法依据*: 首次出现锁定术语“Stockmayer 势 (SP)”；完整保留化学式 CHCl_{3}、误差棒 ±0.05、单位 kJ/mol 与 K、引用 [1]、表号 Table 1 及否定词“未”。
```
