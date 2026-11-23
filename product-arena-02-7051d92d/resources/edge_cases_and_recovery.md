# Edge Cases & Exception Recovery Playbook (`academic-language-polisher`)

Follow these strict rules whenever inputs deviate from standard clean Markdown/TXT manuscripts:

## 1. Pure Factual Q&A / Non-Polishing Requests (Trigger Guard)
- **Condition**: `case_plan.json["signals"]["is_non_polish_qa"] == true` (e.g., user asks "What temperature did the catalyst run at?" or "Summarize Table 2", with no request to polish, proofread, copyedit, revise, or translate-polish text).
- **Action**: Do NOT rewrite or alter the manuscript. Set `status: "not_applicable_qa"` in `result.json`, state in `summary` and `limitations` that no editing was requested, and answer or explain honestly without touching source prose.

## 2. Long Manuscripts (> 15,000 characters or > 120 sentences)
- **Action**: Never paste the entire manuscript into one prompt or edit in one giant pass.
  1. Read `/app/work/polish_brief.md` and `/app/work/glossary_resolved.json` first.
  2. Read `/app/work/source_segments.json` in ordered slices of 25 sentences (`P001.S01`..).
  3. Build `/app/work/polished_payload.json` incrementally (saving after every slice so partial progress survives budget limits).
  4. Verify with `verify_polish.py` once all sentences are populated.

## 3. Encoding Mix-ups & Mojibake (GBK / GB18030 / UTF-8 BOM / Latin-1)
- `common.read_text_safe()` automatically tries `utf-8-sig`, `utf-8`, `gb18030`, `cp1252`, and `latin-1`.
- If fallback encoding was used or replacement characters (`\ufffd`) appear, record the affected file and line count in `uncertainty[]` and add a warning rather than silently guessing garbled characters.

## 4. LaTeX, Equations, Citations & Cross-References
- **Rule**: Treat `$...$`, `$$...$$`, `\(...\)`, `\[...\]`, `\begin{equation}...\end{equation}`, `\cite{...}`, `\ref{...}`, `\eqref{...}`, `[1, 2]`, and `(Author et al., 2021)` as **opaque protected tokens**.
- Never alter macro names, citation keys, labels, numeric indices, or equation contents when polishing surrounding prose.
- Always verify balanced `{}` braces and `$` delimiters after editing (`verify_polish.py` Axis 7 enforces this).

## 5. CJK-English-Japanese Mixed Scripts (中英日混排)
- Detect Japanese Kana (`[\u3040-\u30ff]`) or unexpected mixed punctuation (half-width `,`/`.` between Chinese characters, or full-width `，` inside English clauses).
- Fix CJK punctuation consistently according to target language (`zh` -> full-width `，。；：`, `en` -> half-width `, . ; :`), and flag stray non-Chinese Kana/mojibake tokens in `author_confirmation_items[]` rather than silently deleting potentially intentional loanwords or author names.

## 6. Internal Glossary Conflicts (`glossary_conflicts[]`)
- **Condition**: Input supplies two glossaries (or two entries in one glossary) mapping the same source term to conflicting target translations/spellings (e.g., `electrochemical impedance spectroscopy` -> `电化学阻抗谱` in Glossary A vs `交流阻抗谱` in Glossary B).
- **Action**:
  1. Never silently pick one and pretend no conflict existed.
  2. Document both definitions and source locations in `author_confirmation_items[]` and `uncertainty[]`.
  3. Apply the dominant or higher-precedence choice provisionally with `needs_human_confirmation: true` on affected sentences.

## 7. Ambiguous Target Genre or Register
- **Condition**: `task.md` does not explicitly specify whether the draft is a journal paper, thesis, grant proposal, or reviewer response, and cues are mixed.
- **Action**: State the inferred genre and evidence in `case_plan.json` / `uncertainty[]`, apply conservative academic prose (`journal_article` neutral standard), and add a closed-ended confirmation item if register changes (e.g., passive vs. active "we") depend on venue choice.

## 8. Missing, Encrypted, or Corrupt Input Files
- **Condition**: Referenced manuscript file is missing (`missing_referenced_files`), empty, encrypted PDF without password, or scanned image PDF without available OCR text.
- **Action**: Do NOT fabricate sample sentences. Output `status: "unable_to_determine"` (or `"partial"` if another readable file exists), populate `uncertainty[]` and `limitations[]` with exact file paths and system error messages, and run `assemble_output.py --checkpoint partial --strict`.
