"""处理运行（ProcessingRun）枚举。

取值即数据库 CHECK 约束与契约枚举的唯一来源。状态与阶段都是封闭集合：
未知取值一律拒绝，不存在"未知状态默认成功"的分支。

**七个状态**（N4-001 权威定义）：
``CREATED → QUEUED → RUNNING → SUCCEEDED/FAILED``，失败可经 ``RETRYING``
重新排队；``CANCELLED`` 可由 CREATED/QUEUED/RUNNING/RETRYING 进入。
``FAILED`` 不能直接取消（那会抹掉错误原因），只能由服务端 ``system-close``
做保留错误的收口。``SUCCEEDED`` / ``CANCELLED`` 与收口后的 ``FAILED`` 是
终态，没有出边。

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
    SYSTEM_CLOSE = "system-close"
    WORKER_CLAIM = "worker-claim"
    WORKER_ADVANCE = "worker-advance"
    WORKER_SUCCEED = "worker-succeed"
    WORKER_FAIL = "worker-fail"
    WORKER_RETRY = "worker-retry"
    CANCEL = "cancel"


class ProcessingStepStatus(StrEnum):
    """单个阶段的执行结果（N4-002）。

    ``SKIPPED`` 是一等状态，不是"成功"的别名：它表示该阶段在**当前流水线
    版本下没有真实实现**，必须同时记录原因（``log_summary``），数据库 CHECK
    也要求它非空。把未实现阶段写成 ``SUCCEEDED`` 会让"处理完成"变成谎话。
    """

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


#: 终态步骤：不再变化，重试会以新的 ``attempt`` 另起一行。
PROCESSING_STEP_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        ProcessingStepStatus.SUCCEEDED.value,
        ProcessingStepStatus.FAILED.value,
        ProcessingStepStatus.SKIPPED.value,
    }
)


class ProcessingStage(StrEnum):
    FILE_VALIDATION = "FILE_VALIDATION"
    METADATA_EXTRACTION = "METADATA_EXTRACTION"
    FORMAT_PARSING = "FORMAT_PARSING"
    TIME_ALIGNMENT = "TIME_ALIGNMENT"
    CALIBRATION_VALIDATION = "CALIBRATION_VALIDATION"
    #: V51-001~003：从真实 payload 提取 SensorStream / Observation / Action。
    #: 放在 EPISODE_SEGMENTATION 之前——先有流与观察，才有"按时间切 Episode"的依据。
    STREAM_EXTRACTION = "STREAM_EXTRACTION"
    EPISODE_SEGMENTATION = "EPISODE_SEGMENTATION"
    DATA_CLEANING = "DATA_CLEANING"
    QUALITY_PRE_CHECK = "QUALITY_PRE_CHECK"


# 阶段顺序是权威定义：只允许前进，不允许回退（重试从当前阶段继续）。
STAGE_SEQUENCE: tuple[ProcessingStage, ...] = tuple(ProcessingStage)

# 状态层面的终态：没有出边。注意 ``FAILED`` 写入 ``closed_at`` 后也是终态，
# 判断请用 ``ProcessingRun.is_terminal``（只看持久字段，不读取审计表）。
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
    "PROCESSING_STEP_TERMINAL_STATUSES",
    "PUBLIC_PROCESSING_ACTIONS",
    "STAGE_SEQUENCE",
    "ProcessingRunAction",
    "ProcessingRunStatus",
    "ProcessingStage",
    "ProcessingStepStatus",
]
