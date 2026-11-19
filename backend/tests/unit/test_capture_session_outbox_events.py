"""outbox 归类检查：采集会话事件必须是留痕类，不得误入投递白名单。

依据 `docs/architecture/outbox事件语义界定-v1.0.md`：没有消费者的事件必须
显式登记为留痕类。本测试同时确认事件名使用模块级字面量（静态检查脚本可提取）。
"""

from __future__ import annotations

from pathlib import Path

from app.application.collection.capture_sessions import (
    SESSION_ACTION_EVENTS,
    SESSION_CREATED_EVENT,
)

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "check_outbox_events.py"
_USE_CASE = _ROOT / "backend" / "app" / "application" / "collection" / "capture_sessions.py"


def test_session_events_are_audit_class_only() -> None:
    events = {SESSION_CREATED_EVENT, *SESSION_ACTION_EVENTS.values()}
    assert len(events) == 6
    assert all(event.startswith("collection.capture_session.") for event in events)

    script = _SCRIPT.read_text(encoding="utf-8")
    for event in events:
        assert event not in script, (
            f"{event} 被登记为投递类事件；采集会话当前没有消费者，"
            "应保持留痕类，接入消费者需单独评审"
        )


def test_session_events_are_extractable_module_level_literals() -> None:
    source = _USE_CASE.read_text(encoding="utf-8")
    assert 'SESSION_CREATED_EVENT = "collection.capture_session.created"' in source
    for event in SESSION_ACTION_EVENTS.values():
        assert f'"{event}"' in source
    # 禁止 f-string 事件名：静态检查抓不到，会重新制造 R-13 的漏检。
    assert 'event_type=f"' not in source
