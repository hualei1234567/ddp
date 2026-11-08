"""设备域异常到契约错误码的统一映射。

API 层是唯一把领域错误转换为 HTTP 语义的位置；领域与用例不依赖 HTTP。
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.domain.collection.errors import CollectionDomainError
from app.domain.devices.errors import DeviceDomainError

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
