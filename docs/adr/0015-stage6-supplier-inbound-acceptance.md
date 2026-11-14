# ADR-0015 阶段 6 供应商与到货验收域决策

- 状态：**ACCEPTED（设计已定，实现随 DDP-360 落地）**
- 日期：2026-11-14
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：ADR-0002、ADR-0003、ADR-0004、ADR-0012、ADR-0014；
  `docs/product/阶段6-供应商与到货验收PRD.md`；`docs/product/阶段6-供应商与到货验收标准.md`；
  总规范 §12.3、§12.4、§13.1、§15、§16、§18
- 边界：本 ADR 只定义供应商与到货验收域的领域边界与契约原则；不修改阶段 2–5 既有契约。

## 1. 背景

总规范 §12.3 要求供应商档案、合作项目、任务分配、数据交付、质量、交付周期、返工、
成本与绩效；§12.4 要求每次交付记录交付单、数据集版本、清单、质量报告、Manifest、
接收方、验收状态、意见与返工。

现状：阶段 4 已有 `TrainingDelivery`（我方 → 训练方的数据交付），阶段 2 已有
`RawAsset` 与上传链路。**缺的是上游「供应商 → DDP」的到货验收入口**。

## 2. 关键命名冲突与决策

`Delivery` 在 §12.4 同时覆盖「我方交付训练方」与「供应商交付我方」。为不破坏已冻结的
`ddp.stage4/1.0.0`（`TrainingDelivery`），本域采用不冲突命名：

| 本文对象 | 路由 | 说明 |
|---|---|---|
| `Supplier` | `/suppliers` | 供应商档案 |
| `DeliveryBatch` | `/inbound-batches` | 供应商的一次到货批次 |
| `InboundSegment` | `/inbound-batches/{id}/segments` | 批次内片段 |
| `AcceptanceLedgerEntry` | `/acceptance-ledger` | 只追加核验账本 |

**决策**：不复用 `TrainingDelivery`，不共用表；两者仅通过项目与 RawAsset 关联。
理由：方向相反、状态机不同、权限主体不同（训练交付的验收方是算法方，到货验收的
裁决方是质检/项目方）。强行合并会造成状态机与权限耦合，违反 ADR-0014 的"规则单一权威"。

## 3. 数据边界

- 片段只保存 `raw_asset_id` 引用与诊断快照（`diagnostics` JSONB），**不复制**数据。
- 诊断快照由外部系统产出（画面工具、VLM 初筛），DDP 只存不解释，契约标注为只读。
- 账本表 `acceptance_ledger_entries` 只追加，数据库触发器禁止 UPDATE/DELETE。

## 4. 状态机

见 PRD §5。终审判定由服务端按片段统计计算，**不接受客户端指定**，
避免"人工把全部拒收说成通过"。

## 5. 权限与职责分离

- 供应商档案创建/停用：Owner；登记与改档：Editor。
- 批次登记：Editor；终审：Owner（高风险放行）。
- 片段裁决：Editor，且**裁决人不得与片段登记人相同**（复用阶段 4 的职责分离模式）。
- 账本导出：Owner；查询：全体成员。

## 6. 复用清单

| 能力 | 复用源 |
|---|---|
| 项目作用域与 RBAC | `app/api/deps.get_project_scope/get_write_scope` |
| 乐观锁 | `app/api/v1/preconditions.require_expected_version` |
| 幂等 | `app/application/datasets/idempotency.IdempotencyGuard` |
| 审计/outbox | `app/application/datasets/audit.record_change` |
| 分页 | `app/api/v1/dataset_pagination` |
| 错误映射 | `app/api/errors.py`（新增 `supplier_error_to_app`） |
| 前端骨架 | `PageContainer`、`ApiErrorState`、`useCursorPage`、`useRequest`、`validation.ts` |

## 7. 影响与风险

- 新增 5 张表与 1 个迁移（`0022_supplier_inbound`），只新增结构、不回填历史。
- 风险：供应商主数据与外部系统不一致 → 用 `external_ref` 字段保留外部键，冲突时不覆盖。
- 风险：诊断快照体量 → 只存摘要与对象存储键，不存全量帧数据。

## 8. 变更记录

| 日期 | 变更 | 任务 |
|---|---|---|
| 2026-11-14 | 首次决策：命名、边界、状态机、权限与复用清单 | DDP-360 |
