"""阶段 3 标注域枚举。

取值以 ``docs/architecture/contract-draft/annotation/state-machines.v1.json``
与对应 JSON Schema 草案为准；草案未冻结，未知值一律拒绝。
"""

from __future__ import annotations

from enum import StrEnum


class AnnotationSchemaStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"
    REVOKED = "REVOKED"


class AnnotationSchemaAction(StrEnum):
    SUBMIT_REVIEW = "submit-review"
    RETURN_DRAFT = "return-draft"
    PUBLISH = "publish"
    DEPRECATE = "deprecate"
    REVOKE = "revoke"


class AnnotationLabelType(StrEnum):
    ENUM = "ENUM"
    MULTI_ENUM = "MULTI_ENUM"
    BOOLEAN = "BOOLEAN"
    NUMBER = "NUMBER"
    STRING = "STRING"
    FREE_TEXT = "FREE_TEXT"
    TIME_RANGE = "TIME_RANGE"
    ACTION_SEGMENT = "ACTION_SEGMENT"
    OBJECT_TRACK = "OBJECT_TRACK"
    JSON = "JSON"


class AnnotationLevel(StrEnum):
    TASK = "TASK"
    EPISODE = "EPISODE"
    TIME_RANGE = "TIME_RANGE"
    ACTION_SEGMENT = "ACTION_SEGMENT"
    FRAME = "FRAME"
    OBJECT_TRACK = "OBJECT_TRACK"


class LabelVisibility(StrEnum):
    ANNOTATOR = "ANNOTATOR"
    REVIEWER_ONLY = "REVIEWER_ONLY"
    GOLD_ONLY = "GOLD_ONLY"
    SYSTEM_ONLY = "SYSTEM_ONLY"


class SchemaCompatibility(StrEnum):
    BACKWARD_COMPATIBLE = "BACKWARD_COMPATIBLE"
    BREAKING = "BREAKING"


class AnnotationSampleSetStatus(StrEnum):
    DRAFT = "DRAFT"
    PREVIEW = "PREVIEW"
    SNAPSHOTTED = "SNAPSHOTTED"
    CONSUMED = "CONSUMED"
    CANCELLED = "CANCELLED"


class AnnotationSampleSetAction(StrEnum):
    PREVIEW = "preview"
    SNAPSHOT = "snapshot"
    CONSUME = "consume"
    CANCEL = "cancel"


class SampleStrategy(StrEnum):
    FILTER = "FILTER"
    STRATIFIED = "STRATIFIED"
    RISK_BASED = "RISK_BASED"
    MANUAL = "MANUAL"


class DedupKey(StrEnum):
    EPISODE_LINEAGE = "EPISODE_LINEAGE"
    EPISODE_VERSION = "EPISODE_VERSION"
    NONE = "NONE"


class SampleRole(StrEnum):
    NORMAL = "NORMAL"
    GOLD = "GOLD"
    IAA = "IAA"
    QUALITY = "QUALITY"


class SampleTimeDomain(StrEnum):
    UTC = "UTC"
    ROS = "ROS"
    DEVICE = "DEVICE"
    HOST = "HOST"
    MONOTONIC = "MONOTONIC"
    UNKNOWN = "UNKNOWN"


class DataSource(StrEnum):
    REAL = "REAL"
    SYNTHETIC = "SYNTHETIC"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class AnnotationTaskStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    SUBMITTED = "SUBMITTED"
    REVIEWING = "REVIEWING"
    ARBITRATION = "ARBITRATION"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    REWORK = "REWORK"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ARCHIVED = "ARCHIVED"


class AnnotationTaskAction(StrEnum):
    SUBMIT_REVIEW = "submit-review"
    RETURN_DRAFT = "return-draft"
    APPROVE = "approve"
    READY = "ready"
    ASSIGN = "assign"
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    BLOCK = "block"
    UNBLOCK = "unblock"
    SUBMIT = "submit"
    SYSTEM_REVIEW = "system-review"
    ACCEPT = "accept"
    REJECT = "reject"
    ESCALATE = "escalate"
    RESOLVE_ACCEPTED = "resolve-accepted"
    RESOLVE_REJECTED = "resolve-rejected"
    REWORK = "rework"
    RESUME_WORK = "resume-work"
    COMPLETE = "complete"
    ARCHIVE = "archive"
    CANCEL = "cancel"


class AnnotationBatchStatus(StrEnum):
    PLANNED = "PLANNED"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    SUBMITTED = "SUBMITTED"
    REVIEWING = "REVIEWING"
    ARBITRATION = "ARBITRATION"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    REWORK = "REWORK"
    CANCELLED = "CANCELLED"


class AnnotationBatchAction(StrEnum):
    READY = "ready"
    ASSIGN = "assign"
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    BLOCK = "block"
    UNBLOCK = "unblock"
    SUBMIT = "submit"
    SYSTEM_REVIEW = "system-review"
    ACCEPT = "accept"
    REJECT = "reject"
    ESCALATE = "escalate"
    RESOLVE_ACCEPTED = "resolve-accepted"
    RESOLVE_REJECTED = "resolve-rejected"
    REWORK = "rework"
    RESUME_WORK = "resume-work"
    CANCEL = "cancel"


class AnnotationAssignmentStatus(StrEnum):
    ASSIGNED = "ASSIGNED"
    CLAIMED = "CLAIMED"
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    COMPLETED = "COMPLETED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class AnnotationAssignmentAction(StrEnum):
    CLAIM = "claim"
    REVOKE = "revoke"
    SYSTEM_EXPIRE = "system-expire"
    START = "start"
    RELEASE = "release"
    SUBMIT = "submit"
    COMPLETE = "complete"
    REWORK = "rework"


class AssignmentRole(StrEnum):
    ANNOTATOR = "ANNOTATOR"
    REVIEWER = "REVIEWER"
    ARBITER = "ARBITER"


class TaskPriority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class AssignmentMode(StrEnum):
    MANUAL = "MANUAL"
    CLAIM = "CLAIM"
    MIXED = "MIXED"


class WorkloadStatus(StrEnum):
    NORMAL = "NORMAL"
    AT_CAPACITY = "AT_CAPACITY"
    OVER_CAPACITY = "OVER_CAPACITY"
    STALE = "STALE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class MissingSemantics(StrEnum):
    NONE = "NONE"
    NOT_APPLICABLE = "N/A"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"
    PARTIAL = "PARTIAL"


class AnnotationSubmissionStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
    ARBITRATING = "ARBITRATING"
    RESOLVED_APPROVED = "RESOLVED_APPROVED"
    RESOLVED_REJECTED = "RESOLVED_REJECTED"
    RESOLVED_REWORK = "RESOLVED_REWORK"
    REWORK_REQUIRED = "REWORK_REQUIRED"
    WITHDRAWN = "WITHDRAWN"
    SUPERSEDED = "SUPERSEDED"


class AnnotationSubmissionAction(StrEnum):
    SUBMIT = "submit"
    WITHDRAW = "withdraw"
    SYSTEM_REVIEW_START = "system-review-start"
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"
    ARBITRATE_START = "arbitrate-start"
    RESOLVE_APPROVED = "resolve-approved"
    RESOLVE_REJECTED = "resolve-rejected"
    RESOLVE_REWORK = "resolve-rework"
    REQUIRE_REWORK = "require-rework"
    SYSTEM_SUPERSEDE = "system-supersede"


class AnnotationRecordStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"


class AnnotationRecordAction(StrEnum):
    SUBMIT_WITH_SUBMISSION = "submit-with-submission"
    APPROVE_WITH_SUBMISSION = "approve-with-submission"
    REJECT_WITH_SUBMISSION = "reject-with-submission"
    SYSTEM_SUPERSEDE = "system-supersede"
    INVALIDATE = "invalidate"


class LabelValueSource(StrEnum):
    HUMAN = "HUMAN"
    AI_ASSISTED = "AI_ASSISTED"
    IMPORTED = "IMPORTED"


class LabelValueStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class AnnotationReviewStatus(StrEnum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"


class AnnotationReviewAction(StrEnum):
    CLAIM = "claim"
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"
    CANCEL = "cancel"


class ReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"


class ReviewIssueSeverity(StrEnum):
    BLOCKER = "BLOCKER"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    SUGGESTION = "SUGGESTION"


class AnnotationArbitrationStatus(StrEnum):
    PENDING = "PENDING"
    IN_ARBITRATION = "IN_ARBITRATION"
    RESOLVED_APPROVED = "RESOLVED_APPROVED"
    RESOLVED_REJECTED = "RESOLVED_REJECTED"
    RESOLVED_REWORK = "RESOLVED_REWORK"
    CANCELLED = "CANCELLED"


class AnnotationArbitrationAction(StrEnum):
    CLAIM = "claim"
    RECUSE = "recuse"
    RESOLVE_APPROVED = "resolve-approved"
    RESOLVE_REJECTED = "resolve-rejected"
    RESOLVE_REWORK = "resolve-rework"
    CANCEL = "cancel"


class ArbitrationTrigger(StrEnum):
    REVIEWER_DISAGREEMENT = "REVIEWER_DISAGREEMENT"
    ANNOTATOR_DISPUTE = "ANNOTATOR_DISPUTE"
    GOLD_SET_CONFLICT = "GOLD_SET_CONFLICT"
    IAA_CONFLICT = "IAA_CONFLICT"
    HIGH_RISK = "HIGH_RISK"
    POLICY = "POLICY"


class ArbitrationDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REWORK = "REWORK"
    ABSTAIN = "ABSTAIN"


class GoldSetStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    REVOKED = "REVOKED"


class GoldSetAction(StrEnum):
    SUBMIT_REVIEW = "submit-review"
    RETURN_DRAFT = "return-draft"
    ACTIVATE = "activate"
    RETIRE = "retire"
    REVOKE = "revoke"


class GoldSetExposurePolicy(StrEnum):
    HIDDEN = "HIDDEN"
    ROTATING = "ROTATING"
    CONTROLLED = "CONTROLLED"


class GoldRotationPolicy(StrEnum):
    NONE = "NONE"
    PER_TASK = "PER_TASK"
    PER_PERIOD = "PER_PERIOD"
    PER_ANNOTATOR = "PER_ANNOTATOR"


class GoldItemDifficulty(StrEnum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"


class QualitySampleStatus(StrEnum):
    PLANNED = "PLANNED"
    SAMPLED = "SAMPLED"
    ASSIGNED = "ASSIGNED"
    IN_REVIEW = "IN_REVIEW"
    PASSED = "PASSED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class QualitySampleAction(StrEnum):
    SAMPLE = "sample"
    ASSIGN = "assign"
    START = "start"
    PASS = "pass"  # noqa: S105 - 枚举值不是凭据
    FAIL = "fail"
    ESCALATE = "escalate"
    CLOSE = "close"
    CANCEL = "cancel"


class QualitySampleType(StrEnum):
    RANDOM = "RANDOM"
    STRATIFIED = "STRATIFIED"
    RISK_BASED = "RISK_BASED"
    GOLD_SET = "GOLD_SET"
    IAA_OVERLAP = "IAA_OVERLAP"


class QualitySampleOutcome(StrEnum):
    PASS = "PASS"  # noqa: S105 - 枚举值不是凭据
    FAIL = "FAIL"
    ESCALATE = "ESCALATE"


class IAAReportStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    CANCELLED = "CANCELLED"


class IAAReportAction(StrEnum):
    WORKER_START = "worker-start"
    WORKER_SUCCEED = "worker-succeed"
    WORKER_FAIL = "worker-fail"
    WORKER_INSUFFICIENT = "worker-insufficient"
    RETRY = "retry"
    CANCEL = "cancel"


class IAAMetric(StrEnum):
    PERCENT_AGREEMENT = "PERCENT_AGREEMENT"
    COHEN_KAPPA = "COHEN_KAPPA"
    FLEISS_KAPPA = "FLEISS_KAPPA"
    KRIPPENDORFF_ALPHA = "KRIPPENDORFF_ALPHA"
    SPAN_F1 = "SPAN_F1"
    BOUNDARY_MAE = "BOUNDARY_MAE"
    GOLD_ACCURACY = "GOLD_ACCURACY"


class IAAValueStatus(StrEnum):
    OK = "OK"
    NOT_APPLICABLE = "N/A"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    PARTIAL = "PARTIAL"


class IAAObservationSource(StrEnum):
    SUBMISSION = "SUBMISSION"
    GOLD_SET = "GOLD_SET"


class QualityRuleSeverity(StrEnum):
    """质量规则严重级别：ERROR 计入失败判定，WARN 只提醒。

    只设两级：三级以上会让"是否阻断"的判定散落在各个调用点。是否需要阻断
    由 ``count`` 与 ``QualityCheckStatus`` 的映射决定，见
    :mod:`app.application.annotation.quality_checks`。
    """

    ERROR = "ERROR"
    WARN = "WARN"


class QualityCheckStatus(StrEnum):
    """一次质量检查的**结论**（不是异步执行状态）。

    同步执行且与业务写入同事务：执行失败即事务回滚，不存在"执行失败但留下
    记录"的中间态，因此不需要 RUNNING / ERROR 之类的执行态词汇。
    """

    PASSED = "PASSED"
    FAILED = "FAILED"
