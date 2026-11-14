"""供应商与到货验收枚举。

取值即为数据库 CHECK 约束与契约枚举的唯一来源；未知取值一律拒绝，
不存在"未知状态默认成功"的分支。
"""

from __future__ import annotations

from enum import StrEnum


class SupplierStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"


class SupplierCategory(StrEnum):
    """供应商类型；用于筛选与绩效分组，不参与状态判断。"""

    DATA_COLLECTION = "DATA_COLLECTION"
    ANNOTATION = "ANNOTATION"
    HARDWARE = "HARDWARE"
    INTEGRATION = "INTEGRATION"
    OTHER = "OTHER"


class DeliveryBatchStatus(StrEnum):
    ANNOUNCED = "ANNOUNCED"
    IN_TRANSIT = "IN_TRANSIT"
    ARRIVED = "ARRIVED"
    SCREENING = "SCREENING"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_ACCEPTED = "PARTIALLY_ACCEPTED"
    REJECTED = "REJECTED"
    REWORK = "REWORK"
    CLOSED = "CLOSED"


class SegmentStatus(StrEnum):
    REGISTERED = "REGISTERED"
    PENDING_REVIEW = "PENDING_REVIEW"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NEED_MORE = "NEED_MORE"


class SegmentDecision(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    NEED_MORE = "NEED_MORE"


# 批次已终审（不再接受片段裁决）的状态集合。
BATCH_SETTLED_STATUSES: frozenset[str] = frozenset(
    {
        DeliveryBatchStatus.ACCEPTED.value,
        DeliveryBatchStatus.PARTIALLY_ACCEPTED.value,
        DeliveryBatchStatus.REJECTED.value,
        DeliveryBatchStatus.CLOSED.value,
    }
)

# 片段终态：不可再次裁决。
SEGMENT_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        SegmentStatus.ACCEPTED.value,
        SegmentStatus.REJECTED.value,
    }
)

# 需要在账本中记录"已裁决"的片段状态。
SEGMENT_DECIDED_STATUSES: frozenset[str] = frozenset(
    {
        SegmentStatus.ACCEPTED.value,
        SegmentStatus.REJECTED.value,
        SegmentStatus.NEED_MORE.value,
    }
)


class SupplierAction(StrEnum):
    ACTIVATE = "activate"
    SUSPEND = "suspend"
    RESUME = "resume"
    TERMINATE = "terminate"


class DeliveryBatchAction(StrEnum):
    DISPATCH = "dispatch"
    ARRIVE = "arrive"
    START_SCREENING = "start-screening"
    FINALIZE = "finalize"
    REWORK = "rework"
    CLOSE = "close"


class SegmentAction(StrEnum):
    QUEUE_REVIEW = "queue-review"
    RESUBMIT = "resubmit"
