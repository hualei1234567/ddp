"""处理运行（ProcessingRun）枚举。

取值即数据库 CHECK 约束与契约枚举的唯一来源。状态与阶段都是封闭集合：
未知取值一律拒绝，不存在"未知状态默认成功"的分支。

**七个状态**（N4-001 权威定义）：
``CREATED → QUEUED → RUNNING → SUCCEEDED/FAILED``，失败可经 ``RETRYING``
重新排队；``CANCELLED`` 可由 CREATED/QUEUED/RUNNING/FAILED/RETRYING 进入。``SUCCEEDED`` 与
``CANCELLED`` 是终态，没有出边。

**八个阶段**取自《DDP 具身智能数据闭环平台建设总规范》§7.1 Pipeline：
``Upload → File Validation → Metadata Extraction → Format Parsing →
Time Alignment → Calibration Validation → Episode Segmentation →
Data Cleaning → Quality Pre-check → Artifact Registration``。
Upload 由接入域（``ingestion_jobs``）负责、Artifact Registration 由产物
记录承担，因此本实体只承载中间八个执行阶段。本任务（N4-001）只定义阶段
取值与顺序，不实现编排（N4-002）。
"""

from __future__ import annotations

from enum import StrEnum


class ProcessingRunStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RETRYING = "RETRYING"


class ProcessingRunAction(StrEnum):
    """处理运行动作。

    ``system-*`` / ``worker-*`` 只允许服务端内部调用，**不会**出现在
    ``allowed_actions`` 中，也不得下发给客户端。
    """

    SYSTEM_QUEUE = "system-queue"
    WORKER_CLAIM = "worker-claim"
    WORKER_ADVANCE = "worker-advance"
    WORKER_SUCCEED = "worker-succeed"
    WORKER_FAIL = "worker-fail"
    WORKER_RETRY = "worker-retry"
    CANCEL = "cancel"


class ProcessingStage(StrEnum):
    FILE_VALIDATION = "FILE_VALIDATION"
    METADATA_EXTRACTION = "METADATA_EXTRACTION"
    FORMAT_PARSING = "FORMAT_PARSING"
    TIME_ALIGNMENT = "TIME_ALIGNMENT"
    CALIBRATION_VALIDATION = "CALIBRATION_VALIDATION"
    EPISODE_SEGMENTATION = "EPISODE_SEGMENTATION"
    DATA_CLEANING = "DATA_CLEANING"
    QUALITY_PRE_CHECK = "QUALITY_PRE_CHECK"


# 阶段顺序是权威定义：只允许前进，不允许回退（重试从当前阶段继续）。
STAGE_SEQUENCE: tuple[ProcessingStage, ...] = tuple(ProcessingStage)

# 终态：没有出边。
PROCESSING_RUN_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        ProcessingRunStatus.SUCCEEDED.value,
        ProcessingRunStatus.CANCELLED.value,
    }
)

# 客户端可见动作的唯一来源；``system-*`` / ``worker-*`` 永远不在此集合内。
PUBLIC_PROCESSING_ACTIONS: frozenset[str] = frozenset({ProcessingRunAction.CANCEL.value})


__all__ = [
    "PROCESSING_RUN_TERMINAL_STATUSES",
    "PUBLIC_PROCESSING_ACTIONS",
    "STAGE_SEQUENCE",
    "ProcessingRunAction",
    "ProcessingRunStatus",
    "ProcessingStage",
]
