"""OpenTelemetry (DDP_OTEL_ENABLED=true) 分布式追踪非 Mock 端到端演练脚本。

## 演练什么

启动一个本地轻量 HTTP OTLP 接收桩（``127.0.0.1:<随机端口>/v1/traces``），
配置 ``DDP_OTEL_ENABLED=true`` 与 ``DDP_OTEL_EXPORTER_OTLP_PROTOCOL=http``，
挂载真实 FastAPI 应用（含真实 ``TelemetryMiddleware`` 与官方 ``OTLPSpanExporter``），
发送带 W3C ``traceparent`` 头的 HTTP 请求并强制刷新导出，真实断言：

1. FastAPI 响应返回 200 且响应头 ``X-Trace-Id`` 与上游 ``traceparent`` 的 trace_id 一致；
2. 出站 ``inject_headers()`` 生成合法 W3C ``traceparent`` 头并延续同一链路；
3. 本地 OTLP HTTP 接收桩真实收到 ``POST /v1/traces`` 且 body 为非空 OTLP protobuf；
4. 反序列化 ``ExportTraceServiceRequest`` 真实包含 ``service.name``、路由模板 span 名
   （``GET /health``）、``http.route=/health``、``http.response.status_code=200`` 以及
   精确匹配的 ``trace_id`` 与 ``parent_span_id``。

## 依赖约束（MEM-INC-030）

本脚本顶层**仅导入 Python 标准库**；``fastapi`` / ``starlette`` / ``opentelemetry`` /
``app.*`` 一律在函数体内惰性导入，确保在最小 CI 环境（仅 pytest + pyyaml）中可安全导入。

## 用法

```bash
backend/.venv/bin/python scripts/ops/otel_trace_drill.py \
  --out docs/testing/evidence/2026-11-28-observability-closure/otel-drill-result.json
```
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import secrets
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

#: W3C Trace Context ``traceparent`` 正则：``00-<32hex>-<16hex>-<2hex>``
TRACEPARENT_RE = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)


def build_traceparent(
    *,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    sampled: bool = True,
) -> tuple[str, str, str]:
    """构造合法 W3C ``traceparent`` 请求头，返回 ``(header, trace_id, parent_span_id)``。"""
    resolved_trace = (trace_id or secrets.token_hex(16)).lower()
    resolved_parent = (parent_span_id or secrets.token_hex(8)).lower()
    flags = "01" if sampled else "00"
    header = f"00-{resolved_trace}-{resolved_parent}-{flags}"
    if not TRACEPARENT_RE.match(header):
        raise ValueError(f"非法 traceparent 参数：{header!r}")
    return header, resolved_trace, resolved_parent


def parse_traceparent(header: str) -> dict[str, str]:
    """解析 W3C ``traceparent`` 字符串。"""
    match = TRACEPARENT_RE.match(header.strip().lower())
    if not match:
        raise ValueError(f"非法 W3C traceparent 格式：{header!r}")
    return {
        "version": match.group(1),
        "trace_id": match.group(2),
        "parent_span_id": match.group(3),
        "trace_flags": match.group(4),
    }


class OtlpHttpStubReceiver:
    """本地回环轻量 OTLP/HTTP 接收桩（接受 ``POST /v1/traces``）。"""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.base_url: str = ""

    def start(self) -> str:
        receiver = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length > 0 else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                content_encoding = headers.get("content-encoding", "").lower()
                decoded_body = (
                    gzip.decompress(raw) if content_encoding == "gzip" else raw
                )

                with receiver._lock:
                    receiver.received.append(
                        {
                            "path": self.path,
                            "content_type": headers.get("content-type", ""),
                            "content_encoding": content_encoding,
                            "raw_bytes_len": len(raw),
                            "body_bytes": decoded_body,
                            "received_at": datetime.now(UTC).isoformat(),
                        }
                    )

                ack = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(ack)))
                self.end_headers()
                self.wfile.write(ack)

            def log_message(self, *_args: object) -> None:
                """静默访问日志，避免干扰结构化 JSON 输出。"""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        return self.base_url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


def _extract_any_value(value: Any) -> Any:
    """将 OTLP protobuf AnyValue 转成 Python 标量。"""
    kind = value.WhichOneof("value")
    if kind == "string_value":
        return value.string_value
    if kind == "int_value":
        return value.int_value
    if kind == "double_value":
        return value.double_value
    if kind == "bool_value":
        return value.bool_value
    return None


def decode_otlp_trace_request(body_bytes: bytes) -> dict[str, Any]:
    """反序列化 OTLP/HTTP protobuf ``ExportTraceServiceRequest``。"""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    request = ExportTraceServiceRequest()
    request.ParseFromString(body_bytes)

    spans_out: list[dict[str, Any]] = []
    resource_attrs_list: list[dict[str, Any]] = []

    for resource_spans in request.resource_spans:
        res_attrs = {
            kv.key: _extract_any_value(kv.value)
            for kv in resource_spans.resource.attributes
        }
        resource_attrs_list.append(res_attrs)
        for scope_spans in resource_spans.scope_spans:
            scope_name = scope_spans.scope.name
            for span in scope_spans.spans:
                span_attrs = {
                    kv.key: _extract_any_value(kv.value) for kv in span.attributes
                }
                spans_out.append(
                    {
                        "name": span.name,
                        "scope": scope_name,
                        "trace_id": span.trace_id.hex(),
                        "span_id": span.span_id.hex(),
                        "parent_span_id": span.parent_span_id.hex(),
                        "kind": int(span.kind),
                        "attributes": span_attrs,
                    }
                )

    return {
        "resource_count": len(resource_attrs_list),
        "resources": resource_attrs_list,
        "span_count": len(spans_out),
        "spans": spans_out,
    }


def run_otel_trace_drill(
    *,
    service_name: str = "ddp-api-otel-drill",
    sample_ratio: float = 1.0,
) -> dict[str, Any]:
    """执行真实 FastAPI + OTLP/HTTP 追踪闭环演练并返回结构化报告。"""
    backend_dir = REPO / "backend"
    data_platform_dir = REPO / "data-platform"
    for path_str in (str(data_platform_dir), str(backend_dir)):
        if path_str in sys.path:
            sys.path.remove(path_str)
        sys.path.insert(0, path_str)

    # 若同进程此前加载过 services/alert-receiver/app（同名顶层包 app），临时隔离并在结束时恢复
    stashed_app_modules: dict[str, Any] = {}
    existing_app = sys.modules.get("app")
    existing_file = getattr(existing_app, "__file__", "") or ""
    if existing_app is not None and not str(Path(existing_file).resolve()).startswith(
        str(backend_dir.resolve())
    ):
        for mod_name in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
            stashed_app_modules[mod_name] = sys.modules.pop(mod_name)

    # 临时设置生产同款 DDP_OTEL_* 环境变量（退出时原样恢复）
    env_overrides = {
        "DDP_ENV": "local",
        "DDP_RATE_LIMIT_ENABLED": "false",
        "DDP_OTEL_ENABLED": "true",
        "DDP_OTEL_SERVICE_NAME": service_name,
        "DDP_OTEL_EXPORTER_OTLP_ENDPOINT": "",  # 启动 stub 后填入
        "DDP_OTEL_EXPORTER_OTLP_PROTOCOL": "http",
        "DDP_OTEL_SAMPLE_RATIO": str(sample_ratio),
    }
    saved_env = {key: os.environ.get(key) for key in env_overrides}

    stub = OtlpHttpStubReceiver()
    stub_url = stub.start()
    env_overrides["DDP_OTEL_EXPORTER_OTLP_ENDPOINT"] = stub_url
    provider: Any = None
    telemetry_mod: Any = None
    get_settings_fn: Any = None

    try:
        os.environ.update(env_overrides)

        from app.core import telemetry
        from app.core.config import get_settings
        from app.main import create_app
        from starlette.testclient import TestClient

        telemetry_mod = telemetry
        get_settings_fn = get_settings
        if hasattr(get_settings, "cache_clear"):
            get_settings.cache_clear()
        telemetry._reset_state()

        app = create_app()

        assert telemetry.is_enabled(), "DDP_OTEL_ENABLED=true 后追踪应处于启用态"
        state = telemetry._state
        assert state is not None and state.provider is not None
        provider = state.provider

        incoming_traceparent, trace_id, parent_span_id = build_traceparent()

        # 1. 触发真实 FastAPI /health 请求并透传上游 W3C traceparent
        with TestClient(app) as client:
            response = client.get(
                "/health", headers={"traceparent": incoming_traceparent}
            )

        assert response.status_code == 200, f"/health 返回异常: {response.status_code}"
        response_trace_id = response.headers.get("x-trace-id", "")
        assert (
            response_trace_id == trace_id
        ), f"响应头 X-Trace-Id 与上游 traceparent 不一致: {response_trace_id!r} != {trace_id!r}"

        # 2. 验证出站 W3C traceparent 注入（client_span + inject_headers）
        parent_ctx = telemetry.extract_context({"traceparent": incoming_traceparent})
        with (
            telemetry.server_span(
                name="DRILL outbound-check", parent_context=parent_ctx
            ),
            telemetry.client_span(
                method="POST", url="http://adapter.internal/training-runs"
            ),
        ):
            outbound_headers = telemetry.inject_headers()

        outbound_traceparent = outbound_headers.get("traceparent", "")
        parsed_outbound = parse_traceparent(outbound_traceparent)
        assert (
            parsed_outbound["trace_id"] == trace_id
        ), f"出站 traceparent trace_id 断链: {parsed_outbound['trace_id']} != {trace_id}"

        # 3. 强制刷新 BatchSpanProcessor，让官方 OTLPSpanExporter 真实发送 HTTP POST
        flushed = provider.force_flush(timeout_millis=5000)
        assert flushed, "TracerProvider.force_flush 超时"

        assert (
            len(stub.received) >= 1
        ), "本地 OTLP HTTP 接收桩未收到任何 /v1/traces 请求"

        all_spans: list[dict[str, Any]] = []
        all_resources: list[dict[str, Any]] = []
        received_summaries: list[dict[str, Any]] = []

        for item in stub.received:
            assert item["path"] == "/v1/traces", f"非预期 OTLP 路径: {item['path']}"
            assert item["raw_bytes_len"] > 0, "收到空 OTLP 请求体"
            decoded = decode_otlp_trace_request(item["body_bytes"])
            all_spans.extend(decoded["spans"])
            all_resources.extend(decoded["resources"])
            received_summaries.append(
                {
                    "path": item["path"],
                    "content_type": item["content_type"],
                    "content_encoding": item["content_encoding"],
                    "raw_bytes_len": item["raw_bytes_len"],
                    "span_count": decoded["span_count"],
                    "received_at": item["received_at"],
                }
            )

        # 4. 断言反序列化后的真实 OTLP span 数据
        assert any(
            r.get("service.name") == service_name for r in all_resources
        ), f"OTLP Resource 缺少预期 service.name={service_name!r}: {all_resources}"

        health_spans = [
            s
            for s in all_spans
            if s["name"] == "GET /health"
            and s["trace_id"] == trace_id
            and s["parent_span_id"] == parent_span_id
        ]
        assert health_spans, f"OTLP 载荷中未找到匹配的 GET /health span: {all_spans}"
        health_span = health_spans[0]
        assert health_span["attributes"].get("http.route") == "/health"
        assert health_span["attributes"].get("http.response.status_code") == 200

        return {
            "passed": True,
            "executed_at": datetime.now(UTC).isoformat(),
            "otel_config": {
                "enabled": True,
                "service_name": service_name,
                "protocol": "http",
                "endpoint": f"{stub_url}/v1/traces",
                "sample_ratio": sample_ratio,
            },
            "trace_propagation": {
                "incoming_traceparent": incoming_traceparent,
                "response_x_trace_id": response_trace_id,
                "outbound_traceparent": outbound_traceparent,
                "trace_id_matched": response_trace_id
                == trace_id
                == parsed_outbound["trace_id"],
            },
            "otlp_stub_received": received_summaries,
            "verified_health_span": health_span,
            "total_exported_spans": len(all_spans),
        }
    finally:
        if provider is not None:
            provider.shutdown()
        if telemetry_mod is not None:
            telemetry_mod._reset_state()
        for key, prev in saved_env.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        if get_settings_fn is not None and hasattr(get_settings_fn, "cache_clear"):
            get_settings_fn.cache_clear()
        if stashed_app_modules:
            for mod_name in [
                k for k in sys.modules if k == "app" or k.startswith("app.")
            ]:
                sys.modules.pop(mod_name, None)
            sys.modules.update(stashed_app_modules)
        stub.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="DDP OpenTelemetry (OTLP/HTTP) 非 Mock 追踪演练"
    )
    parser.add_argument(
        "--service-name",
        default="ddp-api-otel-drill",
        help="演练上报的 OTel service.name（默认 ddp-api-otel-drill）",
    )
    parser.add_argument(
        "--sample-ratio",
        type=float,
        default=1.0,
        help="采样率 0~1（默认 1.0）",
    )
    parser.add_argument("--out", default="", help="演练报告 JSON 输出路径")
    args = parser.parse_args(argv)

    result = run_otel_trace_drill(
        service_name=args.service_name,
        sample_ratio=args.sample_ratio,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"演练结果已写入 {out_path}", file=sys.stderr)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
