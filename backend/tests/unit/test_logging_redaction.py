"""日志格式化测试：敏感参数脱敏、长参数截断、异常堆栈脱敏。"""

from __future__ import annotations

import json
import logging

from app.core.logging import JsonFormatter


def _format(
    message: object,
    args: object = (),
    *,
    exc_info: tuple[type[BaseException], BaseException, object | None] | None = None,
) -> dict[str, object]:
    record = logging.LogRecord(
        name="ddp.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=exc_info,
    )
    return json.loads(JsonFormatter().format(record))


def test_sensitive_named_argument_is_redacted() -> None:
    payload = _format("token=%s", ("super-secret-token",))

    assert "super-secret-token" not in payload["message"]
    assert "token=***" in payload["message"]


def test_sensitive_mapping_argument_is_redacted() -> None:
    payload = _format(
        "payload=%s",
        ({"password": "hunter2", "value": "visible"},),
    )

    assert "hunter2" not in payload["message"]
    assert "visible" in payload["message"]


def test_bearer_token_in_message_is_redacted() -> None:
    payload = _format("Authorization: Bearer abc.def.ghi", ())

    assert "abc.def.ghi" not in payload["message"]
    assert "***" in payload["message"]


def test_long_argument_is_truncated() -> None:
    long_value = "a" * 500
    payload = _format("value=%s", (long_value,))

    assert long_value not in payload["message"]
    assert "…<truncated>" in payload["message"]


def test_exception_text_is_redacted_and_truncated() -> None:
    error = ValueError("token=leak-value " + "x" * 5000)
    payload = _format("failed", (), exc_info=(ValueError, error, None))

    assert isinstance(payload["exc"], str)
    assert "leak-value" not in payload["exc"]
    assert len(payload["exc"]) < 5000


def test_malformed_format_does_not_raise() -> None:
    payload = _format("%s %s", ("only-one",))

    assert "only-one" in payload["message"]
