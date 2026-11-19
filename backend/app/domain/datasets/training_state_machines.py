"""训练需求、交付、外部引用与跨项目授权的状态机。

迁移表以 ``docs/architecture/contract-draft/dataset/state-machines.v1.json``
为基线。为支持“批准后变更创建新 revision 并重新审批”，TrainingRequest 增加
``APPROVED/SUBMITTED/PENDING_REVIEW -> DRAFT (new-revision)`` 显式迁移；
该扩展只用于创建新 revision，不允许原地修改已批准 revision 内容。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.datasets.errors import DomainValidationError, InvalidStateTransitionError
from app.domain.datasets.training_enums import (
    CONTENT_LOCKED_REVISION_STATES,
    DatasetGrantStatus,
    ExternalTrainingStatus,
    RevisionStatus,
    TrainingDeliveryStatus,
    TrainingRequestStatus,
)

_RULES = Mapping[
    object,
    Mapping[str, object],
]

_REVISION_RULES: Mapping[RevisionStatus, Mapping[str, RevisionStatus]] = MappingProxyType(
    {
        RevisionStatus.DRAFT: {
            "submit": RevisionStatus.SUBMITTED,
            "system-supersede": RevisionStatus.SUPERSEDED,
        },
        RevisionStatus.SUBMITTED: {
            "system-review-start": RevisionStatus.PENDING_REVIEW,
            "system-supersede": RevisionStatus.SUPERSEDED,
        },
        RevisionStatus.PENDING_REVIEW: {
            "approve": RevisionStatus.APPROVED,
            "reject": RevisionStatus.REJECTED,
            "system-supersede": RevisionStatus.SUPERSEDED,
        },
        RevisionStatus.APPROVED: {
            "system-supersede": RevisionStatus.SUPERSEDED,
        },
        RevisionStatus.REJECTED: {
            "system-supersede": RevisionStatus.SUPERSEDED,
        },
        RevisionStatus.SUPERSEDED: {},
    }
)

_REQUEST_RULES: Mapping[TrainingRequestStatus, Mapping[str, TrainingRequestStatus]] = (
    MappingProxyType(
        {
            TrainingRequestStatus.DRAFT: {
                "submit-revision": TrainingRequestStatus.SUBMITTED,
                "block": TrainingRequestStatus.BLOCKED,
                "cancel": TrainingRequestStatus.CANCELLED,
            },
            TrainingRequestStatus.SUBMITTED: {
                "system-review-start": TrainingRequestStatus.PENDING_REVIEW,
                "new-revision": TrainingRequestStatus.DRAFT,
                "block": TrainingRequestStatus.BLOCKED,
                "cancel": TrainingRequestStatus.CANCELLED,
            },
            TrainingRequestStatus.PENDING_REVIEW: {
                "approve": TrainingRequestStatus.APPROVED,
                "reject": TrainingRequestStatus.REJECTED,
                "new-revision": TrainingRequestStatus.DRAFT,
                "block": TrainingRequestStatus.BLOCKED,
                "cancel": TrainingRequestStatus.CANCELLED,
            },
            TrainingRequestStatus.REJECTED: {
                "new-revision": TrainingRequestStatus.DRAFT,
                "cancel": TrainingRequestStatus.CANCELLED,
            },
            TrainingRequestStatus.APPROVED: {
                "new-revision": TrainingRequestStatus.DRAFT,
                "start-delivery": TrainingRequestStatus.IN_DELIVERY,
                "block": TrainingRequestStatus.BLOCKED,
                "cancel": TrainingRequestStatus.CANCELLED,
            },
            TrainingRequestStatus.IN_DELIVERY: {
                "mark-delivered": TrainingRequestStatus.DELIVERED,
                "block": TrainingRequestStatus.BLOCKED,
                "cancel": TrainingRequestStatus.CANCELLED,
            },
            TrainingRequestStatus.DELIVERED: {
                "accept-delivery": TrainingRequestStatus.ACCEPTED,
                "require-revision": TrainingRequestStatus.REVISION_REQUIRED,
                "block": TrainingRequestStatus.BLOCKED,
                "cancel": TrainingRequestStatus.CANCELLED,
            },
            TrainingRequestStatus.REVISION_REQUIRED: {
                "start-delivery": TrainingRequestStatus.IN_DELIVERY,
                "block": TrainingRequestStatus.BLOCKED,
                "cancel": TrainingRequestStatus.CANCELLED,
            },
            TrainingRequestStatus.ACCEPTED: {
                "close": TrainingRequestStatus.CLOSED,
            },
            TrainingRequestStatus.BLOCKED: {
                "unblock": TrainingRequestStatus.DRAFT,
                "cancel": TrainingRequestStatus.CANCELLED,
            },
            TrainingRequestStatus.CLOSED: {},
            TrainingRequestStatus.CANCELLED: {},
        }
    )
)

_DELIVERY_RULES: Mapping[TrainingDeliveryStatus, Mapping[str, TrainingDeliveryStatus]] = (
    MappingProxyType(
        {
            TrainingDeliveryStatus.DRAFT: {
                "ready": TrainingDeliveryStatus.READY,
                "cancel": TrainingDeliveryStatus.CANCELLED,
            },
            TrainingDeliveryStatus.READY: {
                "deliver": TrainingDeliveryStatus.DELIVERED,
                "cancel": TrainingDeliveryStatus.CANCELLED,
            },
            TrainingDeliveryStatus.DELIVERED: {
                "accept": TrainingDeliveryStatus.ACCEPTED,
                "reject": TrainingDeliveryStatus.REJECTED,
            },
            TrainingDeliveryStatus.REJECTED: {
                "rework": TrainingDeliveryStatus.REWORK,
            },
            TrainingDeliveryStatus.REWORK: {
                "resume-ready": TrainingDeliveryStatus.READY,
            },
            TrainingDeliveryStatus.ACCEPTED: {},
            TrainingDeliveryStatus.CANCELLED: {},
        }
    )
)

_EXTERNAL_RULES: Mapping[ExternalTrainingStatus, Mapping[str, ExternalTrainingStatus]] = (
    MappingProxyType(
        {
            ExternalTrainingStatus.SUBMITTED: {
                "sync": ExternalTrainingStatus.RUNNING,
                "system-succeed": ExternalTrainingStatus.SUCCEEDED,
                "system-fail": ExternalTrainingStatus.FAILED,
                "system-cancel": ExternalTrainingStatus.CANCELLED,
                "system-unknown": ExternalTrainingStatus.UNKNOWN,
                "system-unavailable": ExternalTrainingStatus.UNAVAILABLE,
                # 演练：由本地 DRY_RUN 执行器在校验/计划完成后收口，终态不可再推进。
                # 单独动作而不是复用 system-succeed：演练不是成功训练。
                "system-dry-run": ExternalTrainingStatus.DRY_RUN,
            },
            ExternalTrainingStatus.RUNNING: {
                "sync": ExternalTrainingStatus.RUNNING,
                "system-succeed": ExternalTrainingStatus.SUCCEEDED,
                "system-fail": ExternalTrainingStatus.FAILED,
                "system-cancel": ExternalTrainingStatus.CANCELLED,
                "system-unknown": ExternalTrainingStatus.UNKNOWN,
                "system-unavailable": ExternalTrainingStatus.UNAVAILABLE,
            },
            ExternalTrainingStatus.UNKNOWN: {
                "system-unknown": ExternalTrainingStatus.UNKNOWN,
                "sync": ExternalTrainingStatus.RUNNING,
                "system-succeed": ExternalTrainingStatus.SUCCEEDED,
                "system-fail": ExternalTrainingStatus.FAILED,
                "system-cancel": ExternalTrainingStatus.CANCELLED,
                "system-unavailable": ExternalTrainingStatus.UNAVAILABLE,
            },
            ExternalTrainingStatus.UNAVAILABLE: {
                "system-unavailable": ExternalTrainingStatus.UNAVAILABLE,
                "sync": ExternalTrainingStatus.RUNNING,
                "system-succeed": ExternalTrainingStatus.SUCCEEDED,
                "system-fail": ExternalTrainingStatus.FAILED,
                "system-cancel": ExternalTrainingStatus.CANCELLED,
                "system-unknown": ExternalTrainingStatus.UNKNOWN,
            },
            ExternalTrainingStatus.SUCCEEDED: {},
            ExternalTrainingStatus.FAILED: {},
            ExternalTrainingStatus.CANCELLED: {},
            ExternalTrainingStatus.DRY_RUN: {},
        }
    )
)

_GRANT_RULES: Mapping[DatasetGrantStatus, Mapping[str, DatasetGrantStatus]] = MappingProxyType(
    {
        DatasetGrantStatus.PENDING_APPROVAL: {
            "approve": DatasetGrantStatus.ACTIVE,
            "reject": DatasetGrantStatus.REJECTED,
            "system-expire": DatasetGrantStatus.EXPIRED,
        },
        DatasetGrantStatus.ACTIVE: {
            "revoke": DatasetGrantStatus.REVOKED,
            "system-expire": DatasetGrantStatus.EXPIRED,
        },
        DatasetGrantStatus.REJECTED: {},
        DatasetGrantStatus.EXPIRED: {},
        DatasetGrantStatus.REVOKED: {},
    }
)

_FAMILIES: Mapping[str, _RULES] = MappingProxyType(
    {
        "revision": _REVISION_RULES,
        "request": _REQUEST_RULES,
        "delivery": _DELIVERY_RULES,
        "external": _EXTERNAL_RULES,
        "grant": _GRANT_RULES,
    }
)

_REASON_REQUIRED: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "request": frozenset(
            {"new-revision", "reject", "block", "unblock", "cancel", "require-revision"}
        ),
        "revision": frozenset({"reject"}),
        "delivery": frozenset({"reject", "rework", "cancel"}),
        "external": frozenset(),
        "grant": frozenset({"reject", "revoke"}),
    }
)

# 创建后永远不可原地修改的列；触发器与领域层共用。
REQUEST_IMMUTABLE_COLUMNS: tuple[str, ...] = (
    "project_id",
    "code",
    "owner_id",
    "created_at",
    "created_by",
)
REVISION_IMMUTABLE_COLUMNS: tuple[str, ...] = (
    "project_id",
    "training_request_id",
    "revision_no",
    "snapshot",
    "change_reason",
    "supersedes_revision_id",
    "revision_sha256",
    "created_at",
    "created_by",
)
# 审批结论一旦写入，不得在同一 revision 上改写；只能整体被新 revision 取代。
REVISION_APPROVAL_COLUMNS: tuple[str, ...] = (
    "submitted_by",
    "submitted_at",
    "reviewed_by",
    "reviewed_at",
    "approved_by",
    "approved_at",
    "rejected_by",
    "rejected_at",
    "rejection_reason",
)
DELIVERY_IMMUTABLE_COLUMNS: tuple[str, ...] = (
    "project_id",
    "training_request_id",
    "training_request_revision_id",
    "dataset_version_id",
    "export_job_id",
    "delivery_note_no",
    "recipient_id",
    "recipient_team",
    "artifact_manifest_key",
    "artifact_manifest_sha256",
    "aggregate_sha256",
    "item_count",
    "split_counts",
    "format",
    "format_version",
    "quality_report_ref",
    # 需求核对报告是「交付的这批数据是否满足已批准需求」的留证，一旦落库不可改：
    # 迁移 0038 的文档早已声明它在清单里，实际漏登记——等于允许事后把
    # FAILED/UNVERIFIABLE 改成 PASSED，绕开 N6 的全部阻断逻辑。触发器与
    # 应用层共用本清单，登记在这里两处同时生效。
    "requirement_match",
    "created_at",
    "created_by",
)
DELIVERY_ACCEPTANCE_COLUMNS: tuple[str, ...] = (
    "delivered_at",
    "accepted_by",
    "accepted_at",
    "acceptance_opinion",
    "rejection_reason",
    "rework_ref",
)
EXTERNAL_IMMUTABLE_COLUMNS: tuple[str, ...] = (
    "project_id",
    "adapter_id",
    "adapter_version",
    "training_delivery_id",
    "training_request_revision_id",
    "dataset_version_id",
    "external_run_id",
    "external_run_url",
    "correlation_id",
    "idempotency_key",
    "submitted_at",
    "created_at",
    "created_by",
)
EXTERNAL_TERMINAL_CONTENT_COLUMNS: tuple[str, ...] = (
    "external_status",
    "status_stale",
    "last_synced_at",
    "model_ref",
    "evaluation_ref",
    "error_code",
    "error_summary",
    # 演练计划只在 SUBMITTED -> DRY_RUN 的一次性收口里写入，进入终态后不可改，
    # 防止事后把"将执行的动作清单"改成另一份。
    "dry_run_plan",
)
GRANT_IMMUTABLE_COLUMNS: tuple[str, ...] = (
    "project_id",
    "dataset_id",
    "dataset_version_id",
    "grantee_project_id",
    "grantee_team",
    "scope",
    "reason",
    "expires_at",
    "created_at",
    "created_by",
)
GRANT_TERMINAL_CONTENT_COLUMNS: tuple[str, ...] = (
    "approved_by",
    "approved_at",
    "rejected_by",
    "rejected_at",
    "rejection_reason",
    "revoked_at",
    "revoked_by",
    "revoke_reason",
)


def _target(
    rules: Mapping[object, Mapping[str, object]], entity: str, current: object, action: str
) -> object:
    target = rules.get(current, {}).get(action)
    if target is None:
        current_text = getattr(current, "value", current)
        raise InvalidStateTransitionError(entity, current_text, action)
    return target


def _pairs(rules: Mapping[object, Mapping[str, object]]) -> frozenset[tuple[str, str]]:
    return frozenset(
        (getattr(current, "value", str(current)), getattr(target, "value", str(target)))
        for current, transitions in rules.items()
        for target in transitions.values()
    )


def _allowed(rules: Mapping[object, Mapping[str, object]], current: object) -> frozenset[str]:
    return frozenset(rules.get(current, {}))


def _family(family: str) -> Mapping[object, Mapping[str, object]]:
    rules = _FAMILIES.get(family)
    if rules is None:
        raise DomainValidationError("未知状态机")
    return rules


def revision_target(current: RevisionStatus, action: str) -> RevisionStatus:
    return _target(_REVISION_RULES, "需求修订", current, action)  # type: ignore[return-value]


def request_target(current: TrainingRequestStatus, action: str) -> TrainingRequestStatus:
    return _target(_REQUEST_RULES, "训练需求", current, action)  # type: ignore[return-value]


def delivery_target(current: TrainingDeliveryStatus, action: str) -> TrainingDeliveryStatus:
    return _target(_DELIVERY_RULES, "训练交付", current, action)  # type: ignore[return-value]


def external_target(
    current: ExternalTrainingStatus,
    action: str,
) -> ExternalTrainingStatus:
    return _target(_EXTERNAL_RULES, "外部训练引用", current, action)  # type: ignore[return-value]


def grant_target(current: DatasetGrantStatus, action: str) -> DatasetGrantStatus:
    return _target(_GRANT_RULES, "数据集授权", current, action)  # type: ignore[return-value]


def allowed_revision_actions(current: RevisionStatus) -> frozenset[str]:
    return _allowed(_REVISION_RULES, current)


def allowed_request_actions(current: TrainingRequestStatus) -> frozenset[str]:
    return _allowed(_REQUEST_RULES, current)


def allowed_delivery_actions(current: TrainingDeliveryStatus) -> frozenset[str]:
    return _allowed(_DELIVERY_RULES, current)


def allowed_external_actions(current: ExternalTrainingStatus) -> frozenset[str]:
    return _allowed(_EXTERNAL_RULES, current)


def allowed_grant_actions(current: DatasetGrantStatus) -> frozenset[str]:
    return _allowed(_GRANT_RULES, current)


def validate_action_name(action: object, *, family: str) -> str:
    if not isinstance(action, str) or not action.strip():
        raise DomainValidationError("action 必须是非空字符串")
    value = action.strip()
    rules = _family(family)
    known = {name for transitions in rules.values() for name in transitions}
    if value not in known:
        raise DomainValidationError("未知状态动作")
    return value


def action_requires_reason(family: str, action: str) -> bool:
    return action in _REASON_REQUIRED.get(family, frozenset())


def revision_transition_pairs() -> frozenset[tuple[str, str]]:
    return _pairs(_REVISION_RULES)


def request_transition_pairs() -> frozenset[tuple[str, str]]:
    return _pairs(_REQUEST_RULES)


def delivery_transition_pairs() -> frozenset[tuple[str, str]]:
    return _pairs(_DELIVERY_RULES)


def external_transition_pairs() -> frozenset[tuple[str, str]]:
    return _pairs(_EXTERNAL_RULES)


def grant_transition_pairs() -> frozenset[tuple[str, str]]:
    return _pairs(_GRANT_RULES)


def revision_terminal_states() -> frozenset[str]:
    return frozenset(item.value for item in CONTENT_LOCKED_REVISION_STATES)


def delivery_terminal_states() -> frozenset[str]:
    return frozenset({"ACCEPTED", "CANCELLED"})


def external_terminal_states() -> frozenset[str]:
    return frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "DRY_RUN"})


def grant_terminal_states() -> frozenset[str]:
    return frozenset({"REJECTED", "EXPIRED", "REVOKED"})
