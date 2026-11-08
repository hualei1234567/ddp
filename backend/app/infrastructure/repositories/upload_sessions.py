"""上传会话与分片 SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import RawAssetFormat, UploadSessionStatus, UploadVerificationStatus
from app.domain.ingestion.errors import (
    DomainValidationError,
    ResourceNotFoundError,
    UploadPartConflictError,
    VersionConflictError,
)
from app.domain.ingestion.models import UploadPart, UploadSession
from app.infrastructure.db.models.ingestion import UploadPartModel, UploadSessionModel
from app.infrastructure.repositories._collection_sql import as_utc


class SqlUploadSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, session: UploadSession) -> UploadSession:
        scope.ensure(session.project_id)
        if session.version != 1:
            raise DomainValidationError("新增上传会话 version 必须为 1")
        now = datetime.now(UTC)
        row = UploadSessionModel(
            id=session.upload_session_id,
            project_id=session.project_id,
            collection_task_id=session.collection_task_id,
            batch_id=session.batch_id,
            status=session.status.value,
            verification_status=session.verification_status.value,
            verification_error_code=session.verification_error_code,
            verified_at=session.verified_at,
            original_filename=session.original_filename,
            declared_content_type=session.declared_content_type,
            declared_size_bytes=session.declared_size_bytes,
            expected_sha256=session.expected_sha256,
            format_hint=session.format_hint.value if session.format_hint else None,
            part_size_bytes=session.part_size_bytes,
            max_size_bytes=session.max_size_bytes,
            allowed_content_types=list(session.allowed_content_types),
            object_key=session.object_key,
            multipart_upload_id=session.multipart_upload_id,
            expires_at=session.expires_at,
            completed_at=session.completed_at,
            aborted_at=session.aborted_at,
            failure_code=session.failure_code,
            failure_summary=session.failure_summary,
            uploaded_bytes=session.uploaded_bytes,
            uploaded_part_count=session.uploaded_part_count,
            deduplicated=session.deduplicated,
            deduplicated_asset_id=session.deduplicated_asset_id,
            asset_id=session.asset_id,
            validation_job_id=session.validation_job_id,
            version=session.version,
            created_at=session.created_at or now,
            updated_at=session.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        self._session.flush()
        return session

    def get(self, scope: ProjectScope, session_id: UUID) -> UploadSession | None:
        row = self._session.scalar(
            select(UploadSessionModel).where(
                UploadSessionModel.id == session_id,
                UploadSessionModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def save(
        self,
        scope: ProjectScope,
        session: UploadSession,
        *,
        expected_version: int,
    ) -> UploadSession:
        scope.ensure(session.project_id)
        if expected_version < 1:
            raise DomainValidationError("expected_version 必须大于等于 1")
        if session.version != expected_version:
            raise VersionConflictError("上传会话")
        now = session.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(UploadSessionModel)
            .where(
                UploadSessionModel.id == session.upload_session_id,
                UploadSessionModel.project_id == scope.project_id,
                UploadSessionModel.version == expected_version,
            )
            .values(
                status=session.status.value,
                verification_status=session.verification_status.value,
                verification_error_code=session.verification_error_code,
                verified_at=session.verified_at,
                completed_at=session.completed_at,
                aborted_at=session.aborted_at,
                failure_code=session.failure_code,
                failure_summary=session.failure_summary,
                uploaded_bytes=session.uploaded_bytes,
                uploaded_part_count=session.uploaded_part_count,
                deduplicated=session.deduplicated,
                deduplicated_asset_id=session.deduplicated_asset_id,
                asset_id=session.asset_id,
                validation_job_id=session.validation_job_id,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise _save_failure(self._session, scope, session.upload_session_id)
        return replace(
            session,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def set_progress(
        self,
        scope: ProjectScope,
        session_id: UUID,
        *,
        uploaded_bytes: int,
        uploaded_part_count: int,
        status: str | None = None,
        now: datetime,
    ) -> UploadSession:
        values: dict[str, object] = {
            "uploaded_bytes": uploaded_bytes,
            "uploaded_part_count": uploaded_part_count,
            "updated_at": now,
            "updated_by": scope.actor_id,
            "version": UploadSessionModel.version + 1,
        }
        if status is not None:
            values["status"] = status
        result = self._session.execute(
            update(UploadSessionModel)
            .where(
                UploadSessionModel.id == session_id,
                UploadSessionModel.project_id == scope.project_id,
            )
            .values(**values)
        )
        if result.rowcount == 0:
            raise ResourceNotFoundError("上传会话", session_id)
        self._session.flush()
        updated = self.get(scope, session_id)
        if updated is None:
            raise ResourceNotFoundError("上传会话", session_id)
        return updated

    def _from_row(self, row: UploadSessionModel) -> UploadSession:
        format_hint = RawAssetFormat(row.format_hint) if row.format_hint else None
        return UploadSession(
            upload_session_id=row.id,
            project_id=row.project_id,
            collection_task_id=row.collection_task_id,
            batch_id=row.batch_id,
            status=UploadSessionStatus(row.status),
            verification_status=UploadVerificationStatus(row.verification_status),
            verification_error_code=row.verification_error_code,
            verified_at=as_utc(row.verified_at),
            original_filename=row.original_filename,
            declared_content_type=row.declared_content_type,
            declared_size_bytes=row.declared_size_bytes,
            expected_sha256=row.expected_sha256,
            format_hint=format_hint,
            part_size_bytes=row.part_size_bytes,
            max_size_bytes=row.max_size_bytes,
            allowed_content_types=tuple(row.allowed_content_types or ()),
            object_key=row.object_key,
            multipart_upload_id=row.multipart_upload_id,
            expires_at=as_utc(row.expires_at),
            completed_at=as_utc(row.completed_at),
            aborted_at=as_utc(row.aborted_at),
            failure_code=row.failure_code,
            failure_summary=row.failure_summary,
            uploaded_bytes=row.uploaded_bytes,
            uploaded_part_count=row.uploaded_part_count,
            deduplicated=row.deduplicated,
            deduplicated_asset_id=row.deduplicated_asset_id,
            asset_id=row.asset_id,
            validation_job_id=row.validation_job_id,
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )


class SqlUploadPartRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, part: UploadPart) -> UploadPart:
        scope.ensure(part.project_id)
        if part.version != 1:
            raise DomainValidationError("新增分片 version 必须为 1")
        row = UploadPartModel(
            project_id=part.project_id,
            upload_session_id=part.upload_session_id,
            part_number=part.part_number,
            size_bytes=part.size_bytes,
            etag=part.etag,
            sha256=part.sha256,
            recorded_at=part.recorded_at,
            version=part.version,
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError as exc:
            message = str(exc.orig).lower()
            if any(token in message for token in ("unique", "duplicate", "pk_")):
                raise UploadPartConflictError("同分片号已存在") from exc
            raise
        return part

    def get(
        self,
        scope: ProjectScope,
        session_id: UUID,
        part_number: int,
    ) -> UploadPart | None:
        row = self._session.scalar(
            select(UploadPartModel).where(
                UploadPartModel.project_id == scope.project_id,
                UploadPartModel.upload_session_id == session_id,
                UploadPartModel.part_number == part_number,
            )
        )
        return self._from_row(row) if row is not None else None

    def list(self, scope: ProjectScope, session_id: UUID) -> list[UploadPart]:
        rows = self._session.scalars(
            select(UploadPartModel)
            .where(
                UploadPartModel.project_id == scope.project_id,
                UploadPartModel.upload_session_id == session_id,
            )
            .order_by(UploadPartModel.part_number)
        )
        return [self._from_row(row) for row in rows]

    def _from_row(self, row: UploadPartModel) -> UploadPart:
        return UploadPart(
            project_id=row.project_id,
            upload_session_id=row.upload_session_id,
            part_number=row.part_number,
            size_bytes=row.size_bytes,
            etag=row.etag,
            sha256=row.sha256,
            recorded_at=as_utc(row.recorded_at),
            version=row.version,
        )


def _save_failure(
    session: Session,
    scope: ProjectScope,
    session_id: UUID,
) -> Exception:
    row = session.scalar(
        select(UploadSessionModel.id).where(
            UploadSessionModel.id == session_id,
            UploadSessionModel.project_id == scope.project_id,
        )
    )
    if row is None:
        return ResourceNotFoundError("上传会话", session_id)
    return VersionConflictError("上传会话")
