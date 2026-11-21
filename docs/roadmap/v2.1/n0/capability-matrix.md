# DDP V2.1 N0-02 Capability Matrix

- 日期：2026-11-18
- 基线提交：`7a9bb4b`
- 覆盖范围：V2.1 实施级 WBS 的 **N1–N9 共 48 个能力项**（新增 N8-07 前端 E2E；N0 是过程项，不在此表）
- 状态口径（唯一）：
  - **VERIFIED**：有代码 + 自动化测试或真实运行证据，且契约/前端一致。
  - **PARTIAL**：主体能力可用，但存在本条列出的明确缺口。
  - **MISSING**：审计后确认不存在。
  - **BLOCKED**：受环境/外部依赖限制，不能验证。

## 0. 总览

| 状态 | 数量 |
|---|---:|
| VERIFIED | 24 |
| PARTIAL | 22 |
| MISSING | 1 |
| BLOCKED | 1 |
| **合计** | **48** |

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
| DDP-V21-N2-01 | 数据源中心（HF/URL/本地/S3） | **PARTIAL** | N2-003：登记可显式 `verify_remote` 调用 `HuggingFaceAdapter.describe()`（默认关闭、超时 10s、有界重试上限可配 1–3）+ 详情页/详情 API；失败返回结构化 `details.remote_check`（7 类稳定原因：超时/404/401/5xx/非法响应/超大响应/不可用），远端错误文本经 `sanitize_log_message` 脱敏，白名单字段 + 长度上界；前端详情页覆盖 Loading/Empty/Error/Forbidden/Partial/Stale | 仍只支持 Hugging Face；URL/本地/S3 源未实现；真实 HF 联网验收 `NOT RUN`（默认跳过） |
| DDP-V21-N2-02 | 导入中心（状态机、retry、cancel、progress、error、logs） | **VERIFIED** | 状态机 + Worker + API + 列表页；阶段/进度/步骤日志（cursor 分页）；PENDING/RUNNING 时 7s 自动轮询、终态停止；N2-010 原地合并刷新（TD-29 闭环）；N2-006 导入审看页展示统一质量六类结论与下钻；N4-008 按对象列举可自动发现最近运行 | 手动刷新仍全量重置；筛选切换需刷新才重取（TD-32） |
| DDP-V21-N2-03 | Raw Asset 字段、状态机、动作、去重 | **VERIFIED** | `domain/ingestion/models.py:332`；`state_machines.py:65`；`uploads.py:234-267`；`test_raw_asset_actions_api.py`；`scripts/qa/raw_asset_lifecycle_smoke.py` | — |
| DDP-V21-N2-04 | Episode Explorer（时间/任务/机器人/相机/质量/状态） | **VERIFIED** | N2-004：camera 过滤（`EXISTS` 下推）+ 前端质量/标注/机器人/机位/时间筛选，全部写入 URL；契约补齐 11 个 query 参数 | `EpisodeListPage.tsx` 729 行，后续评估拆分 |
| DDP-V21-N2-05 | 人工审看（播放/倍速/Range/时间轴/导航/多视角/decision+reason+evidence） | **PARTIAL** | N2-005：倍速/时间轴/2 机位同屏/结构化 evidence；预签名 URL + 206 Range 已实测 | 审看绑定导入任务，无 Episode 级导航；多机位为独立播放（不保证音画同步） |
| DDP-V21-N2-06 | 六类质量报告 UI（Integrity/Temporal/Visual/Metadata/Annotation/Overall） | **VERIFIED** | N2-006/N4-008：`QualityRunReport` 展示服务端六类摘要与整体结论，`ImportQualityRunSection` 自动发现最近运行并支持 URL 深链；findings 游标分页、类别服务端筛选、严重度已加载范围筛选及媒体资产下钻；严格区分 INCOMPLETE / NOT_APPLICABLE / FAILED；覆盖 Loading/Empty/Error/Forbidden/Partial/Stale/Disabled；2026-11-21 专项 6 files / 74 tests passed | 历史扁平导入报告仅保留为对照，不作为六类权威结论 |

## 3. N3 Canonical + Adapter

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N3-01 | Canonical 实体 12 类 | **PARTIAL** | N3-001：`data-platform/ddp_data/canonical/` 定义 Frame / VideoStream / SensorStream / Action / Observation / Episode（`ddp.canonical/1.0.0`，只读契约 + LeRobot 投影） | 统一 QualityResult 仍无跨域实体；Frame/Stream 尚无生产者与持久化（ADR-0035 有意不建表） |
| DDP-V21-N3-02 | Adapter 接口与注册发现 | **VERIFIED** | N3-002：`AdapterSpec` 新增可选 `fetch/verify/write`，能力集由实现推导，未实现显式抛 `AdapterCapabilityError`；旧 Adapter 零改动；注册表可按能力检索 | 内置 Adapter 暂只声明 parse/detect；导入链路尚未接线新能力（后续增量） |
| DDP-V21-N3-03 | 多格式 Adapter 优先级（LeRobot/Parquet/MP4/MCAP/ROS/HDF5） | **PARTIAL** | N3-003/N3-004/N3-005/N3-006/N3-007：官方 `pyarrow==23.0.1` 读 Parquet footer；受控 ffprobe 探测 MP4；官方 `mcap==1.6.0` 只读 MCAP header/summary；官方 `h5py==3.16.0` 只读 HDF5 结构；官方 `rosbags==0.11.9` 只读 rosbag1/rosbag2 索引与 metadata（topic/msgtype/消息数/时间范围），rosbag2 metadata 字节与 topic/消息数/文本均有界，file 级压缩显式拒绝，超限显式失败、错误文本脱敏且不注册 AdapterRegistry；真实公开样例 RoboMimic v1.5（HF 固定 revision，36.7 MB）6008 数据集/1802 组，以及 foxglove/mcap 固定 revision ROS1 bag（2 topics/3 messages）均经 SHA256 校验并探测通过；LeRobot preview + JSONL 既有 | Parquet 仅 footer、不读数据页；Parquet/MP4/MCAP/HDF5/ROS Bag 均未接入导入链路（N3-008/N3-009）；ROS Bag 仅索引/metadata，不解码消息体字段；HDF5 不读取数据集值，故均不构成观测/动作语义验证；真实机器人数据集端到端仍 BLOCKED |

## 4. N4 Processing Pipeline + 质量中心

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N4-01 | Processing Run 实体与状态机（CREATED/QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/RETRYING） | **VERIFIED** | N4-001/002/005/006/012/013/014：七态状态机、迁移 0056/0057/0065、投递与消费、阶段步骤表与 `GET /steps`；**FILE_VALIDATION / METADATA_EXTRACTION / FORMAT_PARSING / TIME_ALIGNMENT / CALIBRATION_VALIDATION / EPISODE_SEGMENTATION / QUALITY_PRE_CHECK 七个阶段真实执行**（分段阶段只检查既有 Episode 冻结规则版本是否可由权威注册表追溯，明确未执行重新切片）；create/cancel/fail/retry/system-close 全审计，永久失败/死信自动 `closed_at` 收口且保留错误 | DATA_CLEANING 仍显式 SKIPPED；Episode 未冻结规则参数，手工/历史标签只能标记“不可重放”；前端 `/processing` 页面仍 PLANNED |
| DDP-V21-N4-02 | 时间同步（timestamp/frame index/offset/drift） | **PARTIAL / PRODUCER VERIFIED** | N4-003/011/012：时钟域一致性、精确帧索引、offset/drift 与 UNKNOWN 语义；严格契约与 LeRobot v3 producer 只读取逐视频显式 `from_timestamp` / `to_timestamp`；固定 DROID-100 revision 的 100 个 episode、三路视频经 producer → parser → Pipeline handler 验证均为 ALIGNED，报告版本为 `ddp.processing.time-alignment-report/1.0.0` | 通用 MP4/MCAP/Parquet 摘要仍不得推断分组、绝对起点或时钟；来源未声明物理时钟，故保持 UNKNOWN；校正始终 `applied=false` |
| DDP-V21-N4-03 | Episode 切分（metadata/time range/manual/task boundary） | **PARTIAL** | N4-014：Processing 阶段复用 Episode 关联解析、项目隔离 Repository 与 `SegmentationRuleRegistry`，有界统计注册规则“可追溯”和手工/历史标签“不可重放”，越权/不存在 fail-closed；既有 `fixed-window` / `episode-boundary` / `MANUAL_TIME_RANGE` 创建链路保持不变 | Episode 未持久化规则参数，故不能完整重放；本阶段不创建或重新切片 Episode；仍无 task-boundary、无帧级切片 |
| DDP-V21-N4-04 | 质量引擎（checksum/decode/fps/resolution/duration/timestamp/missing frame/metadata/annotation） | **PARTIAL** | N4-004/005/007/008：统一规则注册表 + 六类分层 + 规则集内容 SHA + `quality_runs/quality_findings` + 创建/详情/findings/按对象列举；处理流水线 QUALITY 阶段逐 Episode 自动质检；数据集构造/导出按 `min_quality_score` 读取最新结论（无运行/INCOMPLETE/低分阻断） | decode/resolution/missing-frame 仍无规则；数据集规则源入口未开放；门禁阈值暂存 `selection_filters`（非独立契约字段） |

## 5. N5 数据资产与 Dataset 生命周期

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N5-01 | Asset Catalog 跨资源检索 | **PARTIAL** | N5-001：`GET /projects/{id}/assets/search` 跨 RAW_ASSET/EPISODE/DATASET/DATASET_VERSION/MEDIA_ASSET 联合检索；前缀索引下推、项目隔离、复合游标；契约 1.18.0 | 仅匹配既有标识前缀（code/episode_key），不匹配名称/文件名；Annotation/Quality 维度未接入；PG EXPLAIN 未验收 |
| DDP-V21-N5-02 | Dataset Version 生命周期（DRAFT→…→FROZEN→DEPRECATED） | **VERIFIED** | `domain/datasets/{enums,state_machines,models}.py`；`validity_status` 子轴；N5-004 + N5-007 巡检对 manifest 与 `raw_assets.object_key` 双层探测，明确缺失写 UNKNOWN；冻结后 DB 触发器不可变；导出/交付/冻结对非 VALID fail-closed | 实际用 `SUPERSEDED` 表达弃用，与任务书 `DEPRECATED` 命名不同；对象上限口径为 manifest+raw asset 总数，大版本会整轮跳过 |
| DDP-V21-N5-03 | Manifest 字段完整性与固定 | **VERIFIED** | `domain/datasets/manifest.py:33-153`；`test_dataset_domain.py:442` | files 以 raw_asset_ids + item_sha256 表达，无独立文件清单（可接受） |
| DDP-V21-N5-04 | Lineage 全链 | **VERIFIED** | N5-002/005/006 + N5-003：Episode→Annotation、Episode→Quality、DatasetVersion→TRAINING_RUN（`TRAINED_ON`，ADR-0036）全部同事务写边、幂等；迁移 0059；存量引用可用 `app.workers.lineage_backfill` 默认 dry-run、显式 `--apply` 回填；`GET /projects/{id}/lineage/graph` 受控深度（≤3）/方向/节点 500/边 1000 上限、BFS 防环、按边游标；契约 1.21.0 | 图查询不含 `DATA_SOURCE` 节点类型；Episode 级自动质检与经营成本核算仍 MISSING（全局口径） |

## 6. N6 标注治理

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N6-01 | Schema Registry（action/skill/language/contact/object/failure/temporal） | **PARTIAL** | N6-001：7 类代码级预设（action/segment/object/attributes/quality/scene/robot-state）+ 只读目录端点；模板经既有 `AnnotationSchema` 完整校验；10 种 label type / 6 层 level | 前端尚未接入预设选择（后续 UI）；预设是静态代码资产，不支持项目级注册 |
| DDP-V21-N6-02 | 标注任务（batch/assignment/priority/SLA/reviewer/status） | **VERIFIED** | N6-002 + N6-005：纯函数 `is_overdue` + 列表 `overdue` 下推过滤 + 派生字段 `is_overdue` + 巡检计数 + 前端逾期标签/汇总；巡检产出 `annotation.task.overdue` 事件，按 `<task_id>:<due_at>` 窗口去重、uuid5 确定性事件 ID，重复巡检不重复投递；收尾新增只读端点 `GET /projects/{id}/annotation-overdue-summary`（总数、优先级/状态分布、最早期逾期时间、SLA 分桶 `<24h` / `24–72h` / `≥72h`，阈值来自领域常量 `OVERDUE_BUCKET_HOURS`）与前端逾期汇总卡片（Loading/Empty/Error/Forbidden/Stale） | 无自动改派/关闭（刻意）；提醒仅落事件与审计，站内信/邮件通道未接入 |
| DDP-V21-N6-03 | AI 辅助标注（suggestion/confidence/human confirmation） | **VERIFIED** | `ai_suggestion.py:113-235`；`annotation_ai.py`；AI 值单独存储、`AI_ASSISTED` 标记、人工确认强一致 | — |
| DDP-V21-N6-04 | QA：抽检/盲审/双标/IAA/仲裁 | **PARTIAL** | N6-003/004/006：双标策略 + 一致性判定复用 IAA + 不达标自动仲裁；`blind_review` 普通复核盲审（服务端对非标注人脱敏，含 Hash/AI 人工值）；创建页双标表单 | 盲审对所有非标注人生效（仲裁人豁免未定义）；聚合统计仍有小样本推断风险 |
| DDP-V21-N6-05 | 返工闭环 Reject→Reason→Rework→Review→Pass | **VERIFIED** | `state_rules.py:159-162,284-295`；`review.py:241`；`submissions.py:113` 轮次上限；API 用例 | — |

## 7. N7 Training / Evaluation / Data Gap

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N7-01 | Training Dataset Grant | **VERIFIED** | `domain/datasets/dataset_grants.py:48`；`dataset_grant_routes.py`；`test_dataset_grant_api.py` | 无独立 requester/purpose（`created_by` + reason 兼任） |
| DDP-V21-N7-02 | Training Adapter（Local/Mock/Dry Run） | **PARTIAL** | `ExecutionMode`（LOCAL/MOCK/DRY_RUN/REMOTE/UNKNOWN）+ 迁移 0054 + 部署声明 `DDP_TRAINING_ADAPTER_EXECUTION_MODES` + 前端显著标注；`local-adapter` 真实执行；N7-007：MOCK / DRY_RUN 成为**独立执行器**——DRY_RUN 只做校验与计划（迁移 0067 的 `dry_run_plan`、终态 `DRY_RUN`），不调外部平台、不产生训练产物；MOCK 产物带 `mock=true` 且无 model object_key，模型版本登记入口拒绝非真实引用；未配置模式提交 422（fail-closed，刻意） | MOCK/DRY_RUN 仅覆盖提交链路，未接入外部回调与轮询；存量 MOCK+SUCCEEDED 记录保持可读（未强制标注）；前端已把 `external_status=DRY_RUN` 展示为「演练（未训练）」并用橙色与成功区分（`datasetPresentation.ts` + 单测）；MOCK/DRY_RUN 与真实训练的视觉隔离仍依赖既有 execution_mode 标注 |
| DDP-V21-N7-03 | Training Run 记录 | **PARTIAL** | N7-002 + 迁移 0063：既有外部引用上新增 `config_summary`（≤8KiB、脱敏）、`code_version`、`model_version`；存量保持 NULL 不回填；契约 1.18.0 | 无独立 TrainingRun；状态轮询未实现（按需 sync/cancel 仍为权威入口）；DB 层未把三列纳入不可变守卫 |
| DDP-V21-N7-04 | Evaluation（model/dataset/config/metrics/artifacts/failure cases） | **VERIFIED** | N7-003：`FailureCase` 领域值对象 + 迁移 0061 + 回调校验（类别/重复/4KiB/200 条/64KiB）+ 响应与前端详情展示；成功评测后不可变 | 列表响应可能较大（每评测 ≤64KiB），后续可加独立明细端点 |
| DDP-V21-N7-05 | Data Gap（多来源、target_count、priority、status） | **PARTIAL** | N7-004 + N7-008：五类来源证据、`unmapped`（含原因）与 `truncated_sources`；证据不造维度、不参与覆盖口径；前端展示来源徽标、未映射与截断；契约 1.14.0 | 低分评测只能作为项目级未映射证据，不能归因到具体维度；缺口项仍无 priority/status |
| DDP-V21-N7-06 | 闭环 Evaluation→Failure→Gap→Requirement→New Data | **VERIFIED** | `application/modelops/feedback.py:221,291`；`requirements.py:318`；N7-005 候选缺口；N7-006：新数据回流闭环 e2e（真实 PG + 真实导入 Worker）——反馈转需求 → 审批 → 新采集（第二批数据源，新 Episode ACCEPTED/APPROVED）→ 新数据集版本冻结 → 训练交付 → 训练引用（MOCK 显式标注且登记模型版本被 422 拒绝；LOCAL 经真实 `sync` 到 SUCCEEDED）→ 新模型版本 READY → 新版本复评 PASS → satisfy/link-retest/close + 反馈 resolve，缺口视图 `open_feedback_count=0`、`current_count=20`、`suggested=0`；证据 `backend/tests/e2e/test_data_reflow_e2e.py`、`docs/testing/主链端到端与可追溯性验收-2026-11-17.md` §13；契约 1.23.0（未新增字段） | 候选缺口转需求仍人工（刻意，不自动审批）；真实训练平台计算与真实权重 `BLOCKED`（N7-02），本项只证明编排闭环 |

## 8. N8 可观测性 / 性能 / 安全 / 部署

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N8-01 | Metrics（延迟/错误/队列/导入吞吐/处理时长/质量失败/存储） | **PARTIAL** | `core/metrics.py`；`GET /metrics`；N8-004 增加 outbox 积压/最老等待/死信、导入最近成功与 24h 吞吐、处理/质量状态分布、对象缺失；按组超时降级 | 处理时长仍缺（无全局时间索引）；状态表缺 `status` 前缀索引（TD-11）；PG 运行时路径仅编译级验证 |
| DDP-V21-N8-02 | Trace（Request→Application→DB→Worker→Storage） | **PARTIAL** | `api/middleware/request_id.py`；audit `request_id`；outbox `request_id`；N8-003 Worker 从事件 payload 恢复 trace，缺失/非法时生成并标注 `generated`，上下文按 token 复位 | 无 traceparent/OTel；历史存量事件无 request_id，只能生成本地 trace；日志外的步骤表无 trace 列 |
| DDP-V21-N8-03 | 性能基线（100/1K/10K episodes、并发导入/审核、Video Range） | **PARTIAL** | N8-002：`scripts/perf/` 默认 dry-run，`--apply` 才在本机白名单测试库建 `ddp_perf_*` 临时库并跑完整迁移；100/1K/10K × 并发 1/4/8 的只读分页、`quality_status` 过滤、视频预签名与真实 MinIO 64KiB Range（校验 206）；输出 p50/p95/p99/吞吐/错误率 + 硬件/PG/MinIO/Git/负载 + 阈值 SHA256；报告 `docs/operations/性能基线-DDP-V21-N8-002-2026-11-19.md` | 只读场景；并发写入、Worker 吞吐、队列积压、长稳内存未测（PLANNED）；staging/生产基线 BLOCKED；阈值仅本机 advisory，需同机同参取中位数 |
| DDP-V21-N8-04 | Backup / Restore | **VERIFIED** | `scripts/ops/backup-restore-drill.sh`；`docs/operations/备份与恢复演练记录-2026-11-17.md`（PG+MinIO 全量、抽样 sha256 一致） | 仅本机、无加密/异地/RTO |
| DDP-V21-N8-05 | Staging 发布链 | **BLOCKED** | CI 无 deploy job；`scripts/deploy-laptop.sh` 仅本机 | 主机不可解析（RK-01） |
| DDP-V21-N8-06 | Security（JWT/RBAC/scope/signed URL/SSRF/path traversal/secret） | **VERIFIED** | N8-001 + N8-008：训练与模型网关共用 `adapter_url_guard.py`，两个独立 allow_private 开关默认 false；JWT/RBAC/scope/signed URL/path traversal/secret 均有用例 | 限流 Redis 不可用时 fail-open（ADR-0031 已接受） |
| DDP-V21-N8-07 | 前端交互测试与 E2E | **VERIFIED** | N8-007/N8-013：独立 CI job 启动真实后端、固定 digest MinIO、Worker 与 Chromium；JSON 门禁严格要求 4 passed / 0 skipped并上传报告；上传以官方 SDK 完整 MCAP 断言 UploadSession `COMPLETED` 与 RawAsset `VALID`；Testing Library + jsdom 覆盖交互；`npm test` 952 passed / 19 skipped | 外接已有环境仍需显式 ID 与有效 MCAP 路径；staging/真实机器人数据验证仍 BLOCKED |

## 9. N9 真实机器人 / 规模化

| Task ID | 能力项 | 状态 | 现有实现 / 证据 | 缺口 |
|---|---|---|---|---|
| DDP-V21-N9-01 | Robot Registry（model/serial/firmware/controller/sensor/profile） | **PARTIAL** | `domain/devices/models.py:72-91`；`robots.py`；`test_robots_api.py`；N9-015 固定 DROID 官方 GitHub commit 与 Hugging Face DROID-100 revision，严格映射 Franka Panda/FR3、Robotiq 2F-85、ZED 2 ×2、ZED Mini ×1，并经既有 Robot/Device/Sensor API 与 Repository 写入回读 | 无 controller 字段；公开来源无序列号/固件/硬件版本，保持 NULL；真实设备在线握手仍 BLOCKED |
| DDP-V21-N9-02 | Capture Plan（task/scene/robot/sensor/operator/SOP/target/threshold） | **PARTIAL** | N9-003：迁移 0064 增加 `target_episode_count` 与 `quality_thresholds`（键复用数据集质量白名单）；Plan 仍是项目级可复用方案 | 无逐设备执行态；目标/阈值是计划值，不构成自动验收；前端未展示 |
| DDP-V21-N9-03 | Capture Session（Plan→Session→Raw→Upload→Validation→Episode） | **PARTIAL** | N9-001/N9-008/N9-014：会话状态机、迁移 0066/0068、产物归属与只读分页；前端列表/详情/产物独立分页和动作条严格消费服务端 `allowed_actions`，pause/resume/abort 在服务边界强制非空 reason，覆盖七类页面状态 | 未与真实硬件联调（BLOCKED）；历史产物保持 NULL（不回填）；计划目标与实绩仍未自动对账 |
| DDP-V21-N9-04 | 真实设备格式 Adapter（LeRobot/ROS Bag/MCAP/HDF5） | **PARTIAL** | 格式识别 + 魔数（`domain/ingestion/enums.py:85`）；LeRobot preview；N9-015 以固定 GitHub/HF 来源完成真实公开硬件事实与三流 schema → DDP 设备档案闭环；N3-006 补齐 HDF5 结构只读探测并以 RoboMimic v1.5 真实公开样例复验；N3-007 补齐 rosbag1/rosbag2 索引/metadata 只读探测并以 foxglove/mcap 固定 revision ROS1 bag 复验 | ROS Bag 仅索引/metadata、不解码消息体字段；HDF5 仅结构不含观测/动作语义；公开数据不含现场序列号、固件、标定和物理时钟；真实硬件在环验证仍 BLOCKED |
| DDP-V21-N9-05 | 项目隔离与多基地/供应商 | **VERIFIED** | `locations`、`suppliers`、项目成员、跨项目隔离测试；N9-005 新增 `GET /projects/{id}/sites/overview` 按场地只读聚合设备/传感器/采集任务 | 无跨基地调度与供应商计费；仅挂机器人的传感器不归因到场地 |

## 10. 状态变更规则

1. PARTIAL → VERIFIED 必须有：新增测试或真实运行证据 + 契约/前端同步 + 本文档更新。
2. MISSING 项开工前必须补 ADR（涉及新实体/新状态机）与契约版本。
3. BLOCKED 项不得用 Mock 结果升级状态；解除阻塞后重新验证。
4. 本表是 N1–N9 排期的唯一状态来源；PR 描述里的 Capability 段必须引用 Task ID。

