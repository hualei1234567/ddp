"""OpenTelemetry 追踪的装配层测试。

覆盖六件事，其中前两件是这套设计的硬约束：

1. **关闭态零开销**：未启用时不 import 任何 OTel 模块、不构造 TracerProvider、
   不产生 span、``current_trace_id()`` 返回 None；
2. **关闭态行为与接入前完全一致**：响应无 ``X-Trace-Id`` 头、无 span；
3. 开启态（内存 exporter 收集）：拿到 span、span 名是**路由模板**、属性齐全、
   响应头有 32 位 hex trace_id；
4. 跨请求传播：``traceparent`` 传入时子 span 的 parent 正确；
5. ``current_trace_id()`` 在有/无 span 时分别返回什么；
6. 配置解析：``DDP_OTEL_ENABLED=true`` 但未配 endpoint 时的降级语义。

HTTP 中间件的行为在 ``tests/api/test_telemetry_api.py``。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import pytest
from app.core.config import Settings
from app.core.telemetry import (
    _reset_state,
    configure_telemetry,
    current_trace_id,
    extract_context,
    get_tracer,
    inject_headers,
    is_enabled,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

#: 关闭态下绝不允许出现的 OTel 模块前缀。
_OTEL_MODULE_PREFIX = "opentelemetry"


@pytest.fixture(autouse=True)
def _clean_telemetry_state() -> None:
    """每个用例前后都清空进程内装配状态，避免用例间串味。"""
    _reset_state()
    yield
    _reset_state()


def _settings(**overrides: object) -> Settings:
    """构造一份「追踪开启、endpoint 已配」的 Settings。"""
    base: dict[str, object] = {
        "otel_enabled": True,
        "otel_service_name": "ddp-test",
        "otel_exporter_otlp_endpoint": "http://collector.invalid:4318",
        "otel_exporter_otlp_protocol": "http",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@dataclass
class Harness:
    """一次开启态装配的结果 + 收集 span 的内存 exporter。"""

    exporter: InMemorySpanExporter
    provider: TracerProvider

    def spans(self) -> list[object]:
        return list(self.exporter.get_finished_spans())


@pytest.fixture()
def enabled_harness() -> Harness:
    """装配一套开启态追踪，span 收集到内存 exporter。

    ``install_global=False``：OTel 的全局 provider 只能设置一次，装到全局会
    污染后续用例（重复设置只会得到一条无意义 warning）。这里用 provider 级
    tracer，行为与全局一致。
    """
    exporter = InMemorySpanExporter()
    state = configure_telemetry(
        _settings(),
        span_exporter=exporter,
        install_global=False,
        batch=False,
    )
    assert state.enabled is True
    return Harness(exporter=exporter, provider=state.provider)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. 关闭态：零开销
# ---------------------------------------------------------------------------


def test_disabled_does_not_import_otel_modules() -> None:
    """关闭时连 OTel 模块都不该被 import（零开销的硬指标）。"""
    _reset_state()
    # 清掉可能被其他用例间接引入的 OTel 模块，才能观察本次装配的真实行为。
    for name in [n for n in sys.modules if n.startswith(_OTEL_MODULE_PREFIX)]:
        del sys.modules[name]

    state = configure_telemetry(_settings(otel_enabled=False))

    assert state.enabled is False
    assert state.provider is None
    assert is_enabled() is False
    assert [n for n in sys.modules if n.startswith(_OTEL_MODULE_PREFIX)] == []


def test_disabled_creates_no_tracer_and_no_span() -> None:
    state = configure_telemetry(_settings(otel_enabled=False))

    assert state.provider is None
    assert get_tracer("ddp-test") is None
    assert current_trace_id() is None
    assert inject_headers() == {}


def test_disabled_makes_no_span_even_around_server_span() -> None:
    """``server_span`` 在关闭态产出 None，且不产生任何 span。"""
    from app.core.telemetry import server_span

    exporter = InMemorySpanExporter()
    configure_telemetry(
        _settings(otel_enabled=False),
        span_exporter=exporter,
        install_global=False,
        batch=False,
    )

    with server_span(name="GET /x") as span:
        assert span is None

    assert exporter.get_finished_spans() == ()


# ---------------------------------------------------------------------------
# 2. 「开了但没配 endpoint」→ 降级 + WARNING，不拒绝启动
# ---------------------------------------------------------------------------


def test_enabled_without_endpoint_degrades_to_noop(caplog: pytest.LogCaptureFixture) -> None:
    """未配 endpoint 时降级为不追踪，并显式记 WARNING（不静默假装已接线）。"""
    caplog.set_level("WARNING", logger="ddp.telemetry")

    state = configure_telemetry(_settings(otel_exporter_otlp_endpoint=""))

    assert state.enabled is False
    assert state.provider is None
    assert is_enabled() is False
    assert any("未配置" in record.message or "降级" in record.message for record in caplog.records)


def test_enabled_without_endpoint_keeps_service_running() -> None:
    """降级不是拒绝启动：装配不抛异常，应用照常可用。"""
    state = configure_telemetry(_settings(otel_exporter_otlp_endpoint="  "))

    assert state.enabled is False


def test_enabled_with_invalid_endpoint_is_rejected_by_settings() -> None:
    """endpoint 格式非法属于"配错了"，由启动自检 fail-closed 拒绝。"""
    settings = _settings(otel_exporter_otlp_endpoint="not-a-url")

    problems = settings._telemetry_config_problems()

    assert problems, "非法 endpoint 必须在启动自检里被拦下"


def test_endpoint_not_checked_when_tracing_disabled() -> None:
    """追踪关闭时 endpoint 写错也不影响启动（该配置根本不生效）。"""
    settings = _settings(otel_enabled=False, otel_exporter_otlp_endpoint="not-a-url")

    assert settings._telemetry_config_problems() == []


def test_sample_ratio_bounds_are_enforced() -> None:
    """采样比例必须落在 0~1（配错会在 Settings 构造期直接失败）。"""
    with pytest.raises(ValueError):
        Settings(otel_sample_ratio=1.5)
    with pytest.raises(ValueError):
        Settings(otel_sample_ratio=-0.1)


def test_protocol_must_be_grpc_or_http() -> None:
    with pytest.raises(ValueError):
        Settings(otel_exporter_otlp_protocol="thrift")


# ---------------------------------------------------------------------------
# 3. 开启态：span、资源属性
# ---------------------------------------------------------------------------


def test_enabled_creates_real_tracer(enabled_harness: Harness) -> None:
    assert enabled_harness.provider is not None
    assert is_enabled() is True


def test_configure_is_idempotent(enabled_harness: Harness) -> None:
    """重复装配不重建 provider（OTel 的全局 provider 只能设置一次）。"""
    exporter = InMemorySpanExporter()
    first = configure_telemetry(
        _settings(), span_exporter=exporter, install_global=False, batch=False
    )
    second = configure_telemetry(
        _settings(), span_exporter=exporter, install_global=False, batch=False
    )

    assert first is second


def test_resource_carries_service_identity(enabled_harness: Harness) -> None:
    """Resource 带 service.name / service.version / deployment.environment。"""
    settings = _settings()
    tracer = enabled_harness.provider.get_tracer("ddp")
    with tracer.start_as_current_span("probe"):
        pass

    span = enabled_harness.exporter.get_finished_spans()[0]
    resource = span.resource
    assert resource.attributes["service.name"] == "ddp-test"
    assert resource.attributes["service.version"] == settings.app_version
    assert resource.attributes["deployment.environment"] == settings.env


# ---------------------------------------------------------------------------
# 4. 跨请求传播：W3C traceparent
# ---------------------------------------------------------------------------


def test_extract_context_reads_traceparent(enabled_harness: Harness) -> None:
    carrier = {"traceparent": ("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")}

    context = extract_context(carrier)

    assert context is not None
    span_context = _span_context_from(context)
    assert span_context.trace_id != 0
    assert span_context.is_valid is True


def test_extract_context_without_traceparent_returns_root(enabled_harness: Harness) -> None:
    """没有合法 traceparent 时提取出的是空上下文（服务端 span 成为根 span）。"""
    context = extract_context({})

    assert context is not None
    # 空上下文里没有 span：``get_value`` 返回 None。
    from opentelemetry.context import get_value
    from opentelemetry.trace.propagation import _SPAN_KEY

    assert get_value(_SPAN_KEY, context=context) is None


def test_inject_headers_carries_traceparent(enabled_harness: Harness) -> None:
    """开启态下注入 traceparent（跨服务传播）。"""
    tracer = enabled_harness.provider.get_tracer("ddp")
    with tracer.start_as_current_span("probe"):
        headers = inject_headers()

    assert "traceparent" in headers
    parts = headers["traceparent"].split("-")
    assert len(parts) == 4
    assert len(parts[1]) == 32
    assert len(parts[2]) == 16


# ---------------------------------------------------------------------------
# 5. current_trace_id() 的两种返回
# ---------------------------------------------------------------------------


def test_current_trace_id_none_without_span(enabled_harness: Harness) -> None:
    assert current_trace_id() is None


def test_current_trace_id_is_32_hex_inside_span(enabled_harness: Harness) -> None:
    tracer = enabled_harness.provider.get_tracer("ddp")
    with tracer.start_as_current_span("probe"):
        trace_id = current_trace_id()

    assert trace_id is not None
    assert len(trace_id) == 32
    assert all(char in "0123456789abcdef" for char in trace_id)


def test_current_trace_id_none_when_tracing_disabled() -> None:
    configure_telemetry(_settings(otel_enabled=False))

    assert current_trace_id() is None


def _span_context_from(context: object) -> object:
    """从提取出的 Context 里取出携带的 SpanContext。"""
    from opentelemetry.context import get_value
    from opentelemetry.trace.propagation import _SPAN_KEY

    span = get_value(_SPAN_KEY, context=context)
    return span.get_span_context()
