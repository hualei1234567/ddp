"""依赖健康检查 API（V52-036）。

口径：``GET /api/v1/health/dependencies`` **永远返回 HTTP 200**，依赖状态放在
响应体里；任一依赖异常不得变成 500（否则 LB 会摘掉副本，把降级放大成不可用）。

正常态用真实 SQLite 执行 ``SELECT 1``；降级态用替身会话/替身工厂让探活抛异常，
断言状态码仍是 200 且 ``status`` 如实降级。契约缺口（该端点 ``include_in_schema=False``）
见 ``app/api/v1/health_dependencies_routes.py`` 的模块注释。
"""

from __future__ import annotations

import pytest
from app.api.v1 import health_dependencies_routes
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from tests.support_clients import managed_client

_ENDPOINT = "/api/v1/health/dependencies"


class _FailingSession:
    """探活会话替身：``SELECT 1`` 直接抛数据库错误，模拟数据库不可达。"""

    def execute(self, *_args: object, **_kwargs: object) -> object:
        raise OperationalError("SELECT 1", {}, RuntimeError("database unavailable"))

    def close(self) -> None:
        pass


@pytest.fixture()
def healthy_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """真实 SQLite：探活执行真正的 ``SELECT 1``。"""
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    factory = create_session_factory(engine)
    monkeypatch.setattr(health_dependencies_routes, "get_session_factory", lambda: factory)
    try:
        yield managed_client(create_app())
    finally:
        engine.dispose()


def _assert_shape(body: dict[str, object]) -> dict[str, object]:
    """断言响应结构固定（契约形状，调用方据此解析）。"""
    assert set(body) == {"status", "checks"}
    checks = body["checks"]
    assert isinstance(checks, dict)
    assert set(checks) == {"database"}
    database = checks["database"]
    assert isinstance(database, dict)
    assert set(database) == {"status", "latency_ms", "detail"}
    assert isinstance(database["latency_ms"], int)
    assert database["latency_ms"] >= 0
    assert isinstance(database["detail"], str)
    assert database["detail"]
    return database


def test_dependencies_healthy_returns_200_ok(healthy_client: TestClient) -> None:
    response = healthy_client.get(_ENDPOINT)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    database = _assert_shape(body)
    assert database["status"] == "ok"


def test_dependencies_degraded_when_session_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话执行抛异常：仍返回 200，状态降级，且不回显异常原文。"""

    def factory() -> _FailingSession:
        return _FailingSession()

    monkeypatch.setattr(health_dependencies_routes, "get_session_factory", lambda: factory)
    client = managed_client(create_app())

    response = client.get(_ENDPOINT)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    database = _assert_shape(body)
    assert database["status"] == "degraded"
    # 探活异常原文可能含连接串/主机名，绝不进响应体。
    assert "database unavailable" not in str(body)


def test_dependencies_degraded_when_session_factory_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """引擎/连接池装配失败也降级：探活不能因依赖注入阶段异常变 500。"""

    def _broken_factory() -> object:
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(health_dependencies_routes, "get_session_factory", _broken_factory)
    client = managed_client(create_app())

    response = client.get(_ENDPOINT)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert _assert_shape(body)["status"] == "degraded"
