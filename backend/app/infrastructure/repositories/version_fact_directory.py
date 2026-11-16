"""``VersionFactDirectory`` 的 SQLAlchemy 实现。

只读查询，按 ``project_id`` 强制隔离：先确认版本属于当前项目，再按该版本的
``dataset_items`` 取 Episode 场景标签与已批准标注标签。任一查询失败都向上抛，
不做"查不到就当没有"的兜底——那会把缺陷伪装成 UNVERIFIABLE。
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.datasets.version_facts import VersionFactProjection
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_results import (
    AnnotationRecord,
    AnnotationSubmission,
)
from app.infrastructure.db.models.datasets import DatasetItemModel
from app.infrastructure.db.models.episodes import EpisodeModel

_APPROVED_SUBMISSION_STATUSES = ("APPROVED", "RESOLVED_APPROVED")
# LabelValue.value 可能是标量或容器；只有标量与字符串列表能作为"动作/标签取值"
# 参与近似核对，其余形状原样跳过（不猜测语义）。
_SCALAR_TYPES = (str, int, float, bool)


class SqlVersionFactDirectory:
    def __init__(self, session: Session) -> None:
        self._session = session

    def project(
        self,
        scope: ProjectScope,
        dataset_version_id: UUID,
    ) -> VersionFactProjection:
        item_rows = list(
            self._session.execute(
                select(
                    DatasetItemModel.episode_id,
                    DatasetItemModel.annotation_submission_id,
                ).where(
                    DatasetItemModel.project_id == scope.project_id,
                    DatasetItemModel.dataset_version_id == dataset_version_id,
                )
            )
        )
        if not item_rows:
            # 版本内没有条目：场景与标签都无从投影，返回 None 让领域层判 UNVERIFIABLE。
            return VersionFactProjection()
        episode_ids = [row.episode_id for row in item_rows]
        scene_labels = self._scene_labels(scope, episode_ids)
        annotation_labels = self._annotation_labels(
            scope,
            [row.annotation_submission_id for row in item_rows],
        )
        return VersionFactProjection(
            scene_labels=scene_labels,
            annotation_labels=annotation_labels,
        )

    def _scene_labels(
        self,
        scope: ProjectScope,
        episode_ids: list[UUID],
    ) -> frozenset[str]:
        rows = self._session.scalars(
            select(EpisodeModel.scene_labels).where(
                EpisodeModel.project_id == scope.project_id,
                EpisodeModel.id.in_(episode_ids),
            )
        )
        labels: set[str] = set()
        for value in rows:
            labels.update(str(item) for item in (value or ()))
        return frozenset(labels)

    def _annotation_labels(
        self,
        scope: ProjectScope,
        submission_ids: list[UUID],
    ) -> dict[str, frozenset[str]]:
        rows = self._session.execute(
            select(AnnotationRecord.labels).where(
                AnnotationRecord.project_id == scope.project_id,
                AnnotationRecord.submission_id.in_(submission_ids),
                AnnotationRecord.status == "APPROVED",
                AnnotationRecord.submission_id.in_(
                    select(AnnotationSubmission.id).where(
                        AnnotationSubmission.project_id == scope.project_id,
                        AnnotationSubmission.status.in_(_APPROVED_SUBMISSION_STATUSES),
                    )
                ),
            )
        )
        collected: dict[str, set[str]] = defaultdict(set)
        for (labels,) in rows:
            for label in labels or ():
                if not isinstance(label, dict):
                    continue
                key = label.get("label_key")
                if not isinstance(key, str) or not key.strip():
                    continue
                collected[key.strip()].update(_label_values(label.get("value")))
        return {key: frozenset(values) for key, values in collected.items()}


def _label_values(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, _SCALAR_TYPES):
        return {str(value)}
    if isinstance(value, list | tuple):
        return {item for item in value if isinstance(item, str)}
    return set()


__all__ = ["SqlVersionFactDirectory"]
