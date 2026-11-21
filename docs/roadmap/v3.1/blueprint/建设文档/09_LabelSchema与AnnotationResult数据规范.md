# Label Schema 与 Annotation Result 数据规范

## 1. 目标

标签结构由版本化 LabelSchema 驱动，Annotation Console 只负责按 Schema 渲染和编辑，不在前端硬编码项目标签。

## 2. LabelSchema

核心字段：`schema_id`、`project_id/scope`、`name`、`version`、`status (DRAFT/PUBLISHED/DEPRECATED)`、`task_granularity`、`definitions`、`validation_rules`、`instruction_ref`、`created_by`、`published_at`。

发布后的 Schema Version 不可原地修改。新增字段或规则必须创建新版本。

## 3. Label Definition

每个标签字段至少定义 key、display_name、description、value_type、required、enum_options、unit、min/max、temporal_scope、allowed_overlap、evidence_required、display_group、order、default_value（仅语义明确时）和受控 validation rule。

类型建议：`BOOLEAN / ENUM / MULTI_ENUM / NUMBER / TEXT / JSON_OBJECT / TEMPORAL_SEGMENT / POINT_EVENT / KEYPOINT / POSE / ACTION_SEQUENCE`。

## 4. AnnotationResult

建议结构：`result_id`、`annotation_task_id`、`episode_id`、`annotator_id`、`label_schema_id/schema_version`、`result_version`、`source_type (HUMAN/AI/IMPORTED)`、`payload`、`temporal_segments`、`point_events`、`validation_status`、`submitted_at`、`created_at`、`supersedes_result_id`、`content_hash`。

结果版本不可静默覆盖。草稿可更新，提交后形成 immutable snapshot。

## 5. 时间片段

每个片段记录 segment_id、label_key、start/end timestamp、start/end frame（如可用）、payload、source、confidence（仅 AI）、created_by 和 version。

校验 start < end；范围在 Episode duration 内；frame 与 timestamp 的映射依据明确；重叠是否允许由 Schema 决定；原始时间和 normalized time 同时可追溯。

## 6. AI 建议与人工真值

AI 结果独立保存，记录 model_id/model_version、prompt_version、inference_config、confidence、generated_at、raw_output_hash。人工采纳/修改/拒绝均记录 review_action。禁止 AI 结果直接覆盖人工标签或被标记为人工已审核。

## 7. 示例 Schema

```json
{
  "schema_id": "robot-manipulation-basic",
  "version": "1.0.0",
  "task_granularity": "EPISODE",
  "definitions": [
    {"key": "task_instruction", "display_name": "任务指令", "value_type": "TEXT", "required": true, "temporal_scope": "EPISODE"},
    {"key": "action_phase", "display_name": "动作阶段", "value_type": "TEMPORAL_SEGMENT", "required": true, "enum_options": ["APPROACH", "GRASP", "LIFT", "MOVE", "PLACE", "RELEASE"]},
    {"key": "episode_success", "display_name": "任务是否成功", "value_type": "BOOLEAN", "required": true, "temporal_scope": "EPISODE"},
    {"key": "failure_reason", "display_name": "失败原因", "value_type": "MULTI_ENUM", "required": false, "enum_options": ["NO_CONTACT", "SLIP", "COLLISION", "OCCLUSION", "OTHER"]}
  ]
}
```

此示例仅是通用测试 Schema，不代表任何具体机器人项目已确认的标注规范。正式 Schema 必须由算法/数据负责人确认。

## 8. 数据库约束

先检查现有 annotation models / migrations；优先扩展现有聚合；若已有等价字段，不新建重复表；任务分配、结果版本、Schema Version、审核记录需有唯一约束和 FK；JSON payload 需数据库约束与应用层 Schema 校验；历史 Migration 禁止修改；新 Migration 必须 PG upgrade/downgrade/re-upgrade 验证。

## 9. 验收

Schema Version 固定；结果可版本化；时间片段合法；人工/AI/导入结果区分；结果可追溯 Episode、Task、Assignee、Schema 和来源；已审核结果不可被无痕覆盖。
