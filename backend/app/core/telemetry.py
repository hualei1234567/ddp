"""OpenTelemetry 分布式追踪的**装配层**。

本模块只回答三个问题：要不要追踪、用哪个 TracerProvider、当前追踪上下文是什么。
**不含 HTTP 适配**：服务端 span 由 ``app/api/middleware/telemetry.py`` 开启，
出站 client span 由 ``app/infrastructure/adapter_http.py`` 开启。

## 默认关闭、零开销（硬约束）

未配置 exporter 时：

- **不 import 任何 opentelemetry 模块**（含 SDK 与 exporter）——所有 import 都写在
  函数体内，模块导入路径上没有任何 OTel 依赖；
- 不构造 TracerProvider，不设置全局 provider；
- 不产生任何 span；
- 响应头不带 ``X-Trace-Id``；
- 出站请求不带 ``traceparent``。

也就是说「关闭」不是「打开但没人看」，而是真正的旁路：关闭态下本模块对请求
路径的贡献只有一次布尔判断（``is_enabled()``）。

### 为什么必须 lazy import

实测（Python 3.11、本机）：``import opentelemetry.api.trace`` 约 35ms，
``opentelemetry.sdk.trace`` 约 30ms，OTLP exporter 还要再拖起 protobuf，
gRPC 传输还要拖起 grpcio。这些开销全部发生在**进程启动**路径上，与是否产生
span 无关——即使随后走 no-op 分支也已经付了代价。顶层 import + 开关判断的写法
会把这段启动开销无条件付给所有部署，故一律 lazy import。

## 「已启用但未配置 endpoint」的语义：降级 + WARNING，不拒绝启动

追踪是**可观测性**依赖，不是**业务正确性**依赖：它挂了，业务照常服务。把它
升级成「启动失败」，等于让一次监控配置错误变成一次全站可用性故障。仓库里已有
同源口径：``DDP_METRICS_ACCESS_TOKEN`` 未配置时不让 API 启动，只让 ``/metrics``
返回 503（见 ``app/core/config.py`` 中 ``_metrics_access_config_problems`` 的
注释）——可观测性的配置问题应降级为「该项不可用」，而不是拖垮主链路。

但降级**必须显式**：记一条 WARNING，说明追踪未接线、日志里的 ``trace_id`` 仍是
``request_id``。静默降级会让人以为「已经在追踪了」，比直接报错更难排查。

对照：endpoint **配了但格式非法**（不是 http/https 绝对地址）属于配置写错，
由 ``Settings`` 在启动自检里拒绝启动（fail-closed），不降级。

## 「一次配置、多处复用」的实现约束

防高基数的路由归一逻辑只有一处权威实现（``app/core/metrics.py`` 的
``route_label_from_scope`` / ``method_label``），本模块**不另写一套**；中间件
直接复用 ``Metrics`` 实例的方法。这样 Prometheus 指标与分布式 trace 的
「路径桶」永远一致，不会出现两个口径。

## 出站传播

``inject_headers()`` 把当前上下文注入为 W3C ``traceparent`` / ``tracestate``；
``extract_context()`` 反向提取。propagator 固定为官方 W3C tracecontext
（**不**传播 baggage：客户端可任意构造 baggage，传播它等于把不可信数据带进
下游服务与日志）。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from app.core.config import Settings

if TYPE_CHECKING:
    from opentelemetry.context import Context
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SpanExporter
    from opentelemetry.trace import Span, Tracer

logger = logging.getLogger("ddp.telemetry")

#: OTel instrumentation scope 名称（trace 的来源标识）。
INSTRUMENTATION_NAME: Final = "ddp"

#: 未显式配置服务名时的兜底值（OTel 惯例用小写短名）。
DEFAULT_SERVICE_NAME: Final = "ddp"

#: OTLP/HTTP 传输的信号路径（官方 collector 默认暴露 ``/v1/traces``）。
OTLP_HTTP_TRACES_PATH: Final = "/v1/traces"

#: 支持的 OTLP 传输协议。``grpc`` 对应 4317 端口，``http`` 对应 4318 端口。
SUPPORTED_PROTOCOLS: Final = ("grpc", "http")

#: span 属性的取值类型（OTel 只接受标量与标量序列）。
AttributeValue = str | int | float | bool

_state_lock = threading.Lock()
_state: TelemetryState | None = None


@dataclass(frozen=True, slots=True)
class TelemetryState:
    """一次装配的结果快照（不可变）。

    ``enabled`` 为 False 时 ``provider`` 必为 None：此时本模块不持有任何 OTel
    对象，``get_tracer()`` 返回 None、``current_trace_id()`` 返回 None。
    """

    enabled: bool
    service_name: str
    endpoint: str
    protocol: str
    sample_ratio: float
    provider: TracerProvider | None = None


def _disabled_state(settings: Settings, *, reason: str = "") -> TelemetryState:
    """构造关闭态快照。

    ``reason`` 只用于日志，不进状态本身：状态只需要表达「开」或「不开」。
    """
    if reason:
        logger.warning("OpenTelemetry 追踪未启用：%s", reason)
    return TelemetryState(
        enabled=False,
        service_name=settings.otel_service_name or DEFAULT_SERVICE_NAME,
        endpoint="",
        protocol=settings.otel_exporter_otlp_protocol,
        sample_ratio=settings.otel_sample_ratio,
        provider=None,
    )


def configure_telemetry(
    settings: Settings,
    *,
    span_exporter: SpanExporter | None = None,
    install_global: bool = True,
    batch: bool = True,
) -> TelemetryState:
    """幂等初始化 TracerProvider。

    - 关闭（``DDP_OTEL_ENABLED=false``）：返回关闭态，**不 import 任何 OTel 模块**；
    - 开启但未配置 endpoint：**降级为关闭态 + WARNING**（理由见模块注释）；
    - 开启且已配置 endpoint：构建 provider 并（默认）安装为全局 provider。

    参数：

    - ``span_exporter``：注入自定义 exporter（测试用内存 exporter 收集 span）；
      为 None 时按协议构造官方 OTLP exporter。
    - ``install_global``：是否调用 ``trace.set_tracer_provider``。测试传 False
      即可获得真实 span 而不污染进程级全局 provider（OTel 的全局 provider
      只能设置一次，重复设置只会得到一条无意义 warning）。
    - ``batch``：是否用 ``BatchSpanProcessor``（异步批量、生产默认）。测试传
      False 用 ``SimpleSpanProcessor``，span 立即可见。

    幂等语义：已启用时重复调用直接返回既有状态，**不重复安装全局 provider**
    （重复安装是 OTel 明令禁止的操作，且会与首次安装的 exporter 争抢 span）。
    """
    global _state

    with _state_lock:
        existing = _state
        if existing is not None and existing.enabled:
            return existing

        if not settings.otel_enabled:
            _state = _disabled_state(settings, reason="DDP_OTEL_ENABLED 未开启")
            return _state

        endpoint = settings.otel_exporter_otlp_endpoint.strip()
        if not endpoint:
            _state = _disabled_state(
                settings,
                reason=(
                    "已设置 DDP_OTEL_ENABLED=true 但未配置 DDP_OTEL_EXPORTER_OTLP_ENDPOINT，"
                    "本次降级为不追踪（不产生 span、响应不带 X-Trace-Id）"
                ),
            )
            return _state

        try:
            exporter = span_exporter or _build_span_exporter(
                endpoint=endpoint,
                protocol=settings.otel_exporter_otlp_protocol,
            )
            provider = _build_provider(settings=settings, exporter=exporter, batch=batch)
        except Exception as exc:  # noqa: BLE001 - 装配失败必须降级而非拖垮启动
            _state = _disabled_state(
                settings,
                reason=(f"追踪装配失败（{type(exc).__name__}: {exc}），本次降级为不追踪"),
            )
            return _state

        if install_global:
            _install_global_provider(provider)
        _install_propagator()
        _state = TelemetryState(
            enabled=True,
            service_name=settings.otel_service_name or DEFAULT_SERVICE_NAME,
            endpoint=endpoint,
            protocol=settings.otel_exporter_otlp_protocol,
            sample_ratio=settings.otel_sample_ratio,
            provider=provider,
        )
        logger.info(
            "OpenTelemetry 追踪已启用：service=%s endpoint=%s protocol=%s sample_ratio=%s",
            _state.service_name,
            _state.endpoint,
            _state.protocol,
            _state.sample_ratio,
        )
        return _state


def is_enabled() -> bool:
    """追踪是否已接线。关闭时为 False（此时不应产生任何 span）。"""
    state = _state
    return state is not None and state.enabled


def get_tracer(name: str) -> Tracer | None:
    """取得 tracer；**未启用时返回 None**（调用方据此短路，不产生 span）。"""
    state = _state
    if state is None or not state.enabled or state.provider is None:
        return None
    return state.provider.get_tracer(name)


def current_trace_id() -> str | None:
    """当前追踪上下文的 trace id（32 位小写 hex）。

    - 未启用追踪：``None``；
    - 已启用但当前没有活跃 span：``None``；
    - 已启用且处于某个 span 内：该 span 的 trace id（W3C 格式，32 位 hex）。

    与 ``app.core.logging.current_trace_id()`` 不是同一个东西：后者是「日志关联
    用的请求标识」（关闭追踪时等于 ``request_id``），本函数只回答「OTel 的
    trace id 是多少」。
    """
    state = _state
    if state is None or not state.enabled:
        return None
    from opentelemetry.trace import format_trace_id, get_current_span

    span_context = get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return format_trace_id(span_context.trace_id)


@contextmanager
def server_span(
    *,
    name: str,
    attributes: Mapping[str, AttributeValue] | None = None,
    parent_context: Context | None = None,
) -> Iterator[Span | None]:
    """开启一个服务端 span；未启用时产出 ``None``（不产生任何 span）。"""
    tracer = get_tracer(INSTRUMENTATION_NAME)
    if tracer is None:
        yield None
        return
    from opentelemetry.trace import SpanKind

    with tracer.start_as_current_span(
        name,
        context=parent_context,
        kind=SpanKind.SERVER,
        attributes=dict(attributes or {}),
    ) as span:
        yield span


@contextmanager
def client_span(
    *,
    method: str,
    url: str,
) -> Iterator[Span | None]:
    """开启一个出站 client span；未启用时产出 ``None``。

    ``url`` 只用于取**路径**与主机名作为 span 属性：查询串与 fragment 不进
    span（可能带凭据，且属于高基数维度）。
    """
    tracer = get_tracer(INSTRUMENTATION_NAME)
    if tracer is None:
        yield None
        return
    from opentelemetry.trace import SpanKind

    parsed = urlsplit(url)
    with tracer.start_as_current_span(
        f"{method} {parsed.path or '/'}",
        kind=SpanKind.CLIENT,
        attributes={
            "http.request.method": method,
            "url.path": parsed.path or "/",
        },
    ) as span:
        yield span


def inject_headers() -> dict[str, str]:
    """把当前追踪上下文注入为 W3C 请求头。

    未启用追踪时返回空 dict：不 import OTel、不产生任何头，出站请求与接入前
    逐字节一致。
    """
    state = _state
    if state is None or not state.enabled:
        return {}
    from opentelemetry.propagate import inject

    headers: dict[str, str] = {}
    inject(headers)
    return headers


def extract_context(carrier: Mapping[str, str]) -> Context | None:
    """从 W3C ``traceparent``/``tracestate`` 头提取上游追踪上下文。

    未启用追踪时返回 ``None``；没有合法 ``traceparent`` 时同样返回 ``None``
    ——此时服务端 span 是根 span。
    """
    state = _state
    if state is None or not state.enabled:
        return None
    from opentelemetry.propagate import extract

    return extract(carrier)


def _build_span_exporter(*, endpoint: str, protocol: str) -> SpanExporter:
    """按协议构造官方 OTLP exporter（重量级 import 只发生在这里）。"""
    if protocol == "grpc":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=_resolve_endpoint(endpoint, protocol))


def _resolve_endpoint(endpoint: str, protocol: str) -> str:
    """把配置的 endpoint 解析成 exporter 可直接使用的地址。

    OTLP/gRPC 用主机端口（``http://collector:4317``）；OTLP/HTTP 必须给**完整
    信号路径**（``http://collector:4318/v1/traces``）——显式传 ``endpoint=``
    时 SDK 不会自动补路径（只有 ``OTEL_EXPORTER_OTLP_ENDPOINT`` 环境变量才会
    自动拼接），故这里补齐。
    """
    if protocol == "grpc":
        return endpoint
    parsed = urlsplit(endpoint)
    if parsed.path in ("", "/"):
        return endpoint.rstrip("/") + OTLP_HTTP_TRACES_PATH
    return endpoint


def _build_provider(
    *,
    settings: Settings,
    exporter: SpanExporter,
    batch: bool,
) -> TracerProvider:
    """构造 TracerProvider（Resource + 采样器 + span processor）。"""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name or DEFAULT_SERVICE_NAME,
            "service.version": settings.app_version,
            "deployment.environment": settings.env,
        }
    )
    provider = TracerProvider(
        resource=resource,
        sampler=TraceIdRatioBased(settings.otel_sample_ratio),
    )
    processor = BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    return provider


def _install_global_provider(provider: TracerProvider) -> None:
    """把 provider 装成进程级全局 provider。

    OTel 的全局 provider **只能设置一次**（第二次调用只会得到一条 warning），
    故调用方必须先做幂等判断（本模块的 ``configure_telemetry`` 已保证）。
    """
    from opentelemetry import trace as otel_trace

    otel_trace.set_tracer_provider(provider)


def _install_propagator() -> None:
    """把全局 propagator 固定为官方 W3C tracecontext。

    只用 tracecontext（不传播 baggage）：客户端可任意构造 baggage，传播它等于
    把不可信数据带进下游服务与日志，而跨服务传播只需要
    ``traceparent``/``tracestate``。
    """
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    set_global_textmap(CompositePropagator([TraceContextTextMapPropagator()]))


def _reset_state() -> None:
    """仅供测试：清空进程内装配状态。

    下一次 ``configure_telemetry`` 会重新初始化。**生产代码不得调用**——
    生产依赖「一次装配、全进程共享」的语义。
    """
    global _state
    with _state_lock:
        _state = None


__all__ = [
    "DEFAULT_SERVICE_NAME",
    "INSTRUMENTATION_NAME",
    "OTLP_HTTP_TRACES_PATH",
    "SUPPORTED_PROTOCOLS",
    "TelemetryState",
    "configure_telemetry",
    "current_trace_id",
    "extract_context",
    "get_tracer",
    "inject_headers",
    "is_enabled",
    "server_span",
    "client_span",
]
