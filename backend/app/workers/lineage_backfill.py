"""存量 ``TRAINED_ON`` 血缘回填 Worker CLI（DDP-V21-N5-006）。

运行方式::

    # 默认 dry-run：只扫描并打印每条候选，一个字节都不写
    python -m app.workers.lineage_backfill
    python -m app.workers.lineage_backfill --limit 200 --max-batches 10

    # 显式 --apply 才写边；补一条审计 + 一条留痕事件，事务逐条提交
    python -m app.workers.lineage_backfill --apply --limit 200 --max-batches 50

**为什么不挂进 maintenance worker**：维护 worker 面向"库的卫生"（备份/归档/
清扫），默认关闭且要求 ``pg_dump`` 可用；把回填塞进它的循环会在无人值守时
自动改写血缘，而本任务必须显式执行、且 dry-run 是默认。故单独一个 CLI，复用
同样的会话工厂、配置与日志装配，无需新表、无需迁移。

安全边界：

- 默认 dry-run，``--apply`` 是唯一的写入开关；
- 只补缺失边：候选查询用反连接排除已存在的 ``TRAINED_ON`` 边，写边再经
  ``ensure_edge`` 幂等兜底；不删除、不修改任何既有边；
- 源版本按每条候选自己的 ``ProjectScope`` 重新读取，跨项目脏引用直接跳过；
- 分批（``--limit``）+ 批次数上限（``--max-batches``）+ 墙钟超时
  （``--timeout-seconds``），不在 HTTP 线程执行；
- 失败不吞：单条失败回滚该条、计入 ``failed`` 并让进程以非零码退出。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.application.datasets.lineage import DatasetVersionLineageRecorder
from app.application.datasets.lineage_backfill import (
    DEFAULT_BATCH_LIMIT,
    MAX_BATCH_LIMIT,
    BackfillReport,
    TrainingLineageBackfill,
)
from app.core.logging import configure_logging
from app.domain.datasets.errors import DomainValidationError
from app.infrastructure.db.session import create_session_factory, get_engine
from app.infrastructure.repositories.dataset_audit import (
    SqlDatasetAuditRepository,
    SqlDatasetOutboxRepository,
)
from app.infrastructure.repositories.dataset_repositories import SqlDatasetVersionRepository
from app.infrastructure.repositories.lineage import SqlLineageRepository
from app.infrastructure.repositories.training_lineage_backfill import (
    SqlTrainingLineageBackfillSource,
)

logger = logging.getLogger("ddp.worker.lineage_backfill")

DEFAULT_MAX_BATCHES = 10
DEFAULT_TIMEOUT_SECONDS = 300.0


def build_backfill(session: Session) -> TrainingLineageBackfill:
    """装配补偿用例；写边复用数据集侧的既有 recorder。"""
    return TrainingLineageBackfill(
        candidates=SqlTrainingLineageBackfillSource(session),
        versions=SqlDatasetVersionRepository(session),
        lineage=DatasetVersionLineageRecorder(SqlLineageRepository(session)),
        audit=SqlDatasetAuditRepository(session),
        outbox=SqlDatasetOutboxRepository(session),
        uow=session,
    )


def run_once(
    factory: sessionmaker[Session],
    *,
    apply: bool,
    limit: int = DEFAULT_BATCH_LIMIT,
    max_batches: int = DEFAULT_MAX_BATCHES,
    timeout_seconds: float | None = DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> BackfillReport:
    """在一个会话里跑一轮补偿；dry-run 不提交任何写操作。"""
    with factory() as session:
        report = build_backfill(session).run(
            apply=apply,
            limit=limit,
            max_batches=max_batches,
            timeout_seconds=timeout_seconds,
            now=now,
        )
        if not apply:
            # 只读查询同样会开启事务；显式回滚，确保 dry-run 零写入。
            session.rollback()
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="存量 TRAINED_ON 血缘回填（默认 dry-run）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写边与审计；不传时只扫描并打印候选（dry-run）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_BATCH_LIMIT,
        help=f"每批候选上限（1-{MAX_BATCH_LIMIT}，默认 {DEFAULT_BATCH_LIMIT}）",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=DEFAULT_MAX_BATCHES,
        help=f"本轮最多处理的批次数（默认 {DEFAULT_MAX_BATCHES}）",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"墙钟超时，批次之间检查（默认 {DEFAULT_TIMEOUT_SECONDS:.0f}s）",
    )
    args = parser.parse_args(argv)

    configure_logging()
    factory = create_session_factory(get_engine())
    try:
        report = run_once(
            factory,
            apply=args.apply,
            limit=args.limit,
            max_batches=args.max_batches,
            timeout_seconds=args.timeout_seconds,
            now=datetime.now(UTC),
        )
    except DomainValidationError as exc:
        # 参数越界是操作错误，直接给出原因，不写任何数据。
        logger.error("参数非法：%s", exc)
        return 2

    for entry in report.entries:
        print(entry.describe())
    print(json.dumps(report.summary(), ensure_ascii=False, sort_keys=True))
    if not report.applied:
        print("dry-run：未写入任何数据；确认无误后加 --apply 执行。")
    if report.timed_out:
        print("本轮达到墙钟超时；剩余候选留待下次运行（幂等，可重复执行）。")
    logger.info("血缘回填完成", extra=report.summary())
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
