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
    # 演练（DRY_RUN）专用终态：只做校验与计划，不产生训练产物、不调用外部平台。
    # 它不是 SUBMITTED（没有提交给任何外部平台），更不是 SUCCEEDED（没有训练）。
    # 单独成值是为了让"这是演练"成为可查询的**状态事实**，而不是只能靠
    # execution_mode 旁证；任何按 SUCCEEDED 判定真实训练成功的下游都不会误吞它。
    DRY_RUN = "DRY_RUN"


class ExecutionMode(StrEnum):
    """训练执行方式标记。

    这不是第二套训练状态机：它只回答"这次训练是在什么环境、用什么方式跑的"，
    与 ``ExternalTrainingStatus``（提交/运行/成功/失败）正交。引入它的唯一目的
    是让 Mock、空跑、本地参考实现与真实远端训练在数据与界面上**可区分**，
    避免把模拟结果当成真实训练结果。

    ``UNKNOWN`` 只用于"记录产生时还没有这个字段、无法回溯判定"的存量数据；
    新提交必须由 Adapter 配置或请求显式声明四种可执行方式之一，不得写入
    ``UNKNOWN``，更不得默认成 ``REMOTE``。
    """

    LOCAL = "LOCAL"
    MOCK = "MOCK"
    DRY_RUN = "DRY_RUN"
    REMOTE = "REMOTE"
    UNKNOWN = "UNKNOWN"


# 部署配置与客户端请求只允许声明这四种真实执行方式。UNKNOWN 是"无法判定"，
# 不是一种执行方式，因此既不允许配置也不允许客户端提交。
DECLARED_EXECUTION_MODES: frozenset[ExecutionMode] = frozenset(
    {
        ExecutionMode.LOCAL,
        ExecutionMode.MOCK,
        ExecutionMode.DRY_RUN,
        ExecutionMode.REMOTE,
    }
)

# 结果**不是真实训练**的执行方式。MOCK（模拟）与 DRY_RUN（空跑）都必须被下游
# 识别并拒绝当作真实训练结果；这是唯一权威定义，模型版本登记等下游从这里取，
# 不得各自复制一份字符串集合。
NON_REAL_EXECUTION_MODES: frozenset[ExecutionMode] = frozenset(
    {
        ExecutionMode.MOCK,
        ExecutionMode.DRY_RUN,
    }
)


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
        ExternalTrainingStatus.DRY_RUN,
    }
)
TERMINAL_GRANT_STATES: frozenset[DatasetGrantStatus] = frozenset(
    {
        DatasetGrantStatus.REJECTED,
        DatasetGrantStatus.EXPIRED,
        DatasetGrantStatus.REVOKED,
    }
)
