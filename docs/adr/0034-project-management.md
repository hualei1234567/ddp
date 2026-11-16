# ADR-0034 项目与成员管理：复用既有权限模型，不做硬删除

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-17
- 决策者：General Owner；批准人：用户（长期授权）
- 关联：ADR-0004（多项目隔离）、`CLAUDE.md`（复用优先）、迁移 0002 / 0046
- 影响范围：`app/domain/identity/projects.py`（新增）、`app/application/projects/`（新增）、
  `app/infrastructure/repositories/projects.py`（新增）、`app/api/v1/project_*`（新增）、
  `apps/web/src/pages/projects/`、`apps/web/src/pages/people/`（新增）

## 1. 背景

`project_id` 是全平台的隔离基石（ADR-0004），但阶段 8 之前：

- 没有任何 `/projects` 端点，项目只能靠 seed / 脚本写入；
- 前端导航的「项目管理」「人员与团队」是 `available: false` 的禁用项；
- 项目改名只能手写 SQL，没有审计与并发保护；
- 项目下线只能删除，而 `projects.id` 被全部业务表以 `ON DELETE CASCADE` 引用。

本次补齐项目与项目内成员的最小管理能力，**不引入企业级多租户、计费或账单**。

## 2. 决策

### 2.1 复用既有权限模型，不新建"项目管理域"

成员关系与角色早已存在：`project_members` 表、`ProjectRole`
（`viewer` / `editor` / `owner`）、`ROLE_PERMISSIONS` 权限矩阵、
`ProjectAccessService` 与 `app.api.deps` 的 `get_project_scope` /
`get_write_scope` / `get_delete_scope`。本次只补**写入面**：

- 领域：`app/domain/identity/projects.py`（项目实体、状态、成员视图、最后 owner 规则）；
- 应用：`app/application/projects/`（列表、创建、修改、成员增删改）；
- 基础设施：`app/infrastructure/repositories/projects.py`；
- API：`app/api/v1/project_routes.py`。

**没有**新增角色、没有新增权限矩阵、没有第二套成员表。
`ProjectContext` / `ProjectScope` 仍是唯一授权权威，路由依赖仍是唯一判定点。

### 2.2 谁能创建项目：仅平台管理员

项目创建发生在任何项目上下文之外，普通成员的权限完全来自成员关系，
系统里不存在"允许创建项目"的既有信号。可选方案只有两个：

1. 任何已认证用户都能创建（自服务）；
2. 平台管理员创建，创建者自动成为该项目的 `owner`。

选择方案 2：它是现有模型能直接表达的最保守语义，也与"不做企业级多租户"的边界
一致。创建者写成 `owner` 保证项目有人可继续管理成员，而不是只能由平台管理员接手。
`project_members.user_id` 有外键，因此要求创建者已登记在 `users` 目录中，
否则返回 403，而不是留下无人可管理的项目。

### 2.3 不提供删除项目，只提供 `ARCHIVED` 状态位

`projects.id` 被采集、Episode、标注、数据集、审计等全部业务表以
`ON DELETE CASCADE` 引用。`DELETE /projects/{id}` 等于一次性销毁该项目全部数据，
且不可恢复，与"删除类操作必须逐条确认、先备份"的规范冲突。

替代方案：`PATCH /projects/{id}` 支持 `status: "ARCHIVED"`。默认列表只返回
`ACTIVE` 项目，`?status=ARCHIVED` 可查看归档项目，可随时恢复。

**明确不做的事**：`ARCHIVED` 当前**不阻断**业务写入。理由是阻断必须在
`ProjectContext.can()` 上统一实现并回归全部业务端点，属于独立任务卡；
做一个只覆盖部分端点的"半个开关"会让状态含义随入口不同而不同，比不做更危险。
该缺口已作为风险登记在交付报告中，后续任务卡需要同时补：归档拦截、跨模块回归、
以及归档项目的下载/导出行为定义。

### 2.4 成员管理：`MANAGE_MEMBERS` + "至少保留一名 owner"

- 读成员列表：项目 `READ`；
- 添加成员、调整角色、移除成员：项目 `MANAGE_MEMBERS`（owner 或平台管理员）；
- 项目改名 / 改描述 / 归档：同样要求 `MANAGE_MEMBERS`——项目元信息属于项目治理，
  不是普通业务写入，`editor` 不应能改项目名。

"最后一名 owner"保护：

- 降级或移除最后一名 owner 返回 409；
- owner 移除自己同样受此约束（有第二名 owner 时才允许）；
- 成员变更前先锁 `projects` 行（PostgreSQL `SELECT ... FOR UPDATE`；
  SQLite 忽略该子句但写事务本身串行），把同一项目的成员写操作串行化。
  没有这把锁时，两个并发请求各自降级不同的 owner 会同时通过"数 owner"检查，
  提交后项目变成 0 名 owner。

同值改角色不写库、不产生审计（PATCH 幂等）。

### 2.5 本阶段没有用户目录与邀请

`users` 表只有 `display_name`，没有任何用户创建/邀请入口。本次按
`user_id` 添加成员：用户不存在返回 404，不做"静默创建用户"。
前端表单明确要求填写已登记的用户 ID，并说明获取方式。用户目录/邀请属于
身份系统集成，不在本次范围。

### 2.6 幂等策略：由唯一约束承担，不引入 `Idempotency-Key`

`idempotency_records` 的作用域是 `(project_id, user_id, key)`，而创建项目时
尚不存在项目，天然无法落幂等记录。实际幂等性由数据约束保证：

- 项目：`projects.code` 全局唯一，重复创建返回 409；
- 成员：`(project_id, user_id)` 唯一，重复添加返回 409；
- 改角色/移除：按值幂等（同值不写库；移除不存在的成员返回 404）。

因此本域写接口不要求 `Idempotency-Key`，也不修改契约中的通用头规则。

### 2.7 错误映射：沿用身份域既有做法，不新增第八张域错误表

`app/api/errors.py` 已有 8 张域错误映射表且本文件已 793 行（超过 800 行需拆分）。
身份域既有约定（`ProjectAccessService`）是应用层直接抛 `core.errors.AppError`。
本次沿用该约定：错误码全部落在既有集合内
（`forbidden` / `not_found` / `conflict` / `validation_failed` /
`precondition_required`），不引入新错误码，也不扩大 `api/errors.py`。

### 2.8 迁移

迁移 `0046_project_management` 为 `projects` 增加
`description` / `status` / `version` 与两条 CHECK、一个 `(status, code)` 索引；
必须显式 `op.add_column` + `batch_alter_table`（SQLite 加 CHECK 只能重建表），
不得使用 `Model.__table__.create()`。

编号 0046 由任务分配，但当前分支 0044/0045 由并行任务开发，因此
`down_revision` 暂接 `0043`；**合并时必须改为 `0045`**，否则会出现两个 head。
迁移测试同样以 `0043` 为回滚目标，合并时一并改为 `0045`。

## 3. 契约缺口

`packages/contracts/openapi.yaml` 尚未声明 8 个新操作，按任务要求本次不修改契约。
缺口清单与字段/状态码/错误码见交付报告，待契约 Owner 走变更流程后
把 `parity-baseline.json` 的 8 个 `runtime-only` 键清除。

## 4. 后果

正面：

- 项目与成员有了可审计、带并发保护的 HTTP 入口与页面；
- 权限模型没有分叉，ADR-0004 的隔离保证不变；
- 项目下线不再需要删除数据。

代价与风险：

- `ARCHIVED` 不阻断业务写入（见 2.3）；
- 人员页面按项目选择，项目超过 100 个时下拉框只显示前 100 个（可用 URL 指定）；
- 添加成员需要用户 ID，缺少用户目录/邀请体验；
- 迁移编号需要在合并时重排。
