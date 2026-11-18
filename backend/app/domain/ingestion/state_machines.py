"""上传会话与 RawAsset 状态机。

迁移表直接对应 ``state-machines.v1.json``：
- 只允许显式动作；
- 终态没有出边；
- 未知状态/动作一律拒绝，不存在默认成功分支。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.ingestion.enums import (
    PUBLIC_RAW_ASSET_ACTIONS,
    RawAssetStatus,
    UploadSessionAction,
    UploadSessionStatus,
)
from app.domain.ingestion.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)

UPLOAD_SESSION_TERMINAL: frozenset[UploadSessionStatus] = frozenset(
    {
        UploadSessionStatus.COMPLETED,
        UploadSessionStatus.ABORTED,
        UploadSessionStatus.EXPIRED,
        UploadSessionStatus.FAILED,
    }
)
RAW_ASSET_TERMINAL: frozenset[RawAssetStatus] = frozenset({RawAssetStatus.TOMBSTONED})

_UPLOAD_SESSION_RULES: Mapping[
    UploadSessionStatus,
    Mapping[str, UploadSessionStatus],
] = MappingProxyType(
    {
        UploadSessionStatus.CREATED: {
            UploadSessionAction.BEGIN_UPLOAD.value: UploadSessionStatus.UPLOADING,
            UploadSessionAction.COMPLETE.value: UploadSessionStatus.COMPLETING,
            UploadSessionAction.ABORT.value: UploadSessionStatus.ABORTED,
            UploadSessionAction.SYSTEM_EXPIRE.value: UploadSessionStatus.EXPIRED,
            UploadSessionAction.SYSTEM_FAIL.value: UploadSessionStatus.FAILED,
        },
        UploadSessionStatus.UPLOADING: {
            UploadSessionAction.COMPLETE.value: UploadSessionStatus.COMPLETING,
            UploadSessionAction.ABORT.value: UploadSessionStatus.ABORTED,
            UploadSessionAction.SYSTEM_EXPIRE.value: UploadSessionStatus.EXPIRED,
            UploadSessionAction.SYSTEM_FAIL.value: UploadSessionStatus.FAILED,
        },
        UploadSessionStatus.COMPLETING: {
            UploadSessionAction.SYSTEM_REGISTER.value: UploadSessionStatus.COMPLETED,
            UploadSessionAction.SYSTEM_RETRYABLE_FAILURE.value: UploadSessionStatus.UPLOADING,
            UploadSessionAction.SYSTEM_FAIL.value: UploadSessionStatus.FAILED,
        },
        UploadSessionStatus.COMPLETED: {},
        UploadSessionStatus.ABORTED: {},
        UploadSessionStatus.EXPIRED: {},
        UploadSessionStatus.FAILED: {},
    }
)

_RAW_ASSET_RULES: Mapping[RawAssetStatus, Mapping[str, RawAssetStatus]] = MappingProxyType(
    {
        RawAssetStatus.VALIDATING: {
            "validate-success": RawAssetStatus.VALID,
            "validate-quarantine": RawAssetStatus.QUARANTINED,
            "validate-reject": RawAssetStatus.REJECTED,
            "system-fail": RawAssetStatus.REJECTED,
            "tombstone": RawAssetStatus.TOMBSTONED,
        },
        RawAssetStatus.QUARANTINED: {
            "release-quarantine-or-revalidate": RawAssetStatus.VALIDATING,
            "reject-quarantined": RawAssetStatus.REJECTED,
            "tombstone": RawAssetStatus.TOMBSTONED,
        },
        RawAssetStatus.VALID: {
            "post-hoc-quarantine": RawAssetStatus.QUARANTINED,
            "archive": RawAssetStatus.ARCHIVED,
            "tombstone": RawAssetStatus.TOMBSTONED,
        },
        RawAssetStatus.REJECTED: {
            "tombstone": RawAssetStatus.TOMBSTONED,
        },
        RawAssetStatus.ARCHIVED: {
            "tombstone": RawAssetStatus.TOMBSTONED,
        },
        RawAssetStatus.TOMBSTONED: {},
    }
)


def upload_session_target_status(
    current: UploadSessionStatus,
    action: str,
) -> UploadSessionStatus:
    target = _UPLOAD_SESSION_RULES.get(current, {}).get(action)
    if target is None:
        raise InvalidStateTransitionError("上传会话", current.value, action)
    return target


def assert_upload_session_action(
    current: UploadSessionStatus,
    action: str,
) -> UploadSessionStatus:
    return upload_session_target_status(current, action)


def raw_asset_target_status(
    current: RawAssetStatus,
    action: str,
) -> RawAssetStatus:
    target = _RAW_ASSET_RULES.get(current, {}).get(action)
    if target is None:
        raise InvalidStateTransitionError("原始资产", current.value, action)
    return target


def allowed_raw_asset_actions(current: RawAssetStatus) -> frozenset[str]:
    """当前状态下**对外可点**的原始资产动作。

    只返回公开集合内的动作：``validate-success`` / ``validate-quarantine`` /
    ``validate-reject`` / ``system-fail`` 由应用层自行发起，不该出现在响应里，
    否则前端会渲染出按了必然 422 的按钮。
    """
    return frozenset(_RAW_ASSET_RULES.get(current, {})) & PUBLIC_RAW_ASSET_ACTIONS


def allowed_upload_session_actions(current: UploadSessionStatus) -> frozenset[str]:
    return frozenset(_UPLOAD_SESSION_RULES.get(current, {}))


def validate_upload_session_action(action: object) -> str:
    if not isinstance(action, str) or not action.strip():
        raise DomainValidationError("action 必须是非空字符串")
    value = action.strip()
    known = {name for rules in _UPLOAD_SESSION_RULES.values() for name in rules}
    if value not in known:
        raise DomainValidationError("未知上传会话动作")
    return value


def upload_session_transition_pairs() -> frozenset[tuple[str, str]]:
    """供数据库兜底触发器生成白名单。"""
    return frozenset(
        (current.value, target.value)
        for current, rules in _UPLOAD_SESSION_RULES.items()
        for target in rules.values()
    )


def raw_asset_transition_pairs() -> frozenset[tuple[str, str]]:
    """供数据库兜底触发器生成白名单。"""
    return frozenset(
        (current.value, target.value)
        for current, rules in _RAW_ASSET_RULES.items()
        for target in rules.values()
    )
