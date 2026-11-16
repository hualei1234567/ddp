"""用量快照 Worker 测试：真实 SQLite + 真实聚合，验证快照随数据变化。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.governance import UsageSnapshotModel
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.workers.usage_snapshots import UsageSnapshotWorker
from sqlalchemy import func, select

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def test_worker_refresh_writes_all_kinds_and_tracks_real_counts() -> None:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    project_id, user_id = uuid4(), uuid4()

    with factory() as session:
        session.add(Project(id=project_id, code="p-a", name="项目 A"))
        session.add(User(id=user_id, display_name="用户"))
        session.commit()
        session.add(
            EpisodeModel(
                id=uuid4(),
                project_id=project_id,
                episode_lineage_id=uuid4(),
                code="ep-1",
                status="PENDING",
                start_time_ns=0,
                end_time_ns=100,
                duration_ns=100,
                canonical_schema_version="v1",
                pipeline_id="pipeline",
                pipeline_version="1",
                segmentation_rule_version="seg.v1",
                created_by=user_id,
                updated_by=user_id,
            )
        )
        session.commit()

    worker = UsageSnapshotWorker(factory)
    rows = worker.refresh_once(now=NOW)
    assert rows == 5  # 一个项目 × 五种资源

    with factory() as session:
        snapshots = {
            row.resource_kind: row
            for row in session.scalars(
                select(UsageSnapshotModel).where(UsageSnapshotModel.project_id == project_id)
            )
        }
        assert snapshots["EPISODES"].used_value == 1
        assert snapshots["EPISODES"].measured_at is not None
        assert snapshots["OBJECT_STORAGE_BYTES"].used_value == 0

        # 再插入一个 Episode，刷新后快照反映最新计数。
        session.add(
            EpisodeModel(
                id=uuid4(),
                project_id=project_id,
                episode_lineage_id=uuid4(),
                code="ep-2",
                status="PENDING",
                start_time_ns=0,
                end_time_ns=100,
                duration_ns=100,
                canonical_schema_version="v1",
                pipeline_id="pipeline",
                pipeline_version="1",
                segmentation_rule_version="seg.v1",
                created_by=user_id,
                updated_by=user_id,
            )
        )
        session.commit()

    worker.refresh_once(now=NOW)
    with factory() as session:
        count = session.scalar(
            select(UsageSnapshotModel.used_value).where(
                UsageSnapshotModel.project_id == project_id,
                UsageSnapshotModel.resource_kind == "EPISODES",
            )
        )
        assert count == 2
        total = session.scalar(select(func.count()).select_from(UsageSnapshotModel))
        assert total == 5

    engine.dispose()
