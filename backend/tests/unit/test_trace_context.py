"""追踪上下文：从事件恢复 / 无来源生成 / 复位与日志标注。"""

from __future__ import annotations

import json
import logging

import pytest
from app.core.logging import (
    TRACE_SOURCE_EVENT,
    TRACE_SOURCE_GENERATED,
    JsonFormatter,
    bind_trace,
    current_trace_id,
    is_safe_trace_id,
    trace_from_event_payload,
    trace_id_var,
)


def _format() -> dict[str, object]:
    record = logging.LogRecord(
        name="ddp.test.trace",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="处理事件",
        args=(),
        exc_info=None,
    )
    return json.loads(JsonFormatter().format(record))


def test_restores_request_id_from_event_payload() -> None:
    context = trace_from_event_payload({"request_id": "req-from-http"})

    assert context.trace_id == "req-from-http"
    assert context.source == TRACE_SOURCE_EVENT
    assert context.is_generated is False


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"request_id": None},
        {"request_id": 123},
        {"request_id": ""},
        {"request_id": "bad\nvalue"},
        {"request_id": "x" * 129},
    ],
)
def test_generates_and_marks_when_source_missing_or_unsafe(
    payload: dict[str, object] | None,
) -> None:
    context = trace_from_event_payload(payload)

    assert context.source == TRACE_SOURCE_GENERATED
    assert context.is_generated is True
    # 生成本地 ID，不伪造来源；32 位 hex 且满足同一安全字符集。
    assert len(context.trace_id) == 32
    assert is_safe_trace_id(context.trace_id)


def test_generated_ids_are_unique_not_event_ids() -> None:
    first = trace_from_event_payload({"event_id": "same-event"})
    second = trace_from_event_payload({"event_id": "same-event"})

    assert first.trace_id != second.trace_id
    # 不把 payload 里的其他 ID 当成 request_id 使用。
    assert first.trace_id != "same-event"


def test_bind_trace_sets_and_resets_context_token() -> None:
    assert current_trace_id() is None
    context = trace_from_event_payload({"request_id": "req-inner"})

    with bind_trace(context):
        assert current_trace_id() == "req-inner"
        assert _format()["trace_id"] == "req-inner"
        assert _format()["trace_source"] == TRACE_SOURCE_EVENT

    # 退出上下文必须复位，不能串到下一条事件。
    assert current_trace_id() is None
    assert _format()["trace_id"] is None
    assert "trace_source" not in _format()


def test_nested_bind_restores_outer_context() -> None:
    outer = trace_from_event_payload({"request_id": "outer"})
    inner = trace_from_event_payload({"request_id": "inner"})

    with bind_trace(outer):
        with bind_trace(inner):
            assert current_trace_id() == "inner"
        assert current_trace_id() == "outer"
    assert current_trace_id() is None


def test_bind_trace_resets_on_exception() -> None:
    context = trace_from_event_payload({"request_id": "req-boom"})

    with pytest.raises(RuntimeError):
        with bind_trace(context):
            assert current_trace_id() == "req-boom"
            raise RuntimeError("处理失败")

    assert current_trace_id() is None


def test_generated_context_is_marked_in_logs() -> None:
    context = trace_from_event_payload({})

    with bind_trace(context):
        payload = _format()

    assert payload["trace_id"] == context.trace_id
    assert payload["trace_source"] == TRACE_SOURCE_GENERATED


def test_trace_context_does_not_leak_into_thread_local_state() -> None:
    """``contextvars`` 按上下文隔离；复位后不得残留。"""
    assert trace_id_var.get() is None
    context = trace_from_event_payload({"request_id": "req-clean"})
    with bind_trace(context):
        assert trace_id_var.get() == "req-clean"
    assert trace_id_var.get() is None
