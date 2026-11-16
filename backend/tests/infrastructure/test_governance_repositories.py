"""治理仓储集成测试：真实 SQLite + 真实表 + 真实聚合 SQL。

覆盖三件事：

1. **权威来源是真的**：用量来自业务表计数 / ``raw_assets.size_bytes`` 求和，
   排除已丢弃状态，且项目之间严格隔离；
2. **快照仓库整体替换**：不会出现"一半新一半旧"；
3. **配额仓库**：唯一约束、跨项目不可见、乐观锁区分 404 与 409。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.governance import (
    DuplicateResourceError,
    ResourceKind,
    ResourceQuota,
    ResourceUsage,
    UsageSnapshot,
    VersionConflictError,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.datasets import DatasetModel, DatasetVersionModel
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.governance import UsageSnapshotModel
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.db.models.jobs import IngestionJobModel
from app.infrastructure.db.models.modelops import (
    InferenceTaskRow,
    ModelRow,
    ModelVersionRow,
)
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.governance import (
    SqlResourceQuotaRepository,
    SqlUsageReader,
    SqlUsageSnapshotRepository,
)
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64


@pytest.fixture()
def factory() -> sessionmaker[Session]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    return create_session_factory(engine)


def _seed_project(session: Session, project_id: UUID, user_id: UUID, code: str) -> None:
    session.add(Project(id=project_id, code=code, name=f"项目 {code}"))
    session.add(User(id=user_id, display_name=f"用户 {code}"))


def _episode(session: Session, project_id: UUID, user_id: UUID, code: str) -> None:
    session.add(
        EpisodeModel(
            id=uuid4(),
            project_id=project_id,
            episode_lineage_id=uuid4(),
            code=code,
            status="PENDING",
            start_time_ns=0,
            end_time_ns=1_000_000,
            duration_ns=1_000_000,
            canonical_schema_version="canonical.v1",
            pipeline_id="pipeline",
            pipeline_version="1",
            segmentation_rule_version="seg.v1",
            created_by=user_id,
            updated_by=user_id,
        )
    )


def _ingestion_job(session: Session, project_id: UUID, user_id: UUID) -> None:
    session.add(
        IngestionJobModel(
            id=uuid4(),
            project_id=project_id,
            job_type="VALIDATE",
            input_manifest_key="manifests/input.json",
            status="QUEUED",
            timeout_sec=60,
            created_by=user_id,
            updated_by=user_id,
        )
    )


def _dataset_version(session: Session, project_id: UUID, user_id: UUID, code: str) -> None:
    dataset_id = uuid4()
    lineage_id = uuid4()
    session.add(
        DatasetModel(
            id=dataset_id,
            project_id=project_id,
            dataset_lineage_id=lineage_id,
            code=code,
            name=f"数据集 {code}",
            purpose="TRAINING",
            owner_id=user_id,
            status="DRAFT",
            usage_policy="TRAINING_ALLOWED",
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
    session.add(
        DatasetVersionModel(
            id=uuid4(),
            project_id=project_id,
            dataset_id=dataset_id,
            dataset_lineage_id=lineage_id,
            dataset_version=1,
            status="DRAFT",
            validity_status="VALID",
            selection_rule_version="selection.v1",
            selection_filters={},
            selection_snapshot_at=NOW,
            dedup_policy_id=uuid4(),
            dedup_policy_version=1,
            dedup_policy_sha256=SHA,
            split_policy_id=uuid4(),
            split_policy_version=1,
            split_policy_sha256=SHA,
            leakage_policy_id=uuid4(),
            leakage_policy_version=1,
            leakage_policy_sha256=SHA,
            quality_policy_id=uuid4(),
            quality_policy_version=1,
            quality_policy_sha256=SHA,
            created_by=user_id,
            updated_by=user_id,
        )
    )


def _raw_asset(
    session: Session,
    project_id: UUID,
    user_id: UUID,
    code: str,
    *,
    size_bytes: int,
    status: str,
) -> None:
    asset_id = uuid4()
    session.add(
        RawAssetModel(
            id=asset_id,
            project_id=project_id,
            code=code,
            source_type="UPLOAD",
            original_filename=f"{code}.mcap",
            object_key=f"objects/{code}.mcap",
            size_bytes=size_bytes,
            content_type="application/x-mcap",
            format="MCAP",
            sha256=f"sha256:{code:0>64}"[:71],
            # 守卫要求初始状态必须是 VALIDATING，目标状态通过合法迁移到达。
            status="VALIDATING",
            ingested_at=NOW,
            storage_class="STANDARD",
            uploaded_by=user_id,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
    if status != "VALIDATING":
        session.execute(
            update(RawAssetModel).where(RawAssetModel.id == asset_id).values(status=status)
        )


def _inference_task(session: Session, project_id: UUID, user_id: UUID, code: str) -> None:
    model_id = uuid4()
    session.add(
        ModelRow(
            id=model_id,
            project_id=project_id,
            code=f"model-{code}",
            name=f"模型 {code}",
            task_type="IMITATION",
            framework="lerobot",
            owner_id=user_id,
            status="DRAFT",
            created_at=NOW,
            updated_at=NOW,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
    model_version_id = uuid4()
    session.add(
        ModelVersionRow(
            id=model_version_id,
            project_id=project_id,
            model_id=model_id,
            version_no=1,
            status="DRAFT",
            artifact={},
            runtime={},
            artifact_sha256=SHA,
            created_at=NOW,
            updated_at=NOW,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
    session.add(
        InferenceTaskRow(
            id=uuid4(),
            project_id=project_id,
            model_version_id=model_version_id,
            status="QUEUED",
            created_at=NOW,
            updated_at=NOW,
            created_by=user_id,
        )
    )


# --- 用量读取 --------------------------------------------------------------
def test_measure_all_counts_real_tables_and_isolates_projects(
    factory: sessionmaker[Session],
) -> None:
    project_a, project_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    with factory() as session:
        _seed_project(session, project_a, user_a, "p-a")
        _seed_project(session, project_b, user_b, "p-b")
        session.commit()

        _episode(session, project_a, user_a, "ep-a1")
        _episode(session, project_a, user_a, "ep-a2")
        _episode(session, project_b, user_b, "ep-b1")

        _ingestion_job(session, project_a, user_a)
        _dataset_version(session, project_a, user_a, "ds-a")
        _inference_task(session, project_a, user_a, "a")

        # 100 计入；TOMBSTONED 的 999 必须被排除。
        _raw_asset(session, project_a, user_a, "asset-a1", size_bytes=100, status="VALID")
        _raw_asset(
            session,
            project_a,
            user_a,
            "asset-a2",
            size_bytes=999,
            status="TOMBSTONED",
        )
        _raw_asset(session, project_b, user_b, "asset-b1", size_bytes=5, status="VALID")
        session.commit()

        measured = SqlUsageReader(session).measure_all(at=NOW)

        assert measured[project_a][ResourceKind.EPISODES].value == 2
        assert measured[project_a][ResourceKind.DATASET_VERSIONS].value == 1
        assert measured[project_a][ResourceKind.INGESTION_JOBS].value == 1
        assert measured[project_a][ResourceKind.INFERENCE_TASKS].value == 1
        assert measured[project_a][ResourceKind.OBJECT_STORAGE_BYTES].value == 100

        assert measured[project_b][ResourceKind.EPISODES].value == 1
        # 项目 B 没有数据集/任务：真实 0，不是缺失。
        assert measured[project_b][ResourceKind.DATASET_VERSIONS].value == 0
        assert measured[project_b][ResourceKind.OBJECT_STORAGE_BYTES].value == 5
        # 每项都带来源与截止时间。
        for usage in measured[project_a].values():
            assert usage.source
            assert usage.measured_at == NOW


def test_measure_single_project_does_not_see_other_project(
    factory: sessionmaker[Session],
) -> None:
    project_a, project_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    with factory() as session:
        _seed_project(session, project_a, user_a, "p-a")
        _seed_project(session, project_b, user_b, "p-b")
        session.commit()
        _episode(session, project_a, user_a, "ep-a1")
        _episode(session, project_b, user_b, "ep-b1")
        _episode(session, project_b, user_b, "ep-b2")
        session.commit()

        scope = ProjectScope(project_id=project_a, actor_id=user_a)
        usage = SqlUsageReader(session).measure(scope, ResourceKind.EPISODES, at=NOW)

        assert usage.value == 1
        assert usage.source == "episodes.count"


# --- 快照仓库 --------------------------------------------------------------
def test_snapshot_repository_replaces_all_and_reads_scoped(
    factory: sessionmaker[Session],
) -> None:
    project_a, project_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    with factory() as session:
        _seed_project(session, project_a, user_a, "p-a")
        _seed_project(session, project_b, user_b, "p-b")
        session.commit()

        repo = SqlUsageSnapshotRepository(session)
        repo.replace_all(
            [
                UsageSnapshot(
                    project_id=project_a,
                    usage=ResourceUsage(
                        resource_kind=ResourceKind.EPISODES,
                        value=2,
                        measured_at=NOW,
                        source="episodes.count",
                    ),
                ),
                UsageSnapshot(
                    project_id=project_b,
                    usage=ResourceUsage(
                        resource_kind=ResourceKind.EPISODES,
                        value=0,
                        measured_at=NOW,
                        source="episodes.count",
                    ),
                ),
            ]
        )
        session.commit()

        scope_a = ProjectScope(project_id=project_a, actor_id=user_a)
        scope_b = ProjectScope(project_id=project_b, actor_id=user_b)
        assert repo.latest(scope_a)[ResourceKind.EPISODES].value == 2
        # 真实 0 会被读回，而不是被当成"没有快照"。
        assert repo.latest(scope_b)[ResourceKind.EPISODES].value == 0

        # 整体替换：旧值消失，新值生效。
        repo.replace_all(
            [
                UsageSnapshot(
                    project_id=project_a,
                    usage=ResourceUsage(
                        resource_kind=ResourceKind.EPISODES,
                        value=5,
                        measured_at=NOW,
                        source="episodes.count",
                    ),
                )
            ]
        )
        session.commit()

        assert repo.latest(scope_a)[ResourceKind.EPISODES].value == 5
        assert ResourceKind.EPISODES not in repo.latest(scope_b)


# --- 配额仓库 --------------------------------------------------------------
def test_quota_repository_isolates_projects_and_enforces_optimistic_lock(
    factory: sessionmaker[Session],
) -> None:
    project_a, project_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    with factory() as session:
        _seed_project(session, project_a, user_a, "p-a")
        _seed_project(session, project_b, user_b, "p-b")
        session.commit()

        scope_a = ProjectScope(project_id=project_a, actor_id=user_a)
        scope_b = ProjectScope(project_id=project_b, actor_id=user_b)
        repo = SqlResourceQuotaRepository(session)
        quota = repo.create(
            scope_a,
            ResourceQuota(
                quota_id=uuid4(),
                project_id=project_a,
                resource_kind=ResourceKind.EPISODES,
                limit_value=10,
                created_at=NOW,
                updated_at=NOW,
            ),
        )
        session.commit()

        assert repo.get(scope_a, ResourceKind.EPISODES) is not None
        assert repo.get(scope_b, ResourceKind.EPISODES) is None

        # 同项目同类型重复创建被唯一约束拦下。
        with pytest.raises(DuplicateResourceError):
            repo.create(
                scope_a,
                ResourceQuota(
                    quota_id=uuid4(),
                    project_id=project_a,
                    resource_kind=ResourceKind.EPISODES,
                    limit_value=20,
                ),
            )
        session.rollback()

        with pytest.raises(VersionConflictError):
            repo.save(
                scope_a,
                quota.with_limit(20, now=NOW, actor_id=user_a),
                expected_version=99,
            )

        saved = repo.save(
            scope_a,
            quota.with_limit(20, now=NOW, actor_id=user_a),
            expected_version=quota.version,
        )
        session.commit()
        assert saved.version == quota.version + 1
        assert repo.get(scope_a, ResourceKind.EPISODES).limit_value == 20


def test_usage_snapshot_model_is_registered_in_metadata() -> None:
    """新增表必须进 metadata，否则 ``create_all`` 与迁移会分叉。"""
    assert "resource_quotas" in Base.metadata.tables
    assert "usage_snapshots" in Base.metadata.tables
    assert UsageSnapshotModel.__table__.name == "usage_snapshots"
