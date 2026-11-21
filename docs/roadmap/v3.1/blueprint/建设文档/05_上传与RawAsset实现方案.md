# 数据上传、断点续传与 RawAsset 实施方案

## 1. 复用基线

已知现有上传链路包含预签名直传、分片上传绑定、`uploadQueue.ts` / `uploadTransfer.ts` / `sha256.ts`、`useUploadQueue`、`infrastructure/storage/s3.py`、MinIO / S3 兼容对象存储和 Ingestion Application / RawAsset API。

V3.1 不重写上传内核。重点是让 Edge Agent 采用同一服务端上传协议，并把上传结果与 CaptureSession / CollectionTask / Manifest 关联。

## 2. 上传业务步骤

```text
Edge Local Spool
  ↓ request upload intent
DDP creates UploadSession
  ↓ returns object key + presigned multipart instructions
Edge uploads parts
  ↓ report completed parts
DDP completes multipart
  ↓ server verifies size / checksum / content signature
RawAsset REGISTERED
  ↓ outbox event
IngestionJob QUEUED
```

## 3. Upload 状态机

建议状态：`CREATED → INITIATED → UPLOADING → PARTS_COMPLETE → VERIFYING → VERIFIED → INGESTION_QUEUED`。

异常：`INTERRUPTED / RETRYABLE_FAILED / PERMANENT_FAILED / CANCELLED / QUARANTINED`。

- 网络中断不等于上传失败；保存已完成分片。
- 重试使用同一 UploadSession / Idempotency-Key。
- 已存在同 Hash 且同权限范围的对象可复用，但必须保留新的业务引用，不可误删原对象。
- 校验失败进入 QUARANTINED，不进入可训练数据集。
- 完成上传不等于解析成功；解析成功不等于质量通过。

## 4. UploadSession 最小字段

`upload_session_id`、`project_id`、`collection_task_id`、`capture_session_id`、`edge_agent_id`、`manifest_id/manifest_hash`、`expected_asset_count`、`expected_total_bytes`、`upload_protocol`、`status`、`completed_parts`、`expires_at`、`idempotency_key`、`created_by/created_at`、`verified_at`。

如果现有 Upload Session 模型已具备等价字段，直接复用并补关联，不新建重复实体。

## 5. RawAsset 生命周期

```text
DISCOVERED → UPLOADING → UPLOADED → VERIFYING → VERIFIED → INGESTION_PENDING → PARSED
```

异常：`CORRUPT / QUARANTINED / PARSE_FAILED / DELETED_BY_RETENTION_POLICY`。

RawAsset 只代表原始文件和元数据。处理后产物必须另建 ProcessedAsset / Artifact 引用，禁止覆盖 Raw。

## 6. Manifest 校验

服务端校验 Manifest Schema 版本、CaptureSession / CollectionTask 归属、文件相对路径无路径穿越、文件数量和大小、SHA256、MIME / magic bytes、允许文件类型、设备/传感器引用、时间范围、重复资产和外部数据来源许可。

Manifest 和文件校验通过后才能创建正式 RawAsset 引用。

## 7. 安全

预签名 URL 短时有效；不在日志中输出签名 URL 和 Token；object key 不接受用户任意路径；上传大小、扩展名、MIME 和文件签名交叉校验；访问对象必须经过 ProjectScope；不允许前端/Edge Agent 直接写数据库；不允许客户端自行指定 verified 状态。

## 8. 失败恢复

| 故障 | 系统行为 |
|---|---|
| 网络断开 | 保留本地 Spool 和分片进度 |
| Agent 重启 | 从本地 Upload State 恢复 |
| URL 过期 | 重新申请该分片授权，不重新创建 Session |
| 分片重复 | 幂等接受或返回已完成状态 |
| Hash 不匹配 | QUARANTINED，禁止进入处理 |
| MinIO 临时不可用 | Retryable，指数退避+抖动 |
| Manifest 不合法 | Permanent failure，返回字段路径 |
| 文件部分缺失 | Session PARTIAL，展示缺失清单 |
| 重复提交 | Idempotency 防重 |

## 9. 验收

Edge 与 Web 使用同一套后端上传权威规则；上传中断恢复成功；完整性验证通过；RawAsset、CaptureSession、CollectionTask 三者关联；重复请求不生成重复业务记录；MinIO 对象可回读；上传失败与解析失败状态区分；原始对象不可被处理流程覆盖。
