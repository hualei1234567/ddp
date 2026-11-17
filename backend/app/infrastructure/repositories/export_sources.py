"""导出用 Episode 事实、已批准标签与媒体位置的读取适配器。

只读、无副作用，供 ``ExportExecutionService`` 在 Worker 内装配训练输入索引：

- Episode 事实：``episodes.duration_ns`` / ``episodes.success``；
- 媒体位置：``episode_raw_assets`` 关联 ``raw_assets``（对象键、Hash、大小）；
- 标签：``annotation_records.labels``，且**只取** ``status='APPROVED'`` 的记录。

标签按数据集条目冻结的 ``annotation_submission_id`` + ``annotation_record_digest``
精确取，不是"该 Episode 当前最新的批准记录"：版本冻结之后再有新标注被批准，属于
版本外事实，混进来会破坏导出产物的可复现性。

所有查询强制 ``scope.project_id`` 过滤；跨项目 ID 一律查不到。
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.datasets.exporting import EpisodeExportSource, EpisodeMediaRef
from app.domain.datasets.models import DatasetItem
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_results import AnnotationRecord
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.repositories.dataset_directory import load_episode_assets

APPROVED_RECORD_STATUS = "APPROVED"


class SqlExportEpisodeSourceDirectory:
    """复用既有表与既有资产 join 规则的导出取数实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_items(
        self,
        scope: ProjectScope,
        items: Sequence[DatasetItem],
    ) -> Sequence[EpisodeExportSource]:
        if not items:
            return ()
        episode_ids = list(dict.fromkeys(item.episode_id for item in items))
        facts = self._load_episode_facts(scope, episode_ids)
        media = self._load_media(scope, episode_ids, facts)
        labels = self._load_approved_labels(scope, items)
        sources: list[EpisodeExportSource] = []
        for item in items:
            # 用条目冻结的记录摘要精确命中：同一提交下若存在多条记录，摘要才是
            # "当时冻结的是哪一条"的权威标识；靠"最新版本"会取到版本外的新数据。
            record = labels.get(
                (item.annotation_submission_id, item.episode_id, item.annotation_record_digest)
            )
            success, duration_ns = facts.get(item.episode_id, (None, None))
            sources.append(
                EpisodeExportSource(
                    episode_id=item.episode_id,
                    duration_ns=duration_ns,
                    success=success,
                    labels=tuple(record.labels or ()) if record is not None else (),
                    media=tuple(media.get(item.episode_id, ())),
                    annotation_submission_id=(record.submission_id if record is not None else None),
                    annotation_version=(record.annotation_version if record is not None else None),
                )
            )
        return tuple(sources)

    def _load_episode_facts(
        self,
        scope: ProjectScope,
        episode_ids: list[UUID],
    ) -> dict[UUID, tuple[bool | None, int | None]]:
        rows = self._session.execute(
            select(EpisodeModel.id, EpisodeModel.success, EpisodeModel.duration_ns).where(
                EpisodeModel.project_id == scope.project_id,
                EpisodeModel.id.in_(episode_ids),
            )
        )
        return {row[0]: (row[1], row[2]) for row in rows}

    def _load_media(
        self,
        scope: ProjectScope,
        episode_ids: list[UUID],
        facts: dict[UUID, tuple[bool | None, int | None]],
    ) -> dict[UUID, list[EpisodeMediaRef]]:
        grouped = load_episode_assets(self._session, scope, episode_ids)
        result: dict[UUID, list[EpisodeMediaRef]] = {}
        for episode_id, pairs in grouped.items():
            links: list[EpisodeMediaRef] = []
            for link, asset in pairs:
                if link.start_time_ns is not None and link.end_time_ns is not None:
                    duration_ns: int | None = link.end_time_ns - link.start_time_ns
                else:
                    # 关联没有切片范围时，媒体覆盖整个 Episode：用 Episode 时长，
                    # 不用 0 或猜测值冒充。
                    duration_ns = facts.get(episode_id, (None, None))[1]
                links.append(
                    EpisodeMediaRef(
                        episode_id=episode_id,
                        role=link.role,
                        object_key=asset.object_key,
                        sha256=asset.sha256,
                        size_bytes=asset.size_bytes,
                        duration_ns=duration_ns,
                        asset_id=asset.id,
                        content_type=asset.content_type,
                    )
                )
            result[episode_id] = links
        return result

    def _load_approved_labels(
        self,
        scope: ProjectScope,
        items: Sequence[DatasetItem],
    ) -> dict[tuple[UUID, UUID, str], AnnotationRecord]:
        """取 ``status='APPROVED'`` 的记录，键为 (提交, Episode, 记录摘要)。

        非 APPROVED（DRAFT/SUBMITTED/REJECTED/SUPERSEDED/INVALIDATED）记录一律
        不参与导出；命中不到就返回空，由上层如实留空。
        """
        submission_ids = list(dict.fromkeys(item.annotation_submission_id for item in items))
        if not submission_ids:
            return {}
        episode_ids = list(dict.fromkeys(item.episode_id for item in items))
        rows = self._session.scalars(
            select(AnnotationRecord)
            .where(
                AnnotationRecord.project_id == scope.project_id,
                AnnotationRecord.submission_id.in_(submission_ids),
                AnnotationRecord.episode_id.in_(episode_ids),
                AnnotationRecord.status == APPROVED_RECORD_STATUS,
            )
            .order_by(
                AnnotationRecord.submission_id,
                AnnotationRecord.episode_id,
                AnnotationRecord.id,
            )
        )
        records: dict[tuple[UUID, UUID, str], AnnotationRecord] = {}
        for row in rows:
            records.setdefault((row.submission_id, row.episode_id, row.record_sha256), row)
        return records


__all__ = ["SqlExportEpisodeSourceDirectory"]
