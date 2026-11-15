"""阶段 5 跨模块辅助仓储：审计、outbox、数据集门禁与 Adapter 回调。

从 ``modelops.py`` 拆出（原文件 828 行）。这些类不被 modelops 的主仓储复用，
拆出后主文件只保留模型/版本/评测/推理/反馈/需求的业务仓储。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.modelops.audit import AuditRecordData, OutboxEventData
from app.application.modelops.ports import CallbackRecord
from app.domain.identity.models import ProjectScope
from app.domain.modelops.errors import DuplicateResourceError
from app.infrastructure.db.models.collection import AuditLog
from app.infrastructure.db.models.collection import OutboxEvent as OutboxModel
from app.infrastructure.db.models.datasets import DatasetVersionModel
from app.infrastructure.db.models.modelops import ModelCallbackRow
from app.infrastructure.repositories.idempotency import (
    SqlIdempotencyRepository as SqlSharedIdempotencyRepository,
)
from app.infrastructure.repositories.modelops_rows import _callback


class SqlModelAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, scope: ProjectScope, record: AuditRecordData) -> None:
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


class SqlModelOutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, scope: ProjectScope, event: OutboxEventData) -> None:
        scope.ensure(event.project_id)
        self._session.add(
            OutboxModel(
                id=event.event_id,
                project_id=event.project_id,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                event_type=event.event_type,
                payload=dict(event.payload),
                # 投递类进 PENDING 等待消费者；留痕类写入即终态 RECORDED。
                status="PENDING" if event.dispatch else "RECORDED",
                dispatch=event.dispatch,
                attempts=0,
                available_at=event.occurred_at,
                created_at=event.occurred_at,
            )
        )
        self._session.flush()


# 幂等仓储统一在 app.infrastructure.repositories.idempotency；保留旧类名别名。
SqlModelIdempotencyRepository = SqlSharedIdempotencyRepository


class SqlDatasetGate:
    def __init__(self, session: Session) -> None:
        self._session = session

    def version_state(self, project_id: UUID, version_id: UUID):
        row = self._session.get(DatasetVersionModel, version_id)
        if row is None or row.project_id != project_id:
            return None
        return row.status, row.validity_status

    def item_count(self, project_id: UUID, version_id: UUID) -> int:
        row = self._session.get(DatasetVersionModel, version_id)
        if row is None or row.project_id != project_id:
            return 0
        return row.item_count


class SqlModelCallbackRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_event(self, adapter_id: str, event_id: str) -> CallbackRecord | None:
        row = self._session.get(ModelCallbackRow, {"adapter_id": adapter_id, "event_id": event_id})
        return _callback(row) if row else None

    def get_by_nonce(self, adapter_id: str, nonce: str) -> CallbackRecord | None:
        row = self._session.scalar(
            select(ModelCallbackRow).where(
                ModelCallbackRow.adapter_id == adapter_id,
                ModelCallbackRow.nonce == nonce,
            )
        )
        return _callback(row) if row else None

    def add(self, record: CallbackRecord) -> None:
        self._session.add(
            ModelCallbackRow(
                adapter_id=record.adapter_id,
                event_id=record.event_id,
                nonce=record.nonce,
                payload_sha256=record.payload_sha256,
                received_at=record.received_at,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise DuplicateResourceError("模型回调已存在") from exc

    def prune_before(self, *, received_before: datetime) -> None:
        rows = list(
            self._session.scalars(
                select(ModelCallbackRow).where(ModelCallbackRow.received_at < received_before)
            )
        )
        for row in rows:
            self._session.delete(row)
        self._session.flush()
