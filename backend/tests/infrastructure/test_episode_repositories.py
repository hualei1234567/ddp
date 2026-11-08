"""Episode/血缘 SQLite 集成测试：项目隔离、READY 冻结、重叠、修订与只追加。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.episodes.commands import EpisodeActionCommand, ReviseEpisodeCommand
from app.domain.episodes.enums import EpisodeStatus, LineageNodeType, LineageRelation
from app.domain.episodes.errors import (
    EpisodeOverlapConflictError,
    LineageConflictError,
    ResourceNotFoundError,
)
from app.domain.episodes.lineage import LineageEdge
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.episodes import (
    DataLineageEdgeModel,
    EpisodeModel,
    EpisodeRawAssetModel,
)
from app.infrastructure.repositories.episodes import SqlEpisodeRepository
from app.infrastructure.repositories.lineage import SqlLineageRepository
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.infrastructure.episode_support import (
    SHA_A,
    build_services,
    create_command,
    make_ready,
)


def _scope(ids: object, project: str = "a") -> ProjectScope:
    if project == "a":
        return ProjectScope(ids.project_a, actor_id=ids.user_a)  # type: ignore[attr-defined]
    return ProjectScope(ids.project_b, actor_id=ids.user_b)  # type: ignore[attr-defined]


def test_create_episode_and_cross_project_isolation(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        episode_service, _, _ = build_services(session)
        episode = episode_service.create(
            _scope(ids, "a"),
            create_command(ids.asset_a, code="EP-A-001"),  # type: ignore[attr-defined]
        )
        assert episode.status is EpisodeStatus.PENDING
        assert episode.episode_version == 1

    with factory() as session:
        repository = SqlEpisodeRepository(session)
        assert repository.get(_scope(ids, "a"), episode.episode_id) is not None
        assert repository.get(_scope(ids, "b"), episode.episode_id) is None
        assert repository.list(_scope(ids, "b")) == []

    with factory() as session:
        episode_service, _, _ = build_services(session)
        with pytest.raises(ResourceNotFoundError):
            episode_service.create(
                _scope(ids, "a"),
                create_command(ids.asset_b, code="EP-A-CROSS"),  # type: ignore[attr-defined]
            )


def test_ready_freezes_data_fields(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        episode_service, _, _ = build_services(session)
        episode = episode_service.create(
            _scope(ids, "a"),
            create_command(ids.asset_a, code="EP-A-READY"),  # type: ignore[attr-defined]
        )
        ready = make_ready(episode_service, _scope(ids, "a"), episode)
        assert ready.status is EpisodeStatus.READY
        assert ready.manifest_sha256 is not None
        checking = episode_service.apply_action(
            _scope(ids, "a"),
            ready.episode_id,
            EpisodeActionCommand(
                action="quality-check",
                expected_version=ready.version,
            ),
        )
        passed = episode_service.apply_action(
            _scope(ids, "a"),
            checking.episode_id,
            EpisodeActionCommand(
                action="record-quality",
                expected_version=checking.version,
                quality_status="PASS",
            ),
        )
        accepted = episode_service.apply_action(
            _scope(ids, "a"),
            passed.episode_id,
            EpisodeActionCommand(action="accept", expected_version=passed.version),
        )
        assert accepted.status is EpisodeStatus.ACCEPTED

    with factory() as session:
        with pytest.raises(IntegrityError, match="episode_data_immutable"):
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode.episode_id)
                .values(start_time_ns=1)
            )
            session.flush()
        session.rollback()

    with factory() as session:
        with pytest.raises(IntegrityError, match="episode_quality_immutable"):
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode.episode_id)
                .values(quality_status="FAIL")
            )
            session.flush()
        session.rollback()

    with factory() as session:
        with pytest.raises(IntegrityError, match="episode_delete_forbidden"):
            session.execute(delete(EpisodeModel).where(EpisodeModel.id == episode.episode_id))
            session.flush()


def test_overlap_rejected_unless_allowed(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        episode_service, _, _ = build_services(session)
        episode_service.create(
            _scope(ids, "a"),
            create_command(ids.asset_a, code="EP-A-OVERLAP-1"),  # type: ignore[attr-defined]
        )
        with pytest.raises(EpisodeOverlapConflictError):
            episode_service.create(
                _scope(ids, "a"),
                create_command(ids.asset_a, code="EP-A-OVERLAP-2"),  # type: ignore[attr-defined]
            )
        allowed = episode_service.create(
            _scope(ids, "a"),
            create_command(
                ids.asset_a,  # type: ignore[attr-defined]
                code="EP-A-OVERLAP-3",
                allow_overlap=True,
                overlap_reason="DE/QI 审批允许重叠",
            ),
        )
        assert allowed.status is EpisodeStatus.PENDING


def test_revision_creates_new_version_without_touching_old(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        episode_service, _, _ = build_services(session)
        episode = episode_service.create(
            _scope(ids, "a"),
            create_command(ids.asset_a, code="EP-A-REV-1"),  # type: ignore[attr-defined]
        )
        ready = make_ready(episode_service, _scope(ids, "a"), episode)
        old_hash = ready.manifest_sha256
        revised = episode_service.revise(
            _scope(ids, "a"),
            ready.episode_id,
            ReviseEpisodeCommand(code="EP-A-REV-2"),
        )
        assert revised.episode_version == 2
        assert revised.episode_lineage_id == ready.episode_lineage_id
        assert revised.supersedes_id == ready.episode_id
        assert revised.status is EpisodeStatus.PENDING

    with factory() as session:
        repository = SqlEpisodeRepository(session)
        old = repository.get(_scope(ids, "a"), ready.episode_id)
        new = repository.get(_scope(ids, "a"), revised.episode_id)
        assert old is not None and new is not None
        assert old.status is EpisodeStatus.READY
        assert old.manifest_sha256 == old_hash
        assert new.episode_version == 2
        lineage = SqlLineageRepository(session)
        supersedes = lineage.list_supersedes_edges(_scope(ids, "a"))
        assert any(
            edge.from_node_id == revised.episode_id and edge.to_node_id == ready.episode_id
            for edge in supersedes
        )


def test_lineage_append_only_and_cross_project_rejected(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        episode_service, _, lineage_service = build_services(session)
        episode = episode_service.create(
            _scope(ids, "a"),
            create_command(ids.asset_a, code="EP-A-LINEAGE"),  # type: ignore[attr-defined]
        )
        edges = lineage_service.traverse(
            _scope(ids, "a"),
            node_type=LineageNodeType.RAW_ASSET,
            node_id=ids.asset_a,  # type: ignore[attr-defined]
            direction="forward",
            depth=2,
        )
        assert any(edge.relation is LineageRelation.SEGMENTED_INTO for edge in edges)

        duplicate = LineageEdge(
            edge_id=uuid4(),
            project_id=ids.project_a,  # type: ignore[attr-defined]
            from_node_type=LineageNodeType.RAW_ASSET,
            from_node_id=ids.asset_a,  # type: ignore[attr-defined]
            to_node_type=LineageNodeType.EPISODE,
            to_node_id=episode.episode_id,
            relation=LineageRelation.SEGMENTED_INTO,
            metadata={},
            created_by=ids.user_a,  # type: ignore[attr-defined]
            created_at=datetime.now(UTC),
        )
        with pytest.raises(LineageConflictError):
            SqlLineageRepository(session).add_edge(_scope(ids, "a"), duplicate)

    with factory() as session:
        row = session.scalar(select(DataLineageEdgeModel))
        assert row is not None
        with pytest.raises(IntegrityError, match="lineage_edge_append_only"):
            session.execute(
                update(DataLineageEdgeModel)
                .where(DataLineageEdgeModel.id == row.id)
                .values(relation="INVALIDATES")
            )
            session.flush()
        session.rollback()

    with factory() as session:
        row = session.scalar(select(DataLineageEdgeModel))
        assert row is not None
        with pytest.raises(IntegrityError, match="lineage_edge_append_only"):
            session.execute(delete(DataLineageEdgeModel).where(DataLineageEdgeModel.id == row.id))
            session.flush()
        session.rollback()

    with factory() as session:
        lineage = SqlLineageRepository(session)
        scope_b = _scope(ids, "b")
        node_b = uuid4()
        lineage.ensure_node(
            scope_b,
            node_type=LineageNodeType.EPISODE,
            node_id=node_b,
            now=datetime.now(UTC),
        )
        session.commit()
        assert (
            lineage.get_node(
                _scope(ids, "a"),
                node_type=LineageNodeType.EPISODE,
                node_id=node_b,
            )
            is None
        )
        with pytest.raises(IntegrityError):
            session.add(
                DataLineageEdgeModel(
                    id=uuid4(),
                    project_id=ids.project_a,  # type: ignore[attr-defined]
                    from_node_type="EPISODE",
                    from_node_id=episode.episode_id,
                    to_node_type="EPISODE",
                    to_node_id=node_b,
                    relation="INVALIDATES",
                    edge_metadata={},
                    created_by=ids.user_a,  # type: ignore[attr-defined]
                    created_at=datetime.now(UTC),
                )
            )
            session.flush()
        session.rollback()


def test_episode_raw_assets_are_append_only_and_frozen_after_ready(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        episode_service, _, _ = build_services(session)
        episode = episode_service.create(
            _scope(ids, "a"),
            create_command(ids.asset_a, code="EP-A-ASSETS"),  # type: ignore[attr-defined]
        )
        ready = make_ready(episode_service, _scope(ids, "a"), episode)
        assert ready.status is EpisodeStatus.READY

    with factory() as session:
        rows = session.scalars(select(EpisodeRawAssetModel)).all()
        assert len(rows) == 1
        row = rows[0]
        with pytest.raises(IntegrityError, match="episode_raw_asset_immutable"):
            session.execute(
                update(EpisodeRawAssetModel)
                .where(
                    EpisodeRawAssetModel.project_id == row.project_id,
                    EpisodeRawAssetModel.episode_id == row.episode_id,
                    EpisodeRawAssetModel.asset_id == row.asset_id,
                    EpisodeRawAssetModel.role == row.role,
                )
                .values(size_bytes_snapshot=1)
            )
            session.flush()
        session.rollback()

    with factory() as session:
        with pytest.raises(IntegrityError, match="episode_raw_asset_parent_frozen"):
            session.add(
                EpisodeRawAssetModel(
                    project_id=ids.project_a,  # type: ignore[attr-defined]
                    episode_id=ready.episode_id,
                    asset_id=ids.asset_a,  # type: ignore[attr-defined]
                    role="EXTRA",
                    start_time_ns=1_000,
                    end_time_ns=2_000,
                    sha256_snapshot=SHA_A,
                    size_bytes_snapshot=1024,
                )
            )
            session.flush()
        session.rollback()

    with factory() as session:
        with pytest.raises(IntegrityError, match="episode_raw_asset_delete_forbidden"):
            session.execute(delete(EpisodeRawAssetModel))
            session.flush()
