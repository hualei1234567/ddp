"""标注 HTTP 写动作幂等。

作用域 ``(project_id, user_id, key)``；同一键不同请求体返回
``idempotency_key_reused``；记录与业务写入同事务提交。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from app.application.annotation.ports import (
    IdempotencyRecord,
    IdempotencyRepository,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    IdempotencyKeyReusedError,
)
from app.domain.identity.models import ProjectScope

DEFAULT_TTL = timedelta(hours=24)
MIN_KEY_LENGTH = 8
MAX_KEY_LENGTH = 128


def validate_key(key: str | None) -> str | None:
    if key is None:
        return None
    if len(key) < MIN_KEY_LENGTH or len(key) > MAX_KEY_LENGTH:
        raise DomainValidationError(
            f"Idempotency-Key 长度必须在 {MIN_KEY_LENGTH} 到 {MAX_KEY_LENGTH} 之间"
        )
    return key


def request_fingerprint(
    method: str,
    path: str,
    body: Mapping[str, object] | None,
) -> str:
    canonical = json.dumps(
        {"method": method, "path": path, "body": dict(body or {})},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


class IdempotencyGuard:
    def __init__(self, repository: IdempotencyRepository) -> None:
        self._repository = repository

    def lookup(
        self,
        scope: ProjectScope,
        key: str | None,
        *,
        method: str,
        path: str,
        body: Mapping[str, object] | None,
        now: datetime,
    ) -> IdempotencyRecord | None:
        if key is None:
            return None
        record = self._repository.get(scope, key)
        if record is None:
            return None
        if _as_utc(record.expires_at) <= now:
            self._repository.delete(scope, key)
            return None
        if record.request_hash != request_fingerprint(method, path, body):
            raise IdempotencyKeyReusedError("同一 Idempotency-Key 对应了不同的请求体")
        return record

    def store(
        self,
        scope: ProjectScope,
        key: str | None,
        *,
        method: str,
        path: str,
        body: Mapping[str, object] | None,
        response_status: int,
        response_ref: str | None,
        now: datetime,
        ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        if key is None:
            return
        self._repository.save(
            scope,
            IdempotencyRecord(
                project_id=scope.project_id,
                user_id=scope.actor_id,
                key=key,
                method=method,
                path=path,
                request_hash=request_fingerprint(method, path, body),
                response_status=response_status,
                response_ref=response_ref,
                created_at=now,
                expires_at=now + ttl,
            ),
        )
