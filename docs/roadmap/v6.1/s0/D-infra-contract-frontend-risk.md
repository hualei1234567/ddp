# DDP V6.1 S0 · 审计代理 D：迁移拓扑 / 契约与前端 Gap / 工程风险

> 只读审计产出 · 审计时间 2026-11-28 · 仓库基线 `main = c5f78be`（PR#171 合并）

## ① 迁移拓扑证明（单 head）

命令与输出（本地库 localhost:55432/ddp）：

```
$ cd backend && PYTHONPATH=".:../data-platform" DDP_DATABASE_URL="postgresql+psycopg://ddp:ddp@localhost:55432/ddp" \
  .venv/bin/python -m alembic heads
0086 (head)
```

- `alembic heads` 输出**仅一个 head：0086** ✓
- 文件数 vs 图谱步数：`migrations/versions/*.py` = **76 文件**；`alembic history` = **76 步**；文件内 `revision = ` 行 = **76 条**，三者一致，无孤儿/重复 revision ✓
- 与主控确认事实一致（迁移 head=0086）。

### V6.1 新表预判（grep `app/infrastructure/db/models/` + `app/domain/`）

| V6.1 概念 | 现状 | 证据 |
|---|---|---|
| `video_frame` | **缺失** | db/models 无任何 video_frame 表/模型；仅有运行时验证证据（`docs/testing/evidence/2026-11-25-lerobot-frame-verify`） |
| `frame_index` | **领域已有，持久化缺失** | `app/domain/episodes/sync_frame_index.py`、`app/domain/processing/cleaning_rules.py`（frame_index 清洗规则）已存在；无对应 DB 表 |
| `clock_sync` | **领域已有，持久化缺失** | `app/domain/episodes/sync_analysis.py` 已声明 `time_domain/clock_source/clock_id/start_offset_frames`，属计算型不落库；无 clock_sync 表 |
| `training_run` | **部分（近邻已有，主表缺失）** | 已有 `external_training_references`、`training_requests`、`training_deliveries`、`training_adapter_callbacks`；`enums.py:99` 注释已引用「DatasetVersion → TrainingRun（ADR-0036）」概念但无 `training_runs` 表 |

**结论**：V6.1 至少需新增 0087+ 迁移补 `video_frames`（含 frame_index/clock 偏移列）与 `training_runs` 两类持久化；frame_index/clock_sync 领域逻辑已先行，落库成本低。

## ② 前端 13 类 Gap 表（`apps/web/src/routes.tsx` + `pages/` 求证）

| # | 页面类别 | 状态 | 证据（路由 / 页面文件） |
|---|---|---|---|
| 1 | 多模态 Episode Explorer | **部分** | `episodes` + `episodes/:episodeId`（EpisodeListPage/DetailPage）；跨流多模态 Explorer 视图无 |
| 2 | 视频时间轴 | **缺失** | 无 timeline 路由/组件；仅 `pages/datasources/playback.ts` 与 episode 详情文本字段 |
| 3 | Pipeline 监控 | **缺失** | Episode 详情仅展示 `pipeline_id@pipeline_version` 文本；后端 `processing_runs/processing_run_steps` 表无前端页面 |
| 4 | 清洗工作台 | **部分** | `collection/workbench/:taskId`（WorkbenchPage + checklist）为采集工作台；专用清洗/处理工作台无 |
| 5 | 标注运营 | **已有** | `annotation/*` 全套：my-tasks、reviews、arbitrations、workload、gold-sets、iaa-reports、quality-samples、sample-sets、schemas、submissions、workbench |
| 6 | Judge 质量 | **部分** | quality-samples / iaa-reports 已有；LLM-as-Judge 专项质量页无 |
| 7 | Dataset 管理 | **已有** | `datasets`、`datasets/:id`、`datasets/:id/versions/:versionId`、`exports`、TrainingRequest/Delivery 页 |
| 8 | Training Run | **缺失** | `training` 仅为 redirect → training-requests；无 TrainingRun 列表/详情页（对应后端 training_runs 表缺失） |
| 9 | Evaluation | **已有** | `evaluations` + `evaluations/:evaluationId`（models/EvaluationListPage + DetailPage） |
| 10 | Badcase | **缺失** | 全仓无 badcase 路由/页面 |
| 11 | 设备 | **已有** | `devices/:id`、`robots/:id`、Calibration、Sensor 列表 |
| 12 | 驾驶舱 | **部分** | `analytics`（AnalyticsPage + 用量配额表）已有；含 Pipeline/质量/任务聚合的运营驾驶舱无 |
| 13 | 推理服务/任务（补充类） | **已有** | `inference-services`、`inference-tasks/:taskId`（models/InferenceService/Task 页） |

> 主控描述为「13 类」，实际枚举 12 类，表内第 13 行为补齐项。
> 统计：**已有 6 · 部分 4 · 缺失 3**（时间轴、Pipeline 监控、Badcase 完全缺失；Training Run 缺页面且后端缺表，双重缺口）。

## ③ 测试 / CI 画像

| 维度 | 数值 | 来源 |
|---|---|---|
| backend 用例 | 3478（主控确认，未重跑） | 主控事实 |
| web 用例 | 1250（主控确认，未重跑） | 主控事实 |
| 证据目录 | **38**（`docs/testing/evidence/`，2026-11-08 → 2026-11-27 持续产出） | 实测 |
| CI main 最新 | **success · 45m36s**（run 46026948991，PR#171 合并触发） | `gh run list` |
| CI main 次新 | **failure · 48m27s**（run 45939067423，PR#170 合并触发） | `gh run list` |
| CI job 构成 | 5 jobs：契约 OpenAPI lint/兼容性 diff、前端 类型/lint/测试/构建、后端 lint/迁移/测试、前端关键路径 E2E、Secret/依赖/Agent 守卫 | `gh run view` |
| 单 job duration | **跳过**：本 gh 版本对 run 46026948991 返回 `durationMinutes: null`、`started_at/completed_at: null`，无法取数 | 实测 |
| Dependabot 开放 PR | **15 / 最近 30 条开放 PR 中**（含 major：prettier 3→3.10、@eslint/js 9→10、alembic 1.14→1.20、antd-icons 5→6） | `gh pr list` |

### CI 时长对多代理并行开发的影响（一句话）

**~46 分钟的 main CI 与 15 个积压 Dependabot PR 使每次 PR 反馈周期接近 1 小时、合并闸门串行化——多代理并行开发时 CI 是第一瓶颈，各代理必须本地先行跑对应子集测试、按域拆小 PR，否则并行度被合并队列吃掉。**

## ④ 工程风险 Top 3

1. **CI 反馈环 ~46min + Dependabot 15 个积压（含 4 个 major 升级）**：多代理并行 PR 在慢闸门上串行排队，迭代吞吐受限；major 依赖（eslint 10 / prettier 3.10 / alembic 1.20 / antd-icons 6）一旦合入可能波及 1250 条 web 用例，需专人批量验证消纳。
2. **V6.1 持久化断层**：`video_frame` 表缺失、`clock_sync/frame_index` 仅有领域计算无落库、`training_runs` 主表缺失（仅近邻表）——0087+ 迁移是 V6.1 第一依赖项，阻塞 Episode Explorer/时间轴/Training Run 全部前端工作。
3. **前端三类页面完全缺失（时间轴/Pipeline 监控/Badcase）+ 四类部分缺失**：后端 `processing_runs/processing_run_steps` 已有数据但零前端暴露；按上表缺口估算 V6.1 前端新建/增强面积约 7 个页面簇，是三域中最大的工程面。
