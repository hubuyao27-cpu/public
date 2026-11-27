# Publication Templates & Craft Patterns: Introduction & Related Work

## 0. Base-Agent Anti-Patterns vs. Gold Synthesis Patterns (Four Core Criteria)

| Criterion | Base-Agent Anti-Pattern (Avoid) | Gold Pattern (Enforce) |
|---|---|---|
| **立得住 (CARS Move 1)** | *"近年来，X 领域受到了广泛关注，具有重要的科学意义 [1-4]。"* | **Lineage -> Bottleneck -> Quantitative Constraint**: *"Although single-atom Ni-N4 catalysts reach 96.5% CO Faradaic efficiency in H-cells [R01], liquid-phase CO2 diffusion caps current density below 50 mA/cm2, blocking industrial MEA scaling."* |
| **分得清 (2D Route Synthesis)** | *"In 2020 Zhang proposed A [1]. In 2022 Li proposed B [2]. In 2024 Wang proposed C [3]."* | **Group by Method/Problem Axis + Trade-off + Bridge**: Group into 2–4 routes (`Route A` vs `Route B`), compare conclusions, **applicability conditions**, and **limitations** along a shared axis, and end with a bridge paragraph connecting prior limits to our approach. |
| **说得实 (CARS Move 2)** | *"However, existing methods still suffer from many drawbacks and fail to perform well."* | **Derived Gap + Attribution**: *"Both porous-carbon [R02] and zero-gap MEA [R04] routes trade off high current density (>300 mA/cm2) against salt-precipitation pore blockage after 12–50 h; [作者判断] neither resolves three-phase transport and anti-flooding jointly."* |
| **对得上 (CARS Move 3)** | *"We propose the first method that completely solves all stability issues with 99% accuracy."* | **4-Part Scoped Contribution**: **(1) Method + locator** (`Section 2.3`, `Fig. 1`) -> **(2) Metric + locator** (`465 mA/cm2`, `97.4% FE(CO)` in `Table 1`) -> **(3) Operating Scope** (`zero-gap flow cell at -0.75 V vs. RHE over 120 h`) -> **(4) Boundary** (what remains untested). |

---

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
3. **验证边界与适用范围**：……（如无可核验正文证据，则标注 `[待作者确认: GAP-XX]`）。

### 2. 相关工作（Related Work）
> **组织原则（分得清）**：严禁按时间顺序逐篇摘要拼接！按**问题设定 / 技术路线 / 数据与评价方式**分为 2–4 个逻辑分组，每组内显式比较各工作的**核心结论、适用条件与局限性**，并附多维对照表（`related_work_matrix.md`），最后以**桥接段（Bridge Paragraph）**收束到本文定位。

#### 2.1 技术路线 A：<路线名称与分组依据>
- 总结该路线的共同机理/假设前提。
- 在同一比较维度（问题设定、方法特点、数据/工况、关键结论、适用条件与局限）上对比代表性工作 `[R01, R02]`（禁止 `[1-5]` 整堆无锚点堆砌）。
- 小结该路线的结构性取舍（trade-off）。

#### 2.2 技术路线 B：<路线名称与分组依据>
- 同上展开，并显式对比路线 A 与路线 B 的边界差异。

#### 2.3 路线对比小结与本文定位（Bridge to This Work）
- 归纳各路线在核心评价维度上的共性缺口，明确本文方法在哪个约束维度上作出不同设计选择。

### 参考文献（References）
按目标格式（默认 GB/T 7714-2015）统一列出所有正文中引用的文献；每条须包含作者、年份、标题、发表来源及 DOI/编号，缺失字段以 `[待作者确认: GAP-XX]` 显式标出。

---

## B. English Manuscript Template (IEEE / APA / Nature / BibTeX)

### 1. Introduction
- **Research Background & Technical Bottleneck (Move 1)**: Open directly with domain lineage, concrete unsolved problem, and measurable constraints supported by foundational and recent literature (`[R01]` -> rendered to `[1]`, `(Author, Year)`, or `\cite{key}`). Never open with *"In recent years, ... attracted widespread attention"*.
- **Research Gap Derivation (Move 2)**: Derive explicit gaps from (i) shared limitations across cited routes, (ii) uncovered operating/data regimes, or (iii) conflicting findings between studies. Mark uncorroborated synthetic inferences explicitly as `[AUTHOR_JUDGMENT]` and add `[AUTHOR_CONFIRM: GAP-XX]`.
- **Contributions (Move 3 — Evidence-Bound & Scope-Qualified)**:
  1. **Methodology**: ... (anchored to `[method locator]`, `Fig. X` / `Table Y`).
  2. **Empirical Findings**: ... achieving `[metric + locator]` under `[exact dataset/regime conditions]`.
  3. **Applicability Boundary**: ...

### 2. Related Work
Group prior literature into **2–4 explicit methodological or problem-setting routes** (never chronological paper-by-paper summaries). For each route, compare:
- **Problem Setting & Assumptions**
- **Core Conclusions & Quantitative Trade-offs**
- **Applicability Conditions & Shared Limitations**
End Related Work with a **Bridge Paragraph** positioning our work against the exact limitations identified above, paired with `related_work_matrix.md`.
