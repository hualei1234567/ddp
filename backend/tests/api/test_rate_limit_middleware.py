"""限流中间件测试：认证桶、写桶、关闭开关与代理头信任边界。"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
import redis
from app import main as main_module
from app.api.middleware.rate_limit import client_fingerprint
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from redis.backoff import NoBackoff
from redis.retry import Retry

_REDIS_URL = os.environ.get("DDP_TEST_REDIS_URL", "").strip()


class _CollectingHandler(logging.Handler):
    """挂在具名 logger 上的采集器。

    不能用 pytest 的 ``caplog``：``create_app()`` 会调用 ``configure_logging()``，
    后者清空 root handlers，caplog 的 root handler 随之被摘掉。
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self) -> list[str]:
        return [record.getMessage() for record in self.records]


@pytest.fixture()
def rate_limit_logs() -> Iterator[_CollectingHandler]:
    handler = _CollectingHandler()
    logger = logging.getLogger("ddp.rate_limit")
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def _client(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> TestClient:
    values: dict[str, object] = {
        "rate_limit_auth_requests": 2,
        "rate_limit_auth_window_seconds": 60,
        "rate_limit_write_requests": 2,
        "rate_limit_write_window_seconds": 60,
    }
    values.update(overrides)
    settings = Settings(env="local", **values)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    app = create_app()

    @app.api_route("/api/v1/auth/ping", methods=["GET", "POST"])
    def auth_ping() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/v1/test/write")
    def write_probe() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/v1/test/read")
    def read_probe() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_auth_path_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)

    assert client.get("/api/v1/auth/ping").status_code == 200
    assert client.get("/api/v1/auth/ping").status_code == 200
    limited = client.get("/api/v1/auth/ping")

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) > 0
    assert limited.headers["X-RateLimit-Limit"] == "2"
    assert limited.headers["X-RateLimit-Remaining"] == "0"
    assert limited.headers["X-Request-Id"]
    # 限流响应同样必须经过安全头中间件（验证中间件顺序）。
    assert limited.headers["X-Content-Type-Options"] == "nosniff"


def test_write_methods_are_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)

    assert client.post("/api/v1/test/write").status_code == 200
    assert client.post("/api/v1/test/write").status_code == 200
    limited = client.post("/api/v1/test/write")

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"


def test_read_requests_are_not_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, rate_limit_write_requests=1)

    for _ in range(5):
        assert client.get("/api/v1/test/read").status_code == 200


def test_rate_limiting_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        monkeypatch,
        rate_limit_enabled=False,
        rate_limit_auth_requests=1,
    )

    for _ in range(5):
        assert client.get("/api/v1/auth/ping").status_code == 200


def test_auth_bucket_takes_precedence_over_write_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        monkeypatch,
        rate_limit_auth_requests=1,
        rate_limit_write_requests=100,
    )

    assert client.post("/api/v1/auth/ping").status_code == 200
    assert client.post("/api/v1/auth/ping").status_code == 429


def test_auth_path_prefix_respects_segment_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/api/v1/authz`` 不得命中 ``/api/v1/auth`` 认证桶。"""
    client = _client(
        monkeypatch,
        rate_limit_auth_requests=1,
        rate_limit_write_requests=100,
    )

    assert client.get("/api/v1/authz/ping").status_code == 404
    assert client.get("/api/v1/authz/ping").status_code == 404


def test_forwarded_for_is_not_trusted_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, rate_limit_auth_requests=1)

    first = client.get(
        "/api/v1/auth/ping",
        headers={"X-Forwarded-For": "203.0.113.1"},
    )
    second = client.get(
        "/api/v1/auth/ping",
        headers={"X-Forwarded-For": "203.0.113.2"},
    )

    assert first.status_code == 200
    assert second.status_code == 429


def test_forwarded_for_can_be_trusted_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        monkeypatch,
        rate_limit_auth_requests=1,
        rate_limit_trust_forwarded_for=True,
    )

    first = client.get(
        "/api/v1/auth/ping",
        headers={"X-Forwarded-For": "203.0.113.1"},
    )
    other = client.get(
        "/api/v1/auth/ping",
        headers={"X-Forwarded-For": "203.0.113.2"},
    )
    repeated = client.get(
        "/api/v1/auth/ping",
        headers={"X-Forwarded-For": "203.0.113.1"},
    )

    assert first.status_code == 200
    assert other.status_code == 200
    assert repeated.status_code == 429


def test_rate_limit_hit_is_logged_without_raw_ip(
    monkeypatch: pytest.MonkeyPatch,
    rate_limit_logs: _CollectingHandler,
) -> None:
    """限流命中必须可观测（桶/窗口/额度），且不得把客户端 IP 原样写进日志。"""
    client = _client(
        monkeypatch,
        rate_limit_auth_requests=1,
        rate_limit_trust_forwarded_for=True,
    )
    forwarded_ip = "203.0.113.77"

    assert client.get(
        "/api/v1/auth/ping", headers={"X-Forwarded-For": forwarded_ip}
    ).status_code == 200
    assert client.get(
        "/api/v1/auth/ping", headers={"X-Forwarded-For": forwarded_ip}
    ).status_code == 429

    hits = [message for message in rate_limit_logs.messages() if "限流命中" in message]
    assert len(hits) == 1
    message = hits[0]
    assert "bucket" in message
    assert "'auth'" in message
    assert "window_seconds" in message
    assert "retry_after_seconds" in message
    assert forwarded_ip not in message
    assert client_fingerprint(forwarded_ip) in message


def test_rate_limit_allowed_is_logged_at_debug(
    monkeypatch: pytest.MonkeyPatch,
    rate_limit_logs: _CollectingHandler,
) -> None:
    """放行只记 debug，避免正常流量把日志刷爆。"""
    client = _client(monkeypatch, rate_limit_auth_requests=5)

    assert client.get("/api/v1/auth/ping").status_code == 200

    allowed = [r for r in rate_limit_logs.records if "限流放行" in r.getMessage()]
    assert len(allowed) == 1
    assert allowed[0].levelno == logging.DEBUG


def test_client_fingerprint_is_stable_and_opaque() -> None:
    first = client_fingerprint("203.0.113.5")
    second = client_fingerprint("203.0.113.5")
    other = client_fingerprint("203.0.113.6")

    assert first == second
    assert first != other
    assert "203.0.113.5" not in first
    assert len(first) == 16


def test_shutdown_closes_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis 客户端必须在应用关闭时释放，不能泄漏连接池。"""
    closed: list[bool] = []

    class _ProbeLimiter:
        def check(self, key: str, *, limit: int, window_seconds: int):
            raise AssertionError("本用例不应触发限流检查")

        def close(self) -> None:
            closed.append(True)

    settings = Settings(env="local")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "build_rate_limiter", lambda _settings: _ProbeLimiter())

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200

    assert closed == [True]


@pytest.fixture()
def _redis_prefix() -> Iterator[str]:
    """每个用例独立键前缀，结束后清理自己写入的键。"""
    prefix = f"ddp:test:rl:api:{uuid4().hex}"
    yield prefix
    client = redis.Redis.from_url(
        _REDIS_URL,
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
        retry=Retry(NoBackoff(), 0),
    )
    try:
        keys = list(client.scan_iter(match=f"{prefix}:*", count=100))
        if keys:
            client.delete(*keys)
    finally:
        client.close()


def _redis_client(monkeypatch: pytest.MonkeyPatch, prefix: str, **overrides: object) -> TestClient:
    values: dict[str, object] = {
        "rate_limit_backend": "redis",
        "rate_limit_redis_url": _REDIS_URL,
        "rate_limit_redis_key_prefix": prefix,
    }
    values.update(overrides)
    return _client(monkeypatch, **values)


@pytest.mark.skipif(not _REDIS_URL, reason="未配置 DDP_TEST_REDIS_URL，BLOCKED")
def test_redis_backend_limits_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    _redis_prefix: str,
) -> None:
    """redis 后端的完整链路：中间件 → 线程池 → Redis → 429。"""
    client = _redis_client(monkeypatch, _redis_prefix, rate_limit_auth_requests=2)

    assert client.get("/api/v1/auth/ping").status_code == 200
    assert client.get("/api/v1/auth/ping").status_code == 200
    limited = client.get("/api/v1/auth/ping")

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.headers["X-RateLimit-Limit"] == "2"
    assert limited.headers["X-RateLimit-Remaining"] == "0"
    assert int(limited.headers["Retry-After"]) > 0


@pytest.mark.skipif(not _REDIS_URL, reason="未配置 DDP_TEST_REDIS_URL，BLOCKED")
def test_redis_backend_quota_is_shared_across_apps(
    monkeypatch: pytest.MonkeyPatch,
    _redis_prefix: str,
) -> None:
    """两个独立的 app 实例（模拟两个副本）共享同一配额。

    进程内后端会各自计数、共放行 2 次；redis 后端必须只放行 1 次。
    """
    first_app = _redis_client(monkeypatch, _redis_prefix, rate_limit_auth_requests=1)
    second_app = _redis_client(monkeypatch, _redis_prefix, rate_limit_auth_requests=1)

    assert first_app.get("/api/v1/auth/ping").status_code == 200
    assert second_app.get("/api/v1/auth/ping").status_code == 429


@pytest.mark.skipif(not _REDIS_URL, reason="未配置 DDP_TEST_REDIS_URL，BLOCKED")
def test_redis_backend_fails_open_when_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    rate_limit_logs: _CollectingHandler,
) -> None:
    """Redis 不可用时按 ADR-0031 fail-open：放行并记 error，接口仍可用。"""
    client = _client(
        monkeypatch,
        rate_limit_backend="redis",
        rate_limit_redis_url="redis://127.0.0.1:6390/0",
        rate_limit_redis_socket_timeout_seconds=0.2,
        rate_limit_redis_connect_timeout_seconds=0.2,
        rate_limit_auth_requests=1,
    )

    # 配额为 1，若限流生效第二次必为 429；两次 200 证明降级放行。
    assert client.get("/api/v1/auth/ping").status_code == 200
    assert client.get("/api/v1/auth/ping").status_code == 200

    errors = [r for r in rate_limit_logs.records if r.levelno == logging.ERROR]
    assert any("fail-open" in r.getMessage() for r in errors)
    assert any("降级放行" in r.getMessage() for r in errors)
    # 降级日志同样不得泄露原始 IP。
    assert not any("127.0.0.1" in r.getMessage() for r in errors)
