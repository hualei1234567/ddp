# ADR-0037 CaptureSession 实体：与 CollectionTask 的边界

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-19
- 决策者：System Architect；批准人：用户（长期授权）
- 任务：DDP-V21-N9-001
- 关联：`docs/roadmap/v2.1/n0/v2.1-backlog.md`、
  `docs/roadmap/v2.1/n0/existing-reusability-matrix.md`、
  `backend/app/domain/collection/models.py`、`backend/app/domain/collection/state_machines.py`、
  `backend/app/application/collection/capture_sessions.py`、迁移 0066
- 影响范围：采集域新增实体/表/API/契约；不改 `collection_tasks` /
  `collection_batches` 既有字段与状态机，不改前端。

## 1. 背景

采集域已有三层能力：

- `CollectionTask`：任务计划与审核流（DRAFT → … → COMPLETED），带方案/SOP/
  场景/动作库引用、排期、目标数量与验收动作；
- `CollectionBatch`：任务下的批次拆分（`sequence_no`、预计/提交/验收数量）；
- 素材四实体（方案 / SOP / 场景 / 动作库）与场地 `locations`。

缺的是"**一次真实执行**"：谁在什么时间、用哪台机器人在哪个场地上真正开录、
暂停、恢复、结束或中止。现有任务只有 `actual_start` / `actual_end` 两个字段，
只能表达"最后一次执行的起止"。当同一任务换设备、换操作员、暂停后重开或失败
重来涉及多次执行时，历史执行事实无处记录；而任务状态机本身是审核流，混入
执行态会让"提交/验收"与"开录/暂停"互相污染。

本 ADR 裁决：是否复用 `collection_tasks` 扩展，还是新建独立实体。结论是
**新建 `capture_sessions`**，并明确它与任务/批次/素材/场地/设备的关系。

## 2. 决策

### 2.1 新建实体，不扩展现有表

不能只扩 `collection_tasks` 的理由（按复用优先顺序逐条排除）：

1. **复用现有字段不成立**：任务已有 `actual_start` / `actual_end`，但一对时间
   只能保留一条执行；多次执行会互相覆盖，丢失审计价值。
2. **扩展任务表不成立**：给任务加"当前会话状态"列，等于把执行态塞进审核流
   状态机。`CollectionTask.apply_action` 与数据库触发器都按审核迁移校验，
   新增列需要第二套状态机与第二条触发器，二者会争用同一行的
   `status` / `updated_at`，反而制造耦合。
3. **复用批次不成立**：批次是"任务内的数量拆分"，一个批次仍可能多次开录；
   且批次没有操作员、机器人、场地与执行时间语义，扩到批次等于把批次改造成
   会话，破坏其"数量分段"的原始职责。
4. **组合现有能力不成立**：没有其他聚合能同时承载"任务归属 + 执行时间 +
   操作员 + 机器人 + 场地 + 中止原因"。

因此新建独立表 `capture_sessions`。数据库外键保证同项目引用，应用层做可读
的 404/422 前置校验。

### 2.2 职责边界

| 对象 | 回答的问题 | 不负责 |
|---|---|---|
| `CollectionPlan` | 这份方案计划采多少、质量阈值是什么 | 不记录执行 |
| `CollectionTask` | 要采什么、按哪版素材、谁来审、验收是否达标 | 不记录多次执行的过程 |
| `CollectionBatch` | 任务被拆成几段、每段预计多少 | 不记录开录/暂停/结束 |
| **`CaptureSession`** | **这次执行何时开始、谁操作、用哪台机器人/场地、何时暂停/恢复/结束或中止** | **不推进任务状态、不改素材、不做审核/验收** |
| `RawAsset` / `Episode` | 实际落盘的数据与样本 | 不记录执行过程 |

关系：

- `CaptureSession.task_id` / `batch_id` **可空**：允许"先开录、后归属"；
  给了批次而没给任务时，应用层自动继承批次的任务；两者都给且不一致返回 422。
- `operator_id` 必须是项目成员；`robot_id` 必须是同项目且状态 `ACTIVE`；
  `location_id` 必须同项目存在。跨项目引用在读取关联对象时即 404。
- 会话不复制方案/SOP 版本：这些由任务在创建时冻结，会话通过任务间接关联，
  避免第二份版本快照与任务脱节。

### 2.3 状态机

```text
SCHEDULED --start--> RUNNING --pause--> PAUSED --resume--> RUNNING
    |                   |                  |
    |                   +--complete-------> COMPLETED   (终态)
    +--abort------------+--abort----------> ABORTED     (终态)
```

规则：

- 只允许表中列出的具名动作；未知状态/动作一律拒绝；
- `COMPLETED` / `ABORTED` 没有出边；
- `pause` / `resume` / `abort` 必须提供 `reason`（与任务/批次同一口径）；
- `SCHEDULED` 不得带 `started_at` / `ended_at`；`RUNNING` / `PAUSED` 必须有
  `started_at` 且不得有 `ended_at`；`COMPLETED` 必须有两者；`ABORTED` 必须有
  `ended_at`，允许 `started_at` 为空（未开录即放弃）；
- 状态机唯一权威实现：`backend/app/domain/collection/state_machines.py`；
  数据库触发器只做白名单兜底，不另写业务判断。

### 2.4 API 与项目隔离

- `GET/POST /api/v1/projects/{project_id}/capture-sessions`
- `GET /api/v1/projects/{project_id}/capture-sessions/{session_id}`
- `POST /api/v1/projects/{project_id}/capture-sessions/{session_id}/actions/{action}`

所有仓储方法接收服务端构造的 `ProjectScope`，按 ID 查询带 `project_id`
过滤；跨项目访问返回 404，不泄漏资源是否存在。写路径走
`If-Match` 乐观锁与 `Idempotency-Key`；响应 `allowed_actions` 由服务端状态机
计算。创建/动作均同事务写审计与 outbox。

### 2.5 事件与外部控制

会话事件全部为**留痕类**，`dispatch=False`：

| 事件 | 产生点 |
|---|---|
| `collection.capture_session.created` | 创建用例 |
| `collection.capture_session.started` | `start` 动作 |
| `collection.capture_session.paused` | `pause` 动作 |
| `collection.capture_session.resumed` | `resume` 动作 |
| `collection.capture_session.completed` | `complete` 动作 |
| `collection.capture_session.aborted` | `abort` 动作 |

**不向机器人下发任何运动、复位或其他控制命令**。将来若要接入真实控制器，
必须单独立项，补消费者、授权、时限、失败记录与安全评审，不能把留痕事件
擅自改成投递类。

### 2.6 数据库

迁移 0066 新建 `capture_sessions`：

- `(project_id, id)` 唯一键，供其他表复合外键引用；
- `task_id` / `batch_id` / `robot_id` / `location_id` 均使用
  `(project_id, ...)` 复合外键，禁止跨项目；
- `status` 用 `varchar + CHECK`，并由触发器校验初始状态、迁移白名单与禁删；
- 时间范围 `ended_at >= started_at` 由 CHECK 兜底；更严格的"状态—时间一致"
  由领域实体强制（SQLite 加复杂 CHECK 需重建表，代价大于收益）；
- 不存大对象：数据实体在 `raw_assets` / `episodes`，会话只存元数据与状态。

## 3. 不做什么

- 不修改 `collection_tasks` / `collection_batches` 的字段与状态机；
- 不在本任务实现"会话→Episode/RawAsset"的归属写入，也不自动推进任务状态；
- 不实现机器人控制、设备连接、实时遥测或运动指令；
- 不提供 PATCH / DELETE：会话是执行记录，字段在创建时确定，中止用
  `abort` 表达；删除与改历史会破坏执行事实；
- 不做跨任务/跨项目的会话合并或调度；
- 不改前端；前端接入属于后续任务。

## 4. 后果

正面：

- 多次执行可保留、可审计，任务仍只承担计划与验收；
- 与任务/批次状态机解耦，二者可独立演进；
- 项目隔离由复合外键 + 仓储过滤 + API 依赖三层保证。

代价与边界：

- 多一张表与一组触发器/索引；
- 会话与 Episode/RawAsset 的归属链路尚未建立，当前只能回答"执行发生过"，
  不能回答"这次执行产出了哪些 Episode"——后者依赖真实设备接入，属
  `DDP-V21-N9-004`（`BLOCKED`）；
- 未接入机器人控制器，`start` / `pause` / `resume` / `complete` / `abort`
  只是平台内的执行记录，不代表已向硬件发出指令。
