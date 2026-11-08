"""Dataset / DatasetVersion 状态机。

迁移表对应 ``state-machines.v1.json``：
- 只允许显式列出的具名动作；未知状态/动作一律拒绝；
- 终态不得回退；冻结后内容不可原地修改；
- validity_status 与生命周期状态分离。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.datasets.enums import DatasetStatus, DatasetVersionStatus, ValidityStatus
from app.domain.datasets.errors import DomainValidationError, InvalidStateTransitionError

_TRANSITION = tuple[str, str]

_DATASET_RULES: Mapping[DatasetStatus, Mapping[str, DatasetStatus]] = MappingProxyType(
    {
        DatasetStatus.DRAFT: {
            "activate": DatasetStatus.ACTIVE,
            "archive": DatasetStatus.ARCHIVED,
        },
        DatasetStatus.ACTIVE: {
            "archive": DatasetStatus.ARCHIVED,
        },
        DatasetStatus.ARCHIVED: {
            "restore": DatasetStatus.ACTIVE,
        },
    }
)

_VERSION_RULES: Mapping[DatasetVersionStatus, Mapping[str, DatasetVersionStatus]] = MappingProxyType(
    {
        DatasetVersionStatus.DRAFT: {
            "build": DatasetVersionStatus.BUILDING,
            "cancel": DatasetVersionStatus.CANCELLED,
        },
        DatasetVersionStatus.BUILDING: {
            "system-build-complete": DatasetVersionStatus.VALIDATING,
            "system-build-fail": DatasetVersionStatus.VALIDATION_FAILED,
            "cancel": DatasetVersionStatus.CANCELLED,
        },
        DatasetVersionStatus.VALIDATING: {
            "system-validate-pass": DatasetVersionStatus.READY,
            "validate": DatasetVersionStatus.READY,
            "ready": DatasetVersionStatus.READY,
            "system-validate-fail": DatasetVersionStatus.VALIDATION_FAILED,
            "validate-fail": DatasetVersionStatus.VALIDATION_FAILED,
            "cancel": DatasetVersionStatus.CANCELLED,
        },
        DatasetVersionStatus.VALIDATION_FAILED: {
            "return-draft": DatasetVersionStatus.DRAFT,
            "validate": DatasetVersionStatus.READY,
            "cancel": DatasetVersionStatus.CANCELLED,
        },
        DatasetVersionStatus.READY: {
            "freeze": DatasetVersionStatus.FROZEN,
            "return-draft": DatasetVersionStatus.DRAFT,
            "cancel": DatasetVersionStatus.CANCELLED,
        },
        DatasetVersionStatus.FROZEN: {
            "system-supersede": DatasetVersionStatus.SUPERSEDED,
            "archive": DatasetVersionStatus.ARCHIVED,
        },
        DatasetVersionStatus.SUPERSEDED: {
            "archive": DatasetVersionStatus.ARCHIVED,
        },
        DatasetVersionStatus.CANCELLED: {},
        DatasetVersionStatus.ARCHIVED: {},
    }
)

_VALIDITY_RULES: Mapping[ValidityStatus, Mapping[str, ValidityStatus]] = MappingProxyType(
    {
        ValidityStatus.VALID: {
            "system-mark-stale": ValidityStatus.STALE,
            "system-mark-unknown": ValidityStatus.UNKNOWN,
        },
        ValidityStatus.STALE: {
            "invalidate": ValidityStatus.INVALIDATED,
            "revalidate": ValidityStatus.VALID,
        },
        ValidityStatus.UNKNOWN: {
            "system-mark-stale": ValidityStatus.STALE,
            "invalidate": ValidityStatus.INVALIDATED,
        },
        ValidityStatus.INVALIDATED: {},
    }
)

_REASON_REQUIRED: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "dataset": frozenset({"archive", "restore"}),
        "version": frozenset(
            {
                "system-build-fail",
                "system-validate-fail",
                "validate-fail",
                "return-draft",
                "cancel",
                "archive",
            }
        ),
        "validity": frozenset(
            {"system-mark-stale", "system-mark-unknown", "invalidate", "revalidate"}
        ),
    }
)

# 冻结后禁止原地修改的字段；只允许状态/失效标记/取代关系/归档元数据变化。
FROZEN_IMMUTABLE_COLUMNS: tuple[str, ...] = (
    "dataset_id",
    "dataset_lineage_id",
    "dataset_version",
    "selection_rule_version",
    "selection_filters",
    "selection_snapshot_at",
    "dedup_policy_id",
    "dedup_policy_version",
    "dedup_policy_sha256",
    "split_policy_id",
    "split_policy_version",
    "split_policy_sha256",
    "leakage_policy_id",
    "leakage_policy_version",
    "leakage_policy_sha256",
    "quality_policy_id",
    "quality_policy_version",
    "quality_policy_sha256",
    "candidate_count",
    "selected_count",
    "excluded_count",
    "duplicated_count",
    "item_count",
    "train_count",
    "validation_count",
    "test_count",
    "total_duration_ns",
    "total_bytes",
    "episode_version_digest",
    "annotation_schema_versions",
    "annotation_submission_refs_digest",
    "manifest_key",
    "manifest_sha256",
    "quality_report_id",
    "quality_report_sha256",
    "leakage_report_id",
    "leakage_report_sha256",
    "leakage_status",
    "source_snapshot_at",
    "freeze_approval_id",
    "frozen_by",
    "frozen_at",
    "supersedes_version_id",
)

TERMINAL_VERSION_STATES: frozenset[DatasetVersionStatus] = frozenset(
    {
        DatasetVersionStatus.SUPERSEDED,
        DatasetVersionStatus.CANCELLED,
        DatasetVersionStatus.ARCHIVED,
    }
)


def _target(
    rules: Mapping[object, Mapping[str, object]],
    entity: str,
    current: object,
    action: str,
) -> object:
    target = rules.get(current, {}).get(action)
    if target is None:
        current_text = getattr(current, "value", current)
        raise InvalidStateTransitionError(entity, current_text, action)
    return target


def _pairs(rules: Mapping[object, Mapping[str, object]]) -> frozenset[_TRANSITION]:
    return frozenset(
        (getattr(current, "value", str(current)), getattr(target, "value", str(target)))
        for current, transitions in rules.items()
        for target in transitions.values()
    )


def _allowed(rules: Mapping[object, Mapping[str, object]], current: object) -> frozenset[str]:
    return frozenset(rules.get(current, {}))


def dataset_target(current: DatasetStatus, action: str) -> DatasetStatus:
    return _target(_DATASET_RULES, "数据集", current, action)  # type: ignore[return-value]


def version_target(current: DatasetVersionStatus, action: str) -> DatasetVersionStatus:
    return _target(_VERSION_RULES, "数据集版本", current, action)  # type: ignore[return-value]


def validity_target(current: ValidityStatus, action: str) -> ValidityStatus:
    return _target(_VALIDITY_RULES, "数据集版本有效性", current, action)  # type: ignore[return-value]


def allowed_dataset_actions(current: DatasetStatus) -> frozenset[str]:
    return _allowed(_DATASET_RULES, current)


def allowed_version_actions(current: DatasetVersionStatus) -> frozenset[str]:
    return _allowed(_VERSION_RULES, current)


def allowed_validity_actions(current: ValidityStatus) -> frozenset[str]:
    return _allowed(_VALIDITY_RULES, current)


def validate_action_name(action: object, *, family: str) -> str:
    if not isinstance(action, str) or not action.strip():
        raise DomainValidationError("action 必须是非空字符串")
    value = action.strip()
    rules = {
        "dataset": _DATASET_RULES,
        "version": _VERSION_RULES,
        "validity": _VALIDITY_RULES,
    }.get(family)
    if rules is None:
        raise DomainValidationError("未知状态机")
    known = {name for transitions in rules.values() for name in transitions}
    if value not in known:
        raise DomainValidationError("未知状态动作")
    return value


def action_requires_reason(family: str, action: str) -> bool:
    return action in _REASON_REQUIRED.get(family, frozenset())


def dataset_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_DATASET_RULES)


def version_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_VERSION_RULES)


def validity_transition_pairs() -> frozenset[_TRANSITION]:
    return _pairs(_VALIDITY_RULES)


def dataset_terminal_states() -> frozenset[str]:
    return frozenset(
        getattr(current, "value", str(current))
        for current, transitions in _DATASET_RULES.items()
        if not transitions
    )


def version_terminal_states() -> frozenset[str]:
    return frozenset(item.value for item in TERMINAL_VERSION_STATES)
