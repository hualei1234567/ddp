"""PostgreSQL Episode/Job/血缘触发器集成测试。

未配置 ``DDP_TEST_POSTGRES_URL`` / ``DDP_DATABASE_URL`` 时跳过并标注 BLOCKED；
CI 会在真实 PostgreSQL 上执行。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.episodes import (
    DataLineageEdgeModel,
    EpisodeModel,
    LineageNodeRefModel,
)
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.models.jobs import IngestionJobModel
from app.infrastructure.db.session import create_session_factory
from sqlalchemy import create_engine, delete, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get(
    "DDP_DATABASE_URL",
    "",
)
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SHA = f"sha256:{'a' * 64}"


@pytest.fixture()
def postgres_engine() -> Iterator[Engine]:
    schema = f"ddp_episode_it_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    engine: Engine | None = None
    try:
        with admin.connect() as connection:
            connection.execute(CreateSchema(schema))
        engine = create_engine(
            _POSTGRES_URL,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        Base.metadata.create_all(engine)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


def _project_user(engine: Engine) -> tuple[UUID, UUID]:
    factory = create_session_factory(engine)
    project_id, user_id = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_id, code=f"p-{project_id.hex[:8]}", name="项目"),
                User(id=user_id, display_name="用户"),
            ]
        )
        session.commit()
    return project_id, user_id


def _episode(project_id: UUID, user_id: UUID) -> EpisodeModel:
    return EpisodeModel(
        id=uuid4(),
        project_id=project_id,
        episode_lineage_id=uuid4(),
        episode_version=1,
        code=f"EP-{uuid4().hex[:12]}",
        status="PENDING",
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
        attempt=1,
        max_attempts=3,
        created_by=user_id,
        updated_by=user_id,
    )


def test_postgres_episode_ready_freeze(postgres_engine: Engine) -> None:
    project_id, user_id = _project_user(postgres_engine)
    factory = create_session_factory(postgres_engine)
    episode = _episode(project_id, user_id)
    with factory() as session:
        session.add(episode)
        session.commit()
        session.execute(
            update(EpisodeModel).where(EpisodeModel.id == episode.id).values(status="PROCESSING")
        )
        session.commit()
        session.execute(
            update(EpisodeModel)
            .where(EpisodeModel.id == episode.id)
            .values(
                status="READY",
                manifest_key=f"canonical/{project_id}/{episode.id}/1/manifest.json",
                manifest_sha256=SHA,
            )
        )
        session.commit()

        with pytest.raises(IntegrityError, match="episode_data_immutable"):
            session.execute(
                update(EpisodeModel).where(EpisodeModel.id == episode.id).values(start_time_ns=1)
            )
            session.flush()
        session.rollback()

        with pytest.raises(IntegrityError, match="episode_delete_forbidden"):
            session.execute(delete(EpisodeModel).where(EpisodeModel.id == episode.id))
            session.flush()


def test_postgres_lineage_cross_project_and_append_only(postgres_engine: Engine) -> None:
    project_a, user_a = _project_user(postgres_engine)
    project_b, _user_b = _project_user(postgres_engine)
    factory = create_session_factory(postgres_engine)
    node_a, node_b = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                LineageNodeRefModel(
                    project_id=project_a,
                    node_type="EPISODE",
                    node_id=node_a,
                    is_active=True,
                    updated_at=NOW,
                ),
                LineageNodeRefModel(
                    project_id=project_b,
                    node_type="EPISODE",
                    node_id=node_b,
                    is_active=True,
                    updated_at=NOW,
                ),
            ]
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.add(
                DataLineageEdgeModel(
                    id=uuid4(),
                    project_id=project_a,
                    from_node_type="EPISODE",
                    from_node_id=node_a,
                    to_node_type="EPISODE",
                    to_node_id=node_b,
                    relation="INVALIDATES",
                    edge_metadata={},
                    created_by=user_a,
                    created_at=NOW,
                )
            )
            session.flush()
        session.rollback()

        session.add(
            DataLineageEdgeModel(
                id=uuid4(),
                project_id=project_a,
                from_node_type="EPISODE",
                from_node_id=node_a,
                to_node_type="EPISODE",
                to_node_id=uuid4(),
                relation="INVALIDATES",
                edge_metadata={},
                created_by=user_a,
                created_at=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

        local_target = uuid4()
        session.add(
            LineageNodeRefModel(
                project_id=project_a,
                node_type="EPISODE",
                node_id=local_target,
                is_active=True,
                updated_at=NOW,
            )
        )
        session.flush()
        edge = DataLineageEdgeModel(
            id=uuid4(),
            project_id=project_a,
            from_node_type="EPISODE",
            from_node_id=node_a,
            to_node_type="EPISODE",
            to_node_id=local_target,
            relation="INVALIDATES",
            edge_metadata={},
            created_by=user_a,
            created_at=NOW,
        )
        session.add(edge)
        session.commit()

        with pytest.raises(IntegrityError, match="lineage_edge_append_only"):
            session.execute(
                update(DataLineageEdgeModel)
                .where(DataLineageEdgeModel.id == edge.id)
                .values(relation="SUPERSEDES")
            )
            session.flush()


def test_postgres_job_retry_limit(postgres_engine: Engine) -> None:
    project_id, user_id = _project_user(postgres_engine)
    factory = create_session_factory(postgres_engine)
    job = IngestionJobModel(
        id=uuid4(),
        project_id=project_id,
        job_type="PARSE_METADATA",
        raw_asset_ids=[],
        input_manifest_key="manifests/input.json",
        status="QUEUED",
        attempt=1,
        max_attempts=2,
        timeout_sec=60,
        output_refs=[],
        metrics={},
        created_by=user_id,
        updated_by=user_id,
    )
    with factory() as session:
        session.add(job)
        session.commit()
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
        with pytest.raises(IntegrityError, match="ingestion_job_retry_not_allowed"):
            session.execute(
                update(IngestionJobModel)
                .where(IngestionJobModel.id == job.id)
                .values(status="QUEUED", attempt=3)
            )
            session.flush()
