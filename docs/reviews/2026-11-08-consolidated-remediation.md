# 设备域四路审查整改计划

> 日期：2026-11-08
> 输入：安全审查、QA 代码审查、架构审查、UX 审查
> 结论：设备域当前最多 Code Complete，**不得以 QA Accepted 进入阶段 1**。
> 原则：先冻结契约，再并行修复；P0 全部关闭前不进入阶段 1 验收。

---

## 1. 阻断结论

| 来源 | 结论 | P0 | P1 | P2 |
|---|---|---|---|---|
| 安全 | 阻断（有条件） | 1（Header 身份 + 默认 local） | 5 | 7 |
| QA | REQUEST CHANGES | 1（前后端契约漂移） | 8 | 9 |
| 架构 | 有条件通过 | 0 | 7（F1–F7） | 8（F8–F15）+ 9 契约偏差 |
| UX | 只读骨架，不可验收 | 1（分页/加载更多） | 7 | 5 |

---

## 2. 首批必须关闭的 P0

1. **认证默认值**：`DDP_ENV` 默认 `local` + 信任 `X-User-Id`/`X-Platform-Role`。
2. **前后端契约漂移**：`channels` 前端 `string[]` vs 后端 `int`；`uncertainty` 前端 `number` vs 后端对象。
3. **列表只加载 100 条**：设备/传感器/标定超过 100 条不可达。

---

## 3. 任务拆分（DDP-201～210）

| 任务 | 角色 | 优先级 | 内容 | 依赖 | worktree |
|---|---|---|---|---|---|
| DDP-201 | BE | P0 | 认证默认值与项目上下文 fail-closed（P0-1、P1-2、P1-5） | — | ddp-be |
| DDP-202 | BE | P0 | 标定 target 项目约束、溯源不可变、状态机直写禁令、激活原子/过期/并发（P1-1/3、F1/F2/F12） | 201 | ddp-be |
| DDP-203 | BE | P1 | SQLite 外键强制、输入上限、NaN、异常映射（P1-4、P2-1/2/3） | — | ddp-be |
| DDP-204 | BE | P1 | 安全响应头/CORS/限流/Secret 启动校验（P2-5/7） | 201 | ddp-be |
| DDP-205 | QA | P1 | 安全与迁移负向测试、契约测试、阶段 1 验收（含 PG） | 201–210 | ddp-qa |
| DDP-206 | FE | P0 | 契约对齐：channels/uncertainty/RGB_D + 真实后端 fixture + 契约测试 | — | ddp-fe |
| DDP-207 | Architect | P0 | 设备域契约冻结：D1–D9、F2/F3/F5/F6 字段决策、ADR-0009 | — | main（docs） |
| DDP-208 | BE | P0 | 设备 API + 应用用例层 + 错误码映射（QA P1-8） | 207、201 | ddp-be |
| DDP-209 | FE | P1 | 分页/加载更多、详情刷新全部 Tab、筛选/Tab 写 URL（UX P0/P1-2/5） | 206 | ddp-fe |
| DDP-210 | FE | P1 | 心跳/标定过期 Stale、移动端、长 ID Tooltip/复制、组件测试（UX P1-4/6/7、QA P1-9） | 206 | ddp-fe |

---

## 4. 执行波次

### Wave 1（并行，当前）

- **DDP-201** BE：认证/项目上下文安全。
- **DDP-206** FE：契约对齐。
- **DDP-207** Architect：契约冻结文档。

三者改不同目录：`backend/app/{api,core,domain/identity}`、`apps/web/src/services`、`docs/architecture`。

### Wave 2（Wave 1 完成后并行）

- DDP-202 BE（依赖 201）
- DDP-208 BE（依赖 207/201）
- DDP-209 FE（依赖 206）
- DDP-210 FE（依赖 206）

BE 的 202/208 共享 `backend/`，需按顺序或拆分子任务；FE 的 209/210 共享 `apps/web/`，同样顺序执行。

### Wave 3

- DDP-203/204 BE
- DDP-205 QA 验收（依赖全部）

---

## 5. 门禁

每个修复任务必须：

1. `ruff check` + `ruff format --check` + `pytest` 通过；
2. 前端 `tsc` + `eslint` + `vitest` + `build` 通过；
3. 新增负向测试：伪造身份、跨项目 target、篡改已校验标定、viewer 写 403、SQLite 外键；
4. 契约变更同步 `packages/contracts` 与前端类型；
5. 未执行项标 `NOT RUN`，环境阻塞标 `BLOCKED`；
6. 交付说明附真实命令与结果。

---

## 6. 不做/后置

- UUIDv7、profile_version/schema_version、审计事件表等需要契约冻结的项，先由 DDP-207 出决策，再排期。
- 迁移/仓储拆分（F8/F9）、分页服务端游标、设备写操作 UI 属阶段 1 后续迭代，不在首批 P0。
