"""采集批次 SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.collection.enums import CollectionBatchStatus
from app.domain.collection.errors import DomainValidationError
from app.domain.collection.models import CollectionBatch
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import (
    CollectionBatch as CollectionBatchModel,
)
from app.infrastructure.repositories._collection_sql import (
    as_utc,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    raise_update_failure,
    scoped_select,
)

_TERMINAL_STATUSES = (
    CollectionBatchStatus.ACCEPTED.value,
    CollectionBatchStatus.CANCELLED.value,
)


class SqlCollectionBatchRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, batch: CollectionBatch) -> CollectionBatch:
        scope.ensure(batch.project_id)
        ensure_initial_version(batch.version)
        now = datetime.now(UTC)
        row = CollectionBatchModel(
            id=batch.batch_id,
            project_id=batch.project_id,
            task_id=batch.task_id,
            code=batch.code,
            sequence_no=batch.sequence_no,
            status=enum_value(batch.status),
            assigned_to=batch.assigned_to,
            scheduled_start=batch.scheduled_start,
            scheduled_end=batch.scheduled_end,
            expected_count=batch.expected_count,
            submitted_count=batch.submitted_count,
            accepted_count=batch.accepted_count,
            status_reason=batch.status_reason,
            created_at=batch.created_at or now,
            updated_at=batch.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session)
        return batch

    def get(self, scope: ProjectScope, batch_id: UUID) -> CollectionBatch | None:
        row = self._session.scalar(
            select(CollectionBatchModel).where(
                CollectionBatchModel.id == batch_id,
                CollectionBatchModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_code(self, scope: ProjectScope, code: str) -> CollectionBatch | None:
        row = self._session.scalar(
            select(CollectionBatchModel).where(
                CollectionBatchModel.code == code.strip(),
                CollectionBatchModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        status: CollectionBatchStatus | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[CollectionBatch]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = scoped_select(CollectionBatchModel, scope).where(
            CollectionBatchModel.task_id == task_id
        )
        if status is not None:
            statement = statement.where(CollectionBatchModel.status == status.value)
        if after_id is not None:
            statement = statement.where(CollectionBatchModel.id > after_id)
        statement = statement.order_by(CollectionBatchModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def list_non_terminal(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
    ) -> list[CollectionBatch]:
        statement = (
            scoped_select(CollectionBatchModel, scope)
            .where(
                CollectionBatchModel.task_id == task_id,
                CollectionBatchModel.status.not_in(_TERMINAL_STATUSES),
            )
            .order_by(CollectionBatchModel.sequence_no)
        )
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def next_sequence_no(self, scope: ProjectScope, task_id: UUID) -> int:
        current = self._session.scalar(
            select(func.max(CollectionBatchModel.sequence_no)).where(
                CollectionBatchModel.project_id == scope.project_id,
                CollectionBatchModel.task_id == task_id,
            )
        )
        return int(current or 0) + 1

    def sum_expected_count(self, scope: ProjectScope, task_id: UUID) -> int:
        total = self._session.scalar(
            select(func.coalesce(func.sum(CollectionBatchModel.expected_count), 0)).where(
                CollectionBatchModel.project_id == scope.project_id,
                CollectionBatchModel.task_id == task_id,
                CollectionBatchModel.status != CollectionBatchStatus.CANCELLED.value,
            )
        )
        return int(total or 0)

    def save(
        self,
        scope: ProjectScope,
        batch: CollectionBatch,
        *,
        expected_version: int,
    ) -> CollectionBatch:
        scope.ensure(batch.project_id)
        ensure_expected_version(batch.version, expected_version)
        now = batch.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(CollectionBatchModel)
            .where(
                CollectionBatchModel.id == batch.batch_id,
                CollectionBatchModel.project_id == scope.project_id,
                CollectionBatchModel.version == expected_version,
            )
            .values(
                code=batch.code,
                sequence_no=batch.sequence_no,
                status=enum_value(batch.status),
                assigned_to=batch.assigned_to,
                scheduled_start=batch.scheduled_start,
                scheduled_end=batch.scheduled_end,
                expected_count=batch.expected_count,
                submitted_count=batch.submitted_count,
                accepted_count=batch.accepted_count,
                status_reason=batch.status_reason,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                CollectionBatchModel,
                scope,
                batch.batch_id,
                "采集批次",
            )
        return replace(
            batch,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: CollectionBatchModel) -> CollectionBatch:
        return CollectionBatch(
            batch_id=row.id,
            project_id=row.project_id,
            task_id=row.task_id,
            code=row.code,
            sequence_no=row.sequence_no,
            status=CollectionBatchStatus(row.status),
            assigned_to=row.assigned_to,
            scheduled_start=as_utc(row.scheduled_start),
            scheduled_end=as_utc(row.scheduled_end),
            expected_count=row.expected_count,
            submitted_count=row.submitted_count,
            accepted_count=row.accepted_count,
            status_reason=row.status_reason,
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )
