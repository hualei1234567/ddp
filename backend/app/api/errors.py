"""设备域异常到契约错误码的统一映射。

API 层是唯一把领域错误转换为 HTTP 语义的位置；领域与用例不依赖 HTTP。
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
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

_MESSAGES = {
    "validation_failed": "请求参数或领域规则校验失败",
    "calibration_target_invalid": "标定目标不存在、跨项目或类型不匹配",
    "calibration_profile_invalid": "标定配置不存在、跨项目或与记录不匹配",
    "state_transition_invalid": "资源状态不允许该操作",
    "version_conflict": "资源版本已变化，请重新读取后再试",
    "resource_immutable": "资源已冻结或包含不可变字段",
    "duplicate_resource": "项目内业务键或序列号重复",
    "resource_not_found": "资源不存在或不属于当前项目",
}


def domain_error_to_app(exc: DeviceDomainError) -> AppError:
    code = getattr(exc, "code", "validation_failed")
    status_code = _STATUS_BY_CODE.get(code, 422)
    message = _MESSAGES.get(code, str(exc))
    return AppError(code, message, status_code)


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
