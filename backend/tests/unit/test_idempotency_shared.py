"""幂等权威实现的单元测试。

``app.application.idempotency`` 是所有写动作幂等规则的唯一实现；四个域文件
只做错误类型适配。这里同时验证：规则本体行为、以及"四个域适配层对外错误
类型仍然各自归属本域"（收敛不改变 HTTP 形状）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.application.idempotency import (
    DEFAULT_TTL,
    IdempotencyGuard,
    IdempotencyRecord,
    check_key_length,
    request_fingerprint,
)
from app.application.idempotency import IdempotencyGuard as SharedGuard
from app.domain.identity.models import ProjectScope

_NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


class FakeRepository:
    """内存仓储；只实现协议要求的方法。"""

    def __init__(self) -> None:
        self.records: dict[str, IdempotencyRecord] = {}
        self.deleted: list[str] = []
        self.saved: list[IdempotencyRecord] = []

    def get(self, scope: ProjectScope, key: str) -> IdempotencyRecord | None:
        record = self.records.get(key)
        if record is None:
            return None
        assert record.project_id == scope.project_id
        return record

    def save(self, scope: ProjectScope, record: IdempotencyRecord) -> None:
        scope.ensure(record.project_id)
        self.records[record.key] = record
        self.saved.append(record)

    def delete(self, scope: ProjectScope, key: str) -> None:
        scope.ensure(self.records[key].project_id)
        self.records.pop(key, None)
        self.deleted.append(key)


class _InvalidKey(Exception):
    pass


class _Reused(Exception):
    pass


def _scope() -> ProjectScope:
    return ProjectScope(project_id=uuid4(), actor_id=uuid4())


def _guard(repository: FakeRepository) -> IdempotencyGuard:
    return IdempotencyGuard(
        repository,
        invalid_key_error=_InvalidKey,
        reused_error=_Reused,
    )


def _store(
    guard: IdempotencyGuard,
    repository: FakeRepository,
    scope: ProjectScope,
    *,
    key: str = "idem-key-0001",
    now: datetime = _NOW,
    ttl: timedelta = DEFAULT_TTL,
    body: dict[str, object] | None = None,
) -> None:
    guard.store(
        scope,
        key,
        method="POST",
        path="/api/v1/projects/x/things",
        body=body or {"name": "a"},
        response_status=201,
        response_ref="ref-1",
        now=now,
        ttl=ttl,
    )


# --- 规则本体 ---------------------------------------------------------------


def test_fingerprint_is_order_insensitive_for_body_keys() -> None:
    left = request_fingerprint("POST", "/p", {"a": 1, "b": 2})
    right = request_fingerprint("POST", "/p", {"b": 2, "a": 1})

    assert left == right
    assert left.startswith("sha256:")


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ({"method": "POST"}, {"method": "PUT"}),
        ({"path": "/p"}, {"path": "/q"}),
        ({"body": {"a": 1}}, {"body": {"a": 2}}),
    ],
)
def test_fingerprint_changes_with_request_shape(
    left: dict[str, object], right: dict[str, object]
) -> None:
    base = {"method": "POST", "path": "/p", "body": None}
    assert request_fingerprint(**{**base, **left}) != request_fingerprint(**{**base, **right})


def test_none_key_is_never_stored_nor_looked_up() -> None:
    repository = FakeRepository()
    guard = _guard(repository)
    scope = _scope()

    assert guard.lookup(scope, None, method="POST", path="/p", body=None, now=_NOW) is None
    _store(guard, repository, scope, key=None)  # type: ignore[arg-type]

    assert repository.saved == []


def test_replay_returns_same_record() -> None:
    repository = FakeRepository()
    guard = _guard(repository)
    scope = _scope()
    _store(guard, repository, scope)

    replay = guard.lookup(
        scope,
        "idem-key-0001",
        method="POST",
        path="/api/v1/projects/x/things",
        body={"name": "a"},
        now=_NOW + timedelta(minutes=1),
    )

    assert replay is not None
    assert replay.response_ref == "ref-1"


def test_same_key_different_body_raises_reused() -> None:
    repository = FakeRepository()
    guard = _guard(repository)
    scope = _scope()
    _store(guard, repository, scope)

    with pytest.raises(_Reused):
        guard.lookup(
            scope,
            "idem-key-0001",
            method="POST",
            path="/api/v1/projects/x/things",
            body={"name": "b"},
            now=_NOW + timedelta(minutes=1),
        )


def test_expired_record_is_deleted_and_allows_retry() -> None:
    repository = FakeRepository()
    guard = _guard(repository)
    scope = _scope()
    _store(guard, repository, scope, ttl=timedelta(seconds=1))

    replay = guard.lookup(
        scope,
        "idem-key-0001",
        method="POST",
        path="/api/v1/projects/x/things",
        body={"name": "a"},
        now=_NOW + timedelta(seconds=5),
    )

    assert replay is None
    assert repository.deleted == ["idem-key-0001"]
    assert "idem-key-0001" not in repository.records


def test_naive_expiry_timestamp_is_treated_as_utc() -> None:
    """SQLite 读回的时间可能丢时区；不能因此把未过期记录判成过期。"""
    repository = FakeRepository()
    guard = _guard(repository)
    scope = _scope()
    repository.records["idem-key-0001"] = IdempotencyRecord(
        project_id=scope.project_id,
        user_id=scope.actor_id,
        key="idem-key-0001",
        method="POST",
        path="/api/v1/projects/x/things",
        request_hash=request_fingerprint("POST", "/api/v1/projects/x/things", {"name": "a"}),
        response_status=201,
        response_ref="ref-1",
        created_at=_NOW.replace(tzinfo=None),
        expires_at=(_NOW + timedelta(hours=1)).replace(tzinfo=None),
    )

    assert (
        guard.lookup(
            scope,
            "idem-key-0001",
            method="POST",
            path="/api/v1/projects/x/things",
            body={"name": "a"},
            now=_NOW,
        )
        is not None
    )


@pytest.mark.parametrize("key", ["short", "a" * 129])
def test_key_length_is_enforced(key: str) -> None:
    with pytest.raises(_InvalidKey):
        _guard(FakeRepository()).validate_key(key)


@pytest.mark.parametrize("key", [None, "a" * 8, "a" * 128])
def test_key_length_accepts_boundaries(key: str | None) -> None:
    assert _guard(FakeRepository()).validate_key(key) == key


def test_check_key_length_raises_value_error_for_adapter_to_translate() -> None:
    with pytest.raises(ValueError, match="Idempotency-Key"):
        check_key_length("tiny")


# --- 适配层：错误类型仍归属各自域 -------------------------------------------


@pytest.mark.parametrize(
    ("module", "domain_error", "reused_error"),
    [
        ("datasets", "app.domain.datasets.errors", "app.domain.datasets.errors"),
        ("collection", "app.domain.collection.errors", "app.domain.collection.errors"),
        ("annotation", "app.domain.annotation.errors", "app.domain.annotation.errors"),
        ("modelops", "app.domain.modelops.errors", "app.domain.modelops.errors"),
    ],
)
def test_domain_adapters_keep_their_own_error_types(
    module: str, domain_error: str, reused_error: str
) -> None:
    import importlib

    adapter = importlib.import_module(f"app.application.{module}.idempotency")
    errors = importlib.import_module(domain_error)

    # 薄适配层是共享实现的子类，规则不会分叉。
    assert issubclass(adapter.IdempotencyGuard, SharedGuard)

    repository = FakeRepository()
    guard = adapter.IdempotencyGuard(repository)
    scope = _scope()

    with pytest.raises(errors.DomainValidationError):
        adapter.validate_key("short")

    adapter.IdempotencyGuard(repository).store(
        scope,
        "idem-key-0001",
        method="POST",
        path="/p",
        body={"a": 1},
        response_status=201,
        response_ref="ref-1",
        now=_NOW,
    )
    with pytest.raises(errors.IdempotencyKeyReusedError):
        guard.lookup(scope, "idem-key-0001", method="POST", path="/p", body={"a": 2}, now=_NOW)
