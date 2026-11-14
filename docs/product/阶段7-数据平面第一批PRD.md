# 阶段 7 数据平面第一批 PRD：外部数据源登记与导入任务

- 版本：v1.0（第一批）
- 日期：2026-11-15
- 依据：`docs/ddp/07-数据治理与HuggingFace-MVP规范.md`、`docs/ddp/03-模块映射与优先级.md` §3
- 契约：`ddp.stage7/1.0.0`（见 `docs/architecture/阶段7-数据平面第一批契约冻结-v1.0.md`）
- 决策：ADR-0018

## 1. 要解决的问题

DDP 已能处理**自有采集**的数据（阶段 2–6），但无法接入**外部公开数据集**。
`docs/ddp/07` 要求优先用 Hugging Face 上许可与访问条件明确的公开第一人称数据集
验证真实数据链路。缺这一环，整条链路只能跑合成数据。

第一批解决最前面两步的前半部分：

```
✅ 数据集登记 → ✅ 导入任务登记 → ⬜ 实际下载 → ⬜ 校验 → ⬜ 解析 → ⬜ 审看 → ⬜ 审核
```

## 2. 用户与场景

| 角色 | 场景 |
|---|---|
| 数据工程师 | 发现一个合适的 HF 数据集，登记进 DDP 并记录许可与访问条件 |
| 数据工程师 | 发起一次小样本导入，跟踪任务状态与结果 |
| 项目 Owner | 审阅项目内登记了哪些外部数据源、许可是否合规 |
| 合规/审核 | 确认某个数据源是否允许用于商业训练 |

## 3. 功能需求

### FR-1 登记数据源

必填：`code`、`name`、`repo_id`、`revision`、`license`、`access_condition`。

- `repo_id` 形如 `owner/name`，不接受 URL（引导用户填正确的东西，而不是接受模糊输入）；
- `revision` **必须是 40 位 commit sha**。前端字段提示直接写明原因：
  "分支名会移动，导入不可复现"；
- `license` 按 Dataset Card 原值填写；不确定时填 `UNKNOWN` 并在备注说明；
- `access_condition=GATED` 时表单自动勾选 `is_gated`，避免提交必然失败的组合。

**验收**：登记后能在列表看到；重复登记同一 `(repo_id, revision, config, split)` 返回 409。

### FR-2 修改治理字段

可改：`name`、`license`、`access_condition`、`is_gated`、`config_name`、`split`、`notes`。
**不可改**：`repo_id`、`revision`。

理由：改 revision 等于换数据，必须重新登记以保证血缘可追。

**验收**：无 `If-Match` 返回 428；版本过期返回 409；尝试改 revision 返回 422（字段不在白名单）。

### FR-3 发起导入

必填：`scope`、`tool_name`、`tool_version`；`scope=SAMPLE` 时 `sample_limit` 必填。

**验收**：创建后状态为 `PENDING`，`allowed_actions` 为 `["start","cancel"]`；
投递 `datasource.import_requested` 事件，payload 含 repo_id 与 revision。

### FR-4 推进导入状态

`PENDING → RUNNING → {SUCCEEDED, FAILED, CANCELLED}`。

- `fail` 必须带 `error_message`（失败留痕）；
- `succeed` 带 `row_count` / `file_count`；
- 终态之后任何动作返回 409 `state_transition_invalid`。

**验收**：状态机非法迁移被拒绝；前端按钮只按 `allowed_actions` 渲染。

## 4. 页面状态覆盖（`CLAUDE.md`「涉及前端必须覆盖」）

| 状态 | 实现 |
|---|---|
| Loading | 表格 `loading` + `PageState` |
| Empty | `locale.emptyText="暂无数据源"` |
| Error | `ApiErrorState` + 重试 |
| Forbidden | `resolvePagePhase` → 清空列与数据（不显示"空"来掩盖 403） |
| Partial | —（本批无分片加载语义） |
| Stale | 刷新失败时显示"展示的是上次成功获取的数据" |
| Disabled | 前端不推断；动作按钮完全由 `allowed_actions` 驱动 |
| Missing project | `ProjectScopeRequired` |

## 5. 明确不做（第一批）

| 不做 | 原因 |
|---|---|
| 真实下载与缓存 | 属 worker 批次；HTTP 线程不得处理大文件 |
| 文件校验接入 | 复用既有上传链路，需与 worker 一起做 |
| 解析（MP4/MCAP/HDF5） | 属下一批 |
| 视频审看 | ADR-0017 已定选型，等解析产出真实媒体 |
| 人工审核 | 属下一批 |
| 质量报告（原始数据维度） | 属后续 |
| 数据集同步/自动发现 | 避免在没验证前引入定时任务 |

## 6. 非功能要求

| 项 | 要求 | 现状 |
|---|---|---|
| 项目隔离 | 所有查询强制 `project_id`，跨项目返回 404 | ✅ 仓储 `scoped_select` |
| 幂等 | 写动作支持 `Idempotency-Key` | ✅ 复用共享实现 |
| 乐观锁 | PATCH 与动作要求 `If-Match` | ✅ 428 / 409 |
| 审计 | 登记/改档/导入状态迁移写 `audit_logs` + `outbox_events` | ✅ 复用共享 `record_change` |
| 大文件 | 不在 HTTP 线程处理 | ✅ 本批不下载 |
| 契约 | OpenAPI 声明 + 冻结基线 + CI SHA 校验 | ✅ stage7 基线已纳入 CI |
| 测试 | 领域 + API + 迁移 + 前端 client | ✅ 60 后端 + 8 前端 |

## 7. 验收标准

- [x] 8 个 operationId 与运行期端点零差异
- [x] 三条硬约束在 OpenAPI / Pydantic / DB 三处同时生效（迁移测试直接验证 DB 层）
- [x] 真实 PostgreSQL 16 上 841 passed / 0 skipped
- [x] 迁移 0024 升级 → 回滚 → 再升级通过
- [x] `make check` 全绿
- [ ] 真实 HF 数据集导入演练（等 worker 批次）
