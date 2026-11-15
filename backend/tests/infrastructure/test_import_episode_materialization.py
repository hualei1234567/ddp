"""导入产物 → Episode 物化：真实仓储、真实 EpisodeService，不 mock。

这是 N1 主链的最后一段：

    HF 导入 -> RawAsset -> [本模块] -> Episode

此前这一段完全不存在，``episodes`` 表恒为 0 行，标注/质检/数据集/训练
全部无米下锅。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.application.datasources.episode_materialization import (
    ImportEpisodeMaterializer,
)
from app.domain.datasources.media import MediaAsset
from app.domain.datasources.models import DataSource, DataSourceImport
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import RawAssetFormat, RawAssetSourceType, RawAssetStatus
from app.domain.ingestion.models import RawAsset
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.datasources import (
    SqlDataSourceImportRepository,
    SqlDataSourceRepository,
    SqlMediaAssetRepository,
)
from app.infrastructure.repositories.episodes import SqlEpisodeRepository
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.infrastructure.episode_support import build_services

NOW = datetime(2026, 11, 16, tzinfo=UTC)
_SHA = "a" * 40


def _raw_asset(scope: ProjectScope, index: int) -> RawAsset:
    asset_id = uuid4()
    return RawAsset(
        asset_id=asset_id,
        project_id=scope.project_id,
        code=f"RAW-{asset_id.hex[:20]}",
        source_type=RawAssetSourceType.IMPORT,
        original_filename=f"demo_{index}_main.mp4",
        object_key=f"raw/{scope.project_id}/2026/11/16/{asset_id}/content.mp4",
        size_bytes=1024 + index,
        content_type="video/mp4",
        format=RawAssetFormat.MP4,
        sha256="sha256:" + f"{index:064d}",
        # DB 触发器强制新建资产必须 VALIDATING 起始；随后走 mark_valid 转 VALID，
        # 与真实导入链路一致（import_runner 就是这两步）。
        status=RawAssetStatus.VALIDATING,
        uploaded_by=scope.actor_id,
        ingested_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        created_by=scope.actor_id,
        updated_by=scope.actor_id,
    )


@pytest.fixture()
def env(tmp_path: Path) -> tuple[sessionmaker[Session], ProjectScope, UUID, list[RawAsset]]:
    engine = create_db_engine(
        f"sqlite+pysqlite:///{tmp_path / 'materialize.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    project_id, actor_id = uuid4(), uuid4()
    with factory() as session:
        session.add(Project(id=project_id, code=f"p-{project_id.hex[:6]}", name="物化项目"))
        session.add(User(id=actor_id, display_name="提交人"))
        session.flush()
        session.add(ProjectMember(project_id=project_id, user_id=actor_id, role="editor"))
        session.commit()
    scope = ProjectScope(project_id, actor_id=actor_id)

    # 两个 demo，第一个两机位、第二个单机位。
    with factory() as session:
        source = SqlDataSourceRepository(session).add(
            scope,
            DataSource(
                data_source_id=uuid4(),
                project_id=project_id,
                code=f"hf-{project_id.hex[:6]}",
                name="示例源",
                kind="HUGGINGFACE",
                repo_id="lerobot/aloha_sim_insertion_human",
                revision=_SHA,
                license="Apache-2.0",
                access_condition="PUBLIC",
                is_gated=False,
            ),
        )
        job = SqlDataSourceImportRepository(session).add(
            scope,
            DataSourceImport(
                import_id=uuid4(),
                project_id=project_id,
                data_source_id=source.data_source_id,
                status="SUCCEEDED",
                scope="SAMPLE",
                tool_name="huggingface_hub",
                tool_version="0.27.1",
                sample_limit=10,
                finished_at=NOW,
            ),
        )
        raw_repo = SqlRawAssetRepository(session)
        media_repo = SqlMediaAssetRepository(session)
        raw_assets = [_raw_asset(scope, i) for i in range(3)]
        for asset in raw_assets:
            created = raw_repo.add(scope, asset)
            raw_repo.save(
                scope, created.mark_valid(now=NOW), expected_version=created.version
            )
        specs = (
            ("demo_001", "main", 0, 0.0, 12.0),
            ("demo_001", "wrist", 1, 0.5, 11.0),
            ("demo_002", "main", 2, 100.0, 5.0),
        )
        for episode_key, camera, index, start, seconds in specs:
            media_repo.add(
                scope,
                MediaAsset(
                    asset_id=uuid4(),
                    project_id=project_id,
                    data_source_id=source.data_source_id,
                    import_id=job.import_id,
                    episode_key=episode_key,
                    path=f"videos/{episode_key}_{camera}.mp4",
                    camera=camera,
                    object_key=raw_assets[index].object_key,
                    size_bytes=raw_assets[index].size_bytes,
                    sha256=raw_assets[index].sha256,
                    content_type="video/mp4",
                    task_name="Sew lace fabric",
                    raw_asset_id=raw_assets[index].asset_id,
                    excerpt_start_seconds=start,
                    excerpt_seconds=seconds,
                    created_at=NOW,
                ),
            )
        session.commit()
        import_id = job.import_id

    try:
        yield factory, scope, import_id, raw_assets
    finally:
        engine.dispose()


def _materializer(session: Session) -> ImportEpisodeMaterializer:
    episode_service, _, _ = build_services(session)
    return ImportEpisodeMaterializer(
        imports=SqlDataSourceImportRepository(session),
        assets=SqlMediaAssetRepository(session),
        episodes=episode_service,
        episode_repository=SqlEpisodeRepository(session),
    )


def test_one_episode_per_media_group(env) -> None:
    """按 episode_key 分组：两个 demo -> 两条 Episode，而不是三个机位三条。"""
    factory, scope, import_id, _ = env
    with factory() as session:
        result = _materializer(session).materialize(scope, import_id)

    assert result.episode_count == 2
    keys = {item.episode_key for item in result.created}
    assert keys == {"demo_001", "demo_002"}
    assert result.skipped_episode_keys == ()


def test_episode_references_all_cameras_of_the_group(env) -> None:
    """demo_001 的两个机位都必须挂到同一条 Episode 上（不允许半条）。"""
    factory, scope, import_id, raw_assets = env
    with factory() as session:
        result = _materializer(session).materialize(scope, import_id)

    by_key = {item.episode_key: item for item in result.created}
    assert set(by_key["demo_001"].raw_asset_ids) == {
        raw_assets[0].asset_id,
        raw_assets[1].asset_id,
    }
    assert by_key["demo_002"].raw_asset_ids == (raw_assets[2].asset_id,)


def test_materialize_is_idempotent(env) -> None:
    """重复物化不造第二份 Episode——确定性 code 撞车时计为跳过。"""
    factory, scope, import_id, _ = env
    with factory() as session:
        first = _materializer(session).materialize(scope, import_id)
    assert first.episode_count == 2

    with factory() as session:
        second = _materializer(session).materialize(scope, import_id)
    assert second.episode_count == 0
    assert set(second.skipped_episode_keys) == {"demo_001", "demo_002"}


def test_created_episodes_are_queryable(env) -> None:
    """落库可查，且时间范围取组内机位并集。"""
    from app.infrastructure.db.models.episodes import EpisodeModel

    factory, scope, import_id, _ = env
    with factory() as session:
        _materializer(session).materialize(scope, import_id)

    with factory() as session:
        rows = list(
            session.scalars(
                select(EpisodeModel).where(EpisodeModel.project_id == scope.project_id)
            )
        )
    assert len(rows) == 2
    demo_001 = next(row for row in rows if row.code == "EP-demo_001")
    # 机位起点 0.0s 与 0.5s、时长 12s 与 11s -> 并集 [0s, 12s]
    assert demo_001.start_time_ns == 0
    assert demo_001.end_time_ns == 12 * 1_000_000_000


def test_empty_import_fails_loudly(env) -> None:
    """没有任何媒体资产时必须显式失败，不能静默产出 0 条。"""
    from app.domain.datasources.errors import DomainValidationError

    factory, scope, _, _ = env
    with factory() as session:
        source = SqlDataSourceRepository(session).add(
            scope,
            DataSource(
                data_source_id=uuid4(),
                project_id=scope.project_id,
                code=f"hf-empty-{uuid4().hex[:6]}",
                name="空源",
                kind="HUGGINGFACE",
                repo_id="lerobot/aloha_sim_insertion_human",
                revision=_SHA,
                license="Apache-2.0",
                access_condition="PUBLIC",
                is_gated=False,
            ),
        )
        job = SqlDataSourceImportRepository(session).add(
            scope,
            DataSourceImport(
                import_id=uuid4(),
                project_id=scope.project_id,
                data_source_id=source.data_source_id,
                status="SUCCEEDED",
                scope="SAMPLE",
                tool_name="huggingface_hub",
                tool_version="0.27.1",
                sample_limit=10,
            ),
        )
        session.commit()
        empty_id = job.import_id

    with factory() as session, pytest.raises(DomainValidationError):
        _materializer(session).materialize(scope, empty_id)
