"""ProcessingRun SQL 仓储。

所有查询强制项目作用域；``save`` 用 ``WHERE version = expected_version``
做条件更新，行数为 0 即抛 ``version_conflict``，不做"读后覆盖"。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.domain.processing.errors import VersionConflictError
from app.domain.processing.models import ProcessingRun
from app.infrastructure.db.models.processing import ProcessingRunModel


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


def _run(row: ProcessingRunModel) -> ProcessingRun:
    return ProcessingRun(
        run_id=row.id,
        project_id=row.project_id,
        pipeline_version=row.pipeline_version,
        input_refs=tuple(row.input_refs or ()),
        output_refs=tuple(row.output_refs or ()),
        status=row.status,
        stage=row.stage,
        attempt=row.attempt,
        retry_count=row.retry_count,
        error_code=row.error_code,
        error_summary=row.error_summary,
        started_at=_as_utc(row.started_at),
        finished_at=_as_utc(row.finished_at),
        row_version=row.version,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


class SqlProcessingRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, run: ProcessingRun) -> ProcessingRun:
        scope.ensure(run.project_id)
        self._session.add(
            ProcessingRunModel(
                id=run.run_id,
                project_id=run.project_id,
                pipeline_version=run.pipeline_version,
                input_refs=list(run.input_refs),
                output_refs=list(run.output_refs),
                status=run.status,
                stage=run.stage,
                attempt=run.attempt,
                retry_count=run.retry_count,
                error_code=run.error_code,
                error_summary=run.error_summary,
                started_at=run.started_at,
                finished_at=run.finished_at,
                version=run.row_version,
                created_at=run.created_at,
                updated_at=run.updated_at,
                created_by=run.created_by,
                updated_by=run.updated_by,
            )
        )
        self._session.flush()
        return run

    def get(self, scope: ProjectScope, run_id: UUID) -> ProcessingRun | None:
        row = self._session.get(ProcessingRunModel, run_id)
        if row is None or row.project_id != scope.project_id:
            return None
        return _run(row)

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        stage: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ProcessingRun]:
        stmt = select(ProcessingRunModel).where(ProcessingRunModel.project_id == scope.project_id)
        if status is not None:
            stmt = stmt.where(ProcessingRunModel.status == status)
        if stage is not None:
            stmt = stmt.where(ProcessingRunModel.stage == stage)
        if after_id is not None:
            stmt = stmt.where(ProcessingRunModel.id > after_id)
        rows = self._session.scalars(stmt.order_by(ProcessingRunModel.id).limit(limit)).all()
        return [_run(row) for row in rows]

    def save(
        self,
        scope: ProjectScope,
        run: ProcessingRun,
        *,
        expected_version: int,
    ) -> ProcessingRun:
        scope.ensure(run.project_id)
        result = self._session.execute(
            update(ProcessingRunModel)
            .where(
                ProcessingRunModel.id == run.run_id,
                ProcessingRunModel.project_id == scope.project_id,
                ProcessingRunModel.version == expected_version,
            )
            .values(
                status=run.status,
                stage=run.stage,
                output_refs=list(run.output_refs),
                attempt=run.attempt,
                retry_count=run.retry_count,
                error_code=run.error_code,
                error_summary=run.error_summary,
                started_at=run.started_at,
                finished_at=run.finished_at,
                version=expected_version + 1,
                updated_at=run.updated_at,
                updated_by=run.updated_by,
            )
        )
        if result.rowcount != 1:
            raise VersionConflictError()
        self._session.flush()
        refreshed = self.get(scope, run.run_id)
        if refreshed is None:
            raise VersionConflictError()
        return refreshed


__all__ = ["SqlProcessingRunRepository"]
