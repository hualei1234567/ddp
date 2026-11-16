"""Episode 状态机与 READY/ACCEPTED 冻结边界。"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.episodes.enums import EpisodeStatus
from app.domain.episodes.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)

EPISODE_TERMINAL: frozenset[EpisodeStatus] = frozenset(
    {
        EpisodeStatus.REJECTED,
        EpisodeStatus.ARCHIVED,
        EpisodeStatus.INVALIDATED,
    }
)

# READY 起冻结数据字段；ACCEPTED 起额外冻结质量结论。
EPISODE_DATA_FROZEN_STATUSES: frozenset[EpisodeStatus] = frozenset(
    {
        EpisodeStatus.READY,
        EpisodeStatus.QUALITY_CHECKING,
        EpisodeStatus.ACCEPTED,
        EpisodeStatus.COMPLETED,
        EpisodeStatus.ARCHIVED,
    }
)
EPISODE_QUALITY_FROZEN_STATUSES: frozenset[EpisodeStatus] = frozenset(
    {
        EpisodeStatus.ACCEPTED,
        EpisodeStatus.COMPLETED,
        EpisodeStatus.ARCHIVED,
    }
)

# 「可用于训练」的 Episode 状态口径。
# datasets.selection 与 annotation.selection 目前各自内联同一份值；这两个域与
# episodes 域没有依赖关系，为收敛而新增领域间依赖不划算，因此本次只在
# episodes 域内提供权威常量，供新代码（缺口覆盖统计）引用，不改既有三处。
EPISODE_COVERABLE_STATUSES: frozenset[EpisodeStatus] = frozenset(
    {
        EpisodeStatus.ACCEPTED,
        EpisodeStatus.COMPLETED,
    }
)

# 缺口视图「已覆盖」口径：数据已落定、可被统计与复用，但不要求通过训练门禁。
# READY 起 manifest 与数据字段冻结（见 EPISODE_DATA_FROZEN_STATUSES），
# 因此 READY/QUALITY_CHECKING 的 Episode 已经"有数据"，只是质检结论未定。
# 与 EPISODE_COVERABLE_STATUSES 的区别是有意为之：若只算 ACCEPTED/COMPLETED，
# QUALITY_STATUS 这个统计轴会退化成恒等于 PASS，失去诊断价值。
EPISODE_COVERAGE_STATUSES: frozenset[EpisodeStatus] = frozenset(
    {
        EpisodeStatus.READY,
        EpisodeStatus.QUALITY_CHECKING,
        EpisodeStatus.ACCEPTED,
        EpisodeStatus.COMPLETED,
    }
)

EPISODE_DATA_FROZEN_FIELDS: tuple[str, ...] = (
    "episode_lineage_id",
    "episode_version",
    "code",
    "task_id",
    "batch_id",
    "robot_id",
    "device_ids",
    "sensor_ids",
    "start_time_ns",
    "end_time_ns",
    "duration_ns",
    "time_base",
    "canonical_schema_version",
    "pipeline_id",
    "pipeline_version",
    "segmentation_rule_version",
    "calibration_snapshot",
    "frame_count",
    "manifest_key",
    "manifest_sha256",
    "supersedes_id",
    "success",
    "scene_labels",
    "language_instruction",
)

_EPISODE_RULES: Mapping[EpisodeStatus, Mapping[str, EpisodeStatus]] = MappingProxyType(
    {
        EpisodeStatus.PENDING: {
            "system-start": EpisodeStatus.PROCESSING,
            "invalidate": EpisodeStatus.INVALIDATED,
        },
        EpisodeStatus.PROCESSING: {
            "system-ready": EpisodeStatus.READY,
            "system-fail": EpisodeStatus.FAILED,
            "invalidate": EpisodeStatus.INVALIDATED,
        },
        EpisodeStatus.READY: {
            "quality-check": EpisodeStatus.QUALITY_CHECKING,
            "invalidate": EpisodeStatus.INVALIDATED,
        },
        EpisodeStatus.QUALITY_CHECKING: {
            "accept": EpisodeStatus.ACCEPTED,
            "reject": EpisodeStatus.REJECTED,
            "invalidate": EpisodeStatus.INVALIDATED,
        },
        EpisodeStatus.ACCEPTED: {
            "complete": EpisodeStatus.COMPLETED,
            "invalidate": EpisodeStatus.INVALIDATED,
        },
        EpisodeStatus.COMPLETED: {
            "archive": EpisodeStatus.ARCHIVED,
            "invalidate": EpisodeStatus.INVALIDATED,
        },
        EpisodeStatus.FAILED: {
            "retry": EpisodeStatus.PROCESSING,
            "invalidate": EpisodeStatus.INVALIDATED,
        },
        EpisodeStatus.REJECTED: {},
        EpisodeStatus.ARCHIVED: {},
        EpisodeStatus.INVALIDATED: {},
    }
)


def episode_target_status(current: EpisodeStatus, action: str) -> EpisodeStatus:
    target = _EPISODE_RULES.get(current, {}).get(action)
    if target is None:
        raise InvalidStateTransitionError("Episode", current.value, action)
    return target


def validate_episode_action(action: object) -> str:
    if not isinstance(action, str) or not action.strip():
        raise DomainValidationError("action 必须是非空字符串")
    value = action.strip()
    known = {name for rules in _EPISODE_RULES.values() for name in rules}
    if value not in known:
        raise DomainValidationError("未知 Episode 动作")
    return value


def allowed_episode_actions(current: EpisodeStatus) -> frozenset[str]:
    return frozenset(_EPISODE_RULES.get(current, {}))


def episode_transition_pairs() -> frozenset[tuple[str, str]]:
    """供数据库兜底触发器生成白名单。"""
    return frozenset(
        (current.value, target.value)
        for current, rules in _EPISODE_RULES.items()
        for target in rules.values()
    )
