# DDP 阶段 6 供应商与到货验收 PRD

> 文档编号：PRD-DDP-360
> 任务：DDP-360｜阶段：6（规模化运营）｜版本：V1.0｜日期：2026-11-14
> Owner：Product Manager｜批准：用户（长期授权）
> 关联：`docs/DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md` §12.3 供应商、§12.4 交付、
> §13.1 指标域、§18 UI/UX；`docs/adr/0014-reuse-first-engineering-standard.md`
> 状态：**设计基线**；实现完成后由验收标准给出逐项结果。

## 1. 背景与问题

阶段 2–5 已打通「采集 → 上传接入 → Episode → 标注 → 数据集 → 训练交付 → 模型闭环」，
但**上游数据由外部供应商大批量交付**这一入口尚未纳入平台：供应商发来的数据包只有
外部系统的本地目录与人工 Excel 台账，DDP 无法回答：

1. 这批数据是谁交付的、合同/合作项目是什么、计划多少、实际到多少？
2. 每一个片段是否通过人工审看？拒收原因是什么？谁签的字？
3. 供应商的交付及时率、一次通过率、返工率、平均周期是多少？
4. 通过验收的片段如何进入既有 RawAsset → Episode 链路，而不是另起一套数据湖？

本 PRD 建设**供应商档案 + 到货批次 + 片段验收 + 核验账本 + 供应商绩效**，
作为阶段 2 数据接入的**上游闸门**。

## 2. 目标与非目标

### 2.1 目标（P0）

| ID | 目标 | 可验证结果 |
|---|---|---|
| G1 | 供应商档案可管理 | 供应商 CRUD + 状态机 + 项目归属 |
| G2 | 到货批次可登记与追踪 | 批次状态机、计划/实到量、供应商与项目隔离 |
| G3 | 片段可逐条人工验收 | 通过 / 拒收 / 待补件三种裁决，必须写台账 |
| G4 | 验收核验账本不可篡改 | 只追加、无更新/删除通道、可导出 |
| G5 | 供应商绩效可量化 | 及时率、通过率、返工率、平均周期；缺失值不得显示为 0 |
| G6 | 通过验收的片段可进入既有链路 | 与 RawAsset 显式关联，不复制数据 |

### 2.2 非目标

- 不做供应商结算、发票、合同财务核算（财务系统边界）。
- 不托管供应商原始数据副本；只在对象存储保存引用与 Hash。
- 不做 AI 自动放行：模型初筛只是**建议**，人工裁决是唯一放行依据。
- 不做供应商门户/外部账号体系（P1 评估）。
- 不修改供应商网络、传输链路与外部系统。

## 3. 角色

| 角色 | 权限 |
|---|---|
| Project Owner | 供应商档案创建/停用、批次验收终审、账本导出 |
| Project Editor | 供应商登记、批次登记、片段裁决、待补件 |
| Viewer | 只读查看供应商、批次、片段、绩效 |
| Supplier Manager（阶段 6 启用） | 同 Editor，并负责供应商主数据；本版本映射到 Editor |

权限由服务端 RBAC 强制，前端只按 `allowed_actions` 渲染。

## 4. 核心对象

| 对象 | 说明 | 关键约束 |
|---|---|---|
| Supplier | 供应商档案 | 项目内 `code` 唯一；状态机；联系人不入库敏感信息 |
| DeliveryBatch | 一次到货批次 | 关联 supplier + 项目；计划/实到计数；状态机 |
| InboundSegment | 批次内单个片段 | 关联 RawAsset（可空）；唯一 `(batch_id, segment_code)` |
| SegmentDecision | 片段验收裁决 | 只追加；裁决人不得与登记人相同（职责分离） |
| AcceptanceLedgerEntry | 核验账本 | 只追加；记录动作、前后状态、快照 Hash、request_id |

## 5. 状态机

### 5.1 Supplier

```text
DRAFT → ACTIVE → SUSPENDED → ACTIVE
ACTIVE/SUSPENDED → TERMINATED（终态）
```

### 5.2 DeliveryBatch

```text
ANNOUNCED → IN_TRANSIT → ARRIVED → SCREENING
SCREENING → ACCEPTED / PARTIALLY_ACCEPTED / REJECTED
REJECTED → REWORK → SCREENING
ACCEPTED / PARTIALLY_ACCEPTED → CLOSED（终态）
```

规则：

- 只有 `SCREENING` 状态才能裁决片段。
- 全部片段 `ACCEPTED` → 只能 `ACCEPTED`；部分通过 → `PARTIALLY_ACCEPTED`；
  全部 `REJECTED` → `REJECTED`。由服务端按片段统计自动判定，不接受客户端指定。
- 有 `NEED_MORE` 片段时不允许批次终审。

### 5.3 InboundSegment

```text
REGISTERED → PENDING_REVIEW → ACCEPTED / REJECTED / NEED_MORE
NEED_MORE → PENDING_REVIEW（补件后重新提交）
ACCEPTED / REJECTED → 终态
```

## 6. 关键流程

### 6.1 到货验收（对应参考界面的「片段审片 + 人工裁决」）

1. Editor 创建供应商（首次）与到货批次，登记计划量与 OSS 目录引用。
2. 批次进入 `ARRIVED` 后，Editor 批量登记片段（片段编码 + RawAsset 引用 + 诊断快照）。
3. 批次转 `SCREENING`，逐条审看：每片段必须裁决 `ACCEPTED / REJECTED / NEED_MORE`，
   必须填写原因（拒收与待补件强制）。
4. 全部片段裁决完后请求批次终审，服务端按统计判定 `ACCEPTED /
   PARTIALLY_ACCEPTED / REJECTED`。
5. 每次登记与裁决写入 `AcceptanceLedgerEntry`，不可修改、不可删除。

### 6.2 待补件返工

`NEED_MORE` 片段由供应商补件后，Editor 更新其 RawAsset 引用并重新提交 `PENDING_REVIEW`；
批次可整体 `REWORK → SCREENING`。

### 6.3 进入既有链路

片段验收通过后，通过 `raw_asset_id` 指向阶段 2 的 RawAsset；DDP 不复制数据。
批次终审通过时，服务端校验所有 `ACCEPTED` 片段都已有 RawAsset 引用，否则拒绝终审。

## 7. 指标口径（§13 治理）

| 指标 | 定义 | 缺失处理 |
|---|---|---|
| 及时率 | 实到日期 ≤ 计划日期的批次数 / 总批次数 | 无计划日期 → `N/A` |
| 一次通过率 | 首次终审即 `ACCEPTED` 的批次 / 已终审批次 | 无终审批次 → `N/A` |
| 片段通过率 | `ACCEPTED` 片段 / 已裁决片段 | 无裁决 → `N/A` |
| 返工率 | 发生过 `REWORK` 的批次 / 已终审批次 | 无终审 → `N/A` |
| 平均验收周期 | 终审时间 − 到货时间，取均值 | 无终审 → `N/A` |

**缺失数据不得显示为 0**，必须区分 `0` 与 `N/A`；估算值与真实值分开标注。

## 8. 页面

| 路由 | 页面 | 说明 |
|---|---|---|
| `/suppliers` | 供应商列表 | 档案、状态、合作批次、通过率 |
| `/suppliers/:id` | 供应商详情 | 档案、绩效、批次列表、账本 |
| `/inbound-batches` | 到货批次列表 | 状态、供应商、计划/实到、及时率 |
| `/inbound-batches/:id` | 到货批次详情 | 批次概览、片段列表、终审 |
| `/inbound-batches/:id/segments/:segmentId` | 片段审片 | 参考界面：画面区 + 诊断面板 + 裁决区 |
| `/acceptance-ledger` | 验收核验账本 | 只读流水，支持按批次/供应商过滤 |

每个页面覆盖 Loading / Empty / Error / Forbidden / Partial / Stale / Disabled。

## 9. 复用清单（依 ADR-0014 审计结论）

| 需求 | 复用 | 不新建 |
|---|---|---|
| 项目隔离与 RBAC | `get_project_scope` / `get_write_scope` / `ProjectScope` | — |
| 乐观锁与幂等 | `require_expected_version` / `IdempotencyGuard` | — |
| 审计与 outbox | `app/application/datasets/audit.record_change` | — |
| 分页 | `dataset_pagination.build_page` | — |
| 错误映射 | `app/api/errors.py` 注册表 | — |
| 前端页面骨架 | `PageContainer` / `ApiErrorState` / `useCursorPage` / `useRequest` | — |
| 前端解析器 | `services/validation.ts` | 新增 `supplierParsers` |
| 数据载体 | `RawAssetModel` / `RawAsset` | 不复制数据 |

## 10. 风险

| 风险 | 缓解 |
|---|---|
| 与训练 `Delivery` 命名混淆 | 命名为 `DeliveryBatch` / `InboundSegment`，路由 `/inbound-batches` |
| 账本被误改 | 数据库触发器禁更新/删除 + 应用层无写通道 |
| AI 初筛被当作放行 | 诊断字段只读、裁决必须人工、`decided_by` 必填 |
| 绩效指标虚高 | 缺失返回 `null`，前端显示「暂无」 |
