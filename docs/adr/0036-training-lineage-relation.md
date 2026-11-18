# ADR-0036 DatasetVersion → TrainingRun 血缘关系：`TRAINED_ON`

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-18
- 决策者：System Architect；批准人：用户（长期授权）
- 任务：DDP-V21-N5-005
- 关联：ADR-0012（阶段 4 血缘关系草案）、`docs/roadmap/v2.1/n0/v2.1-backlog.md`、
  `backend/app/domain/episodes/lineage.py`、`backend/app/application/datasets/lineage.py`、
  `backend/app/application/datasets/training_references.py`、迁移 0009 / 0059
- 影响范围：血缘关系枚举、端点白名单、`data_lineage_edges.relation` CHECK、
  OpenAPI `LineageRelation` 枚举、前端关系标签；不改节点类型，不新建表。

## 1. 背景

N5-002 已把 `Episode → Annotation`、`Episode → QualityRun` 两条边同事务写入血缘图，
但 `DatasetVersion → TrainingRun` 一直缺失：

- `LineageNodeType.TRAINING_RUN` 早已存在（迁移 0009 的节点类型白名单），但没有
  任何关系与之对应；
- `LineageRelation` 只有 10 个取值，且 `data_lineage_edges.relation` 由 CHECK 约束
  限制在枚举内，新增取值必须迁移；
- ADR-0012 §5.2 草案中的 `TRAINED_AS` 定义为
  `TRAINING_DELIVERY → TRAINING_RUN`，与本次要表达的"某个数据集版本被用于哪次训练"
  **方向与语义都不同**。直接照搬会把两个不同事实压在同一个关系名下。

本 ADR 只裁决关系命名、方向、节点身份与写入时机；不引入训练集群、不改变
`external_training_references` 的权威地位（外部训练平台仍是 run 与状态的权威系统）。

## 2. 决策

### 2.1 关系名：`TRAINED_ON`

新增 `LineageRelation.TRAINED_ON = "TRAINED_ON"`。

理由：

- 语义是"这次训练**基于**哪个数据集版本"，读作
  `DatasetVersion --TRAINED_ON--> TrainingRun`，方向与既有"上游产出/供给下游"一致；
- `TRAINED_AS` 已被 ADR-0012 预留给 `TRAINING_DELIVERY → TRAINING_RUN`
  （"这次交付最终成为某次训练"）。两者端点不同、含义不同，复用会产生歧义；
- 不复用 `INCLUDED_IN`：该关系是 `Episode → DatasetVersion` 的成员关系，表示
  "Episode 被纳入版本"；训练运行不是数据集版本的成员，方向也相反。把训练塞进
  `INCLUDED_IN` 会让"版本包含哪些样本"和"版本被谁消费"两个查询互相污染。

### 2.2 方向：`DatasetVersion`（上游）→ `TRAINING_RUN`（下游）

- `from_node_type = DATASET_VERSION`，`from_node_id = dataset_version_id`；
- `to_node_type = TRAINING_RUN`，`to_node_id = external_ref_id`。

这样正向遍历版本即可回答"这个版本训练出了哪些 run"，反向从 run 可回溯到版本，
与 `INCLUDED_IN`（Episode → DatasetVersion）串成
`Episode → DatasetVersion → TrainingRun` 的完整链路。

ADR-0012 的 `TRAINED_AS` 仍保留为未实现草案，不得与本关系混用；实现
`TRAINING_DELIVERY → TRAINING_RUN` 时再单独评估。

### 2.3 节点身份：`external_ref_id`

`TRAINING_RUN` 节点的 `node_id` 取 `external_training_references.id`（`external_ref_id`）：

- 平台内没有独立的 `training_runs` 表，外部训练引用就是"一次训练"在本平台的权威记录；
- `external_ref_id` 是 UUID、项目内唯一、在引用创建时即确定，满足血缘节点"先登记、
  后写边"的复合外键要求；
- 外部平台的 `external_run_id` 是字符串且依赖 Adapter 命名空间，不适合做节点主键；
  它写入边的 `metadata`，用于跨系统对账。

### 2.4 写入时机与事务

在 `ExternalTrainingReferenceUseCases.create` **成功登记引用之后、同一事务提交之前**
写入节点与边：

1. `ensure_node(TRAINING_RUN, external_ref_id)`；
2. `ensure_node(DATASET_VERSION, delivery.dataset_version_id)`；
3. `ensure_edge(DATASET_VERSION, version_id, TRAINING_RUN, external_ref_id, TRAINED_ON)`。

- **同一事务**：写边失败则整个会话回滚，引用、审计与 outbox 均不落库，不允许出现
  "引用存在但没有血缘"的静默缺口；
- **幂等**：`ensure_edge` 幂等；同一幂等键重放直接返回既有引用，不重复建边；
- **项目隔离**：`dataset_version_id` 只来自按当前 `ProjectScope` 读取的
  `TrainingDelivery`，不接受客户端传入；跨项目访问在读取交付时即 404；
- 存量引用不自动补边：补边需要额外判定历史 run 的真实性，不在本次范围；如需回填，
  必须走单独的、可审计的补偿任务。

### 2.5 数据库、契约与前端

- 迁移 0059 重建 `data_lineage_edges.relation` 的 CHECK，只增加 `TRAINED_ON`；
  历史行不变，升级可回滚（回滚前若已存在 `TRAINED_ON` 边则显式拒绝，避免静默删血缘）；
- `packages/contracts` 的 `LineageRelation` 枚举新增该值；
- 前端 `LINEAGE_RELATION_VALUES` 与 `LINEAGE_RELATION_LABELS` 同步，标签为"用于训练"。

## 3. 后果

正面：

- 版本到训练运行的血缘闭环可查询、可审计，回答"这个版本被谁训练过"；
- 关系名与 ADR-0012 的预留语义不冲突，未来实现交付到 run 的边时仍可用 `TRAINED_AS`；
- 写边与引用创建同事务，避免半成功状态。

代价与边界：

- 每次创建外部引用多写 2 个节点 + 1 条边，属于同一事务内的少量写入；
- 不解决训练 run 的完整字段（config、code_version、model_version）与轮询问题，那属于
  DDP-V21-N7-002；
- 无通用图查询 API 的现状不变，仍通过既有节点血缘端点读取。
