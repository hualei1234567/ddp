# 四类运营看板指标口径（吞吐 / 质量 / 成本 / 数据飞轮）

- 版本：v1.0 · 2026-11-26
- 适用：V5.2 阶段 4-5「可运营 / 可观测」；对应
  `docs/roadmap/v5.2/phase4-production-plan.md` §3 4-1 与 §4-5
- 数据来源红线：**所有指标必须能追溯到真实来源**（Prometheus 指标名 / HTTP 端点 /
  数据库表）。本文不发明指标；代码里没有的能力标 `MISSING` / `PLANNED`。

---

## 0. 数据来源与访问方式

### 0.1 三类来源

| 来源 | 访问方式 | 作用域 | 权限 |
|---|---|---|---|
| Prometheus | `GET /metrics`（Prometheus 文本格式） | **全局聚合，无 `project_id` 标签** | 静态 Bearer：`DDP_METRICS_ACCESS_TOKEN`（≥32 字符）；来源网段白名单 `DDP_METRICS_ALLOWED_SOURCE_NETWORKS` |
| 业务 API | `GET /api/v1/...` | 按 `project_id` 作用域 | HS256 Bearer JWT；权限因端点而异（READ / `editor` / owner） |
| 只读 SQL | PG 只读连接 | 按 `project_id` | 平台管理员（运维报告/取证用） |

> `/metrics` 的指标定义与高基数防护见 `backend/app/core/metrics.py`；采集端点
> 在 `backend/app/api/observability.py`。非 local 环境未配令牌时 `/metrics`
> 返回 503（不是启动失败），抓取端表现为 `up=0`。

### 0.2 刷新频率基线

| 来源 | 基线频率 | 说明 |
|---|---|---|
| Prometheus 抓取 | **15s**（`metrics.py` 文档默认口径） | 业务类 gauge 在采集时查库（pull），不是应用主动计数 |
| API 看板 | **5min**（建议，运营频繁刷新无明显收益） | 逾期/队列类可 1min |
| 日结类看板 | **每日 01:00 UTC** | 成本、飞轮转化率、质量周报口径 |
| 事件级（回调响应） | **每批一次** | 回流 `accepted/duplicated/rate_limited` |

### 0.3 现有 Prometheus 指标全量清单（`backend/app/core/metrics.py`）

| 指标名 | 类型/标签 | 用途 |
|---|---|---|
| `ddp_http_requests_total` | counter，`method` / `path`(路由模板) / `status` | 端点吞吐与错误率 |
| `ddp_http_request_duration_seconds` | histogram，`method` / `path` | 端点延迟 p50/p95 |
| `ddp_ingestion_jobs` | gauge，`status` | 接入任务状态分布 |
| `ddp_episodes` | gauge，`status` | Episode 状态分布（10 态） |
| `ddp_data_source_imports` | gauge，`status` | 数据源导入状态分布（5 态） |
| `ddp_processing_runs` | gauge，`status` | 处理运行状态分布（7 态） |
| `ddp_quality_runs` | gauge，`status` | 质量运行结论分布（3 态） |
| `ddp_outbox_due_events` | gauge，无标签 | 到期未投递事件数 |
| `ddp_outbox_oldest_due_age_seconds` | gauge，无标签 | 最老到期事件等待秒数 |
| `ddp_outbox_failed_events` | gauge，无标签 | 死信事件数 |
| `ddp_data_source_import_last_success_timestamp_seconds` | gauge，无标签 | 最近成功导入时间（Unix 秒） |
| `ddp_data_source_imports_succeeded_24h` | gauge，无标签 | 24h 成功导入次数 |
| `ddp_raw_assets_object_missing` | gauge，无标签 | 对象缺失被隔离的原始资产数 |

**重要边界**：**没有**标注域（annotation）的 Prometheus 指标（标注吞吐/质量/成本/熔断均无
Prometheus 指标）。任何看板若需要标注域细粒度数据，只能走 §0.1 的 API 或只读 SQL，
禁止编造指标名。

---

## 1. 吞吐看板（Throughput）

**目标**：回答"平台/项目是否在稳定产出、卡在哪里"。

| 看板项 | 真实来源 | 计算口径 | 刷新 | 异常阈值（依据） |
|---|---|---|---|---|
| HTTP 端点吞吐 | `ddp_http_requests_total` | `sum by (path,status)(rate(...[5m]))`；提交吞吐用 `path="/api/v1/projects/{project_id}/annotation-submissions",method="POST",status="201"` | 15s | 5xx 占比 >1% 持续 5min → 告警（**运营建议**；SLO 未定稿，`phase4 §3 4-1` 仍为计划） |
| 端点延迟 p95 | `ddp_http_request_duration_seconds` | `histogram_quantile(0.95, sum by (le,path)(rate(..._bucket[5m])))` | 15s | **性能预算未建立**（`MISSING`，阶段 4-2 待做）；先看趋势不设硬阈值 |
| Episode 状态分布 | `ddp_episodes{status}` | 直接取 gauge，按 `status` 分族 | 15s | `FAILED` 类状态 >0 需排查（**运营建议**） |
| 接入/处理/质检运行 | `ddp_ingestion_jobs`、`ddp_processing_runs`、`ddp_quality_runs` | 直接取 gauge | 15s | 同上；`FAILED` >0 触发排查 |
| 导入 24h 吞吐 | `ddp_data_source_imports_succeeded_24h` | 直接取 gauge（滑动 24h） | 15s | 连续 2 个抓取点为 0 → 告警（**运营建议**） |
| 最近成功导入时间 | `ddp_data_source_import_last_success_timestamp_seconds` | `time() - value` 得距今天数 | 15s | >24h 未成功 → 告警（**建议**；从未成功时无样本，不是 0） |
| 投递队列积压 | `ddp_outbox_due_events`、`ddp_outbox_oldest_due_age_seconds`、`ddp_outbox_failed_events` | 直接取 gauge | 15s | `oldest_due_age > 900s` = 停滞（对齐代码 `_STALE_THRESHOLD_S=900`、`dispatch_queue.py:40`）；`failed > 0` 立即告警 |
| 队列健康快照（项目级） | `GET /api/v1/projects/{project_id}/ops/dispatch-queue` | `pending/published/failed/retrying/oldest_pending_age_seconds/stale/failures_by_event_type` | 1min | `stale=true` 或 `failed>0` → 告警；`failures_by_event_type` 定位首个坏下游 |
| 标注逾期 | `GET /api/v1/projects/{project_id}/annotation-overdue-summary` | `total_overdue`、`by_priority[]`、`by_status[]`、`buckets[]`（`LT_24H` / `D1_TO_D3` / `GT_3D`，分桶边界 `OVERDUE_BUCKET_HOURS=(24,72)`） | 5min | `total_overdue>0` 黄色；任一 `GT_3D>0` 红色（**建议**，桶边界是代码实值） |
| 标注员工作量 | `GET /api/v1/projects/{project_id}/annotation-workload?assignee_id=&period_start=&period_end=` | 周期必填；容量取 `DDP_ANNOTATION_CAPACITY_MINUTES`（未配置返回 `capacity_minutes=null`，不补 0） | 日 | 配置了容量且 `used > capacity` → 告警；未配置则只展示不判定 |
| 提交吞吐（项目级明细） | `GET /api/v1/projects/{project_id}/annotation-submissions?task_id=&status=` | 按 `task_id` 必填；`limit` 1..100，`cursor` 翻页 | 5min | 无固定阈值；配合 `GET .../annotation-assignments?assignee_id=` 看承接能力 |

---

## 2. 质量看板（Quality）

**目标**：回答"标得对不对、一致性够不够、有没有系统性坏题"。

| 看板项 | 真实来源 | 计算口径 | 刷新 | 异常阈值（依据） |
|---|---|---|---|---|
| IAA 一致性报告 | `GET /api/v1/projects/{project_id}/iaa-reports?task_id=`（`limit` 1..100） | 取报告 `metrics[]`：`PERCENT_AGREEMENT` / `COHEN_KAPPA` / `FLEISS_KAPPA` / `KRIPPENDORFF_ALPHA` / `SPAN_F1` / `BOUNDARY_MAE` / `GOLD_ACCURACY`；报告自带 `status`、`sample_item_count`、`overlap_item_count`、`annotator_count` | 任务完成后 / 日 | **代码无阈值**。运营建议：`COHEN_KAPPA` 或 `FLEISS_KAPPA` <0.6 预警、<0.4 停线复核；`GOLD_ACCURACY` <85% 与熔断同口径告警 |
| 生成 IAA 报告 | `POST /api/v1/projects/{project_id}/iaa-reports` | 写作用域；参数 `task_id`、`metric_definitions_version`、`iou_threshold`（默认 `DEFAULT_IOU_THRESHOLD=0.5`） | 按需 | 报告 `status` 非完成态 → 不得当质量结论使用 |
| 双标一致性 | `GET /api/v1/projects/{project_id}/annotation-tasks/{task_id}/consistency` | 只读结论，不创建仲裁、不自动通过 | 按需 | 出现 `PENDING_SECOND_ANNOTATOR` 表示尚无第二名标注者，不能判定一致性 |
| 黄金题准确率 | `POST /api/v1/projects/{project_id}/gold-sets/{gold_set_id}/evaluations`（返回 `accuracy`/`evaluated_count`/`passed_count`）；持久化表 `annotation_gold_set_evaluations` | `accuracy = passed_count / evaluated_count`；注意 `accuracy` 可为 `NULL`（样本不足） | 每次评估 | <85% 且连续 2 次 → 复核该标注员与黄金集；与熔断阈值同口径（`PAUSE_THRESHOLD=0.85`） |
| 质检抽检样本 | `GET /api/v1/projects/{project_id}/quality-samples?task_id=&status=`；动作 `POST .../quality-samples/{quality_sample_id}/actions/{action}`（`pass`/`fail`/`escalate`/`close`/`cancel`） | 样本 `outcome` ∈ `PASS`/`FAIL`/`ESCALATE`；抽检通过率 = `outcome=PASS` 样本 / 已关闭样本 | 日 | **代码无阈值**；建议 `outcome=FAIL` 率 >10% 触发批次复核 |
| 单提交质检运行 | `GET /api/v1/projects/{project_id}/annotation-submissions/{submission_id}/quality-checks` | 运行结果列表（`limit` 1..100） | 按需 | 无 |
| 熔断人数 | 表 `annotation_honeypot_state`（`state` + `paused_at`） | `count(*) group by state`；PAUSED 人员列表 | 日 | `PAUSED > 0` 关注；单日新增 ≥3 人 → 升级复核黄金集（**建议**） |
| 复核/仲裁留痕 | `GET /api/v1/projects/{project_id}/audit-logs`（owner） | 按 `resource_type`/`action`/`result`/`occurred_from`/`occurred_to` 过滤 | 日 | 无阈值；用于复盘四眼原则执行情况 |
| 数据集质检运行 | `ddp_quality_runs{status}` | 直接取 gauge（仅统计已产出结论的 `SUCCEEDED`；`FAILED`/`INCOMPLETE` 即失败与未完成） | 15s | `FAILED`/`INCOMPLETE` >0 → 排查（**建议**） |

> 质量结论的权威顺序：黄金题（有标准答案）> 双标一致性 > 抽检。IAA 报告是**证据**，
> 不是处置动作；任何解除/降级都要落审计留痕。

---

## 3. 成本看板（Cost）

**目标**：回答"LLM 调用花了多少、钱花在哪个模型/用途、配额是否触顶"。

| 看板项 | 真实来源 | 计算口径 | 刷新 | 异常阈值（依据） |
|---|---|---|---|---|
| Token 成本台账 | `GET /api/v1/projects/{project_id}/ops/token-ledger`（owner） | 按 `(model_id, purpose)` 聚合：`call_count`、`input_tokens`、`output_tokens`、`total_tokens`、`cost_estimate`（**USD 十进制字符串**，4 位小数）、`cost_estimated_tokens`；`total_cost_estimate` 为有价部分合计 | 1h / 日 | `total_cost_estimate = null` → 价格表未覆盖（`DDP_AI_TOKEN_PRICE_MAP`）告警；`entries` 为空 → 先查 `DDP_AI_TOKEN_USAGE_ENABLED` 是否为 `true`（默认 `false`） |
| 成本明细对账 | 表 `ai_token_usage`（`cost_estimate NUMERIC(14,4)`，`purpose` ∈ `PRE_LABEL`/`LLM_JUDGE`/`EMBED`，索引 `(project_id, occurred_at)`） | `SUM(cost_estimate) GROUP BY model_id, purpose` 与 API 对账；`cost_estimate IS NULL` 行数=未配价覆盖量 | 日 | 与 API 不一致 → 计量链路故障；NULL 行占比 >0 → 价格表漏配 |
| 配额使用 | `GET /api/v1/projects/{project_id}/usage-report` | 每条含 `used`/`limit`/`remaining`/`exceeded`/`status`/`measurement_source`/`measured_at`；`used=null` 表示尚无快照，不是 0 | 1h | `exceeded=true` 或 `status∈{AT_LIMIT, OVER_LIMIT}` → 告警；`status=UNMEASURED` → 查 `measurement_source`（无快照不是 0） |
| 配额清单 | `GET /api/v1/projects/{project_id}/quotas` | 各资源种类上限与版本 | 日 | 无 |
| AI 端点调用量 | `ddp_http_requests_total` | 按 `path` 过滤预标注/建议类端点，`rate[5m]` | 15s | 无固定阈值；与 token 台账交叉验证 |
| **ROI / 人工成本** | — | **`MISSING`**：配置层无时薪项（grep `config.py` 无 ROI/hrly 配置），token_ledger 只到成本 | — | 不可用；成本月报只能算「Token 支出」，不能算「投入产出比」 |

**成本口径红线**：

- `cost_estimate = null` 表示**该模型未配置价格**，不是免费；汇总时不能把 null 当 0；
- 未启用 `DDP_AI_TOKEN_USAGE_ENABLED` 时 `ai_token_usage` 无行，看板为空是**配置问题**，
  不能解读为"零成本"；
- 货币值经 JSON 双精度会丢精度，API 用字符串承载，看板展示不要二次 `float` 求和。

---

## 4. 数据飞轮看板（Data Flywheel）

**目标**：回答"评测失败有没有变成新数据、回流是否健康、下游是否真的消费"。

| 看板项 | 真实来源 | 计算口径 | 刷新 | 异常阈值（依据） |
|---|---|---|---|---|
| 回流受理结果 | `POST /api/v1/projects/{project_id}/annotation-backflow` 响应 | 每批 `accepted` / `duplicated` / `rate_limited` + `decisions[]` | 每批 | `duplicated/accepted > 1` → 回调侧重试风暴（**建议**）；`rate_limited>0` → 走 [backflow-intake.md](backflow-intake.md) §4 限流处置 |
| 回流案例池 | `GET /api/v1/projects/{project_id}/annotation-backflow?limit=`（owner；`limit` 1..200） | `items[].status` ∈ `RECEIVED`/`CONVERTED`；受理量=`items` 总数；**转化率 = `CONVERTED` / (`RECEIVED` + `CONVERTED`)** | 日 | `RECEIVED` 积压 >50 或最老 `RECEIVED` 超 7 天 → 告警（**建议**）；本版无事后转换端点，积压会持续增长，应推动"受理即带 conversion" |
| 案例明细 | 表 `annotation_backflow_cases`（唯一键 `(source, case_ref)`，迁移 0082 约束名 `uq_annotation_backflow_cases_source_case_ref`） | `count(*) group by source, status`；单 source 占比 | 日 | 单 source >80% → 评估评测平台集中度（**建议**） |
| 评测失败来源 | `GET /api/v1/projects/{project_id}/evaluations` | 评测记录列表（`status`/`verdict`/`limit`/`cursor`） | 日 | 失败/异常评测占比升高 → 回源头查模型能力维度 |
| 导出产物 | `GET /api/v1/projects/{project_id}/exports`、`GET .../exports/{export_job_id}/artifacts` | 导出任务状态；制品清单含哈希与行数（清单可复现性是硬要求） | 日 | 制品哈希不一致 → 阻断下游消费；导出失败率借 `ddp_http_requests_total{path="/api/v1/projects/{project_id}/exports",status=~"5.."}`（**建议**） |
| 训练交付 | `GET /api/v1/projects/{project_id}/training-deliveries` | 交付记录列表 | 日 | 无记录但已有导出 → 飞轮断在下游（**建议**告警） |
| 平台级产出口径 | `ddp_episodes{status}`、`ddp_processing_runs{status}` | 数据在平台内的流转分布 | 15s | 与导出口径交叉核对，防止"导出成功但源数据为空" |

**飞轮闭环判定**（阶段 4 验收要求「回流出→标注→导出」端到端跑通一次）：

1. 评测回调被受理（`accepted>0`）且转成任务（`task_id` 非空）；
2. 任务完成并产出提交（`annotation-submissions` 有记录）；
3. 导出制品清单可复现（`exports/{export_job_id}/artifacts` 哈希稳定）；
4. 训练侧有交付记录（`training-deliveries`）。
   任一环缺失即"飞轮未闭环"，需在周报标注缺口环节。

---

## 5. 告警规则落地优先级

| 优先级 | 规则 | 来源 | 处置 |
|---|---|---|---|
| P0 | `ddp_outbox_failed_events > 0` | Prometheus | 查死信事件类型；按 Worker 运维流程重投（HTTP 面不提供重投） |
| P0 | `ddp_outbox_oldest_due_age_seconds > 900` | Prometheus | 队列停滞；对照 `GET .../ops/dispatch-queue` 的 `stale` 定位 |
| P0 | `ddp_raw_assets_object_missing > 0` | Prometheus | 对象存储内容缺失，立即查 MinIO + 备份 |
| P1 | HTTP 5xx 占比 >1%（5min） | Prometheus | 定位 `path` 标签；**阈值待 SLO 定稿后收紧** |
| P1 | `total_overdue > 0` 且 `GT_3D > 0` | API | 排班调整 / 升级 owner |
| P1 | 黄金题 `accuracy < 0.85` 连续 2 次 | API + 表 | 复核黄金集与标注员，按熔断 SOP 处理 |
| P1 | `token-ledger.total_cost_estimate = null` 或空 `entries` | API | 查价格表 / 计量开关 |
| P2 | `rate_limited > 0` | 回流响应 | 限流削峰 |
| P2 | `PAUSED` 单日新增 ≥3 | 表 | 复核黄金集是否存在系统性问题 |

> 告警阈值凡标注「**建议**」者，均无代码/SLO 依据，需在阶段 4-2 建立性能与
> SLO 预算后回填；在此之前不得当作对外承诺。

---

## 6. 已知缺口（不得当作已具备）

| 缺口 | 证据 | 影响 |
|---|---|---|
| 无标注域 Prometheus 指标 | `backend/app/core/metrics.py` 指标常量清单 | 标注吞吐/质量/成本/熔断无法用 Prometheus 直接告警，只能轮询 API/SQL |
| 无性能预算表 | `phase4-production-plan.md` 4-2 仍为任务 | p95 阈值只能看趋势 |
| 无 ROI / 时薪口径 | 配置层无相关字段 | 成本看板只到 Token 支出 |
| `/metrics` 无 project 标签 | `metrics.py` 高基数防护设计（禁止 user/project 标签） | 全局看板与项目级看板必须分开做，不能跨项目下钻 |
| 回流无事后转换端点 | 路由层无 `convert` | 飞轮转化率依赖"受理时带 conversion"，否则 `RECEIVED` 只增不减 |

---

## 7. 验证命令（写文档时已执行）

```bash
grep -n "_GAUGE\|_TOTAL\|_SECONDS" backend/app/core/metrics.py | sed -n '1,40p'
grep -n "prefix=" backend/app/api/v1/ops_routes.py backend/app/api/v1/token_ledger_routes.py
grep -rn '"/iaa-reports"\|"/annotation-overdue-summary"\|"/annotation-workload"\|"/quality-samples"\|"/annotation-tasks/{task_id}/consistency"' backend/app/api/v1/
grep -n "honeypot_breaker_enabled\|ai_token_usage_enabled\|ai_token_price_map\|metrics_access_token\|metrics_allowed_source_networks" backend/app/core/config.py
grep -n "OVERDUE_BUCKET_HOURS" backend/app/domain/annotation/overdue_summary.py
grep -n "_STALE_THRESHOLD_S" backend/app/application/ops/dispatch_queue.py
```
