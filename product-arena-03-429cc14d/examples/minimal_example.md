# Minimal Illustrative Example (`academic-citation-manager`)

> **Note**: This is an illustrative example using `examples/sample_manuscript.md`, `examples/sample_library.bib`, and `examples/sample_library.ris` to demonstrate interface contracts — **not** a formal competition case output.

## 1. Sample Input
- Manuscript: `examples/sample_manuscript.md` (contains a paragraph-end citation cluster `[1-2]`, a missing citation placeholder `[待引用]`, and a dangling reference `[99]`)
- Reference libraries: `examples/sample_library.bib` + `examples/sample_library.ris` (contains an arXiv preprint + NeurIPS published duplicate of *Attention Is All You Need* plus a distinct 2023 catalyst paper)

## 2. Invocation Steps
```bash
mkdir -p /tmp/demo_in /tmp/demo_work /tmp/demo_out
cp examples/sample_manuscript.md /tmp/demo_in/manuscript.md
cp examples/sample_library.bib /tmp/demo_in/refs.bib
cp examples/sample_library.ris /tmp/demo_in/refs.ris
echo '{"case_id": "demo_01"}' > /tmp/demo_in/case.json
echo '请按 GB/T 7714-2015 顺序编码制（上标方括号）整理引用。' > /tmp/demo_in/task.md

# Step 1: Prepare & save early partial checkpoint
python3 -B scripts/prepare_case.py --input-dir /tmp/demo_in --work-dir /tmp/demo_work --out-dir /tmp/demo_out
```

## 3. Sample `citation_plan.json` (Illustrative)
```json
{
  "case_id": "demo_01",
  "target_style": "gbt7714_2015_numeric",
  "in_text_format": "superscript_bracket",
  "manuscript_edits": [
    {
      "sentence_id": "S001",
      "action": "split_cluster_to_clauses",
      "revised_sentence_with_tokens": "Transformer 架构通过自注意力机制显著提升了序列建模能力[[CITE:REF_001]]。",
      "anchor_rationale": "Relocate citation directly to the Transformer claim clause."
    },
    {
      "sentence_id": "S002",
      "action": "flag_insufficient_evidence",
      "revised_sentence_with_tokens": "某未公开催化剂在实验中实现了 96.5% 的转化率[证据不足/待人工核验]。",
      "anchor_rationale": "No supporting record in library for 96.5% catalyst claim."
    }
  ],
  "claim_verifications": [
    {
      "claim_id": "C001",
      "sentence_id": "S001",
      "cited_ref_ids": ["REF_001"],
      "verdict": "supported_by_abstract",
      "evidence_locator": "sample_library.bib:1",
      "evidence_quote": "Attention Is All You Need"
    }
  ]
}
```

```bash
# Step 3: Synchronize, verify 10 invariants, and assemble deliverables
python3 -B scripts/verify_and_assemble.py --work-dir /tmp/demo_work --out-dir /tmp/demo_out --plan /tmp/demo_work/citation_plan.json
```

## 4. Expected Illustrative Output
- `/tmp/demo_out/revised_manuscript.md`:
  - `S001` receives `<sup>[1]</sup>` (merged NeurIPS published record prioritized over arXiv preprint)
  - `S002` retains `[证据不足/待人工核验]`
  - Dangling `[99]` becomes `[待核验引用: 原标记 99]`
  - Bibliography contains strictly `[1]` and `[2]` in first-appearance order (`1..2`).
- `/tmp/demo_out/result.json`: `status: "completed"`, `verification_summary.passed: true`.
