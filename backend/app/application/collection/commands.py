"""采集任务/批次与素材应用层命令。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class NewCollectionTask:
    code: str
    name: str
    priority: str = "P1"
    target_episode_count: int = 0
    target_duration_sec: int = 0
    requirement_id: UUID | None = None
    plan_id: UUID | None = None
    sop_id: UUID | None = None
    sop_version: str | None = None
    scenario_id: UUID | None = None
    action_spec_id: UUID | None = None
    action_spec_version: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    location_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewCollectionBatch:
    expected_count: int = 0
    code: str | None = None
    sequence_no: int | None = None
    assigned_to: UUID | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None


@dataclass(frozen=True, slots=True)
class NewCaptureSession:
    """创建采集会话的命令。

    只接受"这次执行绑定谁/在哪/属于哪个任务或批次"；``status``、时间戳、
    ``project_id``、``version`` 与审计字段全部由服务端决定。
    """

    code: str
    task_id: UUID | None = None
    batch_id: UUID | None = None
    operator_id: UUID | None = None
    robot_id: UUID | None = None
    location_id: UUID | None = None


# --- COL-002 素材命令 -------------------------------------------------------
# 四类素材的创建命令字段刻意保持"扁平 + 可选"，与既有 ``NewCollectionTask`` 一致；
# 状态由用例填 ``DRAFT``，不接受客户端传 status（服务端权威字段）。


@dataclass(frozen=True, slots=True)
class NewCollectionPlan:
    code: str
    name: str
    plan_version: int = 1
    description: str | None = None
    owner_id: UUID | None = None
    target_episode_count: int | None = None
    quality_thresholds: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class NewCollectionSop:
    code: str
    name: str
    sop_version: str
    steps: tuple[dict[str, object], ...] = ()
    description: str | None = None


@dataclass(frozen=True, slots=True)
class NewScenario:
    code: str
    name: str
    scene_labels: tuple[str, ...] = ()
    description: str | None = None


@dataclass(frozen=True, slots=True)
class NewActionSpec:
    code: str
    name: str
    spec_version: str
    actions: tuple[dict[str, object], ...] = ()
    description: str | None = None
