# ADR-0044 质检结论驱动路由：结论只读、动作复用、路由幂等

- 状态：**ACCEPTED**
- 日期：2026-11-22
- 决策者：System Architect + 后端；批准人：用户（长期授权）
- 关联：ADR-0042（生命周期与结论正交）、ADR-0043（异步边界）、V31-030、V31-031、V31-032
- 约束：不新增状态机、不新增数据库列、不新增契约错误码、不改既有同步路径语义

## 1. 背景

V31-030 把 `quality_runs` 的 `lifecycle` 与结论拆成正交两列，V31-031 补上了
异步执行链路（`enqueue` / `start` / `conclude` + Worker）。现在一条质量运行
可以从 `QUEUED` 走到 `SUCCEEDED` 并携带结论。

但**结论产出之后没有任何消费方**：

| 现状 | 位置 | 问题 |
|---|---|---|
| 标注提交质检 | `application/annotation/quality_checks.py` | 结论写进 `annotation_quality_runs`，与提交状态**无关联** |
| 统一质检 | `application/quality/use_cases.py` | 结论写进 `quality_runs`，只被 HTTP 查询读取 |
| 双标一致性 | `application/annotation/consistency.py` | 有 `auto_approve` 自动通过，但那是**一致性**结论，不是质检结论 |

结果是：质检算出 `FAILED` 之后，提交仍然停在 `SUBMITTED`，等人手工去
`require-rework`；质检算出 `PASSED` 之后，提交也不会自动转审核。V3.1 里程碑
M4 的完成判据「质检运行状态机 + 失败重投 + **结论驱动返工**」只完成了前两项。

## 2. 候选方案

| 方案 | 做法 | 问题 |
|---|---|---|
| A. 在质检用例里直接改提交状态 | `QualityRunUseCases.conclude` 末尾调 `submissions.apply_action` | 质检域反向依赖标注域；两个用例互相持有；且同步路径（`POST /quality/runs`）会意外改变提交状态 |
| B. 新增独立的路由用例，只消费 `SUCCEEDED` 结论（本方案） | 新增 `QualityRoutingUseCases`，读结论 → 决定动作 → 调既有 `reviews` / `submissions` 用例 | 需要明确"什么时候路由"与"重复路由怎么办" |
| C. 由 Worker 在 `conclude` 之后直接路由 | 把路由塞进 Worker | Worker 只该执行，不该持有业务决策；且同步路径仍然不会路由，两条路分叉 |

## 3. 决策

**选方案 B。**

### 3.1 路由是独立用例，不是质检的一部分

`QualityRoutingUseCases.route(scope, run_id)`：

1. 读 `quality_runs` 的**结论视图**（`lifecycle = SUCCEEDED`）；未完成的运行
   直接返回 `SKIPPED`——"没算完"绝不能触发任何状态推进。
2. 按 `subject_type` 分派：
   - `EPISODE`：结论 `FAILED` → 该 Episode 下所有 `SUBMITTED` 提交
     `require-rework`；`PASSED` → `system-review-start`。
   - `DATASOURCE_IMPORT`：**不路由**。导入质检的结论用于决定是否物化
     Episode，与标注提交无关；强行路由会改错对象。
3. 每次动作都走既有用例（`AnnotationSubmissionUseCases.apply_action`），
   不新写状态机、不直接改表。

### 3.2 结论 → 动作的映射只有一处

| 质检结论 | 目标对象 | 动作 | 理由 |
|---|---|---|---|
| `FAILED` | Episode 下的 `SUBMITTED` 提交 | `require-rework` | 质检不通过 = 需要返工，与人工驳回同一条边 |
| `PASSED` | Episode 下的 `SUBMITTED` 提交 | `system-review-start` | 质检通过 = 可以进入人工复核 |
| `INCOMPLETE` | 无 | 不路由 | "没判出结论"不是通过也不是失败，推进任何状态都是伪造 |
| 执行失败（`lifecycle = FAILED`） | 无 | 不路由 | 执行失败没有可信结论 |

映射表放在领域层（`domain/quality/routing.py`），与 `QUALITY_RUN_TRANSITIONS`
同级：应用层只做"读结论 → 查映射 → 调用例"，不重新列举"哪些结论该路由"。

### 3.3 路由必须幂等

同一结论可能被重复消费（Worker 重投、人工重放、事件重发）。路由的幂等靠
**目标对象的状态机**，不靠额外的路由记录表：

- 提交已经是 `REWORK_REQUIRED` 时，再 `require-rework` 会被状态机拒绝
  （`REWORK_REQUIRED` 的出边只有 `system-supersede`）；
- 提交已经是 `IN_REVIEW` / `APPROVED` 时，再 `system-review-start` 同样被拒。

因此路由把"目标状态机拒绝"视为**已完成**（`ALREADY_APPLIED`），而不是失败。
这与 `reviews.py` 里双人复核 claim 的处理完全一致：不推进不是错误，先到者
已经完成了这次迁移。

### 3.4 路由失败不否决质检结论

质检结论是**已经发生的事实**，路由是它的下游动作。路由失败（例如目标提交
被并发推进、状态机拒绝）只记录告警与返回 `PARTIAL`，**不回滚质检运行**，
也不把运行改成 `FAILED`——那会让"算出来的结论"消失。运维可据 `run_id`
重放路由。

## 4. 后果

- 新增 `application/quality/routing.py`（用例）与 `domain/quality/routing.py`
  （映射表），不新增表、不新增列、不新增契约错误码。
- `QualityRunUseCases` 不感知路由；同步路径（`POST /quality/runs`）默认
  **不自动路由**——是否需要路由由调用方显式决定，避免"查一次质检结果就改了
  提交状态"的意外副作用。
- Worker 在 `conclude` 成功后调用路由；路由失败只告警，事件仍算已发布
  （结论已落库，重放走人工或后续补偿）。
- V31-033（批量重跑与差异报告）复用同一映射表，不重复定义"哪种结论对应
  哪种动作"。

## 5. 残余风险

| 风险 | 说明 |
|---|---|
| 路由与人工操作竞争 | 人工先 `require-rework` 后路由到达，状态机拒绝即视为已完成，不会双写 |
| 一个 Episode 多个提交 | 路由作用于该 Episode 下**全部** `SUBMITTED` 提交；若业务上只该路由最新一版，需要额外规则，本 ADR 不假设 |
| 路由无独立审计 | 动作本身由既有用例写审计与 outbox，路由层不重复记录；排查时按 `run_id` 关联 |
