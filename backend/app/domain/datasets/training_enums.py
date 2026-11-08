"""阶段 4 训练需求、交付、外部引用与跨项目授权的领域枚举。

取值严格对齐 ``docs/architecture/contract-draft/dataset/``；未知值一律拒绝。
"""

from __future__ import annotations

from enum import StrEnum


class TrainingRequestStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    IN_DELIVERY = "IN_DELIVERY"
    DELIVERED = "DELIVERED"
    ACCEPTED = "ACCEPTED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class RevisionStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class TrainingDeliveryStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    DELIVERED = "DELIVERED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    REWORK = "REWORK"
    CANCELLED = "CANCELLED"


class ExternalTrainingStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


class DatasetGrantStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class GrantScope(StrEnum):
    VIEW = "VIEW"
    DOWNLOAD = "DOWNLOAD"
    EXPORT = "EXPORT"


class TrainingType(StrEnum):
    SUPERVISED = "SUPERVISED"
    IMITATION = "IMITATION"
    RL = "RL"
    FINE_TUNE = "FINE_TUNE"
    OTHER = "OTHER"


class TrainingPriority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class AdapterCapability(StrEnum):
    SUBMIT = "submit"
    STATUS = "status"
    CANCEL = "cancel"
    CALLBACK = "callback"
    MODEL_REFERENCE = "model_ref"
    EVALUATION_REFERENCE = "evaluation_ref"


TERMINAL_REQUEST_STATES: frozenset[TrainingRequestStatus] = frozenset(
    {
        TrainingRequestStatus.CLOSED,
        TrainingRequestStatus.CANCELLED,
    }
)
# APPROVED 之后内容不可原地修改，但仍可被新 revision 取代，因此不是终态。
CONTENT_LOCKED_REVISION_STATES: frozenset[RevisionStatus] = frozenset(
    {
        RevisionStatus.APPROVED,
        RevisionStatus.REJECTED,
        RevisionStatus.SUPERSEDED,
    }
)
TERMINAL_DELIVERY_STATES: frozenset[TrainingDeliveryStatus] = frozenset(
    {
        TrainingDeliveryStatus.ACCEPTED,
        TrainingDeliveryStatus.CANCELLED,
    }
)
TERMINAL_EXTERNAL_STATES: frozenset[ExternalTrainingStatus] = frozenset(
    {
        ExternalTrainingStatus.SUCCEEDED,
        ExternalTrainingStatus.FAILED,
        ExternalTrainingStatus.CANCELLED,
    }
)
TERMINAL_GRANT_STATES: frozenset[DatasetGrantStatus] = frozenset(
    {
        DatasetGrantStatus.REJECTED,
        DatasetGrantStatus.EXPIRED,
        DatasetGrantStatus.REVOKED,
    }
)
