#!/usr/bin/env python3
"""DDP-V21-N8-002 性能基线：只读列表分页与视频 Range。

默认 **dry-run**：只打印计划与安全校验结果，不建库、不写数据、不发请求。
必须显式 ``--apply`` 才执行；每个数据规模使用一个独立临时库
（``ddp_perf_*``），结束后删除，对象存储产物按 ``perf-baseline/<run_id>/``
前缀清理。禁止指向非本机或非白名单库。

用法::

    backend/.venv/bin/python scripts/perf/baseline.py            # dry-run
    backend/.venv/bin/python scripts/perf/baseline.py --apply \\
        --scales 100,1000,10000 --env-file .env \\
        --report-out docs/operations/perf-baseline-<date>.json

前置：本地 compose 栈（PG 55432、MinIO 9000）在跑；``--env-file`` 或进程
环境提供 ``DDP_JWT_SECRET`` 与 MinIO 凭据。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"
# 造数复用 app.infrastructure.db.models 的权威列定义；backend 必须在导入路径上。
for _path in (SCRIPTS_DIR, BACKEND_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from perf import core, dataset, report, runner  # noqa: E402

TASK_ID = "DDP-V21-N8-002"
DEFAULT_DSN = "postgresql+psycopg://ddp:ddp@localhost:55432/ddp_test"
DEFAULT_CHUNK_KIB = 64
ADVISORY_NOTE = "阈值为本机开发环境参考值，不是 CI 门禁；换硬件/PG 版本后需重新校准。"


def _load_env_file(path: Path) -> None:
    """把 KEY=VALUE 写入进程环境（不覆盖已显式设置的值）。"""
    if not path.exists():
        raise FileNotFoundError(f"环境文件不存在：{path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _safe_command(argv: Sequence[str]) -> str:
    """报告里不回显本机绝对路径（可移植、避免泄漏开发者目录结构）。"""
    return " ".join("<path>" if Path(item).is_absolute() else item for item in argv)


def _make_token(user_id: UUID) -> str:
    import jwt

    secret = os.environ.get("DDP_JWT_SECRET", "")
    if not secret:
        raise RuntimeError("缺少 DDP_JWT_SECRET；用 --env-file 或进程环境提供")
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iss": os.environ.get("DDP_JWT_ISSUER", "ddp"),
            "aud": os.environ.get("DDP_JWT_AUDIENCE", "ddp-api"),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
            "platform_role": "member",
        },
        secret,
        algorithm="HS256",
    )


def _read_scenarios(
    *,
    base_url: str,
    token: str,
    seed: dataset.SeedResult,
    page_sizes: Sequence[int],
    concurrencies: Sequence[int],
    requests: int,
    warmup: int,
    timeout_seconds: float,
) -> dict[str, dict[str, Any]]:
    headers = runner.bearer_headers(token)
    scenarios: dict[str, dict[str, Any]] = {}
    resources = (
        ("episodes", seed.episode_ids),
        ("raw-assets", seed.asset_ids),
        ("datasets", seed.dataset_ids),
    )
    for resource, ids in resources:
        for limit in page_sizes:
            variants = [
                runner.RequestSpec(
                    label=label,
                    method="GET",
                    url=core.build_list_url(
                        base_url,
                        str(seed.project_id),
                        resource,
                        limit=limit,
                        cursor=cursor,
                    ),
                    headers=headers,
                )
                for label, cursor in (
                    ("first", None),
                    ("mid", core.cursor_at(ids, 0.5)),
                    ("deep", core.cursor_at(ids, 0.9)),
                )
            ]
            for concurrency in concurrencies:
                result = runner.run_requests(
                    variants,
                    concurrency=concurrency,
                    requests=requests,
                    warmup=warmup,
                    timeout_seconds=timeout_seconds,
                )
                scenarios[f"{resource}.list.limit{limit}.c{concurrency}"] = result.as_report()

    filtered = [
        runner.RequestSpec(
            label="quality_pass",
            method="GET",
            url=core.build_list_url(
                base_url,
                str(seed.project_id),
                "episodes",
                limit=20,
                extra_query=(("quality_status", "PASS"),),
            ),
            headers=headers,
        )
    ]
    result = runner.run_requests(
        filtered,
        concurrency=1,
        requests=requests,
        warmup=warmup,
        timeout_seconds=timeout_seconds,
    )
    scenarios["episodes.filter.quality_pass.limit20.c1"] = result.as_report()
    return scenarios


def _video_scenarios(
    *,
    base_url: str,
    token: str,
    seed: dataset.SeedResult,
    concurrencies: Sequence[int],
    requests: int,
    warmup: int,
    timeout_seconds: float,
    object_size: int,
    chunk_size: int,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    headers = runner.bearer_headers(token)
    presign_specs = [
        runner.RequestSpec(
            label="presign",
            method="POST",
            url=core.build_playback_url(base_url, str(seed.project_id), str(asset_id)),
            headers=headers,
        )
        for asset_id in seed.media_asset_ids
    ]
    results: dict[str, dict[str, Any]] = {}
    for concurrency in concurrencies:
        measured = runner.run_requests(
            presign_specs,
            concurrency=concurrency,
            requests=max(20, requests // 2),
            warmup=warmup,
            timeout_seconds=timeout_seconds,
        )
        results[f"video.presign.c{concurrency}"] = measured.as_report()

    urls = runner.fetch_playback_urls(
        base_url, token, str(seed.project_id), [str(item) for item in seed.media_asset_ids]
    )
    runner.verify_range(urls[0], object_size=object_size, chunk_size=chunk_size)
    range_specs = runner.build_range_specs(
        urls, object_size=object_size, chunk_size=chunk_size, requests=requests
    )
    for concurrency in concurrencies:
        measured = runner.run_requests(
            range_specs,
            concurrency=concurrency,
            requests=requests,
            warmup=warmup,
            timeout_seconds=timeout_seconds,
        )
        results[f"video.range.64k.c{concurrency}"] = measured.as_report()
    info = {
        "status": "OK",
        "object_size_bytes": object_size,
        "chunk_size_bytes": chunk_size,
        "playback_urls": len(urls),
    }
    return info, results


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{TASK_ID} 性能基线")
    parser.add_argument("--dsn", default=os.environ.get("DDP_PERF_DSN", DEFAULT_DSN))
    parser.add_argument("--scales", default="100,1000,10000")
    parser.add_argument("--page-sizes", default="20,100")
    parser.add_argument("--concurrency", default="1,4,8")
    parser.add_argument("--requests", type=int, default=60)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--api-port", type=int, default=8123)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--thresholds", default=str(SCRIPT_DIR / "thresholds.json"))
    parser.add_argument("--report-out", default=None)
    parser.add_argument("--skip-video", action="store_true")
    parser.add_argument("--video-object-mib", type=int, default=4)
    parser.add_argument("--apply", action="store_true", help="真正建库、造数与测量")
    parser.add_argument("--keep", action="store_true", help="保留临时库与对象（排障）")
    parser.add_argument("--fail-on-threshold", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        target = core.ensure_safe_base_dsn(args.dsn)
        scales = core.parse_scales(args.scales)
        page_sizes = core.parse_int_list(args.page_sizes, name="page-sizes")
        concurrencies = core.parse_int_list(args.concurrency, name="concurrency")
    except ValueError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    if args.requests < 1 or args.warmup < 0 or args.video_object_mib < 1:
        print("配置错误：requests/video-object-mib 必须为正，warmup 不能为负", file=sys.stderr)
        return 2

    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    report_path = (
        Path(args.report_out)
        if args.report_out
        else REPO_ROOT / "docs" / "operations" / f"perf-baseline-{stamp}.json"
    )
    print(f"{TASK_ID} 性能基线")
    print(f"连接点：{target.host}:{target.port}/{target.database}（本地白名单已校验）")
    print(f"数据规模：{list(scales)}；分页大小：{list(page_sizes)}；并发：{list(concurrencies)}")
    print(f"每场景请求：{args.requests}（warmup {args.warmup}x并发）")
    print(f"报告输出：{report_path}")
    if not args.apply:
        print("\n[dry-run] 未做任何修改。确认无误后加 --apply 执行。")
        return 0

    if args.env_file:
        try:
            _load_env_file(Path(args.env_file))
        except FileNotFoundError as exc:
            print(f"配置错误：{exc}", file=sys.stderr)
            return 2
    elif (REPO_ROOT / ".env").exists():
        _load_env_file(REPO_ROOT / ".env")

    if not os.environ.get("DDP_JWT_SECRET"):
        print("配置错误：缺少 DDP_JWT_SECRET（--env-file 或进程环境）", file=sys.stderr)
        return 2
    thresholds_path = Path(args.thresholds)
    try:
        thresholds = core.load_thresholds(thresholds_path) if thresholds_path.exists() else None
    except ValueError as exc:
        print(f"配置错误：阈值文件非法：{exc}", file=sys.stderr)
        return 2
    if thresholds is None:
        print(f"警告：阈值文件不存在，报告只给数据不做判定：{thresholds_path}", file=sys.stderr)

    load_start = report.load_average()
    run_token = secrets.token_hex(3)
    runs: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, float | int]] = {}
    cleanup_failures: list[str] = []
    failed = False

    for scale in scales:
        db_name = core.make_temp_db_name(scale, stamp=stamp, suffix=f"{run_token}{scale}")
        temp_dsn = core.with_database(args.dsn, db_name)
        record: dict[str, Any] = {
            "scale": scale,
            "database": db_name,
            "scenarios": {},
            "video": {"status": "SKIPPED"} if args.skip_video else {},
        }
        server: runner.ApiServer | None = None
        objects: dataset.ObjectFixtures | None = None
        seed: dataset.SeedResult | None = None
        video_ready = False
        object_size = args.video_object_mib * 1024 * 1024
        print(f"\n[规模 {scale}] 临时库 {db_name}")
        try:
            dataset.create_database(args.dsn, db_name)
            record["migration_seconds"] = round(dataset.run_migrations(temp_dsn, BACKEND_DIR), 3)
            started = time.perf_counter()
            seed = dataset.seed_synthetic_data(
                temp_dsn, scale=scale, run_id=f"{stamp}-{scale}", with_video=not args.skip_video
            )
            record["seed_seconds"] = round(time.perf_counter() - started, 3)
            record["row_counts"] = dataset.table_counts(temp_dsn)
            print(
                f"  迁移 {record['migration_seconds']}s / 造数 {record['seed_seconds']}s / "
                f"行数 {record['row_counts']}"
            )

            if not args.skip_video:
                try:
                    objects = dataset.ObjectFixtures.from_settings()
                    upload_started = time.perf_counter()
                    objects.upload_synthetic_videos(seed.object_keys, size_bytes=object_size)
                    video_ready = True
                    record["object_upload_seconds"] = round(time.perf_counter() - upload_started, 3)
                except Exception as exc:  # 对象存储不可用时如实降级为 BLOCKED
                    record["video"] = {
                        "status": "BLOCKED",
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                    print(f"  video BLOCKED：{record['video']['reason']}", file=sys.stderr)

            server = runner.ApiServer(backend_dir=BACKEND_DIR, dsn=temp_dsn, port=args.api_port)
            server.start()
            token = _make_token(seed.user_id)
            record["scenarios"] = _read_scenarios(
                base_url=server.base_url,
                token=token,
                seed=seed,
                page_sizes=page_sizes,
                concurrencies=concurrencies,
                requests=args.requests,
                warmup=args.warmup,
                timeout_seconds=args.timeout,
            )
            if video_ready and seed.media_asset_ids:
                info, video_results = _video_scenarios(
                    base_url=server.base_url,
                    token=token,
                    seed=seed,
                    concurrencies=concurrencies,
                    requests=args.requests,
                    warmup=args.warmup,
                    timeout_seconds=args.timeout,
                    object_size=object_size,
                    chunk_size=DEFAULT_CHUNK_KIB * 1024,
                )
                record["video"] = info
                record["scenarios"].update(video_results)
            elif not args.skip_video and not record["video"]:
                record["video"] = {"status": "BLOCKED", "reason": "未生成媒体资产"}
            for key, scenario in record["scenarios"].items():
                summaries[f"s{scale}.{key}"] = scenario["summary"]
        except Exception as exc:  # 记录失败原因后停止，仍写报告并执行清理
            record["error"] = f"{type(exc).__name__}: {exc}"
            failed = True
            print(f"  失败：{record['error']}", file=sys.stderr)
        finally:
            if server is not None:
                server.stop()
                if record.get("error"):
                    record["api_log"] = server.tail_log(40)
                server.cleanup_log()
            if objects is not None and seed is not None:
                cleanup_failures.extend(objects.delete(seed.object_keys))
            if args.keep:
                print(f"  --keep：保留临时库 {db_name}")
            else:
                try:
                    dataset.drop_database(args.dsn, db_name)
                except Exception as exc:
                    cleanup_failures.append(f"drop {db_name}: {exc}")
                    print(f"  清理临时库失败：{exc}", file=sys.stderr)
        runs.append(record)
        if failed:
            break

    threshold_result: dict[str, Any]
    if thresholds is not None:
        threshold_result = core.evaluate_thresholds(summaries, thresholds)
        threshold_result["thresholds_source"] = thresholds_path.name
    else:
        threshold_result = {
            "passed": None,
            "evaluated_scenarios": len(summaries),
            "violations": [],
            "unmatched_patterns": [],
            "thresholds_source": None,
            "thresholds_sha256": None,
        }
    threshold_result["advisory"] = True
    threshold_result["note"] = ADVISORY_NOTE
    load_end = report.load_average()
    cpu_count = os.cpu_count() or 1
    noisy = bool(load_start and load_start["1m"] > cpu_count * 0.75)
    threshold_result["load_average_start"] = load_start
    threshold_result["load_average_end"] = load_end
    threshold_result["noisy_environment"] = noisy
    if noisy and threshold_result.get("passed") is False:
        threshold_result["note"] += (
            f"；本次运行开始时 1 分钟负载 {load_start['1m']} 已超过 {cpu_count} 核的 75%，"
            "超限项可能包含环境噪声，需在低负载时复测确认。"
        )
    if thresholds_path.exists():
        threshold_result["thresholds_sha256"] = hashlib.sha256(
            thresholds_path.read_bytes()
        ).hexdigest()

    payload = {
        "schema_version": 1,
        "task_id": TASK_ID,
        "generated_at": report.utc_now_iso(),
        "command": _safe_command(sys.argv),
        "environment": {
            "hardware": report.hardware_info(),
            "pg_version": report.pg_version(args.dsn),
            "minio_image": report.docker_image("ddp-minio"),
            "git": report.git_info(REPO_ROOT),
        },
        "config": {
            "scales": list(scales),
            "page_sizes": list(page_sizes),
            "concurrency": list(concurrencies),
            "requests_per_scenario": args.requests,
            "warmup": args.warmup,
            "video_enabled": not args.skip_video,
            "video_object_mib": args.video_object_mib,
            "chunk_kib": DEFAULT_CHUNK_KIB,
            "dsn_host": target.host,
            "dsn_database": target.database,
        },
        "thresholds": threshold_result,
        "runs": runs,
        "cleanup": {
            "kept": args.keep,
            "failures": cleanup_failures,
        },
    }
    if failed:
        payload["status"] = "FAILED"
    elif cleanup_failures:
        payload["status"] = "PARTIAL"
    else:
        payload["status"] = "OK"
    report.write_report(report_path, payload)
    report.print_summary(payload)
    print(f"\n报告已写入：{report_path}")

    if failed or cleanup_failures:
        return 1
    if args.fail_on_threshold and threshold_result.get("passed") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
