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
| Pipeline `EPISODE_SEGMENTATION` 只核验已有 Episode，不切片不创建 | `PARTIAL` | `backend/app/application/processing/segmentation_stage.py:39-79` |
| 导入成功不自动触发 Episode 物化，需手工 API | 已消除（V31-014，2026-11-22） | `backend/app/workers/datasources.py` 消费投递类 `datasource.import.succeed`（测试：`tests/workers/test_datasource_episode_worker.py`）；手工端点保留为重放入口 |
| SensorStream / Observation / Action 无真实生产 | `MISSING` | 仅有类型定义与构造测试 |
| Canonical 实体无数据库持久化 | `MISSING` | 无对应表 / repository |
| 产物仅 `output_refs` JSON 数组，无 Artifact 目录 | `MISSING` | `backend/app/infrastructure/db/models/processing.py:58-65` |
| `DATA_CLEANING` 使用 `SkippedStage` | `MISSING` | `backend/app/application/processing/stages.py:144-149` |
| Canonical 版本分裂 `1.0.0-draft` vs `1.0.0` | 已消除（V31-004，2026-11-22） | 导入物化改为引用 `ddp_data.canonical.v1.SCHEMA_VERSION`（`episode_materialization.py`）；防漂移测试 `tests/unit/test_episode_status_alignment.py`。历史 `-draft` 数据按 ADR-0038 §2.4 有意保留 |
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
| 视频播放 / 逐帧 / 倍速 / 时间轴 | `MISSING`（`apps/web/src/components/annotation` 与 `pages/annotation` 中无 video/player/timeline 引用） |
| `TIME_RANGE` / `ACTION_SEGMENT` 时间轴编辑器 | `MISSING` |
| `FRAME` / `OBJECT_TRACK` 框选与轨迹工具 | `MISSING` |
| 审核 / 仲裁中的媒体证据上下文 | `UNVERIFIED` |
| 浏览器—真实后端全链 E2E | `UNVERIFIED` |

> 结论修正：`docs/roadmap/v3.1/blueprint/README.md` 中“完整标注控制台未完成”的判断
> **已不成立**；准确表述是“结构化标注闭环已完成，媒体标注能力待补齐”。

## 7. 自动质检、人工审核、返工、仲裁

### Existing

- 质量检查 `PASSED / FAILED`、抽检、黄金集、IAA、双标一致性、AI 建议：
  `backend/app/api/v1/annotation_quality.py:50-446`、
  `annotation_quality_check_routes.py:24-49`
- 审核 / 驳回 / 返工 / 仲裁状态机：`enums.py:311-372`
- `annotation_quality_check_routes` 为同步执行

### Gap

- **异步自动质检编排 `PARTIAL`**：无“提交后自动触发”、无运行状态机、无重试/死信、
  无规则版本快照。`backend/app/domain/annotation/enums.py:499-504` 明确其与业务写入同事务。
- 风险：提交事务被质检规则拖长。

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
| 文档状态漂移 | 中高 | `docs/roadmap/v2.1/n0/risk-list.md:72` 仍称 CaptureSession 未关联产物 |
| Canonical 版本分裂 | 已消除 | 版本号单一来源（ADR-0038）；历史数据保留原值属溯源事实 |
| `COMPLETED` 绕过 Accepted 语义 | 中 | ADR-0038 §2.1 裁决 `COMPLETED` 可进 Dataset Builder（状态机保证其质量结论为 PASS）；数据库兜底由 V31-030 补 |

## 10. 与 V2.1 能力矩阵的关系

V2.1 矩阵（`docs/roadmap/v2.1/n0/capability-matrix.md`）记录 N1–N9 的 WBS 状态，
仍是 V2.1 条目的唯一状态源。本文件不复制其条目，只记录 **V3.1 新增的八条业务能力视角**。
两者冲突时，以代码证据为准，并同步更新两处。
