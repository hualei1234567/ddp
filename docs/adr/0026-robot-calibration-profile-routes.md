# ADR-0026 Robot 与 CalibrationProfile 路由（G-01）

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：`阶段1-设备域契约冻结-v1.md` §2.1/§2.3、ADR-0020（Profile 不可变）、
  ADR-0025（outbox 语义）；审计 G-01
- 影响范围：`/robots` 全套端点、`/calibration-profiles` 端点、`robots` 表六列

## 1. 背景

阶段 1 设备域冻结文档定义了 Robot 与 CalibrationProfile 的完整契约，
但**两者都没有对外路由**：Robot 只能由测试直接插库，Profile 同理。
后果是冻结契约对外不可用——数据模型存在、规则存在，但没有入口。

审计把这记为 G-01（P0，功能新增）。

## 2. 复用审计（`CLAUDE.md` §一 强制前置）

动手前先确认已有能力，结论是**几乎全部可复用**：

| 能力 | 现状 | 结论 |
|---|---|---|
| `Robot` 领域模型 | `domain/devices/models.py`，含全部档案字段与生命周期动作 | 复用 |
| `SqlRobotRepository` | `add/get/get_by_code/list/save` 齐备，含复合外键与唯一约束 | 复用 |
| `CalibrationProfile` 领域模型 | 含 scope/target_type/target_version 校验 | 复用 |
| `SqlCalibrationProfileRepository` | `add/get`，且**刻意不提供 UPDATE**（G-07） | 复用 |
| Profile 不可变守卫 | 迁移 0029 的触发器拦 UPDATE/DELETE | 复用（路由层不设 PATCH） |
| 幂等 / 分页 / 审计 / outbox | `IdempotencyGuard`、`build_page`、`record_change` | 复用 |
| Device 路由 | 作为端到端模板（动作、`If-Match`、幂等） | 参照 |

**唯一真实缺口**：`robots` 表没有 G-09 引入的状态语义六列
（`status_reason` / `fault_code` / `fault_detail` / `fault_occurred_at` /
`status_changed_at` / `status_changed_by`）。G-09 当时只补了 devices 与 sensors。

这不是"顺手加字段"，而是路由的**前置条件**：没有这六列，
`POST /robots/{id}/suspend {"reason": "..."}` 的 reason 无处落库，
等于"点了挂起但不知道为什么"，与 G-10 修掉的问题同类。因此本次一并补齐（迁移 0031）。

## 3. 决策

### 3.1 端点集合

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/robots` | 游标分页，可按 status 过滤 |
| POST | `/robots` | 创建，初始 `PROVISIONED`，`Idempotency-Key` |
| GET | `/robots/{robot_id}` | 详情 |
| PATCH | `/robots/{robot_id}` | 改档案，**不含 status**，`If-Match` |
| POST | `/robots/{robot_id}/activate` | 激活 |
| POST | `/robots/{robot_id}/maintenance` | 进入维护 |
| POST | `/robots/{robot_id}/fault` | 上报故障，`fault_code` 必填 |
| POST | `/robots/{robot_id}/suspend` | 挂起 |
| POST | `/robots/{robot_id}/retire` | 退役 |
| POST | `/calibration-profiles` | 登记新版本，`Idempotency-Key` |
| GET | `/calibration-profiles/{profile_id}` | 详情 |

**没有 `PATCH /calibration-profiles/{id}`**：Profile 是不可变配置版本，
改配置 = 新建版本。这一点在路由层就体现为"不存在写-改端点"，
而不是留一个 PATCH 再由下层拒绝——后者会让调用方以为可以原地改。
测试 `test_profile_has_no_update_route` 断言该路径返回 405。

### 3.2 为什么先补 robots 表

见 §2 末尾。补列与本 ADR 同一次提交，否则路由的状态动作语义不完整。

### 3.3 与 Device 的差异

`Robot._validate_profile` 要求 `target_type=ROBOT`，Device 要求 `DEVICE`。
这类「实体类型 ↔ Profile 目标类型」的对应关系目前只有两处，各自内联在用例里；
出现第三种时再提取公共校验函数——现在提取只会多一层间接。

### 3.4 Profile 的 `profile_version` 与表列 `version`

冻结契约的字段名是 `profile_version`，但表列仍叫 `version`（G-03 才改名）。
对外响应统一用 `profile_version`，避免把内部列名泄漏进契约；
前端契约测试 `标定配置对外用 profile_version，不暴露表列名 version` 锁定这一点。

## 4. 兼容性

| 项 | 变化 |
|---|---|
| 新增端点 | 8 + 2 = 10 个（纯新增，无破坏） |
| 新增 schema | `Robot` / `RobotPage` / `RobotCreate` / `RobotPatch` / `CalibrationProfile` / `CalibrationProfileCreate` |
| 新增路径参数 | `RobotId` / `ProfileId` |
| 数据库 | 迁移 0031 给 `robots` 加 6 个可空列（可回滚，实测升降） |
| 契约版本 | `1.6.0` → `1.7.0` |

旧端点与旧字段无一改动。

## 5. 验收证据

| 项 | 结果 |
|---|---|
| API 测试 | 10 passed（创建/列表/详情/隔离/幂等/动作/状态语义/Profile 引用/target_type 校验/版本连续/无 PATCH） |
| 真实后端契约快照 | 快照脚本实跑 `POST /robots`、`GET /robots`、`GET /calibration-profiles/{id}` 均成功并纳入快照 |
| 前端契约对齐 | 350 passed（含 Robot 字段集合、Profile 无 `version` 泄漏、严格解析器接受） |
| 迁移升降（真实 PostgreSQL） | `0030 → 0031 → 0030 → 0031` 通过 |
| Redocly lint | valid |
| 真实 PostgreSQL 全量 | 968 passed / 0 skipped |
| `make check` | 通过 |
