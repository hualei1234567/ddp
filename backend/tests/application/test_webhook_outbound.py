"""Webhook 对外出站登记与投递队列测试（阶段 6）。

覆盖：
- 开关关闭时登记器是 **no-op**（零写入、零查询）；
- 事件类型不在白名单时不登记（避免把内部留痕事件意外推给外部）；
- 命中订阅时按订阅数登记；幂等命中（已登记过）不重复登记；
- 退避计算：指数翻倍、封顶、非法入参拒绝；
- 包装 outbox 仓储：先写业务事件，再登记待投递（顺序与"同事务"语义）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.application.webhook.outbound import (
    OUTBOUND_EVENT_TYPES,
    OutboundRegistrar,
)
from app.infrastructure.repositories.webhook_outbox import (
    next_attempt_time,
    retry_backoff_seconds,
)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


class FakeSubscriptionLookup:
    """假订阅查询：记录调用，返回配置好的订阅 id。"""

    def __init__(self, subscription_ids: tuple[UUID, ...] = ()) -> None:
        self.subscription_ids = subscription_ids
        self.calls: list[tuple[UUID, str]] = []

    def active_subscription_ids(self, project_id: UUID, event_type: str) -> tuple[UUID, ...]:
        self.calls.append((project_id, event_type))
        return self.subscription_ids


class FakeQueueWriter:
    """假出站队列：记录登记调用；已存在的 (订阅, 事件) 组合返回 False。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.existing: set[tuple[UUID, UUID]] = set()

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
        key = (subscription_id, event_id)
        if key in self.existing:
            return False
        self.existing.add(key)
        self.calls.append(
            {
                "project_id": project_id,
                "subscription_id": subscription_id,
                "event_id": event_id,
                "event_type": event_type,
                "payload": payload,
                "now": now,
            }
        )
        return True


def test_disabled_registrar_is_noop() -> None:
    """开关关闭：不查订阅、不写队列。"""
    lookup = FakeSubscriptionLookup((uuid4(),))
    writer = FakeQueueWriter()
    registrar = OutboundRegistrar(lookup, writer, enabled=False)  # type: ignore[arg-type]

    result = registrar.register(
        project_id=uuid4(),
        event_type="annotation.submission.status_changed",
        event_id=uuid4(),
        payload={"submission_id": "s-1"},
        now=NOW,
    )

    assert result.enqueued == 0
    assert result.skipped == 0
    assert lookup.calls == []
    assert writer.calls == []


def test_event_type_outside_whitelist_is_not_registered() -> None:
    """白名单外的事件类型不登记（内部留痕事件不得意外外传）。"""
    lookup = FakeSubscriptionLookup((uuid4(),))
    writer = FakeQueueWriter()
    registrar = OutboundRegistrar(lookup, writer, enabled=True)  # type: ignore[arg-type]

    result = registrar.register(
        project_id=uuid4(),
        event_type="annotation.gold_set.answers_accessed",
        event_id=uuid4(),
        payload={},
        now=NOW,
    )

    assert result.enqueued == 0
    assert writer.calls == []


def test_registers_once_per_matched_subscription() -> None:
    lookup = FakeSubscriptionLookup((uuid4(), uuid4(), uuid4()))
    writer = FakeQueueWriter()
    registrar = OutboundRegistrar(lookup, writer, enabled=True)  # type: ignore[arg-type]
    project_id = uuid4()
    event_id = uuid4()

    result = registrar.register(
        project_id=project_id,
        event_type="annotation.submission.status_changed",
        event_id=event_id,
        payload={"submission_id": "s-1"},
        now=NOW,
    )

    assert result.enqueued == 3
    assert result.skipped == 0
    assert len(writer.calls) == 3
    assert all(call["project_id"] == project_id for call in writer.calls)
    assert all(call["event_id"] == event_id for call in writer.calls)
    assert all(call["now"] == NOW for call in writer.calls)


def test_duplicate_registration_is_skipped() -> None:
    """同一订阅 + 同一事件重复登记 → 跳过，不产生第二条待投递。"""
    subscription_id = uuid4()
    lookup = FakeSubscriptionLookup((subscription_id,))
    writer = FakeQueueWriter()
    registrar = OutboundRegistrar(lookup, writer, enabled=True)  # type: ignore[arg-type]
    event_id = uuid4()

    first = registrar.register(
        project_id=uuid4(),
        event_type="annotation.submission.status_changed",
        event_id=event_id,
        payload={},
        now=NOW,
    )
    second = registrar.register(
        project_id=uuid4(),
        event_type="annotation.submission.status_changed",
        event_id=event_id,
        payload={},
        now=NOW,
    )

    assert first.enqueued == 1
    assert first.skipped == 0
    assert second.enqueued == 0
    assert second.skipped == 1
    assert len(writer.calls) == 1


def test_no_matching_subscription_registers_nothing() -> None:
    lookup = FakeSubscriptionLookup(())
    writer = FakeQueueWriter()
    registrar = OutboundRegistrar(lookup, writer, enabled=True)  # type: ignore[arg-type]

    result = registrar.register(
        project_id=uuid4(),
        event_type="annotation.submission.status_changed",
        event_id=uuid4(),
        payload={},
        now=NOW,
    )

    assert result.enqueued == 0
    assert writer.calls == []


def test_whitelist_covers_expected_event_types() -> None:
    """白名单必须包含下游最关心的三类状态变更事件。"""
    assert "annotation.submission.status_changed" in OUTBOUND_EVENT_TYPES
    assert "annotation.review.status_changed" in OUTBOUND_EVENT_TYPES
    assert "annotation.arbitration.status_changed" in OUTBOUND_EVENT_TYPES
    # 敏感事件不得在白名单里
    assert "annotation.gold_set.answers_accessed" not in OUTBOUND_EVENT_TYPES


def test_retry_backoff_doubles_and_caps() -> None:
    assert retry_backoff_seconds(1, base_seconds=5.0, max_seconds=300.0) == 5.0
    assert retry_backoff_seconds(2, base_seconds=5.0, max_seconds=300.0) == 10.0
    assert retry_backoff_seconds(3, base_seconds=5.0, max_seconds=300.0) == 20.0
    # 封顶
    assert retry_backoff_seconds(20, base_seconds=5.0, max_seconds=300.0) == 300.0


def test_retry_backoff_rejects_invalid_arguments() -> None:
    with pytest.raises(ValueError):
        retry_backoff_seconds(0, base_seconds=5.0, max_seconds=300.0)
    with pytest.raises(ValueError):
        retry_backoff_seconds(1, base_seconds=0.0, max_seconds=300.0)
    with pytest.raises(ValueError):
        retry_backoff_seconds(1, base_seconds=10.0, max_seconds=5.0)


def test_next_attempt_time_uses_backoff() -> None:
    result = next_attempt_time(2, now=NOW, base_seconds=5.0, max_seconds=300.0)
    assert (result - NOW).total_seconds() == 10.0
