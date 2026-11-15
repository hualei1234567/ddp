"""限流器单元测试：进程内实现行为、配置校验与后端装配。"""

from __future__ import annotations

import pytest
from app.core.config import Settings
from app.infrastructure.rate_limit import InMemoryRateLimiter, RedisRateLimiter, build_rate_limiter
from pydantic import ValidationError


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_allows_requests_up_to_limit() -> None:
    limiter = InMemoryRateLimiter(clock=FakeClock())

    first = limiter.check("ip:1", limit=2, window_seconds=60)
    second = limiter.check("ip:1", limit=2, window_seconds=60)

    assert first.allowed is True
    assert first.remaining == 1
    assert second.allowed is True
    assert second.remaining == 0


def test_rejects_request_over_limit_with_retry_after() -> None:
    clock = FakeClock()
    limiter = InMemoryRateLimiter(clock=clock)
    limiter.check("ip:1", limit=2, window_seconds=60)
    limiter.check("ip:1", limit=2, window_seconds=60)
    clock.advance(15)

    decision = limiter.check("ip:1", limit=2, window_seconds=60)

    assert decision.allowed is False
    assert decision.remaining == 0
    assert decision.retry_after_seconds == 45


def test_window_expiry_frees_capacity() -> None:
    clock = FakeClock()
    limiter = InMemoryRateLimiter(clock=clock)
    limiter.check("ip:1", limit=1, window_seconds=10)
    assert limiter.check("ip:1", limit=1, window_seconds=10).allowed is False

    clock.advance(10)

    assert limiter.check("ip:1", limit=1, window_seconds=10).allowed is True


def test_buckets_are_isolated_by_key() -> None:
    limiter = InMemoryRateLimiter(clock=FakeClock())
    limiter.check("ip:1", limit=1, window_seconds=60)

    assert limiter.check("ip:2", limit=1, window_seconds=60).allowed is True


def test_bucket_store_is_bounded() -> None:
    limiter = InMemoryRateLimiter(clock=FakeClock(), max_buckets=2)
    limiter.check("a", limit=1, window_seconds=60)
    limiter.check("b", limit=1, window_seconds=60)

    limiter.check("c", limit=1, window_seconds=60)

    # 超限时淘汰最旧桶，新键仍可正常计数，且总量不无限增长。
    assert limiter.check("c", limit=1, window_seconds=60).allowed is False


@pytest.mark.parametrize(
    ("limit", "window_seconds"),
    [(0, 60), (-1, 60), (1, 0), (1, -1)],
)
def test_invalid_policy_is_rejected(limit: int, window_seconds: int) -> None:
    limiter = InMemoryRateLimiter(clock=FakeClock())

    with pytest.raises(ValueError):
        limiter.check("ip:1", limit=limit, window_seconds=window_seconds)


def test_build_rate_limiter_defaults_to_memory() -> None:
    limiter = build_rate_limiter(Settings(env="local"))

    assert isinstance(limiter, InMemoryRateLimiter)


def test_rate_limit_backend_rejects_unknown_value() -> None:
    """枚举外的后端名在配置解析阶段就拒绝，不留"能配但运行时崩"的空壳。"""
    with pytest.raises(ValidationError):
        Settings(env="local", rate_limit_backend="memcached")


def test_rate_limit_backend_accepts_redis_with_url() -> None:
    settings = Settings(
        env="local",
        rate_limit_backend="redis",
        rate_limit_redis_url="redis://localhost:6379/0",
    )

    settings.validate_runtime_security()

    assert settings.rate_limit_backend == "redis"


def test_redis_backend_without_url_fails_startup_check() -> None:
    """启用 redis 却不给地址必须 fail-closed，否则会误以为已有全局配额。"""
    settings = Settings(env="local", rate_limit_backend="redis")

    with pytest.raises(RuntimeError) as excinfo:
        settings.validate_runtime_security()

    assert "DDP_RATE_LIMIT_REDIS_URL" in str(excinfo.value)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "http://localhost:6379/0",
        "localhost:6379",
        "redis:///0",
        "redis://localhost:not-a-port/0",
        "redis://localhost:99999/0",
        " redis://localhost:6379/0",
    ],
)
def test_redis_url_is_validated(url: str) -> None:
    settings = Settings(
        env="local",
        rate_limit_backend="redis",
        rate_limit_redis_url=url,
    )

    with pytest.raises(RuntimeError):
        settings.validate_runtime_security()


def test_redis_url_with_tls_scheme_is_accepted() -> None:
    settings = Settings(
        env="local",
        rate_limit_backend="redis",
        rate_limit_redis_url="rediss://cache.internal:6380/1",
    )

    settings.validate_runtime_security()


def test_redis_url_is_ignored_when_backend_is_memory() -> None:
    """回滚路径必须简单：切回 memory 时 env 里残留的 URL 不得阻止启动。"""
    settings = Settings(
        env="local",
        rate_limit_backend="memory",
        rate_limit_redis_url="redis://localhost:6379/0",
    )

    settings.validate_runtime_security()

    assert build_rate_limiter(settings).__class__ is InMemoryRateLimiter


def test_build_rate_limiter_returns_redis_backend_when_configured() -> None:
    limiter = build_rate_limiter(
        Settings(
            env="local",
            rate_limit_backend="redis",
            rate_limit_redis_url="redis://localhost:6379/0",
        )
    )

    try:
        assert isinstance(limiter, RedisRateLimiter)
    finally:
        limiter.close()


def test_rate_limit_redis_timeouts_are_bounded() -> None:
    """超时必须短且有上界：限流不能把 HTTP 线程挂住。"""
    settings = Settings(env="local")

    assert settings.rate_limit_redis_socket_timeout_seconds <= 5
    assert settings.rate_limit_redis_connect_timeout_seconds <= 5
    with pytest.raises(ValidationError):
        Settings(env="local", rate_limit_redis_socket_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(env="local", rate_limit_redis_connect_timeout_seconds=60)


def test_memory_limiter_degraded_flag_is_false() -> None:
    """``degraded`` 只应出现在后端故障降级路径，正常放行不得置位。"""
    limiter = InMemoryRateLimiter(clock=FakeClock())

    decision = limiter.check("ip:1", limit=1, window_seconds=60)

    assert decision.degraded is False


def test_memory_limiter_close_clears_state() -> None:
    limiter = InMemoryRateLimiter(clock=FakeClock())
    limiter.check("ip:1", limit=1, window_seconds=60)

    limiter.close()

    assert limiter.check("ip:1", limit=1, window_seconds=60).allowed is True
