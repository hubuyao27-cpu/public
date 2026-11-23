# Publication Templates: Introduction & Related Work

## A. 中文论文模板（GB/T 7714-2015 / IEEE 数字引用）

### 1. 引言（Introduction）
#### 1.1 研究背景（立得住：领域脉络 -> 具体问题 -> 技术约束 -> 紧迫性）
- **切入规则**：首句直接锚定具体科学/工程问题与领域脉络（禁用“近年来受到广泛关注”“随着……快速发展”等空洞套话），并引用奠基性或关键性文献 `[R01]`。
- **技术约束与紧迫性**：给出具体物理/计算/实验约束（如通量限制、精度-复杂度权衡、数据稀缺条件）及其定量表现（均标注来源定位）。

#### 1.2 问题演进与研究空白（说得实：缺口推导 -> 本文切入点）
- 指出已有路线在**共同限制**、**未覆盖场景**或**结论冲突**上的客观缺口（每条空白须引用支撑文献编号 `[Rxx]`；若属于作者归纳判断且材料无直接引证，必须显式标注 `[作者判断]` 并附 `[待作者确认: GAP-XX]`）。
- 说明本文工作如何针对性地回应上述空白。

#### 1.3 主要贡献（对得上：方法/图表/结果一一对应 + 适用边界）
本文的主要贡献可核对如下：
1. **方法/架构贡献**：提出……，具体实现见本文方法节 `[方法来源定位]`，核心流程/结构对应图 `Fig. X` `[来源定位]`。
2. **实验/机理发现**：在 `[具体数据集/体系与工况条件]` 下取得 `[量化指标，附来源定位]`，验证了……（严格限定适用边界，不使用“首次彻底解决”“全面超越所有”等无界断言）。
3. **资源/工程价值**：……（如无可核验正文证据，则标注 `[待作者确认: GAP-XX]`）。

### 2. 相关工作（Related Work）
> **组织原则（分得清）**：严禁按时间顺序逐篇摘要拼接！按**问题设定 / 技术路线 / 数据与评价方式**分为 2–4 个逻辑分组，每组内显式比较各工作的**核心结论、适用条件与局限性**，并附多维对照表（`related_work_matrix.md`）。

#### 2.1 技术路线 A：<路线名称与分组依据>
- 总结该路线的共同机理/假设前提。
- 在同一比较维度（问题设定、方法特点、数据/工况、关键结论、适用条件与局限）上对比代表性工作 `[R01, R02]`（禁止 `[1-5]` 整堆无锚点堆砌）。
- 小结该路线的结构性取舍（trade-off）。

#### 2.2 技术路线 B：<路线名称与分组依据>
- 同上展开，并显式对比路线 A 与路线 B 的边界差异。

### 参考文献（References）
按目标格式（默认 GB/T 7714-2015）统一列出所有正文中引用的文献；每条须包含作者、年份、标题、发表来源及 DOI/编号，缺失字段以 `[待作者确认: GAP-XX]` 显式标出。

---

## B. English Manuscript Template (IEEE / APA / Nature / BibTeX)

### 1. Introduction
- **Research Background & Technical Bottleneck**: Open directly with the domain lineage, concrete unsolved problem, and measurable technical constraints supported by foundational and recent literature (`[1]`, `(Author, Year)`, or `\cite{key}`). Avoid hollow openings (*"In recent years, ... has attracted widespread attention"*).
- **Research Gap Derivation**: Derive explicit gaps from (i) shared assumptions/limitations across cited routes, (ii) uncovered operating/data regimes, or (iii) conflicting findings between studies. Mark uncorroborated synthetic inferences explicitly as `[AUTHOR_JUDGMENT]` and add `[AUTHOR_CONFIRM: GAP-XX]`.
- **Contributions (Evidence-Bound & Scope-Qualified)**:
  1. **Methodology**: ... (anchored to `[method locator]`, `Fig. X` / `Table Y`).
  2. **Empirical Findings**: ... achieving `[metric + locator]` under `[exact dataset/regime conditions]`.
  3. **Verification / Applicability Scope**: ...

### 2. Related Work
Group prior literature into **2–4 explicit methodological or problem-setting routes** (never chronological paper-by-paper summaries). For each route, compare:
- **Problem Setting & Assumptions**
- **Core Conclusions & Quantitative Trade-offs**
- **Applicability Conditions & Shared Limitations**
Pair the narrative with a multi-dimensional comparison matrix (`related_work_matrix.md`).
