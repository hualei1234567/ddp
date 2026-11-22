# ADR-0042 质量运行的生命周期与结论正交

- 状态：**ACCEPTED**
- 日期：2026-11-22
- 决策者：System Architect + 后端；批准人：用户（长期授权）
- 关联：ADR-0014（复用优先）、ADR-0038（Episode 可训练口径）、V31-030、V31-031
- 约束：不改写既有结论语义、不迁移历史数据、不新增 API

## 1. 背景

`quality_runs`（迁移 0058）只有一张枚举 `status`：`PASSED` / `FAILED` /
`INCOMPLETE`，且 `started_at` / `finished_at` 为 NOT NULL。这三个值全部是
**结论**——"已经算完了，结果是什么"。

V3.1 要求"提交后自动质检"：质检要变成异步执行（Outbox → Worker），于是运行
还需要表达"排队中 / 执行中 / 执行失败待重投 / 已取消"。把这两类事实塞进一张
枚举会带来三个具体问题：

1. **"没算完"会被读成"通过"**：读取方只能靠字符串白名单判断"这行算完与否"，
   任何遗漏都会让未完成的数据流入下游（标注路由、Dataset 门禁）。
2. **假时间**：排队中的运行没有真实开始/结束时间，NOT NULL 只能填假值——
   等于伪造执行证据。
3. **`FAILED` 语义重载**：规则判定失败需要转返工；执行崩溃需要重投。两者的
   处置完全不同，却是同一个字符串。

## 2. 候选方案

| 方案 | 做法 | 问题 |
|---|---|---|
| A. 往 `status` 里加 `QUEUED/RUNNING/...` | 单枚举覆盖两种事实 | 既有客户端与门禁按枚举白名单读取，扩展值会被当成未知；"没算完"与"通过"仍在同一列 |
| B. 生命周期与结论拆成两列（本方案） | `lifecycle` 独立列；`status` 只在 `SUCCEEDED` 时有值 | 需要放宽三列可空性并改 CHECK；结论视图必须显式过滤 |
| C. 新建 `quality_run_attempts` 子表 | 每次执行一行 | 对当前需求是过度建模：一次运行的重投次数有限，且没有独立的执行级产物需要归档 |

## 3. 决策

**选方案 B。**

1. **两个事实、两条列**：`lifecycle`
   （`QUEUED/RUNNING/SUCCEEDED/FAILED/RETRYING/CANCELLED`）回答"走到哪一步"，
   `status`（`PASSED/FAILED/INCOMPLETE`）回答"结论是什么"。**只有
   `SUCCEEDED` 携带结论**。
2. **状态机是唯一迁移入口**（`domain/quality/enums.py` 的
   `QUALITY_RUN_TRANSITIONS` + `QualityRun.mark_*`）：`QUEUED` 不能直接跳到
   结论；`FAILED` 只能重投；`SUCCEEDED` / `CANCELLED` 是终态。
   `schedule_retry` 受 `max_attempts` 约束。
3. **未完成的运行不带结论字段**：`status` / `score` / 计数 / `category_summary`
   / `findings` 必须为空。`domain` 与数据库 CHECK
   （`lifecycle_fields_consistent`）同时强制，绕过领域层的写入也写不出
   "没跑却通过"。
4. **执行错误与结论分离**：`error_code` / `error_summary` 只在 `FAILED` 必填，
   重投期间保留（"为什么又跑了一遍"），成功时由 `mark_succeeded` 清空。
5. **默认值兼容历史**：`lifecycle` 默认 `SUCCEEDED`，迁移把存量行回填为
   `SUCCEEDED`——0070 之前落库的每一行本来就是已算完的结论。
6. **结论视图只认 `SUCCEEDED`**：`SqlQualityRunRepository.get/list/
   latest_for_subjects` 全部带上 `lifecycle = 'SUCCEEDED'`。未完成的运行在
   V31-031 之前**不可见**（还没有面向生命周期的查询入口）——这是有意的：
   `QualityRunOut` 的契约要求 `status` / `started_at` 非空。

## 4. 后果

- 既有 HTTP 契约（`QualityRunOut`）与门禁语义**不变**：能读到的仍然是结论。
- 回滚是有条件的：`downgrade` 在存在 `lifecycle <> 'SUCCEEDED'` 的行时直接
  报错。把这些行回填成 `status` + 时间就是伪造质量证据，宁可拒绝回滚。
- V31-031 必须在 Worker 中补齐"写入生命周期"的仓储方法（`add` 只接受
  已产出结论的运行），并另行提供生命周期查询入口。
- `quality_runs` 不再是纯追加表：同一行会随生命周期推进被更新。**结论字段
  一旦写入不再改写**，重跑仍是新 run（沿用 0058 的口径）。

## 5. 残余风险

| 风险 | 说明 |
|---|---|
| 未完成运行不可观测 | V31-031 之前，排队中的运行只存在于数据库；无 API 可查（已知，短期） |
| `attempt` 语义 | 当前 `attempt` 表示"这是第几次执行（1 起）"，`max_attempts` 是上限；与处理链路的 `attempt/max_attempts` 口径一致，未引入新的命名 |
| 迁移重建表 | SQLite 走 `batch_alter_table`；PostgreSQL 走原生 ALTER，往返测试覆盖两库 |
