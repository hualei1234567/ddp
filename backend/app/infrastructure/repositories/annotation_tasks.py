"""标注任务 SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.task import AnnotationTask
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_work import (
    AnnotationTask as AnnotationTaskModel,
)
from app.infrastructure.repositories._annotation_sql import (
    as_utc,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    raise_update_failure,
    scoped_select,
)


class SqlAnnotationTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, task: AnnotationTask) -> AnnotationTask:
        scope.ensure(task.project_id)
        ensure_initial_version(task.version)
        now = datetime.now(UTC)
        row = AnnotationTaskModel(
            id=task.task_id,
            project_id=task.project_id,
            code=task.code,
            name=task.name,
            description=task.description,
            status=enum_value(task.status),
            sample_set_id=task.sample_set_id,
            schema_id=task.schema_id,
            schema_version=task.schema_version,
            schema_sha256=task.schema_sha256,
            gold_set_id=task.gold_set_id,
            iaa_policy=dict(task.iaa_policy),
            quality_policy=dict(task.quality_policy),
            assignment_mode=task.assignment_mode.value,
            target_item_count=task.target_item_count,
            target_episode_count=task.target_episode_count,
            due_at=task.due_at,
            priority=task.priority.value,
            progress=dict(task.progress),
            quality_summary=dict(task.quality_summary),
            iaa_report_id=task.iaa_report_id,
            accepted_at=task.accepted_at,
            completed_at=task.completed_at,
            status_reason=task.status_reason,
            blocked_reason=task.blocked_reason,
            version=task.version,
            created_at=task.created_at or now,
            updated_at=task.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session)
        return task

    def get(self, scope: ProjectScope, task_id: UUID) -> AnnotationTask | None:
        row = self._session.scalar(
            select(AnnotationTaskModel).where(
                AnnotationTaskModel.id == task_id,
                AnnotationTaskModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_code(self, scope: ProjectScope, code: str) -> AnnotationTask | None:
        row = self._session.scalar(
            select(AnnotationTaskModel).where(
                AnnotationTaskModel.project_id == scope.project_id,
                AnnotationTaskModel.code == code.strip(),
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationTask]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = scoped_select(AnnotationTaskModel, scope)
        if status is not None:
            statement = statement.where(AnnotationTaskModel.status == status)
        if after_id is not None:
            statement = statement.where(AnnotationTaskModel.id > after_id)
        statement = statement.order_by(AnnotationTaskModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        task: AnnotationTask,
        *,
        expected_version: int,
    ) -> AnnotationTask:
        scope.ensure(task.project_id)
        ensure_expected_version(task.version, expected_version)
        now = task.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(AnnotationTaskModel)
            .where(
                AnnotationTaskModel.id == task.task_id,
                AnnotationTaskModel.project_id == scope.project_id,
                AnnotationTaskModel.version == expected_version,
            )
            .values(
                name=task.name,
                description=task.description,
                status=enum_value(task.status),
                iaa_policy=dict(task.iaa_policy),
                quality_policy=dict(task.quality_policy),
                assignment_mode=task.assignment_mode.value,
                target_item_count=task.target_item_count,
                target_episode_count=task.target_episode_count,
                due_at=task.due_at,
                priority=task.priority.value,
                progress=dict(task.progress),
                quality_summary=dict(task.quality_summary),
                gold_set_id=task.gold_set_id,
                iaa_report_id=task.iaa_report_id,
                accepted_at=task.accepted_at,
                completed_at=task.completed_at,
                status_reason=task.status_reason,
                blocked_reason=task.blocked_reason,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                AnnotationTaskModel,
                scope,
                task.task_id,
                "标注任务",
            )
        return replace(
            task,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def list_for_sample_set(
        self,
        scope: ProjectScope,
        *,
        sample_set_id: UUID,
        active_only: bool = True,
    ) -> list[AnnotationTask]:
        statement = scoped_select(AnnotationTaskModel, scope).where(
            AnnotationTaskModel.sample_set_id == sample_set_id
        )
        if active_only:
            statement = statement.where(AnnotationTaskModel.status != "CANCELLED")
        statement = statement.order_by(AnnotationTaskModel.id)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def count_active_for_sample_set(
        self,
        scope: ProjectScope,
        *,
        sample_set_id: UUID,
    ) -> int:
        total = self._session.scalar(
            select(func.count())
            .select_from(AnnotationTaskModel)
            .where(
                AnnotationTaskModel.project_id == scope.project_id,
                AnnotationTaskModel.sample_set_id == sample_set_id,
                AnnotationTaskModel.status != "CANCELLED",
            )
        )
        return int(total or 0)

    def _from_row(self, row: AnnotationTaskModel) -> AnnotationTask:
        return AnnotationTask(
            task_id=row.id,
            project_id=row.project_id,
            code=row.code,
            name=row.name,
            description=row.description,
            status=row.status,
            sample_set_id=row.sample_set_id,
            schema_id=row.schema_id,
            schema_version=row.schema_version,
            schema_sha256=row.schema_sha256,
            gold_set_id=row.gold_set_id,
            iaa_policy=row.iaa_policy or {},
            quality_policy=row.quality_policy or {},
            assignment_mode=row.assignment_mode,
            target_item_count=row.target_item_count,
            target_episode_count=row.target_episode_count,
            due_at=as_utc(row.due_at),
            priority=row.priority,
            progress=row.progress or {},
            quality_summary=row.quality_summary or {},
            iaa_report_id=row.iaa_report_id,
            accepted_at=as_utc(row.accepted_at),
            completed_at=as_utc(row.completed_at),
            status_reason=row.status_reason,
            blocked_reason=row.blocked_reason,
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )
