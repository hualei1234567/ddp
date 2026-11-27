"""Webhook 订阅领域模型（阶段 5 / 蓝图 N5 生态对接）。

设计要点：

- **secret 只存 hash**：明文只在创建响应里返回一次，签名时用明文计算 HMAC；
- **签名**：`X-DDP-Signature: sha256=<hmac>`，附带 `X-DDP-Timestamp`，下游可验签 +
  防重放（时间戳窗口由下游决定）；
- **失败熔断**：连续失败计数递增，达 ``FAILURE_PAUSE_THRESHOLD`` 自动 PAUSED——
  订阅方地址挂了不能拖垮 outbox 投递，也不能让平台一直重试垃圾地址；
- 纯函数 ``should_deliver`` / ``next_state_after`` 便于单测，不触 DB。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import UUID

from app.domain.annotation.errors import DomainValidationError

# 连续失败达此值自动 PAUSED
FAILURE_PAUSE_THRESHOLD = 5
# 单个事件最大投递尝试次数（由调用方按层重试实现）
MAX_ATTEMPTS_PER_EVENT = 3
# 投递超时（秒），由应用层 HTTP 客户端控制
DELIVERY_TIMEOUT_SECONDS = 5

_SIGNATURE_PREFIX = "sha256="


class WebhookSubscriptionStatus:
    """订阅状态（与迁移 CHECK 枚举一致）。"""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"


def normalize_url(raw: str) -> str:
    """校验并规范化订阅 URL：只接受 https 或本机 http（防外传与误配）。

    生产场景天然是 https；本地联调允许 http，但**必须指向本机**，
    避免把事件推到任意外部地址。
    """
    url = raw.strip()
    if not url:
        raise DomainValidationError("订阅 URL 不能为空")
    if len(url) > 2048:
        raise DomainValidationError("订阅 URL 超长（上限 2048）")
    from urllib.parse import urlparse

    lowered = url.lower()
    if lowered.startswith("https://"):
        # https 也必须有合法主机，避免把"https://"这种残缺地址当合法
        host = urlparse(url).hostname
        if not host:
            raise DomainValidationError("订阅 URL 缺少主机名")
        return url
    if lowered.startswith("http://"):
        host = urlparse(url).hostname
        if not host:
            raise DomainValidationError("订阅 URL 缺少主机名")
        # 允许的本机地址（IPv4/localhost/IPv6 回环）。urlparse 已正确处理
        # 带括号的 IPv6 字面量（``[::1]``），因此无需再手工切分。
        if host not in {"127.0.0.1", "localhost", "::1", "[::1]"}:
            raise DomainValidationError(f"http 订阅地址必须是本机地址（{host}），外联请用 https")
        return url
    raise DomainValidationError("订阅 URL 必须是 http:// 或 https:// 开头")


def hash_secret(secret: str) -> str:
    """明文 secret → SHA-256 十六进制（只存 hash，不存明文）。"""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


# 对称加密块大小（字节）
_ENCRYPT_BLOCK = 32


def _keystream(key: bytes, counter: int) -> bytes:
    """用 HMAC-SHA256 派生密钥流（counter 作为消息，按块推进）。

    这是**真实流密码**（XOR 加密），但**无认证**——防篡改需在部署层用 TLS
    或下游侧的验签保证。强度如实标注：不是 AEAD，不是生产级密钥管理。
    """
    return hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha256).digest()


def encrypt_secret(plain: str, key: bytes) -> str:
    """加密订阅明文 secret（base64 密文）。"""
    raw = plain.encode("utf-8")
    keystream = b""
    offset = 0
    while offset < len(raw):
        keystream += _keystream(key, offset // _ENCRYPT_BLOCK)
        offset += _ENCRYPT_BLOCK
    streamed = keystream[: len(raw)]
    return base64.b64encode(bytes(a ^ b for a, b in zip(raw, streamed, strict=False))).decode(
        "ascii"
    )


def decrypt_secret(cipher_b64: str, key: bytes) -> str:
    """解密订阅明文 secret（与 :func:`encrypt_secret` 互逆）。"""
    raw = base64.b64decode(cipher_b64.encode("ascii"))
    keystream = b""
    offset = 0
    while offset < len(raw):
        keystream += _keystream(key, offset // _ENCRYPT_BLOCK)
        offset += _ENCRYPT_BLOCK
    streamed = keystream[: len(raw)]
    return bytes(a ^ b for a, b in zip(raw, streamed, strict=False)).decode("utf-8")


def verify_secret_hash(secret: str, secret_hash: str) -> bool:
    """验签前校验：明文与库内 hash 是否匹配（常量时间比较）。"""
    return hmac.compare_digest(hash_secret(secret), secret_hash)


def sign_payload(secret: str, body: bytes, timestamp: str) -> str:
    """生成投递签名：HMAC-SHA256(secret, timestamp + body)。

    时间戳进签名，下游可拒过期重放（重放窗口由下游配置）。
    """
    message = f"{timestamp}\n".encode() + body
    return _SIGNATURE_PREFIX + hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class WebhookSubscription:
    """一条 Webhook 订阅（领域聚合根）。

    不变量：URL 已校验、event_types 非空、secret_hash 是 64 位十六进制、
    failure 计数非负、状态取合法枚举。
    """

    id: UUID
    project_id: UUID
    url: str
    secret_hash: str
    event_types: tuple[str, ...]
    status: str = WebhookSubscriptionStatus.ACTIVE
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    #: **临时字段**：创建时携带明文 secret，落库前由仓储加密、之后置空。
    #: 不参与相等比较与 repr（避免明文进日志或被误持久化）。
    secret_plain: str | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", normalize_url(self.url))
        if not self.event_types:
            raise DomainValidationError("订阅必须指定至少一个事件类型")
        if len(self.secret_hash) != 64:
            raise DomainValidationError("secret_hash 必须是 64 位十六进制")
        if self.consecutive_failures < 0:
            raise DomainValidationError("consecutive_failures 不能为负")
        if self.status not in {WebhookSubscriptionStatus.ACTIVE, WebhookSubscriptionStatus.PAUSED}:
            raise DomainValidationError(f"订阅状态非法：{self.status}")

    def matches(self, event_type: str) -> bool:
        """该事件类型是否命中本订阅。"""
        return event_type in self.event_types

    @property
    def is_paused(self) -> bool:
        return self.status == WebhookSubscriptionStatus.PAUSED


def should_deliver(subscription: WebhookSubscription, event_type: str) -> bool:
    """是否向该订阅投递该事件（PAUSED 一律不投，即使命中类型）。"""
    return not subscription.is_paused and subscription.matches(event_type)


@dataclass(frozen=True, slots=True)
class SubscriptionTransition:
    """一次投递后的订阅状态迁移结果。"""

    subscription: WebhookSubscription
    action: str  # "none" / "paused" / "reset"


def next_state_after(
    subscription: WebhookSubscription,
    *,
    success: bool,
    now: datetime,
) -> SubscriptionTransition:
    """投递后的订阅状态迁移（纯函数）。

    规则：
    - 成功 → 清零失败计数、刷新 last_success_at、若之前 PAUSED 则**恢复 ACTIVE**；
    - 失败 → 失败计数 +1，刷新 last_error_at；计数达阈值则自动 **PAUSED**；
    - 未达阈值 → 保持当前状态（可能仍是 ACTIVE）。
    """
    if success:
        cleared = replace(
            subscription,
            consecutive_failures=0,
            last_success_at=now,
            last_error_at=None,
            last_error_message=None,
            status=WebhookSubscriptionStatus.ACTIVE,
            updated_at=now,
        )
        action = "reset" if subscription.consecutive_failures > 0 else "none"
        return SubscriptionTransition(subscription=cleared, action=action)

    failures = subscription.consecutive_failures + 1
    if failures >= FAILURE_PAUSE_THRESHOLD:
        paused = replace(
            subscription,
            consecutive_failures=failures,
            last_error_at=now,
            last_error_message="连续投递失败达阈值，自动暂停",
            status=WebhookSubscriptionStatus.PAUSED,
            updated_at=now,
        )
        return SubscriptionTransition(subscription=paused, action="paused")

    updated = replace(
        subscription,
        consecutive_failures=failures,
        last_error_at=now,
        updated_at=now,
    )
    return SubscriptionTransition(subscription=updated, action="none")


def build_signature_headers(secret: str, body: bytes, timestamp: str) -> Mapping[str, str]:
    """构造投递请求头（签名 + 时间戳）。"""
    return {
        "X-DDP-Signature": sign_payload(secret, body, timestamp),
        "X-DDP-Timestamp": timestamp,
        "Content-Type": "application/json",
    }
