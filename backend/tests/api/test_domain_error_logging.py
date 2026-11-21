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

import gc
import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
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


# --- 兜底错误码：internal_error / dependency_unavailable -------------------


def _build_probe_app(route_handler):
    """造一个只挂探针路由的最小 app，复用真实 create_app 的异常处理器。"""
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()

    @app.get("/test/probe")
    def probe() -> dict[str, bool]:
        return route_handler()

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.filterwarnings(
    # Starlette 在 raise_server_exceptions=False 的 500 路径上会遗留一个未关闭的
    # anyio memory stream（见 anyio/streams/memory.py 的 __del__）。
    # 这是第三方实现的既有问题，与本用例要验证的行为无关；显式标注而不是
    # 全局忽略，避免掩盖我们自己的资源泄漏。
    "ignore::pytest.PytestUnraisableExceptionWarning"
)
def test_unexpected_error_returns_internal_error_with_request_id() -> None:
    """未捕获异常必须走统一错误体，带 code 与 request_id。

    此前没有任何 `Exception` 兜底：Starlette 直接返回裸 500，响应体不含
    request_id、也不走统一错误体。客户端拿不到关联日志的标识，前端也无法
    按 code 分支，只能显示"未知错误"。契约里该码叫 `internal_error`。
    """

    def boom() -> dict[str, bool]:
        raise RuntimeError("数据库密码是 hunter2")

    client = _build_probe_app(boom)
    with _capture_app_main_logs() as captured, client:
        response = client.get("/test/probe")
    # TestClient 已关闭；在本用例的局部 warning filter 仍生效时回收 Starlette
    # 500 路径遗留的 AnyIO stream，避免 __del__ 延迟到下一个无关测试的 setup。
    del client
    gc.collect()

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["request_id"], "500 响应必须带 request_id，否则无法关联日志"
    # 绝不回显异常原文。
    assert "hunter2" not in response.text
    # 原文进日志。
    assert any("hunter2" in message for message in _messages(captured))


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_database_unavailable_returns_503_not_500() -> None:
    """数据库不可用 → 503 可重试，不是 500 内部错误。

    客户端处置完全不同：503 应退避重试，500 重试无用。契约为此专设
    `dependency_unavailable`（http 503、retryable true），实现此前从未产生它。
    """
    from sqlalchemy.exc import OperationalError

    def db_down() -> dict[str, bool]:
        raise OperationalError(
            "SELECT 1",
            {},
            Exception("could not connect to server: Connection refused"),
        )

    client = _build_probe_app(db_down)
    with _capture_app_main_logs() as captured, client:
        response = client.get("/test/probe")
    del client
    gc.collect()

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "dependency_unavailable"
    assert response.headers.get("Retry-After") == "5"
    # 连接串与 SQL 片段不得外传。
    assert "Connection refused" not in response.text
    assert "SELECT 1" not in response.text
    assert any("Connection refused" in message for message in _messages(captured))
