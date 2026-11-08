# ADR-0011 阶段 3 标注域契约与质量闭环决策

- 状态：**已提议（PROPOSED，待用户批准；未冻结）**
- 日期：2026-11-08
- 决策者：System Architect（待确认）；批准人：待用户
- 任务：DDP-315（P1）
- 起草：Product Manager（按任务要求起草草案，架构决策需 System Architect 评审）
- 关联：ADR-0002、ADR-0003、ADR-0004、ADR-0005、ADR-0010；`docs/product/阶段3-标注PRD.md`；`docs/product/阶段3-标注验收标准.md`；`docs/architecture/阶段3-标注契约索引.md`；`docs/architecture/contract-draft/annotation/*`
- 契约草案版本：`ddp.stage3/1.0.0-draft`
- 边界：本 ADR 只做设计与契约决策；不修改 `backend/**`、`apps/web/**`、`backend/migrations/**`、`packages/contracts/**`，不执行迁移或部署。

## 1. 背景

阶段 2 已冻结采集、上传、RawAsset 与基础 Episode 的草案：Episode `READY` 冻结数据字段，`ACCEPTED/COMPLETED` 可进入下游，血缘边只追加，所有对象强制 `project_id`。阶段 3 需要把 Episode 转成可审核、可仲裁、可量化的标注结果。若不在阶段 3 入口冻结以下语义，后续会出现：

- 标签 Schema 被前端或数据库静默修改，历史标注不可比；
- 样本选择无快照，任务无法复现，指标分母漂移；
- 提交可原地覆盖，复核/仲裁无法追溯；
- 标注员自审、复核人自仲裁，质量结论失去可信度；
- 黄金集答案泄漏，准确率失真；
- IAA 用黄金集或非独立标注冒充，指标误导；
- 抽检无策略、无理由，无法解释质量结论；
- 审计/outbox 与业务不同事务，状态变化无证据；
- 血缘缺少标注节点，阶段 4 数据集无法追溯。

## 2. 决策

### 2.1 标注域独立且以 Episode 为唯一输入

- 新增 Annotation 领域模块，包含 Schema、样本集、任务、批次、分配、提交、记录、复核、仲裁、黄金集、抽检、IAA。
- 标注输入必须是阶段 2 的 Episode；不得直接标注 RawAsset、对象 Key 或未形成 Episode 的文件。
- Episode 数据字段与原始资产只读；阶段 3 只维护 `annotation_status` 投影与追加血缘/审计。
- 理由：保持 Raw/Canonical/Processed/Labeled 分层；避免标注绕过切片、标定与 Manifest。

### 2.2 标签 Schema 版本化、发布不可变

- Schema 状态 `DRAFT → PENDING_REVIEW → PUBLISHED → DEPRECATED`，安全原因可 `REVOKED`。
- 发布后标签定义、类型、枚举、范围、必填不可原地修改；修改创建新 `schema_version`。
- 任务批准时冻结 `schema_id + schema_version + schema_sha256`；提交必须引用同一快照。
- `GOLD_ONLY/SYSTEM_ONLY` 标签不得出现在标注员响应。
- 理由：保证历史标注可解释、IAA 可比、黄金集不泄漏。

### 2.3 样本集快照与输入门禁

- 样本选择支持预览、去重、分层、快照；快照后清单与 Hash 固定，变更创建新样本集。
- 默认只接受 Episode `ACCEPTED/COMPLETED` 且 `PASS/WARN`；`WARN` 需 AM 确认；不合格 Episode 默认拒绝。
- 默认按 `episode_lineage_id` 去重；同一 Episode 不同时间片段需显式允许。
- 理由：保证任务可复现、指标分母稳定、避免重复样本污染 IAA 与训练。

### 2.4 任务/批次/分配分离与容量治理

- 任务表达目标与策略；批次表达分层执行；分配表达人员工作量；三者状态独立但有兼容约束。
- 任务必须绑定已快照样本集与已发布 Schema；批次不得超样本集；任务取消级联取消批次。
- 分配/领取校验项目成员、任务状态、容量、过期与职责分离；超容量需 AM 审批。
- 理由：避免把所有状态塞进一个实体；支持分层、分批与工作量治理。

### 2.5 提交版本化、复核与仲裁分离

- 提交后不可原地编辑；修订创建新 `submission_version`；旧版本保留；服务端计算并校验 `records_digest`。
- 复核人不得是标注人；仲裁人不得是标注人或首轮复核人；仲裁有法定人数与最终结论。
- 驳回必须有问题项、严重级别、定位与原因；通过必须记录检查项；升级必须给触发原因。
- 理由：保证质量结论可追溯、职责分离有效、争议可闭环。

### 2.6 黄金集隐藏、IAA 严格重叠

- 黄金集 `blind=true`；期望答案仅 AM/QI/AR/AU 与授权 PO 可见；不得进入标注员响应、日志、导出与前端缓存。
- 黄金集用于准确率，不用于 IAA；黄金集注入应隐藏，标注员不得识别。
- IAA 只在同任务、同 Schema 版本、至少两名标注员独立标注的重叠样本上计算；样本不足返回 `INSUFFICIENT_DATA`，不显示 0。
- 指标支持一致率、Cohen/Fleiss Kappa、Krippendorff Alpha、Span F1、边界 MAE；指标定义版本化。
- 理由：防止答案泄漏与指标误用；保证 IAA 反映真实标注员间一致性。

### 2.7 质检抽检策略化

- 抽检支持随机、分层、风险、黄金集、IAA 重叠样本；抽样策略与理由版本化。
- 抽检人不得是标注人；通过/不通过/转仲裁/返工闭环；结论必须留痕。
- 抽检通过不等于所有提交通过；抽检失败必须返工或升级。
- 理由：在成本与质量之间建立可解释、可复现的抽样治理。

### 2.8 权限、项目隔离与数据最小化

- 所有对象强制 `project_id`；父引用使用 `(project_id, id)` 复合外键；跨项目统一 404/403。
- 服务端权威 RBAC；前端隐藏不构成权限；黄金集答案、他人草稿、他人工作量、跨项目 ID 不进入普通响应。
- PA 访问项目业务数据需 break-glass 并强审计。
- 理由：沿用阶段 1/2 的隔离原则；标注数据含客户场景、人员与质量信息，必须最小必要。

### 2.9 审计、outbox 与 DB 兜底

- 状态迁移、分配/领取/撤销、提交、复核、仲裁、黄金集访问、抽检、Schema 发布、IAA、导出与跨项目拒绝写审计。
- 业务事务与 outbox 同事务；事件含 `event_id/event_type/event_version/occurred_at/producer/aggregate_type/aggregate_id/project_id/correlation_id/causation_id/payload/schema_version`；消费按 `event_id` 幂等。
- 状态迁移由领域动作 + DB 触发器双重校验；触发器要求 `ddp.actor_id`/`ddp.request_id`/`ddp.project_id`，缺失拒绝。
- 提交、批准记录、审计、血缘边禁止 DELETE；冻结字段由触发器保护。
- 理由：防止直写绕过、审计缺失与事件重复。

### 2.10 血缘扩展与版本

- 阶段 3 新增节点：`ANNOTATION_SCHEMA/ANNOTATION_SAMPLE_SET/ANNOTATION_TASK/ANNOTATION_BATCH/ANNOTATION_ASSIGNMENT/ANNOTATION_SUBMISSION/ANNOTATION/ANNOTATION_REVIEW/ANNOTATION_ARBITRATION/GOLD_SET/QUALITY_SAMPLE/IAA_REPORT`。
- 新增关系：`SAMPLED_FROM/SPLIT_INTO/ASSIGNED_TO/SUBMITTED_AS/CONTAINS/REVIEWED_BY/ESCALATED_TO/RESOLVED_BY/SUPERSEDES/USES_SCHEMA/EVALUATED_AGAINST/QUALITY_SAMPLED/USED_FOR_IAA/INCLUDED_IN`。
- 不修改阶段 2 封闭枚举；通过 `annotation-lineage.v1.schema.json` 新版本扩展；实施时评估 `lineage_node_refs` 约束迁移与契约版本升级。
- 边只追加；跨项目禁止；成环禁止（`SUPERSEDES` 线性）；失效追加 `INVALIDATES`。
- 理由：阶段 4 数据集/训练必须能从标注结果反向追溯到 Episode/RawAsset。

### 2.11 错误码与版本

- 新增标注错误码见 `error-codes.v1.json`；客户端按 `code` 分支；未知 code 不得默认成功。
- 契约草案版本 `ddp.stage3/1.0.0-draft`；HTTP 主版本仍 `/api/v1`。
- 破坏性变更（删除/改名/改类型/改语义/必填化/状态迁移变化/血缘枚举变化）必须新 ADR + 用户批准。
- 理由：保证前后端与契约测试可稳定演进。

## 3. 后果

### 3.1 正面

- 标注 Schema、样本、任务、提交、复核、仲裁、黄金集、IAA、抽检均有版本与快照，历史可复现。
- 职责分离、黄金集防泄漏、项目隔离有明确边界与测试目标。
- 审计/outbox/血缘支持阶段 4 数据集与训练追溯。
- 状态机与错误码可作为前后端并行与契约测试基线。

### 3.2 代价与约束

- 需要 Schema 表单渲染、视频/片段预览、草稿保存、IAA 计算与黄金集脱敏，前端复杂度上升。
- 需要审计/outbox、幂等、乐观锁、DB 触发器与复合外键；写路径复杂。
- 黄金集、IAA、抽检阈值需要业务评审与配置化，不能靠默认值上线。
- 阶段 2 血缘枚举扩展需要迁移与契约版本升级；不能直接修改阶段 2 文件。
- 真实标注验收依赖真实团队与规范；当前 `BLOCKED`。

### 3.3 迁移与回滚（建议，未执行）

建议迁移 `0011`–`0016`：Schema/黄金集 → 样本/任务 → 分配/提交 → 复核/仲裁 → 抽检/IAA → 触发器/索引/outbox。实际 revision 以当前分支为准；只新增结构，不做历史回填；有数据后回滚必须先导出/备份，禁止直接 drop。迁移执行、生产回滚需用户单独批准。

## 4. 替代方案

| 方案 | 结论 | 原因 |
|---|---|---|
| 标签直接存前端/自由 JSON | 否决 | 不可版本化、不可校验、IAA 不可比 |
| 任务与批次合并为一个状态机 | 否决 | 分层、分批、工作量与状态会耦合 |
| 提交可原地修改 | 否决 | 覆盖历史，复核/仲裁无法追溯 |
| 复核与仲裁同一角色 | 否决 | 职责分离失效 |
| 黄金集答案对标注员可见 | 否决 | 指标失真、合规风险 |
| 用黄金集替代 IAA | 否决 | 黄金集不是独立标注员间一致性 |
| 抽检由标注员自选 | 否决 | 抽样偏差、质量结论不可信 |
| 仅应用层校验项目/状态 | 否决 | 直写 SQL/后台脚本可绕过；沿用阶段 1/2 DB 兜底 |
| 修改阶段 2 血缘枚举 | 否决（默认） | 破坏阶段 2 契约；应新版本扩展 |
| 微服务/CQRS/Event Sourcing | 否决（默认） | 无真实收益与 ADR |

## 5. 批准与生效

- 当前状态：**已提议，未批准**。本 ADR 与阶段 3 草案不构成冻结契约，不得据此声称实现完成或进入阶段验收。
- 批准后生效范围：阶段 3 标注领域模型、状态机、Schema/样本/任务/提交/复核/仲裁/黄金集/IAA/抽检语义、权限矩阵、审计/outbox、血缘扩展、错误码与迁移策略。
- 不包含：迁移执行、生产部署、发布、真实数据处理、物理删除、容量/性能/准确率承诺、外部标注工具采购。
- 批准后维护规则：`packages/contracts/**` 由 System Architect/契约 Owner 单一维护；实现完成并通过契约测试后才允许写入实现快照；破坏性变更必须新建 ADR；冻结文档 SHA256、批准人、日期与变更记录必须归档。

## 6. 验收与证据

- 设计覆盖：Schema、样本集、任务/批次、分配/工作量、提交/记录、复核/仲裁、黄金集、抽检、IAA、权限/隔离、审计/outbox、血缘、错误码、迁移策略。
- 机器可读草案：`docs/architecture/contract-draft/annotation/` 下 OpenAPI、JSON Schema、状态机、错误码、血缘扩展。
- 未执行：`pytest`、`ruff`、`tsc`、Alembic、Redocly、oasdiff、JSON Schema 校验、Markdown lint 全部 `NOT RUN`；本会话无 Bash 工具。
- 实施验收必须附：PostgreSQL 跨项目负向测试、并发领取/复核/仲裁、状态触发器、冻结字段篡改、黄金集答案脱敏、IAA 手工复算、抽检策略、审计 outbox 回滚、血缘正反向与成环证据。

## 7. 变更记录

| 日期 | 变更 | 任务 |
|---|---|---|
| 2026-11-08 | 首次提出；状态「已提议（待批准）」 | DDP-315 |
