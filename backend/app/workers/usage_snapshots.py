"""用量快照 Worker（N9 规模化运营）。

职责：定时把"每个项目的核心资源用量"聚合成 ``usage_snapshots``，供
``GET /projects/{id}/usage-report`` 读取。

为什么必须是独立 Worker 而不是 HTTP 里现算
------------------------------------------
对象存储字节数是 ``SUM(raw_assets.size_bytes)``，数据量增长后不是一次索引
前缀查找就能返回；把它塞进报表请求会让"打开经营报表"随数据量线性变慢。
这里离线算好，HTTP 只读一行/几行快照。

运行方式::

    python -m app.workers.usage_snapshots --once
    python -m app.workers.usage_snapshots --interval-seconds 300

失败处理：单次刷新失败时记录 ``exception`` 日志并保留旧快照（事务回滚），
下一轮重试；**不写空表、不写 0**。这与 ``maintenance.py`` 的循环策略一致。
"""

from __future__ import annotations

import argparse
import logging
import threading
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.application.governance import SnapshotUseCases
from app.core.logging import configure_logging
from app.infrastructure.db.session import create_session_factory, get_engine
from app.infrastructure.repositories.governance import (
    SqlUsageReader,
    SqlUsageSnapshotRepository,
)

logger = logging.getLogger("ddp.worker.usage_snapshots")


class UsageSnapshotWorker:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def refresh_once(self, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        with self._factory() as session:
            use_cases = SnapshotUseCases(
                usage=SqlUsageReader(session),
                snapshots=SqlUsageSnapshotRepository(session),
                uow=session,
            )
            count = use_cases.refresh(now=moment)
        logger.info("用量快照刷新完成", extra={"snapshot_rows": count})
        return count

    def run(self, *, stop_event: threading.Event, interval_seconds: float) -> None:
        while not stop_event.is_set():
            try:
                self.refresh_once()
            except Exception:  # noqa: BLE001 —— 单次失败不应终止整个循环
                logger.exception("用量快照刷新失败，保留上一版快照")
            stop_event.wait(interval_seconds)


def _install_signal_handlers(stop_event: threading.Event) -> None:
    import signal

    def _stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DDP 用量快照 Worker")
    parser.add_argument("--once", action="store_true", help="刷新一次后退出")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=300.0,
        help="循环刷新间隔（秒），默认 300",
    )
    args = parser.parse_args(argv)
    if args.interval_seconds <= 0:
        parser.error("--interval-seconds 必须大于 0")

    configure_logging()
    worker = UsageSnapshotWorker(create_session_factory(get_engine()))
    if args.once:
        worker.refresh_once()
        return 0

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    worker.run(stop_event=stop_event, interval_seconds=args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
