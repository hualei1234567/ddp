# 阶段 2 契约附件 B：Episode、血缘、API 摘要与可测试契约

- 归属：`docs/architecture/阶段2-采集上传RawAsset-Episode契约-v1.md` 的 §8、§9、§12
- 状态：草案（未冻结）
- 版本：`ddp.stage2/1.0.0-draft`
- 机器可读：`docs/architecture/contract-draft/collection/episode.v1.schema.json`、`lineage-edge.v1.schema.json`、`openapi.stage2.v1.yaml`

## 8. Episode 基础切片与血缘

### 8.1 Episode 创建

`POST /api/v1/projects/{project_id}/episodes`，请求 `EpisodeCreate`：

| 字段 | 必填 | 说明 |
|---|---|---|
| `code` | 否 | 项目内唯一；缺省由服务端生成 |
| `collection_task_id`、`batch_id` | 否 | 同项目；与 RawAsset 来源兼容 |
| `robot_id`、`device_ids`、`sensor_ids` | 否 | 同项目；缺省从 RawAsset 快照补齐 |
| `raw_asset_ids` | 是 | 1–64 个；必须 `VALID/ARCHIVED`；全部同项目 |
| `segmentation` | 是 | 切片规则与时间范围；见 §8.2 |
| `time_base` | 否 | 缺省从 RawAsset/传感器快照推导；缺失或 `UNVERIFIED/FAILED` 时创建失败 |
| `calibration_snapshot` | 否 | 客户端可提供精确记录；服务端必须校验并补齐；不得只给 `calibration_id` |
| `scene_labels`、`language_instruction` | 否 | 元数据；不替代审核标签 |
| `success`、`failure_reason` | 否 | 采集结果；失败时必须给原因 |

创建规则：

1. 服务端必须校验所有 RawAsset 同项目、状态可用、格式可切片；跨项目统一 404。
2. `segmentation.method=MANUAL_TIME_RANGE` 时必须提供 `start_time_ns`、`end_time_ns`；`end > start`；范围必须落在每个 RawAsset 的声明时间范围内。
3. `segmentation.method=RULE` 时必须提供 `rule_version` 与规则参数；阶段 2 只允许已注册规则，未注册返回 `422 validation_failed`。
4. 同一资产/流上的重叠区间默认拒绝（`409 episode_overlap_conflict`）；仅 DE/QI 显式 `allow_overlap=true` 且给 `overlap_reason` 时允许，并写审计。
5. 标定快照必须满足 `valid_from <= captured_at < valid_to` 且 `status=ACTIVE`；缺失/过期/撤销返回 `422 calibration_not_active`；校验和变化返回 `422 calibration_snapshot_invalid`。
6. 创建后 Episode `status=PENDING`；同一事务写 `episode_raw_assets`、`data_lineage_edges`（`RAW_ASSET → EPISODE`、`INGESTION_JOB → EPISODE`）、审计/outbox；异步入 `PROCESSING`。
7. 切片规则、Pipeline、Parser、Canonical Schema 版本必须记录；不得使用“最新版本”隐式解析。

### 8.2 切片规则

`segmentation` 对象：

```json
{
  "method": "MANUAL_TIME_RANGE",
  "rule_version": "seg-manual-v1",
  "start_time_ns": "1730000000000000000",
  "end_time_ns": "1730000010000000000",
  "time_domain": "UTC",
  "allow_overlap": false,
  "overlap_reason": null
}
```

- `method`：`MANUAL_TIME_RANGE`、`RULE`、`PARSER_SEGMENT`（阶段 2 仅前两者可用；解析器切片留扩展位）。
- `rule_version` 必须非空；规则实现必须纯函数式、可重放；相同输入 + 规则版本 + 配置 Hash 必须得到相同边界。
- 切片边界必须满足：`start_time_ns < end_time_ns`；`duration_ns = end - start`；边界落在资产时间范围内；不得跨资产缺失时间。
- 规则不得按“最新文件”“最新标定”或本地时区隐式选择；冲突失败关闭。
- 重叠检测以 `(project_id, raw_asset_id, stream_id, time_range)` 为准；默认拒绝；允许时记录审批人/原因。

### 8.3 Episode Manifest

Manifest 由 Worker 生成，存对象存储 `canonical/{project_id}/{episode_id}/{episode_version}/manifest.json`；DB 只存 `manifest_key`、`manifest_sha256`、`frame_count`、`start/end/duration` 等索引字段。Manifest 至少包含：

| 字段 | 说明 |
|---|---|
| `schema_version` | `ddp.canonical/1.0.0-draft`（或后续冻结版本） |
| `episode_id`、`episode_lineage_id`、`episode_version`、`project_id`、`code` | 标识与版本 |
| `task_id`、`batch_id`、`robot_id`、`device_ids`、`sensor_ids` | 采集来源 |
| `start_time`、`end_time`、`duration_ns`、`time_base`、`time_alignment` | 时间语义；`time_base.sync_status` 必须显式 |
| `calibration_snapshot[]` | 至少 `calibration_id`、`calibration_version`、`checksum`、`target_type`、`target_id`、`schema_version` |
| `streams[]` | `stream_id`、`sensor_id`、`modality`、`frame_count`、`sample_rate_hz`、`dropped_count`、`chunk_refs`；每块含 `chunk_index`、`row_count`、`sha256`、`time_range` |
| `raw_asset_ids` | 输入原始资产；不得包含跨项目/墓碑资产 |
| `provenance` | `raw_asset_ids`、`ingestion_job_ids`、`pipeline_id/version`、`parser_id/version`、`config_hash`、`segmentation_rule_version` |
| `quality` | `status`、`rule_version`、`issues[]`；问题项含 `code`、`severity`、`time_range`、`sensor_id`、`message`、`evidence_ref` |
| `manifest_sha256` | 对 Manifest 规范化 JSON（RFC 8785 JCS）计算的 SHA256；不得包含自身 |

Manifest 与 DB 一致性：

- `manifest_sha256` 必须由服务端计算；客户端提交一律忽略。
- `READY` 前生成并校验；`READY` 起 Manifest 不可变；修订生成新 Episode 版本与新 Manifest。
- Manifest 中所有引用必须同项目；出现跨项目引用立即失败并审计。
- `GET /episodes/{id}/manifest` 返回 Manifest JSON 与 `manifest_sha256`；超过内联上限（建议 1 MiB）时返回 `manifest_url` 短期签名地址，并要求客户端校验 Hash。

### 8.4 数据血缘

节点类型：

`RAW_ASSET`、`UPLOAD_SESSION`、`COLLECTION_TASK`、`COLLECTION_BATCH`、`INGESTION_JOB`、`EPISODE`、`QUALITY_RUN`、`ANNOTATION`、`DATASET_VERSION`、`TRAINING_RUN`。

边关系：

| 关系 | 方向 | 说明 |
|---|---|---|
| `UPLOADED_BY` | RAW_ASSET → UPLOAD_SESSION | 资产由会话登记 |
| `PRODUCED_BY` | RAW_ASSET → COLLECTION_TASK/BATCH | 来源任务/批次 |
| `USED_AS_INPUT` | INGESTION_JOB → RAW_ASSET | Job 输入 |
| `PRODUCED` | INGESTION_JOB → EPISODE | Job 产物 |
| `SEGMENTED_INTO` | RAW_ASSET → EPISODE | 切片关系 |
| `SUPERSEDES` | EPISODE(new) → EPISODE(old) | 修订谱系 |
| `INVALIDATES` | EPISODE/事件 → EPISODE/RAW_ASSET | 失效传播 |
| `EVALUATED_BY` | EPISODE → QUALITY_RUN | 质检引用 |
| `ANNOTATED_BY` | EPISODE → ANNOTATION | 阶段 3 扩展 |
| `INCLUDED_IN` | EPISODE → DATASET_VERSION | 阶段 4 扩展 |

规则：

1. `data_lineage_edges` 只追加；不得 UPDATE/DELETE；纠错通过追加新边与失效标记。
2. 每条边必须带 `project_id`；`from`/`to` 节点必须属于同一项目；DB 触发器/复合外键兜底。
3. 血缘图不得成环（`SUPERSEDES` 除外，必须线性且唯一）；应用层校验 + 数据库唯一约束。
4. 失效不删除历史：追加 `INVALIDATES` 边，保留原 Episode/资产元数据。
5. 查询支持正向/反向，深度 1–5，分页；默认只返回当前项目。
6. 血缘查询结果必须带节点类型、ID、关系、时间、操作者（可脱敏）；不得泄漏跨项目节点存在性。

### 8.5 Episode 失效传播

- RawAsset → `QUARANTINED/TOMBSTONED`：所有引用它的非终态 Episode → `INVALIDATED`；`ARCHIVED` Episode 标记 `quality_status=FAIL` 并追加失效边，不删除。
- 标定 `REVOKED/EXPIRED`：引用该标定快照的 Episode 进入 `QUALITY_CHECKING` 重新评估；无法满足则 `INVALIDATED`，不得静默改写历史快照。
- Manifest Hash 校验失败：Episode `FAILED/INVALIDATED`，记录 `manifest_hash_mismatch` 与证据。
- 失效动作必须 PO + QI；系统自动传播写审计并标注 `actor=SYSTEM`。

## 9. API 契约摘要

完整机器可读路径见 `docs/architecture/contract-draft/collection/openapi.stage2.v1.yaml`。所有路径省略 `/api/v1` 前缀；项目范围由服务端强制。

### 9.1 采集任务与批次

| 方法 | 路径 | 说明 | 主要错误码 |
|---|---|---|---|
| GET/POST | `/projects/{project_id}/collection-tasks` | 列表/创建任务 | `project_scope_denied`、`validation_failed`、`duplicate_resource` |
| GET/PATCH | `/projects/{project_id}/collection-tasks/{task_id}` | 详情/修改（仅 DRAFT 核心字段） | `resource_not_found`、`version_conflict`、`resource_immutable` |
| POST | `/projects/{project_id}/collection-tasks/{task_id}/actions/{action}` | 状态动作白名单 | `state_transition_invalid`、`forbidden`、`validation_failed` |
| GET/POST | `/projects/{project_id}/collection-tasks/{task_id}/batches` | 批次列表/拆分 | `state_transition_invalid`、`duplicate_resource` |
| GET/PATCH | `/projects/{project_id}/collection-batches/{batch_id}` | 批次详情/修改 | `resource_not_found`、`version_conflict` |
| POST | `/projects/{project_id}/collection-batches/{batch_id}/actions/{action}` | 批次状态动作 | `state_transition_invalid`、`forbidden` |

`action` 白名单：`submit-review`、`return-draft`、`approve`、`ready`、`assign`、`start`、`pause`、`resume`、`block`、`unblock`、`submit`、`accept`、`reject`、`rework`、`complete`、`cancel`。非法 action 返回 `422 validation_failed`，不得拼接到 SQL/状态字段。

### 9.2 上传

| 方法 | 路径 | 说明 | 主要错误码 |
|---|---|---|---|
| POST | `/projects/{project_id}/upload-sessions` | 创建上传会话 | `file_type_unsupported`、`file_too_large`、`idempotency_key_reused` |
| GET | `/projects/{project_id}/upload-sessions/{upload_session_id}` | 断点状态 | `upload_session_expired`、`resource_not_found` |
| POST | `/projects/{project_id}/upload-sessions/{upload_session_id}/parts/{part_number}/url` | 获取分片 URL | `upload_session_expired`、`upload_part_conflict` |
| POST | `/projects/{project_id}/upload-sessions/{upload_session_id}/parts/{part_number}/ack` | 分片确认 | `upload_part_conflict`、`upload_session_expired` |
| GET | `/projects/{project_id}/upload-sessions/{upload_session_id}/parts` | 已确认分片列表 | `resource_not_found` |
| POST | `/projects/{project_id}/upload-sessions/{upload_session_id}/complete` | 完成并登记/命中资产 | `upload_incomplete`、`size_mismatch`、`file_signature_mismatch`、`hash_mismatch`、`version_conflict` |
| POST | `/projects/{project_id}/upload-sessions/{upload_session_id}/abort` | 中止会话 | `state_transition_invalid`、`resource_immutable` |

### 9.3 RawAsset、Ingestion、Episode、血缘

| 方法 | 路径 | 说明 | 主要错误码 |
|---|---|---|---|
| GET | `/projects/{project_id}/raw-assets` | 资产列表/筛选 | `project_scope_denied` |
| GET | `/projects/{project_id}/raw-assets/{asset_id}` | 资产详情 | `resource_not_found` |
| GET | `/projects/{project_id}/raw-assets/{asset_id}/download-url` | 短期下载地址 | `forbidden`、`asset_quarantined`、`asset_tombstoned` |
| POST | `/projects/{project_id}/raw-assets/{asset_id}/revalidate` | 重新校验 | `state_transition_invalid`、`asset_not_usable` |
| POST | `/projects/{project_id}/raw-assets/{asset_id}/release-quarantine` | 隔离放行（双人） | `forbidden`、`quarantine_release_denied` |
| POST | `/projects/{project_id}/raw-assets/{asset_id}/tombstone` | 墓碑（双人） | `forbidden`、`state_transition_invalid` |
| POST | `/projects/{project_id}/raw-assets/{asset_id}/ingest` | 创建解析/切片 Job | `asset_not_usable`、`state_transition_invalid` |
| GET | `/projects/{project_id}/ingestion-jobs` | Job 列表 | `project_scope_denied` |
| GET | `/projects/{project_id}/ingestion-jobs/{job_id}` | Job 详情 | `resource_not_found` |
| POST | `/projects/{project_id}/ingestion-jobs/{job_id}/retry` | 重试 | `job_not_retryable`、`state_transition_invalid` |
| POST | `/projects/{project_id}/ingestion-jobs/{job_id}/cancel` | 取消 | `state_transition_invalid` |
| GET | `/projects/{project_id}/ingestion-jobs/{job_id}/log-url` | 日志短期地址 | `forbidden`、`resource_not_found` |
| GET/POST | `/projects/{project_id}/episodes` | 列表/创建切片 | `validation_failed`、`episode_overlap_conflict`、`calibration_not_active` |
| GET/PATCH | `/projects/{project_id}/episodes/{episode_id}` | 详情/修改允许的元数据 | `resource_immutable`、`version_conflict` |
| POST | `/projects/{project_id}/episodes/{episode_id}/actions/{action}` | `quality-check/accept/reject/complete/archive/invalidate/retry` | `state_transition_invalid`、`forbidden` |
| GET | `/projects/{project_id}/episodes/{episode_id}/manifest` | Manifest/Hash | `resource_not_found`、`manifest_hash_mismatch` |
| GET | `/projects/{project_id}/episodes/{episode_id}/download-url` | 数据下载 | `forbidden`、`asset_quarantined` |
| GET | `/projects/{project_id}/episodes/{episode_id}/lineage` | Episode 血缘 | `resource_not_found` |
| GET | `/projects/{project_id}/lineage/nodes/{node_type}/{node_id}` | 通用血缘遍历 | `resource_not_found`、`validation_failed` |

### 9.4 通用请求/响应

- 写动作：`If-Match: "<version>"`；缺失 428，失配 409。
- 幂等：`Idempotency-Key`；创建会话、完成上传、创建 Job、创建 Episode、状态动作必须支持。
- 分页：`cursor`、`limit`；响应 `{items, next_cursor}`。
- 异步：`POST complete`、`POST ingest`、`POST episodes` 返回 `202`/`201` + Job；客户端轮询 Job/资产/Episode。
- 错误体：沿用 `ErrorEnvelope` `{"error":{"code","message","request_id","details"?}}`；`details` 为可选兼容新增，字段级错误放其中，不得泄漏跨项目信息。

## 12. 可测试契约与验收映射

> 下列为契约测试/集成测试必须覆盖的用例；状态均为 `NOT RUN`（本会话无 Bash 工具，未执行任何测试）。

| ID | 用例 | 期望 | 映射 |
|---|---|---|---|
| S2-T01 | `DRAFT` 任务直接 `start` | `409 state_transition_invalid`；DB 状态不变 | AC-COL-03 |
| S2-T02 | `APPROVED` 任务直接 `submit` | 拒绝；仅允许 `ready→assign→start` | AC-COL-03 |
| S2-T03 | 任务 `CANCELLED` 后任意动作 | `409`；审计记录尝试 | AC-COL-03 |
| S2-T04 | 批次在任务 `APPROVED` 时 `start` | `409`；批次不得领先任务 | AC-COL-03 |
| S2-T05 | 同 `Idempotency-Key` 同请求创建会话 | 返回同一 `upload_session_id`；`Idempotent-Replayed: true` | AC-UPL-02 |
| S2-T06 | 同键不同请求体 | `409 idempotency_key_reused` | AC-UPL-02 |
| S2-T07 | 上传分片 1、3，缺 2 后 `complete` | `409 upload_incomplete`，details 含 `[2]` | AC-UPL-01 |
| S2-T08 | 补传分片 2 后 `complete` | `202`；资产 `VALIDATING`；已验证分片不重传 | AC-UPL-01 |
| S2-T09 | 同分片同 ETag/大小/Hash 重复 ack | 幂等成功，分片记录不重复 | AC-UPL-01 |
| S2-T10 | 同分片不同 Hash ack | `409 upload_part_conflict` | AC-UPL-01 |
| S2-T11 | 总大小与声明不符 | `422 size_mismatch`；会话仍可修正 | AC-UPL-03 |
| S2-T12 | 声明 MP4、实际文本 | `422 file_signature_mismatch`；无可用资产 | AC-UPL-03 |
| S2-T13 | 实际 Hash 与声明不符 | 资产 `REJECTED`，错误码 `hash_mismatch`；既有资产不受影响 | AC-UPL-03 |
| S2-T14 | 同任务重复上传相同内容 | 不新增可用资产；`deduplicated=true`；关联不重复 | AC-UPL-02 |
| S2-T15 | 项目 A 用户访问项目 B 资产/会话/下载 | 列表不泄漏；详情/下载 `404`；审计 | AC-UPL-12 |
| S2-T16 | 隔离资产创建 Job/Episode | `409 asset_not_usable`；下载拒绝 | AC-UPL-05 |
| S2-T17 | 同一人提案并批准隔离放行 | `403 forbidden`；双人审批失败 | AC-UPL-05 |
| S2-T18 | 墓碑资产下载 | `410 asset_tombstoned`；元数据/血缘仍可查 | AC-UPL-08 |
| S2-T19 | 非 VALID 资产创建 Episode | `409 asset_not_usable` | AC-UPL-07 |
| S2-T20 | Episode 时间范围超出资产 | `422 episode_time_range_invalid` | AC-UPL-07 |
| S2-T21 | 同资产重叠区间且 `allow_overlap=false` | `409 episode_overlap_conflict` | AC-UPL-07 |
| S2-T22 | Episode `READY` 后 PATCH 数据字段 | `409 resource_immutable`；Hash 不变 | AC-UPL-08 |
| S2-T23 | Episode 修订 | 新 `episode_id`、`episode_version+1`、`supersedes_id` 指向旧版；旧版不变 | AC-UPL-07 |
| S2-T24 | RawAsset 墓碑后 Episode | 非终态 Episode `INVALIDATED`；血缘追加失效边 | AC-UPL-08 |
| S2-T25 | 血缘正向/反向查询 | 可追溯到 RawAsset/任务/Job；无跨项目节点 | AC-UPL-06 |
| S2-T26 | 直接 SQL 非法状态更新 | DB 触发器拒绝；返回/记录 `state_transition_invalid` | AC-COL-03 |
| S2-T27 | 对象 Key 生成 | 含 `project_id`、无原始文件名/路径穿越；不符合正则拒绝 | AC-UPL-06 |
| S2-T28 | 过期会话继续分片/完成 | `410 upload_session_expired`；新建会话可恢复 | AC-UPL-04 |
| S2-T29 | `complete` 并发 | 仅一个成功；另一个幂等返回或 `409 version_conflict`；无重复可用资产 | AC-UPL-02 |
| S2-T30 | 审计 outbox 写入失败 | 业务事务回滚；状态不变 | AC-COL-13 |
