# ADR-0020 标定记录版本语义与 Profile 快照

- 状态：**ACCEPTED（分两阶段执行）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：ADR-0009、G-04 / G-05 / G-06 / G-07
- 影响范围：`calibration_records` / `calibration_profiles` 表、设备域 API、前端类型

## 1. 三个问题其实是同一个

| 差距 | 现象 | 根因 |
|---|---|---|
| G-04 | 只有 `version` 一个版本字段 | **业务修订号**与**并发锁号**混用 |
| G-05 | 记录只存 `profile_id` | Profile 变更后，记录无法复现当时的 schema |
| G-06 | Profile 无 `target_id` / 版本唯一键 | 无法表达"类型级模板"与"目标级配置"；同名版本可重复 |

三者共同根因：**标定是"某时刻的快照"，但模型按"当前值"存储**。标定一旦
用于生产，事后 Profile 被改，就再也说不清"当初按什么规则做的"。

## 2. 决策

### 2.1 G-04：拆成 `calibration_version` + `lock_version`

| 字段 | 语义 | 变更时机 |
|---|---|---|
| `calibration_version` | **业务修订号**，从 1 开始，每次内容实质变更 +1 | 修改 parameters / frames / validity |
| `lock_version` | **乐观锁**，任何写入 +1 | 任何写入（含状态迁移） |

现状 `version` 同时承担两者：状态从 DRAFT→SUBMITTED 也会 +1，导致"内容第 3 版"
与"第 7 次写入"无法区分，Canonical 快照的"第几版"就没有意义。

**迁移策略**：保留 `version` 作为 `lock_version` 的别名（继续写），
新增 `calibration_version` 并回填为 1。API 同时返回两者，
`version` 标 DEPRECATED，阶段 2 移除。

**为什么不在本阶段删 `version`**：`If-Match` 用的是它，前端所有写动作都依赖它。
改名会让所有写接口同时失效，风险远超收益。

### 2.2 G-05：记录固化 Profile 快照

新增三列：

| 列 | 类型 | 说明 |
|---|---|---|
| `profile_version` | int | 创建时 Profile 的业务版本 |
| `profile_schema_version` | string(64) | 创建时 Profile 的 schema 版本 |
| `profile_snapshot` | JSON | 创建时 Profile 的 `required_types` + `selection_rule` 副本 |

**为什么存快照而不只存版本号**：版本号只能指向"当时的 Profile 那一行"，
但那一行会被改（G-07 的不可变触发器尚未实现）。存快照是唯一能保证
"事后可复现"的做法——多占一点存储，换掉"说不清"的风险。

回填：既有记录用当前 Profile 值回填，并在 `profile_snapshot` 里标注
`{"backfilled": true}`，**不假装它们当时就是这样**。

### 2.3 G-06：Profile 增加 `target_id` 与版本唯一键

| 变更 | 说明 |
|---|---|
| 新增 `target_id` (UUID, nullable) | 绑定到具体目标（机器人/设备/传感器）；`SYSTEM` 作用域必须为空 |
| 新增唯一键 `(project_id, name, target_id, profile_version)` | 同一逻辑 Profile 的版本号不可重复 |
| `target_id` 与 `scope` 一致性 | **领域层校验**（`CalibrationProfile.__post_init__`），不是 DB CHECK |

**实际 scope 取值**：`ROBOT` / `DEVICE` / `SENSOR` / `SYSTEM`（见
`domain/devices/enums.py`），不存在 `TYPE` / `TARGET`。

**`target_id` 与 `scope` 的一致性由领域层保证，没有数据库 CHECK**：迁移 0028 只加了
`target_id` 列与唯一索引，`calibration_profiles` 表上的 CHECK 只有
`profile_version >= 1`。`scope=SYSTEM` 时 `target_id` 不可为空的约束落在
`app/domain/devices/calibration.py` 的 `__post_init__` 里（测试
`tests/unit/test_calibration_snapshot.py::test_system_scope_cannot_bind_target`）。
本表早先版本写的是"一致性 CHECK"，与实现不符，已按实际实现改正。

**`SYSTEM` 作用域的 `target_id` 取两种不同口径，不要混用**：

- **Profile（`calibration_profiles.target_id`）**：`scope=SYSTEM` 时为空，由应用层
  按项目隔离——**不引入一个看起来像外键但不是外键的特殊值**；
- **标定记录（`calibration_records.target_id`）**：`target_type=SYSTEM` 时
  `target_id == project_id`。这不是我们选的约定，而是**数据库复合外键的硬要求**：
  `calibration_records` 通过 `(project_id, target_type, target_id)` 引用投影表
  `calibration_target_refs`，SYSTEM 行的投影由
  `infrastructure/repositories/calibration_target_refs.py::ensure_system_target_ref`
  按 `target_id = project_id` 维护（表上 CHECK 同名约束
  `system_target_matches_project`）。仓储在 `calibration_records.py` 里显式校验
  "SYSTEM 标定的 `target_id` 必须等于当前项目 ID"。

两处口径不同是有意的：Profile 不需要过外键，记录需要。

## 3. 不做的部分

| 项 | 原因 |
|---|---|
| G-07 Profile 不可变触发器 | 独立批次；本次先保证"记录侧有快照"，即使 Profile 被改也不影响历史记录 |
| G-03 `profile_version` 更名 | 同上；本次新增的是记录侧字段，不碰 Profile 的 `version` 语义 |
| `SYSTEM` 作用域记录的 `target_id` 填 `project_id` | 不是选择，是 `calibration_target_refs` 复合外键的硬要求；见 §2.3 |

## 4. 迁移与回滚

迁移 0028（下表按**当时**的列名记录；`calibration_profiles.version` 已在
ADR-0032 / 迁移 0032 改名为 `profile_version`，这里同步改为新名以免误导）：

```sql
ALTER TABLE calibration_records ADD COLUMN calibration_version INT NOT NULL DEFAULT 1;
ALTER TABLE calibration_records ADD COLUMN profile_version INT;
ALTER TABLE calibration_records ADD COLUMN profile_schema_version VARCHAR(64);
ALTER TABLE calibration_records ADD COLUMN profile_snapshot JSON;
UPDATE calibration_records r SET profile_version = p.version,
    profile_schema_version = p.schema_version,
    profile_snapshot = json_object('backfilled', json('true'))
  FROM calibration_profiles p WHERE p.id = r.profile_id;
ALTER TABLE calibration_profiles ADD COLUMN target_id UUID;
CREATE UNIQUE INDEX uq_profiles_name_target_version ON calibration_profiles(project_id, name, target_id, profile_version);
```

回滚：downgrade 删列与索引。**注意**：回滚会丢失 `calibration_version` 的增量历史
（因为原 `version` 从未记录过它），这是有意接受的代价，已在 downgrade 注释写明。

## 5. 验收标准

- [ ] 记录创建时 `profile_version` / `profile_schema_version` / `profile_snapshot` 全部写入
- [ ] Profile 事后被改，记录的三个快照字段不变（有测试）
- [ ] 同一 Profile 的版本号不可重复（真实 PG 上验证唯一键）
- [ ] `scope=SYSTEM` 且 `target_id` 非空 → 422（实现为领域校验，非 DB CHECK；测试 `test_system_scope_cannot_bind_target`）
- [ ] 旧客户端仍可只读 `version`（= `lock_version`）
- [ ] 回填记录的 `profile_snapshot` 标注 `backfilled: true`，不伪装成原始数据
