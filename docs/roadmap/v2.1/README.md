# DDP V2.1 建设路线（实施基线）

- 建立日期：2026-11-18
- 事实基线提交：`7a9bb4b`（main）
- 上游依据：
  - `docs/DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md`（唯一建设基线）
  - `docs/ddp/00–09`（持续迭代治理）
  - `docs/roadmap/next-stage-v2/audit/N0-N9-项目最终报告-2026-11-17.md`（上一轮事实）
  - `~/Downloads/DDP_V2.1_下一阶段建设包_FINAL/`（V2.1 实施级蓝图，已复制要点进本目录）

## 目录

| 文件 | 内容 |
|---|---|
| `n0/current-system-baseline.md` | N0-01 当前系统基线（可从单一文档恢复事实） |
| `n0/capability-matrix.md` | N0-02 能力矩阵（V2.1 WBS 全部条目唯一状态） |
| `n0/existing-reusability-matrix.md` | Existing / Reusable 矩阵（开发前必查） |
| `n0/technical-debt-list.md` | 技术债清单（含证据与修复建议） |
| `n0/v2.1-backlog.md` | N1–N9 Backlog（Task ID、优先级、状态、验收） |
| `n0/v2.1-dependency-dag.md` | 依赖 DAG 与并行分组 |
| `n0/risk-list.md` | 风险清单（概率/影响/缓解/责任） |
| `n0/no-go-list.md` | 停止线 |

## 与 V2.0 的关系

- `docs/roadmap/next-stage-v2/` 是 **V2.0 的历史蓝图与阶段性报告**，保留作为证据链。
- 本目录是 **V2.1 执行基线**：新增任务、状态变更、能力矩阵更新都写在这里。
- 两者冲突时，以本目录 `n0/current-system-baseline.md` 记录的代码事实为准；
  产品方向以 V1.0 总规范 + V2.1 建设包为准。

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
没有复用评估结论的任务不得开工（`docs/ddp/06`、CLAUDE.md §一）。

