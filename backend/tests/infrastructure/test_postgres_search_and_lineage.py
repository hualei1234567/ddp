"""PostgreSQL 上的检索谓词与 DatasetVersion 血缘集成测试。

SQLite 覆盖不到两件事，必须用真实 PostgreSQL 验证一次：

1. ``jsonb_array_elements_text`` 展开谓词能否真的执行（SQLite 走的是 ``json_each``，
   编译通过不代表 PG 上能跑）；
2. 血缘 ``INCLUDED_IN`` 边的两端复合外键、``CHECK`` 枚举（``DATASET_VERSION``）
   在 PG 的 DDL 里是否已经包含——本地 SQLite 的建表语句由 ORM 生成，与 Alembic
   迁移的 DDL 是两份来源。

未配置测试库时整模块跳过（BLOCKED），不静默通过。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.application.datasets.lineage import DatasetVersionLineageRecorder
from app.domain.episodes.filters import EpisodeListFilters
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.session import create_session_factory
from app.infrastructure.repositories.episodes import SqlEpisodeRepository
from app.infrastructure.repositories.lineage import SqlLineageRepository
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get(
    "DDP_DATABASE_URL",
    "",
)

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)

_NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


@pytest.fixture()
def postgres_factory() -> Iterator[tuple[sessionmaker[Session], dict[str, UUID]]]:
    schema = f"ddp_search_{uuid4().hex}"
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
        factory = create_session_factory(engine)
        project_id, user_id = uuid4(), uuid4()
        with factory() as session:
            session.add_all(
                [
                    Project(id=project_id, code=f"p-{uuid4().hex[:8]}", name="检索项目"),
                    User(id=user_id, display_name="检索用户"),
                ]
            )
            session.commit()
        yield factory, {"project_id": project_id, "user_id": user_id}
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


def _insert_episode(
    factory: sessionmaker[Session],
    project_id: UUID,
    user_id: UUID,
    *,
    index: int,
    scene_labels: list[str],
    device_ids: list[str],
    quality_status: str = "NOT_CHECKED",
    camera: str | None = None,
) -> UUID:
    from app.infrastructure.db.models.episodes import EpisodeModel

    episode_id = uuid4()
    with factory() as session:
        session.add(
            EpisodeModel(
                id=episode_id,
                project_id=project_id,
                episode_lineage_id=uuid4(),
                episode_version=1,
                code=f"EP-PG-{index}",
                status="PENDING",
                quality_status=quality_status,
                annotation_status="NOT_STARTED",
                scene_labels=scene_labels,
                device_ids=device_ids,
                sensor_ids=[],
                start_time_ns=1_000 + index * 10_000,
                end_time_ns=2_000 + index * 10_000,
                duration_ns=1_000,
                time_base={
                    "time_domain": "UTC",
                    "clock_source": "HOST_CLOCK",
                    "sync_method": "NTP",
                    "sync_status": "VERIFIED",
                },
                canonical_schema_version="ddp.canonical/1.0.0-draft",
                pipeline_id="parser",
                pipeline_version="1.0.0",
                segmentation_rule_version="seg-v1",
                frame_count=1,
                calibration_snapshot=[
                    {
                        "calibration_id": str(uuid4()),
                        "calibration_version": 1,
                        "checksum": "sha256:" + "a" * 64,
                        "target_type": "ROBOT",
                        "target_id": str(uuid4()),
                        "schema_version": "ddp.calibration/1.0.0",
                    }
                ],
                preview_refs=[],
                attempt=1,
                max_attempts=3,
                version=1,
                created_at=_NOW,
                updated_at=_NOW,
                created_by=user_id,
                updated_by=user_id,
            )
        )
        if camera is not None:
            from app.infrastructure.db.models.episodes import EpisodeRawAssetModel
            from app.infrastructure.db.models.ingestion import RawAssetModel

            asset_id = uuid4()
            session.add(
                RawAssetModel(
                    id=asset_id,
                    project_id=project_id,
                    code=f"RAW-PG-{index}",
                    source_type="IMPORT",
                    original_filename=f"episode-{index}.mp4",
                    object_key=f"raw/{project_id}/2026/11/16/{asset_id}/content.mp4",
                    size_bytes=1024,
                    content_type="video/mp4",
                    format="MP4",
                    sha256="sha256:" + "a" * 64,
                    status="VALID",
                    raw_metadata={"camera": camera},
                    ingested_at=_NOW,
                    storage_class="STANDARD",
                    uploaded_by=user_id,
                    version=1,
                    created_at=_NOW,
                    updated_at=_NOW,
                    created_by=user_id,
                    updated_by=user_id,
                )
            )
            session.add(
                EpisodeRawAssetModel(
                    project_id=project_id,
                    episode_id=episode_id,
                    asset_id=asset_id,
                    role="PRIMARY",
                    sha256_snapshot="sha256:" + "a" * 64,
                    size_bytes_snapshot=1024,
                )
            )
        session.commit()
    return episode_id


def test_pg_scene_and_device_filters_execute(
    postgres_factory: tuple[sessionmaker[Session], dict[str, UUID]],
) -> None:
    factory, ids = postgres_factory
    project_id = ids["project_id"]
    device = uuid4()
    kitchen = _insert_episode(
        factory,
        project_id,
        ids["user_id"],
        index=0,
        scene_labels=["kitchen", "indoor"],
        device_ids=[str(device)],
    )
    _insert_episode(
        factory,
        project_id,
        ids["user_id"],
        index=1,
        scene_labels=["street"],
        device_ids=[str(uuid4())],
    )

    scope = ProjectScope(project_id, actor_id=ids["user_id"])
    with factory() as session:
        repository = SqlEpisodeRepository(session)
        by_scene = repository.list(
            scope, filters=EpisodeListFilters(scene_labels=("kitchen",)), limit=10
        )
        by_device = repository.list(scope, filters=EpisodeListFilters(device_id=device), limit=10)
        none = repository.list(
            scope, filters=EpisodeListFilters(scene_labels=("warehouse",)), limit=10
        )
    assert [row.episode_id for row in by_scene] == [kitchen]
    assert [row.episode_id for row in by_device] == [kitchen]
    assert none == []


def test_pg_camera_filter_executes(
    postgres_factory: tuple[sessionmaker[Session], dict[str, UUID]],
) -> None:
    """PostgreSQL 上的 JSON 事实抽取是 ``->>``，必须真跑一次而不只编译。"""
    factory, ids = postgres_factory
    project_id = ids["project_id"]
    main = _insert_episode(
        factory,
        project_id,
        ids["user_id"],
        index=10,
        scene_labels=[],
        device_ids=[],
        camera="main",
    )
    wrist = _insert_episode(
        factory,
        project_id,
        ids["user_id"],
        index=11,
        scene_labels=[],
        device_ids=[],
        camera="wrist",
    )
    # 无来源事实的 Episode（例如上传资产）不命中任何机位。
    _insert_episode(
        factory,
        project_id,
        ids["user_id"],
        index=12,
        scene_labels=[],
        device_ids=[],
    )

    scope = ProjectScope(project_id, actor_id=ids["user_id"])
    with factory() as session:
        repository = SqlEpisodeRepository(session)
        by_main = repository.list(scope, filters=EpisodeListFilters(camera="main"), limit=10)
        by_wrist = repository.list(scope, filters=EpisodeListFilters(camera="wrist"), limit=10)
        unknown = repository.list(
            scope, filters=EpisodeListFilters(camera="overhead_ceiling"), limit=10
        )
    assert [row.episode_id for row in by_main] == [main]
    assert [row.episode_id for row in by_wrist] == [wrist]
    assert unknown == []


def test_pg_included_in_edges_are_written_and_idempotent(
    postgres_factory: tuple[sessionmaker[Session], dict[str, UUID]],
) -> None:
    factory, ids = postgres_factory
    project_id = ids["project_id"]
    scope = ProjectScope(project_id, actor_id=ids["user_id"])
    episode_ids = [
        _insert_episode(
            factory,
            project_id,
            ids["user_id"],
            index=index,
            scene_labels=["kitchen"],
            device_ids=[],
        )
        for index in range(3)
    ]
    version_id = uuid4()

    with factory() as session:
        recorder = DatasetVersionLineageRecorder(SqlLineageRepository(session))
        created = recorder.record_inclusion(
            scope,
            dataset_version_id=version_id,
            episode_ids=episode_ids,
            now=_NOW,
            metadata={"dataset_id": str(uuid4())},
        )
        session.commit()
    assert created == 3

    # 重复登记：不新增边。
    with factory() as session:
        recorder = DatasetVersionLineageRecorder(SqlLineageRepository(session))
        again = recorder.record_inclusion(
            scope,
            dataset_version_id=version_id,
            episode_ids=episode_ids,
            now=_NOW,
        )
        session.commit()
    assert again == 0

    from app.infrastructure.db.models.episodes import DataLineageEdgeModel

    with factory() as session:
        edges = list(
            session.scalars(
                select(DataLineageEdgeModel).where(
                    DataLineageEdgeModel.project_id == project_id,
                    DataLineageEdgeModel.relation == "INCLUDED_IN",
                    DataLineageEdgeModel.to_node_id == version_id,
                )
            )
        )
    assert len(edges) == 3
    assert {edge.from_node_id for edge in edges} == set(episode_ids)
    assert all(edge.to_node_type == "DATASET_VERSION" for edge in edges)
