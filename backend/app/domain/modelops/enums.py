"""阶段 5 模型闭环领域枚举。"""

from __future__ import annotations

from enum import StrEnum


class ModelStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    ARCHIVED = "ARCHIVED"


class ModelTaskType(StrEnum):
    IMITATION = "IMITATION"
    REINFORCEMENT = "REINFORCEMENT"
    VLM = "VLM"
    OTHER = "OTHER"


class ModelVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    READY = "READY"
    RELEASED = "RELEASED"
    DEPRECATED = "DEPRECATED"
    ARCHIVED = "ARCHIVED"


class ArtifactFormat(StrEnum):
    ONNX = "ONNX"
    TORCHSCRIPT = "TORCHSCRIPT"
    SAFETENSORS = "SAFETENSORS"
    TENSORRT = "TENSORRT"
    OTHER = "OTHER"


class ArtifactVerification(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"


class EvaluationStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EvaluationVerdict(StrEnum):
    PASS = "PASS"  # noqa: S105 - 状态值，不是凭据
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class InferenceServiceStatus(StrEnum):
    PENDING = "PENDING"
    DEPLOYING = "DEPLOYING"
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    RETIRED = "RETIRED"


class InferenceEnvironment(StrEnum):
    DEV = "DEV"
    STAGING = "STAGING"
    PROD = "PROD"


class HealthStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"


class InferenceTaskStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FeedbackIssueType(StrEnum):
    ACTION_COVERAGE = "ACTION_COVERAGE"
    SCENE_COVERAGE = "SCENE_COVERAGE"
    LONG_TAIL = "LONG_TAIL"
    VIEWPOINT = "VIEWPOINT"
    GENERALIZATION = "GENERALIZATION"
    LABEL_GAP = "LABEL_GAP"
    DATA_QUALITY = "DATA_QUALITY"
    OTHER = "OTHER"


class FeedbackSeverity(StrEnum):
    BLOCKER = "BLOCKER"
    MAJOR = "MAJOR"
    MINOR = "MINOR"


class FeedbackStatus(StrEnum):
    OPEN = "OPEN"
    TRIAGED = "TRIAGED"
    REQUIREMENT_CREATED = "REQUIREMENT_CREATED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class GapDimensionType(StrEnum):
    SCENE = "SCENE"
    ACTION = "ACTION"
    ROBOT = "ROBOT"
    DEVICE = "DEVICE"
    LABEL = "LABEL"
    MODALITY = "MODALITY"


class RequirementSourceType(StrEnum):
    MODEL_FEEDBACK = "MODEL_FEEDBACK"
    EVALUATION = "EVALUATION"
    MANUAL = "MANUAL"
    OPERATION = "OPERATION"


class RequirementStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    SATISFIED = "SATISFIED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class RequirementPriority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
