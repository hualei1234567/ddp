"""采集任务/批次应用层命令。"""

from __future__ import annotations

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
