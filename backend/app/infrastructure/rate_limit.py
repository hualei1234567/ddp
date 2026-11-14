"""限流基础设施。

当前提供进程内滑动窗口实现，适合单实例或“每进程限流”。多副本 / 多 worker
部署时各进程独立计数，无法形成全局配额。

接口刻意保持最小：只接收桶键、上限与窗口，返回是否放行及剩余额度，
便于将来替换为分布式实现而不改动 API 层。分布式限流尚未实现，配置层
（``rate_limit_backend``）因此只接受 ``memory``；实现时再放开枚举并引入依赖。
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from app.core.config import Settings

# 进程内桶数量上限：防止大量伪造来源把内存打满。
_MAX_BUCKETS = 10_000
# 每 N 次检查做一次全量过期清理，避免每次请求都遍历所有桶。
_CLEANUP_INTERVAL = 1_024


@dataclass(frozen=True)
class RateLimitDecision:
    """一次限流检查的结果。"""

    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int = 0


class RateLimiter(Protocol):
    """限流后端协议；将来接入分布式实现时沿用同一契约。"""

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        """记录一次命中并返回决策。"""
        ...


@dataclass
class _Bucket:
    window_seconds: int
    hits: deque[float]


class InMemoryRateLimiter:
    """线程安全的进程内滑动窗口限流器。

    以 ``time.monotonic`` 为时钟；测试可注入替代时钟，避免 sleep。
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_buckets: int = _MAX_BUCKETS,
    ) -> None:
        self._clock = clock
        self._max_buckets = max(1, max_buckets)
        self._buckets: dict[str, _Bucket] = {}
        self._lock = Lock()
        self._checks_since_cleanup = 0

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        if window_seconds < 1:
            raise ValueError("window_seconds 必须大于 0")

        now = self._clock()
        cutoff = now - window_seconds
        with self._lock:
            self._checks_since_cleanup += 1
            if self._checks_since_cleanup >= _CLEANUP_INTERVAL:
                self._sweep(now)
                self._checks_since_cleanup = 0

            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self._max_buckets:
                    self._sweep(now)
                if len(self._buckets) >= self._max_buckets:
                    # 仍超限时按插入顺序淘汰最旧的桶；宁可误伤也不能无限增长。
                    self._buckets.pop(next(iter(self._buckets)))
                bucket = _Bucket(window_seconds=window_seconds, hits=deque())
                self._buckets[key] = bucket
            else:
                bucket.window_seconds = window_seconds

            while bucket.hits and bucket.hits[0] <= cutoff:
                bucket.hits.popleft()

            if len(bucket.hits) >= limit:
                retry_after = max(
                    1,
                    math.ceil(bucket.hits[0] + window_seconds - now),
                )
                return RateLimitDecision(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    retry_after_seconds=retry_after,
                )

            bucket.hits.append(now)
            return RateLimitDecision(
                allowed=True,
                limit=limit,
                remaining=max(0, limit - len(bucket.hits)),
            )

    def _sweep(self, now: float) -> None:
        expired = [
            key
            for key, bucket in self._buckets.items()
            if not bucket.hits or bucket.hits[-1] <= now - bucket.window_seconds
        ]
        for key in expired:
            del self._buckets[key]


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """按配置构建限流后端。

    ``rate_limit_backend`` 目前只有 ``memory`` 一个合法值（pydantic 校验保证），
    因此这里不做分支；新增后端时在此按枚举分派。
    """
    del settings  # 当前唯一的后端不需要配置
    return InMemoryRateLimiter()
