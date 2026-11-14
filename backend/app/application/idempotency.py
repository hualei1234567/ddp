"""HTTP 写动作幂等的单一权威实现。

作用域 ``(project_id, user_id, key)``；同一键不同请求体返回
``idempotency_key_reused``；记录与业务写入同事务提交，过期记录允许重新执行。

为什么放在 ``application`` 顶层而不是各域各写一份：同一条幂等规则只能存在
一处权威实现（`CLAUDE.md` 第五节「规则只允许存在一处权威实现」）。各域文件
（``application/<domain>/idempotency.py``）只做错误类型适配，保留各自领域
错误类以接入既有的域错误映射，因此对外 HTTP 行为完全不变。

本模块不依赖 FastAPI 与 ORM；仓储通过 :class:`IdempotencyRepository` 协议注入。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.domain.identity.models import ProjectScope

DEFAULT_TTL = timedelta(hours=24)
MIN_KEY_LENGTH = 8
MAX_KEY_LENGTH = 128


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """一次已完成的写动作；``request_hash`` 用于判定同键是否同请求。"""

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


class IdempotencyRepository(Protocol):
    """幂等记录仓储；作用域由 ``ProjectScope`` 强制，不得跨项目读写。"""

    def get(self, scope: ProjectScope, key: str) -> IdempotencyRecord | None: ...

    def save(self, scope: ProjectScope, record: IdempotencyRecord) -> None: ...

    def delete(self, scope: ProjectScope, key: str) -> None: ...


def request_fingerprint(method: str, path: str, body: Mapping[str, object] | None) -> str:
    """请求指纹：方法 + 路径 + 规范化 JSON 体；键序无关，跨进程稳定。"""
    canonical = json.dumps(
        {"method": method, "path": path, "body": dict(body or {})},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_key_length(key: str) -> None:
    """只校验长度；失败时抛 ``ValueError``，由域适配层转成域错误。"""
    if len(key) < MIN_KEY_LENGTH or len(key) > MAX_KEY_LENGTH:
        raise ValueError(f"Idempotency-Key 长度必须在 {MIN_KEY_LENGTH} 到 {MAX_KEY_LENGTH} 之间")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


class IdempotencyGuard:
    """查重 + 落库；错误类型由子类注入，保证各域异常映射不变。"""

    def __init__(
        self,
        repository: IdempotencyRepository,
        *,
        invalid_key_error: Callable[[str], Exception],
        reused_error: Callable[[str], Exception],
    ) -> None:
        self._repository = repository
        self._invalid_key_error = invalid_key_error
        self._reused_error = reused_error

    def validate_key(self, key: str | None) -> str | None:
        if key is None:
            return None
        try:
            check_key_length(key)
        except ValueError as exc:
            raise self._invalid_key_error(str(exc)) from exc
        return key

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
            # 过期记录允许重新执行；顺手清掉，避免表无限增长。
            self._repository.delete(scope, key)
            return None
        if record.request_hash != request_fingerprint(method, path, body):
            raise self._reused_error("同一 Idempotency-Key 对应了不同的请求体")
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
