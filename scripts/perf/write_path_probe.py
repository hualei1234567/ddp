"""写路径容量探测（审计 R1：写路径零压测是最大盲区）。

## 这个脚本测什么、不测什么

**测**：写事务骨架的真实写入能力与并发争用——本项目每个领域写操作都在同一
事务里落「审计行 + outbox 事件」，这两张表是所有写路径的公共瓶颈点。
并发写它们的组合，能回答：可持续写入 TPS、p95 提交延迟、死锁/锁等待情况。

**不测**：端到端 HTTP 写接口（如标注提交）的业务延迟。原因：那需要先造出
任务/批次/分配/提交的完整前置数据，且会把业务校验耗时混进容量数字。本脚本
先用公共骨架建立容量基线，业务链路压测留后续任务。

## 安全护栏（硬编码，不可绕过）

1. **只允许本机回环 PG**（非 loopback 直接拒绝）
2. 建库连接点必须在白名单（`postgres` / `ddp`），临时库名必须以
   `ddp_perf_` 开头——避免误在开发库/生产库上跑 DDL
3. **默认 dry-run**，必须显式 `--execute`
4. 只写临时库，结束后 `DROP DATABASE`（除非 `--keep-db`）

## 用法

```bash
# 先看计划（不执行）
python scripts/perf/write_path_probe.py --dsn postgresql+psycopg://ddp:ddp@localhost:55432/postgres

# 真实执行
python scripts/perf/write_path_probe.py --dsn ... --execute --workers 1,4,16 --iterations 200
```
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Lock, Thread

from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "data-platform"))

from scripts.perf.core import (  # noqa: E402
    ensure_safe_base_dsn,
    ensure_safe_perf_db_name,
    percentile,
    with_database,
)

PROBE_PREFIX = "ddp_perf_write_"


@dataclass(frozen=True)
class WorkerResult:
    """单个并发 worker 的写入样本。"""

    latencies_ms: list[float]
    errors: list[str]


@dataclass(frozen=True)
class ProbeResult:
    """一档并发下的汇总结果。"""

    workers: int
    iterations_per_worker: int
    samples: int
    errors: int
    error_samples: list[str]
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    tps: float
    deadlocks: int
    lock_waits: int


def _create_temp_db(base_dsn: str) -> tuple[str, str]:
    """创建一次性压测库并跑迁移；返回 (库名, 该库的 DSN)。"""
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    name = ensure_safe_perf_db_name(f"{PROBE_PREFIX}{stamp}_{uuid.uuid4().hex[:6]}")
    admin = create_engine(
        base_dsn.replace("+psycopg", "+psycopg"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin.connect() as conn:
            # 库名标识符不能 bind；name 已由 ensure_safe_perf_db_name 校验前缀/长度/字符集
            # nosemgrep: ddp-no-raw-sql-fstring —— 标识符来自校验函数，非请求值
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()

    temp_dsn = with_database(base_dsn, name)
    env = {"DDP_DATABASE_URL": temp_dsn}
    import os

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO / "backend",
        env={**os.environ, **env, "PYTHONPATH": ".:../data-platform"},
        check=True,
        capture_output=True,
    )
    return name, temp_dsn


def _drop_temp_db(base_dsn: str, name: str) -> None:
    admin = create_engine(base_dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            # 同上：name 来自 ensure_safe_perf_db_name 校验后的探针临时库名。
            # nosemgrep: ddp-no-raw-sql-fstring —— 标识符来自校验函数，非请求值
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


def _seed_project(dsn: str) -> tuple[uuid.UUID, uuid.UUID]:
    """写入一个 project + user（外键需要）；返回 (project_id, user_id)。"""
    engine = create_engine(dsn)
    project_id, user_id = uuid.uuid4(), uuid.uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, display_name, created_at, updated_at)"
                    " VALUES (:id, :name, now(), now())"
                ),
                {"id": user_id, "name": "压测用户"},
            )
            conn.execute(
                text(
                    "INSERT INTO projects (id, code, name, status, version, created_at, updated_at)"
                    " VALUES (:id, :code, :name, 'ACTIVE', 1, now(), now())"
                ),
                {
                    "id": project_id,
                    "code": f"perf-{project_id.hex[:8]}",
                    "name": "压测项目",
                },
            )
    finally:
        engine.dispose()
    return project_id, user_id


def _write_once(conn, project_id: uuid.UUID, user_id: uuid.UUID, seq: int) -> None:
    """一次代表性写事务：审计行 + outbox 事件（本项目所有写路径的共同骨架）。"""
    occurred = datetime.now(UTC)
    conn.execute(
        text(
            "INSERT INTO audit_logs (id, project_id, actor_user_id, actor_role, resource_type,"
            " resource_id, action, result, occurred_at)"
            " VALUES (:id, :pid, :uid, 'OWNER', 'PERF_PROBE', :rid, 'PROBE', 'SUCCESS', :at)"
        ),
        {
            "id": uuid.uuid4(),
            "pid": project_id,
            "uid": user_id,
            "rid": uuid.uuid4(),
            "at": occurred,
        },
    )
    conn.execute(
        text(
            "INSERT INTO outbox_events (id, project_id, aggregate_type, aggregate_id, event_type,"
            " status, dispatch, attempts, available_at, created_at)"
            # 约束 ck_outbox_events_dispatch_status_consistent：
            # dispatch=false 时 status 必须为 RECORDED（表示只落库不投递）
            " VALUES (:id, :pid, 'PERF_PROBE', :aid, 'perf.probe.recorded', 'RECORDED', false, 0,"
            " :at, :at)"
        ),
        {"id": uuid.uuid4(), "pid": project_id, "aid": uuid.uuid4(), "at": occurred},
    )


def _run_worker(
    dsn: str,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    iterations: int,
    barrier: Barrier,
    sink: list[WorkerResult],
    lock: Lock,
) -> None:
    """一个并发写 worker：barrier 对齐起点，逐次记延迟。"""
    engine = create_engine(dsn, pool_size=1, max_overflow=0)
    latencies: list[float] = []
    errors: list[str] = []
    try:
        barrier.wait()
        for i in range(iterations):
            start = time.perf_counter()
            try:
                with engine.begin() as conn:
                    _write_once(conn, project_id, user_id, i)
                latencies.append((time.perf_counter() - start) * 1000.0)
            except Exception as exc:  # noqa: BLE001 - 压测需要记录任何失败
                errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        engine.dispose()
        with lock:
            sink.append(WorkerResult(latencies_ms=latencies, errors=errors))


def _probe_concurrency(
    dsn: str,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    workers: int,
    iterations: int,
) -> ProbeResult:
    """跑一档并发，返回汇总。"""
    barrier = Barrier(workers)
    sink: list[WorkerResult] = []
    lock = Lock()
    threads = [
        Thread(
            target=_run_worker,
            args=(dsn, project_id, user_id, iterations, barrier, sink, lock),
            daemon=True,
        )
        for _ in range(workers)
    ]
    wall_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - wall_start

    latencies = sorted(v for r in sink for v in r.latencies_ms)
    errors = [e for r in sink for e in r.errors]
    deadlocks = sum(1 for e in errors if "deadlock" in e.lower())
    lock_waits = sum(
        1 for e in errors if "lock" in e.lower() and "deadlock" not in e.lower()
    )
    return ProbeResult(
        workers=workers,
        iterations_per_worker=iterations,
        samples=len(latencies),
        errors=len(errors),
        error_samples=errors[:3],
        p50_ms=round(percentile(latencies, 50), 2) if latencies else 0.0,
        p95_ms=round(percentile(latencies, 95), 2) if latencies else 0.0,
        p99_ms=round(percentile(latencies, 99), 2) if latencies else 0.0,
        max_ms=round(max(latencies), 2) if latencies else 0.0,
        tps=round(len(latencies) / wall, 1) if wall > 0 else 0.0,
        deadlocks=deadlocks,
        lock_waits=lock_waits,
    )


def _pg_connection_stats(dsn: str) -> dict[str, object]:
    """读 PG 侧的连接与等待统计（容量模型的输入）。"""
    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            max_conn = conn.execute(text("SHOW max_connections")).scalar_one()
            active = conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                )
            ).scalar_one()
            waits = conn.execute(
                text(
                    "SELECT coalesce(wait_event_type, 'CPU') AS t, count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database() GROUP BY 1"
                )
            ).all()
    finally:
        engine.dispose()
    return {
        "max_connections": int(max_conn),
        "connections_now": int(active),
        "weighted_by_wait_type": {row[0]: int(row[1]) for row in waits},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="写路径容量探测（审计 R1）")
    parser.add_argument(
        "--dsn", required=True, help="建库连接点，必须指向本机 postgres/ddp 库"
    )
    parser.add_argument("--workers", default="1,4,16", help="并发档位，逗号分隔")
    parser.add_argument("--iterations", type=int, default=200, help="每 worker 迭代数")
    parser.add_argument(
        "--execute", action="store_true", help="真正执行（默认 dry-run）"
    )
    parser.add_argument("--keep-db", action="store_true", help="保留临时库供排查")
    parser.add_argument("--out", default="", help="结果 JSON 输出路径")
    args = parser.parse_args(argv)

    base = ensure_safe_base_dsn(args.dsn)
    workers_list = [int(w) for w in args.workers.split(",") if w.strip()]
    if not workers_list or max(workers_list) > 32:
        print("拒绝：并发档位必须在 1..32 之间（生产写保护）", file=sys.stderr)
        return 2

    plan = {
        "base": f"{base.host}:{base.port}/{base.database}",
        "workers": workers_list,
        "iterations_per_worker": args.iterations,
        "estimated_writes": sum(workers_list) * args.iterations * 2,
        "temp_db_prefix": PROBE_PREFIX,
    }
    print(
        json.dumps(
            {"mode": "execute" if args.execute else "dry-run", "plan": plan},
            ensure_ascii=False,
            indent=2,
        )
    )
    if not args.execute:
        print("\n[DRY-RUN] 未执行任何写操作。加 --execute 真实运行。", file=sys.stderr)
        return 0

    db_name, temp_dsn = _create_temp_db(args.dsn)
    print(f"\n临时库已建：{db_name}（迁移已跑）", file=sys.stderr)
    try:
        project_id, user_id = _seed_project(temp_dsn)
        results: list[ProbeResult] = []
        for workers in workers_list:
            print(f"  并发 {workers} × {args.iterations} 次写入...", file=sys.stderr)
            results.append(
                _probe_concurrency(
                    temp_dsn,
                    project_id,
                    user_id,
                    workers=workers,
                    iterations=args.iterations,
                )
            )
        stats = _pg_connection_stats(temp_dsn)
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "plan": plan,
            "results": [asdict(r) for r in results],
            "pg": stats,
            "notes": [
                "每次写入为「审计行 + outbox 事件」双写，代表本项目写事务骨架",
                "未覆盖业务校验耗时与端到端 HTTP 延迟",
                "临时库已删除，数据不落主库"
                if not args.keep_db
                else "临时库按 --keep-db 保留",
            ],
        }
        if args.out:
            Path(args.out).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"结果写入 {args.out}", file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        if args.keep_db:
            print(f"[保留] 临时库 {db_name} 未删除", file=sys.stderr)
        else:
            _drop_temp_db(args.dsn, db_name)
            print(f"临时库 {db_name} 已删除", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
