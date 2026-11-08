# 阶段 4 数据集与训练契约草案（dataset）

- 状态：**DRAFT（`ddp.stage4/1.0.0-draft`，DDP-325）**
- 任务：DDP-325
- Owner：System Architect
- 完整索引：`docs/architecture/阶段4-数据集与训练契约索引.md`
- ADR：`docs/adr/0012-stage4-dataset-training-contract.md`（Proposed）
- 主文档：`docs/product/阶段4-数据集与训练支撑PRD.md`、`docs/product/阶段4-数据集与训练支撑验收标准.md`
- 实现基线：`main@659fe2b`；**阶段 4 无实现，端点/字段/错误码均不代表可用**。

本目录保留阶段 4 的 OpenAPI、JSON Schema、状态机与错误码设计基线。未修改
`packages/contracts/**`、`backend/**`、`apps/web/**` 或迁移；实现快照尚未生成。

文件：

- `openapi.stage4.v1.yaml`
- `state-machines.v1.json`
- `error-codes.v1.json`
- `dataset.v1.schema.json`
- `dataset-version.v1.schema.json`
- `dataset-item.v1.schema.json`
- `selection-preview.v1.schema.json`
- `dedup-policy.v1.schema.json`
- `split-policy.v1.schema.json`
- `leakage-policy.v1.schema.json`
- `leakage-report.v1.schema.json`
- `quality-report.v1.schema.json`
- `manifest.v1.schema.json`
- `export-job.v1.schema.json`
- `training-request.v1.schema.json`
- `training-request-revision.v1.schema.json`
- `training-delivery.v1.schema.json`
- `external-training-reference.v1.schema.json`
- `dataset-grant.v1.schema.json`

所有文件使用简体中文描述；枚举/错误码保留英文稳定标识。OpenAPI 通过相对 `$ref`
引用同目录 JSON Schema，校验器必须支持 OpenAPI 3.1 外部引用。

## 关键边界

1. 本目录不是已实现端点快照；阶段 4 尚未实现，不能伪装为可用。
2. 不建设训练集群、GPU 调度、分布式训练或模型权重托管；只保存外部训练引用。
3. 禁止绕开 Episode 直接从 RawAsset 生成训练集；禁止把未批准标注写入训练集。
4. `FROZEN` DatasetVersion 不可原地修改；变化创建新版本。
5. 泄漏硬规则不可豁免；`FAIL/UNKNOWN` 阻断冻结/导出；软规则默认告警，PO 豁免需审计。
6. 所有写动作使用 `If-Match`；缺失 428，失配 409；写动作支持 `Idempotency-Key`。
7. Adapter 回调使用 HMAC-SHA256 + 时间窗 + nonce + 幂等键 + 重放防护。
8. 审计与 outbox 同事务；事件消费按 `event_id` 幂等；审计/血缘只追加。
9. LeRobot `ddp.lerobot/1.0.0` 在真实 Loader 验证前标 `未验证`，不得宣称已兼容。
10. 未跑测试不得声称冻结或通过；真实训练交付当前 `BLOCKED`。

## 解析自检

```bash
python3 - <<'PY'
import json, pathlib, yaml
for p in pathlib.Path('.').glob('*.json'):
    json.loads(p.read_text())
for p in pathlib.Path('.').glob('*.yaml'):
    yaml.safe_load(p.read_text())
print('parse ok')
PY
```
