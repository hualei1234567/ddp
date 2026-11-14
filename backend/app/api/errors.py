"""设备域异常到契约错误码的统一映射。

API 层是唯一把领域错误转换为 HTTP 语义的位置；领域与用例不依赖 HTTP。
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.domain.annotation.errors import AnnotationDomainError
from app.domain.collection.errors import CollectionDomainError
from app.domain.datasets.errors import DatasetDomainError
from app.domain.datasources.errors import DataSourceDomainError
from app.domain.devices.errors import DeviceDomainError
from app.domain.episodes.errors import EpisodeDomainError
from app.domain.ingestion.errors import IngestionDomainError
from app.domain.modelops.errors import ModelOpsDomainError

_STATUS_BY_CODE = {
    "validation_failed": 422,
    "calibration_invalid": 422,
    "calibration_target_invalid": 422,
    "calibration_profile_invalid": 422,
    "calibration_checksum_mismatch": 422,
    "calibration_not_active": 422,
    "state_transition_invalid": 409,
    "version_conflict": 409,
    "resource_immutable": 409,
    "duplicate_resource": 409,
    "calibration_active_conflict": 409,
    "idempotency_key_reused": 409,
    "resource_not_found": 404,
    "forbidden": 403,
}

# 对外错误文案固定且不含客户端输入；未知领域码统一收敛到 validation_failed。
_MESSAGES = {
    "validation_failed": "请求参数或领域规则校验失败",
    "calibration_invalid": "标定数据校验失败",
    "calibration_target_invalid": "标定目标不存在、跨项目或类型不匹配",
    "calibration_profile_invalid": "标定配置不存在、跨项目或与记录不匹配",
    "calibration_checksum_mismatch": "标定校验和不匹配",
    "calibration_not_active": "标定记录未激活",
    "state_transition_invalid": "资源状态不允许该操作",
    "version_conflict": "资源版本已变化，请重新读取后再试",
    "resource_immutable": "资源已冻结或包含不可变字段",
    "duplicate_resource": "项目内业务键或序列号重复",
    "calibration_active_conflict": "同一标定目标已存在激活记录",
    "idempotency_key_reused": "同一幂等键对应了不同的请求体",
    "resource_not_found": "资源不存在或不属于当前项目",
    "forbidden": "无权执行该操作",
}
_FALLBACK_MESSAGE = "设备域规则校验失败"

_COLLECTION_STATUS_BY_CODE = {
    "validation_failed": 422,
    "state_transition_invalid": 409,
    "version_conflict": 409,
    "resource_immutable": 409,
    "duplicate_resource": 409,
    "conflict": 409,
    "idempotency_key_reused": 409,
    "resource_not_found": 404,
    "forbidden": 403,
}

_COLLECTION_MESSAGES = {
    "validation_failed": "请求参数或采集域规则校验失败",
    "state_transition_invalid": "采集任务或批次不允许该状态迁移",
    "version_conflict": "资源版本已变化，请重新读取后再试",
    "resource_immutable": "资源当前状态不允许修改",
    "duplicate_resource": "项目内业务键重复",
    "conflict": "采集任务或批次存在未处理冲突",
    "idempotency_key_reused": "同一幂等键对应了不同请求体",
    "resource_not_found": "资源不存在或不属于当前项目",
    "forbidden": "无权执行该操作",
}


def collection_error_to_app(exc: CollectionDomainError) -> AppError:
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or code not in _COLLECTION_STATUS_BY_CODE:
        return AppError("validation_failed", "采集域规则校验失败", 422)
    return AppError(
        code,
        _COLLECTION_MESSAGES.get(code, "采集域规则校验失败"),
        _COLLECTION_STATUS_BY_CODE[code],
    )


_INGESTION_STATUS_BY_CODE = {
    "validation_failed": 422,
    "filename_invalid": 422,
    "file_type_unsupported": 415,
    "file_too_large": 413,
    "upload_session_expired": 410,
    "upload_incomplete": 409,
    "upload_part_conflict": 409,
    "upload_session_not_uploading": 409,
    "file_signature_mismatch": 422,
    "hash_mismatch": 422,
    "size_mismatch": 422,
    "object_storage_unavailable": 503,
    "object_missing": 404,
    "asset_not_usable": 422,
    "job_not_retryable": 409,
    "job_timeout": 409,
    "idempotency_key_reused": 409,
    "state_transition_invalid": 409,
    "version_conflict": 409,
    "resource_immutable": 409,
    "conflict": 409,
    "resource_not_found": 404,
    "forbidden": 403,
}

_INGESTION_MESSAGES = {
    "validation_failed": "请求参数或上传域规则校验失败",
    "filename_invalid": "文件名包含非法路径或控制字符",
    "file_type_unsupported": "声明的内容类型不在白名单内",
    "file_too_large": "文件超过允许的最大大小",
    "upload_session_expired": "上传会话已过期，请新建会话",
    "upload_incomplete": "上传分片不完整，请补传缺失分片",
    "upload_part_conflict": "同分片号已存在不同内容",
    "upload_session_not_uploading": "上传会话当前状态不允许该操作",
    "file_signature_mismatch": "对象内容签名与声明格式不一致",
    "hash_mismatch": "整文件 Hash 不一致",
    "size_mismatch": "分片总大小与声明大小不一致",
    "object_storage_unavailable": "对象存储暂不可用，请稍后重试",
    "object_missing": "对象存储中的内容不存在",
    "asset_not_usable": "原始资产当前状态不可用于该操作",
    "job_not_retryable": "接入任务已达到最大重试次数，不能重试",
    "job_timeout": "接入任务尚未超过超时阈值",
    "idempotency_key_reused": "同一幂等键对应了不同请求体",
    "state_transition_invalid": "上传会话或资产不允许该状态迁移",
    "version_conflict": "资源版本已变化，请重新读取后再试",
    "resource_immutable": "资产内容不可变",
    "conflict": "上传会话或资产存在冲突",
    "resource_not_found": "资源不存在或不属于当前项目",
    "forbidden": "无权执行该操作",
}


def ingestion_error_to_app(exc: IngestionDomainError) -> AppError:
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or code not in _INGESTION_STATUS_BY_CODE:
        return AppError("validation_failed", "上传域规则校验失败", 422)
    details = dict(getattr(exc, "details", {}) or {})
    return AppError(
        code,
        _INGESTION_MESSAGES.get(code, "上传域规则校验失败"),
        _INGESTION_STATUS_BY_CODE[code],
        details=details or None,
    )


_EPISODE_STATUS_BY_CODE = {
    "validation_failed": 422,
    "state_transition_invalid": 409,
    "version_conflict": 409,
    "resource_immutable": 409,
    "resource_not_found": 404,
    "forbidden": 403,
    "conflict": 409,
    "episode_overlap_conflict": 409,
    "manifest_hash_mismatch": 422,
    "manifest_schema_invalid": 422,
    "calibration_not_active": 422,
    "episode_time_base_invalid": 422,
    "lineage_conflict": 409,
}

_EPISODE_MESSAGES = {
    "validation_failed": "请求参数或 Episode 规则校验失败",
    "state_transition_invalid": "Episode 当前状态不允许该操作",
    "version_conflict": "Episode 版本已变化，请重新读取后再试",
    "resource_immutable": "Episode 已冻结，数据字段不可修改",
    "resource_not_found": "Episode 或血缘节点不存在、不属于当前项目",
    "forbidden": "无权执行该操作",
    "conflict": "Episode 存在冲突",
    "episode_overlap_conflict": "同一原始资产的时间区间已被其他 Episode 使用",
    "manifest_hash_mismatch": "Manifest Hash 校验失败",
    "manifest_schema_invalid": "Manifest 结构或身份字段非法",
    "calibration_not_active": "引用的标定记录未激活",
    "episode_time_base_invalid": "时间基准不合法",
    "lineage_conflict": "血缘边重复、跨项目或成环",
}


def episode_error_to_app(exc: EpisodeDomainError) -> AppError:
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or code not in _EPISODE_STATUS_BY_CODE:
        return AppError("validation_failed", "Episode 域规则校验失败", 422)
    details = dict(getattr(exc, "details", {}) or {})
    return AppError(
        code,
        _EPISODE_MESSAGES.get(code, "Episode 域规则校验失败"),
        _EPISODE_STATUS_BY_CODE[code],
        details=details or None,
    )


_ANNOTATION_STATUS_BY_CODE = {
    "validation_failed": 422,
    "resource_not_found": 404,
    "forbidden": 403,
    "state_transition_invalid": 409,
    "version_conflict": 409,
    "resource_immutable": 409,
    "duplicate_resource": 409,
    "conflict": 409,
    "idempotency_key_reused": 409,
    "annotation_schema_not_published": 422,
    "annotation_schema_immutable": 409,
    "annotation_schema_snapshot_mismatch": 422,
    "annotation_sample_set_empty": 422,
    "annotation_sample_set_not_snapshotted": 409,
    "annotation_sample_set_cancelled": 409,
    "annotation_sample_episode_ineligible": 422,
    "annotation_sample_duplicate": 422,
    "annotation_task_not_approved": 409,
    "annotation_task_not_ready": 409,
    "annotation_task_has_open_work": 409,
    "annotation_task_rework_limit_exceeded": 409,
    "annotation_batch_not_compatible": 409,
    "annotation_batch_over_capacity": 409,
    "annotation_assignment_capacity_exceeded": 409,
    "annotation_assignment_not_owned": 403,
    "annotation_assignment_expired": 410,
    "annotation_assignment_revoked": 410,
    "annotation_item_already_assigned": 409,
    "annotation_submission_not_editable": 409,
    "annotation_submission_incomplete": 422,
    "annotation_submission_schema_mismatch": 422,
    "annotation_submission_not_submitted": 409,
    "annotation_submission_already_decided": 409,
    "annotation_record_digest_mismatch": 422,
    "annotation_schema_unknown_label": 422,
    "annotation_schema_value_invalid": 422,
    "annotation_review_self_forbidden": 403,
    "annotation_review_not_pending": 409,
    "annotation_review_required": 409,
    "annotation_arbitration_required": 409,
    "annotation_arbitration_not_pending": 409,
    "annotation_arbitration_quorum_not_met": 409,
    "annotation_arbiter_conflict": 403,
    "gold_set_access_denied": 403,
    "gold_set_not_active": 409,
    "gold_set_schema_incompatible": 409,
    "gold_set_item_exposure_forbidden": 403,
    "quality_sample_not_reviewable": 409,
    "quality_sample_policy_missing": 422,
    "quality_sample_self_review_forbidden": 403,
    "iaa_insufficient_overlap": 422,
    "iaa_source_forbidden": 403,
}

_ANNOTATION_MESSAGES = {
    "validation_failed": "请求参数或标注域规则校验失败",
    "resource_not_found": "资源不存在或不属于当前项目",
    "forbidden": "无权执行该操作",
    "state_transition_invalid": "资源状态不允许该操作",
    "version_conflict": "资源版本已变化，请重新读取后再试",
    "resource_immutable": "资源当前状态不允许修改",
    "duplicate_resource": "项目内业务键重复",
    "conflict": "标注资源存在未处理冲突",
    "idempotency_key_reused": "同一幂等键对应了不同请求体",
    "annotation_schema_not_published": "引用的标注 Schema 未发布或不可用",
    "annotation_schema_immutable": "已发布 Schema 的标签定义不可原地修改",
    "annotation_schema_snapshot_mismatch": "Schema 快照不可复算或已漂移",
    "annotation_sample_set_empty": "样本集为空，不能生成快照",
    "annotation_sample_set_not_snapshotted": "样本集未快照或快照不可用",
    "annotation_sample_set_cancelled": "样本集已取消",
    "annotation_sample_episode_ineligible": "Episode 不满足样本入选条件",
    "annotation_sample_duplicate": "样本重复",
    "annotation_task_not_approved": "任务未批准，不能执行该操作",
    "annotation_task_not_ready": "任务未就绪",
    "annotation_task_has_open_work": "任务存在未结工作",
    "annotation_task_rework_limit_exceeded": "返工次数超过上限",
    "annotation_batch_not_compatible": "批次与任务或样本集不兼容",
    "annotation_batch_over_capacity": "批次计划量超过任务目标",
    "annotation_assignment_capacity_exceeded": "分配超过人员容量",
    "annotation_assignment_not_owned": "该分配不属于当前用户",
    "annotation_assignment_expired": "分配已过期，需重新分配",
    "annotation_assignment_revoked": "分配已撤销",
    "annotation_item_already_assigned": "标注项已被分配",
    "annotation_submission_not_editable": "提交当前状态不允许编辑",
    "annotation_submission_incomplete": "提交内容不完整",
    "annotation_submission_schema_mismatch": "提交 Schema 快照与任务不一致",
    "annotation_submission_not_submitted": "提交尚未提交，不能执行该操作",
    "annotation_submission_already_decided": "提交已作出终态结论",
    "annotation_record_digest_mismatch": "标注记录摘要校验失败",
    "annotation_schema_unknown_label": "标签未在 Schema 中定义",
    "annotation_schema_value_invalid": "标签值不符合 Schema 约束",
    "annotation_review_self_forbidden": "复核人不得是标注人",
    "annotation_review_not_pending": "复核不在待处理状态",
    "annotation_review_required": "该操作要求先完成复核",
    "annotation_arbitration_required": "该操作要求先完成仲裁",
    "annotation_arbitration_not_pending": "仲裁不在可操作状态",
    "annotation_arbitration_quorum_not_met": "仲裁投票未达到法定人数",
    "annotation_arbiter_conflict": "仲裁人与标注人或复核人冲突",
    "gold_set_access_denied": "无权查看黄金集期望答案",
    "gold_set_not_active": "黄金集未激活",
    "gold_set_schema_incompatible": "黄金集 Schema 快照不兼容",
    "gold_set_item_exposure_forbidden": "黄金项曝光次数或轮换策略不允许",
    "quality_sample_not_reviewable": "抽检当前状态不允许复核",
    "quality_sample_policy_missing": "缺少有效抽样策略版本",
    "quality_sample_self_review_forbidden": "标注员不得自行选择或复核抽检样本",
    "iaa_insufficient_overlap": "重叠样本不足，无法给出 IAA 结论",
    "iaa_source_forbidden": "IAA 只能基于真实提交重叠计算",
}
_ANNOTATION_FALLBACK_MESSAGE = "标注域规则校验失败"


def annotation_error_to_app(exc: AnnotationDomainError) -> AppError:
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or code not in _ANNOTATION_STATUS_BY_CODE:
        return AppError("validation_failed", _ANNOTATION_FALLBACK_MESSAGE, 422)
    return AppError(
        code,
        _ANNOTATION_MESSAGES.get(code, _ANNOTATION_FALLBACK_MESSAGE),
        _ANNOTATION_STATUS_BY_CODE[code],
    )


def domain_error_to_app(exc: DeviceDomainError) -> AppError:
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or code not in _STATUS_BY_CODE:
        # 不把未知异常的 str(exc) 回显给客户端，避免泄漏内部标识或输入。
        return AppError("validation_failed", _FALLBACK_MESSAGE, 422)
    return AppError(
        code,
        _MESSAGES.get(code, _FALLBACK_MESSAGE),
        _STATUS_BY_CODE[code],
    )


_DATASET_STATUS_BY_CODE = {
    "validation_failed": 422,
    "dataset_access_denied": 403,
    "dataset_freeze_approval_forbidden": 403,
    "dataset_leakage_waiver_forbidden": 403,
    "forbidden": 403,
    "resource_not_found": 404,
    "state_transition_invalid": 409,
    "version_conflict": 409,
    "resource_immutable": 409,
    "dataset_version_immutable": 409,
    "dataset_version_not_frozen": 409,
    "dataset_not_ready": 409,
    "dataset_leakage_check_failed": 409,
    "dataset_leakage_check_unknown": 409,
    "dataset_quality_gate_failed": 409,
    "dataset_annotation_not_approved": 409,
    "dataset_upstream_invalidated": 409,
    "dataset_manifest_mismatch": 409,
    "dataset_freeze_requires_approval": 409,
    "dataset_archive_blocked": 409,
    "duplicate_resource": 409,
    "conflict": 409,
    "idempotency_key_reused": 409,
    "dataset_empty": 422,
    "dataset_insufficient_data": 422,
    "dataset_item_ineligible": 422,
    "dataset_split_invalid": 422,
    "dataset_annotation_snapshot_mismatch": 422,
    "export_format_unsupported": 422,
    "export_format_version_unsupported": 422,
    "export_config_invalid": 422,
    "export_checksum_mismatch": 422,
    "export_job_not_retryable": 409,
    "export_job_cancelled": 410,
    "export_incomplete": 409,
    "export_output_conflict": 409,
    "export_artifact_verify_failed": 409,
    "export_job_failed": 409,
    "export_partial": 409,
    "export_job_timeout": 409,
    "dataset_grant_forbidden": 403,
    "dataset_grant_expired": 410,
    "dataset_grant_revoked": 410,
    "training_request_incomplete": 422,
    "training_request_immutable": 409,
    "training_request_approval_forbidden": 403,
    "training_request_revision_conflict": 409,
    "training_request_not_approved": 409,
    "training_delivery_not_ready": 409,
    "training_delivery_acceptance_forbidden": 403,
    "training_delivery_already_accepted": 409,
    "training_delivery_mismatch": 422,
    "training_adapter_unavailable": 503,
    "training_adapter_unsupported": 422,
    "training_external_run_conflict": 409,
    "training_callback_invalid": 401,
    "training_callback_replayed": 409,
}

_DATASET_MESSAGES = {
    "validation_failed": "请求参数或数据集域规则校验失败",
    "dataset_access_denied": "无权访问该数据集资源",
    "dataset_freeze_approval_forbidden": "构建人不得作为唯一冻结审批人",
    "dataset_leakage_waiver_forbidden": "硬规则不可豁免，或豁免未经策略授权",
    "forbidden": "无权执行该操作",
    "resource_not_found": "资源不存在或不属于当前项目",
    "state_transition_invalid": "数据集版本当前状态不允许该动作",
    "version_conflict": "资源版本已变化，请重新读取后再试",
    "resource_immutable": "资源已冻结或包含不可变字段",
    "dataset_version_immutable": "冻结或终态数据集版本不可原地修改",
    "dataset_version_not_frozen": "数据集版本尚未冻结",
    "dataset_not_ready": "数据集版本尚未就绪",
    "dataset_leakage_check_failed": "泄漏硬规则失败，禁止冻结与导出",
    "dataset_leakage_check_unknown": "泄漏检查无法完成，按失败关闭",
    "dataset_quality_gate_failed": "质量门禁未通过",
    "dataset_annotation_not_approved": "引用了未批准的标注",
    "dataset_upstream_invalidated": "上游数据失效，数据集版本已陈旧",
    "dataset_manifest_mismatch": "Manifest Hash 或内容不一致",
    "dataset_freeze_requires_approval": "缺少独立 QA 核验或冻结审批",
    "dataset_archive_blocked": "归档或恢复条件不满足",
    "duplicate_resource": "项目内业务键重复",
    "conflict": "数据集资源存在未处理冲突",
    "idempotency_key_reused": "同一幂等键对应了不同请求体",
    "dataset_empty": "入选样本为空，不能创建数据集版本",
    "dataset_insufficient_data": "候选数据不足，或训练/验证/测试划分非法",
    "dataset_item_ineligible": "Episode/标注/Manifest/标定/时间同步不合格",
    "dataset_split_invalid": "划分比例、计数或分组非法",
    "dataset_annotation_snapshot_mismatch": "标注/Schema 快照不一致",
    "export_format_unsupported": "不支持的导出格式，不得降级为其他格式",
    "export_format_version_unsupported": "不支持的导出格式版本",
    "export_config_invalid": "导出配置或字段映射非法",
    "export_checksum_mismatch": "导出产物逐文件或汇总 Hash 不一致",
    "export_job_not_retryable": "导出任务不可重试或已达最大尝试次数",
    "export_job_cancelled": "导出任务已取消，不能继续操作",
    "export_incomplete": "导出产物不完整，不能用于交付",
    "export_output_conflict": "导出输出路径或产物冲突",
    "export_artifact_verify_failed": "导出产物回读校验失败",
    "export_job_failed": "导出任务执行失败",
    "export_partial": "仅写出部分产物，不得视为成功",
    "export_job_timeout": "导出任务执行超时",
    "dataset_grant_forbidden": "无权创建或审批数据集授权",
    "dataset_grant_expired": "数据集授权已过期，立即失效",
    "dataset_grant_revoked": "数据集授权已撤销，立即失效",
    "training_request_incomplete": "训练需求必填字段、格式或阈值不完整",
    "training_request_immutable": "已批准需求不可原地修改，请创建新 revision",
    "training_request_approval_forbidden": "提交人不得审批或驳回自己的需求",
    "training_request_revision_conflict": "需求 revision 冲突或已被取代",
    "training_request_not_approved": "需求 revision 未批准，不能进入交付",
    "training_delivery_not_ready": "交付所需的数据集版本或导出产物未就绪",
    "training_delivery_acceptance_forbidden": "交付创建人不得验收自己创建的交付",
    "training_delivery_already_accepted": "交付已验收，不能重复修改",
    "training_delivery_mismatch": "交付与需求 revision、数据集版本或导出任务不匹配",
    "training_adapter_unavailable": "外部训练平台当前不可达",
    "training_adapter_unsupported": "训练 Adapter 不支持该能力",
    "training_external_run_conflict": "外部训练 run 或幂等键冲突",
    "training_callback_invalid": "回调签名、时间窗或 nonce 非法",
    "training_callback_replayed": "回调重放已被拒绝",
}
_DATASET_FALLBACK = "数据集域规则校验失败"


def dataset_error_to_app(exc: DatasetDomainError) -> AppError:
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or code not in _DATASET_STATUS_BY_CODE:
        return AppError("validation_failed", _DATASET_FALLBACK, 422)
    return AppError(
        code,
        _DATASET_MESSAGES.get(code, _DATASET_FALLBACK),
        _DATASET_STATUS_BY_CODE[code],
    )


_MODELOPS_STATUS_BY_CODE = {
    "validation_failed": 422,
    "resource_not_found": 404,
    "forbidden": 403,
    "state_transition_invalid": 409,
    "version_conflict": 409,
    "idempotency_key_reused": 409,
    "duplicate_resource": 409,
    "conflict": 409,
    "model_not_active": 409,
    "model_version_immutable": 409,
    "model_version_in_use": 409,
    "model_release_approval_forbidden": 403,
    "model_artifact_invalid": 422,
    "model_artifact_checksum_mismatch": 422,
    "model_runtime_invalid": 422,
    "model_training_source_invalid": 422,
    "evaluation_dataset_not_ready": 409,
    "evaluation_immutable": 409,
    "evaluation_metrics_invalid": 422,
    "evaluation_baseline_invalid": 422,
    "inference_service_unavailable": 503,
    "inference_service_not_active": 409,
    "inference_task_input_invalid": 422,
    "inference_task_result_invalid": 422,
    "feedback_dimensions_invalid": 422,
    "feedback_source_invalid": 422,
    "feedback_already_converted": 409,
    "feedback_terminal": 409,
    "data_requirement_approval_forbidden": 403,
    "data_requirement_not_satisfied": 409,
    "data_requirement_dataset_invalid": 422,
    "data_requirement_retest_invalid": 422,
    "data_requirement_terminal": 409,
    "model_adapter_unavailable": 503,
    "model_adapter_unsupported": 422,
    "model_callback_invalid": 401,
    "model_callback_replayed": 409,
}


def modelops_error_to_app(exc: ModelOpsDomainError) -> AppError:
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or code not in _MODELOPS_STATUS_BY_CODE:
        return AppError("validation_failed", "模型闭环规则校验失败", 422)
    message = str(exc) or "模型闭环规则校验失败"
    return AppError(code, message, _MODELOPS_STATUS_BY_CODE[code])


def integrity_error_to_app(exc: IntegrityError) -> AppError:
    """不把驱动 SQL、表名或参数回显给客户端。"""
    if _is_unique_violation(exc):
        return AppError("duplicate_resource", _MESSAGES["duplicate_resource"], 409)
    return AppError("conflict", "数据完整性约束冲突", 409)


def _is_unique_violation(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return any(
        token in message
        for token in ("unique", "duplicate", "uq_", "duplicate key", "duplicate entry")
    )


_SUPPLIER_STATUS_BY_CODE = {
    "validation_failed": 422,
    "resource_not_found": 404,
    "forbidden": 403,
    "state_transition_invalid": 409,
    "version_conflict": 409,
    "duplicate_resource": 409,
    "idempotency_key_reused": 409,
    "supplier_not_active": 409,
    "supplier_terminal": 409,
    "batch_not_screening": 409,
    "batch_review_incomplete": 409,
    "batch_already_settled": 409,
    "segment_terminal": 409,
    "segment_decision_forbidden": 403,
    "decision_reason_required": 422,
    "segment_raw_asset_missing": 422,
    "ledger_immutable": 409,
}

_SUPPLIER_MESSAGES = {
    "idempotency_key_reused": "同一幂等键对应了不同的请求体",
    "supplier_not_active": "供应商未激活，不能承接新批次",
    "supplier_terminal": "供应商已终止，不能继续操作",
    "batch_not_screening": "只有 SCREENING 状态的批次可以裁决片段",
    "batch_review_incomplete": "仍有片段未裁决或待补件，不能终审",
    "batch_already_settled": "批次已终审，不能继续操作",
    "segment_terminal": "片段已裁决，不能再次裁决",
    "segment_decision_forbidden": "片段登记人不得裁决自己登记的片段",
    "decision_reason_required": "拒收或待补件必须填写原因",
    "segment_raw_asset_missing": "已通过片段缺少 RawAsset 引用",
    "ledger_immutable": "验收账本只允许追加，不能修改或删除",
}
_SUPPLIER_FALLBACK = "供应商域规则校验失败"


def supplier_error_to_app(exc: Exception) -> AppError:
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or code not in _SUPPLIER_STATUS_BY_CODE:
        return AppError("validation_failed", _SUPPLIER_FALLBACK, 422)
    return AppError(
        code,
        _SUPPLIER_MESSAGES.get(code) or str(exc) or _SUPPLIER_FALLBACK,
        _SUPPLIER_STATUS_BY_CODE[code],
    )


_DATASOURCE_STATUS_BY_CODE = {
    "validation_failed": 422,
    "state_transition_invalid": 409,
    "version_conflict": 409,
    "duplicate_resource": 409,
    "idempotency_key_reused": 409,
    "resource_not_found": 404,
    "forbidden": 403,
}

_DATASOURCE_MESSAGES = {
    "state_transition_invalid": "导入任务状态不允许该操作",
    "version_conflict": "资源版本已变化，请重新读取后再试",
    "duplicate_resource": "项目内数据源编码或 repo/revision 已登记",
    "idempotency_key_reused": "同一幂等键对应了不同的请求体",
    "resource_not_found": "资源不存在或不属于当前项目",
}
_DATASOURCE_FALLBACK = "数据源域规则校验失败"


def datasource_error_to_app(exc: DataSourceDomainError) -> AppError:
    """数据源域错误映射；未登记的 code 一律收敛到 validation_failed。"""
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or code not in _DATASOURCE_STATUS_BY_CODE:
        # 不把 str(exc) 回显，避免泄漏内部标识或客户端输入。
        return AppError("validation_failed", _DATASOURCE_FALLBACK, 422)
    return AppError(
        code,
        _DATASOURCE_MESSAGES.get(code, _DATASOURCE_FALLBACK),
        _DATASOURCE_STATUS_BY_CODE[code],
    )
