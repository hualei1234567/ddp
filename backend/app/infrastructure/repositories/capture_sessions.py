"""采集会话 SQLAlchemy 仓储。

所有读取都带 ``project_id`` 过滤；按 ID 查不到（含跨项目）返回 ``None``，
由用例统一映射 404，不泄漏"该 ID 在别的项目存在"。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.collection.enums import CaptureSessionStatus
from app.domain.collection.errors import DomainValidationError
from app.domain.collection.models import CaptureSession
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.capture_session import CaptureSessionModel
from app.infrastructure.repositories._collection_sql import (
    as_utc,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    raise_update_failure,
    scoped_select,
)


class SqlCaptureSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, session: CaptureSession) -> CaptureSession:
        scope.ensure(session.project_id)
        ensure_initial_version(session.version)
        now = datetime.now(UTC)
        row = CaptureSessionModel(
            id=session.session_id,
            project_id=session.project_id,
            code=session.code,
            task_id=session.task_id,
            batch_id=session.batch_id,
            operator_id=session.operator_id,
            robot_id=session.robot_id,
            location_id=session.location_id,
            status=enum_value(session.status),
            started_at=session.started_at,
            ended_at=session.ended_at,
            status_reason=session.status_reason,
            created_at=session.created_at or now,
            updated_at=session.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session)
        return session

    def get(self, scope: ProjectScope, session_id: UUID) -> CaptureSession | None:
        row = self._session.scalar(
            select(CaptureSessionModel).where(
                CaptureSessionModel.id == session_id,
                CaptureSessionModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_code(self, scope: ProjectScope, code: str) -> CaptureSession | None:
        row = self._session.scalar(
            select(CaptureSessionModel).where(
                CaptureSessionModel.code == code.strip(),
                CaptureSessionModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        status: CaptureSessionStatus | None = None,
        task_id: UUID | None = None,
        batch_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[CaptureSession]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = scoped_select(CaptureSessionModel, scope)
        if status is not None:
            statement = statement.where(CaptureSessionModel.status == status.value)
        if task_id is not None:
            statement = statement.where(CaptureSessionModel.task_id == task_id)
        if batch_id is not None:
            statement = statement.where(CaptureSessionModel.batch_id == batch_id)
        if after_id is not None:
            statement = statement.where(CaptureSessionModel.id > after_id)
        statement = statement.order_by(CaptureSessionModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        session: CaptureSession,
        *,
        expected_version: int,
    ) -> CaptureSession:
        scope.ensure(session.project_id)
        ensure_expected_version(session.version, expected_version)
        now = session.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(CaptureSessionModel)
            .where(
                CaptureSessionModel.id == session.session_id,
                CaptureSessionModel.project_id == scope.project_id,
                CaptureSessionModel.version == expected_version,
            )
            .values(
                code=session.code,
                task_id=session.task_id,
                batch_id=session.batch_id,
                operator_id=session.operator_id,
                robot_id=session.robot_id,
                location_id=session.location_id,
                status=enum_value(session.status),
                started_at=session.started_at,
                ended_at=session.ended_at,
                status_reason=session.status_reason,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                CaptureSessionModel,
                scope,
                session.session_id,
                "采集会话",
            )
        return replace(
            session,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: CaptureSessionModel) -> CaptureSession:
        return CaptureSession(
            session_id=row.id,
            project_id=row.project_id,
            code=row.code,
            task_id=row.task_id,
            batch_id=row.batch_id,
            operator_id=row.operator_id,
            robot_id=row.robot_id,
            location_id=row.location_id,
            status=CaptureSessionStatus(row.status),
            started_at=as_utc(row.started_at),
            ended_at=as_utc(row.ended_at),
            status_reason=row.status_reason,
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )
