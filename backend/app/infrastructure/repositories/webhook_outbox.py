"""Webhook 出站投递队列 SQLAlchemy 仓储（阶段 6）。

职责边界：
- **登记**（:meth:`SqlWebhookOutboxRepository.enqueue`）：业务事务内把待投递
  事件写进队列，与业务写入同事务——业务回滚则不投递，业务成功才投递。
- **抢占与流转**（:class:`SqlWebhookOutboxQueue`）：Worker 侧按
  ``status='PENDING' AND available_at <= now`` 取一条，投递后标
  ``DELIVERED`` / 退避重试 / 达上限标 ``FAILED``。

并发口径与内部 outbox 一致：PostgreSQL 用 ``FOR UPDATE SKIP LOCKED``，
SQLite 无行级锁，依赖单写事务串行化（仅测试/本地使用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.infrastructure.db.models.webhook_outbox import WebhookOutboxModel as _Model
from app.infrastructure.repositories._collection_sql import as_utc

_PENDING = "PENDING"
_DELIVERED = "DELIVERED"
_FAILED = "FAILED"

#: 单条错误信息长度上限（与 ``outbox_events.last_error`` 同口径）。
_MAX_ERROR_LENGTH = 2000


@dataclass(frozen=True, slots=True)
class PendingDelivery:
    """一条待投递的对外事件（Worker 视角）。"""

    id: UUID
    project_id: UUID
    subscription_id: UUID
    event_id: UUID
    event_type: str
    payload: dict[str, object]
    attempts: int
    #: 事件发生时间（登记时刻）：写进投递信封，让下游知道事件"什么时候发生"。
    created_at: datetime


class SqlWebhookOutboxRepository:
    """出站队列写入侧（不提交，由调用方的 UoW 统一提交）。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(
        self,
        *,
        project_id: UUID,
        subscription_id: UUID,
        event_id: UUID,
        event_type: str,
        payload: dict[str, object],
        now: datetime,
    ) -> bool:
        """登记一条待投递事件；已登记过（同订阅同事件）返回 ``False``。

        幂等靠 ``(subscription_id, event_id)`` 唯一约束：重复登记撞唯一键，
        调用方据此判定"已登记"而不是产生第二条待投递——否则下游会收到
        重复事件，重试场景下尤其明显。
        """
        existing = self._session.scalar(
            select(_Model.id).where(
                _Model.subscription_id == subscription_id,
                _Model.event_id == event_id,
            )
        )
        if existing is not None:
            return False
        self._session.add(
            _Model(
                id=uuid4(),
                project_id=project_id,
                subscription_id=subscription_id,
                event_id=event_id,
                event_type=event_type,
                payload=dict(payload),
                status=_PENDING,
                attempts=0,
                available_at=now,
                created_at=now,
            )
        )
        return True


class SqlWebhookOutboxQueue:
    """出站队列消费侧（Worker）。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def claim_next(self, *, now: datetime) -> PendingDelivery | None:
        """取一条到期可投递的待投递记录（PostgreSQL 下加行锁跳过已占用行）。"""
        statement = (
            select(_Model)
            .where(
                _Model.status == _PENDING,
                _Model.available_at <= now,
            )
            .order_by(_Model.available_at, _Model.created_at, _Model.id)
            .limit(1)
        )
        if self._dialect() == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        row = self._session.scalar(statement)
        if row is None:
            return None
        return PendingDelivery(
            id=row.id,
            project_id=row.project_id,
            subscription_id=row.subscription_id,
            event_id=row.event_id,
            event_type=row.event_type,
            payload=dict(row.payload or {}),
            attempts=row.attempts,
            created_at=as_utc(row.created_at) or now,
        )

    def mark_delivered(self, delivery_id: UUID, *, now: datetime) -> bool:
        """标记投递成功。返回 ``False`` 表示记录已不在 PENDING（并发方处理了）。"""
        result = self._session.execute(
            update(_Model)
            .where(_Model.id == delivery_id, _Model.status == _PENDING)
            .values(status=_DELIVERED, delivered_at=now, last_error=None)
        )
        self._session.flush()
        return result.rowcount == 1

    def mark_retry(
        self,
        delivery_id: UUID,
        *,
        attempts: int,
        available_at: datetime,
        last_error: str,
    ) -> bool:
        """标记为可重试（推后可见时间 + 记失败原因）。"""
        result = self._session.execute(
            update(_Model)
            .where(_Model.id == delivery_id, _Model.status == _PENDING)
            .values(
                attempts=attempts,
                available_at=available_at,
                last_error=last_error[:_MAX_ERROR_LENGTH],
            )
        )
        self._session.flush()
        return result.rowcount == 1

    def mark_dead_letter(
        self,
        delivery_id: UUID,
        *,
        attempts: int,
        last_error: str,
    ) -> bool:
        """标记死信（达重试上限，不再自动投递）。"""
        result = self._session.execute(
            update(_Model)
            .where(_Model.id == delivery_id, _Model.status == _PENDING)
            .values(
                status=_FAILED,
                attempts=attempts,
                last_error=last_error[:_MAX_ERROR_LENGTH],
            )
        )
        self._session.flush()
        return result.rowcount == 1

    def _dialect(self) -> str:
        return self._session.get_bind().dialect.name

    def commit(self) -> None:
        """提交本队列所在的事务。

        Worker 侧需要「状态流转 + 提交」成对出现，暴露一个显式方法，
        比让调用方去摸 ``_session`` 私有属性干净。
        """
        self._session.commit()


def retry_backoff_seconds(attempts: int, *, base_seconds: float, max_seconds: float) -> float:
    """指数退避：``base * 2^(attempts-1)``，上限 ``max_seconds``。

    纯函数（便于单测）：``attempts`` 是已失败次数，第 1 次失败后等待
    ``base``，第 2 次 ``base*2``，依此类推。
    """
    if attempts < 1:
        raise ValueError("attempts 必须是正整数")
    if base_seconds <= 0:
        raise ValueError("base_seconds 必须为正数")
    if max_seconds < base_seconds:
        raise ValueError("max_seconds 不得小于 base_seconds")
    exponent = min(attempts - 1, 30)  # 防止 2**attempts 溢出
    return min(base_seconds * (2**exponent), max_seconds)


def next_attempt_time(
    attempts: int,
    *,
    now: datetime,
    base_seconds: float,
    max_seconds: float,
) -> datetime:
    """按退避算出下一次可投递时间。"""
    return now + timedelta(
        seconds=retry_backoff_seconds(
            attempts,
            base_seconds=base_seconds,
            max_seconds=max_seconds,
        )
    )


__all__ = [
    "PendingDelivery",
    "SqlWebhookOutboxQueue",
    "SqlWebhookOutboxRepository",
    "next_attempt_time",
    "retry_backoff_seconds",
]
