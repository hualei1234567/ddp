"""Webhook 订阅领域测试（阶段 5）。

核心不变量：URL 校验、签名可验证、失败熔断状态机、成功自动恢复。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.annotation.errors import DomainValidationError
from app.domain.webhook.subscription import (
    FAILURE_PAUSE_THRESHOLD,
    WebhookSubscription,
    build_signature_headers,
    decrypt_secret,
    encrypt_secret,
    next_state_after,
    normalize_url,
    should_deliver,
    sign_payload,
    verify_secret_hash,
)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def _subscription(**overrides: object) -> WebhookSubscription:
    values: dict[str, object] = {
        "id": uuid4(),
        "project_id": uuid4(),
        "url": "https://hook.example.com/notify",
        "secret_hash": "a" * 64,
        "event_types": ("annotation.completed",),
    }
    values.update(overrides)
    return WebhookSubscription(**values)  # type: ignore[arg-type]


# ---- URL 校验 ----


@pytest.mark.parametrize(
    "url",
    [
        "https://hook.example.com/x",
        "https://hook.example.com",
        "http://127.0.0.1:8080/hook",
        "http://localhost:9000/hook",
        "http://[::1]:8000/hook",
    ],
)
def test_normalize_url_accepts_https_and_local_http(url: str) -> None:
    assert normalize_url(url) == url


@pytest.mark.parametrize(
    "url",
    ["http://evil.example.com/x", "ftp://x.com", "not-a-url", "https://", "", "  "],
)
def test_normalize_url_rejects_unsafe(url: str) -> None:
    with pytest.raises(DomainValidationError):
        normalize_url(url)


def test_normalize_url_rejects_overlong() -> None:
    with pytest.raises(DomainValidationError, match="超长"):
        normalize_url("https://x.com/" + "a" * 2048)


# ---- 加密与签名 ----


def test_encrypt_decrypt_roundtrip() -> None:
    key = b"a-test-key-that-is-long-enough-32bytes!!"
    plain = "super-secret-plain"
    cipher = encrypt_secret(plain, key)
    assert cipher != plain
    assert decrypt_secret(cipher, key) == plain


def test_encrypt_with_different_key_cannot_decrypt() -> None:
    """错误密钥解不出原明文（流密码 XOR，密钥错则结果全错）。"""
    cipher = encrypt_secret("secret", b"key-one")
    with pytest.raises((UnicodeDecodeError, ValueError)):
        decrypt_secret(cipher, b"key-two-different")


def test_sign_payload_is_deterministic_and_verifiable() -> None:
    signature = sign_payload("secret", b"{}", "1700000000")
    assert signature.startswith("sha256=")
    assert signature == sign_payload("secret", b"{}", "1700000000")
    assert signature != sign_payload("secret", b"{}", "1700000010")


def test_verify_secret_hash_matches_and_mismatches() -> None:
    """验签：明文 secret 与库内 hash 匹配时为真，不匹配为假。"""
    from app.domain.webhook.subscription import hash_secret

    digest = hash_secret("secret")
    assert verify_secret_hash("secret", digest) is True
    assert verify_secret_hash("wrong", digest) is False


# ---- 订阅不变量 ----


def test_subscription_requires_non_empty_event_types() -> None:
    with pytest.raises(DomainValidationError):
        _subscription(event_types=())


def test_subscription_requires_valid_secret_hash() -> None:
    with pytest.raises(DomainValidationError):
        _subscription(secret_hash="short")


def test_subscription_rejects_invalid_status() -> None:
    with pytest.raises(DomainValidationError):
        _subscription(status="DELETED")


# ---- 投递判定与状态机 ----


def test_should_deliver_respects_paused_and_event_types() -> None:
    active = _subscription()
    paused = _subscription(status="PAUSED")

    assert should_deliver(active, "annotation.completed") is True
    assert should_deliver(active, "other.event") is False
    assert should_deliver(paused, "annotation.completed") is False


def test_success_resets_failure_count_and_resumes_paused() -> None:
    paused = _subscription(status="PAUSED", consecutive_failures=FAILURE_PAUSE_THRESHOLD)
    transition = next_state_after(paused, success=True, now=NOW)

    assert transition.action == "reset"
    assert transition.subscription.status == "ACTIVE"
    assert transition.subscription.consecutive_failures == 0
    assert transition.subscription.last_success_at == NOW


def test_success_with_clean_state_is_no_op() -> None:
    fresh = _subscription()
    transition = next_state_after(fresh, success=True, now=NOW)
    assert transition.action == "none"


def test_failures_increment_then_pause_at_threshold() -> None:
    subscription = _subscription()
    for expected in range(1, FAILURE_PAUSE_THRESHOLD + 1):
        transition = next_state_after(subscription, success=False, now=NOW)
        subscription = transition.subscription
        assert transition.subscription.consecutive_failures == expected
        assert transition.action == "paused" if expected == FAILURE_PAUSE_THRESHOLD else "none"
        assert transition.subscription.status == (
            "PAUSED" if expected == FAILURE_PAUSE_THRESHOLD else "ACTIVE"
        )


def test_signature_headers_include_timestamp() -> None:
    headers = build_signature_headers("secret", b"{}", "1700000000")
    assert "X-DDP-Signature" in headers
    assert headers["X-DDP-Timestamp"] == "1700000000"
    assert headers["Content-Type"] == "application/json"
