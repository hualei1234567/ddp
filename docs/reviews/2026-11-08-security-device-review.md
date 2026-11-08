# DDP 设备域安全审查报告

> 审查对象：main @ `922db85`（设备域 BE）与 `0018d4e`（设备域 FE）
> 审查角色：Security Auditor（原生 Subagent）
> 日期：2026-11-08
> 结论：**阻断（有条件）**。当前未挂载设备 API，因此没有可远程触发的设备 IDOR；
> 但阶段 1 设备接口上线前，下列 P0/P1 必须全部关闭。

---

## 0. 结论

- 当前 `backend/app/api/v1/router.py` 只注册了 `platform`，设备 API 尚未挂载，P0 暂不可远程触发。
- 存在 **1 个 P0（本地 Header 身份 + 默认 `env=local`，可自封平台管理员）**。
- 存在 **5 个 P1**：标定 `target_id` 跨项目引用、执行人/校验人未绑定服务端主体、
  已校验标定溯源可被 `save()` 重写、SQLite 外键未生效、读写分离依赖路由自觉。
- 数据库复合外键、状态机白名单、项目作用域查询方向正确；问题集中在“认证默认值、目标引用、
  主体绑定、不可变性与真实库验证”。

---

## 1. P0

### P0-1 本地 Header 身份 + 默认 `env=local`，可自封管理员

**位置**
- `backend/app/api/deps.py:43-63`
- `backend/app/core/config.py:24,46-48`
- `.env.example:2`
- `docker-compose.yml:73,85`
- `backend/app/application/identity/access.py:28-36`

**问题**
`Settings.env` 默认 `local`，`get_current_principal` 在 local 下信任 `X-User-Id` 与
`X-Platform-Role`；平台管理员只校验项目存在，`role=None` 后 `can()` 恒为 True。
compose 直接 `env_file: .env` 并暴露 `8000`，默认配置即为 local。

**攻击**
按默认方式部署后，任意可达 8000 端口者发送
`X-User-Id: <任意 UUID>` + `X-Platform-Role: admin`，即可获得任意已知项目的读写删权限，
或冒充任意成员。

**修复**
- `env` 默认改为 `production`；本地 Header 认证改为显式开关（如 `DDP_LOCAL_HEADER_AUTH=true`），
  且只允许 loopback。
- 非 local 且未配置真实 JWT/OIDC 时拒绝启动，而不是静默关闭认证。
- 平台角色只能来自服务端身份源，永远不从请求头读取。
- 阶段 1 上线前落地 JWT/OIDC。

---

## 2. P1

### P1-1 标定 `target_id` 无项目约束

**位置**：`backend/app/infrastructure/db/models/devices.py:228,261-299`；
`backend/migrations/versions/0003_stage1_device_calibration.py:235,259-268`；
`backend/app/infrastructure/repositories/devices.py:558,624`

**问题**：`calibration_records.target_id` 是裸 UUID，只约束 `profile_id`/`supersedes_id`。
项目 A 的 editor 可写入 `target_type=SENSOR, target_id=<项目 B 的 sensor UUID>`。
`add()` 只校验记录自身 `project_id`。

**修复**：写入前按 `target_type` 用项目作用域加载目标，不存在即拒绝；或增加
`target_project_id` + 三张目标表的复合外键/触发器；补 PostgreSQL 负向测试。

### P1-2 执行人/校验人/actor 未绑定服务端主体

**位置**：`backend/app/application/devices/ports.py:24..151`；
`backend/app/domain/identity/models.py:96-114`；
`backend/app/domain/devices/calibration.py:200-210`；
`backend/app/infrastructure/db/models/devices.py:245-254`；
`backend/app/infrastructure/repositories/devices.py:571-572`

**问题**：`actor_id` 是独立入参；`ProjectScope` 不含主体；`performed_by/validated_by`
只外键到全局 users。WRITE 用户可伪造执行人/校验人，四眼原则失效。

**修复**：`ProjectScope/Context` 携带 `actor_id`；仓储从 scope 派生 `created_by/updated_by`；
`performed_by = principal.user_id`；`validated_by` 必须是本项目有校验权限的成员；
新增负向测试。

### P1-3 `save()` 可重写已校验标定的溯源字段

**位置**：`backend/app/infrastructure/repositories/devices.py:553-576,665-693,735`

**问题**：`save()`/`activate()` 全量写入 `target_id/profile_id/checksum/evidence_asset_ids/
performed_by/validated_by/supersedes_id`。已 VALIDATED 的记录可被静默篡改。

**修复**：拆分写入语义；`SUBMITTED` 之后设置不可变字段白名单；`activate()` 不重写溯源字段。

### P1-4 SQLite 未开启 `PRAGMA foreign_keys=ON`

**位置**：`backend/app/infrastructure/db/session.py:29-39`；
`backend/tests/infrastructure/test_devices_repository.py:52-61`；
`backend/tests/migrations/test_devices_migration.py:31,62`

**问题**：SQLite 默认外键关闭，当前测试中的跨项目外键引用不会被拒绝；
测试通过只证明约束“被创建”，不证明“被强制”。

**修复**：SQLite `connect` 事件执行 `PRAGMA foreign_keys=ON`；增加跨项目外键负向测试；
项目隔离必须在 CI 的 PostgreSQL 上跑一遍。

### P1-5 `get_project_scope` 不校验 READ

**位置**：`backend/app/api/deps.py:87-106`

**问题**：`get_project_scope` 只依赖 `get_project_context`；写接口若忘记叠加
`require_project_permission(WRITE)`，viewer 可写入。默认路径不是 fail-closed。

**修复**：`get_project_scope` 默认依赖 READ；另提供 `get_write_scope`/`get_scope(action)`；
补 viewer POST/DELETE 必须 403 的路由级测试。

---

## 3. P2

| ID | 问题 | 位置 | 建议 |
|---|---|---|---|
| P2-1 | 输入长度/JSON 无上限；`frozen_mapping` 浅拷贝 | `domain/devices/validation.py:20-45`、`calibration.py:112,119`、`models.py:82,136` | 字段最大长度、JSON 大小/深度/键数限制；按 calibration_type 做 Schema 校验 |
| P2-2 | `positive_float` 接受 NaN/Infinity | `domain/devices/validation.py:62-65` | 增加 `math.isfinite` |
| P2-3 | 错误回显输入；未映射异常带 SQL 参数进日志 | `validation.py:37-39`、`repositories/devices.py:70-77`、`main.py:45-72`、`logging.py:25-27` | 统一设备域异常处理；IntegrityError 映射 409/500 通用文案；日志脱敏 |
| P2-4 | `location_id` 无外键；owner/actor 可指向非成员 | `db/models/devices.py:81-90,142-147,188-193`、迁移 0003:131-132,180-181 | `(project_id, location_id)` 复合外键；成员资格校验 |
| P2-5 | 默认 Secret 不安全且无启动校验 | `core/config.py:37-44`、`.env.example:12,17` | 非 local 禁止默认值；启动自检；Secret 管理 |
| P2-6 | 项目级联删除与 downgrade 不可逆 | 迁移 0003:63-68,427-454 | 项目软删除/审批；downgrade 危险开关 + 备份检查 |
| P2-7 | 无安全头/CORS/限流 | `main.py:22-32` | 安全头中间件、CORS 白名单、认证与写接口限流 |

---

## 4. 设计上做得好的地方

- 按 ID/编码读取都附加 `project_id` 过滤；跨项目返回 `None`，与“不存在”同路径，避免枚举。
- 写操作统一 `scope.ensure(entity.project_id)`；`ProjectScope` 只能由服务端 `ProjectContext` 派生。
- 授权默认拒绝；未知角色抛错；非 local 拒绝 Header 身份。
- 除 `target_id` 外，设备域表均使用 `(project_id, parent_id)` 复合外键。
- 枚举、正数、`validated_by <> performed_by`、已校验状态必须带校验人等 CHECK 齐全；
  单一 ACTIVE 标定用部分唯一索引兜底。
- SQL 全参数化，无字符串拼接；标定状态机显式白名单、终态无出边。

---

## 5. 阶段 1 上线前必须关闭

| 级别 | 项 | 关闭判据 |
|---|---|---|
| P0 | P0-1 Header 身份 / 默认 local | 非 local 强制真实认证；Header 认证显式开关且默认关闭；启动自检 + 测试 |
| P1 | P1-1 target_id 跨项目 | 应用层 scope 校验或 DB 项目约束；PostgreSQL 负向测试 |
| P1 | P1-2 执行人/校验人绑定 | Scope 携带主体；仓储派生 created_by/updated_by；校验人须为本项目授权成员 |
| P1 | P1-3 标定溯源不可变 | save 不再全量重写溯源字段 |
| P1 | P1-4 SQLite 外键 | `PRAGMA foreign_keys=ON`；PostgreSQL CI 跨项目隔离测试 |
| P1 | P1-5 读写分离 | `get_project_scope` 默认 READ；写接口显式 WRITE/DELETE |

补充：设备路由挂载前必须先落 `require_project_permission` 强制接线与路由级越权测试，
否则 P0-1 与 P1-5 组合会放大为跨项目写漏洞。
