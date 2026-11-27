"""Webhook 出站投递 Worker（阶段 6）：拉取待投递记录 → 签名 → 发往下游。

运行方式::

    DDP_WEBHOOK_OUTBOUND_ENABLED=true DDP_WEBHOOK_ENCRYPTION_KEY=<key> \\
        python -m app.workers.webhook_outbound

**它补的是哪一段**：阶段 5 交付了订阅 CRUD 与投递用例，但投递用例
``deliver_event`` 在生产代码里没有调用点——订阅能建，事件送不出去。
本 Worker 从 ``webhook_outbox`` 表拉取待投递记录，用订阅明文 secret 计算
HMAC 签名并真实 POST 给下游，把能力真正接到链路上。

**幂等与并发**：待投递记录用 ``SELECT ... FOR UPDATE SKIP LOCKED`` 抢占
（SQLite 退化为单写事务串行化）；「同一订阅同一事件」由
``(subscription_id, event_id)`` 唯一约束保证只登记一次。

**失败语义**：

- 网络失败/下游非 2xx → 退避重试（指数退避，上限由配置给出）；
- 超过重投上限 → 记录死信（``FAILED``），不再自动投递，运营可见；
- 订阅侧连续失败达阈值 → 由投递用例把订阅置为 ``PAUSED``（自动熔断，
  见 ``app/domain/webhook/subscription.py``），避免一直打挂掉的下游。

**绝不伪造成功**：只有下游真的返回 2xx 才标 ``DELIVERED``。
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.application.webhook.delivery import DeliveryOutcome, WebhookDeliveryUseCases
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.infrastructure.db.session import create_session_factory, get_engine
from app.infrastructure.repositories.webhook_outbox import (
    PendingDelivery,
    SqlWebhookOutboxQueue,
    next_attempt_time,
)
from app.infrastructure.repositories.webhook_subscription import (
    SqlWebhookSubscriptionRepository,
)

logger = logging.getLogger("ddp.worker.webhook_outbound")

#: 出站传输协议：``(url, headers, body) -> 状态码``。
#: 与 :class:`app.application.webhook.delivery.WebhookDeliveryUseCases` 的
#: transport 参数同签名，便于注入假传输做测试。
Transport = Callable[[str, dict[str, str], bytes], int]


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """一轮投递的结果（Worker 视角）。"""

    status: str  # "idle" / "delivered" / "retry" / "dead_letter"
    event_type: str | None = None
    subscription_id: str | None = None
    error: str | None = None


class WebhookOutboundWorker:
    """对外 Webhook 投递 Worker。"""

    def __init__(
        self,
        factory: sessionmaker[Session],
        settings: Settings,
        *,
        transport: Transport | None = None,
    ) -> None:
        self._factory = factory
        self._settings = settings
        # transport 可注入：测试用假传输，不发真实网络。
        # 默认 None = 用生产 httpx 传输（带 SSRF 守卫）。
        self._transport = transport

    def process_once(self, *, now: datetime | None = None) -> DeliveryResult:
        """处理一条待投递记录。"""
        moment = now or datetime.now(UTC)
        with self._factory() as session:
            queue = SqlWebhookOutboxQueue(session)
            pending = queue.claim_next(now=moment)
            if pending is None:
                return DeliveryResult(status="idle")
            return self._deliver(session, queue, pending, moment)

    def _deliver(
        self,
        session: Session,
        queue: SqlWebhookOutboxQueue,
        pending: PendingDelivery,
        now: datetime,
    ) -> DeliveryResult:
        """投递一条记录并落状态。"""
        subscriptions = SqlWebhookSubscriptionRepository(
            session,
            encryption_key=self._settings.webhook_encryption_key.strip().encode("utf-8"),
        )
        use_cases = WebhookDeliveryUseCases(
            subscriptions,
            transport=self._transport,
            allow_private=self._settings.webhook_outbound_allow_private,
        )
        # 投递体用**信封**：下游必须能从请求体本身知道这是哪个事件、
        # 哪个事件 id，否则它只能靠订阅配置反推——换订阅就要改下游代码。
        # 信封字段名用 ``event_type`` / ``event_id`` / ``data``。
        envelope = {
            "event_type": pending.event_type,
            "event_id": str(pending.event_id),
            "project_id": str(pending.project_id),
            "occurred_at": pending.created_at.isoformat(),
            "data": dict(pending.payload),
        }
        try:
            outcome: DeliveryOutcome = use_cases.deliver_event(
                project_id=pending.project_id,
                event_type=pending.event_type,
                payload=envelope,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 - 投递异常必须计入重试，不得终止 Worker
            return self._fail(queue, pending, now, f"{type(exc).__name__}: {exc}")

        if outcome.delivered > 0 and outcome.failed == 0:
            queue.mark_delivered(pending.id, now=now)
            queue.commit()
            logger.info(
                "Webhook 事件已投递",
                extra={
                    "event_type": pending.event_type,
                    "subscription_id": str(pending.subscription_id),
                    "delivered": outcome.delivered,
                },
            )
            return DeliveryResult(
                status="delivered",
                event_type=pending.event_type,
                subscription_id=str(pending.subscription_id),
            )

        # 部分或全部失败：退避重试。订阅侧的连续失败计数由投递用例维护
        # （达阈值自动 PAUSED），这里只负责队列记录的重试与死信。
        #
        # 错误原因取订阅上的 ``last_error_message``（投递用例写入的真实原因，
        # 如"下游返回 500"）：只写"未完全成功"会让运维无法判断该修下游还是
        # 该修签名。
        error = self._collect_error(subscriptions, pending) or (
            f"投递未完全成功（delivered={outcome.delivered} failed={outcome.failed}）"
        )
        return self._fail(queue, pending, now, error)

    @staticmethod
    def _collect_error(
        subscriptions: SqlWebhookSubscriptionRepository,
        pending: PendingDelivery,
    ) -> str | None:
        """取该订阅记录的最后失败原因（投递用例写入）。"""
        from app.domain.identity.models import ProjectScope

        try:
            scope = ProjectScope(
                project_id=pending.project_id,
                # 仅用于仓储的项目隔离校验，不做权限判定（Worker 是系统身份）。
                actor_id=pending.project_id,
            )
            row = subscriptions.find(scope, pending.subscription_id)
        except Exception:  # noqa: BLE001 - 取错误原因失败不应影响主流程判定
            return None
        if row is None:
            return None
        return row.last_error_message

    def _fail(
        self,
        queue: SqlWebhookOutboxQueue,
        pending: PendingDelivery,
        now: datetime,
        error: str,
    ) -> DeliveryResult:
        """失败处理：未达上限退避重试，达上限标死信。"""
        attempts = pending.attempts + 1
        if attempts >= self._settings.webhook_outbound_max_attempts:
            queue.mark_dead_letter(
                pending.id,
                attempts=attempts,
                last_error=error,
            )
            queue.commit()
            logger.error(
                "Webhook 事件投递进入死信",
                extra={
                    "event_type": pending.event_type,
                    "subscription_id": str(pending.subscription_id),
                    "attempts": attempts,
                    "error": error,
                },
            )
            return DeliveryResult(
                status="dead_letter",
                event_type=pending.event_type,
                subscription_id=str(pending.subscription_id),
                error=error,
            )

        available_at = next_attempt_time(
            attempts,
            now=now,
            base_seconds=self._settings.webhook_outbound_retry_base_seconds,
            max_seconds=self._settings.webhook_outbound_retry_max_seconds,
        )
        queue.mark_retry(
            pending.id,
            attempts=attempts,
            available_at=available_at,
            last_error=error,
        )
        queue.commit()
        logger.warning(
            "Webhook 事件投递失败，将退避重试",
            extra={
                "event_type": pending.event_type,
                "subscription_id": str(pending.subscription_id),
                "attempts": attempts,
                "next_attempt_at": available_at.isoformat(),
                "error": error,
            },
        )
        return DeliveryResult(
            status="retry",
            event_type=pending.event_type,
            subscription_id=str(pending.subscription_id),
            error=error,
        )

    def run(
        self,
        *,
        stop_event: threading.Event | None = None,
        once: bool = False,
        drain: bool = False,
    ) -> None:
        stop = stop_event or threading.Event()
        logger.info(
            "Webhook 出站 Worker 已启动 poll_interval=%s max_attempts=%s",
            self._settings.webhook_outbound_poll_interval_seconds,
            self._settings.webhook_outbound_max_attempts,
        )
        while not stop.is_set():
            try:
                result = self.process_once()
            except Exception:  # noqa: BLE001 - 单条异常不得终止 Worker
                logger.exception("Webhook 出站投递时发生未预期异常，将在下一轮重试")
                if once or drain:
                    return
                stop.wait(self._settings.webhook_outbound_poll_interval_seconds)
                continue
            if once:
                return
            if drain and result.status == "idle":
                return
            if result.status == "idle":
                stop.wait(self._settings.webhook_outbound_poll_interval_seconds)
        logger.info("Webhook 出站 Worker 已停止")


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DDP Webhook 出站投递 Worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="最多投递一条后退出")
    mode.add_argument("--drain", action="store_true", help="投递到当前无可用记录后退出")
    args = parser.parse_args(argv)

    configure_logging()
    settings = get_settings()
    settings.validate_runtime_security()
    if not settings.webhook_outbound_enabled:
        # 开关关闭时不启动：避免"以为在投递"。与 worker 的 fail-closed 口径一致。
        logger.error("Webhook 出站未启用（DDP_WEBHOOK_OUTBOUND_ENABLED=false），拒绝启动")
        return 2

    worker = WebhookOutboundWorker(
        factory=create_session_factory(get_engine()),
        settings=settings,
    )

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    worker.run(stop_event=stop_event, once=args.once, drain=args.drain)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
