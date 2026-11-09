# ADR-0013 阶段 5 模型闭环与智能化契约决策

- 状态：**ACCEPTED（已实现并冻结 `ddp.stage5/1.0.0`）**
- 日期：2026-11-09
- 决策者：System Architect；批准人：用户（长期授权）
- 任务：DDP-332
- 关联：ADR-0012；`docs/product/阶段5-模型闭环与智能化PRD.md`；`docs/product/阶段5-模型闭环与智能化验收标准.md`；`docs/architecture/阶段5-模型闭环契约索引.md`；`docs/architecture/contract-draft/modelops/*`
- 契约版本：`ddp.stage5/1.0.0`（冻结 2026-11-09，基线 SHA256 `ce184a5372fe9cc2bc0780ae9800c5fd42c40786a6c0e6818c58621e4e6a2e6d`）
- 边界：本 ADR 与其实现一并冻结契约；生产迁移、部署与发布仍需单独批准。

## 1. 背景

阶段 4 已冻结并交付可复现 DatasetVersion、导出产物与训练交付。总规范 §11.4/§11.5
要求 DDP 支持模型登记、版本、文件引用、运行环境、推理服务、任务、日志、结果、资源状态、
效果与回滚记录，并把模型问题转化为新的数据需求，形成闭环。阶段 5 验收标准是
**模型问题可转化为数据需求**。

## 2. 决策摘要

1. **DDP 不托管模型权重、不建设推理集群**：只保存模型产物对象引用与 SHA256、运行环境、
   外部推理服务端点与任务结果引用。
2. **版本不可变**：`RELEASED` 的 ModelVersion 产物/运行时/训练来源不可原地修改；变更创建新版本。
3. **评测以冻结数据集为输入**：DatasetVersion 必须 `FROZEN` 且 `VALID`；评测结论只有
   PASS/FAIL/UNKNOWN，缺指标为 UNKNOWN，失败关闭。
4. **外部评测/推理通过 Adapter**：未配置真实 Adapter 返回 503，不得写入成功指标或伪造 PASS。
5. **反馈结构化**：问题类型 + 严重级别 + 维度（场景/动作/机器人/设备/标签/模态）+ 证据；
   反馈可转数据需求并保留双向引用。
6. **需求闭环可验证**：需求必须关联冻结 DatasetVersion 才能 SATISFIED；复测评测通过后 CLOSED；
   所有动作写审计与 outbox。
7. **数据缺口为派生视图**：按维度聚合 OPEN/TRIAGED 反馈，输出当前/目标/建议补采量；
   `UNKNOWN` 不计为 0。
8. **自动标注优化为 P1**：P0 不自动改写标注结果，避免未人工确认的 AI 标签进入验收数据。

## 3. [DECISION] 关键取舍

### 3.1 模型产物与运行环境

- 产物引用：`object_key`、`sha256`、`size_bytes`、`format`（ONNX/TORCHSCRIPT/SAFETENSORS/TENSORRT/OTHER）。
- 运行环境：`framework`、`framework_version`、可选 `python_version`、`cuda_version`、`image_ref`。
- 校验：产物存在、大小与 SHA256 一致才可 READY；否则 422。
- **未验证**：真实产物格式与推理引擎兼容性；不得宣称“已支持某运行时”。

### 3.2 评测

- 输入：模型版本（READY/RELEASED）与冻结 DatasetVersion；可选基线评测。
- 指标：`metrics`（name→number）与 `thresholds`（min/max）；结论计算：
  - 缺任一阈值指标 → UNKNOWN；
  - 任一硬阈值失败 → FAIL；
  - 全部满足 → PASS。
- 结果不可变；重跑创建新 run；FAIL 可直接创建反馈。
- **未验证**：真实评测平台、指标口径与阈值默认值。

### 3.3 推理

- 服务登记：模型版本、环境（DEV/STAGING/PROD）、端点 URL、资源引用、状态与健康摘要。
- 任务：引用模型版本、服务、数据集版本或 Episode 列表；状态 QUEUED→RUNNING→终态。
- 执行不在 DDP；Adapter 回调回写结果/日志引用；不可达 → 503。
- **未验证**：真实推理平台、配额、并发与超时。

### 3.4 反馈与数据缺口

- 反馈必须来自同项目评测/推理或人工登记；维度去重；证据可引用对象键。
- 缺口视图按 `(dimension_type, dimension_value)` 聚合并去重需求；`current_count` 来自数据集统计，
  缺失显示 UNKNOWN，不得记为 0。

### 3.5 数据需求闭环

- 来源：MODEL_FEEDBACK/EVALUATION/MANUAL/OPERATION；保留 `source_feedback_ids`/`source_evaluation_ids`。
- 审批：提交人不得自批；OWNER 审批。
- 满足：必须关联 FROZEN+VALID DatasetVersion（且包含目标维度样本）；记录证据引用。
- 复测：关联 EvaluationRun；CLOSED 需至少一次复测。
- **未验证**：目标维度样本自动校验的实现口径（P0 由服务端校验数据集统计摘要）。

### 3.6 自动标注优化（P1）

- P1 计划：基于黄金集/IAA 与模型反馈，建议置信度阈值、复核比例与模型版本；
  不得自动接受 AI 标签。P0 不实现。

## 4. 契约边界

### 4.1 负责

Model/ModelVersion、EvaluationRun、InferenceService/InferenceTask、ModelFeedback、DataGap、DataRequirement、
Adapter 回调、审计/outbox/血缘扩展。

### 4.2 非目标

不建设训练/推理集群、不做 GPU 调度、不托管权重、不执行训练/推理、不自动接受 AI 标签、
不物理删除、不默认跨项目共享、不引入微服务/CQRS/Event Sourcing、不承诺未测量的效果。

## 5. 版本、变更与回滚

- 草案版本 `ddp.stage5/1.0.0-draft`；冻结后破坏性变更需新 ADR 与版本提升。
- 实现迁移只新增结构（`0021_stage5_modelops`），不回填历史；回滚需先备份；生产迁移单独批准。
- 实现与冻结记录见 `docs/architecture/阶段5-模型闭环契约冻结-v1.0.md`。

## 6. 未验证项与风险

| ID | 事项 | 状态 | 影响 |
|---|---|---|---|
| U5-01 | 真实模型产物与 Hash | 未提供 | 使用 `[SYNTHETIC]` |
| U5-02 | 真实评测平台与指标口径 | 未提供/未定 | 真实评测 `BLOCKED` |
| U5-03 | 真实推理平台、配额、超时 | 未提供 | 真实推理 `BLOCKED` |
| U5-04 | 自动标注优化 | P1 | 不影响 P0 闭环 |
| U5-05 | 需求维度覆盖率自动计算 | 方案未定 | P0 用数据集统计摘要，`未验证` |
| U5-06 | 跨项目模型/数据共享 | 未批准 | 默认拒绝 |

风险缓解：先冻结模型/评测/反馈/需求语义与错误码；实现时以真实 PostgreSQL 与 Adapter 边界测试；
Mock 必须标注且不进入真实指标分母。

## 7. 验收与证据

- 核心验收 AC-MF-12：FAIL 评测 → 反馈 → 需求 → 审批 → 关联冻结数据集 → 复测评测，全链路可追溯。
- 阶段 5 实现完成后需提供：跨项目负向、发布不可变、评测结论、反馈转需求、需求满足门禁、
  审计/outbox、并发/幂等与前端状态覆盖证据。
- 真实评测/推理在没有真实平台时 `BLOCKED`，不得以 Mock 宣称 PASS。

## 8. 变更记录

| 日期 | 变更 | 任务 |
|---|---|---|
| 2026-11-09 | 首次提出：模型/版本/评测/推理/反馈/数据需求决策与边界 | DDP-332 |
| 2026-11-09 | 实现完成并冻结契约 `ddp.stage5/1.0.0`；ADR 转 ACCEPTED | DDP-341 |
