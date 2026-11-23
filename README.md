# Public Scientific Agent Skills — Bohrium AI Product Arena (Season 1)

**玻尔 AI 产品周赛 · 北化分站赛 · 第一期（AI 文献阅读与学术写作）** 全十个赛题的 Harbor Agent Skills 统一开源仓库与项目集合。

---

## 十个赛题总览（10 Challenges & Harbor Skills）

| 序号 | 赛题 ID (`Challenge Slug`) | 赛题名称 | Skill 名称 | 目录链接 | 核心文档 |
|:---:|---|---|---|---|---|
| 01 | `product-arena-01-5a9928c1` | 阅读03｜文献翻译 | `scientific-literature-translator` | [`./product-arena-01-5a9928c1`](./product-arena-01-5a9928c1) | [SKILL.md](./product-arena-01-5a9928c1/SKILL.md) · [README.md](./product-arena-01-5a9928c1/README.md) |
| 02 | `product-arena-02-7051d92d` | 写作02｜学术语言润色 | `academic-language-polisher` | [`./product-arena-02-7051d92d`](./product-arena-02-7051d92d) | [SKILL.md](./product-arena-02-7051d92d/SKILL.md) · [README.md](./product-arena-02-7051d92d/README.md) |
| 03 | `product-arena-03-429cc14d` | 写作04｜参考文献与引用管理 | `academic-citation-manager` / `citation-reference-manager` | [`./product-arena-03-429cc14d`](./product-arena-03-429cc14d) · [`./product-arena-03-429cc14d-citation-manager`](./product-arena-03-429cc14d-citation-manager) | [SKILL.md](./product-arena-03-429cc14d/SKILL.md) · [Enhanced SKILL.md](./product-arena-03-429cc14d-citation-manager/SKILL.md) |
| 04 | `product-arena-04-4b82ad21` | 写作08｜引言与相关工作写作 | `sci-intro-rw-writer` | [`./product-arena-04-4b82ad21`](./product-arena-04-4b82ad21) | [SKILL.md](./product-arena-04-4b82ad21/SKILL.md) · [README.md](./product-arena-04-4b82ad21/README.md) |
| 05 | `product-arena-05-64faa4a2` | 写作09｜方法与实验章节写作 | `sci-methods-writer` | [`./product-arena-05-64faa4a2`](./product-arena-05-64faa4a2) | [SKILL.md](./product-arena-05-64faa4a2/SKILL.md) · [README.md](./product-arena-05-64faa4a2/README.md) |
| 06 | `product-arena-06-6d8cf72b` | 阅读10｜文献总结与结构化笔记 | `scientific-structured-notes` | [`./product-arena-06-6d8cf72b`](./product-arena-06-6d8cf72b) | [SKILL.md](./product-arena-06-6d8cf72b/SKILL.md) · [README.md](./product-arena-06-6d8cf72b/README.md) |
| 07 | `product-arena-07-865de7e4` | 写作13｜LaTeX 写作与编译 | `latex-sci-composer` | [`./product-arena-07-865de7e4`](./product-arena-07-865de7e4) | [SKILL.md](./product-arena-07-865de7e4/SKILL.md) · [README.md](./product-arena-07-865de7e4/README.md) |
| 08 | `product-arena-08-3027d350` | 阅读01｜文献文档解析 | `sci-pdf-parser` | [`./product-arena-08-3027d350`](./product-arena-08-3027d350) | [SKILL.md](./product-arena-08-3027d350/SKILL.md) · [README.md](./product-arena-08-3027d350/README.md) |
| 09 | `product-arena-09-0e8befd4` | 阅读14｜Nanopublication | `nanopublication-extractor` | [`./product-arena-09-0e8befd4`](./product-arena-09-0e8befd4) | [SKILL.md](./product-arena-09-0e8befd4/SKILL.md) · [README.md](./product-arena-09-0e8befd4/README.md) |
| 10 | `product-arena-10-760d1a0b` | 阅读15｜文献知识图谱构建 | `literature-kg-builder` | [`./product-arena-10-760d1a0b`](./product-arena-10-760d1a0b) | [SKILL.md](./product-arena-10-760d1a0b/SKILL.md) · [README.md](./product-arena-10-760d1a0b/README.md) |

---

## 目录结构约定

每个子目录对应一道赛题的完整 Harbor Skill 提交包（`submission/`），均包含：
- `SKILL.md`：包含 YAML frontmatter、执行契约、多阶段确定性工作流与质量门禁规则的主指令文件。
- `README.md`：使用指南、输入/输出契约与验证说明。
- `GITHUB_URL.txt`：指向本公开仓库对应子目录的链接声明。
- `scripts/`：纯 Python 标准库（或兼容环境）的可执行分析、组装与自检脚本。
- `resources/` / `references/` / `schema/` / `examples/`：受控词表、JSON Schema 与最小可复现示例。
