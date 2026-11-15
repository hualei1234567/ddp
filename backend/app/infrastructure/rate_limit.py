"""限流基础设施。

提供两种后端，共享同一 ``RateLimiter`` 协议与 ``RateLimitDecision`` 返回结构：

- ``InMemoryRateLimiter``：进程内滑动窗口（默认）。单实例部署足够，
  多副本部署时各进程独立计数，**不构成全局配额**。
- ``RedisRateLimiter``：以 Redis 为共享计数的滑动窗口。所有副本读写同一份
  计数，因而形成全局配额；Redis 服务端 ``TIME`` 作为时钟，避免副本间时钟漂移。

两者语义一致（滑动窗口、同一 ``limit`` / ``window_seconds`` 参数、
同样的 ``retry_after_seconds`` 计算方式），切换只改变计数存放位置。

不可用时的策略：``RedisRateLimiter`` 采用 **fail-open**（放行并记 error 日志）。
限流是保护措施而非授权措施：Redis 故障时若 fail-closed，所有写接口会整体
不可用（自伤式拒绝服务），且 Redis 故障不应扩大为业务不可用。授权仍由
JWT 与项目域校验独立保证，那部分保持 fail-closed。见 ``docs/adr/0031``。
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Protocol
from uuid import uuid4

import redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from app.core.config import Settings

logger = logging.getLogger("ddp.rate_limit")

# 进程内桶数量上限：防止大量伪造来源把内存打满。
_MAX_BUCKETS = 10_000
# 每 N 次检查做一次全量过期清理，避免每次请求都遍历所有桶。
_CLEANUP_INTERVAL = 1_024
# 键中来源标识的最大长度；超长或含非安全字符时改用摘要，防止键膨胀。
_MAX_KEY_TOKEN_LENGTH = 64

# 滑动窗口 Lua 脚本：一次往返内完成「裁剪过期 → 计数 → 判定 → 记账」。
# 原子性由 Redis 单线程执行脚本保证，避免 read-then-write 的并发竞态。
# 时钟取 Redis 服务端 TIME，多副本共享同一时间基准。
#
# KEYS[1] 计数键；ARGV[1] 窗口毫秒；ARGV[2] 上限；ARGV[3] 本次命中唯一成员。
# 返回 {allowed(0/1), remaining, retry_after_seconds, used}。
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local window_ms = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local member = ARGV[3]

local server_time = redis.call('TIME')
local now_ms = tonumber(server_time[1]) * 1000 + math.floor(tonumber(server_time[2]) / 1000)

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local used = redis.call('ZCARD', key)

if used >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_ms = window_ms
  if oldest[2] then
    retry_ms = tonumber(oldest[2]) + window_ms - now_ms
  end
  local retry_seconds = math.ceil(retry_ms / 1000)
  if retry_seconds < 1 then
    retry_seconds = 1
  end
  -- 拒绝时同样续期，保证窗口内的计数不会因过期而提前清零。
  redis.call('PEXPIRE', key, window_ms)
  return {0, 0, retry_seconds, used}
end

redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms)
return {1, limit - used - 1, 0, used + 1}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    """一次限流检查的结果。

    ``degraded`` 表示本次判定来自降级路径（后端不可用时放行），
    调用方据此记录可观测信号，而不是当成正常放行。
    """

    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int = 0
    degraded: bool = False


class RateLimiter(Protocol):
    """限流后端协议；memory 与 redis 实现沿用同一契约。"""

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        """记录一次命中并返回决策。"""
        ...

    def close(self) -> None:
        """释放后端资源；进程退出或应用关闭时调用。"""
        ...


@dataclass
class _Bucket:
    window_seconds: int
    hits: deque[float]


class InMemoryRateLimiter:
    """线程安全的进程内滑动窗口限流器。

    以 ``time.monotonic`` 为时钟；测试可注入替代时钟，避免 sleep。
    仅适用于单实例部署：多副本各自计数，不构成全局配额。
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
        _validate_policy(limit=limit, window_seconds=window_seconds)

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

    def close(self) -> None:
        """进程内实现无外部资源，保持与协议一致的空实现。"""
        with self._lock:
            self._buckets.clear()

    def _sweep(self, now: float) -> None:
        expired = [
            key
            for key, bucket in self._buckets.items()
            if not bucket.hits or bucket.hits[-1] <= now - bucket.window_seconds
        ]
        for key in expired:
            del self._buckets[key]


class RedisRateLimiter:
    """以 Redis 为共享计数的滑动窗口限流器。

    连接参数全部来自 ``Settings``：``socket_timeout`` / ``socket_connect_timeout``
    限制单次往返耗时；重试策略显式设为零次，否则 redis-py 默认重试会把
    一次不可达放大到数秒，直接拖住 HTTP 线程。

    后端不可用时 fail-open（放行 + error 日志），见模块文档与 ADR-0031。
    """

    def __init__(
        self,
        client: redis.Redis,
        *,
        key_prefix: str,
    ) -> None:
        self._client = client
        self._key_prefix = key_prefix.rstrip(":")
        self._script = client.register_script(_SLIDING_WINDOW_LUA)

    @classmethod
    def from_settings(cls, settings: Settings) -> RedisRateLimiter:
        """按配置创建客户端。不在构造期建立连接，首个请求时才握手。"""
        client = redis.Redis.from_url(
            settings.rate_limit_redis_url,
            socket_timeout=settings.rate_limit_redis_socket_timeout_seconds,
            socket_connect_timeout=settings.rate_limit_redis_connect_timeout_seconds,
            max_connections=settings.rate_limit_redis_max_connections,
            # 默认 Retry 会重试 3 次并退避，把 0.5s 的连接失败放大到数秒。
            retry=Retry(NoBackoff(), 0),
            retry_on_timeout=False,
            # 只做计数，不需要 bytes；decode_responses 让返回值可直接比较。
            decode_responses=True,
            client_name="ddp-rate-limit",
        )
        return cls(client, key_prefix=settings.rate_limit_redis_key_prefix)

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        _validate_policy(limit=limit, window_seconds=window_seconds)

        try:
            raw = self._script(
                keys=[self._bucket_key(key, window_seconds)],
                args=[window_seconds * 1000, limit, _unique_member()],
            )
            return _decision_from_script(raw, limit=limit)
        except (RedisError, OSError) as exc:
            # fail-open：限流是保护措施，Redis 故障不得升级为业务不可用。
            # 这里不记录键（含来源标识），只记录故障本身；请求级细节由中间件
            # 以脱敏形式记录，避免把客户端 IP 写进日志。
            logger.error(
                "Redis 限流后端不可用，本次按 fail-open 放行 error=%s",
                type(exc).__name__,
                exc_info=True,
            )
            return RateLimitDecision(
                allowed=True,
                limit=limit,
                remaining=limit,
                retry_after_seconds=0,
                degraded=True,
            )

    def close(self) -> None:
        self._client.close()

    def _bucket_key(self, key: str, window_seconds: int) -> str:
        """键含桶名、窗口标识与来源，避免不同策略/窗口互相累计。

        滑动窗口没有离散的 ``window_start`` 分界：单个键内以 ZSET 成员时间戳
        表示命中，``PEXPIRE`` 保证键最多存活一个窗口，因此不存在跨窗口累积。
        这里保留 ``{window}s`` 段，确保不同窗口配置不会共用同一份计数。
        """
        return f"{self._key_prefix}:{window_seconds}s:{key}"


def _validate_policy(*, limit: int, window_seconds: int) -> None:
    if limit < 1:
        raise ValueError("limit 必须大于 0")
    if window_seconds < 1:
        raise ValueError("window_seconds 必须大于 0")


def _unique_member() -> str:
    """单次命中的 ZSET 成员。

    必须唯一：ZSET 按成员去重，同毫秒内的多次命中若成员相同会被少计。
    """
    return uuid4().hex


def _decision_from_script(raw: Sequence[object], *, limit: int) -> RateLimitDecision:
    """把 Lua 返回值转成决策对象；形状异常按后端故障处理（由调用方 catch）。"""
    if not isinstance(raw, list | tuple) or len(raw) != 4:
        raise RedisError(f"限流脚本返回格式非法：{raw!r}")
    allowed, remaining, retry_after, _used = (int(item) for item in raw)
    return RateLimitDecision(
        allowed=bool(allowed),
        limit=limit,
        remaining=max(0, remaining),
        retry_after_seconds=max(0, retry_after),
    )


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """按配置构建限流后端。

    默认 ``memory``（单实例）；``redis`` 用于多副本共享配额。
    URL 合法性由 ``Settings.validate_runtime_security`` 在启动时把关，
    这里只按枚举分派。
    """
    if settings.rate_limit_backend == "redis":
        return RedisRateLimiter.from_settings(settings)
    return InMemoryRateLimiter()
