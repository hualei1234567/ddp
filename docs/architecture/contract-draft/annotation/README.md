# 阶段 3 标注契约草案（annotation）

- 状态：**FROZEN（`ddp.stage3/1.0.0`，DDP-323）**；本目录保留机器可读设计基线
- 版本：`ddp.stage3/1.0.0`
- 任务：DDP-315（P1，草案）；DDP-323（P0，冻结）
- Owner：System Architect
- 完整索引：`docs/architecture/阶段3-标注契约索引.md`
- 冻结文档：`docs/architecture/阶段3-契约冻结-v1.0.md`
- 主文档：`docs/product/阶段3-标注PRD.md`、`docs/product/阶段3-标注验收标准.md`
- ADR：`docs/adr/0011-stage3-annotation-contract.md`（ACCEPTED）

本目录保留阶段 3 的 OpenAPI、JSON Schema、状态机与错误码基线。已实现端点快照与
自包含基线以 `packages/contracts/` 为准；本目录不得反向覆盖实现快照。

- 实现快照：`packages/contracts/openapi.yaml`（`info.version=1.1.0`）
- 冻结基线：`packages/contracts/baselines/openapi.stage3.v1.0.0.bundle.yaml`
- 状态机与错误码已同步为 `ddp.stage3/1.0.0` / FROZEN；与实现差异见冻结文档第 5 节。
- 破坏性变更必须新建 ADR；未跑测试不得声称冻结或通过。

文件：

- `openapi.stage3.v1.yaml`
- `state-machines.v1.json`
- `error-codes.v1.json`
- `label-schema.v1.schema.json`
- `annotation-sample-selection.v1.schema.json`
- `annotation-task.v1.schema.json`
- `annotation-batch.v1.schema.json`
- `annotation-assignment.v1.schema.json`
- `annotation-submission.v1.schema.json`
- `annotation-record.v1.schema.json`
- `annotation-review.v1.schema.json`
- `annotation-arbitration.v1.schema.json`
- `gold-set.v1.schema.json`
- `quality-sample.v1.schema.json`
- `iaa-report.v1.schema.json`
- `annotation-workload.v1.schema.json`
- `annotation-lineage.v1.schema.json`

所有文件使用简体中文描述；枚举/错误码保留英文稳定标识。OpenAPI 通过相对 `$ref` 引用同目录 JSON Schema，校验器必须支持 OpenAPI 3.1 外部引用。

## 关键边界

1. 本目录不是已实现端点快照；阶段 3 端点尚未实现，不能伪装为可用。
2. 黄金集期望答案、他人草稿、他人工作量、跨项目 ID 不得进入普通响应。
3. 所有写动作使用 `If-Match`；缺失 428，失配 409；写动作支持 `Idempotency-Key`。
4. 状态迁移由领域动作 + DB 触发器双重兜底；非法迁移 `state_transition_invalid`。
5. 审计与 outbox 同事务；事件消费按 `event_id` 幂等；审计/血缘只追加。
6. 阶段 3 不做 DatasetVersion 冻结/导出；`INCLUDED_IN` 边由阶段 4 追加。
