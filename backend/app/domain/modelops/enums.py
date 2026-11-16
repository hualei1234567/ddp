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


class CoverageSource(StrEnum):
    """缺口视图里 ``current_count`` 的来源。

    真实覆盖（Episode 统计）优先；测不到时退回人工填写值，二者都不可得才 UNKNOWN。
    绝不把"没有统计"当成 0。
    """

    MEASURED = "MEASURED"
    MANUAL = "MANUAL"
    UNKNOWN = "UNKNOWN"


class GapFreshness(StrEnum):
    """缺口视图的新鲜度；由真实覆盖数据的最近更新时间与可配阈值计算。"""

    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class CoverageDimension(StrEnum):
    """缺口视图可以「实测」的维度。

    取值刻意与契约 ``GapDimensionType`` 同名同值，聚合时才能与反馈维度直接对上。

    只收录 Episode 上有权威落点的轴：

    - ``SCENE`` ← ``episodes.scene_labels``（JSON 数组）
    - ``ROBOT`` ← ``episodes.robot_id``
    - ``DEVICE`` ← ``episodes.device_ids``（JSON 数组）
    - ``QUALITY_STATUS`` ← ``episodes.quality_status``

    ``ACTION`` / ``LABEL`` / ``MODALITY`` 在 Episode 上没有权威字段（``task_id`` 是
    采集任务而非动作规格，强行映射会把"任务"当成"动作"），因此**不可实测**：
    它们只会走人工值路径，见交付报告中的契约缺口登记。
    """

    SCENE = "SCENE"
    ROBOT = "ROBOT"
    DEVICE = "DEVICE"
    QUALITY_STATUS = "QUALITY_STATUS"


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
