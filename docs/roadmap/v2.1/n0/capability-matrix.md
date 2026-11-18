# DDP V2.1 N0-02 Capability Matrix

- 日期：2026-11-18
- 基线提交：`7a9bb4b`
- 覆盖范围：V2.1 实施级 WBS 的 **N1–N9 共 47 个能力项**（N0 是过程项，不在此表）
- 状态口径（唯一）：
  - **VERIFIED**：有代码 + 自动化测试或真实运行证据，且契约/前端一致。
  - **PARTIAL**：主体能力可用，但存在本条列出的明确缺口。
  - **MISSING**：审计后确认不存在。
  - **BLOCKED**：受环境/外部依赖限制，不能验证。

## 0. 总览

| 状态 | 数量 |
|---|---:|
| VERIFIED | 21 |
| PARTIAL | 22 |
| MISSING | 3 |
| BLOCKED | 1 |
| **合计** | **47** |

> 读法：PARTIAL 不是「差不多完成」，而是「能用，但缺什么已写在缺口列」。
> 任何阶段要标记 DONE，必须把该阶段所有 PARTIAL 关闭或显式转为接受项。

## 1. N1 设备域与工程治理

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N1-01 | G-04 标定业务版本：内容实质变化才递增，锁版本独立 | **VERIFIED** | `domain/devices/calibration.py:350-361` `with_content_changes`；`calibration_version` 与 `version` 分离；`tests/unit/test_calibration_snapshot.py` | — |
| DDP-V21-N1-02 | G-05 Profile 快照：DRAFT 改 `profile_id` 后重写快照 | **VERIFIED** | `application/devices/calibrations.py:264-267`；`with_profile_snapshot`；快照三字段 | — |
| DDP-V21-N1-03 | G-08 Sensor Frame：`frame_id` / `frame_convention` 全链路 | **VERIFIED** | `application/devices/sensors.py:70-72,192-193`（写入 + PATCH 白名单）；`api/v1/schemas.py` | — |
| DDP-V21-N1-04 | G-09 状态语义字段统一 | **VERIFIED** | `schemas.py:342-373`（Device）、`:408-413`（Sensor）；契约 `schemas.stage1.yaml`；前端 `types/device.ts` | — |
| DDP-V21-N1-05 | G-11 标定审计（CREATE/PATCH/Outbox/actor/时间戳/request id） | **VERIFIED** | 创建/状态动作 + `application/devices/patch_audit.py`（devices/sensors/robots 的 PATCH 变更留痕，同事务）；`calibration_profiles` 按 ADR-0020 不可变、无 PATCH 入口；`test_devices_api.py` 等 4 例 | 无（calibration_profiles 的「改配置」= 登记新版本，create 已审计） |
| DDP-V21-N1-06 | G-16 `supersedes` 链：唯一、禁分叉、连续性 | **VERIFIED** | Profile 侧 `_validate_supersedes`；记录侧 `validate_supersedes_link`（存在/同项目/自引用/分叉）+ `find_successor` + 迁移 0053 部分唯一索引；领域/仓储/迁移共 16 例 | 长度 ≥2 的环（A→B→A）未检测（已知限制，见 Backlog N1-009） |
| DDP-V21-N1-07 | G-19/G-20 统一错误体与字段路径 | **PARTIAL** | `main.py:87-97` `{"error": {code,message,request_id,details}}`；`errors.py:113` `details_codes` 白名单（G-19） | 字段名为 `code` 而非任务书的 `error_code`；无 `field_path` / `trace_id` 独立字段；错误码差异 61 项仍在 parity 基线内 |
| DDP-V21-N1-08 | 横切治理：Outbox / 幂等 / 乐观锁 / 错误映射 / 审计统一 | **PARTIAL** | `application/{audit,idempotency}.py`、`preconditions.py` 已收敛 | outbox 事件归类盲区（f-string）；无通用投递器；设备域 f-string 事件（TD-13） |

## 2. N2 数据平面产品化

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N2-01 | 数据源中心（HF/URL/本地/S3） | **PARTIAL** | N2-003：登记可显式 `verify_remote` 调用 `HuggingFaceAdapter.describe()`（默认关闭、超时 10s）+ 详情页/详情 API | 仍只支持 Hugging Face；URL/本地/S3 源未实现 |
| DDP-V21-N2-02 | 导入中心（状态机、retry、cancel、progress、error、logs） | **VERIFIED** | 状态机 + Worker + API + 列表页；阶段/进度/步骤日志（cursor 分页）；PENDING/RUNNING 时 7s 自动轮询、终态停止；N2-010 原地合并刷新（TD-29 闭环）；N2-006 导入审看页展示统一质量六类结论与下钻 | 手动刷新仍全量重置；筛选切换需刷新才重取（TD-32）；质量运行无按对象列举接口（N4-008） |
| DDP-V21-N2-03 | Raw Asset 字段、状态机、动作、去重 | **VERIFIED** | `domain/ingestion/models.py:332`；`state_machines.py:65`；`uploads.py:234-267`；`test_raw_asset_actions_api.py`；`scripts/qa/raw_asset_lifecycle_smoke.py` | — |
| DDP-V21-N2-04 | Episode Explorer（时间/任务/机器人/相机/质量/状态） | **VERIFIED** | N2-004：camera 过滤（`EXISTS` 下推）+ 前端质量/标注/机器人/机位/时间筛选，全部写入 URL；契约补齐 11 个 query 参数 | `EpisodeListPage.tsx` 729 行，后续评估拆分 |
| DDP-V21-N2-05 | 人工审看（播放/倍速/Range/时间轴/导航/多视角/decision+reason+evidence） | **PARTIAL** | N2-005：倍速/时间轴/2 机位同屏/结构化 evidence；预签名 URL + 206 Range 已实测 | 审看绑定导入任务，无 Episode 级导航；多机位为独立播放（不保证音画同步） |
| DDP-V21-N2-06 | 六类质量报告 UI（Integrity/Temporal/Visual/Metadata/Annotation/Overall） | **MISSING** | 现有为扁平 7 条 rule findings；`application/datasources/quality.py` | 六类分层不存在；不可点击下钻 |

## 3. N3 Canonical + Adapter

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N3-01 | Canonical 实体 12 类 | **PARTIAL** | N3-001：`data-platform/ddp_data/canonical/` 定义 Frame / VideoStream / SensorStream / Action / Observation / Episode（`ddp.canonical/1.0.0`，只读契约 + LeRobot 投影） | 统一 QualityResult 仍无跨域实体；Frame/Stream 尚无生产者与持久化（ADR-0035 有意不建表） |
| DDP-V21-N3-02 | Adapter 接口与注册发现 | **PARTIAL** | `data-platform/ddp_data/adapters/registry.py:76-162`；`builtin.py:226,252`；`adapter_resolver.py:32` | `AdapterSpec` 只有 detect/parse；fetch/verify/write 散在 `import_runner.py` 等，不在接口内 |
| DDP-V21-N3-03 | 多格式 Adapter 优先级（LeRobot/Parquet/MP4/MCAP/ROS/HDF5） | **PARTIAL** | LeRobot preview：`lerobot/parser.py:104`；JSONL：`builtin.py` | 完整 LeRobot Parquet、Parquet、MP4 metadata、MCAP、ROS Bag、HDF5 均无字段级解析；仅魔数识别 |

## 4. N4 Processing Pipeline + 质量中心

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N4-01 | Processing Run 实体与状态机（CREATED/QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/RETRYING） | **VERIFIED** | N4-001/002/005：七态状态机、迁移 0056/0057、`processing.run.requested` 投递与 `processing-worker` 消费、阶段步骤表与 `GET /steps`；FILE_VALIDATION 与 QUALITY_PRE_CHECK 真实执行，其余六阶段显式 SKIPPED | 解析/切片/清洗处理器属后续增量；前端 `/processing` 页面仍 PLANNED |
| DDP-V21-N4-02 | 时间同步（timestamp/frame index/offset/drift） | **PARTIAL** | `domain/episodes/sync_drift.py:110`；导入质量 `stream_sync_aligned` | 只检测 start/duration 偏移；无帧索引、无校正、drift 不落库 |
| DDP-V21-N4-03 | Episode 切分（metadata/time range/manual/task boundary） | **PARTIAL** | `domain/episodes/segmentation_rules.py:235,317,409`；`episode_materialization.py:131` | 只有 MANUAL_TIME_RANGE / fixed-window / episode-boundary；无 task-boundary、无帧级切片 |
| DDP-V21-N4-04 | 质量引擎（checksum/decode/fps/resolution/duration/timestamp/missing frame/metadata/annotation） | **PARTIAL** | N4-004 + N4-005：统一规则注册表 + 六类分层 + 规则集内容 SHA + `quality_runs/quality_findings`（迁移 0058）+ 3 个 API；单 Episode/导入任务可手工触发；处理流水线 QUALITY 阶段逐 Episode 自动质检（单次上限 200，结果可回查） | decode/resolution/missing-frame 仍无规则；质量结论尚未接入下游门禁（N4-006）；数据集规则源入口未开放 |

## 5. N5 数据资产与 Dataset 生命周期

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N5-01 | Asset Catalog 跨资源检索 | **PARTIAL** | 各资源列表 API（raw-assets / episodes / datasets / annotation tasks） | 无统一目录与联合检索；Annotation/Quality 维度未接入 |
| DDP-V21-N5-02 | Dataset Version 生命周期（DRAFT→…→FROZEN→DEPRECATED） | **VERIFIED** | `domain/datasets/{enums,state_machines,models}.py`；`validity_status` 子轴；冻结后 DB 触发器不可变；`test_dataset_validity_api.py` | 实际用 `SUPERSEDED` 表达弃用，与任务书 `DEPRECATED` 命名不同（语义等价，需在契约说明） |
| DDP-V21-N5-03 | Manifest 字段完整性与固定 | **VERIFIED** | `domain/datasets/manifest.py:33-153`；`test_dataset_domain.py:442` | files 以 raw_asset_ids + item_sha256 表达，无独立文件清单（可接受） |
| DDP-V21-N5-04 | Lineage 全链 | **VERIFIED** | N5-002/005/006：Episode→Annotation、Episode→Quality、DatasetVersion→TRAINING_RUN（`TRAINED_ON`，ADR-0036）全部同事务写边、幂等；迁移 0059；存量引用可用 `app.workers.lineage_backfill` 默认 dry-run、显式 `--apply` 回填 | 无通用图查询 API（逐节点遍历） |

## 6. N6 标注治理

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N6-01 | Schema Registry（action/skill/language/contact/object/failure/temporal） | **PARTIAL** | `domain/annotation/schema.py:226`；10 种 label type / 6 层 level | 无七类预设注册表；项目自建；种子仅 4 个标签 |
| DDP-V21-N6-02 | 标注任务（batch/assignment/priority/SLA/reviewer/status） | **VERIFIED** | N6-002：纯函数 `is_overdue` + 列表 `overdue` 下推过滤 + 派生字段 `is_overdue` + 巡检计数 + 前端逾期标签/汇总 | 无自动改派/关闭（刻意）；全局逾期卡片需独立汇总端点 |
| DDP-V21-N6-03 | AI 辅助标注（suggestion/confidence/human confirmation） | **VERIFIED** | `ai_suggestion.py:113-235`；`annotation_ai.py`；AI 值单独存储、`AI_ASSISTED` 标记、人工确认强一致 | — |
| DDP-V21-N6-04 | QA：抽检/盲审/双标/IAA/仲裁 | **PARTIAL** | N6-003 + N6-006：双标策略 + 一致性判定复用 IAA + 不达标自动仲裁；创建页提供双标表单（阈值/auto_approve 默认关闭、二次确认、按角色展示，服务端仍权威） | 普通复核仍非盲审 |
| DDP-V21-N6-05 | 返工闭环 Reject→Reason→Rework→Review→Pass | **VERIFIED** | `state_rules.py:159-162,284-295`；`review.py:241`；`submissions.py:113` 轮次上限；API 用例 | — |

## 7. N7 Training / Evaluation / Data Gap

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N7-01 | Training Dataset Grant | **VERIFIED** | `domain/datasets/dataset_grants.py:48`；`dataset_grant_routes.py`；`test_dataset_grant_api.py` | 无独立 requester/purpose（`created_by` + reason 兼任） |
| DDP-V21-N7-02 | Training Adapter（Local/Mock/Dry Run） | **PARTIAL** | `ExecutionMode`（LOCAL/MOCK/DRY_RUN/REMOTE/UNKNOWN）+ 迁移 0054 + 部署声明 `DDP_TRAINING_ADAPTER_EXECUTION_MODES` + 前端显著标注；`local-adapter` 真实执行 | MOCK/DRY_RUN 目前是**显式标记**而非独立执行器；未配置模式时提交 422（fail-closed，刻意） |
| DDP-V21-N7-03 | Training Run 记录 | **PARTIAL** | `domain/datasets/training_references.py:114`；`training_delivery_routes.py` | 无独立 TrainingRun；config 不落库；无 code_version/model_version；无 worker 轮询 |
| DDP-V21-N7-04 | Evaluation（model/dataset/config/metrics/artifacts/failure cases） | **VERIFIED** | N7-003：`FailureCase` 领域值对象 + 迁移 0061 + 回调校验（类别/重复/4KiB/200 条/64KiB）+ 响应与前端详情展示；成功评测后不可变 | 列表响应可能较大（每评测 ≤64KiB），后续可加独立明细端点 |
| DDP-V21-N7-05 | Data Gap（多来源、target_count、priority、status） | **PARTIAL** | N7-004 + N7-008：五类来源证据、`unmapped`（含原因）与 `truncated_sources`；证据不造维度、不参与覆盖口径；前端展示来源徽标、未映射与截断；契约 1.14.0 | 低分评测只能作为项目级未映射证据，不能归因到具体维度；缺口项仍无 priority/status |
| DDP-V21-N7-06 | 闭环 Evaluation→Failure→Gap→Requirement→New Data | **PARTIAL** | `application/modelops/feedback.py:221,291`；`requirements.py:318`；e2e 主链 | 每步手工触发；无自动「失败→缺口」；新数据回流未验证 |

## 8. N8 可观测性 / 性能 / 安全 / 部署

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N8-01 | Metrics（延迟/错误/队列/导入吞吐/处理时长/质量失败/存储） | **PARTIAL** | `core/metrics.py`；`GET /metrics`（Prometheus 文本）；`test_metrics_api.py` | 有 HTTP/ingestion/episode/进程指标；缺队列积压、导入吞吐、处理时长、质量失败、存储 |
| DDP-V21-N8-02 | Trace（Request→Application→DB→Worker→Storage） | **PARTIAL** | `api/middleware/request_id.py`；audit `request_id`；outbox `request_id`；N8-003 Worker 从事件 payload 恢复 trace，缺失/非法时生成并标注 `generated`，上下文按 token 复位 | 无 traceparent/OTel；历史存量事件无 request_id，只能生成本地 trace；日志外的步骤表无 trace 列 |
| DDP-V21-N8-03 | 性能基线（100/1K/10K episodes、并发导入/审核、Video Range） | **MISSING** | 无压测脚本与记录 | 全部缺失 |
| DDP-V21-N8-04 | Backup / Restore | **VERIFIED** | `scripts/ops/backup-restore-drill.sh`；`docs/operations/备份与恢复演练记录-2026-11-17.md`（PG+MinIO 全量、抽样 sha256 一致） | 仅本机、无加密/异地/RTO |
| DDP-V21-N8-05 | Staging 发布链 | **BLOCKED** | CI 无 deploy job；`scripts/deploy-laptop.sh` 仅本机 | 主机不可解析（RK-01） |
| DDP-V21-N8-06 | Security（JWT/RBAC/scope/signed URL/SSRF/path traversal/secret） | **VERIFIED** | N8-001 + N8-008：训练与模型网关共用 `adapter_url_guard.py`，两个独立 allow_private 开关默认 false；JWT/RBAC/scope/signed URL/path traversal/secret 均有用例 | 限流 Redis 不可用时 fail-open（ADR-0031 已接受） |

## 9. N9 真实机器人 / 规模化

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N9-01 | Robot Registry（model/serial/firmware/controller/sensor/profile） | **PARTIAL** | `domain/devices/models.py:72-91`；`robots.py`；`test_robots_api.py` | 无 controller 字段；传感器独立实体 |
| DDP-V21-N9-02 | Capture Plan（task/scene/robot/sensor/operator/SOP/target/threshold） | **PARTIAL** | `domain/collection/assets.py:68` CollectionPlan；采集任务/批次 | Plan 是项目级可复用方案，无逐设备执行态；缺 target episode 与质量阈值绑定 |
| DDP-V21-N9-03 | Capture Session（Plan→Session→Raw→Upload→Validation→Episode） | **MISSING** | 无会话实体 | 全缺 |
| DDP-V21-N9-04 | 真实设备格式 Adapter（LeRobot/ROS Bag/MCAP/HDF5） | **PARTIAL** | 格式识别 + 魔数（`domain/ingestion/enums.py:85`）；LeRobot preview 解析 | 无字段级解析；无真实设备数据验证（BLOCKED：无硬件） |
| DDP-V21-N9-05 | 项目隔离与多基地/供应商 | **VERIFIED** | `locations`、`suppliers`、项目成员、跨项目隔离测试；N9-005 新增 `GET /projects/{id}/sites/overview` 按场地只读聚合设备/传感器/采集任务 | 无跨基地调度与供应商计费；仅挂机器人的传感器不归因到场地 |

## 10. 状态变更规则

1. PARTIAL → VERIFIED 必须有：新增测试或真实运行证据 + 契约/前端同步 + 本文档更新。
2. MISSING 项开工前必须补 ADR（涉及新实体/新状态机）与契约版本。
3. BLOCKED 项不得用 Mock 结果升级状态；解除阻塞后重新验证。
4. 本表是 N1–N9 排期的唯一状态来源；PR 描述里的 Capability 段必须引用 Task ID。

