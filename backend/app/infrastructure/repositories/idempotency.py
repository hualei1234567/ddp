"""幂等记录的 SQL 仓储（唯一实现）。

四张域内的同名类此前逐字重复（仅类名不同、modelops 少了 delete 与冲突处理）。
统一到此处后，各域仓储在自身模块内保留旧类名做别名，调用点无需改动。

语义要点：
- 作用域 ``(project_id, user_id, key)`` 由服务端 ``ProjectScope`` 强制；
- ``save`` 插入冲突视为并发重复提交，抛 ``ConflictError`` 而不是静默成功；
- 时间戳统一按 UTC 归一，避免 SQLite 读回丢时区导致误判过期。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.idempotency import IdempotencyRecord
from app.core.errors import ConflictError
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import IdempotencyRecord as IdempotencyModel
from app.infrastructure.repositories._sql_support import as_utc


class SqlIdempotencyRepository:
    """幂等记录读写；四个域共用同一张 ``idempotency_records`` 表。"""

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
