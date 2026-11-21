# 标注任务创建、指定分配与工作量管理 PRD

## 1. 产品目标

让数据负责人能够将已完成解析、满足基本可标注条件的 Episode，按明确标注规范组织成 AnnotationTask，并指定给具体标注人。标注人通过“我的任务”进入工作台，完成标注并提交。

## 2. 业务对象边界

- `CollectionTask`：采集指令；
- `AnnotationTaskSpec`：标注任务规范，定义标签、工作要求、输入范围和验收条件；
- `AnnotationBatch`：一次批量生成的标注任务集合；
- `AnnotationTask`：一个可分配的工作单，建议以 Episode 或 Clip 为基本工作单位；
- `AnnotationAssignment`：标注任务与人员的分配关系；
- `AnnotationResult`：标注员提交的一个结果版本；
- `ReviewRecord`：审核结果。

不得将 AnnotationTask 和 LeRobot task_index 混为一谈。

## 3. 创建标注任务流程

```text
选择 Project
  ↓
选择 CollectionTask / DatasetSource / ImportJob
  ↓
筛选 Episode（Processing READY + 可标注）
  ↓
选择 AnnotationTaskSpec
  ↓
锁定 LabelSchema Version
  ↓
设置任务粒度（Episode / Clip）
  ↓
设置优先级、截止时间、工作说明
  ↓
预览任务数量和预计工作量
  ↓
创建 AnnotationBatch + AnnotationTask[]
  ↓
进入分配
```

## 4. AnnotationTaskSpec 字段

`spec_id`、`project_id`、`name/description`、`label_schema_id/version`、`task_granularity`、`supported_media_types`、`required_fields`、`instruction_document_version`、`expected_duration_seconds`（估算值）、`quality_rule_set_id/version`、`review_policy`（single_review / sample_review / double_annotation）、`active/archived`。

Spec 创建后发布版本不可原地修改；修改规范需创建新版本。

## 5. 任务创建规则

- Episode 必须属于用户有权访问的 Project；
- Episode 必须通过基础文件/解析检查；
- 同一 Episode + 同一 Schema Version + 同一任务类型，默认禁止重复创建未完成任务；
- 允许在明确原因下重新标注，必须记录 `supersedes_task_id` 或 `relabel_reason`；
- 已冻结 DatasetVersion 的 AnnotationResult 不允许静默替换；
- Clip 任务必须保存 start_time / end_time，并校验不越过 Episode 时长。

## 6. 指定分配

首期以**人工指定分配**为主，智能推荐为辅：
- 任务负责人在未分配列表选择任务；
- 选择一个具体 Annotator；
- 可按技能标签、当前未完成任务数、历史质量、可用状态筛选；
- 设置截止时间和优先级；
- 批量分配前显示任务数、总估算时长、目标人员当前负载；
- 二次确认后创建 Assignment；
- 分配后出现在“我的任务”。

首期不做黑盒自动派单。后续如加入自动分配，必须可解释、可人工覆盖。

## 7. 分配规则

- 一个 AnnotationTask 同一时刻最多一个 active primary assignee；
- 双人标注策略下，为同一 Episode 创建两个独立 AnnotationWorkItem / Assignment，不共享草稿；
- 重新分配必须结束旧 Assignment 并记录原因；
- 任务被打开后可设置编辑 Lease，防止两个标注员同时覆盖；
- Lease 过期可续租或由负责人回收；
- 并发保存采用 If-Match / row_version；
- 所有分配/回收/转派操作写 Audit + Outbox。

## 8. 标注员能力与负载

最小用户属性：skill_tags、active、project_scope、max_active_tasks（可配置）、当前 assigned/in_progress/overdue 数量、最近质量表现（只展示有样本量依据的统计）。

当前个人开发阶段可以使用测试账号模拟多角色，但权限必须走现有 Identity / ProjectScope，不允许前端只隐藏按钮而后端不校验。

## 9. 任务状态机建议

`DRAFT → READY → ASSIGNED → IN_PROGRESS → SUBMITTED → AUTO_QC → REVIEW_PENDING → ACCEPTED`

分支：
- `SUBMITTED → AUTO_QC_FAILED → REWORK`
- `REVIEW_PENDING → REWORK`
- `REWORK → ASSIGNED / IN_PROGRESS`
- `REVIEW_PENDING → ARBITRATION → ACCEPTED / REWORK / REJECTED`
- `ASSIGNED → UNASSIGNED`（负责人回收）
- `READY / ASSIGNED → CANCELLED`（按规则）

最终状态名必须与现有 Annotation State Machine 对齐。若现有状态机覆盖等价语义，扩展动作而不是另建一套状态机。

## 10. 分配页面

列表列：Task ID、Project、Source CollectionTask、Episode/Clip、TaskSpec/Schema Version、Status、Assignee、Priority、Estimated Duration、Due At、Attempts、Quality/Review Status。

操作：创建批次、选择 Episode、选择 Spec、批量指定人员、单条转派、回收、延期、查看历史、导出任务清单。

## 11. 验收

- 指定 Annotator 后仅该用户能编辑；
- 其他人只读或无权访问；
- 同一任务不能被两个用户同时覆盖；
- 转派保留历史；
- 批量分配事务完整，部分失败有明确报告；
- 重复创建受幂等约束；
- 任务与 Episode、TaskSpec、Schema Version 关联可追溯。
