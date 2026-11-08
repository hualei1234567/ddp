"""数据集审计、outbox 与幂等记录 SQLAlchemy 仓储。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.datasets.ports import AuditRecord, IdempotencyRecord, OutboxEvent
from app.domain.datasets.errors import ConflictError
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import AuditLog
from app.infrastructure.db.models.collection import IdempotencyRecord as IdempotencyModel
from app.infrastructure.db.models.collection import OutboxEvent as OutboxModel
from app.infrastructure.repositories._dataset_sql import as_utc


class SqlDatasetAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, scope: ProjectScope, record: AuditRecord) -> None:
        scope.ensure(record.project_id)
        self._session.add(
            AuditLog(
                id=record.audit_id,
                project_id=record.project_id,
                actor_user_id=record.actor_user_id,
                actor_role=record.actor_role,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
                action=record.action,
                from_status=record.from_status,
                to_status=record.to_status,
                reason=record.reason,
                request_id=record.request_id,
                result=record.result,
                correlation_id=None,
                details=dict(record.details),
                occurred_at=record.occurred_at,
            )
        )
        self._session.flush()


class SqlDatasetOutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, scope: ProjectScope, event: OutboxEvent) -> None:
        scope.ensure(event.project_id)
        self._session.add(
            OutboxModel(
                id=event.event_id,
                project_id=event.project_id,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                event_type=event.event_type,
                payload=dict(event.payload),
                status="PENDING",
                attempts=0,
                available_at=event.occurred_at,
                created_at=event.occurred_at,
            )
        )
        self._session.flush()


class SqlDatasetIdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, scope: ProjectScope, key: str) -> IdempotencyRecord | None:
        row = self._session.scalar(
            select(IdempotencyModel).where(
                IdempotencyModel.project_id == scope.project_id,
                IdempotencyModel.user_id == scope.actor_id,
                IdempotencyModel.key == key,
            )
        )
        if row is None:
            return None
        created_at = as_utc(row.created_at)
        expires_at = as_utc(row.expires_at)
        if created_at is None or expires_at is None:
            return None
        return IdempotencyRecord(
            project_id=row.project_id,
            user_id=row.user_id,
            key=row.key,
            method=row.method,
            path=row.path,
            request_hash=row.request_hash,
            response_status=row.response_status,
            response_ref=row.response_ref,
            created_at=created_at,
            expires_at=expires_at,
        )

    def save(self, scope: ProjectScope, record: IdempotencyRecord) -> None:
        scope.ensure(record.project_id)
        self._session.add(
            IdempotencyModel(
                project_id=record.project_id,
                user_id=record.user_id,
                key=record.key,
                method=record.method,
                path=record.path,
                request_hash=record.request_hash,
                response_status=record.response_status,
                response_ref=record.response_ref,
                created_at=record.created_at,
                expires_at=record.expires_at,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("幂等键并发冲突，请重试") from exc

    def delete(self, scope: ProjectScope, key: str) -> None:
        row = self._session.scalar(
            select(IdempotencyModel).where(
                IdempotencyModel.project_id == scope.project_id,
                IdempotencyModel.user_id == scope.actor_id,
                IdempotencyModel.key == key,
            )
        )
        if row is not None:
            self._session.delete(row)
            self._session.flush()
