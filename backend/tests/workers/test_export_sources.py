"""导出取数适配器测试：只有 APPROVED 标注才进入导出，媒体来自真实资产。

这些用例走真实 SQLite 库与真实仓储（不是内存替身），专门覆盖两条容易出错的
口径：① 未批准的标注记录不得被导出；② 媒体位置必须来自
``episode_raw_assets`` + ``raw_assets``，而不是编造或留空。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.datasets.enums import Split
from app.domain.datasets.models import DatasetItem
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_results import AnnotationRecord
from app.infrastructure.db.models.datasets import DatasetItemModel
from app.infrastructure.repositories.export_sources import SqlExportEpisodeSourceDirectory
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds
from tests.api.dataset_support import RECORD_SHA
from tests.api.export_support import build_frozen_version

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
APPROVED_SHA = "sha256:" + "e" * 64
DRAFT_SHA = "sha256:" + "f" * 64


def _labels(action: str) -> list[dict[str, object]]:
    return [
        {
            "label_key": "episode_outcome",
            "value": "success",
            "source": "HUMAN",
            "label_status": "CONFIRMED",
        },
        {
            "label_key": "action_segment",
            "value": action,
            "source": "HUMAN",
            "label_status": "CONFIRMED",
        },
    ]


def _add_record(
    session: Session,
    *,
    project_id: UUID,
    actor_id: UUID,
    submission_id: UUID,
    task_id: UUID,
    episode_id: UUID,
    episode_version: int,
    schema_id: UUID,
    schema_version: int,
    status: str,
    digest: str,
    action: str,
) -> None:
    """插入一条标注记录；需要 APPROVED 时按真实状态机逐跳推进。

    数据库触发器只允许 DRAFT 初始插入，且非 DRAFT 后内容列不可改，因此
    标签必须在 DRAFT 阶段写入，再推进状态——与生产链路一致。
    """
    record_id = uuid4()
    session.add(
        AnnotationRecord(
            id=record_id,
            project_id=project_id,
            submission_id=submission_id,
            task_id=task_id,
            episode_id=episode_id,
            episode_version=episode_version,
            annotation_lineage_id=uuid4(),
            annotation_version=1,
            schema_id=schema_id,
            schema_version=schema_version,
            status="DRAFT",
            time_range=None,
            labels=_labels(action),
            evidence_refs=[],
            record_sha256=digest,
            supersedes_id=None,
            approved_by=None,
            approved_at=None,
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    session.flush()
    if status == "DRAFT":
        return
    session.execute(
        update(AnnotationRecord)
        .where(AnnotationRecord.id == record_id)
        .values(status="SUBMITTED", version=2)
    )
    if status != "APPROVED":
        raise AssertionError(f"未支持的测试状态：{status}")
    session.execute(
        update(AnnotationRecord)
        .where(AnnotationRecord.id == record_id)
        .values(
            status="APPROVED",
            approved_by=actor_id,
            approved_at=NOW,
            version=3,
        )
    )


def _item(
    episode_id: UUID,
    *,
    project_id: UUID,
    submission_id: UUID,
    digest: str,
    raw_asset_ids: tuple[UUID, ...],
) -> DatasetItem:
    return DatasetItem(
        dataset_item_id=uuid4(),
        project_id=project_id,
        dataset_version_id=uuid4(),
        episode_id=episode_id,
        episode_version=1,
        episode_lineage_id=uuid4(),
        manifest_sha256=RECORD_SHA,
        raw_asset_ids=raw_asset_ids,
        annotation_submission_id=submission_id,
        annotation_version=1,
        annotation_record_digest=digest,
        schema_id=uuid4(),
        schema_version=1,
        schema_sha256=RECORD_SHA,
        split=Split.TRAIN,
    )


def test_export_sources_only_read_approved_labels_and_real_media(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    scope = ProjectScope(ids.project_a, actor_id=ids.editor_a)

    with factory() as session:
        row = session.execute(
            select(
                DatasetItemModel.episode_id,
                DatasetItemModel.annotation_submission_id,
                DatasetItemModel.schema_id,
                DatasetItemModel.schema_version,
                DatasetItemModel.raw_asset_ids,
            ).where(DatasetItemModel.dataset_version_id == UUID(str(ctx["version_id"])))
        ).first()
        assert row is not None
        episode_id = row.episode_id
        submission_id = row.annotation_submission_id
        raw_asset_ids = tuple(UUID(value) for value in row.raw_asset_ids)
        # 复用既有任务的 FK，避免构造出不存在的 annotation_tasks。
        seeded = session.execute(
            select(AnnotationRecord.task_id, AnnotationRecord.episode_version).where(
                AnnotationRecord.project_id == ids.project_a,
                AnnotationRecord.episode_id == episode_id,
            )
        ).first()
        assert seeded is not None

        # 同一 (提交, Episode) 下：一条 APPROVED、一条 DRAFT，只有前者可被导出。
        common = {
            "project_id": ids.project_a,
            "actor_id": ids.editor_a,
            "submission_id": submission_id,
            "task_id": seeded.task_id,
            "episode_id": episode_id,
            "episode_version": seeded.episode_version,
            "schema_id": row.schema_id,
            "schema_version": row.schema_version,
        }
        _add_record(
            session,
            **common,
            status="APPROVED",
            digest=APPROVED_SHA,
            action="grasp",
        )
        _add_record(
            session,
            **common,
            status="DRAFT",
            digest=DRAFT_SHA,
            action="place",
        )
        session.commit()

        directory = SqlExportEpisodeSourceDirectory(session)
        approved = directory.list_for_items(
            scope,
            (
                _item(
                    episode_id,
                    project_id=ids.project_a,
                    submission_id=submission_id,
                    digest=APPROVED_SHA,
                    raw_asset_ids=raw_asset_ids,
                ),
            ),
        )[0]
        pending = directory.list_for_items(
            scope,
            (
                _item(
                    episode_id,
                    project_id=ids.project_a,
                    submission_id=submission_id,
                    digest=DRAFT_SHA,
                    raw_asset_ids=raw_asset_ids,
                ),
            ),
        )[0]

    assert [label["value"] for label in approved.labels] == ["success", "grasp"]
    assert approved.annotation_submission_id == submission_id
    assert pending.labels == ()
    assert pending.annotation_submission_id is None
    assert pending.annotation_version is None

    # 媒体位置来自真实 episode_raw_assets + raw_assets。
    assert len(approved.media) == 1
    media = approved.media[0]
    assert media.episode_id == episode_id
    assert media.role == "PRIMARY"
    assert media.asset_id in raw_asset_ids
    assert media.object_key.startswith("raw/")
    assert media.sha256.startswith("sha256:")
    assert media.size_bytes > 0
    assert media.duration_ns == 10_000
    assert media.content_type == "application/x-mcap"


def test_export_sources_ignore_cross_project_episodes(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """跨项目 ID 一律取不到：媒体与标签都为空，绝不串项目。"""
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    scope = ProjectScope(ids.project_b, actor_id=ids.editor_a)

    with factory() as session:
        row = session.execute(
            select(DatasetItemModel.episode_id, DatasetItemModel.annotation_submission_id).where(
                DatasetItemModel.dataset_version_id == UUID(str(ctx["version_id"]))
            )
        ).first()
        assert row is not None
        item = _item(
            row.episode_id,
            project_id=ids.project_a,
            submission_id=row.annotation_submission_id,
            digest=RECORD_SHA,
            raw_asset_ids=(uuid4(),),
        )
        source = SqlExportEpisodeSourceDirectory(session).list_for_items(scope, (item,))[0]

    assert source.labels == ()
    assert source.media == ()
    assert source.success is None
    assert source.duration_ns is None
