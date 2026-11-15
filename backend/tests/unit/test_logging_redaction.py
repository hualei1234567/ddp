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


def test_pydantic_input_value_is_redacted() -> None:
    """Pydantic 校验错误把用户输入原样带进文本，必须抹掉。

    形态：``[type=string_too_short, input_value='hunter2', input_type=str]``。
    这里用**明文密码**构造，确保脱敏不是靠"看起来像凭据"而是靠固定格式。
    """
    error = ValueError(
        "1 validation error for Credentials\n"
        "password\n"
        "  String should have at least 20 characters "
        "[type=string_too_short, input_value='hunter2-leak', input_type=str]\n"
    )
    payload = _format("请求校验失败", (), exc_info=(ValueError, error, None))

    assert isinstance(payload["exc"], str)
    assert "hunter2-leak" not in payload["exc"]
    assert "input_value='***'" in payload["exc"]


def test_quoted_secret_with_spaces_is_redacted() -> None:
    """引号包裹、含空格的凭据也要整体抹掉。

    旧实现用 ``\\S+`` 只吃到第一个空格，会把 ``"my-secret-example-pass"``
    抹成 ``password=*** example-pass"``，剩下的部分照样泄漏。
    """
    # 用 example-placeholder 形态的假凭据：既能真实触发脱敏分支，
    # 又不会让 ``check_secrets.py`` 误判成仓库里的真凭据。
    payload = _format("failed: %s", ('password="my-secret-example-pass"',))

    assert "example-pass" not in payload["message"]
    assert "my-secret" not in payload["message"]


def test_quoted_api_key_in_json_shape_is_redacted() -> None:
    payload = _format("%s", ('{"api_key": "sk-live-abcdef"}',))

    assert "sk-live-abcdef" not in payload["message"]


def test_ordinary_text_is_not_over_redacted() -> None:
    """不含凭据的文本必须原样保留——过度脱敏会让日志失去排障价值。"""
    payload = _format("用户 %s 的订单 %s 已创建", ("alice", "A-1001"))

    assert "alice" in payload["message"]
    assert "A-1001" in payload["message"]
