# API、事件、状态机与契约演进建议

## 1. 约束

本文件给出逻辑 API 契约建议，不表示这些路径当前已经存在。Claude 必须先读取 OpenAPI、Router、UseCase 和 Client，逐项映射现有接口。存在等价能力时复用现有路径，不得重复造 API。

所有 API 变更必须同步：`Domain → Application → API → OpenAPI → Frontend Types → Client → Tests → Contract Baseline`。

## 2. 现有 API 家族（审计可见）

- `/devices`、`/sensors`、`/robots`、`/calibration_profiles`；
- `/collection/*`；
- `/raw-assets`；
- `/episodes`；
- `/annotation-tasks` 及 annotation quality 相关接口；
- `/datasets`；
- `/training-requests`；
- `/models`、`/evaluations`、`/inference-*`。

## 3. 建议补齐的资源关系

CaptureSession、EdgeAgent/EdgeDeviceIdentity、UploadSession/UploadManifest、AnnotationTaskSpec/SchemaVersion、AnnotationAssignment、AnnotationResultVersion、ReviewRecord/QualityIssue、DatasetVersion Manifest/ExportJob。

## 4. API 家族建议（路径须与现有风格合并）

- Capture：创建/查询/结束 Session、Preflight、Manifest 提交；
- Upload：申请上传、完成分片、完成校验、查询状态；
- Annotation Batch：从 Episode 集合创建任务批次；
- Assignment：指定、批量指定、转派、回收；
- My Work：我的任务、打开任务、保存草稿、提交；
- Review：待审核队列、详情、Accept、Rework、Arbitrate；
- Dataset Builder：候选查询、预览、创建版本、冻结、导出；
- Edge Agent：注册、心跳、能力、撤销。

## 5. 事件建议

使用稳定常量，不用无法静态审计的动态 f-string：
- `capture.session.created`
- `capture.session.finalized`
- `capture.manifest.received`
- `upload.session.created`
- `upload.asset.verified`
- `ingestion.job.queued`
- `processing.run.completed`
- `episode.created`
- `annotation.batch.created`
- `annotation.task.assigned`
- `annotation.result.submitted`
- `annotation.review.accepted`
- `annotation.review.rework`
- `dataset.version.frozen`
- `dataset.export.completed`

事件至少包含 event_id、event_type、schema_version、aggregate_id、project_id、occurred_at、actor_id、correlation_id 和 payload_version。事件消费者必须幂等。

## 6. 状态机统一

状态只能由领域用例转换。前端调用动作 API，不允许 PATCH 任意 status。现有状态机若已覆盖等价语义，扩展现有状态机，不得再建平行状态机。

## 7. 错误响应

统一提供 error_code、message、details、field_path、request_id、trace_id 和 retryable。不得回显 Secret、签名 URL 或原始异常堆栈。

## 8. 契约版本

新增/变更契约需生成 OpenAPI diff、检查破坏性变更、更新冻结 baseline、制定旧客户端兼容策略、执行 Migration upgrade/downgrade/re-upgrade，并由 GitHub CI 验证。
