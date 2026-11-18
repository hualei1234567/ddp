"""导入日志脱敏：错误详情可外发，但不得携带地址、路径或凭据。"""

from __future__ import annotations

from app.application.datasources.observability import sanitize_log_message
from app.domain.datasources.models import IMPORT_LOG_MESSAGE_MAX_LENGTH


def test_url_and_presigned_query_are_redacted() -> None:
    message = sanitize_log_message(
        "下载失败：https://huggingface.co/datasets/a/b?X-Amz-Signature=deadbeef&x=1"
    )

    assert "https://" not in message
    assert "X-Amz-Signature" not in message
    assert "deadbeef" not in message
    assert "[已脱敏地址]" in message


def test_absolute_paths_are_redacted() -> None:
    posix = sanitize_log_message("读取 /Users/alice/datasets/x.mp4 失败")
    windows = sanitize_log_message(r"无法写入 C:\Users\alice\data\x.mp4")

    assert "/Users/alice" not in posix
    assert "[已脱敏路径]" in posix
    assert "C:\\Users" not in windows
    assert "[已脱敏路径]" in windows


def test_ordinary_fractions_are_not_mistaken_for_paths() -> None:
    """``3/10``、``and/or`` 不是绝对路径，不能被误伤。"""
    message = sanitize_log_message("入库进度：3/10 文件，状态 ok/failed")

    assert message == "入库进度：3/10 文件，状态 ok/failed"


def test_credentials_are_redacted() -> None:
    for raw in ("Authorization: Bearer abc.def", "token=secret-value", "api_key=xyz"):
        message = sanitize_log_message(raw)
        assert "abc.def" not in message
        assert "secret-value" not in message
        assert "xyz" not in message
        assert "[已脱敏凭据]" in message


def test_plain_business_message_is_preserved_and_whitespace_collapsed() -> None:
    assert sanitize_log_message(" 元数据校验通过：10 行 ") == "元数据校验通过：10 行"
    assert sanitize_log_message("第一行\n第二行") == "第一行 第二行"


def test_message_is_truncated_to_column_width() -> None:
    message = sanitize_log_message("x" * (IMPORT_LOG_MESSAGE_MAX_LENGTH + 50))

    assert len(message) == IMPORT_LOG_MESSAGE_MAX_LENGTH


def test_empty_message_never_returns_blank() -> None:
    assert sanitize_log_message("   ") == "(无更多信息)"
    assert sanitize_log_message(None) == "(无更多信息)"
