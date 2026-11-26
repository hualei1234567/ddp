"""G10 生产只读压测基线：纯函数层单测（端点解析 / 红线校验 / 脱敏 / 汇总）。

生产只读压测的安全红线全部落在 :mod:`perf.core` 的纯函数里，这里逐条钉住：
超限必须拒绝、写方法无法表达、非本机目标默认拒绝。脚本位于仓库根
``scripts/perf/``，不在 backend 的 import 路径里，按路径加载（与
``test_perf_baseline_core.py`` 的既有做法一致）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CORE_PATH = _REPO_ROOT / "scripts" / "perf" / "core.py"


def _load_core():
    spec = importlib.util.spec_from_file_location("perf_core_readonly_under_test", _CORE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()


def test_mask_dsn_hides_password_only() -> None:
    masked = core.mask_dsn("postgresql+psycopg://ddp:s3cret@127.0.0.1:55432/ddp")
    assert "s3cret" not in masked
    assert masked == "postgresql+psycopg://ddp:***@127.0.0.1:55432/ddp"
    assert core.mask_dsn("postgresql://ddp@127.0.0.1/ddp") == "postgresql://ddp@127.0.0.1/ddp"


def test_parse_endpoints_supports_names_and_defaults() -> None:
    specs = core.parse_readonly_endpoints(
        "health=/health, /api/v1/projects/{project_id}/episodes?limit=20"
    )
    assert [(item.name, item.path) for item in specs] == [
        ("health", "/health"),
        (
            "/api/v1/projects/{project_id}/episodes?limit=20",
            "/api/v1/projects/{project_id}" "/episodes?limit=20",
        ),
    ]


def test_parse_endpoints_keeps_query_string_without_name() -> None:
    """回归：查询串里的 ``=`` 不能被误判成 ``name=path`` 分隔符。"""
    specs = core.parse_readonly_endpoints("/api/v1/projects/{project_id}/episodes?limit=20")
    assert specs[0].path == "/api/v1/projects/{project_id}/episodes?limit=20"
    assert specs[0].name == specs[0].path


@pytest.mark.parametrize(
    "raw",
    [
        "http://evil.example.com/x",  # 绝对 URL：只能压 base-url 下的路径
        "POST /api/v1/projects",  # 含空白：写方法无法被表达
        "GET\t/api/v1/projects",
        "a=/x,a=/y",  # 端点名重复
        "",  # 空清单
    ],
)
def test_parse_endpoints_rejects_write_or_ambiguous_input(raw: str) -> None:
    with pytest.raises(ValueError):
        core.parse_readonly_endpoints(raw)


def test_resolve_project_placeholder() -> None:
    specs = core.parse_readonly_endpoints("episodes=/api/v1/projects/{project_id}/episodes")
    resolved = core.resolve_project_placeholder(specs, "p-1")
    assert resolved[0].path == "/api/v1/projects/p-1/episodes"
    with pytest.raises(ValueError, match="project-id"):
        core.resolve_project_placeholder(specs, None)
    # 没有占位符就不要求项目 ID，且原样返回
    plain = core.parse_readonly_endpoints("health=/health")
    assert core.resolve_project_placeholder(plain, None)[0].path == "/health"


def test_validate_limits_enforces_hard_redlines() -> None:
    core.validate_readonly_limits(concurrency=32, requests=2000, duration=180)
    core.validate_readonly_limits(concurrency=1, requests=1, duration=0)
    with pytest.raises(ValueError, match="并发"):
        core.validate_readonly_limits(concurrency=33, requests=1, duration=0)
    with pytest.raises(ValueError, match="请求数"):
        core.validate_readonly_limits(concurrency=1, requests=2001, duration=0)
    with pytest.raises(ValueError, match="时长"):
        core.validate_readonly_limits(concurrency=1, requests=1, duration=180.5)


def test_ensure_loopback_target_rejects_remote_by_default() -> None:
    assert core.ensure_loopback_target("http://127.0.0.1:8000", allow_non_loopback=False) == (
        "127.0.0.1"
    )
    assert core.ensure_loopback_target("http://localhost:8000", allow_non_loopback=False) == (
        "localhost"
    )
    with pytest.raises(ValueError, match="非本机"):
        core.ensure_loopback_target("https://prod.example.com", allow_non_loopback=False)
    assert core.ensure_loopback_target("https://prod.example.com", allow_non_loopback=True) == (
        "prod.example.com"
    )
    with pytest.raises(ValueError, match="base-url"):
        core.ensure_loopback_target("ftp://127.0.0.1/x", allow_non_loopback=False)


def test_summarize_endpoint_uses_shared_statistics() -> None:
    samples = [
        core.ReadonlySample(10.0, 200, None),
        core.ReadonlySample(20.0, 200, None),
        core.ReadonlySample(30.0, 500, "HTTP 500"),
        core.ReadonlySample(40.0, None, "ConnectError: boom"),
    ]
    payload = core.summarize_readonly_endpoint(
        "/health",
        samples,
        wall_seconds=2.0,
        planned=5,
        truncated_by_time=True,
        aborted_by_guard=False,
    )
    assert payload["requests_sent"] == 4
    assert payload["status_counts"] == {"200": 2, "500": 1, "network_error": 1}
    assert payload["sample_errors"] == ["HTTP 500", "ConnectError: boom"]
    assert payload["truncated_by_time"] is True
    summary = payload["summary"]
    assert summary["count"] == 4
    assert summary["error_count"] == 2
    assert summary["error_rate"] == 0.5
    assert summary["p95_ms"] == 40.0
    # 空样本不得伪造统计
    empty = core.summarize_readonly_endpoint(
        "/health",
        [],
        wall_seconds=1.0,
        planned=1,
        truncated_by_time=False,
        aborted_by_guard=False,
    )
    assert "summary" not in empty
