# DDP-322 关键 HTTP/DB 证据

## D1
- PASS | 迁移库 JSON 列保持原始 json 类型（未临时改 jsonb） | 期望 ['json'] | 实际 {'types': ['json'], 'columns': [{'table': 'annotation_arbitrations', 'column': 'arbiter_ids', 'type': 'json'}, {'table': 'annotation_arbitrations', 'column': 'votes', 'type': 'json'}, {'table': 'annotation_batches', 'column': 'assignee_ids', 'type': 'json'}, {'table': 'annotation_batches', 'column': 'item_refs', 'type': 'json'}, {'table': 'annotation_batches', 'column': 'quality_summary', 'type': 'json'}, {'table': 'annotation_batches', 'column': 'reviewer_pool_ids', 'type': 'json'}, {'table': 'annotation_gold_set_evaluations', 'column': 'results', 'type': 'json'}, {'table': 'annotation_gold_sets', 'column': 'items', 'type': 'json'}, {'table': 'annotation_gold_sets', 'column': 'usage_policy', 'type': 'json'}, {'table': 'annotation_iaa_reports', 'column': 'excluded_items', 'type': 'json'}, {'table': 'annotation_iaa_reports', 'column': 'metrics', 'type': 'json'}, {'table': 'annotation_quality_samples', 'column': 'item_refs', 'type': 'json'}, {'table': 'annotation_quality_samples', 'column': 'metadata', 'type': 'json'}, {'table': 'annotation_records', 'column': 'evidence_refs', 'type': 'json'}, {'table': 'annotation_records', 'column': 'labels', 'type': 'json'}, {'table': 'annotation_records', 'column': 'time_range', 'type': 'json'}, {'table': 'annotation_reviews', 'column': 'checklist', 'type': 'json'}, {'table': 'annotation_reviews', 'column': 'issues', 'type': 'json'}, {'table': 'annotation_sample_sets', 'column': 'exclusion_summary', 'type': 'json'}, {'table': 'annotation_sample_sets', 'column': 'filters', 'type': 'json'}, {'table': 'annotation_sample_sets', 'column': 'strata', 'type': 'json'}, {'table': 'annotation_schemas', 'column': 'annotation_levels', 'type': 'json'}, {'table': 'annotation_schemas', 'column': 'labels', 'type': 'json'}, {'table': 'annotation_submissions', 'column': 'record_refs', 'type': 'json'}, {'table': 'annotation_tasks', 'column': 'iaa_policy', 'type': 'json'}, {'table': 'annotation_tasks', 'column': 'progress', 'type': 'json'}, {'table': 'annotation_tasks', 'column': 'quality_policy', 'type': 'json'}, {'table': 'annotation_tasks', 'column': 'quality_summary', 'type': 'json'}]}
- PASS | 模拟 0015 历史库的守卫函数体存在 OLD!=NEW JSON 比较 | 期望 >0 处旧式 JSON <> 比较 | 实际 7
- PASS | 0016 重装后 7 个守卫函数均使用 CAST AS TEXT | 期望 7/7 含 CAST(OLD ... AS TEXT) | 实际 {'ddp_annotation_arbitration_guard': True, 'ddp_annotation_gold_set_guard': True, 'ddp_annotation_iaa_report_guard': True, 'ddp_annotation_quality_sample_guard': True, 'ddp_annotation_record_guard': True, 'ddp_annotation_review_guard': True, 'ddp_annotation_submission_guard': True}
- PASS | 0016 重装后不再存在 OLD!=NEW JSON 比较 | 期望 0 处 | 实际 0
- PASS | 迁移库中成功构造 7 类标注对象 | 期望 7 类对象均存在 | 实际 {'submission': 'e1364cc8-6258-4eb4-8127-3685159cae04', 'record': '03ae012d-790b-4530-9477-38415b14e980', 'review': 'e592d943-5eed-45e9-9199-54beb2e6f837', 'arbitration': '4e294bf4-b207-4a6e-bfde-76df879833f0', 'gold_set': '050f1755-ccb4-4df1-a6e4-535fe28edc3a', 'quality_sample': 'ee8ab24d-bd50-41ce-a79e-d6f2bbde82e9', 'iaa_report': 'de6fa97c-efff-4047-93aa-868d7ca9cf68'}
- PASS | submission 守卫对 JSON 列 annotation_submissions.record_refs UPDATE 返回业务错误 | 期望 业务错误 annotation_submission_immutable，且无 operator does not exist | 实际 annotation_submission_immutable
- PASS | record 守卫对 JSON 列 annotation_records.labels UPDATE 返回业务错误 | 期望 业务错误 annotation_record_immutable，且无 operator does not exist | 实际 annotation_record_immutable
- PASS | review 守卫对 JSON 列 annotation_reviews.issues UPDATE 返回业务错误 | 期望 业务错误 annotation_review_immutable，且无 operator does not exist | 实际 annotation_review_immutable
- PASS | arbitration 守卫对 JSON 列 annotation_arbitrations.votes UPDATE 返回业务错误 | 期望 业务错误 annotation_arbitration_immutable，且无 operator does not exist | 实际 annotation_arbitration_immutable
- PASS | gold_set 守卫对 JSON 列 annotation_gold_sets.items UPDATE 返回业务错误 | 期望 业务错误 annotation_gold_set_immutable，且无 operator does not exist | 实际 annotation_gold_set_immutable
- PASS | quality_sample 守卫对 JSON 列 annotation_quality_samples.item_refs UPDATE 返回业务错误 | 期望 业务错误 annotation_quality_sample_immutable，且无 operator does not exist | 实际 annotation_quality_sample_immutable
- PASS | iaa_report 守卫对 JSON 列 annotation_iaa_reports.metrics UPDATE 返回业务错误 | 期望 业务错误 annotation_iaa_report_immutable，且无 operator does not exist | 实际 annotation_iaa_report_immutable

## D2
- PASS | 读取任务当前版本 | 期望 [200] | 实际 200
- PASS | 读取批次当前版本 | 期望 [200] | 实际 200
- PASS | 批次提交反馈审 | 期望 [200] | 实际 200
- PASS | 任务提交反馈审 | 期望 [200] | 实际 200
- PASS | 批次进入 REVIEWING | 期望 [200] | 实际 200
- PASS | 批次状态 REVIEWING | 期望 REVIEWING | 实际 REVIEWING
- PASS | 任务进入 REVIEWING | 期望 [200] | 实际 200
- PASS | 任务状态 REVIEWING | 期望 REVIEWING | 实际 REVIEWING
- PASS | 创建复核 round1 | 期望 [201] | 实际 201
- PASS | 复核 round1 领取 | 期望 [200] | 实际 200
- PASS | 驳回缺原因 → 422 | 期望 [422] | 实际 422
- PASS | 合法驳回 | 期望 [200] | 实际 200
- PASS | 复核状态 REJECTED | 期望 REJECTED | 实际 REJECTED
- PASS | 读取被驳回提交 | 期望 [200] | 实际 200
- PASS | 提交状态 REJECTED | 期望 REJECTED | 实际 REJECTED
- PASS | OWNER 发起 require-rework | 期望 [200] | 实际 200
- PASS | 提交状态 REWORK_REQUIRED | 期望 REWORK_REQUIRED | 实际 REWORK_REQUIRED
- PASS | 任务从 REVIEWING 退回 REWORK | 期望 REWORK | 实际 REWORK
- PASS | 批次从 REVIEWING 退回 REWORK | 期望 REWORK | 实际 REWORK
- PASS | 分配退回 IN_PROGRESS | 期望 IN_PROGRESS | 实际 IN_PROGRESS
- PASS | 批次 rework_count 递增为 1 | 期望 1 | 实际 1
- PASS | 创建提交 v2 | 期望 [201] | 实际 201
- PASS | v2 版本号 | 期望 2 | 实际 2
- PASS | v2 supersedes v1 | 期望 47fbcffd-4125-4a97-bde7-92d0ab282e84 | 实际 47fbcffd-4125-4a97-bde7-92d0ab282e84
- PASS | 旧版 v1 保留且状态 SUPERSEDED | 期望 SUPERSEDED | 实际 SUPERSEDED
- PASS | 任务 REWORK 恢复 | 期望 [200] | 实际 200
- PASS | 批次 REWORK 恢复 | 期望 [200] | 实际 200
- PASS | 提交 v2 | 期望 [200] | 实际 200
- PASS | 创建复核 round2 | 期望 [201] | 实际 201
- PASS | 复核 round2 领取 | 期望 [200] | 实际 200
- PASS | 复检通过 v2 | 期望 [200] | 实际 200
- PASS | 复核状态 APPROVED | 期望 APPROVED | 实际 APPROVED
- PASS | v2 最终 APPROVED | 期望 APPROVED | 实际 APPROVED
- PASS | 分配重新进入进行中 | 期望 [200] | 实际 200
- PASS | 创建提交 v3 | 期望 [201] | 实际 201
- PASS | v3 版本号 | 期望 3 | 实际 3
- PASS | 提交 v3 | 期望 [200] | 实际 200
- PASS | 读取任务当前版本（第二轮） | 期望 [200] | 实际 200
- PASS | 读取批次当前版本（第二轮） | 期望 [200] | 实际 200
- PASS | 批次再次提交反馈审 | 期望 [200] | 实际 200
- PASS | 任务再次提交反馈审 | 期望 [200] | 实际 200
- PASS | 批次再次 REVIEWING | 期望 [200] | 实际 200
- PASS | 任务再次 REVIEWING | 期望 [200] | 实际 200
- PASS | 创建复核 round3 | 期望 [201] | 实际 201
- PASS | 复核 round3 领取 | 期望 [200] | 实际 200
- PASS | 复核 round3 驳回 | 期望 [200] | 实际 200
- PASS | 返工次数超过上限 → 409 | 期望 [409] | 实际 409
- PASS | 返工超限错误码 | 期望 annotation_task_rework_limit_exceeded | 实际 annotation_task_rework_limit_exceeded
- PASS | 超限失败不递增 rework_count | 期望 1 | 实际 1

## D3
- PASS | 非复核人/非 OWNER require-rework → 403 | 期望 [403] | 实际 403
- PASS | 创建黄金集 | 期望 [201] | 实际 201
- PASS | AN 读取答案 → 403 | 期望 [403] | 实际 403
- PASS | AN 读取答案错误码 | 期望 gold_set_access_denied | 实际 gold_set_access_denied
- PASS | AE 读取答案 → 403 | 期望 [403] | 实际 403
- PASS | AE 读取答案错误码 | 期望 gold_set_access_denied | 实际 gold_set_access_denied
- PASS | RO 读取答案 → 403 | 期望 [403] | 实际 403
- PASS | RO 读取答案错误码 | 期望 gold_set_access_denied | 实际 gold_set_access_denied
- PASS | OWNER 读取答案 → 200 | 期望 [200] | 实际 200
- PASS | OWNER 响应包含 expected_labels | 期望 包含 expected_labels | 实际 {'gold_item_id': '5d471b5f-a719-46b6-b7b8-a4b7a33bacb1', 'episode_id': '3695b10c-ad7d-4bcd-aa26-b9152f418aa4', 'difficulty': 'MEDIUM', 'weight': 1.0, 'active': True, 'expected_labels': [{'label_key': 'action', 'value': 'PICK'}]}
- PASS | 403 拒绝写入 FAILURE 审计 | 期望 AN/AE/RO 各一条 FAILURE | 实际 {'count': 3, 'actors': ['36d82827-9c13-421a-98d1-0c0217a88c3b', 'a9f6b18c-92ac-4b7f-911d-ebf63b5bd783', 'cba14740-0cbf-44e9-80ab-7fe983738841']}
- PASS | 403 拒绝写入 answers_denied outbox | 期望 ≥3 条 | 实际 3
- PASS | OWNER 成功访问写入 SUCCESS 审计与 outbox | 期望 ≥1 条 SUCCESS + ≥1 条 accessed | 实际 {'success': 1, 'accessed': 1}

## D4
- PASS | 超容量分配 → 409 | 期望 [409] | 实际 409
- PASS | 超容量错误码 | 期望 annotation_assignment_capacity_exceeded | 实际 annotation_assignment_capacity_exceeded

## D5
- PASS | 创建样本集 | 期望 [201] | 实际 201
- PASS | 样本预览 | 期望 [200] | 实际 200
- PASS | exclusion_summary 含 episode_status:REJECTED | 期望 1 | 实际 1
- PASS | exclusion_summary 含 episode_status:INVALIDATED | 期望 1 | 实际 1
- PASS | exclusion_summary 含 episode_status:ARCHIVED | 期望 1 | 实际 1
- PASS | exclusion_summary 含 WARN 未确认 | 期望 1 | 实际 1
- PASS | 不合格 Episode 不入选 | 期望 REJECTED/INVALIDATED/ARCHIVED 均不入选 | 实际 ['3695b10c-ad7d-4bcd-aa26-b9152f418aa4', 'c5da3181-1ebb-4426-91c2-b5a5ffcfde75']
- PASS | 生成样本集快照 | 期望 [200] | 实际 200
- PASS | 快照状态 SNAPSHOTTED | 期望 SNAPSHOTTED | 实际 SNAPSHOTTED

## D6
- PASS | 标注员 2 创建提交 | 期望 [201] | 实际 201
- PASS | 标注员 2 提交 | 期望 [200] | 实际 200
- PASS | RANDOM 抽检计划 | 期望 [201] | 实际 201
- PASS | RANDOM 返回 1 条策略化样本 | 期望 长度 1 | 实际 1
- PASS | RANDOM 策略落库 | 期望 RANDOM | 实际 RANDOM
- PASS | RANDOM 策略版本落库 | 期望 quality/1 | 实际 quality/1
- PASS | RANDOM 样本携带候选 risk_score | 期望 0.9 或 0.1 | 实际 {'risk_score': 0.1}
- PASS | STRATIFIED 抽检计划 | 期望 [201] | 实际 201
- PASS | STRATIFIED 选中 high 层 | 期望 1bf7d234-ceae-4171-9fd2-c05f3b97b671 | 实际 1bf7d234-ceae-4171-9fd2-c05f3b97b671
- PASS | STRATIFIED 逐候选 stratum 落库 | 期望 high | 实际 high
- PASS | STRATIFIED risk_score 落库 | 期望 0.9 | 实际 0.9
- PASS | RISK_BASED 抽检计划 | 期望 [201] | 实际 201
- PASS | RISK_BASED 选中高风险候选 | 期望 1bf7d234-ceae-4171-9fd2-c05f3b97b671 | 实际 1bf7d234-ceae-4171-9fd2-c05f3b97b671
- PASS | RISK_BASED 高风险分落库 | 期望 0.9 | 实际 0.9
- PASS | 重复候选 → 422 | 期望 [422] | 实际 422
- PASS | 重复候选错误码 | 期望 validation_failed | 实际 validation_failed
- PASS | plan 幂等首次 | 期望 [201] | 实际 201
- PASS | plan 幂等重放 | 期望 [201] | 实际 201
- PASS | plan 幂等重放返回同一批样本 | 期望 ['f2dc0d63-3343-4311-a97c-7d3308dedaae'] | 实际 ['f2dc0d63-3343-4311-a97c-7d3308dedaae']
- PASS | plan 同键异体 → 409 | 期望 [409] | 实际 409
- PASS | plan 同键异体错误码 | 期望 idempotency_key_reused | 实际 idempotency_key_reused

## D7
- PASS | 发布后修改被拒 | 期望 [409] | 实际 409
- PASS | 发布后错误码 annotation_schema_immutable | 期望 annotation_schema_immutable | 实际 annotation_schema_immutable

## D8
- PASS | IAA 单标注者 → 201 | 期望 [201] | 实际 201
- PASS | IAA 状态 INSUFFICIENT_DATA | 期望 INSUFFICIENT_DATA | 实际 INSUFFICIENT_DATA
- PASS | IAA 样本不足返回指标 N/A 行 | 期望 >0 行 | 实际 12
- PASS | N/A 行覆盖全部 6 类指标 | 期望 ['BOUNDARY_MAE', 'COHEN_KAPPA', 'FLEISS_KAPPA', 'KRIPPENDORFF_ALPHA', 'PERCENT_AGREEMENT', 'SPAN_F1'] | 实际 ['BOUNDARY_MAE', 'COHEN_KAPPA', 'FLEISS_KAPPA', 'KRIPPENDORFF_ALPHA', 'PERCENT_AGREEMENT', 'SPAN_F1']
- PASS | N/A 行 value 为 null 且状态 INSUFFICIENT_DATA | 期望 全部 value=null, status=INSUFFICIENT_DATA | 实际 {'values': [None, None, None, None, None, None, None, None, None, None, None, None], 'statuses': ['INSUFFICIENT_DATA']}

## A9
- PASS | 迁移升级到 0012 | 期望 {'revision': '0012', 'triggers': 9} | 实际 {'revision': '0012', 'triggers': 9, 'tables': 3}
- PASS | 迁移升级到 0013 | 期望 {'revision': '0013', 'triggers': 23} | 实际 {'revision': '0013', 'triggers': 23, 'tables': 7}
- PASS | 迁移升级到 0014 | 期望 {'revision': '0014', 'triggers': 42} | 实际 {'revision': '0014', 'triggers': 42, 'tables': 11}
- PASS | 迁移升级到 0015 | 期望 {'revision': '0015', 'triggers': 58} | 实际 {'revision': '0015', 'triggers': 58, 'tables': 16}
- PASS | 0016 降级到 0015 后守卫函数保持修复版（no-op） | 期望 revision=0015 且 7/7 含 CAST | 实际 {'revision': '0015', 'has_cast': {'ddp_annotation_arbitration_guard': True, 'ddp_annotation_gold_set_guard': True, 'ddp_annotation_iaa_report_guard': True, 'ddp_annotation_quality_sample_guard': True, 'ddp_annotation_record_guard': True, 'ddp_annotation_review_guard': True, 'ddp_annotation_submission_guard': True}}
- PASS | 重新升级到 head(0016) | 期望 {'revision': '0016', 'triggers': 58} | 实际 {'revision': '0016', 'triggers': 58}

## A10
- PASS | 健康检查 | 期望 [200] | 实际 200
- PASS | Schema 幂等首次创建 | 期望 [201] | 实际 201
- PASS | Schema 幂等重放 | 期望 [201] | 实际 201
- PASS | 相同幂等键返回同一 Schema | 期望 09423eb0-1225-4600-bb01-d8123d7d1592 | 实际 09423eb0-1225-4600-bb01-d8123d7d1592
- PASS | Schema 同键异体 → 409 | 期望 [409] | 实际 409
- PASS | Schema 同键异体错误码 | 期望 idempotency_key_reused | 实际 idempotency_key_reused
- PASS | 项目外用户访问任务 → 403 | 期望 [403] | 实际 403
- PASS | 项目外用户错误码 | 期望 forbidden | 实际 forbidden
- PASS | 跨项目访问黄金集 → 404 | 期望 [404] | 实际 404
- PASS | 跨项目错误码 | 期望 resource_not_found | 实际 resource_not_found
- PASS | Schema 分页第一页 | 期望 [200] | 实际 200
- PASS | 分页 limit 生效 | 期望 ≤1 | 实际 1
- PASS | 分页返回 next_cursor | 期望 非空 cursor | 实际 CUI-sBIlRgC7AdgSPX0Vkg
- PASS | Schema 分页第二页 | 期望 [200] | 实际 200
- PASS | 分页游标不重复 | 期望 两页 ID 不相交 | 实际 {'first': ['09423eb0-1225-4600-bb01-d8123d7d1592'], 'second': ['43e23e23-0dbf-4f92-b8bd-23c62d696a48']}

