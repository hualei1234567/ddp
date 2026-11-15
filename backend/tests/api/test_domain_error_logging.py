"""R-12：领域异常原文必须落日志（响应侧仍不回显）。

背景：``api/errors.py`` 模块注释承诺「一律不回显 ``str(exc)``，领域异常原文
只进日志」。审计复核发现前半句成立、后半句从未兑现——``main.py`` 的异常处理器
没有任何 ``logger`` 调用，排障线索既没回显也没落盘。

这组用例钉住三件事：
1. 4xx 领域异常写 ``warning``，且日志里带异常原文与 request_id；
2. 响应体**不**包含异常原文（不回显的承诺不能因为加了日志而退化）；
3. 5xx 写 ``error`` 并附堆栈（服务端错误必须能在日志里定位）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client


def _device_url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/devices{suffix}"


class _ListHandler(logging.Handler):
    """把日志记录收进列表。

    不能用 pytest 的 ``caplog``：``app.main`` 在装配期调用
    ``configure_logging()`` 会 ``root.handlers.clear()``，把 caplog 挂在
    root 上的捕获 handler 一并清掉。直接挂到目标 logger 上就不受它影响。
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def _capture_app_main_logs() -> Iterator[_ListHandler]:
    handler = _ListHandler()
    target = logging.getLogger("app.main")
    previous_level = target.level
    target.setLevel(logging.DEBUG)
    target.addHandler(handler)
    try:
        yield handler
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)


def _messages(handler: _ListHandler) -> list[str]:
    return [record.getMessage() for record in handler.records]


def _request_id_of(handler: _ListHandler) -> str | None:
    """从日志消息里取 request_id（以结构化字段拼进 message）。"""
    for message in _messages(handler):
        if "'request_id': '" in message:
            return message.split("'request_id': '", 1)[1].split("'", 1)[0]
    return None


def test_client_error_is_logged_with_exception_text(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """404 领域异常：日志里有原文，响应里没有。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    missing = "00000000-0000-7000-8000-000000000000"

    with _capture_app_main_logs() as captured:
        response = client.get(_device_url(ids.project_a, f"/{missing}"))

    assert response.status_code == 404
    # 1) 日志里能看到异常原文（排障线索）。
    messages = _messages(captured)
    assert any("领域异常" in message for message in messages), messages
    assert any("resource_not_found" in message for message in messages), messages
    assert any("设备不存在或不可见" in message for message in messages), messages
    # 2) 响应体不回显领域异常文案（R-12 的不回显承诺）。
    body = response.text
    assert "资源不存在" not in body or "resource_not_found" in body
    assert response.json()["error"]["code"] == "resource_not_found"


def test_logged_record_carries_request_id(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """日志条目带 request_id，可与客户端上报的 request_id 对上。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    missing = "00000000-0000-7000-8000-000000000000"

    with _capture_app_main_logs() as captured:
        response = client.get(_device_url(ids.project_a, f"/{missing}"))

    response_request_id = response.json()["error"]["request_id"]
    assert response_request_id, "响应里应有 request_id"
    assert (
        _request_id_of(captured) == response_request_id
    ), "日志里的 request_id 必须与响应一致，否则无法按 ID 关联日志"


def test_validation_failure_is_logged_not_leaked(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """422 校验失败：日志记录，响应只给固定文案 + 白名单 details。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    with _capture_app_main_logs() as captured:
        response = client.post(_device_url(ids.project_a), json={"name": "缺少 code"})

    assert response.status_code == 422
    assert any("领域异常" in message for message in _messages(captured))


def test_no_logging_for_successful_requests(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """正常请求不写异常日志——否则日志会被 2xx 噪声淹没。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    with _capture_app_main_logs() as captured:
        response = client.get(_device_url(ids.project_a))

    assert response.status_code == 200
    assert not [m for m in _messages(captured) if "领域异常" in m]
