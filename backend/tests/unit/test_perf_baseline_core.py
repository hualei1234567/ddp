"""DDP-V21-N8-002：性能基线脚本纯函数单测。

覆盖统计口径、游标/分页 URL、DSN 安全校验与阈值判定。脚本位于仓库根
``scripts/perf/``，不在 backend 的 import 路径里，按路径加载（与
``test_outbox_event_registry_all_domains.py`` 的既有做法一致）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CORE_PATH = _REPO_ROOT / "scripts" / "perf" / "core.py"


def _load_core():
    spec = importlib.util.spec_from_file_location("perf_core_under_test", _CORE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass(slots=True) 会按 __module__ 回查 sys.modules；按路径加载必须显式登记。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()


def test_percentile_uses_nearest_rank() -> None:
    values = [float(item) for item in range(1, 11)]
    assert core.percentile(values, 50) == 5.0
    assert core.percentile(values, 95) == 10.0
    assert core.percentile(values, 100) == 10.0
    assert core.percentile([7.0], 5) == 7.0
    with pytest.raises(ValueError):
        core.percentile([], 50)
    with pytest.raises(ValueError):
        core.percentile([1.0], 0)


def test_summarize_reports_error_rate_and_throughput() -> None:
    summary = core.summarize([10.0, 20.0, 30.0, 40.0], wall_seconds=2.0, error_count=1)
    assert summary["count"] == 4
    assert summary["error_count"] == 1
    assert summary["error_rate"] == 0.25
    assert summary["throughput_rps"] == 2.0
    assert summary["p50_ms"] == 20.0
    assert summary["p95_ms"] == 40.0
    assert summary["max_ms"] == 40.0
    with pytest.raises(ValueError):
        core.summarize([1.0], wall_seconds=0, error_count=0)
    with pytest.raises(ValueError):
        core.summarize([1.0], wall_seconds=1, error_count=2)


def test_parse_scales_sorts_and_deduplicates() -> None:
    assert core.parse_scales("10000, 100,1000,100") == (100, 1000, 10000)
    for raw in ("", "0", "-1", "abc", "100, x"):
        with pytest.raises(ValueError):
            core.parse_scales(raw)


def test_parse_int_list_rejects_below_minimum() -> None:
    assert core.parse_int_list("8,1,4", name="concurrency") == (1, 4, 8)
    with pytest.raises(ValueError):
        core.parse_int_list("0,1", name="page-sizes")


def test_ensure_safe_base_dsn_requires_loopback_and_allowlist() -> None:
    target = core.ensure_safe_base_dsn("postgresql+psycopg://ddp:ddp@127.0.0.1:55432/ddp_test")
    assert (target.host, target.port, target.database) == ("127.0.0.1", 55432, "ddp_test")
    with pytest.raises(ValueError, match="非本机"):
        core.ensure_safe_base_dsn("postgresql+psycopg://ddp:ddp@db.internal:5432/ddp_test")
    with pytest.raises(ValueError, match="白名单"):
        core.ensure_safe_base_dsn("postgresql+psycopg://ddp:ddp@localhost:55432/ddp")
    with pytest.raises(ValueError, match="PostgreSQL"):
        core.ensure_safe_base_dsn("sqlite+pysqlite:///tmp/x.db")


def test_with_database_preserves_credentials_and_query() -> None:
    dsn = "postgresql+psycopg://ddp:secret@localhost:55432/ddp_test?sslmode=disable"
    replaced = core.with_database(dsn, "ddp_perf_s100_x")
    assert "ddp:secret@localhost:55432/ddp_perf_s100_x" in replaced
    assert "sslmode=disable" in replaced
    assert core.parse_pg_dsn(replaced).database == "ddp_perf_s100_x"


def test_temp_db_name_requires_prefix_and_legal_identifier() -> None:
    name = core.make_temp_db_name(1000, stamp="20261119120000", suffix="ab12")
    assert name == "ddp_perf_s1000_20261119120000_ab12"
    assert core.ensure_safe_perf_db_name(name) == name
    for bad in ("ddp_perf_S1000", "ddp_perf_", "perf_s1000", "ddp_perf_a-b"):
        with pytest.raises(ValueError):
            core.ensure_safe_perf_db_name(bad)
    with pytest.raises(ValueError):
        core.make_temp_db_name(0, stamp="x", suffix="ab")


def test_cursor_matches_api_encoding_and_deep_position() -> None:
    ids = [UUID(int=index) for index in range(1, 11)]
    assert core.encode_cursor(ids[0]) == "AAAAAAAAAAAAAAAAAAAAAQ"
    assert core.encode_cursor(ids[-1]) == "AAAAAAAAAAAAAAAAAAAACg"
    # 10 条记录的 50% 位置是第 6 条（index=5），90% 位置是第 10 条（index=9）。
    assert core.cursor_at(ids, 0.5) == core.encode_cursor(ids[5])
    assert core.cursor_at(ids, 0.9) == core.encode_cursor(ids[9])
    assert core.cursor_at([], 0.5) is None


def test_build_list_url_contains_limit_cursor_and_filter() -> None:
    url = core.build_list_url(
        "http://127.0.0.1:8123/",
        "11111111-1111-1111-1111-111111111111",
        "episodes",
        limit=100,
        cursor="abc",
        extra_query=(("quality_status", "PASS"),),
    )
    assert url.startswith("http://127.0.0.1:8123/api/v1/projects/")
    assert "/episodes?" in url
    assert "limit=100" in url
    assert "cursor=abc" in url
    assert "quality_status=PASS" in url
    with pytest.raises(ValueError):
        core.build_list_url("http://x", "p", "unknown", limit=20)


def _write_thresholds(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_threshold_wildcards_merge_defaults_and_detect_violations(tmp_path: Path) -> None:
    path = _write_thresholds(
        tmp_path,
        {
            "version": 1,
            "defaults": {"max_error_rate": 0.0},
            "scenarios": {
                "s*.episodes.list.*": {"p95_ms": 100},
                "s1000.episodes.list.limit20.c1": {"p95_ms": 50, "min_rps": 2},
            },
        },
    )
    thresholds = core.load_thresholds(path)
    rule = thresholds.for_scenario("s1000.episodes.list.limit20.c1")
    assert rule == {"max_error_rate": 0.0, "p95_ms": 50, "min_rps": 2}
    result = core.evaluate_thresholds(
        {
            "s1000.episodes.list.limit20.c1": {
                "p95_ms": 60,
                "error_rate": 0.0,
                "throughput_rps": 1.0,
            },
            "s100.datasets.list.limit20.c1": {
                "p95_ms": 10,
                "error_rate": 0.01,
                "throughput_rps": 10.0,
            },
        },
        thresholds,
    )
    assert result["passed"] is False
    fields = {(item["scenario"], item["field"]) for item in result["violations"]}
    assert ("s1000.episodes.list.limit20.c1", "p95_ms") in fields
    assert ("s1000.episodes.list.limit20.c1", "throughput_rps") in fields
    assert ("s100.datasets.list.limit20.c1", "error_rate") in fields
    assert result["unmatched_patterns"] == []


def test_threshold_missing_patterns_and_unknown_keys_fail_loudly(tmp_path: Path) -> None:
    path = _write_thresholds(
        tmp_path,
        {"version": 1, "defaults": {}, "scenarios": {"never.match": {"p95_ms": 1}}},
    )
    thresholds = core.load_thresholds(path)
    result = core.evaluate_thresholds(
        {"s100.episodes.list.limit20.c1": {"p95_ms": 1.0}}, thresholds
    )
    assert result["passed"] is True
    assert result["unmatched_patterns"] == ["never.match"]

    bad = _write_thresholds(
        tmp_path,
        {"version": 1, "defaults": {}, "scenarios": {"x": {"p95": 1}}},
    )
    with pytest.raises(ValueError, match="未知键"):
        core.load_thresholds(bad)


def test_random_uuid_cursor_roundtrips() -> None:
    value = uuid4()
    encoded = core.encode_cursor(value)
    raw = encoded + "=" * (-len(encoded) % 4)
    import base64

    assert UUID(bytes=base64.urlsafe_b64decode(raw)) == value
