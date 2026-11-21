# Manifest 样例与核心数据字典

## 1. Capture Manifest

见 `03_端侧机器人采集Agent与硬件适配方案.md`。Manifest 版本化，文件列表与 SHA256 必须对应实际文件。

## 2. 核心关系与基数建议

| 关系 | 基数 | 说明 |
|---|---|---|
| Project → CollectionTask | 1:N | 项目下多个采集任务 |
| CollectionTask → CaptureSession | 1:N | 一任务可多次执行/补采 |
| CaptureSession → RawAsset | 1:N | 一次采集生成多个文件 |
| RawAsset → ProcessingRun | 1:N | 同一原始资产可被不同版本 Pipeline 处理 |
| ProcessingRun → Episode | 1:N | 一次处理生成多个 Episode |
| Episode → AnnotationTask | 1:N | 可按不同 Schema/用途重复标注，但需显式版本/原因 |
| AnnotationTask → Assignment | 1:N history | 允许转派，任一时刻一个 active primary assignee |
| AnnotationTask → AnnotationResult | 1:N | 草稿与提交版本 |
| AnnotationResult → ReviewRecord | 1:N | 可多次审核/返工 |
| Dataset → DatasetVersion | 1:N | Dataset 逻辑集合的多个冻结版本 |
| DatasetVersion → DatasetItem | 1:N | 冻结的 Episode/Result 引用 |
| DatasetVersion → ExportJob | 1:N | 多种格式/多次导出 |

## 3. AnnotationTask Manifest 示例

```json
{
  "annotation_task_manifest": "ddp.annotation-task/1.0.0",
  "batch_id": "uuid",
  "project_id": "uuid",
  "task_spec": {"spec_id": "uuid", "version": "1.0.0"},
  "label_schema": {"schema_id": "uuid", "version": "1.0.0"},
  "items": [
    {
      "annotation_task_id": "uuid",
      "episode_id": "uuid",
      "clip": {"start_ns": 0, "end_ns": 12000000000},
      "assignee_id": "uuid",
      "due_at": "RFC3339"
    }
  ],
  "created_by": "uuid",
  "created_at": "RFC3339"
}
```

## 4. Dataset Manifest 示例

```json
{
  "manifest_schema": "ddp.dataset-manifest/1.0.0",
  "dataset_id": "uuid",
  "dataset_version": "1.0.0",
  "canonical_schema_version": "1.0.0",
  "adapter_versions": ["lerobot-v3:1.0.0"],
  "pipeline": {"id": "ddp-default", "version": "1.0.0", "parameters_hash": "sha256"},
  "label_schema": {"id": "uuid", "version": "1.0.0"},
  "quality_rules": [{"id": "annotation-completeness", "version": "1.0.0"}],
  "splits": {"train": ["episode-id-1"], "validation": ["episode-id-2"], "test": ["episode-id-3"]},
  "items": [{"episode_id": "uuid", "raw_asset_ids": ["uuid"], "annotation_result_id": "uuid", "annotation_result_version": 2, "review_status": "ACCEPTED", "content_hash": "sha256"}],
  "license_summary": ["CC-BY-4.0"],
  "created_by": "uuid",
  "created_at": "RFC3339",
  "manifest_hash": "sha256"
}
```

以上是逻辑样例，字段最终以仓库契约、Schema 和数据模型审查为准。不得未经 ADR 直接按样例创建同名数据库表。
