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


class FailureCaseCategory(StrEnum):
    """失败用例的第一层归因。

    只分"能力环节"这一层，不把具体场景/物体塞进枚举：场景维度属于 Data Gap
    的输入（N7-004），这里只提供稳定、可聚合的事实分类。``OTHER`` 是显式的
    逃生口，避免适配器把未知类别硬塞进已有桶里。
    """

    PERCEPTION = "PERCEPTION"
    PLANNING = "PLANNING"
    CONTROL = "CONTROL"
    LANGUAGE = "LANGUAGE"
    OTHER = "OTHER"


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

    每个轴只有一个权威落点，全部来自 Episode 及其**数据库级外键**可解析的关联行：

    - ``SCENE`` ← ``episodes.scene_labels``（JSON 数组）
    - ``ROBOT`` ← ``episodes.robot_id``
    - ``DEVICE`` ← ``episodes.device_ids``（JSON 数组）
    - ``QUALITY_STATUS`` ← ``episodes.quality_status``
    - ``MODALITY`` ← ``episodes.sensor_ids`` → ``sensors.sensor_type``
      （传感器类型就是模态声明：RGB / DEPTH / IMU / AUDIO …）
    - ``ACTION`` ← ``episodes.task_id`` → ``collection_tasks.action_spec_id`` →
      ``action_specs.actions[].code``（回退 ``name``）。按**被引用的那一行**统计，
      不按 ``code`` 的版本谱系展开——任务冻结的是版本行，不是"同 code 的所有版本"。
    - ``LABEL`` ← ``episodes.annotation_status`` + ``annotation_records``（仅 APPROVED）
      → ``annotation_records.labels[].label_key``

    这两类轴的口径不同，见 :data:`CLOSED_COVERAGE_DIMENSIONS`。
    """

    SCENE = "SCENE"
    ROBOT = "ROBOT"
    DEVICE = "DEVICE"
    QUALITY_STATUS = "QUALITY_STATUS"
    ACTION = "ACTION"
    LABEL = "LABEL"
    MODALITY = "MODALITY"


CLOSED_COVERAGE_DIMENSIONS: frozenset[CoverageDimension] = frozenset(
    {
        CoverageDimension.ACTION,
        CoverageDimension.LABEL,
        CoverageDimension.MODALITY,
    }
)
"""「封闭轴」：总体完整时才允许把"缺失"解释成"0 覆盖"。

- ``ACTION``：覆盖范围内**每个** Episode 都能经 ``task_id → action_spec_id`` 解析出
  可识别的动作定义；
- ``LABEL``：**每个** Episode 都已完成标注（``annotation_status='APPROVED'`` 且存在
  ``APPROVED`` 记录）——未标注的 Episode 只是"还不知道有没有这个标签"；
- ``MODALITY``：**每个** Episode 都声明了可解析的 ``sensor_ids``。

任一 Episode 不具备权威来源时，它的取值是"未知"而不是"没有"，此时**整轴不返回
实测值**，由上游回退人工值或 ``UNKNOWN``。总体完整时，某个维度值缺席才是真实的
0（``MEASURED`` + 0）。这条区分是「没数据 ≠ 覆盖率为 0」的具体落地：宁可报
``UNKNOWN``，也不能用只覆盖一部分 Episode 的数字冒充权威结论。
"""


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
