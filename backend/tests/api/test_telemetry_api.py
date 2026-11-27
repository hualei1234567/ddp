"""OpenTelemetry 追踪的 HTTP 层测试（中间件 + 响应头 + 跨请求传播）。

覆盖五件事：

1. **关闭态与接入前完全一致**：响应无 ``X-Trace-Id`` 头、不产生 span、
   日志字段集合不变（不出现 ``otel_trace_id``）；
2. **开启态**：拿到 span，span 名是**路由模板**（不是原始 URL），属性齐全
   （``http.route`` / ``http.method`` / ``http.status_code``），响应头有
   32 位 hex 的 ``X-Trace-Id``；
3. **跨请求传播**：请求带 ``traceparent`` 时，本服务 span 的 parent 正确；
4. **高基数防护**：span 名里不出现原始 URL；
5. **出站传播**：``app/infrastructure/adapter_http.py`` 的出站请求带
   ``traceparent``（关闭态则不带）。

装配层的单元测试在 ``tests/unit/test_telemetry.py``。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pytest
from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.core.telemetry import _reset_state, configure_telemetry
from app.infrastructure.adapter_http import request_json
from app.main import create_app
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests.api.conftest import ApiIds
from tests.support_clients import managed_client

_DEVICES_TEMPLATE = "/api/v1/projects/{project_id}/devices"
_TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class TracedApp:
    """一套开启态追踪的真实应用 + 收集 span 的内存 exporter。"""

    client: TestClient
    exporter: InMemorySpanExporter

    def server_spans(self) -> list[object]:
        """只取服务端 span（过滤掉测试自身可能产生的其他 span）。"""
        return [span for span in self.exporter.get_finished_spans() if span.kind.name == "SERVER"]


@pytest.fixture()
def traced_app(
    api_db: tuple[object, ApiIds],
    monkeypatch: pytest.MonkeyPatch,
) -> TracedApp:
    """真实应用 + 真实 SQLite + 开启态追踪（span 收集到内存 exporter）。"""
    from app import main as main_module
    from app.api.deps import get_current_principal
    from app.domain.identity.models import PlatformRole, Principal
    from app.infrastructure.db.session import get_session

    factory, ids = api_db
    monkeypatch.setattr(main_module, "get_session_factory", lambda: factory)
    exporter = InMemorySpanExporter()
    configure_telemetry(
        Settings(
            otel_enabled=True,
            otel_service_name="ddp-test",
            otel_exporter_otlp_endpoint="http://collector.invalid:4318",
            otel_exporter_otlp_protocol="http",
        ),
        span_exporter=exporter,
        install_global=False,
        batch=False,
    )
    app = create_app()

    def override_session():  # type: ignore[no-untyped-def]
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=ids.editor_a,
        platform_role=PlatformRole.MEMBER,
    )
    _add_probe_route(app)
    return TracedApp(client=managed_client(app), exporter=exporter)


#: 探针路由：请求处理**期间**打一条日志，用于验证日志里的 otel_trace_id。
_PROBE_PATH = "/api/v1/telemetry-probe"
_PROBE_LOGGER = logging.getLogger("ddp.telemetry.probe")


def _add_probe_route(app: object) -> None:
    """给应用加一条只打日志的探针路由（仅测试用）。"""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    fastapi_app = app if isinstance(app, FastAPI) else None
    assert fastapi_app is not None

    @fastapi_app.get(_PROBE_PATH)
    def _probe() -> JSONResponse:
        _PROBE_LOGGER.info("探针日志")
        return JSONResponse({"ok": True})


@pytest.fixture(autouse=True)
def _clean_telemetry_state() -> None:
    _reset_state()
    yield
    _reset_state()


# ---------------------------------------------------------------------------
# 1. 关闭态：行为与接入前完全一致
# ---------------------------------------------------------------------------


def test_disabled_response_has_no_trace_id_header(
    metrics_client: TestClient,
) -> None:
    """关闭态：响应头不得出现 X-Trace-Id（与接入前逐字节一致）。"""
    response = metrics_client.get("/health")

    assert response.status_code == 200
    assert "X-Trace-Id" not in response.headers
    # 既有的请求 ID 头不受影响。
    assert response.headers["X-Request-Id"]


def test_disabled_produces_no_span(metrics_client: TestClient) -> None:
    """关闭态：中间件不产生任何 span。"""
    exporter = InMemorySpanExporter()
    configure_telemetry(
        Settings(otel_enabled=False),
        span_exporter=exporter,
        install_global=False,
        batch=False,
    )

    metrics_client.get("/health")

    assert exporter.get_finished_spans() == ()


def test_disabled_logs_keep_original_field_set(metrics_client: TestClient) -> None:
    """关闭态：结构化日志字段集合与接入前一致（不出现 otel_trace_id）。"""
    records: list[dict[str, object]] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            import json

            records.append(json.loads(JsonFormatter().format(record)))

    handler = _Collector()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("ddp.telemetry.test")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("关闭态日志")
    finally:
        logger.removeHandler(handler)

    assert records
    assert all("otel_trace_id" not in record for record in records)


# ---------------------------------------------------------------------------
# 2. 开启态：span、属性、响应头
# ---------------------------------------------------------------------------


def test_enabled_response_carries_trace_id_header(traced_app: TracedApp) -> None:
    response = traced_app.client.get("/health")

    assert response.status_code == 200
    trace_id = response.headers["X-Trace-Id"]
    assert _TRACE_ID_PATTERN.match(trace_id), f"trace id 不是 32 位小写 hex：{trace_id}"


def test_enabled_produces_server_span(traced_app: TracedApp) -> None:
    traced_app.client.get("/health")

    spans = traced_app.server_spans()
    assert len(spans) == 1
    assert spans[0].parent is None or spans[0].parent.span_id == 0


def test_span_name_uses_route_template_not_raw_url(traced_app: TracedApp) -> None:
    """span 名必须是路由模板：原始 URL 含 UUID，进 span 名会撑爆链路后端。"""
    traced_app.exporter.clear()

    # 不匹配任何路由的路径（404）：路径完全由客户端控制。
    traced_app.client.get("/api/v1/projects/00000000-0000-7000-8000-000000000000/x")

    spans = traced_app.server_spans()
    assert spans, "未产生服务端 span"
    span = spans[0]
    assert span.attributes["http.route"] == "__unmatched__"
    # 未匹配路由用有界兜底名，绝不含原始路径。
    assert span.name == "HTTP GET"
    assert "00000000-0000-7000-8000-000000000000" not in span.name


def test_span_name_is_route_template(traced_app: TracedApp, api_db: tuple[object, ApiIds]) -> None:
    """命中路由时 span 名为「方法 + 路由模板」。"""
    _factory, ids = api_db
    traced_app.exporter.clear()

    traced_app.client.get(f"/api/v1/projects/{ids.project_a}/devices")

    spans = traced_app.server_spans()
    assert spans
    assert spans[0].name == f"GET {_DEVICES_TEMPLATE}"
    assert spans[0].attributes["http.route"] == _DEVICES_TEMPLATE


def test_span_attributes_are_complete(traced_app: TracedApp, api_db: tuple[object, ApiIds]) -> None:
    """span 属性含 http.route / http.method / http.status_code。"""
    _factory, ids = api_db
    traced_app.exporter.clear()

    traced_app.client.get(f"/api/v1/projects/{ids.project_a}/devices")

    span = traced_app.server_spans()[0]
    assert span.attributes["http.route"] == _DEVICES_TEMPLATE
    assert span.attributes["http.request.method"] == "GET"
    assert span.attributes["http.response.status_code"] == 200


def test_trace_id_header_matches_span_trace_id(traced_app: TracedApp) -> None:
    """响应头的 X-Trace-Id 与 span 的 trace id 同值（可互相定位）。"""
    response = traced_app.client.get("/health")

    span = traced_app.server_spans()[0]
    assert response.headers["X-Trace-Id"] == format(span.context.trace_id, "032x")


# ---------------------------------------------------------------------------
# 3. 跨请求传播
# ---------------------------------------------------------------------------


def test_incoming_traceparent_becomes_parent(traced_app: TracedApp) -> None:
    """上游的 traceparent 传入时，本服务 span 成为其子节点。"""
    upstream_trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    upstream_span_id = "00f067aa0ba902b7"

    traced_app.client.get(
        "/health",
        headers={
            "traceparent": f"00-{upstream_trace_id}-{upstream_span_id}-01",
        },
    )

    span = traced_app.server_spans()[0]
    assert format(span.context.trace_id, "032x") == upstream_trace_id
    assert format(span.parent.span_id, "016x") == upstream_span_id


def test_invalid_traceparent_is_ignored(traced_app: TracedApp) -> None:
    """非法 traceparent 不影响请求：当作没有上游上下文。"""
    response = traced_app.client.get("/health", headers={"traceparent": "garbage"})

    assert response.status_code == 200
    span = traced_app.server_spans()[0]
    assert span.parent is None or span.parent.span_id == 0


# ---------------------------------------------------------------------------
# 4. 出站传播（adapter_http）
# ---------------------------------------------------------------------------


def test_outbound_request_carries_traceparent(traced_app: TracedApp) -> None:
    """出站请求带 traceparent：跨进程链路可串联。"""
    captured: dict[str, str] = {}

    class _RecordingClient:
        def request(self, method: str, url: object, **kwargs: object) -> object:
            captured.update({k: v for k, v in (kwargs.get("headers") or {}).items()})
            raise RuntimeError("stop-after-headers")

    with pytest.raises(RuntimeError):
        request_json(
            client=_RecordingClient(),  # type: ignore[arg-type]
            method="POST",
            url="https://adapter.example.com/training-runs",
            context="训练 Adapter 测试",
        )

    assert "traceparent" in captured
    parts = captured["traceparent"].split("-")
    assert len(parts[1]) == 32


def test_outbound_request_has_no_traceparent_when_disabled() -> None:
    """关闭态出站请求不带任何追踪头（与接入前逐字节一致）。"""
    configure_telemetry(Settings(otel_enabled=False))
    captured: dict[str, str] = {}

    class _RecordingClient:
        def request(self, method: str, url: object, **kwargs: object) -> object:
            captured.update({k: v for k, v in (kwargs.get("headers") or {}).items()})
            raise RuntimeError("stop-after-headers")

    with pytest.raises(RuntimeError):
        request_json(
            client=_RecordingClient(),  # type: ignore[arg-type]
            method="POST",
            url="https://adapter.example.com/training-runs",
            context="训练 Adapter 测试",
        )

    assert captured == {}


# ---------------------------------------------------------------------------
# 5. 日志里的 otel_trace_id
# ---------------------------------------------------------------------------


def test_enabled_logs_carry_otel_trace_id(traced_app: TracedApp) -> None:
    """开启态：请求处理期间打的日志带 otel_trace_id，且与响应头同值。"""
    records: list[dict[str, object]] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            import json

            records.append(json.loads(JsonFormatter().format(record)))

    handler = _Collector()
    handler.setFormatter(JsonFormatter())
    _PROBE_LOGGER.addHandler(handler)
    _PROBE_LOGGER.setLevel(logging.INFO)
    try:
        response = traced_app.client.get(_PROBE_PATH)
    finally:
        _PROBE_LOGGER.removeHandler(handler)

    matching = [r for r in records if r.get("otel_trace_id")]
    assert matching, "开启态日志缺少 otel_trace_id"
    assert matching[0]["otel_trace_id"] == response.headers["X-Trace-Id"]
    # 既有字段不受影响：trace_id（= request_id）仍在。
    assert "trace_id" in matching[0]
