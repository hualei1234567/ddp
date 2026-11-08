# ADR-0009 设备域契约冻结与不可变决策

- 状态：**已批准（APPROVED）**，由用户于 2026-11-08 给予长期授权
- 日期：2026-11-08（批准日期：2026-11-08；最近修订：2026-11-08，DDP-214）
- 决策者：System Architect；批准人：用户
- 任务：DDP-207（P0）；DDP-214（P1，批准记录、契约快照与差距排期）
- 关联：ADR-0002、ADR-0003、ADR-0004；`docs/architecture/阶段1-设备域契约冻结-v1.md`；`docs/architecture/阶段1-设备域冻结字段实现差距与排期.md`；`docs/data-standards/Canonical草案.md`
- 契约版本：`ddp.device/1.0.0`；参数 Schema：`ddp.device.calibration-parameters/1.0.0`
- 契约实现快照：`packages/contracts/openapi.yaml` `0.2.0`（仅覆盖当前已实现端点，**不是**冻结目标的完整实现）

## 0. 批准记录

| 项 | 内容 |
|---|---|
| 批准范围 | 本 ADR 全部决策，以及 `docs/architecture/阶段1-设备域契约冻结-v1.md` 的字段、状态机、错误码与参数 Schema |
| 批准方式 | 用户长期授权；DDP-214 任务书明确“用户已给长期授权” |
| 批准日期 | 2026-11-08 |
| 不包含的授权 | 数据库迁移执行、生产部署、发布、删除数据、绕过分阶段门禁 |
| 生效方式 | 契约即日起作为设备域唯一设计基线；实现按差距清单分批收敛，破坏性变更仍需新建 ADR |
| 跟踪文档 | `docs/architecture/阶段1-设备域冻结字段实现差距与排期.md` |

> 批准表示“设计与契约已冻结、后续实现必须遵守”，不等于“当前代码已经符合契约”。当前实现与冻结目标的逐项差距、责任与建议排期见跟踪文档。

## 1. 背景

设备域四路审查（架构、QA、安全、UX）结论一致：当前实现最多为 Code Complete，不能以 QA Accepted 进入阶段 1。主要阻断：

- CalibrationProfile 缺 `target_id`，版本唯一键未定义。
- CalibrationRecord 的 `version` 同时承担业务版本与乐观锁；提交后仍可被 `save()` 重写溯源字段。
- 缺 `profile_version`/`schema_version`；`parameters` 未按类型校验。
- `target_id` 是裸 UUID，可跨项目引用他项目机器人/设备/传感器。
- 状态迁移缺原因、故障码与审计；`add`/`save` 可绕过状态机直写状态。
- Sensor 缺 `frame_convention`，`time_base`/`sync_status` 归属不清；时间未统一 UTC。
- 前后端 `channels`、`uncertainty`、`RGB_D` 契约漂移。

若不在阶段 1 入口冻结，后续采集、Episode、数据集与训练血缘将无法复现，且跨项目引用会形成 IDOR 与数据污染风险。

## 2. 决策

### 2.1 冻结设备域契约 v1

以 `docs/architecture/阶段1-设备域契约冻结-v1.md` 为唯一设备域契约冻结基线，覆盖 Robot、Device、Sensor、CalibrationProfile、CalibrationRecord 的字段、状态机、错误码、审计事件与参数 Schema。批准后：

- API DTO、Domain 类型、DB 列名、事件字段、前端类型必须使用同一命名。
- `packages/contracts/**` 只能由 System Architect/契约 Owner 在批准后更新；实施不得自行改契约。
- 破坏性变更（删除/改名/改类型/改语义/必填化）必须新建 ADR。

### 2.2 版本三元分离

| 实体 | 业务版本 | 乐观锁 | 规则 |
|---|---|---|---|
| CalibrationProfile | `profile_version` | 无 | 行不可变；新版本新行，`profile_version+1` |
| CalibrationRecord | `calibration_version` | `lock_version` | 修订新行；同一行 `lock_version` 递增 |
| Robot/Device/Sensor | 无 | `version` | `version` 仅乐观锁 |

- `CalibrationRecord.calibration_version` 在同一 `(project_id, scope, target_type, target_id, calibration_type)` 谱系内唯一且连续。
- Canonical `calibration_snapshot[].version` 必须取 `calibration_version`，禁止取 `lock_version`。
- `REJECTED→DRAFT` 不再原地迁移；`revise` 创建新记录，`supersedes_id` 指向旧记录，旧记录不可变。

### 2.3 CalibrationProfile.target_id 与唯一键

- `target_id` 允许为空：空表示该 `target_type` 的类型级模板；非空表示目标级配置。
- `SYSTEM` 的 `target_id` 必须等于 `project_id`。
- 非空 `target_id` 必须存在于 `calibration_target_refs`。
- 版本唯一键：

```sql
UNIQUE (project_id, scope, target_type,
        COALESCE(target_id, '00000000-0000-0000-0000-000000000000'::uuid),
        profile_version)
```

- `supersedes_id` 唯一，禁止一个 Profile 版本被两个新版本分叉。

### 2.4 target_id 完整性：选择 DB 兜底

- 新增 `calibration_target_refs(project_id, target_type, target_id, lifecycle_status, is_retired, updated_at)` 多态目标投影表。
- Robot/Device/Sensor 创建时由 PostgreSQL 触发器写入投影；`SYSTEM` 行在项目创建时写入，`target_id=project_id`。
- `CalibrationProfile.target_id` 与 `CalibrationRecord.target_id` 使用复合外键指向该表。
- 应用层仍做前置解析与友好错误码，但不作为完整性保证；直接 SQL 与其他服务同样受 DB 约束。
- 目标退役后保留历史引用；新建标定拒绝；删除只允许软删除/退役，`ON DELETE RESTRICT`。

### 2.5 提交后不可变

CalibrationRecord 从 `SUBMITTED` 起冻结：

`profile_id/profile_version/schema_version/scope/target_type/target_id/calibration_type/method/parameters/parent_frame/child_frame/valid_from/valid_to/evidence_asset_ids/checksum/uncertainty/performed_by/supersedes_id/calibration_version/created_at/created_by`。

`VALIDATED` 起额外冻结 `validated_by/validated_at`。`save()` 不得写 `status`；状态只能经具名动作，带 `from_status` 条件更新。

### 2.6 状态必填与审计

- 证据、`validated_by/validated_at`、`uncertainty`、`valid_to` 按冻结文档 §6.2 状态矩阵执行。
- 所有迁移必须写追加式 `audit_logs` 与事务性 outbox；审计失败则迁移回滚。
- 生命周期实体保存最近 `status_reason/fault_code/fault_detail/fault_occurred_at/status_changed_at/by` 投影。
- 错误码按冻结文档 §8.6 映射；新增 `calibration_target_invalid`、`calibration_profile_invalid`、`calibration_checksum_mismatch`、`calibration_active_conflict`。

### 2.7 Sensor 字段归属

- Sensor 拥有 `frame_id`、`frame_convention`、`sync_mode`、`sync_group`。
- `time_base`、`sync_status` 归 Episode/Stream/QualityResult，不写入 Sensor；Sensor 详情可返回只读同步投影。
- `coordinate_frame` 删除，统一 `frame_id`；`sensor_type` 统一 `RGB_D`。

### 2.8 add 初始状态与直写禁令

- Robot/Device/Sensor 的 `add` 只允许 `PROVISIONED`。
- CalibrationRecord 的 `add` 只允许 `DRAFT`；首版 `calibration_version=1`，修订 +1。
- 领域实体不暴露可写 `status` 构造参数；Repository `add()` 拒绝非初始状态；`save()` 不包含状态列。
- DB 兜底：枚举 CHECK、INSERT 触发器拒绝非初始状态、UPDATE 触发器校验白名单迁移并写审计。

## 3. 后果

### 3.1 正面

- 设备域契约可支撑 Episode/Canonical 的标定快照与训练血缘复现。
- 跨项目 target 引用在 DB 层被阻断，应用层遗漏不会造成越权。
- 业务版本与并发锁分离，前端与服务端不再争用同一 `version` 字段。
- 状态迁移可审计、可追责、可重放；非法直写被测试与 DB 双重拦截。

### 3.2 代价与约束

- 需要新增迁移（建议 `0004_stage1_device_contract_freeze`），包含列改名、新增列、投影表、触发器、索引与回填。
- `CalibrationRecord.version → lock_version`、`CalibrationProfile.version → profile_version`、`coordinate_frame → frame_id` 是破坏性 API 变更，FE/BE 必须同步。
- 投影表与触发器增加写路径复杂度；必须在 PostgreSQL 上验证，SQLite 结果不作为验收证据。
- Profile 行不可变意味着纠正配置必须新建版本，历史版本不得删除。

### 3.3 迁移与回滚

- 采用 expand/contract：先加新列与投影表 → 回填 → 切换读写 → 观察 → 删除旧列。
- 回填必须按 `project_id` 分批、可重入、有进度与失败记录；`NOT NULL` 在回填后添加。
- 回滚点：上一迁移 revision（`0003`）与上一契约版本；回滚不得覆盖已写入的标定历史。
- 破坏性迁移与生产执行必须经用户单独批准；本 ADR 不授权执行迁移。

## 4. 替代方案

| 方案 | 结论 | 原因 |
|---|---|---|
| 应用层校验 target_id，不加 DB 约束 | 否决 | 直接 SQL/后台脚本可绕过；安全审查 P1-1 未闭环 |
| 每条 target 类型各加一列外键（robot_id/device_id/sensor_id） | 否决 | 多态列膨胀、CHECK 复杂、扩展新目标类型需改表 |
| 保留 `version` 同时承担业务版本与锁 | 否决 | 无法复现快照；并发与修订语义冲突 |
| REJECTED 原地 reopen | 否决 | 覆盖历史参数/证据，Canonical 快照不可复现 |
| `time_base/sync_status` 写入 Sensor | 否决 | 传感器主数据无法表达每次采集/流的时间基准与同步质量 |
| UUIDv4 或客户端生成 ID | 否决 | 无时间有序性；客户端可伪造引用 |

## 5. 批准与生效

本 ADR 已于 2026-11-08 经用户长期授权批准，状态为 **已批准（APPROVED）**。生效范围：

1. 本 ADR 与 `docs/architecture/阶段1-设备域契约冻结-v1.md` 的决策成为设备域唯一设计基线。
2. DB 兜底方案（`calibration_target_refs` + 触发器）纳入阶段 1 实施范围。
3. 破坏性字段更名（`lock_version`/`profile_version`/`frame_id`）与错误码新增已获设计批准。
4. **迁移执行与生产执行仍需另行审批**，本 ADR 不授权执行迁移、部署或数据处理。

批准后的契约维护规则：

- `packages/contracts/openapi.yaml` 当前为 `0.2.0` 实现快照，只允许 Architect 更新；待差距清单关闭后升级为 `1.0.0`。
- 实施任务必须引用冻结文档与差距清单 ID（`G-xx`），不得自行改字段语义。
- 契约兼容性检查已加入 CI；破坏性 diff 必须新建 ADR 并获得用户批准。

## 6. 验收与证据

- 契约文档自检：D1–D9、F2/F3/F5/F6、状态矩阵、参数 Schema、target_id 方案、差异表均已覆盖。
- DDP-214 已补齐：OpenAPI 设备域端点与错误码、Redocly lint、oasdiff 兼容性检查、ADR 批准记录、实现差距与排期。
- 本会话未执行 `pytest/ruff/tsc/Alembic/Redocly/oasdiff/Markdown lint`：当前会话无 Bash 工具，全部标记 `NOT RUN`；CI 加入后以流水线结果为准。
- 实施验收必须附：PostgreSQL 跨项目负向测试、非法状态直写测试、冻结字段篡改测试、并发激活测试、审计 outbox 失败回滚测试、前后端契约测试。

## 7. 变更记录

| 日期 | 变更 | 任务 |
|---|---|---|
| 2026-11-08 | 首次提出，状态“已提议” | DDP-207 |
| 2026-11-08 | 用户长期授权，状态改为“已批准”；补充 OpenAPI 快照、差距清单与 CI 门禁 | DDP-214 |
