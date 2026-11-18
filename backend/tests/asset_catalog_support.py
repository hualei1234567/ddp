"""统一资产目录测试夹具：在真实 SQLite 表中写入五类资源。

项目隔离测试需要"两个项目里存在同名 code"：因此本夹具对每个项目写入完全
相同的 ``code``，只有主键与对象路径不同。若实现漏了 ``project_id`` 条件，
测试会立刻看到重复条目而不是碰巧通过。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.infrastructure.db.models.datasets import DatasetModel, DatasetVersionModel
from app.infrastructure.db.models.datasources import (
    DataSourceImportModel,
    DataSourceModel,
    MediaAssetModel,
)
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.models.ingestion import RawAssetModel
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

BASE_TIME = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
_SHA = "sha256:" + "a" * 64


def _digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def seed_identity(
    factory: sessionmaker[Session],
    *,
    project_a: UUID,
    project_b: UUID,
    users: tuple[UUID, ...],
) -> None:
    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="catalog-a", name="目录项目 A"),
                Project(id=project_b, code="catalog-b", name="目录项目 B"),
                *[
                    User(id=user_id, display_name=f"用户 {index}")
                    for index, user_id in enumerate(users)
                ],
            ]
        )
        session.commit()


def seed_asset_catalog(
    factory: sessionmaker[Session],
    project_id: UUID,
    actor_id: UUID,
    *,
    handle: str,
) -> dict[str, UUID]:
    """写入五类资源；``handle`` 只用于对象键/路径，不改变检索用 code。"""
    ids = {
        "raw_alpha": uuid4(),
        "raw_beta": uuid4(),
        "episode_alpha": uuid4(),
        "episode_beta": uuid4(),
        "dataset_alpha": uuid4(),
        "version_alpha": uuid4(),
        "data_source": uuid4(),
        "import": uuid4(),
        "media_alpha": uuid4(),
        "media_beta": uuid4(),
    }
    with factory() as session:
        session.add_all(
            [
                RawAssetModel(
                    id=ids["raw_alpha"],
                    project_id=project_id,
                    code="raw-alpha",
                    source_type="UPLOAD",
                    original_filename="alpha.mcap",
                    object_key=f"raw/{project_id}/{handle}/alpha.mcap",
                    size_bytes=1024,
                    content_type="application/x-mcap",
                    format="MCAP",
                    sha256=_digest(f"{project_id}:{handle}:raw-alpha"),
                    status="VALIDATING",
                    ingested_at=BASE_TIME,
                    storage_class="STANDARD",
                    quarantine_codes=[],
                    raw_metadata={},
                    uploaded_by=actor_id,
                    version=1,
                    created_at=BASE_TIME,
                    updated_at=BASE_TIME,
                    created_by=actor_id,
                    updated_by=actor_id,
                ),
                RawAssetModel(
                    id=ids["raw_beta"],
                    project_id=project_id,
                    code="raw-beta",
                    source_type="UPLOAD",
                    original_filename="beta.mcap",
                    object_key=f"raw/{project_id}/{handle}/beta.mcap",
                    size_bytes=2048,
                    content_type="application/x-mcap",
                    format="MCAP",
                    sha256=_digest(f"{project_id}:{handle}:raw-beta"),
                    status="VALIDATING",
                    ingested_at=BASE_TIME,
                    storage_class="STANDARD",
                    quarantine_codes=[],
                    raw_metadata={},
                    uploaded_by=actor_id,
                    version=1,
                    created_at=BASE_TIME,
                    updated_at=BASE_TIME + timedelta(minutes=1),
                    created_by=actor_id,
                    updated_by=actor_id,
                ),
                EpisodeModel(
                    id=ids["episode_alpha"],
                    project_id=project_id,
                    episode_lineage_id=uuid4(),
                    episode_version=1,
                    code="ep-alpha",
                    status="PENDING",
                    quality_status="NOT_CHECKED",
                    annotation_status="NOT_STARTED",
                    scene_labels=[],
                    start_time_ns=1_000,
                    end_time_ns=2_000,
                    duration_ns=1_000,
                    time_domain="UTC",
                    time_base={
                        "time_domain": "UTC",
                        "clock_source": "HOST_CLOCK",
                        "sync_method": "PTP",
                        "sync_status": "VERIFIED",
                    },
                    canonical_schema_version="ddp.canonical/1.0.0-draft",
                    pipeline_id="parser",
                    pipeline_version="1.0.0",
                    segmentation_rule_version="seg-v1",
                    calibration_snapshot=[],
                    manifest_key=None,
                    manifest_sha256=None,
                    frame_count=10,
                    attempt=1,
                    max_attempts=3,
                    created_at=BASE_TIME,
                    updated_at=BASE_TIME + timedelta(minutes=2),
                    created_by=actor_id,
                    updated_by=actor_id,
                ),
                EpisodeModel(
                    id=ids["episode_beta"],
                    project_id=project_id,
                    episode_lineage_id=uuid4(),
                    episode_version=1,
                    code="ep-beta",
                    status="PENDING",
                    quality_status="NOT_CHECKED",
                    annotation_status="NOT_STARTED",
                    scene_labels=[],
                    start_time_ns=3_000,
                    end_time_ns=4_000,
                    duration_ns=1_000,
                    time_domain="UTC",
                    time_base={
                        "time_domain": "UTC",
                        "clock_source": "HOST_CLOCK",
                        "sync_method": "PTP",
                        "sync_status": "VERIFIED",
                    },
                    canonical_schema_version="ddp.canonical/1.0.0-draft",
                    pipeline_id="parser",
                    pipeline_version="1.0.0",
                    segmentation_rule_version="seg-v1",
                    calibration_snapshot=[],
                    manifest_key=None,
                    manifest_sha256=None,
                    frame_count=10,
                    attempt=1,
                    max_attempts=3,
                    created_at=BASE_TIME,
                    updated_at=BASE_TIME + timedelta(minutes=3),
                    created_by=actor_id,
                    updated_by=actor_id,
                ),
                DatasetModel(
                    id=ids["dataset_alpha"],
                    project_id=project_id,
                    dataset_lineage_id=uuid4(),
                    code="ds-alpha",
                    name="数据集 Alpha",
                    purpose="TRAINING",
                    owner_id=actor_id,
                    status="DRAFT",
                    tags=[],
                    usage_policy="TRAINING_DENIED",
                    frozen_version_count=0,
                    version=1,
                    created_at=BASE_TIME,
                    updated_at=BASE_TIME + timedelta(minutes=4),
                    created_by=actor_id,
                    updated_by=actor_id,
                ),
                DatasetVersionModel(
                    id=ids["version_alpha"],
                    project_id=project_id,
                    dataset_id=ids["dataset_alpha"],
                    dataset_lineage_id=uuid4(),
                    dataset_version=1,
                    status="DRAFT",
                    validity_status="VALID",
                    selection_rule_version="selection-v1",
                    selection_filters={},
                    selection_snapshot_at=BASE_TIME,
                    dedup_policy_id=uuid4(),
                    dedup_policy_version=1,
                    dedup_policy_sha256=_SHA,
                    split_policy_id=uuid4(),
                    split_policy_version=1,
                    split_policy_sha256=_SHA,
                    leakage_policy_id=uuid4(),
                    leakage_policy_version=1,
                    leakage_policy_sha256=_SHA,
                    quality_policy_id=uuid4(),
                    quality_policy_version=1,
                    quality_policy_sha256=_SHA,
                    candidate_count=0,
                    selected_count=0,
                    excluded_count=0,
                    duplicated_count=0,
                    item_count=0,
                    train_count=0,
                    validation_count=0,
                    test_count=0,
                    annotation_schema_versions=[],
                    blocking_issues=[],
                    warnings=[],
                    exclusion_summary={},
                    build_attempt=0,
                    version=1,
                    created_at=BASE_TIME,
                    updated_at=BASE_TIME + timedelta(minutes=5),
                    created_by=actor_id,
                    updated_by=actor_id,
                ),
                DataSourceModel(
                    id=ids["data_source"],
                    project_id=project_id,
                    code="source-alpha",
                    name="数据源 Alpha",
                    kind="HUGGINGFACE",
                    repo_id="example/catalog",
                    revision="0" * 40,
                    license="CC-BY-4.0",
                    access_condition="PUBLIC",
                    is_gated=False,
                    metadata_json={},
                    version=1,
                    created_at=BASE_TIME,
                    updated_at=BASE_TIME,
                    created_by=actor_id,
                    updated_by=actor_id,
                ),
            ]
        )
        session.commit()

    # RawAsset 的安装触发器只接受合法初始状态；种子数据先以 VALIDATING 落库，
    # 再按真实流转迁移到 VALID，不能直接把终态写进 INSERT。
    with factory() as session:
        # 显式回写 updated_at：ORM 的 onupdate 会把 UPDATE 时间改成当前时刻，
        # 那样两个资产会得到相同时间戳，键集排序用例就失去可判定性。
        for asset_id, updated_at in (
            (ids["raw_alpha"], BASE_TIME),
            (ids["raw_beta"], BASE_TIME + timedelta(minutes=1)),
        ):
            session.execute(
                update(RawAssetModel)
                .where(RawAssetModel.id == asset_id)
                .values(status="VALID", updated_at=updated_at)
                .execution_options(synchronize_session=False)
            )
        session.commit()

    # 数据源、导入与媒体资产分两阶段写入：导入表对 data_sources 有外键，
    # 显式先提交父行，避免依赖 ORM 的隐式排序。
    with factory() as session:
        session.add(
            DataSourceImportModel(
                id=ids["import"],
                project_id=project_id,
                data_source_id=ids["data_source"],
                status="SUCCEEDED",
                scope="FULL",
                tool_name="huggingface_hub",
                tool_version="0.25.0",
                imported_row_count=2,
                imported_file_count=2,
                attempt=1,
                max_attempts=3,
                version=1,
                created_at=BASE_TIME,
                updated_at=BASE_TIME,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        session.add_all(
            [
                MediaAssetModel(
                    id=ids["media_alpha"],
                    project_id=project_id,
                    data_source_id=ids["data_source"],
                    import_id=ids["import"],
                    raw_asset_id=ids["raw_alpha"],
                    episode_key="ep-alpha",
                    path=f"media/{handle}/ep-alpha/cam0.mp4",
                    camera="cam0",
                    object_key=f"media/{project_id}/{handle}/ep-alpha/cam0.mp4",
                    size_bytes=1024,
                    sha256=_digest(f"{project_id}:{handle}:media-alpha"),
                    content_type="video/mp4",
                    task_name="catalog",
                    version=1,
                    created_at=BASE_TIME,
                    updated_at=BASE_TIME + timedelta(minutes=2),
                    created_by=actor_id,
                    updated_by=actor_id,
                ),
                MediaAssetModel(
                    id=ids["media_beta"],
                    project_id=project_id,
                    data_source_id=ids["data_source"],
                    import_id=ids["import"],
                    raw_asset_id=ids["raw_beta"],
                    episode_key="ep-beta",
                    path=f"media/{handle}/ep-beta/cam0.mp4",
                    camera="cam0",
                    object_key=f"media/{project_id}/{handle}/ep-beta/cam0.mp4",
                    size_bytes=2048,
                    sha256=_digest(f"{project_id}:{handle}:media-beta"),
                    content_type="video/mp4",
                    task_name="catalog",
                    version=1,
                    created_at=BASE_TIME,
                    updated_at=BASE_TIME + timedelta(minutes=3),
                    created_by=actor_id,
                    updated_by=actor_id,
                ),
            ]
        )
        session.commit()
    return ids
