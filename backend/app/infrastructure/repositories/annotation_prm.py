"""PRM 步级标注 SQLAlchemy 仓储（V52-020）。

一条轨迹一行：领域聚合 ``PrmAnnotation`` 与行通过 ``to_payload`` /
``from_payload`` 互转；查询返回 :class:`PrmAnnotationView`（ID + 实体 +
审计列），项目隔离由 ``scoped_select`` 强制，跨项目查询返回 ``None`` 由
用例映射 404。创建即不可变：无 update 路径，分叉轨迹作为新行追加
（血缘在 ``fork_lineage`` JSON 列里）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.annotation.ports import PrmAnnotationView
from app.domain.annotation.prm_step import PrmAnnotation
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.prm_annotation import PrmAnnotationModel as _Model
from app.infrastructure.repositories._annotation_sql import (
    flush_or_duplicate,
    scoped_select,
)


class SqlPrmAnnotationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        scope: ProjectScope,
        annotation: PrmAnnotation,
        *,
        annotation_id: UUID,
        actor_id: UUID,
    ) -> PrmAnnotationView:
        now = datetime.now(UTC)
        payload = annotation.to_payload()
        self._session.add(
            _Model(
                id=annotation_id,
                project_id=scope.project_id,
                trajectory_id=annotation.trajectory_id,
                task_goal=annotation.task_goal,
                step_actions=payload["step_actions"],
                marks=payload["marks"],
                fork_lineage=payload.get("fork_lineage"),
                step_count=annotation.step_count,
                marked_count=annotation.labeled_step_count,
                version=1,
                created_at=now,
                updated_at=now,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        flush_or_duplicate(self._session)
        return PrmAnnotationView(
            annotation_id=annotation_id,
            annotation=annotation,
            version=1,
            created_at=now,
            updated_at=now,
        )

    def get(self, scope: ProjectScope, annotation_id: UUID) -> PrmAnnotationView | None:
        row = self._session.scalar(
            select(_Model).where(
                _Model.id == annotation_id,
                _Model.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_trajectory(
        self, scope: ProjectScope, trajectory_id: str
    ) -> PrmAnnotationView | None:
        row = self._session.scalar(
            scoped_select(_Model, scope).where(_Model.trajectory_id == trajectory_id)
        )
        return self._from_row(row) if row is not None else None

    def list_for_project(self, scope: ProjectScope, *, limit: int = 50) -> list[PrmAnnotationView]:
        rows = self._session.scalars(
            scoped_select(_Model, scope).order_by(_Model.created_at, _Model.id).limit(limit)
        )
        return [self._from_row(row) for row in rows]

    def _from_row(self, row: _Model) -> PrmAnnotationView:
        payload: dict[str, object] = {
            "trajectory_id": row.trajectory_id,
            "step_actions": row.step_actions,
            "marks": row.marks,
        }
        if row.task_goal is not None:
            payload["task_goal"] = row.task_goal
        if row.fork_lineage is not None:
            payload["fork_lineage"] = row.fork_lineage
        return PrmAnnotationView(
            annotation_id=row.id,
            annotation=PrmAnnotation.from_payload(payload),
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
        )


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite 读出的 naive 时间补 UTC；PG 侧本就带时区。"""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
