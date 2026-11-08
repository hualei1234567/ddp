# DDP 设备域架构审查

> 审查对象：main @ `922db85`
> 审查角色：System Architect（原生 Plan Subagent）
> 日期：2026-11-08
> 结论：**有条件通过**。可作为阶段 1 地基保留，但不能宣称“BE 完成”或冻结契约。

---

## 1. 已确认正确

- Domain/Application 未依赖 infrastructure/Web/ORM；依赖方向正确。
- 仓储 add/get/list/save/activate 均使用 `ProjectScope` 过滤；跨项目读取返回 `None`。
- 生命周期与标定状态迁移表与 DDP-101 §3.1/§3.2 一致。
- `0003` 在 SQLite 上 upgrade→downgrade→upgrade 通过；部分唯一索引
  `uq_calibration_records_active_target` 设计正确。

---

## 2. P1

### F1 状态机可被构造器/仓储绕过

- `domain/devices/models.py:75,129,189`、`calibration.py:117` 直接接受 `status`；
  `repositories/devices.py:252-263,298-326,665-693` 的 add/save 直接写 status。
- 影响：`replace(record, status=ACTIVE/FAULT/RETIRED)` 可绕过具名动作；`DRAFT→ACTIVE`、
  `RETIRED→ACTIVE` 等非法跳转可入库。
- 修复：add 只允许初始状态；save 只更新档案字段或把当前状态放进 UPDATE WHERE；
  状态变更只暴露 activate/… 命令方法。

### F2 标定业务版本与乐观锁版本混用

- `calibration.py:126` 只有一个 `version`；`reopen` 原地复用记录（`:237-243`）。
- 契约要求提交后只读、REJECTED→DRAFT 生成新版本号（`docs/architecture/...:137,140`）。
- 影响：驳回时的参数/证据快照可被覆盖，Canonical 快照无法复现。
- 修复：区分 `lock_version` 与 `calibration_version`；reopen 创建新记录 + `supersedes_id`；
  SUBMITTED 后禁止修改参数/证据/坐标系。

### F3 缺少 profile_version/schema_version，parameters 未按类型校验

- `calibration.py:106-126` 只有 `profile_id`；`parameters` 只校验“是对象”。
- Canonical `:180-181,187` 要求 profile 版本、Schema 版本、分类型参数 Schema。
- 影响：不同 Schema 标定写入同一表，Episode 无法复现。
- 修复：记录快照字段；按 `calibration_type` 建 JSON Schema 注册表并强制校验。

### F4 target_id/标定引用缺少项目与语义约束

- `db/models/devices.py:228,267-276`；迁移 `:235,259-268`；`models/devices.py:346-355`。
- 影响：可引用他项目/不存在目标；传感器可挂错类型标定；robot/device 的 profile 不校验 target_type。
- 修复：应用层按 target_type 解析并校验 scope；DB 层触发器/多态目标表兜底；补跨项目直插测试。

### F5 状态迁移前置条件、原因字段与审计缺失

- `models.py:48-61` 无 reason/fault_code/occurred_at；`calibration.py:234-235` reject 无原因；
  全仓无 audit/事件模块。
- 影响：无法满足契约“SUSPENDED/RETIRED 必须原因”“所有状态迁移审计”。
- 修复：状态命令携带 reason/fault_code/occurred_at；引入追加式 audit/outbox。

### F6 时间与坐标系未按 Canonical 失败关闭

- `validation.py:48-53` 只要求 tzinfo，不转 UTC；`repositories/devices.py:61-67` 把 naive 当 UTC。
- Sensor 缺 `frame_convention`/`time_base`/`sync_status`。
- 影响：+08:00 写回 SQLite 会平移 8 小时；坐标帧无法校验。
- 修复：统一 `astimezone(UTC)`；明确 Canonical 字段承载层。

### F7 SQLite 外键未开启，缺 PostgreSQL 验证

- `session.py:30-42`；仓储/迁移测试全用 SQLite。
- 影响：复合外键在测试中不生效，项目隔离数据库兜底未被证明。
- 修复：`PRAGMA foreign_keys=ON`；PG 容器跑跨项目直插/迁移/并发用例。

---

## 3. P2

| ID | 问题 | 位置 |
|---|---|---|
| F8 | `upgrade()` 单函数 342 行 | 迁移 `0003:83-424` |
| F9 | 仓储单文件 744 行 | `repositories/devices.py` |
| F10 | 迁移硬编码枚举；target_type 误用 scope 常量 | 迁移 `:21-37,104-107` |
| F11 | IntegrityError 字符串分类脆弱；错误查询未带 project_id | `repositories/devices.py:70-77,96-107` |
| F12 | activate 两步更新缺事务边界，未写 supersedes_id | `repositories/devices.py:707-744` |
| F13 | DB 缺 Domain 的跨字段约束（有效期/帧成对/证据） | 迁移 `:279-304` |
| F14 | `MappingProxyType` 只冻结第一层；“不可变”名不副实 | `validation.py:42-45` |
| F15 | UUIDv7 未落地；标定 save/并发/跨项目测试缺失 | `identity.py:33`、设备测试 |

---

## 4. 与契约的偏差

| ID | 偏差 |
|---|---|
| D1 | CalibrationProfile 缺 `target_id`（契约 `:88`） |
| D2 | CalibrationRecord 缺 `profile_version`/`schema_version` |
| D3 | `evidence_asset_ids`/`validated_by`/`uncertainty` 必填性冲突 |
| D4 | 坐标系字段名不一致（coordinate_frame vs frame_id/frame_convention）；缺 time_base/sync_status |
| D5 | `version` 同时承担乐观锁与业务版本 |
| D6 | UUIDv7 未实现 |
| D7 | REJECTED→DRAFT 未生成新版本 |
| D8 | 迁移原因/故障码/审计缺失 |
| D9 | 标定引用语义未约束（目标一致性） |

---

## 5. 阶段 1 必须冻结的契约项

1. `CalibrationProfile.target_id` 与版本唯一键。
2. `calibration_version` / `lock_version` 分离与提交后不可变字段。
3. `profile_version`、`schema_version`、`checksum`、`supersedes_id` 写入规则。
4. 不同状态必填矩阵（证据、校验人、uncertainty、valid_to）。
5. Sensor 的 `frame_id`/`frame_convention`/`time_base`/`sync_status` 归属与命名。
6. 分类型 `parameters` JSON Schema、单位/坐标系/手眼必需键。
7. 标定目标完整性方案（应用层 + DB 兜底）二选一并冻结。
8. 状态迁移前置条件、角色、原因/故障码、审计事件契约与错误码映射。
9. `add` 合法初始状态、状态机直写禁令、PostgreSQL 验收门禁。
