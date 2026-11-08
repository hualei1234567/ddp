"""Episode/血缘用例命令。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from app.domain.episodes.models import Segmentation


@dataclass(frozen=True, slots=True)
class CreateEpisodeCommand:
    raw_asset_ids: tuple[UUID, ...]
    segmentation: Segmentation
    time_base: Mapping[str, object]
    calibration_snapshot: tuple[Mapping[str, object], ...]
    canonical_schema_version: str
    pipeline_id: str
    pipeline_version: str
    code: str | None = None
    task_id: UUID | None = None
    batch_id: UUID | None = None
    robot_id: UUID | None = None
    device_ids: tuple[UUID, ...] = ()
    sensor_ids: tuple[UUID, ...] = ()
    scene_labels: tuple[str, ...] = ()
    language_instruction: str | None = None
    success: bool | None = None
    failure_reason: str | None = None
    ingestion_job_ids: tuple[UUID, ...] = ()
    evidence_role: str = "INPUT"


@dataclass(frozen=True, slots=True)
class ReviseEpisodeCommand:
    code: str
    expected_version: int | None = None


@dataclass(frozen=True, slots=True)
class MarkEpisodeReadyCommand:
    expected_version: int
    manifest_key: str
    manifest: Mapping[str, object]
    frame_count: int


@dataclass(frozen=True, slots=True)
class EpisodeActionCommand:
    action: str
    expected_version: int
    reason: str | None = None
    quality_status: str | None = None
    error_code: str | None = None
    error_summary: str | None = None


@dataclass(frozen=True, slots=True)
class AppendLineageEdgeCommand:
    from_node_type: str
    from_node_id: UUID
    to_node_type: str
    to_node_id: UUID
    relation: str
    metadata: Mapping[str, object] = field(default_factory=dict)
