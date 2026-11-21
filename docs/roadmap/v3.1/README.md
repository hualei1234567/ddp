# DDP V3.1 建设路线（实施基线）

- 建立日期：2026-11-22
- 事实基线提交：`46864a9`（`origin/main`，PR #62 README 合并）
- 上游依据：
  - `docs/DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md`（唯一建设基线）
  - `docs/roadmap/next-stage-v2/audit/N0-N9-项目最终报告-2026-11-17.md`
  - `docs/roadmap/v2.1/n0/*`（V2.1 执行基线）
  - `docs/roadmap/v3.1/blueprint/*`（V3.1 蓝图包归档，内容逐字节校验一致）
- 本目录地位：**V3.1 的唯一事实入口**。与历史报告冲突时，以 `n0/current-system-baseline.md`
  记录的代码证据为准。

## 目录

| 文件 | 内容 |
|---|---|
| `n0/current-system-baseline.md` | N0-01 当前系统基线（命令可复现） |
| `n0/gap-matrix.md` | N0-02 模块差距矩阵（八条业务能力唯一状态） |
| `n0/reuse-map.md` | N0-03 数据库 / API / 组件复用关系 |
| `n0/task-dag.md` | N0-04 实施任务 DAG 与里程碑 |
| `n0/audit-method.md` | N0-05 审计方法与证据边界 |
| `blueprint/` | 用户提供的 V3.1 蓝图包原件（只读归档） |

## 与 V2.1 的关系

- `docs/roadmap/v2.1/` 保留为 V2.1 执行记录与证据链。
- 本目录是 V3.1 执行基线；新增任务、状态变更、能力矩阵更新写在这里。
- V3.1 不重写既有领域：`docs/roadmap/v3.1/blueprint/README.md` 明确“不重写设备、
  采集、上传、Episode、标注、Dataset、ModelOps 领域”。

## 已确认的基线漂移（本轮修正）

V3.1 蓝图包基于 2026-11-14/15 审计快照，以下结论在当前代码中已过时，不得再作为开工依据：

| 蓝图旧结论 | 当前事实 | 证据 |
|---|---|---|
| 完整标注控制台缺失 | 控制台已存在且覆盖主流程 | `n0/gap-matrix.md` §4.4 |
| CaptureSession 未关联 RawAsset / Episode | 迁移 `0068` + 契约 + artifacts API 已实现 | `backend/migrations/versions/0068_capture_session_artifacts.py:63-89` |
| `/quality` 为占位页 | 已挂载黄金集 / IAA / 抽检 / 质量检查 | `apps/web/src/routes.tsx:227-246` |
| 采集任务需扩字段表示一次执行 | CollectionTask 与 CaptureSession 已是两个聚合 | `docs/adr/0037-capture-session-entity.md` |

## 每个任务的固定开场

```text
Task ID:
Existing:
Reusable:
Gap:
Change:
Risk:
Test:
```

`Existing` 已满足 → 不写新代码；`Reusable` 能满足 → 优先复用。
没有复用评估结论的任务不得开工（`docs/ddp/06-代码质量与防屎山规范.md`、`CLAUDE.md` §一）。
