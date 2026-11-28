#!/usr/bin/env python3
"""处理管线（processing run）并发规模探测（T2-01/02 Pipeline 规模化压测）。

## 这个脚本测什么、不测什么

**测**：并发创建多个 processing run 的写容量——完整走真实端点
``POST /api/v1/projects/{project_id}/processing-runs``（201，初始状态
``CREATED``），经过真实的 JWT 鉴权、项目成员校验、乐观锁版本、审计行与
``processing.run.requested`` outbox 事件落库。这是 **T2-01 全量样本基准的
最小化版本**：全量 200 demos 的真实样本数据并不存在，本脚本用**合成规模**
（每次迭代一个全新 ``input_refs`` 的 run）做容量模型，并在报告里如实标注，
不冒充全量样本基准。

**不测**：
- 不测八阶段 Worker 执行链（``app.workers.processing`` 是异步的，HTTP
  线程创建完即返回；Worker 执行不在本次压测范围内）；
- 不测标注写路径（那是 ``write_path_e2e.py`` 的职责）；
- 不测跨进程横向扩容（本脚本是单进程内并发，结论只对单实例成立）。

## 与既有资产的关系（复用优先，不另起平行体系）

| 资产 | 复用点 |
|---|---|
| ``scripts/perf/core.py`` | ``ensure_safe_base_dsn`` / ``ensure_safe_perf_db_name`` / ``with_database`` / ``summarize`` / ``mask_dsn`` |
| ``scripts/perf/write_path_e2e.py`` | 连接池红线判定 ``pool_redline_conclusion``、``_ConnectionSampler``、``_pg_connection_stats``、临时库 + Alembic 模式、生产连接池口径（10/20）——**直接导入，保证结论口径完全一致** |
| ``backend/tests/api/conftest.py`` | ``seed_identity``（project + editor + 成员关系，身份基线） |
| ``backend/tests/jwt_support.py`` | 测试用 JWT 构造（HS256，真实签名校验路径，不是假身份头） |

## 死锁 / 锁等待的观测口径

并发创建 run 的锁风险集中在 ``processing_runs`` 的插入与审计/outbox 双写：

1. ``pg_stat_database.deadlocks``：PG **累计**死锁计数，压测前后各读一次，
   差值即本次压测新增的死锁数（最硬的死锁证据，不依赖采样）；
2. ``pg_stat_activity.wait_event_type = 'Lock'``：压测期间与每档结束时的
   锁等待连接数（采样峰值 + 档末快照）；
3. 错误样本里的 ``deadlock detected`` 文本（若有）会原样保留在报告里。

## 安全护栏（硬编码，不可绕过）

1. **只允许本机回环 PG**（非 loopback 直接拒绝，见 ``ensure_safe_base_dsn``）；
2. 建库连接点必须在白名单（``postgres`` / ``ddp_test``），临时库名必须以
   ``ddp_perf_`` 开头（见 ``ensure_safe_perf_db_name``）；
3. **默认 dry-run**，必须显式 ``--execute``；
4. 只写临时库，结束后 ``DROP DATABASE``（除非 ``--keep-db``）；
5. **绝不建表到既有库**：所有 DDL 都发生在临时库上，迁移通过
   ``DDP_DATABASE_URL`` 指向临时库执行。

## 用法

```bash
# 先看计划（不建库、不写数据、不发请求）
backend/.venv/bin/python scripts/perf/pipeline_scale_probe.py \\
    --dsn postgresql+psycopg://ddp:ddp@localhost:55432/postgres

# 真实执行
backend/.venv/bin/python scripts/perf/pipeline_scale_probe.py \\
    --dsn postgresql+psycopg://ddp:ddp@localhost:55432/postgres \\
    --concurrency 1,4,8,16 --iterations 25 --execute \\
    --out docs/testing/evidence/2026-11-28-pipeline-scale/result.json
```
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"
DATA_PLATFORM_DIR = REPO_ROOT / "data-platform"
# 复用 backend 的 app 包与测试夹具，backend 与 data-platform 必须在导入路径上。
for _path in (SCRIPTS_DIR, BACKEND_DIR, DATA_PLATFORM_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from perf.core import (  # noqa: E402
    ensure_safe_base_dsn,
    ensure_safe_perf_db_name,
    mask_dsn,
    summarize,
    with_database,
)

# 连接池红线判定与连接采样直接复用写路径压测的实现：口径一致、结论可比。
from perf.write_path_e2e import (  # noqa: E402
    PRODUCTION_MAX_OVERFLOW,
    PRODUCTION_POOL_SIZE,
    _ConnectionSampler,
    _pg_connection_stats,
    pool_redline_conclusion,
)

#: 临时库名前缀：必须落在 `ensure_safe_perf_db_name` 允许的 `ddp_perf_` 前缀内。
TEMP_DB_PREFIX = "ddp_perf_scale_"

#: 并发档位红线：与写路径压测同一口径——单进程内并发超过这个数会让
#: 「连接池是否够用」的结论失真（瓶颈从 DB 变成 Python GIL / 线程调度）。
MAX_CONCURRENCY = 32

#: 每档迭代数红线：单档请求数上限，避免误填巨大数字把本机跑挂。
MAX_ITERATIONS = 2000

#: PG 连接数采样间隔（秒）：与写路径压测同一口径。
CONNECTION_SAMPLE_INTERVAL_SECONDS = 0.05

#: 默认管线版本：与 ``backend/tests/api/test_processing_runs_api.py`` 一致。
DEFAULT_PIPELINE_VERSION = "pipeline-v1"


# ---------------------------------------------------------------------------
# 纯函数层：结果汇总、锁观测与参数红线
#
# 放在脚本顶部且不依赖 FastAPI/SQLAlchemy，便于直接单测口径。
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunSample:
    """一次 processing run 创建请求的结果。"""

    elapsed_ms: float
    status: int | None
    error: str | None


@dataclass(frozen=True, slots=True)
class TierResult:
    """一档并发下的汇总。"""

    workers: int
    iterations: int
    wall_seconds: float
    samples: tuple[RunSample, ...]
    status_counts: dict[str, int]
    error_samples: tuple[str, ...]
    #: 档末观测：``pg_stat_activity`` 中处于 Lock 等待的连接数。
    lock_wait_connections: int = 0
    #: 档内新增死锁数：``pg_stat_database.deadlocks`` 的前后差值。
    deadlock_delta: int = 0

    @property
    def error_count(self) -> int:
        return sum(1 for sample in self.samples if sample.error is not None)


def summarize_tier(result: TierResult) -> dict[str, Any]:
    """汇总一档并发：TPS、p50/p95/p99/max、错误数与锁观测。

    吞吐与延迟口径与 ``perf.core.summarize`` 完全一致（最近秩百分位），
    与 ``write_path_e2e.summarize_tier`` 同一结构，便于两批证据对照。
    """
    if not result.samples:
        raise ValueError("没有可汇总的请求样本")
    summary = summarize(
        [sample.elapsed_ms for sample in result.samples],
        wall_seconds=result.wall_seconds,
        error_count=result.error_count,
    )
    return {
        "workers": result.workers,
        "iterations_per_worker": result.iterations,
        "requests_sent": len(result.samples),
        "wall_seconds": round(result.wall_seconds, 4),
        "tps": summary["throughput_rps"],
        "p50_ms": summary["p50_ms"],
        "p95_ms": summary["p95_ms"],
        "p99_ms": summary["p99_ms"],
        "mean_ms": summary["mean_ms"],
        "max_ms": summary["max_ms"],
        "error_count": result.error_count,
        "error_rate": summary["error_rate"],
        "lock_wait_connections": result.lock_wait_connections,
        "deadlock_delta": result.deadlock_delta,
        "status_counts": dict(result.status_counts),
        "error_samples": list(result.error_samples),
    }


def parse_concurrency(raw: str) -> tuple[int, ...]:
    """解析并发档位：去重、升序、拒绝非正数与超限值。"""
    values: set[int] = set()
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        if not text.isdigit() or int(text) <= 0:
            raise ValueError(f"并发档位必须是正整数：{item!r}")
        values.add(int(text))
    if not values:
        raise ValueError("至少需要一个并发档位")
    if max(values) > MAX_CONCURRENCY:
        raise ValueError(f"并发档位必须在 1..{MAX_CONCURRENCY} 之间（规模探测红线）")
    return tuple(sorted(values))


def parse_iterations(raw: int) -> int:
    """校验迭代数：正数且不超红线。"""
    if raw <= 0:
        raise ValueError(f"迭代数必须是正整数：{raw}")
    if raw > MAX_ITERATIONS:
        raise ValueError(f"迭代数不得超过 {MAX_ITERATIONS}：{raw}")
    return raw


def compute_deadlock_delta(before: int, after: int) -> int:
    """死锁计数差值：压测新增死锁数。负值视为 0（计数器不应回退）。"""
    return max(after - before, 0)


def classify_lock_waits(by_wait_event_type: Mapping[str, int]) -> dict[str, int]:
    """把 ``pg_stat_activity`` 的 wait_event_type 分布拆成锁等待与其余。

    ``Lock`` 是唯一直接表示行/表锁等待的 wait_event_type；其余（含 CPU、
    客户端网络等待）归入 ``other``，避免把「没在等锁」算进锁风险。
    """
    lock = sum(count for key, count in by_wait_event_type.items() if key == "Lock")
    other = sum(count for key, count in by_wait_event_type.items() if key != "Lock")
    return {"lock_wait_connections": lock, "other_wait_connections": other}


def make_temp_db_name(stamp: str) -> str:
    """生成一次性临时库名（带 `ddp_perf_` 前缀，经安全校验函数确认）。"""
    return ensure_safe_perf_db_name(f"{TEMP_DB_PREFIX}{stamp}_{uuid.uuid4().hex[:6]}")


def make_run_payload(
    project_id: uuid.UUID, run_index: int, *, pipeline_version: str
) -> dict[str, object]:
    """构造一次 processing run 创建的请求体。

    ``input_refs`` 每次迭代都给一个全新的引用：合成规模下每次创建都是
    独立 run，避免任何潜在去重/唯一键把并发创建挡成错误。
    """
    return {
        "pipeline_version": pipeline_version,
        "input_refs": [f"raw/{project_id}/scale-run-{run_index}"],
    }


# ---------------------------------------------------------------------------
# 执行环：临时库、造数、并发压测
# ---------------------------------------------------------------------------


def _create_temp_db(base_dsn: str) -> tuple[str, str]:
    """创建一次性压测库并跑迁移；返回 (库名, 该库的 DSN)。

    模式与 ``write_path_e2e._create_temp_db`` 完全一致：subprocess 调
    alembic，env 带 ``DDP_DATABASE_URL`` 指向临时库，``PYTHONPATH`` 含
    ``.:../data-platform``。迁移只作用于临时库。
    """
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    name = make_temp_db_name(stamp)

    from sqlalchemy import create_engine, text

    admin = create_engine(base_dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            # 库名标识符不能 bind；name 已由 ensure_safe_perf_db_name 校验前缀/长度/字符集
            # nosemgrep: ddp-no-raw-sql-fstring —— 标识符来自校验函数，非请求值
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()

    temp_dsn = with_database(base_dsn, name)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={
            **os.environ,
            "DDP_DATABASE_URL": temp_dsn,
            "PYTHONPATH": ".:../data-platform",
        },
        check=True,
        capture_output=True,
    )
    return name, temp_dsn


def _drop_temp_db(base_dsn: str, name: str) -> None:
    """删除一次性压测库。"""
    from sqlalchemy import create_engine, text

    admin = create_engine(base_dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            # 同上：name 来自 ensure_safe_perf_db_name 校验后的临时库名。
            # nosemgrep: ddp-no-raw-sql-fstring —— 标识符来自校验函数，非请求值
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


def _pg_side_observability(dsn: str) -> dict[str, Any]:
    """读 PG 侧的锁与死锁观测值（死锁/锁等待证据的输入）。

    - ``deadlocks``：``pg_stat_database.deadlocks`` 累计值（本库）；
    - ``lock_wait_connections``：当前处于 ``Lock`` 等待的连接数；
    - ``by_wait_event_type``：wait_event_type 分布快照。
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            deadlocks = conn.execute(
                text(
                    "SELECT deadlocks FROM pg_stat_database"
                    " WHERE datname = current_database()"
                )
            ).scalar_one()
            lock_waiting = conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database()"
                    " AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            waits = conn.execute(
                text(
                    "SELECT coalesce(wait_event_type, 'CPU') AS t, count(*)"
                    " FROM pg_stat_activity WHERE datname = current_database()"
                    " GROUP BY 1"
                )
            ).all()
    finally:
        engine.dispose()
    return {
        "deadlocks": int(deadlocks),
        "lock_wait_connections": int(lock_waiting),
        "by_wait_event_type": {row[0]: int(row[1]) for row in waits},
    }


class _BuildAppMixin:
    """构造真实 FastAPI 应用的环境口径（与写路径压测一致，单独成类便于复用）。"""

    @staticmethod
    def build(temp_dsn: str, jwt_secret: str) -> Any:
        """构造真实 FastAPI 应用，指向临时库。

        - ``DDP_DATABASE_URL`` 指向临时库，**不是**主库；
        - ``pool_size`` / ``max_overflow`` 用**生产默认口径**（10 / 20），
          因为连接池红线结论要基于真实配置，而不是为压测临时放宽；
        - 关闭限流：否则写请求会被 IP 桶按分钟挡掉，测到的就不是业务写容量。
        """
        os.environ["DDP_ENV"] = "local"
        os.environ["DDP_DATABASE_URL"] = temp_dsn
        os.environ["DDP_DATABASE_POOL_SIZE"] = str(PRODUCTION_POOL_SIZE)
        os.environ["DDP_DATABASE_MAX_OVERFLOW"] = str(PRODUCTION_MAX_OVERFLOW)
        os.environ["DDP_JWT_SECRET"] = jwt_secret
        os.environ["DDP_JWT_ISSUER"] = "ddp"
        os.environ["DDP_JWT_AUDIENCE"] = "ddp-api"
        os.environ["DDP_RATE_LIMIT_ENABLED"] = "false"
        os.environ["DDP_LOCAL_HEADER_AUTH"] = "false"

        from app.core.config import get_settings
        from app.main import create_app

        get_settings.cache_clear()
        return create_app()


@dataclass(frozen=True, slots=True)
class Identity:
    """一个身份 + 它的 Bearer 令牌。"""

    user_id: uuid.UUID
    token: str


def _make_token(jwt_secret: str, user_id: uuid.UUID) -> str:
    """按 API 同一口径签发 HS256 令牌（复用 ``tests/jwt_support.make_token``）。"""
    from tests.jwt_support import make_token

    return make_token(
        secret=jwt_secret,
        issuer="ddp",
        audience="ddp-api",
        subject=user_id,
        platform_role="member",
        expires_in=3600,
    )


def _make_client(app: Any, identity: Any) -> Any:
    """构造绑定指定 Bearer 令牌的 ``TestClient``。

    **不替换** ``get_current_principal``：请求带真实 ``Authorization`` 头，
    让 ``app.api.deps.get_current_principal`` 走完整的 JWT 校验链。鉴权链
    本身就是被测路径的一部分，替换掉等于没测。
    """
    from fastapi.testclient import TestClient

    client = TestClient(app, headers={"Authorization": f"Bearer {identity.token}"})
    client.__enter__()
    return client


@dataclass(frozen=True, slots=True)
class SeededProject:
    """造好的前置数据：项目与一个可写（editor）身份。"""

    project_id: uuid.UUID
    editor_id: uuid.UUID


def _seed_project(temp_dsn: str) -> SeededProject:
    """造身份基线：复用 ``tests/api/conftest.seed_identity``。

    processing run 创建只要求「项目成员 + 写权限」：``seed_identity``
    造出的 ``project_a`` + ``editor_a``（EDITOR 角色）正好覆盖，不另写
    一套造数逻辑。
    """
    from sqlalchemy import create_engine

    from app.infrastructure.db.session import create_session_factory
    from tests.api.conftest import seed_identity

    engine = create_engine(temp_dsn)
    try:
        factory = create_session_factory(engine)
        ids = seed_identity(factory)
        return SeededProject(project_id=ids.project_a, editor_id=ids.editor_a)
    finally:
        engine.dispose()


def _run_tier(
    app: Any,
    project_id: uuid.UUID,
    identity: Any,
    *,
    workers: int,
    iterations: int,
    pipeline_version: str,
) -> TierResult:
    """跑一档并发，返回样本、状态码分布与锁观测。

    每次迭代 = 一次真实创建请求（``POST /processing-runs``，201）。
    **只统计这一个被测端点**的耗时；身份与项目是压测前造好的前置数据。

    每个 worker 用自己的 ``TestClient``（httpx 客户端在多线程下线程安全，
    且进程内 ASGI 传输没有真实网络连接），共享同一个 editor 身份——
    processing run 创建是项目级写操作，不存在「只能本人操作」的约束。
    """
    from fastapi.testclient import TestClient

    clients = [
        TestClient(app, headers={"Authorization": f"Bearer {identity.token}"})
        for _ in range(workers)
    ]
    for client in clients:
        client.__enter__()
    samples: list[RunSample] = []
    errors: list[str] = []
    lock = threading.Lock()
    base_url = f"/api/v1/projects/{project_id}/processing-runs"
    run_index = 0
    worker_ids = list(range(workers))

    def _worker(client_index: int) -> None:
        nonlocal run_index
        client = clients[client_index]
        for _ in range(iterations):
            with lock:
                index = run_index
                run_index += 1
            payload = make_run_payload(
                project_id, index, pipeline_version=pipeline_version
            )
            start = time.perf_counter()
            try:
                # 不传 timeout：Starlette 的 TestClient 不支持该参数（进程内
                # ASGI 调用没有网络往返），传了只会触发弃用告警。
                response = client.post(base_url, json=payload)
            except Exception as exc:  # noqa: BLE001 - 压测需要记录任何失败
                elapsed = (time.perf_counter() - start) * 1000.0
                with lock:
                    samples.append(
                        RunSample(elapsed, None, f"{type(exc).__name__}: {exc}")
                    )
                    errors.append(f"{type(exc).__name__}: {exc}")
                continue
            elapsed = (time.perf_counter() - start) * 1000.0
            if response.status_code >= 400:
                sample = RunSample(
                    elapsed, response.status_code, f"HTTP {response.status_code}"
                )
                with lock:
                    samples.append(sample)
                    errors.append(sample.error or "")
            else:
                with lock:
                    samples.append(RunSample(elapsed, response.status_code, None))

    wall_start = time.perf_counter()
    threads = [
        threading.Thread(target=_worker, args=(index,), daemon=True)
        for index in worker_ids
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = time.perf_counter() - wall_start
    for client in clients:
        client.__exit__(None, None, None)

    status_counts: dict[str, int] = {}
    for sample in samples:
        key = str(sample.status) if sample.status is not None else "network_error"
        status_counts[key] = status_counts.get(key, 0) + 1
    return TierResult(
        workers=workers,
        iterations=iterations,
        wall_seconds=wall,
        samples=tuple(samples),
        status_counts=status_counts,
        error_samples=tuple(errors[:5]),
    )


def _observe_locks(temp_dsn: str, before: dict[str, Any]) -> dict[str, Any]:
    """压测后观测：死锁差值与档末锁等待连接数。

    返回值是本档末的完整快照，可直接作为下一档的 ``before`` 基线，
    避免重复查询。
    """
    after = _pg_side_observability(temp_dsn)
    return {
        "deadlocks": after["deadlocks"],
        "lock_wait_connections": after["lock_wait_connections"],
        "by_wait_event_type": after["by_wait_event_type"],
        "deadlock_delta": compute_deadlock_delta(
            int(before["deadlocks"]), int(after["deadlocks"])
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="处理管线并发规模探测（多 Worker 创建 processing run）"
    )
    parser.add_argument(
        "--dsn",
        required=True,
        help="建库连接点，必须指向本机回环的 postgres/ddp_test 库",
    )
    parser.add_argument("--concurrency", default="1,4,8,16", help="并发档位，逗号分隔")
    parser.add_argument("--iterations", type=int, default=25, help="每 worker 迭代数")
    parser.add_argument(
        "--execute", action="store_true", help="真正执行（默认 dry-run）"
    )
    parser.add_argument("--keep-db", action="store_true", help="保留临时库供排查")
    parser.add_argument("--out", default="", help="结果 JSON 输出路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        base = ensure_safe_base_dsn(args.dsn)
        concurrency_list = parse_concurrency(args.concurrency)
        iterations = parse_iterations(args.iterations)
    except ValueError as exc:
        print(f"拒绝执行：{exc}", file=sys.stderr)
        return 2

    total_runs = sum(workers * iterations for workers in concurrency_list)
    plan = {
        "base": f"{base.host}:{base.port}/{base.database}",
        "base_dsn_masked": mask_dsn(args.dsn),
        "concurrency": list(concurrency_list),
        "iterations_per_worker": iterations,
        "total_runs": total_runs,
        "temp_db_prefix": TEMP_DB_PREFIX,
        "endpoint": "POST /api/v1/projects/{project_id}/processing-runs (201, CREATED)",
        "pipeline_version": DEFAULT_PIPELINE_VERSION,
        "pool_size": PRODUCTION_POOL_SIZE,
        "max_overflow": PRODUCTION_MAX_OVERFLOW,
        "scale_model": "synthetic",
    }
    print(
        json.dumps(
            {"mode": "execute" if args.execute else "dry-run", "plan": plan},
            ensure_ascii=False,
            indent=2,
        )
    )
    if not args.execute:
        print(
            "\n[DRY-RUN] 未建库、未造数、未发请求。加 --execute 真实运行。",
            file=sys.stderr,
        )
        return 0

    db_name, temp_dsn = _create_temp_db(args.dsn)
    print(f"\n临时库已建：{db_name}（迁移已跑）", file=sys.stderr)
    try:
        # JWT 密钥只存在于内存：进程内生成、注入 Settings，不落盘、不进报告。
        jwt_secret = uuid.uuid4().hex + uuid.uuid4().hex
        app = _BuildAppMixin.build(temp_dsn, jwt_secret)

        work = _seed_project(temp_dsn)
        identity = Identity(
            user_id=work.editor_id, token=_make_token(jwt_secret, work.editor_id)
        )
        print(
            f"前置数据就绪：project={work.project_id} editor={work.editor_id}",
            file=sys.stderr,
        )

        deadlocks_before = _pg_side_observability(temp_dsn)
        results: list[dict[str, Any]] = []
        peak_connections = 0
        sampler_total = 0
        with _ConnectionSampler(temp_dsn) as sampler:
            for workers in concurrency_list:
                print(f"  并发 {workers} × {iterations} 次创建...", file=sys.stderr)
                tier = _run_tier(
                    app,
                    work.project_id,
                    identity,
                    workers=workers,
                    iterations=iterations,
                    pipeline_version=DEFAULT_PIPELINE_VERSION,
                )
                lock_obs = _observe_locks(temp_dsn, deadlocks_before)
                # lock_obs 已是本档末完整快照，直接作为下一档的基线。
                deadlocks_before = lock_obs
                tier_payload = summarize_tier(
                    TierResult(
                        workers=tier.workers,
                        iterations=tier.iterations,
                        wall_seconds=tier.wall_seconds,
                        samples=tier.samples,
                        status_counts=tier.status_counts,
                        error_samples=tier.error_samples,
                        lock_wait_connections=lock_obs["lock_wait_connections"],
                        deadlock_delta=lock_obs["deadlock_delta"],
                    )
                )
                results.append(tier_payload)
                print(
                    f"    TPS={results[-1]['tps']} p95={results[-1]['p95_ms']}ms "
                    f"错误={results[-1]['error_count']} "
                    f"死锁={results[-1]['deadlock_delta']}",
                    file=sys.stderr,
                )
            peak_connections = sampler.peak
            sampler_total = sampler.samples

        pg_stats = _pg_connection_stats(temp_dsn)
        other_connections = max(
            int(pg_stats["connections_on_instance"]) - peak_connections,
            0,
        )
        conclusion = pool_redline_conclusion(
            pool_size=PRODUCTION_POOL_SIZE,
            max_overflow=PRODUCTION_MAX_OVERFLOW,
            max_connections=int(pg_stats["max_connections"]),
            peak_api_connections=peak_connections,
            peak_concurrency=max(concurrency_list),
            other_connections=other_connections,
        )
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "execute",
            "plan": plan,
            "results": results,
            "pg": {
                **pg_stats,
                "peak_connections_during_run": peak_connections,
                "connection_samples": sampler_total,
                "sample_interval_seconds": CONNECTION_SAMPLE_INTERVAL_SECONDS,
            },
            "lock_observability": {
                "final_deadlocks": deadlocks_before["deadlocks"],
                "final_lock_wait_connections": deadlocks_before[
                    "lock_wait_connections"
                ],
                "by_wait_event_type": deadlocks_before["by_wait_event_type"],
            },
            "connection_pool_redline": conclusion,
            "notes": [
                "每次迭代 = 一次真实创建请求（POST /processing-runs，201，CREATED），走完整鉴权/项目成员校验/审计/outbox",
                "**合成规模**：全量 200 demos 样本数据不存在，本探测用合成规模（每次迭代一个全新 input_refs 的 run）做容量模型，是 T2-01 全量样本基准的最小化版本，不代表全量基准",
                "连接池参数取生产默认口径（pool_size=10 / max_overflow=20），压测期间未放宽",
                "限流在本脚本内关闭：否则写请求会被 IP 桶挡掉，测到的不是业务写容量",
                "连接峰值是 50ms 间隔的瞬时采样最大值，尖峰可能被漏掉",
                "死锁证据：pg_stat_database.deadlocks 前后差值（累计计数，不依赖采样）；锁等待证据：pg_stat_activity wait_event_type='Lock' 的档末快照",
                "Worker 异步执行链（八阶段）不在本次压测范围：创建请求落库后即返回",
                "临时库已删除，数据不落主库"
                if not args.keep_db
                else "临时库按 --keep-db 保留",
            ],
        }
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"结果写入 {out_path}", file=sys.stderr)
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
