"""DatasetGrant SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.datasets.dataset_grants import DatasetGrant
from app.domain.datasets.training_enums import DatasetGrantStatus
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.identity import ProjectMember
from app.infrastructure.db.models.training import DatasetGrantModel
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


class SqlDatasetGrantRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, grant: DatasetGrant) -> DatasetGrant:
        scope.ensure(grant.project_id)
        ensure_initial_version(grant.row_version)
        now = _now()
        row = DatasetGrantModel(
            id=grant.grant_id,
            project_id=grant.project_id,
            dataset_id=grant.dataset_id,
            dataset_version_id=grant.dataset_version_id,
            grantee_project_id=grant.grantee_project_id,
            grantee_team=grant.grantee_team,
            scope=list(grant.scope),
            status=enum_value(grant.status),
            reason=grant.reason,
            approved_by=grant.approved_by,
            approved_at=grant.approved_at,
            rejected_by=grant.rejected_by,
            rejected_at=grant.rejected_at,
            rejection_reason=grant.rejection_reason,
            expires_at=grant.expires_at,
            revoked_at=grant.revoked_at,
            revoked_by=grant.revoked_by,
            revoke_reason=grant.revoke_reason,
            version=1,
            created_at=grant.created_at or now,
            updated_at=grant.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session, "数据集授权冲突")
        return replace(grant, created_by=scope.actor_id, updated_by=scope.actor_id)

    def get(self, scope: ProjectScope, grant_id: UUID) -> DatasetGrant | None:
        row = self._session.scalar(
            select(DatasetGrantModel).where(
                DatasetGrantModel.id == grant_id,
                DatasetGrantModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        status: str | None = None,
        grantee_project_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DatasetGrant]:
        limit = bounded_limit(limit, maximum=101)
        statement = scoped_select(DatasetGrantModel, scope).where(
            DatasetGrantModel.dataset_id == dataset_id
        )
        if status is not None:
            statement = statement.where(DatasetGrantModel.status == status)
        if grantee_project_id is not None:
            statement = statement.where(DatasetGrantModel.grantee_project_id == grantee_project_id)
        if after_id is not None:
            statement = statement.where(DatasetGrantModel.id > after_id)
        statement = statement.order_by(DatasetGrantModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def find_for_user(
        self,
        *,
        project_id: UUID,
        dataset_id: UUID,
        user_id: UUID,
    ) -> list[DatasetGrant]:
        statement = (
            select(DatasetGrantModel)
            .join(
                ProjectMember,
                ProjectMember.project_id == DatasetGrantModel.grantee_project_id,
            )
            .where(
                DatasetGrantModel.project_id == project_id,
                DatasetGrantModel.dataset_id == dataset_id,
                ProjectMember.user_id == user_id,
            )
            .order_by(DatasetGrantModel.created_at)
        )
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        grant: DatasetGrant,
        *,
        expected_version: int,
    ) -> DatasetGrant:
        scope.ensure(grant.project_id)
        ensure_expected_version(grant.row_version, expected_version)
        now = _now()
        result = self._session.execute(
            update(DatasetGrantModel)
            .where(
                DatasetGrantModel.id == grant.grant_id,
                DatasetGrantModel.project_id == scope.project_id,
                DatasetGrantModel.version == expected_version,
            )
            .values(
                status=enum_value(grant.status),
                approved_by=grant.approved_by,
                approved_at=grant.approved_at,
                rejected_by=grant.rejected_by,
                rejected_at=grant.rejected_at,
                rejection_reason=grant.rejection_reason,
                revoked_at=grant.revoked_at,
                revoked_by=grant.revoked_by,
                revoke_reason=grant.revoke_reason,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                DatasetGrantModel,
                scope,
                grant.grant_id,
                "数据集授权",
                expected_version,
            )
        return replace(
            grant,
            row_version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: DatasetGrantModel) -> DatasetGrant:
        return DatasetGrant(
            grant_id=row.id,
            project_id=row.project_id,
            dataset_id=row.dataset_id,
            dataset_version_id=row.dataset_version_id,
            grantee_project_id=row.grantee_project_id,
            grantee_team=row.grantee_team,
            scope=list(row.scope or []),
            status=DatasetGrantStatus(row.status),
            reason=row.reason,
            approved_by=row.approved_by,
            approved_at=as_utc(row.approved_at),
            rejected_by=row.rejected_by,
            rejected_at=as_utc(row.rejected_at),
            rejection_reason=row.rejection_reason,
            expires_at=as_utc(row.expires_at) or _now(),
            revoked_at=as_utc(row.revoked_at),
            revoked_by=row.revoked_by,
            revoke_reason=row.revoke_reason,
            row_version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )
