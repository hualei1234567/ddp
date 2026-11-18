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
from app.domain.processing.steps import ProcessingStep
from app.infrastructure.db.models.processing import (
    ProcessingRunModel,
    ProcessingRunStepModel,
)


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


class SqlProcessingRunStepRepository:
    """步骤仓储：项目作用域强制下推，``(run, stage, attempt)`` 单行语义。

    ``upsert`` 不做"读后覆盖"的跨请求缓存，每次都在同一事务内按唯一键定位；
    更新只改结果字段，不移动 ``step_id`` / ``run_id`` / ``attempt``，历史行
    因此始终可追溯。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_key(
        self,
        scope: ProjectScope,
        *,
        run_id: UUID,
        stage: str,
        attempt: int,
    ) -> ProcessingStep | None:
        row = self._session.scalar(
            select(ProcessingRunStepModel).where(
                ProcessingRunStepModel.project_id == scope.project_id,
                ProcessingRunStepModel.run_id == run_id,
                ProcessingRunStepModel.stage == stage,
                ProcessingRunStepModel.attempt == attempt,
            )
        )
        return None if row is None else _step(row)

    def upsert(self, scope: ProjectScope, step: ProcessingStep) -> ProcessingStep:
        scope.ensure(step.project_id)
        row = self._session.scalar(
            select(ProcessingRunStepModel).where(
                ProcessingRunStepModel.run_id == step.run_id,
                ProcessingRunStepModel.stage == step.stage,
                ProcessingRunStepModel.attempt == step.attempt,
            )
        )
        if row is None:
            self._session.add(
                ProcessingRunStepModel(
                    id=step.step_id,
                    project_id=step.project_id,
                    run_id=step.run_id,
                    stage=step.stage,
                    attempt=step.attempt,
                    status=step.status,
                    log_summary=step.log_summary,
                    error_code=step.error_code,
                    error_summary=step.error_summary,
                    started_at=step.started_at,
                    finished_at=step.finished_at,
                    created_at=step.created_at,
                    updated_at=step.updated_at,
                    created_by=step.created_by,
                    updated_by=step.updated_by,
                )
            )
        else:
            # 纵深防御：同一 (run, stage, attempt) 若属于别的项目，拒绝写入，
            # 而不是把当前项目的步骤覆盖到别家运行上。
            scope.ensure(row.project_id)
            row.status = step.status
            row.log_summary = step.log_summary
            row.error_code = step.error_code
            row.error_summary = step.error_summary
            row.started_at = step.started_at
            row.finished_at = step.finished_at
            row.updated_at = step.updated_at
            row.updated_by = step.updated_by
        self._session.flush()
        return step

    def list(
        self,
        scope: ProjectScope,
        *,
        run_id: UUID,
        after_id: UUID | None = None,
        limit: int = 100,
    ) -> list[ProcessingStep]:
        stmt = select(ProcessingRunStepModel).where(
            ProcessingRunStepModel.project_id == scope.project_id,
            ProcessingRunStepModel.run_id == run_id,
        )
        if after_id is not None:
            stmt = stmt.where(ProcessingRunStepModel.id > after_id)
        rows = self._session.scalars(stmt.order_by(ProcessingRunStepModel.id).limit(limit)).all()
        return [_step(row) for row in rows]


def _step(row: ProcessingRunStepModel) -> ProcessingStep:
    return ProcessingStep(
        step_id=row.id,
        project_id=row.project_id,
        run_id=row.run_id,
        stage=row.stage,
        attempt=row.attempt,
        status=row.status,
        log_summary=row.log_summary,
        error_code=row.error_code,
        error_summary=row.error_summary,
        started_at=_as_utc(row.started_at),
        finished_at=_as_utc(row.finished_at),
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


__all__ = ["SqlProcessingRunRepository", "SqlProcessingRunStepRepository"]
