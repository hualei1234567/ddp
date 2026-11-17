"""Episode/Job 集成测试共享构造器。"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.application.episodes.commands import (
    CreateEpisodeCommand,
    EpisodeActionCommand,
    MarkEpisodeReadyCommand,
)
from app.application.episodes.episodes import EpisodeService
from app.application.episodes.lineage import LineageService
from app.application.episodes.provenance import SourceProvenanceResolver
from app.application.ingestion.commands import CreateIngestionJobCommand
from app.application.ingestion.jobs import IngestionJobService
from app.domain.episodes.models import Episode, Segmentation
from app.domain.episodes.validation import build_manifest_key
from app.domain.identity.models import ProjectScope
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.datasource_provenance import (
    SqlSourceProvenanceRepository,
)
from app.infrastructure.repositories.episodes import (
    SqlEpisodeRawAssetRepository,
    SqlEpisodeRepository,
)
from app.infrastructure.repositories.ingestion_jobs import SqlIngestionJobRepository
from app.infrastructure.repositories.lineage import SqlLineageRepository
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from sqlalchemy.orm import Session

SHA_A = f"sha256:{'a' * 64}"


def time_base() -> dict[str, object]:
    return {
        "time_domain": "UTC",
        "clock_source": "NTP",
        "sync_method": "NTP",
        "sync_status": "VERIFIED",
    }


def calibration_snapshot() -> tuple[dict[str, object], ...]:
    return (
        {
            "calibration_id": str(uuid4()),
            "calibration_version": 1,
            "checksum": SHA_A,
            "target_type": "ROBOT",
            "target_id": str(uuid4()),
            "schema_version": "ddp.calibration/1.0.0",
        },
    )


def create_command(
    asset_id: UUID,
    *,
    code: str | None = None,
    start_time_ns: int = 1_000,
    end_time_ns: int = 2_000,
    allow_overlap: bool = False,
    overlap_reason: str | None = None,
) -> CreateEpisodeCommand:
    return CreateEpisodeCommand(
        raw_asset_ids=(asset_id,),
        segmentation=Segmentation(
            method="MANUAL_TIME_RANGE",
            rule_version="seg-manual-v1",
            start_time_ns=start_time_ns,
            end_time_ns=end_time_ns,
            allow_overlap=allow_overlap,
            overlap_reason=overlap_reason,
        ),
        time_base=time_base(),
        calibration_snapshot=calibration_snapshot(),
        canonical_schema_version="ddp.canonical/1.0.0-draft",
        pipeline_id="parser",
        pipeline_version="1.0.0",
        code=code,
    )


def job_command(
    asset_id: UUID,
    *,
    job_type: str = "PARSE_METADATA",
    idempotency_key: str | None = None,
    max_attempts: int = 3,
) -> CreateIngestionJobCommand:
    return CreateIngestionJobCommand(
        job_type=job_type,
        timeout_sec=60,
        raw_asset_ids=(asset_id,),
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        pipeline_id="parser",
        pipeline_version="1.0.0",
    )


def manifest_for(episode: Episode) -> dict[str, object]:
    return {
        "schema_version": "ddp.canonical/1.0.0-draft",
        "episode_id": str(episode.episode_id),
        "episode_lineage_id": str(episode.episode_lineage_id),
        "episode_version": episode.episode_version,
        "project_id": str(episode.project_id),
        "code": episode.code,
        "task_id": None,
        "batch_id": None,
        "robot_id": None,
        "device_ids": [],
        "sensor_ids": [],
        "start_time": str(episode.start_time_ns),
        "end_time": str(episode.end_time_ns),
        "duration_ns": str(episode.duration_ns),
        "time_base": dict(episode.time_base),
        "time_alignment": {"method": "NTP"},
        "calibration_snapshot": [dict(item) for item in episode.calibration_snapshot],
        "streams": [],
        "raw_asset_ids": [],
        "provenance": {"pipeline_id": episode.pipeline_id},
        "quality": {"status": "PASS", "rule_version": "q1", "issues": []},
    }


def manifest_key(episode: Episode) -> str:
    return build_manifest_key(
        episode.project_id,
        episode.episode_id,
        episode.episode_version,
    )


def build_services(
    session: Session,
) -> tuple[EpisodeService, IngestionJobService, LineageService]:
    episodes = SqlEpisodeRepository(session)
    episode_assets = SqlEpisodeRawAssetRepository(session)
    lineage = SqlLineageRepository(session)
    raw_assets = SqlRawAssetRepository(session)
    jobs = SqlIngestionJobRepository(session)
    audit = SqlAuditRepository(session)
    outbox = SqlOutboxRepository(session)
    episode_service = EpisodeService(
        episodes,
        episode_assets,
        lineage,
        raw_assets,
        jobs,
        audit,
        outbox,
        session,
    )
    job_service = IngestionJobService(jobs, raw_assets, audit, outbox, session)
    lineage_service = LineageService(
        lineage,
        audit,
        outbox,
        session,
        SourceProvenanceResolver(
            SqlSourceProvenanceRepository(session),
            raw_assets,
        ),
    )
    return episode_service, job_service, lineage_service


def build_readiness_service(session: Session):
    """装配自动就绪用例。

    刻意走**生产装配函数**而不是在测试里再抄一遍六个协作者：接线一旦漂移，
    测试就测不到真实链路了。
    """
    from app.api.v1.episode_dependencies import build_readiness_service as build

    return build(session)


def make_ready(
    service: EpisodeService,
    scope: ProjectScope,
    episode: Episode,
) -> Episode:
    processing = service.apply_action(
        scope,
        episode.episode_id,
        EpisodeActionCommand(action="system-start", expected_version=episode.version),
    )
    return service.mark_ready(
        scope,
        processing.episode_id,
        MarkEpisodeReadyCommand(
            expected_version=processing.version,
            manifest_key=manifest_key(processing),
            manifest=manifest_for(processing),
            frame_count=30,
        ),
    )


__all__ = [
    "SHA_A",
    "build_readiness_service",
    "build_services",
    "calibration_snapshot",
    "create_command",
    "job_command",
    "make_ready",
    "manifest_for",
    "manifest_key",
    "time_base",
]
