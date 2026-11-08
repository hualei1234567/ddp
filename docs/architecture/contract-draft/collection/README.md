# 阶段 2 契约草案（collection）

- 状态：DRAFT，未冻结
- 版本：`ddp.stage2/1.0.0-draft`
- 完整索引：`docs/architecture/阶段2-契约索引.md`
- 主文档：`docs/architecture/阶段2-采集上传RawAsset-Episode契约-v1.md`

本目录只放阶段 2 的 OpenAPI、JSON Schema、状态机与错误码草案。按角色边界与契约 Owner 规则：

- **不要**直接把本目录内容复制到 `packages/contracts/`；
- 待用户批准、实现完成并通过契约测试后，由 System Architect/契约 Owner 统一升级 `packages/contracts/openapi.yaml` 与版本；
- 破坏性变更必须新建 ADR；未跑测试不得声称冻结或通过。

文件：

- `openapi.stage2.v1.yaml`
- `state-machines.v1.json`
- `error-codes.v1.json`
- `collection-task.v1.schema.json`
- `collection-batch.v1.schema.json`
- `upload-session.v1.schema.json`
- `upload-complete-request.v1.schema.json`
- `raw-asset.v1.schema.json`
- `ingestion-job.v1.schema.json`
- `episode.v1.schema.json`
- `lineage-edge.v1.schema.json`
- `object-key.v1.schema.json`

所有文件使用简体中文描述；枚举/错误码保留英文稳定标识。OpenAPI 通过相对 `$ref` 引用同目录 JSON Schema，校验器必须支持 OpenAPI 3.1 外部引用。
