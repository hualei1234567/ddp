#!/usr/bin/env python3
"""G10 生产只读压测基线：对真实生产 API 的 GET 端点做短时并发压测。

与 staging 向的 ``baseline.py``（自建临时库 + 造数）不同，生产环境不能建库、
不能造数、不能写，且必须实时盯住 PostgreSQL 连接数；故单列本文件。纯函数
（端点解析/红线校验/脱敏/目标校验/分端点汇总）复用 :mod:`perf.core`，环境
元数据复用 :mod:`perf.report`，本文件只保留执行环。

安全红线（硬编码在 perf.core，无 CLI 放宽开关）：
1. 只发 GET，脚本内不存在写方法；端点描述含非 GET 语义直接拒绝；
2. 并发上限 32、每端点请求上限 2000、总时长硬上限 180 秒，超限拒绝执行；
3. 默认 dry-run，只打印计划；必须显式 ``--execute`` 才发请求；
4. 每秒读 ``pg_stat_activity``，占用 > max_connections 的 80% 立即中止；守卫
   连续 3 次查询失败也中止（无法确认安全时按不安全处理）；
5. 默认只允许本机目标，压远程需显式 ``--allow-non-loopback``。

用法见 ``scripts/perf/README.md``（token 铸造、红线、结果解读）。
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from perf import core, report  # noqa: E402
from perf.core import EndpointSpec, ReadonlySample  # noqa: E402

TASK_ID = "G10"
GUARD_FAILURE_LIMIT = 3
DEFAULT_PG_DSN = "postgresql+psycopg://ddp:ddp@127.0.0.1:55432/ddp"
DISCLAIMER = (
    "本报告是单机生产环境的短时只读基线，不等于容量规划结论；"
    "并发/数据分布/后台 worker 负载都会显著影响数字，跨机器与跨时间不可直接比较。"
)


def _plain_dsn(dsn: str) -> str:
    """SQLAlchemy 风格的 ``+psycopg`` 后缀去掉，psycopg 直连不认 driver。"""
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def _connection_snapshot(dsn: str) -> dict[str, Any]:
    """只读查询连接占用；查询失败抛异常，由调用方决定降级或中止。"""
    with psycopg.connect(_plain_dsn(dsn), connect_timeout=5) as connection:
        row = connection.execute(
            "select current_setting('max_connections')::int,"
            " (select count(*) from pg_stat_activity where backend_type = 'client backend')"
        ).fetchone()
    if row is None or int(row[0]) <= 0:
        raise RuntimeError(f"pg_stat_activity 查询结果异常：{row!r}")
    maximum, used = int(row[0]), int(row[1])
    return {
        "max_connections": maximum,
        "used": used,
        "ratio": round(used / maximum, 4),
        "at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }


class ConnectionGuard(threading.Thread):
    """连接数守卫：超阈值或连续查询失败时置中止事件。

    被测 API 的连接来自同一 PG 实例，所以看**整库**客户端后端数，而不只是本脚本
    （本脚本只占守卫连接 1 条）。
    """

    def __init__(self, dsn: str, *, interval_seconds: float = 1.0) -> None:
        super().__init__(name="ddp-pg-connection-guard", daemon=True)
        self._dsn, self._interval = dsn, interval_seconds
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.abort_event = threading.Event()
        self.abort_reason: str | None = None
        self.snapshots: list[dict[str, Any]] = []
        self.query_failures = 0

    def preflight(self) -> dict[str, Any]:
        """执行前取一次快照；连不上或已超阈值都直接失败。"""
        snapshot = _connection_snapshot(self._dsn)
        self.snapshots.append(snapshot)
        if snapshot["ratio"] > core.READONLY_CONNECTION_GUARD_RATIO:
            raise RuntimeError(
                f"执行前连接占比已超阈值：{snapshot['used']}/"
                f"{snapshot['max_connections']}={snapshot['ratio']:.2%}"
            )
        return snapshot

    def run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                snapshot = _connection_snapshot(self._dsn)
            except Exception as exc:  # noqa: BLE001 - 守卫失败必须降级为中止
                self.query_failures += 1
                if self.query_failures < GUARD_FAILURE_LIMIT:
                    continue
                self._abort(f"连接数守卫连续 {GUARD_FAILURE_LIMIT} 次查询失败：{exc}")
                return
            self.query_failures = 0
            with self._lock:
                self.snapshots.append(snapshot)
            if snapshot["ratio"] > core.READONLY_CONNECTION_GUARD_RATIO:
                self._abort(
                    f"PG 连接占用 {snapshot['used']}/{snapshot['max_connections']}"
                    f"={snapshot['ratio']:.2%} 超过 {core.READONLY_CONNECTION_GUARD_RATIO:.0%} 阈值"
                )
                return

    def _abort(self, reason: str) -> None:
        with self._lock:
            self.abort_reason = self.abort_reason or reason
        self.abort_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=5)

    def as_report(self) -> dict[str, Any]:
        snaps = self.snapshots
        return {
            "ratio_limit": core.READONLY_CONNECTION_GUARD_RATIO,
            "max_connections": snaps[0]["max_connections"] if snaps else None,
            "start": snaps[0] if snaps else None,
            "peak": max(snaps, key=lambda item: item["used"]) if snaps else None,
            "end": snaps[-1] if snaps else None,
            "sample_count": len(snaps),
            "query_failures": self.query_failures,
            "aborted": self.abort_event.is_set(),
            "abort_reason": self.abort_reason,
        }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="G10 生产只读压测基线（默认 dry-run，只发 GET）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="生产 API 地址")
    parser.add_argument(
        "--token", default=os.environ.get("DDP_LOAD_TOKEN", ""), help="JWT；默认读 DDP_LOAD_TOKEN"
    )
    parser.add_argument("--endpoints", required=True, help="逗号分隔，可用 name=path 命名")
    parser.add_argument("--project-id", default=None, help="替换端点里的 {project_id}")
    parser.add_argument("--concurrency", type=int, default=4, help="并发（上限 32）")
    parser.add_argument("--requests", type=int, default=200, help="每端点请求数（上限 2000）")
    parser.add_argument("--duration", type=float, default=0.0, help="总时长预算秒（0=按请求数）")
    parser.add_argument("--timeout", type=float, default=10.0, help="单请求超时秒")
    parser.add_argument("--pg-dsn", default=os.environ.get("DDP_PERF_PG_DSN", DEFAULT_PG_DSN))
    parser.add_argument("--health-path", default="/health", help="执行前健康检查路径")
    parser.add_argument("--json-out", default=None, help="原始 JSON 落盘路径")
    parser.add_argument("--execute", action="store_true", help="显式确认：真正发起压测")
    parser.add_argument("--dry-run", action="store_true", help="显式 dry-run（默认行为）")
    parser.add_argument("--allow-non-loopback", action="store_true", help="允许非本机目标")
    return parser.parse_args(argv)


def _auth_headers(token: str) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": f"ddp-readonly-load/{TASK_ID}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _issue(client: httpx.Client, url: str) -> tuple[int | None, str | None]:
    """发一次 GET，返回 (状态码, 错误描述)；2xx 之外都算错误。"""
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"[:160]
    if 200 <= response.status_code < 300:
        return response.status_code, None
    return response.status_code, f"HTTP {response.status_code}"


def _run_endpoint(
    spec: EndpointSpec,
    *,
    base_url: str,
    token: str,
    concurrency: int,
    requests: int,
    deadline: float,
    abort_event: threading.Event,
    timeout: float,
) -> tuple[dict[str, Any], list[ReadonlySample]]:
    """单端点并发压测：固定并发 worker，返回报告字段与原始样本。"""
    samples: list[ReadonlySample] = []
    lock = threading.Lock()
    issued = [0]

    def worker() -> None:
        client = httpx.Client(timeout=timeout, trust_env=False, headers=_auth_headers(token))
        try:
            while not abort_event.is_set() and time.monotonic() < deadline:
                with lock:
                    if issued[0] >= requests:
                        return
                    issued[0] += 1
                begin = time.perf_counter()
                status, error = _issue(client, base_url + spec.path)
                elapsed_ms = (time.perf_counter() - begin) * 1000
                with lock:
                    samples.append(ReadonlySample(elapsed_ms, status, error))
        finally:
            client.close()

    started_at = time.monotonic()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(concurrency)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall_seconds = max(time.monotonic() - started_at, 1e-9)
    payload = core.summarize_readonly_endpoint(
        spec.path,
        samples,
        wall_seconds=wall_seconds,
        planned=requests,
        truncated_by_time=len(samples) < requests and time.monotonic() >= deadline,
        aborted_by_guard=abort_event.is_set(),
    )
    return payload, samples


def _overall(samples: list[ReadonlySample], *, wall_seconds: float) -> dict[str, Any]:
    """整体汇总：全部端点原始样本合并后的 p50/p95/p99、RPS 与错误率。"""
    total = len(samples)
    errors = sum(1 for sample in samples if sample.error)
    overall: dict[str, Any] = {
        "requests_sent": total,
        "wall_seconds": round(wall_seconds, 4),
        "error_count": errors,
        "error_rate": round(errors / total, 6) if total else None,
        "throughput_rps": round(total / wall_seconds, 3),
    }
    if samples:
        durations = [sample.elapsed_ms for sample in samples]
        overall["p50_ms"] = round(core.percentile(durations, 50), 3)
        overall["p95_ms"] = round(core.percentile(durations, 95), 3)
        overall["p99_ms"] = round(core.percentile(durations, 99), 3)
    return overall


def _print_plan(
    args: argparse.Namespace, endpoints: list[EndpointSpec], snapshot: dict[str, Any] | None
) -> None:
    total = args.requests * len(endpoints)
    duration = args.duration if args.duration > 0 else core.READONLY_MAX_DURATION_SECONDS
    guard = (
        f"当前 {snapshot['used']}/{snapshot['max_connections']}={snapshot['ratio']:.2%}"
        if snapshot
        else "当前不可用（执行前会重查）"
    )
    print("=== G10 生产只读压测基线 · DRY-RUN（未发送任何请求）===")
    print(
        f"目标 {args.base_url} | 并发 {args.concurrency}/{core.READONLY_MAX_CONCURRENCY} | "
        f"每端点 {args.requests}/{core.READONLY_MAX_REQUESTS_PER_ENDPOINT} | "
        f"时长 {duration:g}/{core.READONLY_MAX_DURATION_SECONDS:g}s"
    )
    print(f"POSTGRES {core.mask_dsn(args.pg_dsn)}")
    print(f"连接数守卫 >{core.READONLY_CONNECTION_GUARD_RATIO:.0%} 即中止；{guard}")
    print(f"端点 {len(endpoints)} 个，计划请求 {total}：")
    for index, spec in enumerate(endpoints, start=1):
        print(f"  {index}. {spec.name:<28} {spec.path}")
    print("显式追加 --execute 才发压；本脚本不存在写方法。")


def _build_report(
    args: argparse.Namespace,
    endpoints: list[EndpointSpec],
    results: dict[str, dict[str, Any]],
    all_samples: list[ReadonlySample],
    guard: ConnectionGuard,
    *,
    wall_seconds: float,
    mode: str,
) -> dict[str, Any]:
    """组装完整报告：环境元数据 + 安全参数 + 分端点与整体统计。"""
    return {
        "task": f"{TASK_ID} 生产只读压测基线",
        "mode": mode,
        "generated_at": report.utc_now_iso(),
        "disclaimer": DISCLAIMER,
        "environment": {
            "base_url": args.base_url,
            "pg_dsn_masked": core.mask_dsn(args.pg_dsn),
            "pg_version": report.pg_version(args.pg_dsn),
            "hardware": report.hardware_info(),
            "git": report.git_info(REPO_ROOT),
            "load_average_start": report.load_average(),
        },
        "plan": {
            "concurrency": args.concurrency,
            "requests_per_endpoint": args.requests,
            "duration_budget_seconds": args.duration,
            "request_timeout_seconds": args.timeout,
            "wall_seconds": round(wall_seconds, 4),
            "endpoints": [{"name": spec.name, "path": spec.path} for spec in endpoints],
        },
        "safety": {
            "read_only": True,
            "methods": ["GET"],
            "limits": {
                "max_concurrency": core.READONLY_MAX_CONCURRENCY,
                "max_requests_per_endpoint": core.READONLY_MAX_REQUESTS_PER_ENDPOINT,
                "max_duration_seconds": core.READONLY_MAX_DURATION_SECONDS,
                "connection_guard_ratio": core.READONLY_CONNECTION_GUARD_RATIO,
            },
        },
        "connection_guard": guard.as_report(),
        "overall": _overall(all_samples, wall_seconds=wall_seconds),
        "per_endpoint": results,
    }


def _print_summary(payload: dict[str, Any]) -> None:
    overall = payload["overall"]
    print("\n=== 压测结果（单机生产环境短时只读基线）===")
    print(
        f"总请求 {overall['requests_sent']}，墙钟 {overall['wall_seconds']}s，"
        f"RPS {overall['throughput_rps']}，错误率 {overall['error_rate']}"
    )
    if "p95_ms" in overall:
        print(f"整体 p50={overall['p50_ms']}ms p95={overall['p95_ms']}ms p99={overall['p99_ms']}ms")
    print(f"\n{'端点':<26}{'n':>6}{'p50':>9}{'p95':>9}{'p99':>9}{'RPS':>9}{'错误率':>9}")
    for name, item in payload["per_endpoint"].items():
        summary = item.get("summary")
        if not summary:
            print(f"{name:<26}{item['requests_sent']:>6}   （无样本）")
            continue
        print(
            f"{name:<26}{summary['count']:>6}{summary['p50_ms']:>9.2f}"
            f"{summary['p95_ms']:>9.2f}{summary['p99_ms']:>9.2f}"
            f"{summary['throughput_rps']:>9.2f}{summary['error_rate']:>9.2%}"
        )
    guard = payload["connection_guard"]
    print(
        f"\nPG 连接守卫：峰值 {guard['peak']}，阈值 {guard['ratio_limit']:.0%}，"
        f"中止={guard['aborted']}"
    )
    if guard["aborted"]:
        print(f"中止原因：{guard['abort_reason']}")
    print(f"\n注意：{payload['disclaimer']}")


def _preflight_guard(
    args: argparse.Namespace, mode: str
) -> tuple[int | None, dict[str, Any] | None]:
    """执行前连接数预检；返回 (退出码, 快照)，退出码非空表示禁止继续。"""
    guard = ConnectionGuard(args.pg_dsn)
    try:
        snapshot = guard.preflight()
    except Exception as exc:  # noqa: BLE001 - 无法确认连接安全就不允许开始
        if mode == "execute":
            print(f"拒绝执行：连接数守卫不可用（{exc}）", file=sys.stderr)
            return 2, None
        print(f"[dry-run] 连接数守卫不可用，执行前会重查：{exc}", file=sys.stderr)
        return None, None
    print(
        f"连接数守卫预检：{snapshot['used']}/{snapshot['max_connections']}"
        f"={snapshot['ratio']:.2%}（阈值 {core.READONLY_CONNECTION_GUARD_RATIO:.0%}）"
    )
    if snapshot["ratio"] > core.READONLY_CONNECTION_GUARD_RATIO:
        print("拒绝执行：连接占用已超阈值", file=sys.stderr)
        return 2, snapshot
    return None, snapshot


def _preflight_health(base_url: str, health_path: str, token: str, timeout: float) -> str | None:
    """执行前健康检查；返回错误描述，正常返回 None。"""
    url = base_url.rstrip("/") + health_path
    try:
        response = httpx.get(url, timeout=timeout, trust_env=False, headers=_auth_headers(token))
    except httpx.HTTPError as exc:
        return f"健康检查请求失败：{type(exc).__name__}: {exc}"
    if 200 <= response.status_code < 300:
        return None
    return f"健康检查返回 HTTP {response.status_code}：{url}"


def _run_all(
    args: argparse.Namespace, endpoints: list[EndpointSpec], guard: ConnectionGuard
) -> tuple[dict[str, dict[str, Any]], list[ReadonlySample], float]:
    """串行跑各端点（每个端点内部固定并发），返回分项、全部样本与墙钟。"""
    budget = args.duration if args.duration > 0 else core.READONLY_MAX_DURATION_SECONDS
    deadline = time.monotonic() + min(budget, core.READONLY_MAX_DURATION_SECONDS)
    results: dict[str, dict[str, Any]] = {}
    all_samples: list[ReadonlySample] = []
    guard.start()
    started = time.monotonic()
    try:
        for index, spec in enumerate(endpoints, start=1):
            print(f"[{index}/{len(endpoints)}] {spec.name} → {spec.path}", flush=True)
            result, samples = _run_endpoint(
                spec,
                base_url=args.base_url,
                token=args.token,
                concurrency=args.concurrency,
                requests=args.requests,
                deadline=deadline,
                abort_event=guard.abort_event,
                timeout=args.timeout,
            )
            results[spec.name] = result
            all_samples.extend(samples)
            if guard.abort_event.is_set():
                print(f"连接数守卫中止：{guard.abort_reason}", file=sys.stderr)
                break
    finally:
        guard.stop()
    return results, all_samples, max(time.monotonic() - started, 1e-9)


def main(argv: Sequence[str] | None = None) -> int:
    """入口：校验 → dry-run 或执行 → 落盘 → 摘要。返回进程退出码。"""
    args = _parse_args(argv)
    mode = "execute" if args.execute and not args.dry_run else "dry-run"
    if args.execute and args.dry_run:
        print("同时给了 --execute 与 --dry-run，按更安全的 dry-run 处理。", file=sys.stderr)
    try:
        core.ensure_loopback_target(args.base_url, allow_non_loopback=args.allow_non_loopback)
        endpoints = core.resolve_project_placeholder(
            core.parse_readonly_endpoints(args.endpoints), args.project_id
        )
        core.validate_readonly_limits(
            concurrency=args.concurrency, requests=args.requests, duration=args.duration
        )
    except ValueError as exc:
        print(f"拒绝执行：{exc}", file=sys.stderr)
        return 2

    exit_code, snapshot = _preflight_guard(args, mode)
    if exit_code is not None:
        return exit_code
    if mode == "dry-run":
        _print_plan(args, endpoints, snapshot)
        print("\n[dry-run] 未发送任何请求。")
        return 0
    if not args.token:
        print("拒绝执行：缺少 token，请用 --token 或 DDP_LOAD_TOKEN 提供", file=sys.stderr)
        return 2
    health_error = _preflight_health(args.base_url, args.health_path, args.token, args.timeout)
    if health_error:
        print(f"拒绝执行：{health_error}", file=sys.stderr)
        return 2

    guard = ConnectionGuard(args.pg_dsn)
    guard.snapshots = [snapshot] if snapshot else []
    results, all_samples, wall_seconds = _run_all(args, endpoints, guard)
    payload = _build_report(
        args, endpoints, results, all_samples, guard, wall_seconds=wall_seconds, mode=mode
    )
    if args.json_out:
        report.write_report(Path(args.json_out), payload)
        print(f"原始 JSON 已写入：{args.json_out}")
    _print_summary(payload)
    if guard.abort_event.is_set():
        return 3
    return 1 if payload["overall"]["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
