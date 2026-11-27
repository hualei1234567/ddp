"""数据集版本对比测试（阶段 5）。

用真实 SQLite：造两个版本 + 条目，验证差异计数、统计变化、策略指纹变化、
项目隔离与同数据集校验。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.datasets.version_compare import compare_versions
from app.domain.annotation.errors import ResourceNotFoundError
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.datasets import (
    DatasetItemModel,
    DatasetModel,
    DatasetVersionModel,
)
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from sqlalchemy.orm import Session

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
_ACTOR = uuid4()


def _engine_factory() -> tuple[object, object]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    return engine, create_session_factory(engine)


def _seed_dataset(session: Session, project_id: object, user_id: object) -> object:
    """dataset_versions 的复合外键需要 datasets 父行存在。"""
    dataset_id = uuid4()
    session.add(
        DatasetModel(
            id=dataset_id,
            project_id=project_id,
            dataset_lineage_id=uuid4(),
            code=f"ds-{dataset_id.hex[:8]}",
            name="测试数据集",
            purpose="TRAINING",
            owner_id=user_id,
            status="DRAFT",  # 数据集初始状态 guard 要求 DRAFT
            usage_policy="TRAINING_ALLOWED",
            version=1,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    return dataset_id


def _version(
    session: Session,
    dataset_id: object,
    version: int,
    *,
    project: object = None,
    actor: object = _ACTOR,
    **overrides: object,
) -> DatasetVersionModel:
    values: dict[str, object] = {
        "id": uuid4(),
        "project_id": project or uuid4(),
        "dataset_id": dataset_id,
        "dataset_lineage_id": uuid4(),
        "dataset_version": version,
        "status": "DRAFT",
        "validity_status": "VALID",
        "selection_rule_version": "rule-1",
        "selection_filters": {},
        "selection_snapshot_at": NOW,
        "dedup_policy_id": uuid4(),
        "dedup_policy_version": 1,
        "dedup_policy_sha256": "sha256:" + "d" * 64,
        "split_policy_id": uuid4(),
        "split_policy_version": 1,
        "split_policy_sha256": "sha256:" + "s" * 64,
        "leakage_policy_id": uuid4(),
        "leakage_policy_version": 1,
        "leakage_policy_sha256": "sha256:" + "l" * 64,
        "quality_policy_id": uuid4(),
        "quality_policy_version": 1,
        "quality_policy_sha256": "sha256:" + "q" * 64,
        "candidate_count": 10,
        "selected_count": 8,
        "excluded_count": 2,
        "duplicated_count": 0,
        "item_count": 8,
        "train_count": 5,
        "validation_count": 2,
        "test_count": 1,
        "total_duration_ns": 1000,
        "build_attempt": 1,
        "version": 1,
        "created_by": actor,
        "updated_by": actor,
    }
    values.update(overrides)
    row = DatasetVersionModel(**values)  # type: ignore[arg-type]
    session.add(row)
    return row


def _seed_episode(session: Session, project_id: object, user_id: object) -> object:
    """dataset_items 的复合外键需要 episodes 父行存在。"""
    episode_id = uuid4()
    session.add(
        EpisodeModel(
            id=episode_id,
            project_id=project_id,
            episode_lineage_id=uuid4(),
            episode_version=1,
            code=f"ep-{uuid4().hex[:8]}",
            device_ids=[],
            sensor_ids=[],
            status="PENDING",
            quality_status="NOT_CHECKED",
            annotation_status="NOT_STARTED",
            scene_labels=[],
            start_time_ns=0,
            end_time_ns=1000,
            duration_ns=1000,
            time_domain="UTC",
            time_base={},
            canonical_schema_version="v1",
            pipeline_id=f"pipe-{uuid4().hex[:8]}",
            pipeline_version="1",
            segmentation_rule_version="1",
            frame_count=0,
            calibration_snapshot={},
            preview_refs=[],
            attempt=1,
            max_attempts=3,
            version=1,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
    return episode_id


def _item(
    session: Session, version_id: object, sha: str, *, project: object = None, user: object = _ACTOR
) -> None:
    """造一条条目。唯一约束是 (project, version, episode)，
    因此每条条目必须用**独立的 episode**（同版本同 episode 只允许一条）。"""
    episode_id = _seed_episode(session, project or uuid4(), user)
    session.add(
        DatasetItemModel(
            id=uuid4(),
            project_id=project or uuid4(),
            raw_asset_ids=[],
            selected_reason="selected",
            dataset_version_id=version_id,
            episode_id=episode_id,
            episode_version=1,
            episode_lineage_id=uuid4(),
            item_sha256=f"sha256:{sha}",  # 格式约束要求 sha256: 前缀
            manifest_sha256=f"sha256:{sha}",
            annotation_submission_id=uuid4(),
            annotation_version=1,
            annotation_record_digest="sha256:" + "a" * 64,
            schema_id=uuid4(),
            schema_version=1,
            schema_sha256="sha256:" + "x" * 64,
            split="TRAIN",
            stratum=1,
            sample_role="NORMAL",
            weight=1.0,
            created_at=NOW,
        )
    )


def test_compare_reports_item_diff_and_stats() -> None:
    engine, factory = _engine_factory()
    project_id, user_id = uuid4(), uuid4()
    with factory() as session:
        session.add(Project(id=project_id, code="p1", name="项目"))
        session.add(User(id=user_id, display_name="u"))
        session.flush()  # 父行先落库，子行 FK 才可见
        dataset_id = _seed_dataset(session, project_id, user_id)
        base = _version(session, dataset_id, 1, project=project_id, actor=user_id)
        cand = _version(session, dataset_id, 2, project=project_id, actor=user_id)
        session.flush()  # 版本行先落库，条目 FK 检查才可见
        # 基线 3 条：a b c；候选 3 条：b c d → 增 d 删 a 共 b c
        _item(session, base.id, "a" * 64, project=project_id, user=user_id)
        _item(session, base.id, "b" * 64, project=project_id, user=user_id)
        _item(session, base.id, "c" * 64, project=project_id, user=user_id)
        _item(session, cand.id, "b" * 64, project=project_id, user=user_id)
        _item(session, cand.id, "c" * 64, project=project_id, user=user_id)
        _item(session, cand.id, "d" * 64, project=project_id, user=user_id)
        session.commit()

    with factory() as session:
        scope = ProjectScope(project_id=project_id, actor_id=user_id)
        result = compare_versions(
            session,
            scope,
            dataset_id=dataset_id,
            baseline_version_id=base.id,
            candidate_version_id=cand.id,
        )

    assert result.baseline_version == 1
    assert result.candidate_version == 2
    assert result.items_added == 1
    assert result.items_removed == 1
    assert result.items_common == 2
    assert result.item_comparison_truncated is False
    # 样例只含标识字段，不含完整 payload
    assert len(result.added_samples) == 1
    assert "sha256" in result.added_samples[0]
    # 统计：候选 item_count 变化（基线 3 条 vs 候选 3 条但统计值可独立变化）
    assert result.stats_diff["item_count"] == (8, 8)


def test_compare_detects_policy_fingerprint_change() -> None:
    """策略指纹变了（数据没变但口径变了）必须显式暴露。"""
    engine, factory = _engine_factory()
    project_id, user_id = uuid4(), uuid4()
    with factory() as session:
        session.add(Project(id=project_id, code="p2", name="项目"))
        session.add(User(id=user_id, display_name="u"))
        session.flush()  # 父行先落库，子行 FK 才可见
        dataset_id = _seed_dataset(session, project_id, user_id)
        base = _version(session, dataset_id, 1, project=project_id, actor=user_id)
        cand = _version(
            session,
            dataset_id,
            2,
            project=project_id,
            actor=user_id,
            split_policy_sha256="sha256:" + "s2" * 32,
        )
        session.commit()

    with factory() as session:
        scope = ProjectScope(project_id=project_id, actor_id=user_id)
        result = compare_versions(
            session,
            scope,
            dataset_id=dataset_id,
            baseline_version_id=base.id,
            candidate_version_id=cand.id,
        )

    assert "切分策略" in result.policy_changes
    assert "去重策略" not in result.policy_changes  # 未变的不出现


def test_compare_rejects_cross_dataset_versions() -> None:
    engine, factory = _engine_factory()
    project_id, user_id = uuid4(), uuid4()
    with factory() as session:
        session.add(Project(id=project_id, code="p3", name="项目"))
        session.add(User(id=user_id, display_name="u"))
        session.flush()  # 父行先落库，子行 FK 才可见
        base_ds = _seed_dataset(session, project_id, user_id)
        other_ds = _seed_dataset(session, project_id, user_id)
        base = _version(session, base_ds, 1, project=project_id, actor=user_id)
        cand = _version(session, other_ds, 2, project=project_id, actor=user_id)  # 不同 dataset_id
        session.commit()

    with factory() as session:
        scope = ProjectScope(project_id=project_id, actor_id=user_id)
        with pytest.raises(ResourceNotFoundError):
            compare_versions(
                session,
                scope,
                dataset_id=base.dataset_id,
                baseline_version_id=base.id,
                candidate_version_id=cand.id,
            )


def test_compare_rejects_other_project_version() -> None:
    engine, factory = _engine_factory()
    project_id, user_id = uuid4(), uuid4()
    other_project = uuid4()
    with factory() as session:
        session.add(Project(id=project_id, code="p4", name="项目"))
        session.add(Project(id=other_project, code="p5", name="其他"))
        session.add(User(id=user_id, display_name="u"))
        session.flush()  # 父行先落库，子行 FK 才可见
        ds = _seed_dataset(session, other_project, user_id)
        base = _version(session, ds, 1, project=other_project, actor=user_id)
        session.commit()

    with factory() as session:
        scope = ProjectScope(project_id=project_id, actor_id=user_id)
        with pytest.raises(ResourceNotFoundError):
            compare_versions(
                session,
                scope,
                dataset_id=base.dataset_id,
                baseline_version_id=base.id,
                candidate_version_id=base.id,
            )


def test_compare_rejects_invalid_limit() -> None:
    engine, factory = _engine_factory()
    project_id, user_id = uuid4(), uuid4()
    with factory() as session:
        session.add(Project(id=project_id, code="p6", name="项目"))
        session.add(User(id=user_id, display_name="u"))
        session.flush()  # 父行先落库，子行 FK 才可见
        ds = _seed_dataset(session, project_id, user_id)
        base = _version(session, ds, 1, project=project_id, actor=user_id)
        session.commit()

    with factory() as session:
        scope = ProjectScope(project_id=project_id, actor_id=user_id)
        from app.domain.annotation.errors import DomainValidationError

        with pytest.raises(DomainValidationError):
            compare_versions(
                session,
                scope,
                dataset_id=base.dataset_id,
                baseline_version_id=base.id,
                candidate_version_id=base.id,
                limit=0,
            )
