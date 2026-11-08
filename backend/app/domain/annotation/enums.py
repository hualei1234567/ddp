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
