"""Redis 限流后端集成测试（真实 Redis）。

需要真实 Redis：设置 ``DDP_TEST_REDIS_URL``（如 ``redis://localhost:6379/0``）。
未设置时整个模块跳过并标注 BLOCKED——限流语义（原子性、跨客户端共享配额、
键过期）无法用假客户端可靠验证，不做 mock 替代。

每个用例使用独立键前缀并在结束时清理，避免互相干扰与污染共享 Redis。
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from uuid import uuid4

import pytest
import redis
from app.core.config import Settings
from app.infrastructure.rate_limit import (
    RateLimitDecision,
    RedisRateLimiter,
    build_rate_limiter,
)
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.retry import Retry

_REDIS_URL = os.environ.get("DDP_TEST_REDIS_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _REDIS_URL,
    reason="未配置 DDP_TEST_REDIS_URL，Redis 限流集成测试 BLOCKED",
)


@pytest.fixture()
def prefix() -> Iterator[str]:
    """每个用例独立键前缀，结束时清掉自己写入的键。"""
    value = f"ddp:test:rl:{uuid4().hex}"
    yield value
    _cleanup_prefix(value)


@pytest.fixture()
def raw_client() -> Iterator[redis.Redis]:
    client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)
    try:
        yield client
    finally:
        client.close()


def _cleanup_prefix(prefix: str) -> None:
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


def _limiter(prefix: str, **overrides: object) -> RedisRateLimiter:
    settings = Settings(
        env="local",
        rate_limit_backend="redis",
        rate_limit_redis_url=_REDIS_URL,
        rate_limit_redis_key_prefix=prefix,
        **overrides,
    )
    limiter = build_rate_limiter(settings)
    assert isinstance(limiter, RedisRateLimiter)
    return limiter


def test_allows_requests_up_to_limit(prefix: str) -> None:
    limiter = _limiter(prefix)
    try:
        first = limiter.check("auth:203.0.113.1", limit=2, window_seconds=60)
        second = limiter.check("auth:203.0.113.1", limit=2, window_seconds=60)
    finally:
        limiter.close()

    assert first.allowed is True
    assert (first.limit, first.remaining) == (2, 1)
    assert second.allowed is True
    assert second.remaining == 0
    assert first.degraded is False


def test_rejects_request_over_limit_with_retry_after(prefix: str) -> None:
    limiter = _limiter(prefix)
    try:
        limiter.check("auth:203.0.113.2", limit=2, window_seconds=5)
        limiter.check("auth:203.0.113.2", limit=2, window_seconds=5)
        time.sleep(1.1)
        decision = limiter.check("auth:203.0.113.2", limit=2, window_seconds=5)
    finally:
        limiter.close()

    assert decision.allowed is False
    assert decision.remaining == 0
    # 最早一次命中在 1.1s 前，窗口 5s，故还需等待约 3.9s → 向上取整为 4。
    assert 1 <= decision.retry_after_seconds <= 4


def test_window_rolling_frees_capacity(prefix: str) -> None:
    """窗口滚动后恢复：窗口设 1s，等待其过期后同键应重新放行。"""
    limiter = _limiter(prefix)
    try:
        assert limiter.check("write:203.0.113.3", limit=1, window_seconds=1).allowed is True
        assert limiter.check("write:203.0.113.3", limit=1, window_seconds=1).allowed is False
        time.sleep(1.2)
        recovered = limiter.check("write:203.0.113.3", limit=1, window_seconds=1)
    finally:
        limiter.close()

    assert recovered.allowed is True
    assert recovered.remaining == 0


def test_quota_is_shared_across_limiter_instances(prefix: str) -> None:
    """两个独立客户端实例（模拟两个 API 副本）共用同一配额。

    这是「全局而非进程内」的核心证明：进程内实现下两个实例各有一份计数，
    这里必须互相消耗同一份。
    """
    first_process = _limiter(prefix)
    second_process = _limiter(prefix)
    try:
        assert first_process.check("write:203.0.113.4", limit=2, window_seconds=60).allowed is True
        assert second_process.check("write:203.0.113.4", limit=2, window_seconds=60).allowed is True
        # 第三次由 A 进程发起，但配额已被两个进程共同耗尽。
        denied = first_process.check("write:203.0.113.4", limit=2, window_seconds=60)
    finally:
        first_process.close()
        second_process.close()

    assert denied.allowed is False


def test_quota_is_shared_when_requests_are_concurrent(prefix: str) -> None:
    """并发命中也不得超发：Lua 脚本的原子性保证计数不丢。"""
    from concurrent.futures import ThreadPoolExecutor

    limiter = _limiter(prefix, rate_limit_redis_max_connections=8)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            decisions = list(
                pool.map(
                    lambda _: limiter.check("write:203.0.113.5", limit=5, window_seconds=60),
                    range(20),
                )
            )
    finally:
        limiter.close()

    allowed = [d for d in decisions if d.allowed]
    assert len(allowed) == 5
    assert len([d for d in decisions if not d.allowed]) == 15


def test_buckets_are_isolated_by_key(prefix: str) -> None:
    limiter = _limiter(prefix)
    try:
        assert limiter.check("auth:203.0.113.6", limit=1, window_seconds=60).allowed is True
        other = limiter.check("auth:203.0.113.7", limit=1, window_seconds=60)
    finally:
        limiter.close()

    assert other.allowed is True


def test_buckets_are_isolated_by_window(prefix: str) -> None:
    """不同窗口配置不共用计数：键含窗口标识，避免短窗口撞上长窗口配额。"""
    limiter = _limiter(prefix)
    try:
        assert limiter.check("auth:203.0.113.8", limit=1, window_seconds=60).allowed is True
        other_window = limiter.check("auth:203.0.113.8", limit=1, window_seconds=30)
    finally:
        limiter.close()

    assert other_window.allowed is True


def test_key_expires_after_window(prefix: str, raw_client: redis.Redis) -> None:
    """键必须带 TTL，否则 Redis 会无限积累客户端来源。"""
    limiter = _limiter(prefix)
    try:
        limiter.check("auth:203.0.113.9", limit=1, window_seconds=30)
    finally:
        limiter.close()

    keys = list(raw_client.scan_iter(match=f"{prefix}:*", count=100))
    assert len(keys) == 1
    ttl = raw_client.pttl(keys[0])
    assert 0 < ttl <= 30_000


def test_redis_key_contains_window_and_bucket(prefix: str, raw_client: redis.Redis) -> None:
    limiter = _limiter(prefix)
    try:
        limiter.check("write:203.0.113.10", limit=1, window_seconds=45)
    finally:
        limiter.close()

    keys = list(raw_client.scan_iter(match=f"{prefix}:*", count=100))
    assert len(keys) == 1
    assert keys[0].startswith(f"{prefix}:45s:write:")


def test_unavailable_redis_fails_open_by_default() -> None:
    """按 ADR-0031：Redis 不可用时放行并标记 degraded，不得把业务拖死。"""
    limiter = _limiter_unreachable()
    try:
        decision = limiter.check("auth:203.0.113.11", limit=2, window_seconds=60)
    finally:
        limiter.close()

    assert decision.allowed is True
    assert decision.degraded is True
    assert decision.remaining == 2
    assert decision.retry_after_seconds == 0


def test_unavailable_redis_does_not_block_request_thread() -> None:
    """不可达时耗时必须有界：默认重试会把一次失败放大到数秒。"""
    limiter = _limiter_unreachable()
    try:
        started = time.monotonic()
        limiter.check("auth:203.0.113.12", limit=2, window_seconds=60)
        elapsed = time.monotonic() - started
    finally:
        limiter.close()

    assert elapsed < 2.0, f"限流不可达耗时 {elapsed:.2f}s 过长"


def test_degraded_decision_is_not_reported_for_healthy_backend(prefix: str) -> None:
    limiter = _limiter(prefix)
    try:
        decision = limiter.check("auth:203.0.113.13", limit=1, window_seconds=60)
    finally:
        limiter.close()

    assert decision == RateLimitDecision(
        allowed=True, limit=1, remaining=0, retry_after_seconds=0, degraded=False
    )


def test_invalid_policy_is_rejected(prefix: str) -> None:
    limiter = _limiter(prefix)
    try:
        with pytest.raises(ValueError):
            limiter.check("auth:203.0.113.14", limit=0, window_seconds=60)
        with pytest.raises(ValueError):
            limiter.check("auth:203.0.113.14", limit=1, window_seconds=0)
    finally:
        limiter.close()


def _limiter_unreachable() -> RedisRateLimiter:
    """指向一个必然连不上的端口，模拟 Redis 故障。

    选 6390 是刻意避开默认 6379 与测试库，避免误连到正在运行的实例。
    """
    settings = Settings(
        env="local",
        rate_limit_backend="redis",
        rate_limit_redis_url="redis://127.0.0.1:6390/0",
        rate_limit_redis_socket_timeout_seconds=0.2,
        rate_limit_redis_connect_timeout_seconds=0.2,
    )
    limiter = build_rate_limiter(settings)
    assert isinstance(limiter, RedisRateLimiter)
    return limiter


def test_connection_error_type_is_reachable() -> None:
    """守护断言：fail-open 依赖捕获 ``redis.exceptions.ConnectionError``。"""
    assert issubclass(RedisConnectionError, redis.exceptions.RedisError)
