# DDP V3.1 N0-03 数据库 / API / 组件复用关系

- 采集日期：2026-11-22
- 事实基线：`origin/main` @ `46864a9`
- 目的：给出“复用 > 扩展 > 组合 > 新建”的判定依据，避免 V3.1 重复造轮子

## 1. 决策顺序（强制）

```text
复用既有模块 > 扩展现有接口 / 表 > 组合现有能力 > 新建
```

跳过前三步直接新建，评审必须打回（`CLAUDE.md` §一）。

## 2. 数据库：现有表 → V3.1 用途

| 表 | 现有归属 | V3.1 直接复用 | 需要的扩展 | 禁止 |
|---|---|---|---|---|
| `robots` / `devices` / `sensors` | 设备域 | Agent 注册时绑定真实设备 | 无（能力档案已在 `capability_profile`） | 新建第二套设备表 |
| `collection_tasks` / `collection_batches` | 采集域 | Agent 领取的任务来源 | 无 | 把执行态塞进任务表 |
| `capture_sessions` | 采集域 | Agent 每次执行的平台侧记录 | 无（状态机已够） | 新增“上传完成”状态污染语义 |
| `upload_sessions` / `upload_parts` / `upload_part_checksum_bindings` | 接入域 | Agent 断点续传 | 无 | 自建分片表 |
| `raw_assets` | 接入域 | 上传完成产物 | 无（`capture_session_id` 已存在） | 覆盖原始文件 |
| `episodes` / `episode_raw_assets` | Episode 域 | 物化与血缘 | 无 | 重建 Episode 模型 |
| `processing_runs` / `processing_run_steps` | 处理域 | Pipeline 编排 | **需要** Artifact 目录表（`MISSING`） | 把产物继续堆进 `output_refs` JSON |
| `annotation_tasks` / `_batches` / `_assignments` / `_submissions` / `_records` / `_reviews` / `_arbitrations` | 标注域 | 全流程 | 无 | 新建平行标注表 |
| `annotation_schemas` / `annotation_sample_sets` | 标注域 | LabelSchema | 无 | 在前端硬编码字段 |
| `annotation_quality_*` / gold sets / IAA | 标注域 | 质检 | **需要** 异步运行状态（`PARTIAL`） | 把执行态与结论混为一表 |
| `datasets` / `dataset_versions` / `dataset_items` | Dataset 域 | Builder 与冻结 | 无（`manifest_key` 已预留） | 冻结后原地改写 |
| `export_jobs` + artifact manifest | Dataset 域 | 训练导出 | **需要** `ExportFormat` 扩展（Canonical） | 新建第二套导出框架 |
| `training_requests` / `_deliveries` | Dataset 域 | Handoff | 无 | 绕过 Hash 校验交付 |
| 审计日志 / Outbox / 幂等记录 | 平台 | Agent 动作审计、异步事件 | **需要** Agent 相关事件类型登记 | 绕过 Outbox 直发 |

### 允许新增的最小表（如获批准）

| 候选表 | 目的 | 前置条件 |
|---|---|---|
| `edge_agents` / `agent_registrations` / `agent_heartbeats` | 端侧 Agent 身份与在线状态 | 先有 Agent 协议 ADR |
| `agent_task_leases` | 任务领取 / 超时 / 抢占 | 先定义 lease 语义 |
| `processing_artifacts` | 产物目录（kind / schema_version / checksum / producer） | 先有 Artifact 契约 |
| `canonical_streams` / `canonical_observations` / `canonical_actions` | Canonical 实体持久化 | Canonical 版本已统一（ADR-0038/0040）；待 V31-018 产生真实实体后再建表 |

## 3. API：现有接口 → V3.1 用途

| 现有接口 | 复用方式 |
|---|---|
| `GET/POST /capture-sessions`、`POST .../actions/{action}` | Agent 创建 / 启动 / 完成 / 中止会话 |
| `GET /capture-sessions/{id}/artifacts` | 会话产物回读（校验上传完成） |
| `POST /upload-sessions`（含 `capture_session_id`） | Agent 发起上传 |
| `POST .../parts/{n}/url`、`.../ack`、`GET .../parts` | 分片预签名、确认、断点恢复 |
| `POST .../complete`、`POST .../abort` | 完成 / 中止 |
| `GET /raw-assets`、`GET /raw-assets/{id}` | 验证 RawAsset 与归属 |
| `POST /processing-runs` | 提交处理（**待扩展**：接受 `raw_asset_id`） |
| `POST /annotation-tasks`、`.../batches`、`.../annotation-assignments` | 任务、批次、指定标注人 |
| `POST /annotation-assignments/{id}/actions/{action}` | claim / start / release / submit |
| `POST /annotation-submissions`、`.../actions/{action}` | 提交 |
| `POST /annotation-reviews/.../actions/{action}` | 通过 / 驳回 / 返工 |
| `POST /annotation-arbitrations/.../actions/{action}` | 仲裁 |
| `POST /datasets/{id}/versions`、`.../actions/{action}` | Builder 创建、build、validate、freeze |
| `GET .../versions/{id}/selection-preview`、`/items` | 候选预览与条目 |
| `POST /exports` + worker | 训练导出 |
| `POST /training-requests`、`/training-deliveries` | 需求与交付 |

### 契约变更纪律

- `packages/contracts/**` 属共享文件，单一 Owner，变更必须走 ADR / 版本。
- 当前 OpenAPI `1.26.0`，239 条路径。V3.1 新增接口必须先出契约 diff。
- 错误体统一 `{"error": {...}}`；分页统一 cursor；写操作 `If-Match` + `Idempotency-Key`。

### 明确不需要的接口

- 任何“向机器人下发运动 / 复位 / 控制”的接口。未经用户明确授权不得新增。

## 4. 前端：现有页面 / 组件 → V3.1 用途

| 现有资产 | 复用方式 |
|---|---|
| `AnnotationTaskListPage` / `AnnotationTaskDetailPage` | 任务管理入口（已含批次、指派、工作量区） |
| `AssignmentCreateDrawer` / `AssignmentTable` | 指定标注人 |
| `AnnotationWorkbenchPage` / `WorkbenchBody` / `EpisodeEditor` | 标注工作台外壳、草稿、校验、提交 |
| `SchemaForm` / `SchemaField` / `LabelDefinitionEditor` | 结构化字段编辑器 |
| `ReviewQueueBody` / `ReviewPanel` / `ArbitrationQueueBody` / `ArbitrationPanel` | 审核与仲裁 |
| `AnnotationStatusTag` / `ActionButtons` / `PageState` / `ApiErrorState` | 统一状态与反馈 |
| `CaptureSessionDetailPage` / `CaptureSessionActionBar` / `CaptureSessionArtifactList` | 采集会话操作与产物 |
| `DatasetVersionDetailPage` / `ExportListPage` / `TrainingRequest*Page` | 数据集与交付 |
| cursor 分页 hooks、项目 scope 校验、幂等键工具 | 全模块通用 |

### 必须新建的前端能力（`MISSING`）

| 能力 | 归属 | 依赖 |
|---|---|---|
| 媒体审看层（播放 / 逐帧 / 倍速 / 快捷键 / 加载失败态） | `components/media` | ADR-0017 落地 + 媒体访问 API |
| 时间轴片段标注器（`TIME_RANGE` / `ACTION_SEGMENT`） | `components/annotation` | 媒体审看层 |
| 帧 / 对象轨迹标注器（`FRAME` / `OBJECT_TRACK`） | `components/annotation` | 坐标系契约 + 媒体审看层 |
| 审核 / 仲裁媒体证据视图 | `components/annotation` | 媒体审看层 + 版本 diff |
| 个人领取队列 | `pages/annotation` | Assignment 现有 API |
| 工作量运营页 | `pages/annotation` 或 `pages/people` | workload 聚合 API |

### 前端硬约束

- 不做权威权限校验；动作按钮只按服务端 `allowed_actions` 渲染。
- 覆盖 Loading / Empty / Error / Forbidden / Partial / Stale / Disabled。
- 统一 antd 5 + 现有 `PageContainer`，不新建第二套 UI 栈。

## 5. 数据平台 / Adapter 复用

| 资产 | 复用方式 | 注意 |
|---|---|---|
| `ddp_data.adapters.registry.AdapterRegistry` | 统一格式能力注册（detect / inspect / parse / canonicalize） | Processing 目前未接入，另有一套 switch |
| `ddp_data.hdf5` / `ddp_data.rosbag` / `ddp_data.mcap` / `ddp_data.parquet` | 只读探测 | 已复用 |
| `ddp_data.canonical.v1` | Canonical 正式类型 | 导入层已改为引用 `SCHEMA_VERSION`（ADR-0038），无版本副本 |
| `ddp_data.canonical.projection` | Legacy → Canonical 投影 | 暂无生产调用方 |
| `ddp_data.lerobot` | LeRobot 解析 | 与导出方向需区分 |

## 6. 复用判定结论（按 V3.1 七个重点）

| 重点 | 判定 |
|---|---|
| 1 端侧 Agent + CaptureSession | CaptureSession **复用**；Agent **必须新建**（仓库内无等价物） |
| 2 上传 / 断点续传 / RawAsset / MinIO | 全部 **复用**；只补测试与真实 MinIO 验收 |
| 3 解析 / Canonical / Episode / Pipeline | 编排与探测 **复用**；Canonical 共享 Schema 已新增（V31-017：`json_schema.py` 生成器 + `packages/contracts/schemas/canonical-v1/`）；Artifact 目录与 Canonical 持久化 **需新增** |
| 4 AnnotationTask 与指定标注人 | 全部 **复用**；只补 E2E |
| 5 标注控制台 | 外壳与结构化编辑 **复用**；媒体标注 **必须新建** |
| 6 质检 / 审核 / 返工 / 仲裁 | 审核返工仲裁 **复用**；异步质检编排 **需扩展** |
| 7 Dataset / 冻结 / Manifest / 导出 | Builder 与冻结 **复用**；Canonical 导出与 Manifest 对象化 **需扩展** |

**没有任何一个重点需要“推倒重建”。**
