"""阶段 5 写动作幂等。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.modelops.ports import IdempotencyRepository
from app.domain.identity.models import ProjectScope
from app.domain.modelops.errors import DomainValidationError

DEFAULT_TTL = timedelta(hours=24)
MIN_KEY_LENGTH = 8
MAX_KEY_LENGTH = 128


class IdempotencyKeyReusedError(DomainValidationError):
    code = "idempotency_key_reused"


@dataclass(frozen=True, slots=True)
class IdempotencyRecordData:
    project_id: UUID
    user_id: UUID
    key: str
    method: str
    path: str
    request_hash: str
    response_status: int
    response_ref: str | None
    created_at: datetime
    expires_at: datetime


def validate_key(key: str | None) -> str | None:
    if key is None:
        return None
    if len(key) < MIN_KEY_LENGTH or len(key) > MAX_KEY_LENGTH:
        raise DomainValidationError(
            f"Idempotency-Key 长度必须在 {MIN_KEY_LENGTH} 到 {MAX_KEY_LENGTH} 之间"
        )
    return key


def request_fingerprint(method: str, path: str, body: Mapping[str, object] | None) -> str:
    canonical = json.dumps(
        {"method": method, "path": path, "body": dict(body or {})},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


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
    ):
        if key is None:
            return None
        record = self._repository.get(scope, key)
        if record is None or _utc(record.expires_at) <= now:
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
    ) -> None:
        if key is None:
            return
        self._repository.add(
            scope,
            IdempotencyRecordData(
                project_id=scope.project_id,
                user_id=scope.actor_id,
                key=key,
                method=method,
                path=path,
                request_hash=request_fingerprint(method, path, body),
                response_status=response_status,
                response_ref=response_ref,
                created_at=now,
                expires_at=now + DEFAULT_TTL,
            ),
        )
