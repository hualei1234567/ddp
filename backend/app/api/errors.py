"""设备域异常到契约错误码的统一映射。

API 层是唯一把领域错误转换为 HTTP 语义的位置；领域与用例不依赖 HTTP。
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.domain.collection.errors import CollectionDomainError
from app.domain.devices.errors import DeviceDomainError
from app.domain.episodes.errors import EpisodeDomainError
from app.domain.ingestion.errors import IngestionDomainError

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
