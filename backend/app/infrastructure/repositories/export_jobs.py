"""ExportJob SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.datasets.enums import (
    ExportFormat,
    ExportJobStatus,
    ExportVerificationStatus,
)
from app.domain.datasets.export_jobs import ExportJob
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.export_jobs import ExportJobModel
from app.infrastructure.repositories._dataset_sql import (
    as_utc,
    bounded_limit,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    raise_update_failure,
    scoped_select,
)


def _now() -> datetime:
    return datetime.now(UTC)


class SqlExportJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, job: ExportJob) -> ExportJob:
        scope.ensure(job.project_id)
        ensure_initial_version(job.row_version)
        now = _now()
        row = ExportJobModel(
            id=job.export_job_id,
            project_id=job.project_id,
            dataset_version_id=job.dataset_version_id,
            status=enum_value(job.status),
            format=enum_value(job.format),
            format_version=job.format_version,
            exporter_id=job.exporter_id,
            exporter_version=job.exporter_version,
            mapping_version=job.mapping_version,
            config=dict(job.config),
            config_sha256=job.config_sha256,
            idempotency_key=job.idempotency_key,
            requested_by=job.requested_by,
            output_prefix=job.output_prefix,
            artifact_count=job.artifact_count,
            total_bytes=job.total_bytes,
            artifact_manifest_key=job.artifact_manifest_key,
            artifact_manifest_sha256=job.artifact_manifest_sha256,
            aggregate_sha256=job.aggregate_sha256,
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            timeout_seconds=job.timeout_seconds,
            started_at=job.started_at,
            finished_at=job.finished_at,
            cancel_requested_at=job.cancel_requested_at,
            error_code=job.error_code,
            error_summary=job.error_summary,
            log_ref=job.log_ref,
            verification_status=enum_value(job.verification_status),
            verification_evidence_ref=job.verification_evidence_ref,
            version=1,
            created_at=job.created_at or now,
            updated_at=job.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session, "导出任务幂等键冲突")
        return replace(job, created_by=scope.actor_id, updated_by=scope.actor_id)

    def get(self, scope: ProjectScope, job_id: UUID) -> ExportJob | None:
        row = self._session.scalar(
            select(ExportJobModel).where(
                ExportJobModel.id == job_id,
                ExportJobModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_idempotency_key(
        self,
        scope: ProjectScope,
        idempotency_key: str,
    ) -> ExportJob | None:
        row = self._session.scalar(
            select(ExportJobModel).where(
                ExportJobModel.project_id == scope.project_id,
                ExportJobModel.requested_by == scope.actor_id,
                ExportJobModel.idempotency_key == idempotency_key,
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        dataset_version_id: UUID | None = None,
        status: str | None = None,
        format: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ExportJob]:
        limit = bounded_limit(limit, maximum=101)
        statement = scoped_select(ExportJobModel, scope)
        if dataset_version_id is not None:
            statement = statement.where(ExportJobModel.dataset_version_id == dataset_version_id)
        if status is not None:
            statement = statement.where(ExportJobModel.status == status)
        if format is not None:
            statement = statement.where(ExportJobModel.format == format)
        if after_id is not None:
            statement = statement.where(ExportJobModel.id > after_id)
        statement = statement.order_by(ExportJobModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        job: ExportJob,
        *,
        expected_version: int,
    ) -> ExportJob:
        scope.ensure(job.project_id)
        ensure_expected_version(job.row_version, expected_version)
        now = _now()
        result = self._session.execute(
            update(ExportJobModel)
            .where(
                ExportJobModel.id == job.export_job_id,
                ExportJobModel.project_id == scope.project_id,
                ExportJobModel.version == expected_version,
            )
            .values(
                status=enum_value(job.status),
                artifact_count=job.artifact_count,
                total_bytes=job.total_bytes,
                artifact_manifest_key=job.artifact_manifest_key,
                artifact_manifest_sha256=job.artifact_manifest_sha256,
                aggregate_sha256=job.aggregate_sha256,
                attempt=job.attempt,
                started_at=job.started_at,
                finished_at=job.finished_at,
                cancel_requested_at=job.cancel_requested_at,
                error_code=job.error_code,
                error_summary=job.error_summary,
                log_ref=job.log_ref,
                verification_status=enum_value(job.verification_status),
                verification_evidence_ref=job.verification_evidence_ref,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                ExportJobModel,
                scope,
                job.export_job_id,
                "导出任务",
                expected_version,
            )
        return replace(
            job,
            row_version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: ExportJobModel) -> ExportJob:
        return ExportJob(
            export_job_id=row.id,
            project_id=row.project_id,
            dataset_version_id=row.dataset_version_id,
            status=ExportJobStatus(row.status),
            format=ExportFormat(row.format),
            format_version=row.format_version,
            exporter_id=row.exporter_id,
            exporter_version=row.exporter_version,
            mapping_version=row.mapping_version,
            config=dict(row.config or {}),
            config_sha256=row.config_sha256,
            idempotency_key=row.idempotency_key,
            requested_by=row.requested_by,
            output_prefix=row.output_prefix,
            artifact_count=row.artifact_count,
            total_bytes=row.total_bytes,
            artifact_manifest_key=row.artifact_manifest_key,
            artifact_manifest_sha256=row.artifact_manifest_sha256,
            aggregate_sha256=row.aggregate_sha256,
            attempt=row.attempt,
            max_attempts=row.max_attempts,
            timeout_seconds=row.timeout_seconds,
            started_at=as_utc(row.started_at),
            finished_at=as_utc(row.finished_at),
            cancel_requested_at=as_utc(row.cancel_requested_at),
            error_code=row.error_code,
            error_summary=row.error_summary,
            log_ref=row.log_ref,
            verification_status=ExportVerificationStatus(row.verification_status),
            verification_evidence_ref=row.verification_evidence_ref,
            row_version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )
