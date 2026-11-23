# Methods & Experiments Section Blueprint (Reusable Template)

> Adapt headings and terminology to the actual discipline evidenced in `/input/` (Computational/ML, Wet-Lab/Chemistry/Materials/Bio, or Simulation/Engineering) and the venue, audience, and language specified in `task.md`. Never fabricate domain facts from labels. Tag every unconfirmed gap inline as `[待作者确认: GAP-XX]` (Chinese) or `[AUTHOR_CONFIRM: GAP-XX]` (English).

## 1. Method Overview & Executable Procedure (方法流程与可执行步骤)
- State the overall experimental/computational pipeline boundary and assumptions.
- Provide an ordered, executable step table or numbered protocol:
  - **Step S1 (`Stage`)**: **Input** -> **Operation** (exact algorithm / reagent handling / solver action) -> **Output** -> **Decision / Stopping Criterion** *(Source: `file:Lxx`)*.
  - Do not compress multi-stage preprocessing, training, post-processing, or synthesis + calcination + washing into a single vague sentence.

## 2. Datasets & Splits / Materials & Sample Preparation (数据集与划分 / 材料与样品制备)
- **Identity & Version**: Exact dataset name + release version/snapshot/DOI (or reagent purity grade, supplier, lot/batch) *(Source: `file:Lxx`)*.
- **Scale & Filtering**: Raw sample count, inclusion/exclusion rules, and post-filtering counts.
- **Preprocessing / Preparation**: Exact transformations; explicitly note whether scaling/imputation/feature selection is fit on the training split only (leakage prevention).
- **Split / Grouping Protocol**: Exact train/val/test ratios (or control/treatment groups), stratification/grouping axis, random seed, and explicit role of each subset.

## 3. Experimental Setup & Stage-Separated Protocol (实验设置与阶段分离协议)
- **Training / Synthesis / Calibration Stage**: Exact protocol, loss/objective, stopping rule, checkpoint selection criteria.
- **Validation / Characterization Stage**: Exact protocol, hyperparameter/model selection boundary (never tuned on test split), instrument calibration.
- **Testing / Final Evaluation Stage**: Frozen model/sample protocol, inference/measurement settings, post-processing.
- **Hardware, Software & Runtime Environment**: Exact GPU/CPU or instrument models, OS, Python/framework/firmware versions, precision mode, random seeds, and repeat counts (`n` independent runs / replicates).

## 4. Parameter Provenance Table (参数配置与来源对照表)

| ID | Parameter (`key`) | Value | Unit | Target Object | Stage | Source Locator | Status |
|---|---|---|---|---|---|---|---|
| P01 | ... | ... | ... | ... | ... | `config.yaml:L12` | `verified` |

*(Every numeric parameter in Sections 1-3 and Section 5 must match this table verbatim or via documented unit equivalence.)*

## 5. Baselines, Controls & Comparative Evaluation (对照实验与评测协议)
- **Baseline / Control Objects**: Exact model/method/commercial reference names, versions, checkpoints, and implementation sources.
- **Budget & Condition Parity**: Explicit audit stating whether compute budget, epochs, HPO trials, data budget, or testing temperature/pressure are matched across methods; document any asymmetry transparently.
- **Evaluation Metrics & Statistical Reporting**: Exact metric formulas/averaging (`macro`/`micro`, exact units), aggregation across repeats (`mean +/- SD` / `95% CI`), and statistical significance tests (`paired t-test` / `Wilcoxon` / `ANOVA`, threshold).

## 6. Reproducibility Checklist & Concrete Limitations (可复现性清单与失效边界)
- **Reproducibility Checklist**: Code repo + commit hash, environment file/container, exact CLI commands/SOP, data/material access & license, random seeds, expected runtime/cost.
- **Failure-Mode Limitations**: List concrete failure scenarios tied to observable triggers and validity boundaries (`Failure Scenario` -> `Trigger Condition` -> `Affected Scope` -> `Evidence / Attribution`). Avoid generic boilerplate clichés.
