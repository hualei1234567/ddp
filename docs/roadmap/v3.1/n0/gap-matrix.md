# DDP V3.1 N0-02 模块差距矩阵

- 采集日期：2026-11-22
- 事实基线：`origin/main` @ `46864a9`
- 状态口径：`VERIFIED` / `PARTIAL` / `UNVERIFIED` / `MISSING` / `BLOCKED` / `PLANNED` / `DEPRECATED`
- 覆盖范围：用户 V3.1 指令点名的七项重点，加上跨模块的真实链路

> **读法**：`VERIFIED` 只表示“有代码、契约、迁移或测试证据支撑该能力存在且接线完成”，
> 不等于“已在测试环境端到端跑通”。凡未在本轮实际运行的能力，另标 `UNVERIFIED`。

## 1. 结论摘要

| 业务能力 | 后端 | 前端 | 端到端 | 主要缺口 |
|---|---|---|---|---|
| 端侧采集 Agent 与 CaptureSession | `PARTIAL` | `VERIFIED` | `BLOCKED` | Agent 完全 `MISSING` |
| 上传 / 断点续传 / RawAsset / MinIO | `VERIFIED` | `VERIFIED` | `UNVERIFIED` | 上传→RawAsset 继承仅伪测试；真实 MinIO 未验 |
| 解析 / Canonical / Episode / Pipeline | `PARTIAL` | `PARTIAL` | `PARTIAL` | Pipeline 不建 Episode；Canonical Stream/Observation/Action 无生产与持久化 |
| AnnotationTask 创建与指定标注人 | `VERIFIED` | `VERIFIED` | `UNVERIFIED` | 真实浏览器 E2E 缺 |
| 完整标注控制台与真实交互 | `VERIFIED` | `PARTIAL` | `UNVERIFIED` | 媒体播放器 / 时间轴 / 帧与轨迹标注器 `MISSING` |
| 自动质检 / 人工审核 / 返工 / 仲裁 | `PARTIAL` | `VERIFIED` | `UNVERIFIED` | 质检为同步事务内，无异步编排 |
| Dataset Builder / 冻结 / Manifest / 导出 | `VERIFIED` | `PARTIAL` | `UNVERIFIED` | LeRobot 仅元数据；Canonical 导出 `MISSING`；Manifest 未证明落对象存储 |

## 2. 端侧采集 Agent 与 CaptureSession

### Existing（`VERIFIED`）

- Robot / Device / Sensor 领域实体与生命周期状态机：
  `backend/app/domain/devices/models.py:39-260`
- CollectionTask / CollectionBatch，含 `assigned_robot_id`、`assigned_device_ids`、
  `assigned_sensor_ids`、`location_id`：`backend/app/domain/collection/models.py:52-251`
- CaptureSession 独立聚合与状态机 `SCHEDULED → RUNNING ↔ PAUSED → COMPLETED / ABORTED`：
  `backend/app/domain/collection/models.py:353`、`backend/app/domain/collection/enums.py:135-156`、
  `backend/app/application/collection/capture_sessions.py`、
  `backend/app/infrastructure/db/models/capture_session.py:38-108`
- 迁移 `0066_capture_sessions.py`、`0068_capture_session_artifacts.py`
- API：`backend/app/api/v1/capture_session_routes.py:38-142`
- 契约：`packages/contracts/openapi.yaml:562-568`
- 前端：`apps/web/src/services/captureSessions.ts:9-51`、
  `apps/web/src/pages/collection/CaptureSessionDetailPage.tsx:20-57`

### Gap

- **端侧采集 Agent：`MISSING`**。`services/` 仅有 `local-adapter`（训练/评测/推理/回调），
  没有任务领取、录制、本地 spool、断网恢复、multipart 客户端、心跳、Agent 身份。
- **CaptureSession → UploadSession → RawAsset 继承：`PARTIAL`**。
  `backend/tests/api/test_capture_session_artifacts_api.py:296-327` 的函数名声称验证继承，
  实际只创建 UploadSession 并断言响应字段，未上传分片、未 complete、未查询 RawAsset。
- **真实 MinIO / 真实硬件：`UNVERIFIED` / `BLOCKED`**。
- `capture_session_id` 可空是兼容设计，但意味着“会话 COMPLETED”不能推断“素材已上传校验成功”。

### 越权与隔离（已覆盖）

- 跨项目会话详情 / 动作 / 关联任务均返回 404：`backend/tests/api/test_capture_session_api.py:239-283`
- 跨项目 Episode 绑定拒绝、outsider 403：`test_capture_session_artifacts_api.py:106-166,285-349`

## 3. 上传、断点续传、RawAsset 与 MinIO

### Existing（`VERIFIED`）

- `upload_sessions` 已含 `collection_task_id` / `batch_id` / `capture_session_id` /
  `expected_sha256` / `asset_id`：`backend/app/infrastructure/db/models/ingestion.py:59-164`
- 分片与 checksum 绑定：同文件 `:211-260`
- 断点恢复（`list_parts` + 缺失分片计算）：
  `backend/app/application/ingestion/upload_sessions.py:121-282`
- S3/MinIO 适配器（SigV4、预签名、multipart、HEAD、Range）：
  `backend/app/infrastructure/storage/s3.py:29-207`
- Compose MinIO（固定版本 + digest + 健康检查）：`docker-compose.yml:48-69`

### Gap

- **真实 MinIO 上传贯通：`UNVERIFIED`**。API 测试用 `FakeMultipartStorage`
  （`backend/tests/fakes_s3.py:4` 自身声明不替代真实验收）。
- 需补：重复 ACK / 重复 complete / NoSuchUpload / URL 过期 / 上传后回读复算 SHA256。

## 4. 解析、Canonical、Episode 与 Pipeline

### Existing（`VERIFIED`）

- ProcessingRun 七态 + 八阶段 + 步骤历史 + 乐观锁：
  `backend/app/domain/processing/enums.py:27-90`、`models.py:108-372`
- Outbox + Worker + 租约 + 退避重试 + 死信：`backend/app/workers/processing.py:1-17,162-531`
- 五种格式只读探测（MP4 / Parquet / MCAP / HDF5 / ROS Bag），内容魔数不信任扩展名：
  `backend/app/application/processing/metadata_stage.py:70-79,203-218`
- Canonical v1 类型定义（Frame / VideoStream / SensorStream / Action / Observation / Episode）：
  `data-platform/ddp_data/canonical/v1.py:36-398`
- 导入按 `episode_key` 物化 Episode + 自动 readiness：
  `backend/app/application/datasources/episode_materialization.py:131-236`

### Gap

| 缺口 | 状态 | 证据 |
|---|---|---|
| ProcessingRun 输入是裸对象 key，非 RawAsset ID | 已消除（V31-015，2026-11-22） | `POST /processing-runs` 接受 `raw_asset_id`（与 `input_refs` 二选一），服务端解析对象 key 并校验 `VALID`/`ARCHIVED`；跨项目统一 404。测试：`tests/api/test_processing_run_raw_asset.py` |
| Processing 未复用 AdapterRegistry，另有一套格式 switch | `MISSING` | `metadata_stage.py:490-502` vs `data-platform/ddp_data/adapters/registry.py` |
| Pipeline `EPISODE_SEGMENTATION` 只核验已有 Episode，不切片不创建 | 已消除（V31-018，2026-11-22；范围：单文件 MP4） | 输入是 RawAsset 且元数据产物给出 `duration_ms` 时按 `episode-boundary/1.0.0` 创建 Episode（`application/processing/episode_boundaries.py` + `segmentation_stage.py`，落库经 `EpisodeService.create`，稳定 code 幂等）；其余格式如实 `SKIPPED` 并写明"可能是多 Episode 容器，缺少明确边界声明"。测试：`tests/unit/test_processing_episode_boundaries.py`、`test_processing_segmentation_stage.py`、`test_processing_segmentation_pipeline.py`、`tests/workers/test_processing_worker_segmentation.py` |
| 多 Episode 容器（MCAP / ROS Bag / HDF5 / Parquet）没有可用的 Episode 边界声明来源 | `MISSING` | 导入链路 `raw_metadata` 只有 `episode_key`/`source_path`/`camera`/`source_fps`，无时间边界；HDF5 结构摘要只有路径与属性名。在这些格式补齐边界前置条件（上游给出显式分段声明）前，处理链路不把它们整段当成一个 Episode |
| 导入成功不自动触发 Episode 物化，需手工 API | 已消除（V31-014，2026-11-22） | `backend/app/workers/datasources.py` 消费投递类 `datasource.import.succeed`（测试：`tests/workers/test_datasource_episode_worker.py`）；手工端点保留为重放入口 |
| SensorStream / Observation / Action 无真实生产 | `MISSING` | 仅有类型定义与构造测试 |
| Canonical 实体无数据库持久化 | `MISSING` | 无对应表 / repository |
| 产物仅 `output_refs` JSON 数组，无 Artifact 目录 | 已消除（V31-016，2026-11-22） | 新增 `processing_artifacts` 表（迁移 0069）+ 领域 `ProcessingArtifact` + 仓储 + `GET .../processing-runs/{id}/artifacts`。阶段产出登记类型/阶段/尝试/Schema 版本/内容类型/字节数/SHA256/来源输入；`output_refs` 退化为兼容投影。测试：`tests/unit/test_processing_artifacts_domain.py`、`test_processing_artifacts_repository.py`、`test_processing_artifact_registration.py`、`tests/api/test_processing_artifacts_api.py` |
| `DATA_CLEANING` 使用 `SkippedStage` | `MISSING` | `backend/app/application/processing/stages.py:144-149` |
| Canonical 版本分裂 `1.0.0-draft` vs `1.0.0` | 已消除（V31-004，2026-11-22） | 导入物化改为引用 `ddp_data.canonical.v1.SCHEMA_VERSION`（`episode_materialization.py`）；防漂移测试 `tests/unit/test_episode_status_alignment.py`。历史 `-draft` 数据按 ADR-0038 §2.4 有意保留 |
| Canonical 无跨语言共享 Schema；写入入口接受任意版本字符串 | 已消除（V31-017，2026-11-22） | 新增生成器 `ddp_data/canonical/json_schema.py` + 导出脚本 `scripts/export_canonical_schemas.py`，产物 `packages/contracts/schemas/canonical-v1/`（6 个实体）；`EpisodeService.create` 用 `SUPPORTED_SCHEMA_VERSIONS` 白名单拒绝未知版本（`application/episodes/canonical_version.py`）。测试：`tests/unit/test_canonical_json_schema.py`（33 条）、`tests/api/test_episodes_api.py`（白名单 2 条）。ADR-0040 |
| `pipeline.py` 头部注释称仅两阶段实现（已过时） | 文档债 | `backend/app/application/processing/pipeline.py:7-12` |

## 5. AnnotationTask 创建与指定标注人

### Existing（`VERIFIED`）

- 状态机集中在 `backend/app/domain/annotation/state_machines.py`、`enums.py:12-508`
- 任务 / 批次 / 指派 / 提交 / 复核 / 仲裁用例：
  `backend/app/application/annotation/{tasks,batches,assignments,submissions,reviews,arbitrations}.py`
- API：`backend/app/api/v1/annotation_work.py:68-456`、`annotation_results.py:57-443`
- 容量与工作量聚合：`annotation_work.py:376-412`、
  `backend/app/application/annotation/assignments.py:394-424,537-563`
- 前端：`AnnotationTaskCreatePage`、`AnnotationTaskDetailPage`、
  `AssignmentCreateDrawer`、`AssignmentTable`

### Gap

- `PARTIAL` 领取（claim）模式的完整“待领取池”前端入口未见独立页面。
- 工作量后端完整，运营视图（周期趋势 / 超容量预警 / 首检通过率）`PARTIAL`。

## 6. 完整标注控制台与真实交互

### Existing（`VERIFIED`）

- 路由真实挂载：`apps/web/src/routes.tsx:187-246`
- 真实 API 客户端与严格响应解析：`apps/web/src/services/annotation.ts:1-118`
- 工作台在线提交链路：`AnnotationWorkbenchPage.tsx:114-214`、`WorkbenchBody.tsx`
- 审核 / 仲裁组件：`ReviewPanel.tsx`、`ArbitrationPanel.tsx`
- Schema 编辑器：`SchemaDefinitionDrawer.tsx`、`LabelDefinitionEditor.tsx`

### Gap（控制台的真实短板）

| 缺口 | 状态 |
|---|---|
| 视频播放 / 逐帧 / 倍速 / 时间轴 | 已消除（V31-045 切片 A，2026-11-23）：`MediaPreview` 提供倍速 0.25x–2x、帧步进（1/25s）、`←/→` ±1s、`,/.` 逐帧快捷键、播放时钟；组件测试 12 条。此前 `AnnotationTimeline`/`EpisodeMediaPanel`/`MediaPreview` 已存在（本表旧判断"无引用"与代码不符） |
| `TIME_RANGE` / `ACTION_SEGMENT` 时间轴编辑器 | 已消除（V31-045 切片 B，2026-11-23）：时间轴支持拖拽框选→填入标签、片段边界拖拽调整（`onSegmentResize`）、删除按钮（确认由调用方负责）；`AnnotationTimeline` 测试 11 条。此前拖拽框选已存在（本表旧判断"MISSING"过时） |
| `FRAME` / `OBJECT_TRACK` 框选与轨迹工具 | 部分：轨迹行编辑器已有（E2E 覆盖"新增轨迹"）；帧级框选画布仍未做 |
| 审核 / 仲裁中的媒体证据上下文 | 已消除（V31-024 核实，2026-11-23）：`AnnotationSubmissionPage` 同页挂载 `SubmissionMediaPanel` + `ReviewPanel` + `ArbitrationPanel`，复核人从复核队列进入同一页面审看媒体证据；E2E 覆盖复核领取→通过 |
| 浏览器—真实后端全链 E2E | `VERIFIED`（`e2e/annotation-console.spec.ts`：任务→工作台→框选→轨迹→提交→复核→通过全链；V31-045 新增播放控制 E2E） |
| AI 建议时间轴叠加 | 已消除（V31-046 切片 D，2026-11-23）：时间轴渲染 AI 建议轨道（虚线+AI 徽标+置信度），采纳/修订仍以 `AiSuggestionPanel` 为唯一入口 |

> 结论修正（2026-11-23 二次更新）：`docs/roadmap/v3.1/blueprint/README.md` 中"完整标注控制台未完成"的判断
> **已不成立**；本表 2026-11-22 的"媒体标注能力 MISSING"判断**亦已过时**——时间轴拖拽框选/片段填入/
> 轨迹编辑器/全链 E2E 在 V31-027 轮次已落地，V31-045 补齐播放控制与片段边界编辑。

## 7. 自动质检、人工审核、返工、仲裁

### Existing

- 质量检查 `PASSED / FAILED`、抽检、黄金集、IAA、双标一致性、AI 建议：
  `backend/app/api/v1/annotation_quality.py:50-446`、
  `annotation_quality_check_routes.py:24-49`
- 审核 / 驳回 / 返工 / 仲裁状态机：`enums.py:311-372`
- `annotation_quality_check_routes` 为同步执行

### Gap

- **异步自动质检编排 `PARTIAL`**：无“提交后自动触发”、无重试/死信。
  - 已消除（V31-030，2026-11-22）：质检运行生命周期
    `lifecycle`（`QUEUED/RUNNING/SUCCEEDED/FAILED/RETRYING/CANCELLED`）与结论
    `status` 正交拆列（迁移 0070），领域状态机 + 数据库 CHECK 双向兜底；
    结论视图（`get/list/latest_for_subjects`）只认 `SUCCEEDED`。ADR-0042。
    测试：`tests/unit/test_quality_run_lifecycle.py`、
    `test_quality_run_repository_lifecycle.py`、
    `tests/migrations/test_quality_run_lifecycle_migration.py`。
  - 已消除（V31-031 切片一/二，2026-11-22）：执行写入
    `SqlQualityRunRepository.enqueue/find_any/transition`（CAS，ADR-0043），
    用例 `QualityRunUseCases.enqueue/start/conclude`，Worker
    `backend/app/workers/quality.py` 消费 `quality.run.requested`
    （`DISPATCH_EVENTS` 已登记、`docker-compose.yml` 有 `quality-worker`）。
    测试：`tests/unit/test_quality_run_execution_repository.py`（8）、
    `tests/workers/test_quality_run_worker.py`（6）。
  - 已消除（V31-032，2026-11-22）：结论驱动路由
    `domain/quality/routing.py`（结论 → 动作序列判定表）+
    `application/quality/routing.py`（编排，复用既有提交用例，以项目负责人
    身份执行，按状态机逐级推进）；Worker 在结论落库后路由，失败只告警。
    ADR-0044。测试：`tests/unit/test_quality_routing_rules.py`（12）、
    `tests/unit/test_quality_routing_use_cases.py`（8）、
    `tests/workers/test_quality_run_worker.py`（10）。
  - 已消除（V31-033，2026-11-22）：批量重跑
    `POST /quality-runs/batch`（只入队，响应 `QueuedQualityRun` 无结论）与
    差异报告 `GET /quality-runs/{baseline}/diff/{candidate}`（按
    `rule_id + subject_ref` 对齐，四类：NEW/RESOLVED/UNCHANGED/CHANGED）。
    领域判定 `domain/quality/diff.py`，契约 1.30.0。测试：
    `tests/unit/test_quality_run_diff.py`（11）、
    `tests/unit/test_quality_batch_rerun_diff.py`（10）、
    `tests/api/test_quality_batch_rerun_diff_api.py`（9）。
  - 仍缺：前端批量重跑与差异报告入口；生命周期查询入口出现前，
    `QUEUED`/`RUNNING` 只能查库（批量端点已能返回排队事实）。
- `backend/app/domain/annotation/enums.py:499-504` 明确其与业务写入同事务；
  该同步路径尚未改造（改造属于 V31-031）。
- 风险：提交事务被质检规则拖长（在 V31-031 前仍然存在）。

## 8. Dataset Builder、版本冻结、Manifest 与训练导出

### Existing（`VERIFIED`）

- 纳入门禁：Episode 状态 + `AnnotationRecord=APPROVED` +
  Submission `APPROVED/RESOLVED_APPROVED`：
  `backend/app/infrastructure/repositories/dataset_directory.py:23-25,99-128`、
  `backend/app/domain/datasets/selection.py:51-53,323-369`
- DatasetItem 固化 Episode / Annotation / Schema / 资产 / Hash 快照：
  `backend/app/domain/datasets/models.py:399-527`
- 版本不可变三层保护（领域不变量 + 应用动作 + DB 触发器）：
  `models.py:339-396`、`backend/app/infrastructure/db/dataset_guards.py:112-385`
- 确定性 Manifest（JCS + SHA256，读取时复算）：
  `backend/app/domain/datasets/manifest.py:26-210`
- 异步对象存储导出（写后回读、artifact manifest、聚合 Hash、预签名下载）：
  `backend/app/workers/exports.py:47-283`、
  `backend/app/application/datasets/export_execution.py:82-369`
- TrainingRequest / Delivery / Handoff 审批与 Hash 绑定：
  `backend/app/application/datasets/training_deliveries.py:147-548`

### Gap

| 缺口 | 状态 | 说明 |
|---|---|---|
| 严格 Accepted 口径 | `PARTIAL` | `_EPISODE_STATUSES = {"ACCEPTED", "COMPLETED"}`，`COMPLETED` 兼容放行需产品确认 |
| Dataset Manifest 落对象存储 | `PARTIAL` | 有 `manifest_key` 字段，冻结链路未见 put/readback；当前靠 DB 复算 |
| LeRobot 导出 | `PARTIAL` | 仅 `meta/*.json` + NDJSON 索引，无帧级 observation/action，`verification_status=UNVERIFIED` |
| Canonical 训练导出 | `MISSING` | `ExportFormat` 仅 `LE_ROBOT`；Canonical 仅是导入/投影能力 |
| 审核 → Dataset Builder 的 UI 跳转 | `PARTIAL` | 后端门禁可靠，前端缺“审核通过→加入数据集”与条目回链 |
| 真实数据 / 真实 MinIO / 真实 Loader E2E | `MISSING` | 现有“真实”指真实 SQLite 仓储与真实状态机 |

## 9. 跨模块风险

| 风险 | 等级 | 说明 |
|---|---:|---|
| 把平台侧 CaptureSession 当成端侧采集已完成 | 高 | 会严重低估 Agent 工作量 |
| 把同步质检 API 当成完整自动质检 | 高 | 缺异步编排与规则版本 |
| 把 LeRobot 元数据导出宣传为训练格式兼容 | 高 | 代码已固定 `UNVERIFIED`，文案必须一致 |
| 文档状态漂移 | 已消除（V31-003，2026-11-23） | RK-49 已划线闭环（素材侧迁移 0068/V31-001）；`pipeline.py` 头注释与阶段清单同步到八阶段现状 |
| Canonical 版本分裂 | 已消除 | 版本号单一来源（ADR-0038）；历史数据保留原值属溯源事实 |
| `COMPLETED` 绕过 Accepted 语义 | 中 | ADR-0038 §2.1 裁决 `COMPLETED` 可进 Dataset Builder（状态机保证其质量结论为 PASS）；门禁仍只读最新质量结论，未增加额外的数据库级兜底（原计划挂在 V31-030，实际 V31-030 只处理质量运行生命周期） |

## 10. 与 V2.1 能力矩阵的关系

V2.1 矩阵（`docs/roadmap/v2.1/n0/capability-matrix.md`）记录 N1–N9 的 WBS 状态，
仍是 V2.1 条目的唯一状态源。本文件不复制其条目，只记录 **V3.1 新增的八条业务能力视角**。
两者冲突时，以代码证据为准，并同步更新两处。
