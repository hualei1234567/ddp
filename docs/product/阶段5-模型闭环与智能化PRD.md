# DDP 阶段 5 模型闭环与智能化 PRD

> 文档编号：PRD-DDP-332｜任务：DDP-332｜阶段：5｜版本：V0.1（需求基线）｜日期：2026-11-09｜Owner：Product Manager
> 状态：**DRAFT，待冻结**；验收项默认 `NOT RUN`
> 基线：`docs/DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md`（下称“总规范”）§8.3/§8.4/§11.4/§11.5/§22.2
> 关联：`docs/product/阶段5-模型闭环与智能化验收标准.md`、阶段 4 PRD/验收标准、`docs/architecture/阶段4-契约冻结-v1.0.md`
> **实现基线（2026-11-09，`main@3d19edc`）**：阶段 4 已冻结并部署 staging；阶段 5 的 Model、ModelVersion、EvaluationRun、ModelFeedback、DataRequirement、InferenceService/Task 均**未实现**，本文不代表任何功能可用。
> **边界**：只定义业务需求、流程、状态机、角色、字段清单与验收基线；不定义技术实现，不修改代码/契约/迁移。最终以 System Architect 冻结契约与 ADR 为准。

| 标记 | 含义 |
|---|---|
| `DRAFT` | 草案，未冻结 |
| `NOT RUN` | 验收未执行 |
| `未验证` | 缺少真实证据 |
| `BLOCKED` | 环境、人员、外部平台阻塞 |
| `P0/P1/P2` | 阻断 / 增强 / 后续 |
| `[MOCK]` `[SYNTHETIC]` `REAL` | 模拟 / 合成 / 真实 |

## 1. 背景、目标与范围

阶段 4 已把标注验收后的 Episode 冻结为可复现的 DatasetVersion，并把训练需求、导出、交付与外部训练引用纳入血缘。阶段 5 要解决的核心问题是：**模型在真实场景中的问题，能沿数据链路反向转化为可执行的数据需求，并在新数据集上复测形成闭环**。

总规范 §11.5 要求：将动作覆盖不足、场景样本不足、长尾失败案例不足、视角变化不足、泛化不足、标签不完整和数据质量不足等问题转化为新的数据需求，形成“反馈 → 需求 → 方案 → 采集 → 数据集 → 评测”的闭环。阶段 5 验收：**模型问题可转化为数据需求**。

### 1.1 目标

1. **模型登记**：模型、版本、产物引用、运行环境、训练来源、发布状态与回滚记录可追溯。
2. **推理集成**：登记外部推理服务与推理任务，保存输入、结果引用、日志引用、资源与状态；DDP 不托管模型权重与推理集群。
3. **模型评测**：在**已冻结**的 DatasetVersion 上执行评测，保存指标、阈值、结论、报告与基线对比；结论只有 PASS/FAIL/UNKNOWN，不得默认成功。
4. **模型反馈**：把失败案例、覆盖不足与质量问题登记为结构化反馈，包含问题类型、维度、证据与严重级别。
5. **数据缺口分析**：按场景/动作/机器人/设备/标签/模态聚合反馈，给出当前覆盖、目标覆盖与建议补采量。
6. **需求闭环**：反馈可转化为 DataRequirement，并关联后续 DatasetVersion 与复测结果；闭环状态可查询。
7. **治理**：项目隔离、角色权限、审计、outbox、幂等、乐观锁、不可变发布版本、跨项目拒绝与阶段 2/3/4 一致。
8. **自动标注优化（P1）**：基于模型反馈与黄金集/IAA 评估自动标注配置，输出建议；P0 不自动改写标注结果。

### 1.2 成功定义

任一模型问题必须能回答：哪个模型版本、在哪个冻结数据集版本、哪次评测/推理、失败在什么场景/动作/视角、证据是什么、转化成了哪条数据需求、需求是否被采集与数据集满足、在哪个新版本上复测、结论是否改善。任一环缺失，不得宣称“闭环”。

### 1.3 范围与非目标

| 子域 | P0 | P1 | 本期不做 |
|---|---|---|---|
| 模型登记 | Model/ModelVersion 创建、编辑、状态、产物引用与 Hash、运行环境、训练来源 | 模型卡、血缘图、对比视图 | 模型权重托管、模型市场 |
| 版本发布 | VALIDATING→READY→RELEASED→DEPRECATED/ARCHIVED；发布审批与不可变 | 灰度/回滚编排 | 自动部署到生产 |
| 推理服务 | 外部服务登记、环境、端点引用、状态、健康摘要 | 多服务路由、配额与成本 | 自建推理集群、GPU 调度 |
| 推理任务 | 创建、状态、输入/结果/日志引用、取消、失败记录 | 批量推理、定时任务 | 自建推理执行 |
| 模型评测 | 冻结版本评测、指标/阈值/结论/报告、基线对比、不可变结果 | 多指标可视化、竞品对比 | 训练执行、自动调参 |
| 模型反馈 | 结构化问题、维度、严重级别、证据、状态 | 自动归因、聚类 | 自动修改模型 |
| 数据缺口 | 按维度聚合、覆盖对比、建议补采量 | 覆盖率预测 | 需求自动排期 |
| 数据需求 | 创建、审批、关联反馈、满足度、关联数据集版本与复测 | 与采集任务自动联动 | 绕过阶段 2/3 的自动采集 |
| 自动标注优化 | 只读建议（P1） | 置信度阈值/复核比例建议、效果趋势 | 自动接受 AI 标签 |

非目标：N5-01 建设训练集群、推理集群、GPU 调度或模型权重托管；N5-02 执行模型训练（阶段 4 已通过 Adapter 管理引用）；N5-03 自动接受未人工确认的 AI 标签；N5-04 用 Mock/合成评测冒充真实模型效果；N5-05 物理删除数据或改写已发布模型版本/已冻结评测结果；N5-06 默认跨项目共享模型或评测数据；N5-07 未批准的微服务、CQRS、Event Sourcing；N5-08 生产部署与生产数据变更未经批准。

### 1.4 术语

- **Model**：项目内模型容器，稳定 `model_id` 与业务编码；不直接承载权重。
- **ModelVersion**：模型的一次可追溯版本，含产物引用（对象键 + SHA256 + 大小 + 格式）、运行环境、训练来源与发布状态；`RELEASED` 后不可原地修改。
- **InferenceService**：外部推理服务登记（端点、环境、资源引用），DDP 只保存引用与状态。
- **InferenceTask**：一次推理请求的记录，引用模型版本、服务、输入数据集/Episode 与结果。
- **EvaluationRun**：在冻结 DatasetVersion 上的一次评测，保存指标、阈值、结论、报告与基线。
- **ModelFeedback**：结构化模型问题，含问题类型、维度、证据、严重级别与来源评测/推理。
- **DataGap**：由反馈聚合出的数据缺口（维度 → 当前/目标/建议补采量）。
- **DataRequirement**：可审批的数据需求，是“反馈 → 需求”闭环的落点；后续可关联数据集版本与复测。

## 2. 角色与权限

| 动作 | 角色 |
|---|---|
| 创建/编辑模型与版本 | OWNER、EDITOR、平台管理员 |
| 发布/弃用/归档模型版本 | OWNER（发布人不得是唯一创建人） |
| 登记推理服务、创建推理任务 | OWNER、EDITOR |
| 创建评测 | OWNER、EDITOR、QA |
| 提交反馈 | 任何项目成员 |
| 处理反馈（triaged/requirement_created/resolved/dismissed） | OWNER、EDITOR、QA |
| 创建/审批数据需求 | EDITOR 创建；OWNER 审批（提交人不得自批） |
| 关联需求满足与复测 | OWNER、QA |
| 跨项目访问 | 默认拒绝；统一 404/403，不泄漏存在性 |

前端隐藏按钮不构成权限；所有动作以服务端 `allowed_actions` 为准。

## 3. 业务流程

### 3.1 模型登记与发布

1. 创建 Model（DRAFT），填写编码、名称、任务类型、框架、负责人。
2. 创建 ModelVersion（DRAFT），提交产物引用、SHA256、大小、运行环境与训练来源。
3. 提交校验（VALIDATING）：校验产物存在、Hash 匹配、环境字段完整。
4. 校验通过（READY）→ OWNER 审批发布（RELEASED）；发布后不可原地修改。
5. 新版本发布不自动弃用旧版本；弃用（DEPRECATED）/归档（ARCHIVED）需原因并审计。

### 3.2 评测

1. 选择 `RELEASED`（或 READY，需显式允许）的模型版本与 `FROZEN` 且 `VALID` 的 DatasetVersion。
2. 创建 EvaluationRun（QUEUED）；Adapter/外部评测执行，回写指标、结论与报告引用。
3. 结论 PASS/FAIL/UNKNOWN 依据阈值；缺指标 → UNKNOWN，不得默认 PASS。
4. 评测结果不可变；重跑创建新 run，可指定基线 run 做对比。
5. FAIL 的 run 必须能创建 ModelFeedback（问题类型、维度、证据）。

### 3.3 反馈 → 数据需求闭环

1. 从评测或推理任务创建反馈，登记维度（场景/动作/机器人/设备/标签/模态）、当前/目标覆盖、证据。
2. `GET gap-summary` 按维度聚合 OPEN/TRIAGED 反馈，输出缺口与建议补采量。
3. 反馈可转换为 DataRequirement（DRAFT），继承维度与目标覆盖，记录 `source_feedback_ids`。
4. 需求提交（SUBMITTED）→ OWNER 审批（APPROVED）→ 规划/执行（PLANNED/IN_PROGRESS）。
5. 需求满足需关联至少一个 DatasetVersion（且该版本包含目标维度样本），状态置 SATISFIED；随后创建复测 EvaluationRun，记录 `retest_evaluation_ids`。
6. 复测 PASS 后需求可 CLOSED（P1 自动；P0 人工关闭并记录证据）。

### 3.4 推理

1. 登记推理服务（PENDING/DEPLOYING/ACTIVE/STOPPED/RETIRED）。
2. 创建推理任务，引用模型版本、服务与输入（DatasetVersion 或 Episode 列表）。
3. 外部执行后回写 SUCCEEDED/FAILED/CANCELLED、结果引用、指标与日志引用。
4. 失败任务必须记录错误码/摘要；结果未知不得记为成功。

## 4. 核心字段（需求清单，不是数据库设计）

### 4.1 Model

`model_id`、`project_id`、`code`、`name`、`description`、`task_type`（IMITATION/RL/VLM/OTHER）、`framework`、`owner_id`、`status`、`current_version_id`、`tags`、`allowed_actions`、`version`、`created_at/updated_at/created_by/updated_by`。

### 4.2 ModelVersion

`model_version_id`、`model_id`、`version_no`、`status`、`artifact`（`object_key`、`sha256`、`size_bytes`、`format`）、`runtime`（`framework`、`framework_version`、`python_version`、`cuda_version`、`image_ref`）、`training_source`（`training_request_id?`、`training_delivery_id?`、`external_ref_id?`）、`dataset_version_ids[]`、`metrics_summary`、`release_notes`、`validated_at`、`released_by`、`released_at`、`deprecated_at`、`deprecation_reason`、`allowed_actions`、`version`。

### 4.3 InferenceService

`service_id`、`model_version_id`、`environment`（DEV/STAGING/PROD）、`endpoint_url`、`resource_ref`、`config`、`status`、`health_status`（UNKNOWN/HEALTHY/UNHEALTHY）、`deployed_at`、`stopped_at`、`allowed_actions`、`version`。

### 4.4 InferenceTask

`task_id`、`model_version_id`、`service_id?`、`dataset_version_id?`、`episode_ids[]`、`status`、`result_ref`（对象键 + SHA256）、`metrics`、`log_ref`、`error_code`、`error_summary`、`started_at`、`finished_at`、`created_by`、`version`。

### 4.5 EvaluationRun

`evaluation_id`、`model_version_id`、`dataset_version_id`（必须 FROZEN+VALID）、`baseline_evaluation_id?`、`status`、`metrics`（name→value）、`thresholds`（name→min/max）、`verdict`（PASS/FAIL/UNKNOWN）、`report_ref`（对象键 + SHA256）、`error_code`、`error_summary`、`started_at`、`finished_at`、`allowed_actions`、`version`。

### 4.6 ModelFeedback

`feedback_id`、`model_version_id`、`evaluation_id?`、`inference_task_id?`、`issue_type`（ACTION_COVERAGE/SCENE_COVERAGE/LONG_TAIL/VIEWPOINT/GENERALIZATION/LABEL_GAP/DATA_QUALITY/OTHER）、`severity`（BLOCKER/MAJOR/MINOR）、`description`、`dimensions[]`（`type`、`value`、`current_count`、`target_count`）、`evidence_refs[]`、`status`、`converted_requirement_id?`、`resolution_note?`、`allowed_actions`、`version`。

### 4.7 DataRequirement

`requirement_id`、`project_id`、`code`、`title`、`description`、`source_type`（MODEL_FEEDBACK/EVALUATION/MANUAL/OPERATION）、`source_feedback_ids[]`、`source_evaluation_ids[]`、`dimensions[]`、`target_episode_count`、`quality_thresholds`、`priority`、`due_at`、`owner_id`、`status`、`linked_dataset_version_ids[]`、`retest_evaluation_ids[]`、`satisfied_at`、`satisfaction_evidence_ref`、`allowed_actions`、`version`。

### 4.8 DataGap（派生视图）

`dimension_type`、`dimension_value`、`open_feedback_count`、`current_count`（可选，来自数据集统计）、`target_count`（取反馈目标最大值）、`suggested_episode_count`、`requirement_ids[]`、`freshness`（FRESH/STALE/UNKNOWN）。

## 5. 状态机

### 5.1 Model

`DRAFT → ACTIVE → DEPRECATED → ARCHIVED`；`ACTIVE → ARCHIVED`。终态 ARCHIVED 不可回退。

### 5.2 ModelVersion

`DRAFT → VALIDATING → READY → RELEASED → DEPRECATED → ARCHIVED`；
`VALIDATING → VALIDATION_FAILED → DRAFT`（修复后重提）；
`RELEASED → ARCHIVED`（需无 ACTIVE 推理服务）。

### 5.3 InferenceService

`PENDING → DEPLOYING → ACTIVE → STOPPED → RETIRED`；`DEPLOYING → FAILED → PENDING`；终态 RETIRED。

### 5.4 InferenceTask

`QUEUED → RUNNING → SUCCEEDED/FAILED/CANCELLED`；`QUEUED → CANCELLED`；终态不可回退。

### 5.5 EvaluationRun

`QUEUED → RUNNING → SUCCEEDED/FAILED/CANCELLED`；SUCCEEDED 后 verdict 不可修改；终态不可回退。

### 5.6 ModelFeedback

`OPEN → TRIAGED → REQUIREMENT_CREATED → RESOLVED`；`OPEN/TRIAGED → DISMISSED`；终态 RESOLVED/DISMISSED。

### 5.7 DataRequirement

`DRAFT → SUBMITTED → APPROVED → PLANNED → IN_PROGRESS → SATISFIED → CLOSED`；
`SUBMITTED → REJECTED`；任意非终态 → `CANCELLED`；终态 CLOSED/REJECTED/CANCELLED。

## 6. 业务规则

1. **版本不可变**：`RELEASED` ModelVersion 的产物、运行时、训练来源不可修改；变更创建新版本。
2. **评测输入门禁**：DatasetVersion 必须 `FROZEN` 且 `validity_status=VALID`；否则 409 `evaluation_dataset_not_ready`。
3. **结论可解释**：PASS 要求所有阈值指标存在且满足；缺指标 → UNKNOWN；任一硬阈值失败 → FAIL。
4. **不伪造效果**：无真实 Adapter 时评测/推理返回 `BLOCKED`/503，不得写入成功指标或 PASS。
5. **闭环完整性**：反馈转需求必须保留 `source_feedback_ids`；需求 SATISFIED 必须关联冻结 DatasetVersion；复测必须引用该需求关联的版本（或显式豁免并审计）。
6. **项目隔离**：所有对象与引用同项目；跨项目统一 404/403；评测/推理不得引用其他项目数据集。
7. **不可删除**：模型版本、评测、反馈、需求只归档/取消，不物理删除；审计与血缘只追加。
8. **幂等与并发**：创建与动作支持 `Idempotency-Key`；动作要求 `If-Match`；重复评测/发布会创建新资源或 409。
9. **审计**：创建、发布、弃用、评测、反馈转需求、需求审批/满足/关闭、推理状态变化写审计与 outbox。
10. **指标口径**：缺口与覆盖率区分 `0 / N/A / UNKNOWN / STALE`；合成与 Mock 数据不进入真实指标分母。

## 7. 指标口径

| 指标 | 定义 |
|---|---|
| 模型评测通过率 | PASS 的 EvaluationRun / 有结论（PASS/FAIL）的 run；UNKNOWN 不计入分母 |
| 反馈转需求率 | 已转需求的反馈 / 已处理（非 OPEN）反馈 |
| 需求满足周期 | requirement.satisfied_at − approved_at |
| 缺口补采量 | Σ 维度 suggested_episode_count（按需求/维度去重） |
| 复测改善率 | 复测 PASS 的需求 / 已复测需求；无复测显示 N/A |
| 覆盖率 | 目标维度有样本的 Episode / 目标 Episode；缺数据维度显示 UNKNOWN |

## 8. 页面与状态

1. 模型列表/详情：状态、版本、负责人、评测与反馈入口；Loading/Empty/Error/Forbidden/Stale/Disabled。
2. 模型版本详情：产物 Hash、运行时、训练来源、发布动作与历史。
3. 评测列表/详情：指标、阈值、结论、报告、基线对比、从失败创建反馈。
4. 反馈列表/详情：维度、证据、状态、转需求。
5. 数据需求列表/详情：来源反馈、维度、审批、关联数据集版本与复测。
6. 数据缺口视图：按维度聚合、建议补采量、来源反馈。
7. 推理服务/任务（P0 只读+登记）：状态、端点引用、任务结果与日志引用。
8. 前端不做权威权限判断；动作按 `allowed_actions` 渲染。

## 9. 依赖、风险与开放问题

| ID | 事项 | 状态 |
|---|---|---|
| D5-01 | 阶段 4 DatasetVersion 冻结/有效性 | 已实现并 staging 验证 |
| D5-02 | 真实模型评测平台/推理平台 | 未提供；P0 允许 `[MOCK]` Adapter，真实集成 `BLOCKED` |
| D5-03 | 真实模型产物与 SHA256 | 未提供；`[SYNTHETIC]` 产物仅测试 |
| D5-04 | 评测指标口径与阈值 | 需求方未定；P0 由项目配置化，不硬编码 |
| D5-05 | 自动标注优化 | P1；P0 不自动改写标注 |
| D5-06 | 数据需求与阶段 2 采集任务自动联动 | P1；P0 手工关联 |
| D5-07 | 模型/数据跨项目共享 | 默认拒绝；P1 评估授权模型 |

## 10. 验收对齐

阶段 5 门禁以“模型问题可转化为数据需求”为核心，对应验收 AC-MF-12：
创建 FAIL 评测 → 创建反馈 → 转为 DataRequirement → 审批 → 关联冻结 DatasetVersion →
创建复测评测 → 需求状态可追溯。详见 `阶段5-模型闭环与智能化验收标准.md`。

## 11. 变更记录

| 日期 | 变更 | 任务 |
|---|---|---|
| 2026-11-09 | 初版：范围、角色、流程、字段、状态机、规则、指标、页面、依赖 | DDP-332 |
