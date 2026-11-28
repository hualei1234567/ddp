"""可观测性与告警生产闭环演练单元测试。

严格遵守 MEM-INC-030：本文件与被测演练脚本顶层**仅导入 Python 标准库**，
确保在只安装 ``pytest + pyyaml`` 的 CI ``Secret / 依赖 / Agent 守卫`` job 中
可以无报错收集并运行全部纯逻辑与回环测试；需要 ``fastapi`` / ``opentelemetry``
的端到端追踪用例在函数体内按需惰性检测。
"""

from __future__ import annotations

import ast
import gzip
import importlib.util
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
ALERT_DRILL_PATH = REPO / "scripts" / "ops" / "alerting_e2e_drill.py"
OTEL_DRILL_PATH = REPO / "scripts" / "ops" / "otel_trace_drill.py"
OTEL_COLLECTOR_CFG = REPO / "infra" / "monitoring" / "otel-collector-config.yaml"


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_drill_scripts_have_stdlib_only_top_level_imports() -> None:
    """MEM-INC-030 回归守护：两个演练脚本顶层只允许导入标准库模块。"""
    import sys

    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    for path in (ALERT_DRILL_PATH, OTEL_DRILL_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        top_imports: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_imports.add(node.module.split(".")[0])
        non_stdlib = top_imports - stdlib
        assert (
            not non_stdlib
        ), f"{path.name} 顶层导入了非标准库模块：{sorted(non_stdlib)}"


def test_otel_collector_config_structure() -> None:
    """otel-collector-config.yaml 必须声明 OTLP gRPC/HTTP 接收器、批处理与导出管道。"""
    import yaml

    assert OTEL_COLLECTOR_CFG.is_file(), f"缺少配置文件：{OTEL_COLLECTOR_CFG}"
    cfg = yaml.safe_load(OTEL_COLLECTOR_CFG.read_text(encoding="utf-8"))

    protocols = cfg["receivers"]["otlp"]["protocols"]
    assert protocols["grpc"]["endpoint"].endswith(":4317")
    assert protocols["http"]["endpoint"].endswith(":4318")

    assert "memory_limiter" in cfg["processors"]
    assert "batch" in cfg["processors"]

    assert "debug" in cfg["exporters"]
    assert "prometheus" in cfg["exporters"]
    assert cfg["exporters"]["prometheus"]["endpoint"].endswith(":8889")

    pipelines = cfg["service"]["pipelines"]
    assert "otlp" in pipelines["traces"]["receivers"]
    assert "debug" in pipelines["traces"]["exporters"]
    assert "otlp" in pipelines["metrics"]["receivers"]
    assert "prometheus" in pipelines["metrics"]["exporters"]

    assert "health_check" in cfg["extensions"]
    assert "health_check" in cfg["service"]["extensions"]


def test_alertmanager_v4_payload_and_loopback_guard() -> None:
    """告警演练载荷符合 Alertmanager v4 结构，且默认拒绝非回环目标。"""
    mod = _load_module("alerting_e2e_drill", ALERT_DRILL_PATH)

    firing = mod.build_alertmanager_v4_payload(status="firing", fingerprint="fp-1")
    assert firing["version"] == "4"
    assert firing["status"] == "firing"
    assert firing["alerts"][0]["endsAt"] == "0001-01-01T00:00:00Z"

    resolved = mod.build_alertmanager_v4_payload(status="resolved", fingerprint="fp-1")
    assert resolved["status"] == "resolved"
    assert resolved["alerts"][0]["endsAt"] != "0001-01-01T00:00:00Z"

    sig = mod.compute_hmac_signature("secret", b'{"ok":true}')
    assert sig.startswith("sha256=") and len(sig) == 71

    try:
        mod._ensure_loopback_url("http://prod-alert.example.com:8080")
    except ValueError as exc:
        assert "拒绝向非回环地址执行演练" in str(exc)
    else:
        raise AssertionError("非回环地址应被安全护栏拒绝")


def test_alerting_e2e_drill_embedded_passes() -> None:
    """全链路自拉起 alert-receiver：鉴权、HMAC 验签、落盘、去重、恢复与三方计数核对。"""
    mod = _load_module("alerting_e2e_drill", ALERT_DRILL_PATH)

    result = mod.run_alerting_drill(dedup_window_seconds=60.0)
    assert result["passed"] is True
    assert result["mode"] == "embedded"
    assert result["checks"]["health"]["status_code"] == 200
    assert result["checks"]["auth_missing_bearer_rejected"]["status_code"] == 401
    assert result["checks"]["auth_invalid_hmac_rejected"]["status_code"] == 401
    assert result["checks"]["firing_accepted"]["response"] == {
        "status": "accepted",
        "stored": True,
    }
    assert result["checks"]["duplicate_firing"]["response"] == {
        "status": "deduplicated",
        "stored": False,
    }
    assert result["checks"]["resolved_accepted"]["response"] == {
        "status": "accepted",
        "stored": True,
    }
    cross = result["checks"]["cross_check"]
    assert cross["total_delta"] == 2
    assert cross["firing_delta"] == 1
    assert cross["resolved_delta"] == 1
    assert cross["disk_ndjson_lines"] == 2


def test_otel_traceparent_and_stub_receiver() -> None:
    """W3C traceparent 构造/解析与本地 OTLP/HTTP 接收桩（含 gzip 解压）。"""
    mod = _load_module("otel_trace_drill", OTEL_DRILL_PATH)

    header, trace_id, parent_span_id = mod.build_traceparent()
    parsed = mod.parse_traceparent(header)
    assert parsed["version"] == "00"
    assert parsed["trace_id"] == trace_id
    assert parsed["parent_span_id"] == parent_span_id
    assert parsed["trace_flags"] == "01"

    stub = mod.OtlpHttpStubReceiver()
    base_url = stub.start()
    try:
        payload = gzip.compress(b"otlp-protobuf-bytes")
        req = urllib.request.Request(
            f"{base_url}/v1/traces",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/x-protobuf",
                "Content-Encoding": "gzip",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200

        assert len(stub.received) == 1
        assert stub.received[0]["path"] == "/v1/traces"
        assert stub.received[0]["body_bytes"] == b"otlp-protobuf-bytes"
    finally:
        stub.stop()


def test_otel_trace_drill_end_to_end_when_backend_deps_available() -> None:
    """当环境已安装 FastAPI + OpenTelemetry 时，验证真实 OTLP/HTTP 追踪闭环。"""
    import pytest

    pytest.importorskip("fastapi")
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")

    mod = _load_module("otel_trace_drill", OTEL_DRILL_PATH)
    result = mod.run_otel_trace_drill(service_name="ddp-api-pytest-drill")

    assert result["passed"] is True
    assert result["trace_propagation"]["trace_id_matched"] is True
    assert result["verified_health_span"]["name"] == "GET /health"
    assert result["verified_health_span"]["attributes"]["http.route"] == "/health"
    assert (
        result["verified_health_span"]["attributes"]["http.response.status_code"] == 200
    )
    assert result["total_exported_spans"] >= 1
