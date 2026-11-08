"""IngestionJob SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import IngestionJobStatus, IngestionJobType
from app.domain.ingestion.errors import ConflictError, DomainValidationError, VersionConflictError
from app.domain.ingestion.jobs import IngestionJob
from app.infrastructure.db.models.jobs import IngestionJobModel
from app.infrastructure.repositories._collection_sql import as_utc


class SqlIngestionJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, job: IngestionJob) -> IngestionJob:
        scope.ensure(job.project_id)
        if job.version != 1:
            raise DomainValidationError("新增接入任务 version 必须为 1")
        now = datetime.now(UTC)
        row = IngestionJobModel(
            id=job.job_id,
            project_id=job.project_id,
            job_type=job.job_type.value,
            raw_asset_id=job.raw_asset_id,
            raw_asset_ids=[str(item) for item in job.raw_asset_ids],
            input_manifest_key=job.input_manifest_key,
            status=job.status.value,
            idempotency_key=job.idempotency_key,
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            timeout_sec=job.timeout_sec,
            pipeline_id=job.pipeline_id,
            pipeline_version=job.pipeline_version,
            parser_id=job.parser_id,
            parser_version=job.parser_version,
            config_hash=job.config_hash,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error_code=job.error_code,
            error_summary=job.error_summary,
            output_refs=[dict(item) for item in job.output_refs],
            log_ref=job.log_ref,
            metrics=dict(job.metrics),
            version=job.version,
            created_at=job.created_at or now,
            updated_at=job.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError as exc:
            message = str(exc.orig).lower()
            if any(token in message for token in ("unique", "duplicate", "uq_")):
                raise ConflictError("同项目幂等键或任务 ID 已存在") from exc
            raise
        return job

    def get(self, scope: ProjectScope, job_id: UUID) -> IngestionJob | None:
        row = self._session.scalar(
            select(IngestionJobModel).where(
                IngestionJobModel.id == job_id,
                IngestionJobModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_idempotency_key(
        self,
        scope: ProjectScope,
        key: str,
    ) -> IngestionJob | None:
        row = self._session.scalar(
            select(IngestionJobModel).where(
                IngestionJobModel.project_id == scope.project_id,
                IngestionJobModel.idempotency_key == key,
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        status: IngestionJobStatus | None = None,
        raw_asset_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[IngestionJob]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = select(IngestionJobModel).where(
            IngestionJobModel.project_id == scope.project_id
        )
        if status is not None:
            statement = statement.where(IngestionJobModel.status == status.value)
        if raw_asset_id is not None:
            statement = statement.where(IngestionJobModel.raw_asset_id == raw_asset_id)
        if after_id is not None:
            statement = statement.where(IngestionJobModel.id > after_id)
        statement = statement.order_by(IngestionJobModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        job: IngestionJob,
        *,
        expected_version: int,
    ) -> IngestionJob:
        scope.ensure(job.project_id)
        if job.version != expected_version:
            raise VersionConflictError("接入任务")
        now = job.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(IngestionJobModel)
            .where(
                IngestionJobModel.id == job.job_id,
                IngestionJobModel.project_id == scope.project_id,
                IngestionJobModel.version == expected_version,
            )
            .values(
                status=job.status.value,
                attempt=job.attempt,
                started_at=job.started_at,
                finished_at=job.finished_at,
                error_code=job.error_code,
                error_summary=job.error_summary,
                output_refs=[dict(item) for item in job.output_refs],
                log_ref=job.log_ref,
                metrics=dict(job.metrics),
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise VersionConflictError("接入任务")
        return replace(
            job,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: IngestionJobModel) -> IngestionJob:
        return IngestionJob(
            job_id=row.id,
            project_id=row.project_id,
            job_type=IngestionJobType(row.job_type),
            raw_asset_ids=tuple(UUID(item) for item in (row.raw_asset_ids or ())),
            raw_asset_id=row.raw_asset_id,
            input_manifest_key=row.input_manifest_key,
            status=IngestionJobStatus(row.status),
            idempotency_key=row.idempotency_key,
            attempt=row.attempt,
            max_attempts=row.max_attempts,
            timeout_sec=row.timeout_sec,
            pipeline_id=row.pipeline_id,
            pipeline_version=row.pipeline_version,
            parser_id=row.parser_id,
            parser_version=row.parser_version,
            config_hash=row.config_hash,
            started_at=as_utc(row.started_at),
            finished_at=as_utc(row.finished_at),
            error_code=row.error_code,
            error_summary=row.error_summary,
            output_refs=tuple(row.output_refs or ()),
            log_ref=row.log_ref,
            metrics=dict(row.metrics or {}),
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )
