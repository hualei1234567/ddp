"""进程内滑动窗口限流器单元测试。"""

from __future__ import annotations

import pytest
from app.core.config import Settings
from app.infrastructure.rate_limit import InMemoryRateLimiter, build_rate_limiter
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


def test_rate_limit_backend_only_accepts_memory() -> None:
    """分布式限流未实现前，配置层就拒绝 redis，不留"能配但启动失败"的空壳。"""
    with pytest.raises(ValidationError):
        Settings(env="local", rate_limit_backend="redis")
