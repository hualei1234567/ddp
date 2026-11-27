"""Webhook 出站登记（阶段 6）：业务事件 → 待投递队列。

## 为什么要有这一层

阶段 5 交付了订阅 CRUD 与投递用例，但**投递用例没有任何生产调用点**：
订阅建得出来，事件送不出去。本模块补上缺失的那一段——把业务事件
登记成「待投递记录」，由 :class:`app.workers.webhook_outbound.WebhookOutboundWorker`
拉取并真实投递。

## 为什么单独一张队列表，而不是复用 outbox_events

``outbox_events`` 的 ``dispatch`` 通道是**平台内部**事件投递（校验/导出/导入…），
消费者是平台自己的 Worker，白名单在 ``scripts/check_outbox_events.py``。
对外 HTTP 投递的失败语义（连续失败自动 PAUSED 订阅、退避重试、死信）与内部
投递并不等价，混在一张表里会让「订阅被熔断」表现为一条普通内部死信，
运营分不清是订阅问题还是积压问题。因此本模块使用独立的
``webhook_outbox`` 表（迁移 0086），语义单一、边界清楚。

## 事务边界

登记发生在**业务事务内**：业务写入失败则不投递，业务成功才投递。
这与 outbox 的设计一致——事件与业务事实同生共死。

## 开关语义

``DDP_WEBHOOK_OUTBOUND_ENABLED`` 默认 false：关闭时 :meth:`OutboundRegistrar.register`
是 no-op，**不产生任何数据库写入**，业务行为与旧版完全一致。开关关闭时
订阅本身仍可创建（那是纯 CRUD），只是事件不会出站。

## 事件类型白名单

对外投递的事件类型必须显式登记，避免"把内部留痕事件意外推给外部"。
白名单在 :data:`OUTBOUND_EVENT_TYPES`，与
``docs/architecture/outbox事件语义界定-v1.0.md`` 的归类表同步维护。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

logger = logging.getLogger("ddp.application.webhook.outbound")


#: 允许对外投递的业务事件类型（白名单）。
#:
#: 选取原则：下游真正关心、且不含敏感明细的状态变更。
#: - 标注提交状态变更（含复核/仲裁流转）：下游据此同步进度；
#: - 标注员熔断：下游据此停止派单或提示人工处理；
#: - 回流案例受理：下游据此排队评测。
#:
#: **刻意不包含**审计明细、Token 用量、黄金题答案访问等敏感或高噪声事件。
OUTBOUND_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "annotation.submission.status_changed",
        "annotation.review.status_changed",
        "annotation.arbitration.status_changed",
        "annotation.honeypot.paused",
        "annotation.backflow.received",
    }
)


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """一次登记的结果。"""

    event_type: str
    #: 命中的订阅数（已登记的待投递记录数）。
    enqueued: int
    #: 因已有同订阅同事件记录而跳过的数量（幂等命中）。
    skipped: int


class OutboundRegistrar:
    """把业务事件登记成待投递记录。

    **不提交事务**：与业务写入共用调用方的 Session，由调用方统一提交。
    """

    def __init__(
        self,
        subscriptions: SubscriptionLookup,
        outbox: OutboundQueueWriter,
        *,
        enabled: bool,
    ) -> None:
        self._subscriptions = subscriptions
        self._outbox = outbox
        self._enabled = enabled

    def register(
        self,
        *,
        project_id: UUID,
        event_type: str,
        event_id: UUID,
        payload: Mapping[str, object],
        now: datetime | None = None,
    ) -> RegistrationResult:
        """登记一条业务事件到所有命中的 ACTIVE 订阅。

        开关关闭 / 事件类型不在白名单 / 无命中订阅时返回零结果，
        **不产生任何写入**。
        """
        if not self._enabled:
            return RegistrationResult(event_type, 0, 0)
        if event_type not in OUTBOUND_EVENT_TYPES:
            logger.debug(
                "事件类型不在对外投递白名单，跳过",
                extra={"event_type": event_type},
            )
            return RegistrationResult(event_type, 0, 0)

        moment = now or datetime.now(UTC)
        subscription_ids = self._subscriptions.active_subscription_ids(project_id, event_type)
        if not subscription_ids:
            return RegistrationResult(event_type, 0, 0)

        enqueued = 0
        skipped = 0
        for subscription_id in subscription_ids:
            created = self._outbox.enqueue(
                project_id=project_id,
                subscription_id=subscription_id,
                event_id=event_id,
                event_type=event_type,
                payload=dict(payload),
                now=moment,
            )
            if created:
                enqueued += 1
            else:
                # 幂等命中：同订阅同事件已登记过，不重复推送。
                skipped += 1
        if enqueued:
            logger.info(
                "业务事件已登记对外投递",
                extra={
                    "event_type": event_type,
                    "event_id": str(event_id),
                    "enqueued": enqueued,
                    "skipped": skipped,
                },
            )
        return RegistrationResult(event_type, enqueued, skipped)


# ---- 协议（应用层只认协议，不认 SQLAlchemy） ----


class SubscriptionLookup(Protocol):
    """订阅查询端口：返回命中该事件类型的 ACTIVE 订阅 id。

    只返回 id 而非完整订阅对象：登记阶段不需要明文 secret（明文只在
    真正投递时才在内存解密），少解密一次少一次暴露面。
    """

    def active_subscription_ids(self, project_id: UUID, event_type: str) -> tuple[UUID, ...]: ...


class OutboundQueueWriter(Protocol):
    """出站队列写入端口。"""

    def enqueue(
        self,
        *,
        project_id: UUID,
        subscription_id: UUID,
        event_id: UUID,
        event_type: str,
        payload: dict[str, object],
        now: datetime,
    ) -> bool: ...


__all__ = [
    "OUTBOUND_EVENT_TYPES",
    "OutboundRegistrar",
    "RegistrationResult",
]
