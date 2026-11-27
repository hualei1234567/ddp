#!/usr/bin/env python3
"""写路径端到端压测（审计 R1 剩余盲区：真实业务写路径从未压测过）。

## 这个脚本测什么、不测什么

**测**：真实业务写路径（标注提交）的端到端 HTTP 写容量——完整走
``POST /api/v1/projects/{project_id}/annotation-submissions``（建草稿）与
``POST .../annotation-submissions/{id}/actions/submit``（提交）两条真实端点，
经过真实的 JWT 鉴权、项目成员校验、Schema 快照校验、标签校验、乐观锁、
审计 + outbox 双写、质检编排与 Episode 状态投影。

**不测**：
- 不测事务骨架本身（那由 ``write_path_probe.py`` 负责，本脚本不重复）；
- 不测视频 Range / 列表分页（那是 ``baseline.py`` / ``readonly_load.py`` 的职责）；
- 不测跨进程横向扩容能力（本脚本是单进程内并发，结论只对单实例成立）。

## 与既有资产的关系（复用优先，不另起平行体系）

| 资产 | 复用点 |
|---|---|
| ``scripts/perf/core.py`` | ``ensure_safe_base_dsn`` / ``ensure_safe_perf_db_name`` / ``with_database`` / ``summarize`` / ``mask_dsn`` |
| ``scripts/perf/write_path_probe.py`` | ``_create_temp_db`` / ``_drop_temp_db`` 的一次性临时库 + Alembic 模式（照抄同一模式） |
| ``backend/tests/api/annotation_support.py`` | ``create_published_schema`` / ``create_snapshot_set`` / ``seed_episode``（Schema 发布、样本集快照、合法 Episode 种子） |
| ``backend/tests/api/conftest.py`` | 身份基线 ``seed_identity``（project + user + 成员关系） |
| ``backend/tests/jwt_support.py`` | 测试用 JWT 构造（HS256，真实签名校验路径，不是假身份头） |

**身份口径**：本脚本走**真实 Bearer JWT**（``app.infrastructure.auth.jwt``
校验签名/iss/aud/exp），而不是 ``X-User-Id`` 后门——后者只在 ``local`` 环境 +
显式开关 + loopback 三者同时成立时才允许，本脚本不依赖那个后门，鉴权链本身
才是被测对象的一部分。签名密钥由脚本在内存中生成并注入 ``Settings``，
**不落盘、不进命令行历史、不进报告**。

## 「每次迭代都要一个新分配」这件事的真实约束

领域规则不允许在同一分配上反复建草稿：

1. ``uq_annotation_assignments_batch_assignee_role``：同批次 + 同承接人 + 同角色
   只能有一个分配；
2. 分配提交后进入 ``SUBMITTED``，而「建草稿」只允许 ``IN_PROGRESS``
   （``submissions.create_draft`` 的领域校验）；
3. 分配容量上限（``annotation_max_open_items`` 默认 50）按承接人累计，
   ``SUBMITTED`` 仍算「在办」。

因此本脚本在**压测前**先把「工作位」（batch + 已领取并开始的 assignment）
全部造好（每个工作位恰好够一次「建草稿 + 提交」），压测循环里只发这两个
被测端点——这样测到的延迟与吞吐就是业务写路径本身，而不是混着造数开销。

工作位按承接人分摊以避开容量上限；工作位按任务分组以限制「进度重算」的
扫描范围（每个提交的落库都会按任务重算一次进度）。

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
backend/.venv/bin/python scripts/perf/write_path_e2e.py \\
    --dsn postgresql+psycopg://ddp:ddp@localhost:55432/postgres

# 真实执行
backend/.venv/bin/python scripts/perf/write_path_e2e.py \\
    --dsn postgresql+psycopg://ddp:ddp@localhost:55432/postgres \\
    --workers 1,4,16 --iterations 10 --execute \\
    --out docs/testing/evidence/2026-11-27-write-path-e2e/result.json
```
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"
DATA_PLATFORM_DIR = REPO_ROOT / "data-platform"
# 造数复用 app 包的真实 ORM / 领域模型，backend 与 data-platform 必须在导入路径上。
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

#: 临时库名前缀：必须落在 `ensure_safe_perf_db_name` 允许的 `ddp_perf_` 前缀内。
TEMP_DB_PREFIX = "ddp_perf_e2e_"

#: 并发档位红线：单进程内并发超过这个数会让「连接池是否够用」的结论失真
#: （瓶颈从 DB 变成 Python GIL / 线程调度），临时库连接数也会无谓膨胀。
MAX_CONCURRENCY = 32

#: 每档迭代数红线：单档请求数上限，避免误填巨大数字把本机跑挂。
MAX_ITERATIONS = 2000

#: 单次请求超时（秒）：超过就记为错误而不是无限等待。
REQUEST_TIMEOUT_SECONDS = 30.0

#: 生产默认连接池口径（``app/core/config.py`` 的 Settings 默认值）。
#: 压测**不放宽**它——连接池红线结论必须基于真实配置。
PRODUCTION_POOL_SIZE = 10
PRODUCTION_MAX_OVERFLOW = 20

#: 单个承接人可同时持有的分配上限（``annotation_max_open_items`` 默认值）。
#: 工作位分摊的除数，必须与业务规则一致，否则造数会被容量闸口挡下。
ANNOTATOR_OPEN_ITEM_LIMIT = 50

#: PG 连接数采样间隔（秒）：峰值是瞬时采样，间隔越密越准，代价是采样开销。
CONNECTION_SAMPLE_INTERVAL_SECONDS = 0.05

#: 工作位分摊到多少个任务上：任务越少，单任务的提交越多，「进度重算」的
#: 扫描成本越集中（每个提交落库都会按任务重算一次进度）。
DEFAULT_TASK_GROUPS = 4


# ---------------------------------------------------------------------------
# 纯函数层：结果汇总与连接池红线判定
#
# 放在脚本顶部且不依赖 FastAPI/SQLAlchemy，便于直接单测口径。
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RequestSample:
    """一次 HTTP 写请求的结果。"""

    elapsed_ms: float
    status: int | None
    error: str | None


@dataclass(frozen=True, slots=True)
class TierResult:
    """一档并发下的汇总。"""

    workers: int
    iterations: int
    wall_seconds: float
    samples: tuple[RequestSample, ...]
    status_counts: dict[str, int]
    error_samples: tuple[str, ...]
    setup_failures: int = 0

    @property
    def error_count(self) -> int:
        return sum(1 for sample in self.samples if sample.error is not None)


def summarize_tier(result: TierResult) -> dict[str, Any]:
    """汇总一档并发：TPS、p50/p95/p99/max、错误数与错误样本。

    口径与 ``perf.core.summarize`` 完全一致（最近秩百分位），这里额外保留
    状态码分布与错误样本，供报告直接引用。
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
        "setup_failures": result.setup_failures,
        "status_counts": dict(result.status_counts),
        "error_samples": list(result.error_samples),
    }


def pool_redline_conclusion(
    *,
    pool_size: int,
    max_overflow: int,
    max_connections: int,
    peak_api_connections: int,
    peak_concurrency: int,
    other_connections: int,
) -> dict[str, Any]:
    """判定「API 侧连接池是否会打满 PG ``max_connections``」。

    口径说明（刻意保守、逐项列明，不给一个笼统的"够/不够"）：

    - API 侧单进程理论连接上限 = ``pool_size + max_overflow``；
    - 实测并发峰值对应的**实际**连接占用 = 实测峰值（实测值优先）；
    - ``max_connections`` 是 **PG 实例级**上限，因此同实例上其他库的其他连接
      （worker、运维连接、监控采集）也必须计入。``other_connections`` 显式
      参与计算——把它当 0 会得出「刚好够用」这种危险的乐观结论；
    - 结论分三档：``saturated``（会打满）/ ``tight``（余量不足 20%）/
      ``safe``（余量充足）。
    """
    api_connection_cap = pool_size + max_overflow
    effective_peak = max(peak_api_connections, 0)
    others = max(other_connections, 0)
    projected = effective_peak + others
    remaining = max_connections - projected
    usage_ratio = round(projected / max_connections, 4) if max_connections > 0 else None
    if remaining < 0:
        verdict = "saturated"
        statement = (
            f"会打满：API 侧连接上限 {api_connection_cap}"
            f"（pool_size={pool_size} + max_overflow={max_overflow}），"
            f"实测峰值 {effective_peak}，加上同实例其他连接 {others} "
            f"后为 {projected}，已超过 PG max_connections={max_connections}"
        )
    elif usage_ratio is not None and usage_ratio >= 0.8:
        verdict = "tight"
        statement = (
            f"余量不足：占用率 {usage_ratio:.1%}（{projected}/{max_connections}），"
            f"仅剩 {remaining} 个连接；并发再高一点就会触碰 PG 拒绝新连接的边界"
        )
    else:
        verdict = "safe"
        statement = (
            f"不会打满：占用率 {usage_ratio:.1%}（{projected}/{max_connections}），"
            f"余量 {remaining} 个连接；API 侧单进程连接上限为 {api_connection_cap}"
        )
    return {
        "verdict": verdict,
        "statement": statement,
        "api_pool_size": pool_size,
        "api_max_overflow": max_overflow,
        "api_connection_cap": api_connection_cap,
        "measured_peak_api_connections": effective_peak,
        "measured_peak_concurrency": peak_concurrency,
        "other_connections": others,
        "projected_total_connections": projected,
        "pg_max_connections": max_connections,
        "remaining_connections": remaining,
        "usage_ratio": usage_ratio,
    }


def parse_workers(raw: str) -> tuple[int, ...]:
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
        raise ValueError(f"并发档位必须在 1..{MAX_CONCURRENCY} 之间（写路径压测红线）")
    return tuple(sorted(values))


def parse_iterations(raw: int) -> int:
    """校验迭代数：正数且不超红线。"""
    if raw <= 0:
        raise ValueError(f"迭代数必须是正整数：{raw}")
    if raw > MAX_ITERATIONS:
        raise ValueError(f"迭代数不得超过 {MAX_ITERATIONS}：{raw}")
    return raw


def plan_annotator_count(total_slots: int) -> int:
    """承接人数量：让每个承接人的在办分配不超过容量上限。

    每个工作位的分配都算 1 个在办项（``planned_item_count=1``），因此
    ``ceil(total_slots / 50)`` 个承接人即可全部放下。
    """
    if total_slots <= 0:
        raise ValueError("工作位数量必须为正整数")
    return max(1, math.ceil(total_slots / ANNOTATOR_OPEN_ITEM_LIMIT))


def make_temp_db_name(stamp: str) -> str:
    """生成一次性临时库名（带 `ddp_perf_` 前缀，经安全校验函数确认）。"""
    return ensure_safe_perf_db_name(f"{TEMP_DB_PREFIX}{stamp}_{uuid.uuid4().hex[:6]}")


# ---------------------------------------------------------------------------
# 执行环：临时库、造数、并发压测
# ---------------------------------------------------------------------------


def _create_temp_db(base_dsn: str) -> tuple[str, str]:
    """创建一次性压测库并跑迁移；返回 (库名, 该库的 DSN)。

    模式与 ``write_path_probe.py`` 的 ``_create_temp_db`` 完全一致：subprocess
    调 alembic，env 带 ``DDP_DATABASE_URL`` 指向临时库，``PYTHONPATH`` 含
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


def _pg_connection_stats(dsn: str) -> dict[str, Any]:
    """读 PG 侧的连接与等待统计（连接池红线结论的输入）。

    口径：
    - ``max_connections``：PG **实例级**上限；
    - ``connections_on_instance``：实例上**全部**库的连接数（含其他 worker）；
    - ``connections_on_database``：当前临时库上的连接数；
    - ``by_wait_event_type``：按 ``wait_event_type`` 分布（NULL 归入 CPU，即
      「没在等任何事件，正在跑 SQL」）。
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            max_conn = conn.execute(text("SHOW max_connections")).scalar_one()
            instance = conn.execute(
                text("SELECT count(*) FROM pg_stat_activity")
            ).scalar_one()
            on_db = conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                )
            ).scalar_one()
            waits = conn.execute(
                text(
                    "SELECT coalesce(wait_event_type, 'CPU') AS t, count(*)"
                    " FROM pg_stat_activity WHERE datname = current_database() GROUP BY 1"
                )
            ).all()
            states = conn.execute(
                text(
                    "SELECT coalesce(state, 'unknown') AS s, count(*)"
                    " FROM pg_stat_activity WHERE datname = current_database() GROUP BY 1"
                )
            ).all()
    finally:
        engine.dispose()
    return {
        "max_connections": int(max_conn),
        "connections_on_instance": int(instance),
        "connections_on_database": int(on_db),
        "by_wait_event_type": {row[0]: int(row[1]) for row in waits},
        "by_state": {row[0]: int(row[1]) for row in states},
    }


class _ConnectionSampler:
    """压测期间轮询 PG 连接数，记录峰值。

    峰值是**瞬时采样**的最大值，不是精确的最大值——采样间隔内发生的尖峰
    可能被漏掉。因此报告里同时给出采样间隔与采样次数，让读者知道这个
    数字的精度边界。

    采样用一个**独立的长连接引擎**（``pool_size=1``）：每次轮询新建引擎会
    额外制造连接，既污染峰值读数也给被测路径加噪。
    """

    def __init__(
        self,
        dsn: str,
        *,
        interval_seconds: float = CONNECTION_SAMPLE_INTERVAL_SECONDS,
    ) -> None:
        from sqlalchemy import create_engine, text

        self._interval = interval_seconds
        self._engine = create_engine(dsn, pool_size=1, max_overflow=0)
        self._statement = text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.peak = 0
        self.samples = 0

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with self._engine.connect() as conn:
                    count = int(conn.execute(self._statement).scalar_one())
            except Exception:  # noqa: BLE001 - 采样失败不能中断压测
                count = 0
            with self._lock:
                self.samples += 1
                if count > self.peak:
                    self.peak = count
            self._stop.wait(self._interval)

    def __enter__(self) -> _ConnectionSampler:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._engine.dispose()


# ---------------------------------------------------------------------------
# 前置数据与真实 HTTP 压测
# ---------------------------------------------------------------------------


@dataclass
class SeededWork:
    """造好的前置数据：项目身份、任务、批次与工作位。"""

    project_id: uuid.UUID
    editor_id: uuid.UUID
    owner_id: uuid.UUID
    task_ids: list[str] = field(default_factory=list)
    episode_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class Slot:
    """一个「工作位」：一个已领取并开始的分配，恰好够一次「建草稿 + 提交」。

    ``annotator_index`` 指向该工作位的承接人（领取/开始/提交都是
    assignee-only 动作，必须由承接人自己发起）。
    """

    assignment_id: str
    episode_id: uuid.UUID
    annotator_index: int


@dataclass
class Identity:
    """一个身份 + 它的 Bearer 令牌。"""

    user_id: uuid.UUID
    token: str


def _build_app(temp_dsn: str, jwt_secret: str) -> Any:
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


def _make_client(app: Any, identity: Identity) -> Any:
    """构造绑定指定 Bearer 令牌的 ``TestClient``。

    **不替换** ``get_current_principal``：请求带真实 ``Authorization`` 头，让
    ``app.api.deps.get_current_principal`` 走完整的 JWT 校验链。鉴权链本身
    就是被测路径的一部分，替换掉等于没测。

    也**不**替换 ``get_session``：数据库必须真连临时库，否则压的就不是 PG。
    """
    from fastapi.testclient import TestClient

    client = TestClient(app, headers={"Authorization": f"Bearer {identity.token}"})
    client.__enter__()
    return client


def _seed_identity_and_episodes(temp_dsn: str) -> SeededWork:
    """造身份与 Episode：project / user / 成员关系 / 可标注 Episode。

    复用 ``backend/tests/api/conftest.py`` 的 ``seed_identity``（身份基线）与
    ``backend/tests/api/annotation_support.py`` 的 ``seed_episode``（合法
    Episode 种子）——不另写一份造数逻辑。

    Episode 必须同时满足 ``status=ACCEPTED`` 与 ``quality_status=PASS`` 才能
    被样本集选中（``sample_sets._ELIGIBLE_STATUSES`` / ``_ELIGIBLE_QUALITY``），
    所以在 ``seed_episode`` 的合法种子基础上按真实门禁补齐 quality 状态。
    """
    from sqlalchemy import create_engine, update

    from app.infrastructure.db.models.episodes import EpisodeModel
    from app.infrastructure.db.session import create_session_factory
    from tests.api.annotation_support import seed_episode
    from tests.api.conftest import seed_identity

    engine = create_engine(temp_dsn)
    try:
        factory = create_session_factory(engine)
        ids = seed_identity(factory)
        episode_ids = [
            seed_episode(factory, ids.project_a, ids.editor_a, f"e2e-ep-{index:04d}")
            for index in range(8)
        ]
        with factory() as session:
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id.in_(episode_ids))
                .values(quality_status="PASS")
            )
            session.commit()
        return SeededWork(
            project_id=ids.project_a,
            editor_id=ids.editor_a,
            owner_id=ids.owner_a,
            episode_ids=episode_ids,
        )
    finally:
        engine.dispose()


def _create_annotators(
    temp_dsn: str, project_id: uuid.UUID, count: int
) -> list[uuid.UUID]:
    """建 ``count`` 个标注员（项目成员）。

    用真实 ORM（``User`` / ``ProjectMember``，与 ``tests/api/conftest.seed_identity``
    同一套模型）写入，不猜表结构。标注员是**普通成员**（``editor`` 角色），
    权限矩阵由 ``ROLE_PERMISSIONS`` 决定，与脚本无关。
    """
    from sqlalchemy import create_engine

    from app.domain.identity.models import ProjectRole
    from app.infrastructure.db.models.identity import ProjectMember, User
    from app.infrastructure.db.session import create_session_factory

    engine = create_engine(temp_dsn)
    try:
        factory = create_session_factory(engine)
        annotators: list[uuid.UUID] = []
        with factory() as session:
            for index in range(count):
                user_id = uuid.uuid4()
                session.add(User(id=user_id, display_name=f"压测标注员{index}"))
                session.flush()
                session.add(
                    ProjectMember(
                        project_id=project_id,
                        user_id=user_id,
                        role=ProjectRole.EDITOR.value,
                    )
                )
                annotators.append(user_id)
            session.commit()
        return annotators
    finally:
        engine.dispose()


def _act(
    client: Any,
    method: str,
    path: str,
    *,
    expected_version: int | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, Any]:
    """打一个带乐观锁的写端点，返回响应体；失败直接抛错（造数阶段不容错）。

    造数阶段任何一步失败都必须**立刻暴露**——带病的前置数据会让压测数字
    完全失真，而失真往往表现为「错误率 0 但 TPS 很低」，很难事后归因。
    """
    headers: dict[str, str] = {}
    if expected_version is not None:
        headers["If-Match"] = f'"{expected_version}"'
    # 不传 timeout：Starlette 的 TestClient 不支持该参数（进程内 ASGI 调用没有
    # 网络往返，超时由调用方自己控制），传了只会触发弃用告警。
    response = client.request(
        method,
        path,
        json=payload if payload is not None else {},
        headers=headers,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"{method} {path} 失败：HTTP {response.status_code} {response.text[:300]}"
        )
    return response.json()


def _fetch(client: Any, path: str) -> dict[str, Any]:
    """读一个端点，返回响应体；失败直接抛错。"""
    response = client.get(path)
    if response.status_code >= 400:
        raise RuntimeError(
            f"GET {path} 失败：HTTP {response.status_code} {response.text[:300]}"
        )
    return response.json()


def _build_slot_pool(
    app: Any,
    work: SeededWork,
    identities: dict[str, Identity],
    annotator_identities: Sequence[Identity],
    *,
    total_slots: int,
    task_groups: int,
) -> list[Slot]:
    """造出 ``total_slots`` 个工作位（batch + 已领取并开始的 assignment）。

    真实端点、真实状态机推进，顺序严格按领域规则：

    1. 每个任务：样本集快照 → 建任务 → 提交评审 → 负责人批准 → 就绪；
    2. 每个工作位：建批次 → 建分配 → 批次就绪 → 批次分配；
    3. 任务分配 → 任务开始 → 批次开始（要求任务与批次都进入 ``IN_PROGRESS``）；
    4. **领取与开始分配由承接人自己发起**（``claim`` / ``start`` 是
       assignee-only 动作，编辑者代劳会被 403 拒绝），所以这里为每个承接人
       建一个带其 Bearer 令牌的客户端。

    任务批准会**消费**样本集（``SNAPSHOTTED → CONSUMED``），而任务创建要求
    样本集处于 ``SNAPSHOTTED``——所以每个任务必须有自己的一份样本集。
    """
    from tests.api.annotation_support import (
        create_published_schema,
        create_snapshot_set,
    )

    editor = _make_client(app, identities["editor"])
    owner = _make_client(app, identities["owner"])
    annotator_clients = [_make_client(app, item) for item in annotator_identities]
    try:
        schema = create_published_schema(editor, owner, work.project_id, "e2e-schema")
        schema_id = str(schema["schema_id"])
        base_url = f"/api/v1/projects/{work.project_id}"

        task_ids: list[str] = []
        for group in range(task_groups):
            sample_set = create_snapshot_set(
                editor,
                work.project_id,
                f"e2e-sample-set-{group:02d}",
            )
            created = _act(
                editor,
                "POST",
                f"{base_url}/annotation-tasks",
                payload={
                    "code": f"e2e-task-{group:02d}",
                    "name": f"压测任务 {group}",
                    "sample_set_id": str(sample_set["sample_set_id"]),
                    "schema_id": schema_id,
                    # 目标项数 = 该任务的工作位数量；目标 Episode 数不得超过
                    # 样本集入选数（8 条），且不得大于目标项数（领域规则
                    # ``target_item_count >= target_episode_count``）。
                    "target_item_count": max(total_slots, len(work.episode_ids)),
                    "target_episode_count": len(work.episode_ids),
                    "priority": "P1",
                },
            )
            task_id = str(created["task_id"])
            submitted = _act(
                editor,
                "POST",
                f"{base_url}/annotation-tasks/{task_id}/actions/submit-review",
                expected_version=int(created["version"]),
            )
            approved = _act(
                owner,
                "POST",
                f"{base_url}/annotation-tasks/{task_id}/actions/approve",
                expected_version=int(submitted["version"]),
            )
            _act(
                editor,
                "POST",
                f"{base_url}/annotation-tasks/{task_id}/actions/ready",
                expected_version=int(approved["version"]),
            )
            task_ids.append(task_id)

        slots: list[Slot] = []
        per_task = math.ceil(total_slots / task_groups)
        for group, task_id in enumerate(task_ids):
            count = per_task if group < task_groups - 1 else total_slots - len(slots)
            slots.extend(
                _build_task_slots(
                    editor,
                    list(zip(annotator_clients, annotator_identities, strict=True)),
                    work,
                    task_id=task_id,
                    slot_count=count,
                    episode_offset=group,
                )
            )
        return slots
    finally:
        editor.__exit__(None, None, None)
        owner.__exit__(None, None, None)
        for client in annotator_clients:
            client.__exit__(None, None, None)


def _build_task_slots(
    editor: Any,
    annotators: Sequence[tuple[Any, Identity]],
    work: SeededWork,
    *,
    task_id: str,
    slot_count: int,
    episode_offset: int,
) -> list[Slot]:
    """为一个任务造 ``slot_count`` 个工作位。

    ``annotators`` 是 (客户端, 身份) 的配对：领取与开始分配必须由承接人本人
    发起，所以每个工作位都要用它自己的承接人客户端。

    **推进顺序由数据库触发器强制**（``ddp_annotation_assignment_guard`` 与
    ``_BATCH_TASK_LEAD``），不能按直觉排：

    1. 建批次（PLANNED）→ 建分配（ASSIGNED）→ 批次 READY（任务需 READY/ASSIGNED/IN_PROGRESS）；
    2. 任务 ASSIGNED（要求至少一个有效分配——第 1 步已就位）；
    3. 批次 ASSIGNED（任务需 READY/ASSIGNED/IN_PROGRESS）；
    4. 领取分配 CLAIMED（任务与批次都需 ASSIGNED/IN_PROGRESS）；
    5. 任务 IN_PROGRESS（要求有 CLAIMED/IN_PROGRESS 的分配——第 4 步已就位）；
    6. 批次 IN_PROGRESS（任务需 IN_PROGRESS）；
    7. 分配 IN_PROGRESS（任务与批次都需 IN_PROGRESS/REWORK）。
    """
    base_url = f"/api/v1/projects/{work.project_id}"
    slots: list[Slot] = []
    task_assigned = False
    task_started = False
    for index in range(slot_count):
        episode_id = work.episode_ids[(episode_offset + index) % len(work.episode_ids)]
        annotator_index = index % len(annotators)
        annotator_client, annotator_identity = annotators[annotator_index]
        batch = _act(
            editor,
            "POST",
            f"{base_url}/annotation-tasks/{task_id}/batches",
            payload={
                "planned_item_count": 1,
                "item_refs": [str(episode_id)],
            },
        )
        batch_id = str(batch["batch_id"])
        assignment = _act(
            editor,
            "POST",
            f"{base_url}/annotation-batches/{batch_id}/annotation-assignments",
            payload={
                "assignee_id": str(annotator_identity.user_id),
                "planned_item_count": 1,
                "planned_minutes": 1,
            },
        )
        assignment_id = str(assignment["assignment_id"])
        batch = _act(
            editor,
            "POST",
            f"{base_url}/annotation-batches/{batch_id}/actions/ready",
            expected_version=int(batch["version"]),
        )
        if not task_assigned:
            task = _fetch(editor, f"{base_url}/annotation-tasks/{task_id}")
            _act(
                editor,
                "POST",
                f"{base_url}/annotation-tasks/{task_id}/actions/assign",
                expected_version=int(task["version"]),
            )
            task_assigned = True
        batch = _act(
            editor,
            "POST",
            f"{base_url}/annotation-batches/{batch_id}/actions/assign",
            expected_version=int(batch["version"]),
        )
        assignment = _act(
            annotator_client,
            "POST",
            f"{base_url}/annotation-assignments/{assignment_id}/actions/claim",
            expected_version=int(assignment["version"]),
        )
        if not task_started:
            task = _fetch(editor, f"{base_url}/annotation-tasks/{task_id}")
            _act(
                editor,
                "POST",
                f"{base_url}/annotation-tasks/{task_id}/actions/start",
                expected_version=int(task["version"]),
            )
            task_started = True
        batch = _act(
            editor,
            "POST",
            f"{base_url}/annotation-batches/{batch_id}/actions/start",
            expected_version=int(batch["version"]),
        )
        _act(
            annotator_client,
            "POST",
            f"{base_url}/annotation-assignments/{assignment_id}/actions/start",
            expected_version=int(assignment["version"]),
        )
        slots.append(
            Slot(
                assignment_id=assignment_id,
                episode_id=episode_id,
                annotator_index=annotator_index,
            )
        )
    return slots


def _submission_payload(episode_id: uuid.UUID, assignment_id: str) -> dict[str, object]:
    """构造一次「建草稿」的请求体（按分配与 Episode 绑定）。

    结构与 ``tests/api/annotation_support.record_payload`` 一致：一个必填
    ENUM 标签 + 一个可选 TIME_RANGE 标签。
    """
    return {
        "assignment_id": assignment_id,
        "records": [
            {
                "episode_id": str(episode_id),
                "episode_version": 1,
                "labels": [
                    {
                        "label_key": "action",
                        "value": "PICK",
                        "source": "HUMAN",
                        "label_status": "CONFIRMED",
                    },
                    {
                        "label_key": "segment",
                        "value": {
                            "start_time_ns": 1_100,
                            "end_time_ns": 1_500,
                            "time_domain": "UTC",
                        },
                        "source": "HUMAN",
                        "label_status": "CONFIRMED",
                    },
                ],
            }
        ],
        "data_source": "REAL",
    }


def _run_tier(
    app: Any,
    project_id: uuid.UUID,
    slots: Sequence[Slot],
    annotator_identities: Sequence[Identity],
    *,
    workers: int,
    iterations: int,
) -> TierResult:
    """跑一档并发，返回样本与状态码分布。

    每次迭代 = 一次真实写事务链：建草稿（POST 201）→ 提交（POST 200）。
    **只统计这两个被测端点**的耗时；工作位是压测前造好的前置数据。

    「建草稿」与「提交」都要求操作者是该分配的承接人
    （``create_draft`` 校验 ``assignment.assignee_id == scope.actor_id``），
    所以每个工作位用它自己承接人的令牌发请求。
    """
    from fastapi.testclient import TestClient

    # 每个承接人一个客户端：客户端持有自己的 Bearer 头，且 httpx.Client
    # 的连接池在多线程下是线程安全的（这里走进程内 ASGI 传输，无真实连接）。
    clients = [
        TestClient(app, headers={"Authorization": f"Bearer {item.token}"})
        for item in annotator_identities
    ]
    for client in clients:
        client.__enter__()
    samples: list[RequestSample] = []
    errors: list[str] = []
    lock = threading.Lock()
    base_url = f"/api/v1/projects/{project_id}"
    pool = list(slots)

    def _timed(
        client: Any, method: str, path: str, **kwargs: Any
    ) -> tuple[RequestSample, Any]:
        start = time.perf_counter()
        try:
            # 不传 timeout：Starlette 的 TestClient 不支持该参数（进程内 ASGI
            # 调用没有网络往返），传了只会触发弃用告警。
            response = client.request(method, path, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 压测需要记录任何失败
            elapsed = (time.perf_counter() - start) * 1000.0
            return RequestSample(elapsed, None, f"{type(exc).__name__}: {exc}"), None
        elapsed = (time.perf_counter() - start) * 1000.0
        if response.status_code >= 400:
            return (
                RequestSample(
                    elapsed,
                    response.status_code,
                    f"HTTP {response.status_code}: {response.text[:300]}",
                ),
                response,
            )
        return RequestSample(elapsed, response.status_code, None), response

    def _worker() -> None:
        for _ in range(iterations):
            with lock:
                if not pool:
                    return
                slot = pool.pop()
            client = clients[slot.annotator_index % len(clients)]
            payload = _submission_payload(slot.episode_id, slot.assignment_id)
            created, created_response = _timed(
                client,
                "POST",
                f"{base_url}/annotation-submissions",
                json=payload,
            )
            with lock:
                samples.append(created)
            if created.error is not None:
                with lock:
                    errors.append(created.error)
                continue
            submission_id = str(created_response.json()["submission_id"])
            submitted, _ = _timed(
                client,
                "POST",
                f"{base_url}/annotation-submissions/{submission_id}/actions/submit",
                json={},
                headers={"If-Match": '"1"'},
            )
            with lock:
                samples.append(submitted)
            if submitted.error is not None:
                with lock:
                    errors.append(submitted.error)

    wall_start = time.perf_counter()
    threads = [threading.Thread(target=_worker, daemon=True) for _ in range(workers)]
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="写路径端到端压测（真实 HTTP 标注提交）"
    )
    parser.add_argument(
        "--dsn",
        required=True,
        help="建库连接点，必须指向本机回环的 postgres/ddp_test 库",
    )
    parser.add_argument("--workers", default="1,4,16", help="并发档位，逗号分隔")
    parser.add_argument("--iterations", type=int, default=10, help="每 worker 迭代数")
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
        workers_list = parse_workers(args.workers)
        iterations = parse_iterations(args.iterations)
    except ValueError as exc:
        print(f"拒绝执行：{exc}", file=sys.stderr)
        return 2

    total_slots = sum(workers * iterations for workers in workers_list)
    annotator_count = plan_annotator_count(total_slots)
    plan = {
        "base": f"{base.host}:{base.port}/{base.database}",
        "base_dsn_masked": mask_dsn(args.dsn),
        "workers": list(workers_list),
        "iterations_per_worker": iterations,
        "total_slots": total_slots,
        "annotator_count": annotator_count,
        "task_groups": DEFAULT_TASK_GROUPS,
        "temp_db_prefix": TEMP_DB_PREFIX,
        "endpoint": "POST /api/v1/projects/{project_id}/annotation-submissions (+ actions/submit)",
        "requests_per_slot": 2,
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
        app = _build_app(temp_dsn, jwt_secret)

        work = _seed_identity_and_episodes(temp_dsn)
        annotator_ids = _create_annotators(temp_dsn, work.project_id, annotator_count)
        identities = {
            "editor": Identity(work.editor_id, _make_token(jwt_secret, work.editor_id)),
            "owner": Identity(work.owner_id, _make_token(jwt_secret, work.owner_id)),
        }
        annotator_identities = [
            Identity(user_id, _make_token(jwt_secret, user_id))
            for user_id in annotator_ids
        ]
        print(f"造 {total_slots} 个工作位中...", file=sys.stderr)
        slots = _build_slot_pool(
            app,
            work,
            identities,
            annotator_identities,
            total_slots=total_slots,
            task_groups=DEFAULT_TASK_GROUPS,
        )
        print(f"工作位就绪：{len(slots)} 个", file=sys.stderr)

        results: list[dict[str, Any]] = []
        peak_connections = 0
        sampler_total = 0
        slot_cursor = 0
        with _ConnectionSampler(temp_dsn) as sampler:
            for workers in workers_list:
                needed = workers * iterations
                tier_slots = slots[slot_cursor : slot_cursor + needed]
                slot_cursor += needed
                print(f"  并发 {workers} × {iterations} 次提交...", file=sys.stderr)
                tier = _run_tier(
                    app,
                    work.project_id,
                    tier_slots,
                    annotator_identities,
                    workers=workers,
                    iterations=iterations,
                )
                results.append(summarize_tier(tier))
                print(
                    f"    TPS={results[-1]['tps']} p95={results[-1]['p95_ms']}ms "
                    f"错误={results[-1]['error_count']}",
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
            peak_concurrency=max(workers_list),
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
            "connection_pool_redline": conclusion,
            "notes": [
                "每次迭代 = 建草稿(201) + 提交(200) 两次真实写请求，走完整鉴权/校验/审计/outbox",
                "工作位（batch + 领取并开始的分配）在压测前造好，领域规则不允许同一分配反复建草稿",
                "连接池参数取生产默认口径（pool_size=10 / max_overflow=20），压测期间未放宽",
                "限流在本脚本内关闭：否则写请求会被 IP 桶挡掉，测到的不是业务写容量",
                "连接峰值是 50ms 间隔的瞬时采样最大值，尖峰可能被漏掉",
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
