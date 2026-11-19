"""性能基线脚本的纯函数层。

只使用标准库、不连接数据库、不发请求，便于直接单测：
DSN 安全校验、统计摘要、分页游标与 URL、阈值配置与判定。

安全边界（不可协商）：本模块只允许把**本地循环地址上的测试库**作为
CREATE/DROP DATABASE 的连接点；临时库名必须带 ``ddp_perf_`` 前缀。
"""

from __future__ import annotations

import base64
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit
from uuid import UUID

PERF_DB_PREFIX = "ddp_perf_"
# 安全白名单：只允许连本地测试库执行 DDL。这不是可调业务参数，故意不做环境覆盖。
DEFAULT_BASE_DATABASES = ("ddp_test", "postgres")
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_DB_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_MAX_PG_IDENTIFIER = 63


@dataclass(frozen=True, slots=True)
class PgTarget:
    """从 DSN 解析出的最小连接事实。"""

    scheme: str
    host: str
    port: int
    database: str


def parse_pg_dsn(dsn: str) -> PgTarget:
    """解析 ``postgresql+psycopg://user:pass@host:port/db``。

    只接受 PostgreSQL scheme；解析失败抛 ``ValueError``，由 CLI 打印后退出，
    不做任何"尽量继续"的容错——配置错误时继续执行才危险。
    """
    if not dsn or not dsn.strip():
        raise ValueError("数据库 DSN 不能为空")
    parts = urlsplit(dsn.strip())
    if not parts.scheme.startswith("postgresql"):
        raise ValueError(f"只接受 PostgreSQL DSN，实际 scheme={parts.scheme!r}")
    host = (parts.hostname or "").strip()
    if not host:
        raise ValueError("数据库 DSN 缺少主机名")
    database = unquote(parts.path.lstrip("/")).strip()
    if not database:
        raise ValueError("数据库 DSN 缺少库名")
    try:
        port = parts.port or 5432
    except ValueError as exc:
        raise ValueError(f"数据库 DSN 端口非法：{exc}") from exc
    return PgTarget(scheme=parts.scheme, host=host, port=port, database=database)


def with_database(dsn: str, database: str) -> str:
    """替换 DSN 的库名，保留账号、参数与 PostgreSQL driver。"""
    parts = urlsplit(dsn.strip())
    if not parts.scheme.startswith("postgresql"):
        raise ValueError(f"只接受 PostgreSQL DSN，实际 scheme={parts.scheme!r}")
    return urlunsplit(parts._replace(path=f"/{database}"))


def ensure_safe_base_dsn(
    dsn: str,
    *,
    allowed_databases: Sequence[str] = DEFAULT_BASE_DATABASES,
) -> PgTarget:
    """验证据以执行 CREATE/DROP DATABASE 的连接点。

    规则：本地循环地址 + 白名单库名。两条同时满足才允许继续；否则直接拒绝，
    避免误连开发/生产库执行 DDL。
    """
    target = parse_pg_dsn(dsn)
    if target.host.lower() not in LOOPBACK_HOSTS:
        raise ValueError(
            f"拒绝在非本机数据库上执行基线脚本：host={target.host!r}；"
            f"只允许 {sorted(LOOPBACK_HOSTS)}"
        )
    if target.database not in set(allowed_databases):
        raise ValueError(
            f"拒绝以 {target.database!r} 作为建库连接点；"
            f"只允许白名单库 {list(allowed_databases)}"
        )
    return target


def ensure_safe_perf_db_name(name: str) -> str:
    """校验临时库名：必须以 ``ddp_perf_`` 开头且是合法的小写标识符。"""
    if not name.startswith(PERF_DB_PREFIX):
        raise ValueError(f"临时库名必须以 {PERF_DB_PREFIX!r} 开头：{name!r}")
    if not name[len(PERF_DB_PREFIX) :] or name.endswith("_"):
        raise ValueError(f"临时库名缺少有效后缀：{name!r}")
    if len(name) > _MAX_PG_IDENTIFIER:
        raise ValueError(f"临时库名超过 PostgreSQL 标识符上限 {_MAX_PG_IDENTIFIER}：{name!r}")
    if not _DB_NAME_RE.match(name):
        raise ValueError(f"临时库名只能包含小写字母、数字与下划线：{name!r}")
    return name


def make_temp_db_name(scale: int, *, stamp: str, suffix: str) -> str:
    """生成确定可辨的临时库名，例如 ``ddp_perf_s1000_20261119123000_ab12``。"""
    if scale <= 0:
        raise ValueError("scale 必须为正整数")
    if not re.match(r"^[a-z0-9]+$", suffix):
        raise ValueError(f"临时库名后缀只允许小写字母数字：{suffix!r}")
    name = f"{PERF_DB_PREFIX}s{scale}_{stamp}_{suffix}"
    return ensure_safe_perf_db_name(name)


def parse_scales(raw: str) -> tuple[int, ...]:
    """解析 ``100,1000,10000``；去重、升序、拒绝非正数。"""
    values: set[int] = set()
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        if not text.isdigit() or int(text) <= 0:
            raise ValueError(f"数据规模必须是正整数：{item!r}")
        values.add(int(text))
    if not values:
        raise ValueError("至少需要一个数据规模")
    return tuple(sorted(values))


def parse_int_list(raw: str, *, name: str, minimum: int = 1) -> tuple[int, ...]:
    values: set[int] = set()
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        if not text.isdigit() or int(text) < minimum:
            raise ValueError(f"{name} 必须是 >= {minimum} 的整数：{item!r}")
        values.add(int(text))
    if not values:
        raise ValueError(f"{name} 不能为空")
    return tuple(sorted(values))


def percentile(values: Sequence[float], p: float) -> float:
    """最近秩百分位（nearest-rank），口径固定、可复算。

    rank = ceil(p/100 * n)，取排序后第 rank 个（1-based）。
    """
    if not values:
        raise ValueError("空样本无法计算百分位")
    if not 0 < p <= 100:
        raise ValueError(f"百分位必须在 (0, 100] 之间：{p}")
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def summarize(
    durations_ms: Sequence[float],
    *,
    wall_seconds: float,
    error_count: int,
) -> dict[str, float | int]:
    """汇总一组请求耗时：p50/p95/p99、均值、吞吐与错误率。"""
    total = len(durations_ms)
    if total == 0:
        raise ValueError("没有可汇总的请求")
    if wall_seconds <= 0:
        raise ValueError("统计窗口必须为正数")
    if error_count < 0 or error_count > total:
        raise ValueError("error_count 必须在 0..total 之间")
    ordered = sorted(durations_ms)
    return {
        "count": total,
        "error_count": error_count,
        "error_rate": round(error_count / total, 6),
        "throughput_rps": round(total / wall_seconds, 3),
        "p50_ms": round(percentile(ordered, 50), 3),
        "p95_ms": round(percentile(ordered, 95), 3),
        "p99_ms": round(percentile(ordered, 99), 3),
        "mean_ms": round(sum(ordered) / total, 3),
        "max_ms": round(ordered[-1], 3),
    }


def encode_cursor(value: UUID) -> str:
    """与 API 完全同一口径：UUID 字节的 urlsafe base64，去掉 ``=`` 填充。"""
    return base64.urlsafe_b64encode(value.bytes).decode("ascii").rstrip("=")


def cursor_at(ids: Sequence[UUID], fraction: float) -> str | None:
    """在按键排序的 ID 序列中取指定位置作为游标。

    返回值是"排在它之后"的分页起点，用于稳定复现深页场景。
    """
    if not ids:
        return None
    if not 0 <= fraction < 1:
        raise ValueError("fraction 必须在 [0, 1) 之间")
    ordered = sorted(ids)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return encode_cursor(ordered[index])


_LIST_PATHS = {
    "episodes": "/api/v1/projects/{project_id}/episodes",
    "raw-assets": "/api/v1/projects/{project_id}/raw-assets",
    "datasets": "/api/v1/projects/{project_id}/datasets",
}


def build_list_url(
    base_url: str,
    project_id: str,
    resource: str,
    *,
    limit: int,
    cursor: str | None = None,
    extra_query: Sequence[tuple[str, str]] = (),
) -> str:
    """构造只读列表 URL；资源路径集中在这一处，避免脚本内各处硬编码。"""
    try:
        path = _LIST_PATHS[resource]
    except KeyError as exc:
        raise ValueError(f"未知列表资源：{resource!r}") from exc
    query: list[tuple[str, str]] = [("limit", str(limit))]
    if cursor:
        query.append(("cursor", cursor))
    query.extend(extra_query)
    return f"{base_url.rstrip('/')}{path.format(project_id=project_id)}?{urlencode(query)}"


def build_playback_url(base_url: str, project_id: str, asset_id: str) -> str:
    return (
        f"{base_url.rstrip('/')}/api/v1/projects/{project_id}" f"/media-assets/{asset_id}/playback"
    )


@dataclass(frozen=True, slots=True)
class Thresholds:
    """阈值配置：默认值 + 通配模式，匹配越具体覆盖越晚（后写覆盖先写）。"""

    defaults: Mapping[str, float]
    patterns: tuple[tuple[str, Mapping[str, float]], ...]
    source: str

    def for_scenario(self, key: str) -> dict[str, float]:
        merged = dict(self.defaults)
        for pattern, values in self.patterns:
            if fnmatch(key, pattern):
                merged.update(values)
        return merged

    def unmatched_patterns(self, keys: Sequence[str]) -> list[str]:
        return [p for p, _ in self.patterns if not any(fnmatch(k, p) for k in keys)]


_THRESHOLD_KEYS = frozenset({"p95_ms", "min_rps", "max_error_rate"})


def load_thresholds(path: Path) -> Thresholds:
    """读取阈值 JSON；结构或键名非法时直接失败，避免阈值静默失效。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("阈值文件顶层必须是对象")
    if payload.get("version") != 1:
        raise ValueError("阈值文件 version 必须为 1")
    defaults = _threshold_values(payload.get("defaults", {}), where="defaults")
    scenarios = payload.get("scenarios", {})
    if not isinstance(scenarios, dict):
        raise ValueError("thresholds.scenarios 必须是对象")
    patterns: list[tuple[str, Mapping[str, float]]] = []
    for pattern, values in scenarios.items():
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("阈值模式必须是非空字符串")
        patterns.append((pattern, _threshold_values(values, where=f"scenarios[{pattern}]")))
    return Thresholds(defaults=defaults, patterns=tuple(patterns), source=str(path))


def _threshold_values(raw: Any, *, where: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError(f"{where} 必须是对象")
    unknown = set(raw) - _THRESHOLD_KEYS
    if unknown:
        raise ValueError(f"{where} 含未知键：{sorted(unknown)}")
    values: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{where}.{key} 必须是数值")
        if value < 0:
            raise ValueError(f"{where}.{key} 不能为负数")
        values[key] = float(value)
    return values


def evaluate_thresholds(
    results: Mapping[str, Mapping[str, float | int]],
    thresholds: Thresholds,
) -> dict[str, Any]:
    """按场景汇总判定阈值；返回可序列化结果，不抛异常。"""
    violations: list[dict[str, Any]] = []
    for key in sorted(results):
        summary = results[key]
        rule = thresholds.for_scenario(key)
        checks = (
            ("p95_ms", "p95_ms", "p95 超过阈值"),
            ("max_error_rate", "error_rate", "错误率超过阈值"),
        )
        for rule_key, field, message in checks:
            limit = rule.get(rule_key)
            actual = summary.get(field)
            if limit is not None and actual is not None and float(actual) > limit:
                violations.append(
                    {
                        "scenario": key,
                        "field": field,
                        "actual": actual,
                        "limit": limit,
                        "message": message,
                    }
                )
        min_rps = rule.get("min_rps")
        actual_rps = summary.get("throughput_rps")
        if min_rps is not None and actual_rps is not None and float(actual_rps) < min_rps:
            violations.append(
                {
                    "scenario": key,
                    "field": "throughput_rps",
                    "actual": actual_rps,
                    "limit": min_rps,
                    "message": "吞吐低于阈值",
                }
            )
    return {
        "passed": not violations,
        "evaluated_scenarios": len(results),
        "violations": violations,
        "unmatched_patterns": thresholds.unmatched_patterns(list(results)),
        "thresholds_source": thresholds.source,
    }
