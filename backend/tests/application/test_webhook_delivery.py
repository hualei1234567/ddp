"""Webhook 投递应用测试（阶段 5）。

用**假 transport**（不发真实网络），验证：签名请求头、成功/失败计数、
连续 5 次失败自动 PAUSED、PAUSED 不再投递、成功自动恢复。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.webhook.delivery import WebhookDeliveryUseCases
from app.domain.webhook.subscription import (
    FAILURE_PAUSE_THRESHOLD,
    WebhookSubscription,
    WebhookSubscriptionStatus,
)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
KEY = b"test-key-32-bytes-long-enough-ok"


class FakeTransport:
    """记录投递请求，可配置每次返回的状态码。"""

    def __init__(self, status_codes: list[int] | None = None) -> None:
        self.status_codes = status_codes or [200]
        self.calls: list[dict[str, object]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes) -> int:
        status = self.status_codes[min(len(self.calls), len(self.status_codes) - 1)]
        self.calls.append({"url": url, "headers": headers, "body": body})
        return status


def _subscription(
    status: str = WebhookSubscriptionStatus.ACTIVE, failures: int = 0
) -> WebhookSubscription:
    return WebhookSubscription(
        id=uuid4(),
        project_id=uuid4(),
        url="https://hook.example.com/notify",
        secret_hash="a" * 64,
        event_types=("annotation.completed",),
        status=status,
        consecutive_failures=failures,
        secret_plain="example-hmac-plain-0123456789",
    )


def test_successful_delivery_records_success_and_resets_count() -> None:
    from app.infrastructure.repositories.webhook_subscription import (
        DeliveryTarget,
    )

    fake = FakeTransport([200])
    subscription = _subscription(failures=3)

    class _Repo:
        """记录 replace_state 调用的假仓储。"""

        def __init__(self) -> None:
            self.saved: list[WebhookSubscription] = []

        def active_for_event(self, project_id, event_type):
            return (
                DeliveryTarget(subscription=subscription, secret="example-hmac-plain-0123456789"),
            )

        def replace_state(self, subscription_id, updated):
            self.saved.append(updated)

    repo = _Repo()
    use_cases = WebhookDeliveryUseCases(repo, transport=fake)

    outcome = use_cases.deliver_event(
        project_id=subscription.project_id,
        event_type="annotation.completed",
        payload={"task_id": "abc"},
        now=NOW,
    )

    assert outcome.delivered == 1
    assert outcome.failed == 0
    # 成功应清零失败计数
    assert repo.saved[0].consecutive_failures == 0
    assert repo.saved[0].last_success_at == NOW
    # 签名头必须存在且含时间戳
    assert "X-DDP-Signature" in fake.calls[0]["headers"]
    assert fake.calls[0]["headers"]["X-DDP-Timestamp"]


def test_repeated_failures_pause_the_subscription() -> None:
    from app.infrastructure.repositories.webhook_subscription import DeliveryTarget

    fake = FakeTransport([500])
    subscription = _subscription()

    class _Repo:
        saved: list[WebhookSubscription] = []

        def active_for_event(self, project_id, event_type):
            # 每次投递用当前状态（模拟真实循环）
            current = self.saved[-1] if self.saved else subscription
            return (DeliveryTarget(subscription=current, secret="example-hmac-plain-0123456789"),)

        def replace_state(self, subscription_id, updated):
            self.saved.append(updated)

    repo = _Repo()
    use_cases = WebhookDeliveryUseCases(repo, transport=fake)

    for _ in range(FAILURE_PAUSE_THRESHOLD):
        use_cases.deliver_event(
            project_id=subscription.project_id,
            event_type="annotation.completed",
            payload={"x": 1},
            now=NOW,
        )

    final = repo.saved[-1]
    assert final.status == WebhookSubscriptionStatus.PAUSED
    assert final.consecutive_failures == FAILURE_PAUSE_THRESHOLD
    assert final.last_error_message


def test_paused_subscription_is_not_delivered() -> None:
    fake = FakeTransport([200])
    paused = _subscription(status=WebhookSubscriptionStatus.PAUSED)

    class _Repo:
        saved: list[WebhookSubscription] = []

        def active_for_event(self, project_id, event_type):
            # 仓储只返回 ACTIVE 的订阅；PAUSED 的不会进入投递
            return ()

        def replace_state(self, subscription_id, updated):
            self.saved.append(updated)

    use_cases = WebhookDeliveryUseCases(_Repo(), transport=fake)
    outcome = use_cases.deliver_event(
        project_id=paused.project_id,
        event_type="annotation.completed",
        payload={"x": 1},
        now=NOW,
    )

    assert outcome.delivered == 0
    assert fake.calls == []


def test_default_transport_rejects_unsafe_url_before_dial() -> None:
    """SSRF 防护：默认传输在**拨号前**必须过 URL 守卫。

    订阅地址是用户提供的外部输入——不校验就等于让平台成为任意地址的
    探测器（可探测内网端口、可打到 metadata 服务）。这里用私有网地址
    验证守卫在建立连接前就拒绝。
    """
    from app.application.webhook.delivery import WebhookDeliveryUseCases
    from app.infrastructure.adapter_url_guard import AdapterHttpFailure

    class _Repo:
        def active_for_event(self, project_id, event_type):
            return ()

        def replace_state(self, subscription_id, updated):
            raise AssertionError("不应进入投递")

    use_cases = WebhookDeliveryUseCases(_Repo())
    with pytest.raises(AdapterHttpFailure):
        use_cases._httpx_post("http://169.254.169.254/latest/meta-data/", {}, b"{}")


def test_default_transport_disables_redirects() -> None:
    """重定向必须关闭：跟随会把"校验过的首跳"变成"任意目标"，绕过守卫。"""
    import inspect

    from app.application.webhook.delivery import WebhookDeliveryUseCases

    source = inspect.getsource(WebhookDeliveryUseCases._httpx_post)
    assert "follow_redirects=False" in source


def test_unmatched_event_type_delivers_nothing() -> None:
    fake = FakeTransport([200])
    subscription = _subscription()

    class _Repo:
        def active_for_event(self, project_id, event_type):
            # 事件类型不匹配 → 无目标
            return ()

        def replace_state(self, subscription_id, updated):
            raise AssertionError("不应调用 replace_state")

    use_cases = WebhookDeliveryUseCases(_Repo(), transport=fake)
    outcome = use_cases.deliver_event(
        project_id=subscription.project_id,
        event_type="other.event",
        payload={"x": 1},
        now=NOW,
    )

    assert outcome.delivered == 0
    assert fake.calls == []
