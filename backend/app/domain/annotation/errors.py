"""标注域异常。

领域层不依赖 HTTP；稳定 ``code`` 对齐阶段 3 契约草案的 error-codes。
未知状态、非法迁移、终态回退一律显式失败，不存在默认成功分支。
"""

from __future__ import annotations


class AnnotationDomainError(ValueError):
    """标注域可预期错误的基类。"""

    code = "validation_failed"


class DomainValidationError(AnnotationDomainError):
    """字段或跨字段校验失败。"""


class InvalidStateTransitionError(AnnotationDomainError):
    """非法状态迁移或终态回退。"""

    code = "state_transition_invalid"

    def __init__(self, entity: str, current: object, action: object) -> None:
        self.entity = entity
        self.current = current
        self.action = action
        super().__init__(f"{entity} 当前状态 {current} 不允许动作 {action}")


class ResourceNotFoundError(AnnotationDomainError):
    """资源不存在或不属于当前项目。"""

    code = "resource_not_found"

    def __init__(self, resource: str, resource_id: object) -> None:
        self.resource = resource
        self.resource_id = resource_id
        super().__init__(f"{resource}不存在或不可见")


class VersionConflictError(AnnotationDomainError):
    """乐观锁版本不符。"""

    code = "version_conflict"

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"{resource}版本冲突")


class ResourceImmutableError(AnnotationDomainError):
    """资源当前状态不允许修改。"""

    code = "resource_immutable"


class DuplicateResourceError(AnnotationDomainError):
    """项目内业务键重复。"""

    code = "duplicate_resource"


class ConflictError(AnnotationDomainError):
    """跨对象一致性冲突。"""

    code = "conflict"


class ForbiddenOperationError(AnnotationDomainError):
    """职责分离或业务权限拒绝。"""

    code = "forbidden"


class IdempotencyKeyReusedError(AnnotationDomainError):
    """同一幂等键对应不同请求体。"""

    code = "idempotency_key_reused"


class SchemaNotPublishedError(AnnotationDomainError):
    code = "annotation_schema_not_published"


class SchemaImmutableError(AnnotationDomainError):
    code = "annotation_schema_immutable"


class SchemaSnapshotMismatchError(AnnotationDomainError):
    code = "annotation_schema_snapshot_mismatch"


class SampleSetEmptyError(AnnotationDomainError):
    code = "annotation_sample_set_empty"


class SampleSetNotSnapshottedError(AnnotationDomainError):
    code = "annotation_sample_set_not_snapshotted"


class SampleSetCancelledError(AnnotationDomainError):
    code = "annotation_sample_set_cancelled"


class EpisodeIneligibleError(AnnotationDomainError):
    code = "annotation_sample_episode_ineligible"


class DuplicateSampleError(AnnotationDomainError):
    code = "annotation_sample_duplicate"


class TaskNotApprovedError(AnnotationDomainError):
    code = "annotation_task_not_approved"


class TaskNotReadyError(AnnotationDomainError):
    code = "annotation_task_not_ready"


class TaskOpenWorkError(AnnotationDomainError):
    code = "annotation_task_has_open_work"


class BatchNotCompatibleError(AnnotationDomainError):
    code = "annotation_batch_not_compatible"


class BatchOverCapacityError(AnnotationDomainError):
    code = "annotation_batch_over_capacity"


class AssignmentCapacityExceededError(AnnotationDomainError):
    code = "annotation_assignment_capacity_exceeded"


class AssignmentNotOwnedError(AnnotationDomainError):
    code = "annotation_assignment_not_owned"


class AssignmentExpiredError(AnnotationDomainError):
    code = "annotation_assignment_expired"


class AssignmentRevokedError(AnnotationDomainError):
    code = "annotation_assignment_revoked"


class ItemAlreadyAssignedError(AnnotationDomainError):
    code = "annotation_item_already_assigned"


class SubmissionNotEditableError(AnnotationDomainError):
    code = "annotation_submission_not_editable"


class SubmissionIncompleteError(AnnotationDomainError):
    code = "annotation_submission_incomplete"


class SubmissionSchemaMismatchError(AnnotationDomainError):
    code = "annotation_submission_schema_mismatch"


class SubmissionNotSubmittedError(AnnotationDomainError):
    code = "annotation_submission_not_submitted"


class SubmissionAlreadyDecidedError(AnnotationDomainError):
    code = "annotation_submission_already_decided"


class RecordDigestMismatchError(AnnotationDomainError):
    code = "annotation_record_digest_mismatch"


class UnknownLabelError(AnnotationDomainError):
    code = "annotation_schema_unknown_label"


class LabelValueInvalidError(AnnotationDomainError):
    code = "annotation_schema_value_invalid"


class ReviewSelfForbiddenError(AnnotationDomainError):
    code = "annotation_review_self_forbidden"


class ReviewNotPendingError(AnnotationDomainError):
    code = "annotation_review_not_pending"


class ReviewRequiredError(AnnotationDomainError):
    code = "annotation_review_required"


class ArbitrationRequiredError(AnnotationDomainError):
    code = "annotation_arbitration_required"


class ArbitrationNotPendingError(AnnotationDomainError):
    code = "annotation_arbitration_not_pending"


class ArbitrationQuorumNotMetError(AnnotationDomainError):
    code = "annotation_arbitration_quorum_not_met"


class ArbiterConflictError(AnnotationDomainError):
    code = "annotation_arbiter_conflict"


class GoldSetAccessDeniedError(AnnotationDomainError):
    code = "gold_set_access_denied"


class GoldSetNotActiveError(AnnotationDomainError):
    code = "gold_set_not_active"


class GoldSetSchemaIncompatibleError(AnnotationDomainError):
    code = "gold_set_schema_incompatible"


class GoldSetExposureForbiddenError(AnnotationDomainError):
    code = "gold_set_item_exposure_forbidden"


class QualitySampleNotReviewableError(AnnotationDomainError):
    code = "quality_sample_not_reviewable"


class QualitySamplePolicyMissingError(AnnotationDomainError):
    code = "quality_sample_policy_missing"


class QualitySampleSelfReviewForbiddenError(AnnotationDomainError):
    code = "quality_sample_self_review_forbidden"


class IAAInsufficientOverlapError(AnnotationDomainError):
    code = "iaa_insufficient_overlap"


class IAASourceForbiddenError(AnnotationDomainError):
    code = "iaa_source_forbidden"


class ReworkLimitExceededError(AnnotationDomainError):
    code = "annotation_task_rework_limit_exceeded"
