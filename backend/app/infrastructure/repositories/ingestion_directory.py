"""上传会话引用的采集任务/批次存在性适配。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import CollectionBatch, CollectionTask


class SqlCollectionDirectory:
    def __init__(self, session: Session) -> None:
        self._session = session

    def task_exists(self, scope: ProjectScope, task_id: UUID) -> bool:
        row = self._session.scalar(
            select(CollectionTask.id).where(
                CollectionTask.id == task_id,
                CollectionTask.project_id == scope.project_id,
            )
        )
        return row is not None

    def batch_belongs(
        self,
        scope: ProjectScope,
        *,
        batch_id: UUID,
        task_id: UUID,
    ) -> bool:
        row = self._session.scalar(
            select(CollectionBatch.id).where(
                CollectionBatch.id == batch_id,
                CollectionBatch.project_id == scope.project_id,
                CollectionBatch.task_id == task_id,
            )
        )
        return row is not None
