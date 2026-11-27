"""Webhook 出站投递（阶段 5）：从 outbox 取事件 → 匹配订阅 → 签名投递。

## 与 outbox 的关系

本模块**不改变** outbox 的既有投递（校验事件 → 校验处理器）。它面向
**业务事件**（标注完成、熔断、回流等），在事件进入 outbox 的同一事务里
可选登记「待投递」，由 :class:`WebhookDeliveryWorker` 拉取投递。

## 开关与失败语义

- 开关 ``DDP_WEBHOOK_OUTBOUND_ENABLED`` **默认 false**：关闭时
  ``register_outbox_event`` 是 no-op，行为与旧版完全一致；
- 投递失败 → 调用 :func:`next_state_after` 更新订阅状态；
  **连续 5 次失败自动 PAUSED**（不再投递，需人工恢复）；
- 单次投递超时 5s，最多 3 次尝试。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from app.domain.webhook.subscription import (
    DELIVERY_TIMEOUT_SECONDS,
    MAX_ATTEMPTS_PER_EVENT,
    SubscriptionTransition,
    WebhookSubscription,
    build_signature_headers,
    next_state_after,
)
from app.infrastructure.adapter_url_guard import (
    AdapterHttpFailure,
    ensure_safe_adapter_url,
)
from app.infrastructure.repositories.webhook_subscription import (
    SqlWebhookSubscriptionRepository,
)

logger = logging.getLogger("ddp.application.webhook")


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """一次投递的汇总结果。"""

    event_type: str
    delivered: int
    failed: int
    paused: int
    skipped_paused: int


class WebhookDeliveryUseCases:
    """订阅投递的核心逻辑（worker 调用）。"""

    def __init__(
        self,
        subscriptions: SqlWebhookSubscriptionRepository,
        *,
        transport: Callable[[str, dict[str, str], bytes], int] | None = None,
    ) -> None:
        self._subscriptions = subscriptions
        # transport 可注入（测试用假函数）；生产默认 httpx
        self._transport = transport or self._httpx_post

    def deliver_event(
        self,
        *,
        project_id: UUID,
        event_type: str,
        payload: MappingLike,
        now: datetime | None = None,
    ) -> DeliveryOutcome:
        """向项目内所有命中的 ACTIVE 订阅投递一个事件。"""
        moment = now or datetime.now(UTC)
        targets = self._subscriptions.active_for_event(project_id, event_type)
        if not targets:
            return DeliveryOutcome(event_type, 0, 0, 0, 0)

        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        delivered = failed = paused = skipped = 0

        for target in targets:
            outcome, transition = self._deliver_one(target, body, moment)
            if outcome == "success":
                delivered += 1
            elif outcome == "paused":
                paused += 1
            else:
                failed += 1
            self._subscriptions.replace_state(target.subscription.id, transition.subscription)
            if transition.action == "paused":
                logger.warning(
                    "Webhook 订阅因连续失败自动暂停",
                    extra={
                        "subscription_id": target.subscription.id,
                        "url": target.subscription.url,
                    },
                )
        return DeliveryOutcome(
            event_type=event_type,
            delivered=delivered,
            failed=failed,
            paused=paused,
            skipped_paused=skipped,
        )

    def _deliver_one(
        self,
        target,
        body: bytes,
        moment: datetime,  # noqa: ANN001
    ) -> tuple[str, SubscriptionTransition]:
        """投递单条订阅，返回 (outcome, 迁移后状态)。"""
        timestamp = str(int(moment.timestamp()))
        headers = build_signature_headers(target.secret, body, timestamp)

        last_error = "投递失败"
        for attempt in range(1, MAX_ATTEMPTS_PER_EVENT + 1):
            try:
                status = self._transport(target.subscription.url, headers, body)
                if 200 <= status < 300:
                    return "success", next_state_after(
                        target.subscription, success=True, now=moment
                    )
                last_error = f"下游返回 {status}"
            except AdapterHttpFailure as exc:
                # URL 守卫拒绝：地址本身不安全，重试无意义——但仍计失败，
                # 让连续失败达阈值后自动 PAUSED，避免反复打扰非法地址。
                last_error = f"URL 守卫拒绝：{exc}"
            except Exception as exc:  # noqa: BLE001 - 投递异常必须计入失败计数
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < MAX_ATTEMPTS_PER_EVENT:
                time.sleep(0.05)  # 简化退避；生产可按指数退避

        transition = next_state_after(target.subscription, success=False, now=moment)
        # 把错误信息写进订阅（供运营排查）
        final = SubscriptionTransition(
            subscription=_with_error(transition.subscription, last_error),
            action=transition.action,
        )
        return "paused" if final.action == "paused" else "failed", final

    @staticmethod
    def _httpx_post(url: str, headers: dict[str, str], body: bytes) -> int:
        """默认传输：httpx POST，超时 5s。

        出站前必须过 URL 守卫（SSRF 防护）：订阅地址是**用户提供的外部输入**，
        直连等于把平台变成任意地址的探测器。守卫只允许 http/https、
        拒绝内嵌凭据，并按 DNS 解析结果拦截私网/保留地址。

        ``follow_redirects=False``：重定向目标从未经校验——跟随会把"校验过的
        首跳"变成"任意目标"，直接绕过守卫。
        """
        ensure_safe_adapter_url(url, context="Webhook 投递")
        with httpx.Client(
            timeout=DELIVERY_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            response = client.post(url, content=body, headers=headers)
            return response.status_code


def _with_error(subscription: WebhookSubscription, message: str) -> WebhookSubscription:
    from dataclasses import replace

    return replace(subscription, last_error_message=message[:500])


# 类型别名：payload 是任意 JSON 对象
MappingLike = dict[str, Any]
