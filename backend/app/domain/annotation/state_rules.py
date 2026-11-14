"""标注域状态机规则表（纯数据）。

从 ``state_machines.py`` 拆出：该文件原有 919 行，规则表本身约 490 行，
是全仓最大的纯数据块。拆出后规则仍在**一处**定义，语义零变化；
校验函数留在 ``state_machines.py``，两者通过本模块衔接。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.annotation.enums import (
    AnnotationArbitrationAction,
    AnnotationArbitrationStatus,
    AnnotationAssignmentAction,
    AnnotationAssignmentStatus,
    AnnotationBatchAction,
    AnnotationBatchStatus,
    AnnotationRecordAction,
    AnnotationRecordStatus,
    AnnotationReviewAction,
    AnnotationReviewStatus,
    AnnotationSampleSetAction,
    AnnotationSampleSetStatus,
    AnnotationSchemaAction,
    AnnotationSchemaStatus,
    AnnotationSubmissionAction,
    AnnotationSubmissionStatus,
    AnnotationTaskAction,
    AnnotationTaskStatus,
    GoldSetAction,
    GoldSetStatus,
    IAAReportAction,
    IAAReportStatus,
    QualitySampleAction,
    QualitySampleStatus,
)

__all__ = [
    "_ARBITRATION_RULES",
    "_ASSIGNMENT_RULES",
    "_BATCH_RULES",
    "_GOLD_SET_RULES",
    "_IAA_REPORT_RULES",
    "_QUALITY_SAMPLE_RULES",
    "_REASON_REQUIRED",
    "_RECORD_RULES",
    "_REVIEW_RULES",
    "_SAMPLE_SET_RULES",
    "_SCHEMA_RULES",
    "_SUBMISSION_RULES",
    "_TASK_RULES",
]


_SCHEMA_RULES: Mapping[AnnotationSchemaStatus, Mapping[str, AnnotationSchemaStatus]] = (
    MappingProxyType(
        {
            AnnotationSchemaStatus.DRAFT: {
                AnnotationSchemaAction.SUBMIT_REVIEW.value: AnnotationSchemaStatus.PENDING_REVIEW,
            },
            AnnotationSchemaStatus.PENDING_REVIEW: {
                AnnotationSchemaAction.RETURN_DRAFT.value: AnnotationSchemaStatus.DRAFT,
                AnnotationSchemaAction.PUBLISH.value: AnnotationSchemaStatus.PUBLISHED,
            },
            AnnotationSchemaStatus.PUBLISHED: {
                AnnotationSchemaAction.DEPRECATE.value: AnnotationSchemaStatus.DEPRECATED,
                AnnotationSchemaAction.REVOKE.value: AnnotationSchemaStatus.REVOKED,
            },
            AnnotationSchemaStatus.DEPRECATED: {
                AnnotationSchemaAction.REVOKE.value: AnnotationSchemaStatus.REVOKED,
            },
            AnnotationSchemaStatus.REVOKED: {},
        }
    )
)

_SAMPLE_SET_RULES: Mapping[AnnotationSampleSetStatus, Mapping[str, AnnotationSampleSetStatus]] = (
    MappingProxyType(
        {
            AnnotationSampleSetStatus.DRAFT: {
                AnnotationSampleSetAction.PREVIEW.value: AnnotationSampleSetStatus.PREVIEW,
                AnnotationSampleSetAction.CANCEL.value: AnnotationSampleSetStatus.CANCELLED,
            },
            AnnotationSampleSetStatus.PREVIEW: {
                AnnotationSampleSetAction.SNAPSHOT.value: AnnotationSampleSetStatus.SNAPSHOTTED,
                AnnotationSampleSetAction.CANCEL.value: AnnotationSampleSetStatus.CANCELLED,
            },
            AnnotationSampleSetStatus.SNAPSHOTTED: {
                AnnotationSampleSetAction.CONSUME.value: AnnotationSampleSetStatus.CONSUMED,
                AnnotationSampleSetAction.CANCEL.value: AnnotationSampleSetStatus.CANCELLED,
            },
            AnnotationSampleSetStatus.CONSUMED: {},
            AnnotationSampleSetStatus.CANCELLED: {},
        }
    )
)

_TASK_RULES: Mapping[AnnotationTaskStatus, Mapping[str, AnnotationTaskStatus]] = MappingProxyType(
    {
        AnnotationTaskStatus.DRAFT: {
            AnnotationTaskAction.SUBMIT_REVIEW.value: AnnotationTaskStatus.PENDING_REVIEW,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.PENDING_REVIEW: {
            AnnotationTaskAction.RETURN_DRAFT.value: AnnotationTaskStatus.DRAFT,
            AnnotationTaskAction.APPROVE.value: AnnotationTaskStatus.APPROVED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.APPROVED: {
            AnnotationTaskAction.READY.value: AnnotationTaskStatus.READY,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.READY: {
            AnnotationTaskAction.ASSIGN.value: AnnotationTaskStatus.ASSIGNED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.ASSIGNED: {
            AnnotationTaskAction.START.value: AnnotationTaskStatus.IN_PROGRESS,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.IN_PROGRESS: {
            AnnotationTaskAction.PAUSE.value: AnnotationTaskStatus.PAUSED,
            AnnotationTaskAction.BLOCK.value: AnnotationTaskStatus.BLOCKED,
            AnnotationTaskAction.SUBMIT.value: AnnotationTaskStatus.SUBMITTED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.PAUSED: {
            AnnotationTaskAction.RESUME.value: AnnotationTaskStatus.IN_PROGRESS,
            AnnotationTaskAction.SUBMIT.value: AnnotationTaskStatus.SUBMITTED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.BLOCKED: {
            AnnotationTaskAction.UNBLOCK.value: AnnotationTaskStatus.IN_PROGRESS,
            AnnotationTaskAction.SUBMIT.value: AnnotationTaskStatus.SUBMITTED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.SUBMITTED: {
            AnnotationTaskAction.SYSTEM_REVIEW.value: AnnotationTaskStatus.REVIEWING,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.REVIEWING: {
            AnnotationTaskAction.ACCEPT.value: AnnotationTaskStatus.ACCEPTED,
            AnnotationTaskAction.REJECT.value: AnnotationTaskStatus.REJECTED,
            AnnotationTaskAction.ESCALATE.value: AnnotationTaskStatus.ARBITRATION,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.ARBITRATION: {
            AnnotationTaskAction.RESOLVE_ACCEPTED.value: AnnotationTaskStatus.ACCEPTED,
            AnnotationTaskAction.RESOLVE_REJECTED.value: AnnotationTaskStatus.REJECTED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.ACCEPTED: {
            AnnotationTaskAction.COMPLETE.value: AnnotationTaskStatus.COMPLETED,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.REJECTED: {
            AnnotationTaskAction.REWORK.value: AnnotationTaskStatus.REWORK,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.REWORK: {
            AnnotationTaskAction.RESUME_WORK.value: AnnotationTaskStatus.IN_PROGRESS,
            AnnotationTaskAction.CANCEL.value: AnnotationTaskStatus.CANCELLED,
        },
        AnnotationTaskStatus.COMPLETED: {
            AnnotationTaskAction.ARCHIVE.value: AnnotationTaskStatus.ARCHIVED,
        },
        AnnotationTaskStatus.CANCELLED: {},
        AnnotationTaskStatus.ARCHIVED: {},
    }
)

_BATCH_RULES: Mapping[AnnotationBatchStatus, Mapping[str, AnnotationBatchStatus]] = (
    MappingProxyType(
        {
            AnnotationBatchStatus.PLANNED: {
                AnnotationBatchAction.READY.value: AnnotationBatchStatus.READY,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.READY: {
                AnnotationBatchAction.ASSIGN.value: AnnotationBatchStatus.ASSIGNED,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.ASSIGNED: {
                AnnotationBatchAction.START.value: AnnotationBatchStatus.IN_PROGRESS,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.IN_PROGRESS: {
                AnnotationBatchAction.PAUSE.value: AnnotationBatchStatus.PAUSED,
                AnnotationBatchAction.BLOCK.value: AnnotationBatchStatus.BLOCKED,
                AnnotationBatchAction.SUBMIT.value: AnnotationBatchStatus.SUBMITTED,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.PAUSED: {
                AnnotationBatchAction.RESUME.value: AnnotationBatchStatus.IN_PROGRESS,
                AnnotationBatchAction.SUBMIT.value: AnnotationBatchStatus.SUBMITTED,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.BLOCKED: {
                AnnotationBatchAction.UNBLOCK.value: AnnotationBatchStatus.IN_PROGRESS,
                AnnotationBatchAction.SUBMIT.value: AnnotationBatchStatus.SUBMITTED,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.SUBMITTED: {
                AnnotationBatchAction.SYSTEM_REVIEW.value: AnnotationBatchStatus.REVIEWING,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.REVIEWING: {
                AnnotationBatchAction.ACCEPT.value: AnnotationBatchStatus.ACCEPTED,
                AnnotationBatchAction.REJECT.value: AnnotationBatchStatus.REJECTED,
                AnnotationBatchAction.ESCALATE.value: AnnotationBatchStatus.ARBITRATION,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.ARBITRATION: {
                AnnotationBatchAction.RESOLVE_ACCEPTED.value: AnnotationBatchStatus.ACCEPTED,
                AnnotationBatchAction.RESOLVE_REJECTED.value: AnnotationBatchStatus.REJECTED,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.REJECTED: {
                AnnotationBatchAction.REWORK.value: AnnotationBatchStatus.REWORK,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.REWORK: {
                AnnotationBatchAction.RESUME_WORK.value: AnnotationBatchStatus.IN_PROGRESS,
                AnnotationBatchAction.CANCEL.value: AnnotationBatchStatus.CANCELLED,
            },
            AnnotationBatchStatus.ACCEPTED: {},
            AnnotationBatchStatus.CANCELLED: {},
        }
    )
)

_ASSIGNMENT_RULES: Mapping[AnnotationAssignmentStatus, Mapping[str, AnnotationAssignmentStatus]] = (
    MappingProxyType(
        {
            AnnotationAssignmentStatus.ASSIGNED: {
                AnnotationAssignmentAction.CLAIM.value: AnnotationAssignmentStatus.CLAIMED,
                AnnotationAssignmentAction.REVOKE.value: AnnotationAssignmentStatus.REVOKED,
                AnnotationAssignmentAction.SYSTEM_EXPIRE.value: AnnotationAssignmentStatus.EXPIRED,
            },
            AnnotationAssignmentStatus.CLAIMED: {
                AnnotationAssignmentAction.START.value: AnnotationAssignmentStatus.IN_PROGRESS,
                AnnotationAssignmentAction.RELEASE.value: AnnotationAssignmentStatus.ASSIGNED,
                AnnotationAssignmentAction.REVOKE.value: AnnotationAssignmentStatus.REVOKED,
                AnnotationAssignmentAction.SYSTEM_EXPIRE.value: AnnotationAssignmentStatus.EXPIRED,
            },
            AnnotationAssignmentStatus.IN_PROGRESS: {
                AnnotationAssignmentAction.SUBMIT.value: AnnotationAssignmentStatus.SUBMITTED,
                AnnotationAssignmentAction.REVOKE.value: AnnotationAssignmentStatus.REVOKED,
            },
            AnnotationAssignmentStatus.SUBMITTED: {
                AnnotationAssignmentAction.COMPLETE.value: AnnotationAssignmentStatus.COMPLETED,
                AnnotationAssignmentAction.REWORK.value: AnnotationAssignmentStatus.IN_PROGRESS,
            },
            AnnotationAssignmentStatus.COMPLETED: {},
            AnnotationAssignmentStatus.REVOKED: {},
            AnnotationAssignmentStatus.EXPIRED: {},
        }
    )
)

_SUBMISSION_RULES: Mapping[AnnotationSubmissionStatus, Mapping[str, AnnotationSubmissionStatus]] = (
    MappingProxyType(
        {
            AnnotationSubmissionStatus.DRAFT: {
                AnnotationSubmissionAction.SUBMIT.value: AnnotationSubmissionStatus.SUBMITTED,
                AnnotationSubmissionAction.WITHDRAW.value: AnnotationSubmissionStatus.WITHDRAWN,
            },
            AnnotationSubmissionStatus.SUBMITTED: {
                AnnotationSubmissionAction.SYSTEM_REVIEW_START.value: AnnotationSubmissionStatus.IN_REVIEW,
            },
            AnnotationSubmissionStatus.IN_REVIEW: {
                AnnotationSubmissionAction.APPROVE.value: AnnotationSubmissionStatus.APPROVED,
                AnnotationSubmissionAction.REJECT.value: AnnotationSubmissionStatus.REJECTED,
                AnnotationSubmissionAction.ESCALATE.value: AnnotationSubmissionStatus.ESCALATED,
            },
            AnnotationSubmissionStatus.ESCALATED: {
                AnnotationSubmissionAction.ARBITRATE_START.value: AnnotationSubmissionStatus.ARBITRATING,
            },
            AnnotationSubmissionStatus.ARBITRATING: {
                AnnotationSubmissionAction.RESOLVE_APPROVED.value: AnnotationSubmissionStatus.RESOLVED_APPROVED,
                AnnotationSubmissionAction.RESOLVE_REJECTED.value: AnnotationSubmissionStatus.RESOLVED_REJECTED,
                AnnotationSubmissionAction.RESOLVE_REWORK.value: AnnotationSubmissionStatus.RESOLVED_REWORK,
            },
            AnnotationSubmissionStatus.REJECTED: {
                AnnotationSubmissionAction.REQUIRE_REWORK.value: AnnotationSubmissionStatus.REWORK_REQUIRED,
            },
            AnnotationSubmissionStatus.RESOLVED_REJECTED: {
                AnnotationSubmissionAction.REQUIRE_REWORK.value: AnnotationSubmissionStatus.REWORK_REQUIRED,
            },
            AnnotationSubmissionStatus.RESOLVED_REWORK: {
                AnnotationSubmissionAction.REQUIRE_REWORK.value: AnnotationSubmissionStatus.REWORK_REQUIRED,
            },
            AnnotationSubmissionStatus.REWORK_REQUIRED: {
                AnnotationSubmissionAction.SYSTEM_SUPERSEDE.value: AnnotationSubmissionStatus.SUPERSEDED,
            },
            AnnotationSubmissionStatus.APPROVED: {
                AnnotationSubmissionAction.SYSTEM_SUPERSEDE.value: AnnotationSubmissionStatus.SUPERSEDED,
            },
            AnnotationSubmissionStatus.WITHDRAWN: {
                AnnotationSubmissionAction.SYSTEM_SUPERSEDE.value: AnnotationSubmissionStatus.SUPERSEDED,
            },
            AnnotationSubmissionStatus.SUPERSEDED: {},
        }
    )
)

_RECORD_RULES: Mapping[AnnotationRecordStatus, Mapping[str, AnnotationRecordStatus]] = (
    MappingProxyType(
        {
            AnnotationRecordStatus.DRAFT: {
                AnnotationRecordAction.SUBMIT_WITH_SUBMISSION.value: AnnotationRecordStatus.SUBMITTED,
                AnnotationRecordAction.INVALIDATE.value: AnnotationRecordStatus.INVALIDATED,
            },
            AnnotationRecordStatus.SUBMITTED: {
                AnnotationRecordAction.APPROVE_WITH_SUBMISSION.value: AnnotationRecordStatus.APPROVED,
                AnnotationRecordAction.REJECT_WITH_SUBMISSION.value: AnnotationRecordStatus.REJECTED,
                AnnotationRecordAction.INVALIDATE.value: AnnotationRecordStatus.INVALIDATED,
            },
            AnnotationRecordStatus.APPROVED: {
                AnnotationRecordAction.SYSTEM_SUPERSEDE.value: AnnotationRecordStatus.SUPERSEDED,
                AnnotationRecordAction.INVALIDATE.value: AnnotationRecordStatus.INVALIDATED,
            },
            AnnotationRecordStatus.REJECTED: {
                AnnotationRecordAction.SYSTEM_SUPERSEDE.value: AnnotationRecordStatus.SUPERSEDED,
            },
            AnnotationRecordStatus.SUPERSEDED: {},
            AnnotationRecordStatus.INVALIDATED: {},
        }
    )
)

_REVIEW_RULES: Mapping[AnnotationReviewStatus, Mapping[str, AnnotationReviewStatus]] = (
    MappingProxyType(
        {
            AnnotationReviewStatus.PENDING: {
                AnnotationReviewAction.CLAIM.value: AnnotationReviewStatus.IN_REVIEW,
                AnnotationReviewAction.CANCEL.value: AnnotationReviewStatus.CANCELLED,
            },
            AnnotationReviewStatus.IN_REVIEW: {
                AnnotationReviewAction.APPROVE.value: AnnotationReviewStatus.APPROVED,
                AnnotationReviewAction.REJECT.value: AnnotationReviewStatus.REJECTED,
                AnnotationReviewAction.ESCALATE.value: AnnotationReviewStatus.ESCALATED,
                AnnotationReviewAction.CANCEL.value: AnnotationReviewStatus.CANCELLED,
            },
            AnnotationReviewStatus.APPROVED: {},
            AnnotationReviewStatus.REJECTED: {},
            AnnotationReviewStatus.ESCALATED: {},
            AnnotationReviewStatus.CANCELLED: {},
        }
    )
)

_ARBITRATION_RULES: Mapping[
    AnnotationArbitrationStatus, Mapping[str, AnnotationArbitrationStatus]
] = MappingProxyType(
    {
        AnnotationArbitrationStatus.PENDING: {
            AnnotationArbitrationAction.CLAIM.value: AnnotationArbitrationStatus.IN_ARBITRATION,
            AnnotationArbitrationAction.CANCEL.value: AnnotationArbitrationStatus.CANCELLED,
        },
        AnnotationArbitrationStatus.IN_ARBITRATION: {
            AnnotationArbitrationAction.RECUSE.value: AnnotationArbitrationStatus.PENDING,
            AnnotationArbitrationAction.RESOLVE_APPROVED.value: AnnotationArbitrationStatus.RESOLVED_APPROVED,
            AnnotationArbitrationAction.RESOLVE_REJECTED.value: AnnotationArbitrationStatus.RESOLVED_REJECTED,
            AnnotationArbitrationAction.RESOLVE_REWORK.value: AnnotationArbitrationStatus.RESOLVED_REWORK,
            AnnotationArbitrationAction.CANCEL.value: AnnotationArbitrationStatus.CANCELLED,
        },
        AnnotationArbitrationStatus.RESOLVED_APPROVED: {},
        AnnotationArbitrationStatus.RESOLVED_REJECTED: {},
        AnnotationArbitrationStatus.RESOLVED_REWORK: {},
        AnnotationArbitrationStatus.CANCELLED: {},
    }
)

_GOLD_SET_RULES: Mapping[GoldSetStatus, Mapping[str, GoldSetStatus]] = MappingProxyType(
    {
        GoldSetStatus.DRAFT: {
            GoldSetAction.SUBMIT_REVIEW.value: GoldSetStatus.PENDING_REVIEW,
            GoldSetAction.REVOKE.value: GoldSetStatus.REVOKED,
        },
        GoldSetStatus.PENDING_REVIEW: {
            GoldSetAction.RETURN_DRAFT.value: GoldSetStatus.DRAFT,
            GoldSetAction.ACTIVATE.value: GoldSetStatus.ACTIVE,
            GoldSetAction.REVOKE.value: GoldSetStatus.REVOKED,
        },
        GoldSetStatus.ACTIVE: {
            GoldSetAction.RETIRE.value: GoldSetStatus.RETIRED,
            GoldSetAction.REVOKE.value: GoldSetStatus.REVOKED,
        },
        GoldSetStatus.RETIRED: {},
        GoldSetStatus.REVOKED: {},
    }
)

_QUALITY_SAMPLE_RULES: Mapping[QualitySampleStatus, Mapping[str, QualitySampleStatus]] = (
    MappingProxyType(
        {
            QualitySampleStatus.PLANNED: {
                QualitySampleAction.SAMPLE.value: QualitySampleStatus.SAMPLED,
                QualitySampleAction.CANCEL.value: QualitySampleStatus.CANCELLED,
            },
            QualitySampleStatus.SAMPLED: {
                QualitySampleAction.ASSIGN.value: QualitySampleStatus.ASSIGNED,
                QualitySampleAction.CANCEL.value: QualitySampleStatus.CANCELLED,
            },
            QualitySampleStatus.ASSIGNED: {
                QualitySampleAction.START.value: QualitySampleStatus.IN_REVIEW,
                QualitySampleAction.CANCEL.value: QualitySampleStatus.CANCELLED,
            },
            QualitySampleStatus.IN_REVIEW: {
                QualitySampleAction.PASS.value: QualitySampleStatus.PASSED,
                QualitySampleAction.FAIL.value: QualitySampleStatus.FAILED,
                QualitySampleAction.ESCALATE.value: QualitySampleStatus.ESCALATED,
            },
            QualitySampleStatus.PASSED: {
                QualitySampleAction.CLOSE.value: QualitySampleStatus.CLOSED,
            },
            QualitySampleStatus.FAILED: {
                QualitySampleAction.CLOSE.value: QualitySampleStatus.CLOSED,
            },
            QualitySampleStatus.ESCALATED: {
                QualitySampleAction.CLOSE.value: QualitySampleStatus.CLOSED,
            },
            QualitySampleStatus.CLOSED: {},
            QualitySampleStatus.CANCELLED: {},
        }
    )
)

_IAA_REPORT_RULES: Mapping[IAAReportStatus, Mapping[str, IAAReportStatus]] = MappingProxyType(
    {
        IAAReportStatus.QUEUED: {
            IAAReportAction.WORKER_START.value: IAAReportStatus.RUNNING,
            IAAReportAction.CANCEL.value: IAAReportStatus.CANCELLED,
        },
        IAAReportStatus.RUNNING: {
            IAAReportAction.WORKER_SUCCEED.value: IAAReportStatus.SUCCEEDED,
            IAAReportAction.WORKER_FAIL.value: IAAReportStatus.FAILED,
            IAAReportAction.WORKER_INSUFFICIENT.value: IAAReportStatus.INSUFFICIENT_DATA,
            IAAReportAction.CANCEL.value: IAAReportStatus.CANCELLED,
        },
        IAAReportStatus.FAILED: {
            IAAReportAction.RETRY.value: IAAReportStatus.QUEUED,
        },
        IAAReportStatus.SUCCEEDED: {},
        IAAReportStatus.INSUFFICIENT_DATA: {},
        IAAReportStatus.CANCELLED: {},
    }
)

_REASON_REQUIRED: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "schema": frozenset(
            {
                AnnotationSchemaAction.RETURN_DRAFT.value,
                AnnotationSchemaAction.DEPRECATE.value,
                AnnotationSchemaAction.REVOKE.value,
            }
        ),
        "sample_set": frozenset({AnnotationSampleSetAction.CANCEL.value}),
        "task": frozenset(
            {
                AnnotationTaskAction.RETURN_DRAFT.value,
                AnnotationTaskAction.PAUSE.value,
                AnnotationTaskAction.RESUME.value,
                AnnotationTaskAction.BLOCK.value,
                AnnotationTaskAction.UNBLOCK.value,
                AnnotationTaskAction.REJECT.value,
                AnnotationTaskAction.ESCALATE.value,
                AnnotationTaskAction.RESOLVE_REJECTED.value,
                AnnotationTaskAction.REWORK.value,
                AnnotationTaskAction.CANCEL.value,
            }
        ),
        "batch": frozenset(
            {
                AnnotationBatchAction.PAUSE.value,
                AnnotationBatchAction.RESUME.value,
                AnnotationBatchAction.BLOCK.value,
                AnnotationBatchAction.UNBLOCK.value,
                AnnotationBatchAction.REJECT.value,
                AnnotationBatchAction.ESCALATE.value,
                AnnotationBatchAction.RESOLVE_REJECTED.value,
                AnnotationBatchAction.REWORK.value,
                AnnotationBatchAction.CANCEL.value,
            }
        ),
        "assignment": frozenset(
            {
                AnnotationAssignmentAction.RELEASE.value,
                AnnotationAssignmentAction.REVOKE.value,
                AnnotationAssignmentAction.REWORK.value,
            }
        ),
        "submission": frozenset(
            {
                AnnotationSubmissionAction.WITHDRAW.value,
                AnnotationSubmissionAction.REJECT.value,
                AnnotationSubmissionAction.ESCALATE.value,
                AnnotationSubmissionAction.RESOLVE_REJECTED.value,
                AnnotationSubmissionAction.RESOLVE_REWORK.value,
                AnnotationSubmissionAction.REQUIRE_REWORK.value,
                AnnotationSubmissionAction.SYSTEM_SUPERSEDE.value,
            }
        ),
        "record": frozenset(
            {
                AnnotationRecordAction.REJECT_WITH_SUBMISSION.value,
                AnnotationRecordAction.SYSTEM_SUPERSEDE.value,
                AnnotationRecordAction.INVALIDATE.value,
            }
        ),
        "review": frozenset(
            {
                AnnotationReviewAction.REJECT.value,
                AnnotationReviewAction.ESCALATE.value,
                AnnotationReviewAction.CANCEL.value,
            }
        ),
        "arbitration": frozenset(
            {
                AnnotationArbitrationAction.RECUSE.value,
                AnnotationArbitrationAction.RESOLVE_REJECTED.value,
                AnnotationArbitrationAction.RESOLVE_REWORK.value,
                AnnotationArbitrationAction.CANCEL.value,
            }
        ),
        "gold_set": frozenset(
            {
                GoldSetAction.RETURN_DRAFT.value,
                GoldSetAction.RETIRE.value,
                GoldSetAction.REVOKE.value,
            }
        ),
        "quality_sample": frozenset(
            {
                QualitySampleAction.FAIL.value,
                QualitySampleAction.ESCALATE.value,
                QualitySampleAction.CLOSE.value,
                QualitySampleAction.CANCEL.value,
            }
        ),
        "iaa_report": frozenset(
            {
                IAAReportAction.WORKER_FAIL.value,
                IAAReportAction.WORKER_INSUFFICIENT.value,
                IAAReportAction.CANCEL.value,
            }
        ),
    }
)
