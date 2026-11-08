"""Episode/IngestionJob/血缘数据库兜底触发器测试（SQLite）。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.episodes import EpisodeModel, LineageNodeRefModel
from app.infrastructure.db.models.jobs import IngestionJobModel
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SHA = f"sha256:{'a' * 64}"


def _episode(project_id: UUID, user_id: UUID, status: str = "PENDING") -> EpisodeModel:
    return EpisodeModel(
        id=uuid4(),
        project_id=project_id,
        episode_lineage_id=uuid4(),
        episode_version=1,
        code=f"EP-{uuid4().hex[:12]}",
        status=status,
        quality_status="NOT_CHECKED",
        annotation_status="NOT_STARTED",
        start_time_ns=1_000,
        end_time_ns=2_000,
        duration_ns=1_000,
        time_domain="UTC",
        time_base={},
        canonical_schema_version="ddp.canonical/1.0.0-draft",
        pipeline_id="parser",
        pipeline_version="1.0.0",
        segmentation_rule_version="seg-manual-v1",
        manifest_key="canonical/x/y/1/manifest.json" if status != "PENDING" else None,
        manifest_sha256=SHA if status != "PENDING" else None,
        attempt=1,
        max_attempts=3,
        created_by=user_id,
        updated_by=user_id,
    )


def _job(project_id: UUID, user_id: UUID, status: str = "QUEUED") -> IngestionJobModel:
    return IngestionJobModel(
        id=uuid4(),
        project_id=project_id,
        job_type="PARSE_METADATA",
        raw_asset_ids=[],
        input_manifest_key="manifests/input.json",
        status=status,
        attempt=1,
        max_attempts=2,
        timeout_sec=60,
        started_at=NOW if status in {"RUNNING", "FAILED"} else None,
        finished_at=NOW if status == "FAILED" else None,
        error_code="parse_failed" if status == "FAILED" else None,
        error_summary="解析失败" if status == "FAILED" else None,
        output_refs=[],
        metrics={},
        created_by=user_id,
        updated_by=user_id,
    )


def test_episode_initial_status_and_transition_guard(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        session.add(
            _episode(
                ids.project_a,  # type: ignore[attr-defined]
                ids.user_a,  # type: ignore[attr-defined]
                status="READY",
            )
        )
        with pytest.raises(IntegrityError, match="episode_initial_status_invalid"):
            session.flush()
        session.rollback()

        episode = _episode(ids.project_a, ids.user_a)  # type: ignore[attr-defined]
        session.add(episode)
        session.commit()

        with pytest.raises(IntegrityError, match="episode_state_transition_invalid"):
            session.execute(
                update(EpisodeModel).where(EpisodeModel.id == episode.id).values(status="ACCEPTED")
            )
            session.flush()
        session.rollback()

        with pytest.raises(IntegrityError, match="episode_delete_forbidden"):
            session.execute(delete(EpisodeModel).where(EpisodeModel.id == episode.id))
            session.flush()


def test_episode_manifest_required_when_ready(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        episode = _episode(ids.project_a, ids.user_a)  # type: ignore[attr-defined]
        session.add(episode)
        session.commit()
        session.execute(
            update(EpisodeModel).where(EpisodeModel.id == episode.id).values(status="PROCESSING")
        )
        session.commit()
        with pytest.raises(IntegrityError):
            session.execute(
                update(EpisodeModel).where(EpisodeModel.id == episode.id).values(status="READY")
            )
            session.flush()


def test_job_initial_transition_and_retry_guard(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        session.add(_job(ids.project_a, ids.user_a, status="RUNNING"))  # type: ignore[attr-defined]
        with pytest.raises(IntegrityError, match="ingestion_job_initial_status_invalid"):
            session.flush()
        session.rollback()

        job = _job(ids.project_a, ids.user_a)  # type: ignore[attr-defined]
        session.add(job)
        session.commit()

        with pytest.raises(IntegrityError, match="ingestion_job_state_transition_invalid"):
            session.execute(
                update(IngestionJobModel)
                .where(IngestionJobModel.id == job.id)
                .values(status="SUCCEEDED")
            )
            session.flush()
        session.rollback()

        session.execute(
            update(IngestionJobModel)
            .where(IngestionJobModel.id == job.id)
            .values(status="RUNNING", started_at=NOW)
        )
        session.commit()
        session.execute(
            update(IngestionJobModel)
            .where(IngestionJobModel.id == job.id)
            .values(
                status="FAILED",
                finished_at=NOW,
                error_code="parse_failed",
                error_summary="解析失败",
            )
        )
        session.commit()

        with pytest.raises(IntegrityError, match="ingestion_job_state_transition_invalid"):
            session.execute(
                update(IngestionJobModel)
                .where(IngestionJobModel.id == job.id)
                .values(status="QUEUED", attempt=3)
            )
            session.flush()
        session.rollback()


def test_lineage_node_delete_forbidden(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    node_id = uuid4()
    with factory() as session:
        session.add(
            LineageNodeRefModel(
                project_id=ids.project_a,  # type: ignore[attr-defined]
                node_type="EPISODE",
                node_id=node_id,
                is_active=True,
                updated_at=NOW,
            )
        )
        session.commit()
        with pytest.raises(IntegrityError, match="lineage_node_ref_delete_forbidden"):
            session.execute(
                delete(LineageNodeRefModel).where(LineageNodeRefModel.node_id == node_id)
            )
            session.flush()
