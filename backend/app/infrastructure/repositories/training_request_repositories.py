"""TrainingRequest / Revision SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.datasets.training_enums import (
    RevisionStatus,
    TrainingPriority,
    TrainingRequestStatus,
)
from app.domain.datasets.training_requests import TrainingRequest, TrainingRequestRevision
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.training import (
    TrainingRequestModel,
    TrainingRequestRevisionModel,
)
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


class SqlTrainingRequestRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, request: TrainingRequest) -> TrainingRequest:
        scope.ensure(request.project_id)
        ensure_initial_version(request.row_version)
        now = _now()
        row = TrainingRequestModel(
            id=request.training_request_id,
            project_id=request.project_id,
            code=request.code,
            title=request.title,
            status=enum_value(request.status),
            current_revision_id=request.current_revision_id,
            current_revision_no=request.current_revision_no,
            priority=enum_value(request.priority),
            owner_id=request.owner_id,
            blocked_reason=request.blocked_reason,
            blocked_from_status=request.blocked_from_status,
            cancel_reason=request.cancel_reason,
            closed_at=request.closed_at,
            version=1,
            created_at=request.created_at or now,
            updated_at=request.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session, "项目内训练需求编码重复")
        return replace(request, created_by=scope.actor_id, updated_by=scope.actor_id)

    def get(self, scope: ProjectScope, request_id: UUID) -> TrainingRequest | None:
        row = self._session.scalar(
            select(TrainingRequestModel).where(
                TrainingRequestModel.id == request_id,
                TrainingRequestModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_code(self, scope: ProjectScope, code: str) -> TrainingRequest | None:
        row = self._session.scalar(
            select(TrainingRequestModel).where(
                TrainingRequestModel.project_id == scope.project_id,
                TrainingRequestModel.code == code,
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        priority: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[TrainingRequest]:
        limit = bounded_limit(limit, maximum=101)
        statement = scoped_select(TrainingRequestModel, scope)
        if status is not None:
            statement = statement.where(TrainingRequestModel.status == status)
        if priority is not None:
            statement = statement.where(TrainingRequestModel.priority == priority)
        if after_id is not None:
            statement = statement.where(TrainingRequestModel.id > after_id)
        statement = statement.order_by(TrainingRequestModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        request: TrainingRequest,
        *,
        expected_version: int,
    ) -> TrainingRequest:
        scope.ensure(request.project_id)
        ensure_expected_version(request.row_version, expected_version)
        now = _now()
        result = self._session.execute(
            update(TrainingRequestModel)
            .where(
                TrainingRequestModel.id == request.training_request_id,
                TrainingRequestModel.project_id == scope.project_id,
                TrainingRequestModel.version == expected_version,
            )
            .values(
                status=enum_value(request.status),
                current_revision_id=request.current_revision_id,
                current_revision_no=request.current_revision_no,
                priority=enum_value(request.priority),
                blocked_reason=request.blocked_reason,
                blocked_from_status=request.blocked_from_status,
                cancel_reason=request.cancel_reason,
                closed_at=request.closed_at,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                TrainingRequestModel,
                scope,
                request.training_request_id,
                "训练需求",
                expected_version,
            )
        return replace(
            request,
            row_version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: TrainingRequestModel) -> TrainingRequest:
        return TrainingRequest(
            training_request_id=row.id,
            project_id=row.project_id,
            code=row.code,
            title=row.title,
            status=TrainingRequestStatus(row.status),
            current_revision_id=row.current_revision_id,
            current_revision_no=row.current_revision_no,
            priority=TrainingPriority(row.priority),
            owner_id=row.owner_id,
            blocked_reason=row.blocked_reason,
            blocked_from_status=row.blocked_from_status,
            cancel_reason=row.cancel_reason,
            closed_at=as_utc(row.closed_at),
            row_version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )


class SqlTrainingRequestRevisionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        scope: ProjectScope,
        revision: TrainingRequestRevision,
    ) -> TrainingRequestRevision:
        scope.ensure(revision.project_id)
        ensure_initial_version(revision.row_version)
        now = _now()
        row = TrainingRequestRevisionModel(
            id=revision.revision_id,
            project_id=revision.project_id,
            training_request_id=revision.training_request_id,
            revision_no=revision.revision_no,
            status=enum_value(revision.status),
            snapshot=dict(revision.snapshot),
            change_reason=revision.change_reason,
            supersedes_revision_id=revision.supersedes_revision_id,
            submitted_by=revision.submitted_by,
            submitted_at=revision.submitted_at,
            reviewed_by=revision.reviewed_by,
            reviewed_at=revision.reviewed_at,
            approved_by=revision.approved_by,
            approved_at=revision.approved_at,
            rejected_by=revision.rejected_by,
            rejected_at=revision.rejected_at,
            rejection_reason=revision.rejection_reason,
            revision_sha256=revision.revision_sha256 or "",
            version=1,
            created_at=revision.created_at or now,
            created_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session, "需求 revision 序号冲突")
        return replace(revision, created_by=scope.actor_id)

    def get(
        self,
        scope: ProjectScope,
        revision_id: UUID,
    ) -> TrainingRequestRevision | None:
        row = self._session.scalar(
            select(TrainingRequestRevisionModel).where(
                TrainingRequestRevisionModel.id == revision_id,
                TrainingRequestRevisionModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_for_request(
        self,
        scope: ProjectScope,
        *,
        request_id: UUID,
        revision_id: UUID,
    ) -> TrainingRequestRevision | None:
        row = self._session.scalar(
            select(TrainingRequestRevisionModel).where(
                TrainingRequestRevisionModel.id == revision_id,
                TrainingRequestRevisionModel.project_id == scope.project_id,
                TrainingRequestRevisionModel.training_request_id == request_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        request_id: UUID,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[TrainingRequestRevision]:
        limit = bounded_limit(limit, maximum=101)
        statement = scoped_select(TrainingRequestRevisionModel, scope).where(
            TrainingRequestRevisionModel.training_request_id == request_id
        )
        if status is not None:
            statement = statement.where(TrainingRequestRevisionModel.status == status)
        if after_id is not None:
            statement = statement.where(TrainingRequestRevisionModel.id > after_id)
        statement = statement.order_by(TrainingRequestRevisionModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        revision: TrainingRequestRevision,
        *,
        expected_version: int,
    ) -> TrainingRequestRevision:
        scope.ensure(revision.project_id)
        ensure_expected_version(revision.row_version, expected_version)
        result = self._session.execute(
            update(TrainingRequestRevisionModel)
            .where(
                TrainingRequestRevisionModel.id == revision.revision_id,
                TrainingRequestRevisionModel.project_id == scope.project_id,
                TrainingRequestRevisionModel.version == expected_version,
            )
            .values(
                status=enum_value(revision.status),
                submitted_by=revision.submitted_by,
                submitted_at=revision.submitted_at,
                reviewed_by=revision.reviewed_by,
                reviewed_at=revision.reviewed_at,
                approved_by=revision.approved_by,
                approved_at=revision.approved_at,
                rejected_by=revision.rejected_by,
                rejected_at=revision.rejected_at,
                rejection_reason=revision.rejection_reason,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                TrainingRequestRevisionModel,
                scope,
                revision.revision_id,
                "需求修订",
                expected_version,
            )
        return replace(revision, row_version=expected_version + 1)

    def _from_row(self, row: TrainingRequestRevisionModel) -> TrainingRequestRevision:
        return TrainingRequestRevision(
            revision_id=row.id,
            project_id=row.project_id,
            training_request_id=row.training_request_id,
            revision_no=row.revision_no,
            status=RevisionStatus(row.status),
            snapshot=dict(row.snapshot or {}),
            change_reason=row.change_reason,
            supersedes_revision_id=row.supersedes_revision_id,
            submitted_by=row.submitted_by,
            submitted_at=as_utc(row.submitted_at),
            reviewed_by=row.reviewed_by,
            reviewed_at=as_utc(row.reviewed_at),
            approved_by=row.approved_by,
            approved_at=as_utc(row.approved_at),
            rejected_by=row.rejected_by,
            rejected_at=as_utc(row.rejected_at),
            rejection_reason=row.rejection_reason,
            revision_sha256=row.revision_sha256,
            row_version=row.version,
            created_at=as_utc(row.created_at),
            created_by=row.created_by,
        )
