"""领域巡检 Worker：标注租约过期 + 标注任务逾期 + 采集任务数据检查。

运行方式::

    python -m app.workers.domain_sweeps --interval-seconds 60
    python -m app.workers.domain_sweeps --once

**为什么必须有它**

三件事此前都只有"能力"没有"触发点"：

- 标注分配有 ``expire_due``，但没有任何调度在调它。租约过期形同虚设：标注员
  领了活不干，分配永远停在 ``CLAIMED``，容量统计永远认为他手上有活。
- 采集任务有 ``system-data-check`` 迁移，但全仓没有触发点。任务提交后永久停在
  ``SUBMITTED``，验收/完成按钮永远不出现。
- 数据集版本有 ``validity_status`` 子状态机（``VALID→STALE``），但没有任何
  检测器写入它；``invalidate`` / ``revalidate`` 因此永远不可达，失效版本
  仍然可以继续导出与交付。
- 标注任务有 ``due_at``，但没有到期观测。逾期是派生事实，本 worker 只做
  只读计数与日志，**不改派、不关闭、不写状态**。

本 worker 按固定周期跑这四类巡检（逾期扫描只读）。每类都是**有界批量 + 幂等**（只处理
``SUBMITTED`` / 已到租约的记录），单条失败只记日志并留待下一轮，不中断整批。

**为什么不塞进 maintenance worker**

维护 worker 默认关闭（备份需要显式开启），而这两件事是业务主流程的一部分，
必须常开。职责也不同：维护 worker 管"库的卫生"（备份/归档/清扫），本 worker
管"业务状态推进"。
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.application.annotation.assignments import AnnotationAssignmentUseCases
from app.application.collection.data_check import CollectionDataCheckUseCases
from app.application.datasets.validity import DatasetValiditySweep
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.session import create_session_factory, get_engine
from app.infrastructure.repositories.annotation_assignments import (
    SqlAnnotationAssignmentRepository,
)
from app.infrastructure.repositories.annotation_audit import (
    SqlAnnotationAuditRepository,
    SqlAnnotationIdempotencyRepository,
    SqlAnnotationOutboxRepository,
)
from app.infrastructure.repositories.annotation_batches import SqlAnnotationBatchRepository
from app.infrastructure.repositories.annotation_directory import SqlMemberDirectory
from app.infrastructure.repositories.annotation_tasks import SqlAnnotationTaskRepository
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository as SqlCollectionAuditRepository,
)
from app.infrastructure.repositories.collection_audit import (
    SqlOutboxRepository as SqlCollectionOutboxRepository,
)
from app.infrastructure.repositories.collection_tasks import SqlCollectionTaskRepository
from app.infrastructure.repositories.dataset_directory import SqlDatasetUpstreamDirectory
from app.infrastructure.repositories.dataset_repositories import (
    SqlDatasetItemRepository,
    SqlDatasetVersionRepository,
)
from app.infrastructure.repositories.datasources import (
    SqlDataSourceImportRepository,
    SqlMediaAssetRepository,
)
from app.infrastructure.repositories.projects import SqlProjectMemberRepository

logger = logging.getLogger("ddp.worker.domain_sweeps")

# 标注分配里"占用容量"的两个状态；只有这两个会被租约过期推进。
_EXPIRABLE_ASSIGNMENT_STATUSES = ("ASSIGNED", "CLAIMED")
_SWEEP_BATCH_LIMIT = 100


class DomainSweepWorker:
    def __init__(
        self,
        factory: sessionmaker[Session],
        *,
        batch_limit: int = _SWEEP_BATCH_LIMIT,
    ) -> None:
        self._factory = factory
        self._batch_limit = batch_limit

    # ---- 标注分配租约 ----
    def expire_assignments(self, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        expired = 0
        with self._factory() as session:
            assignments = SqlAnnotationAssignmentRepository(session)
            leases = assignments.list_expired_leases(
                moment,
                statuses=_EXPIRABLE_ASSIGNMENT_STATUSES,
                limit=self._batch_limit,
            )
            if not leases:
                return 0
            projects = sorted({project_id for _assignment_id, project_id in leases})
            use_cases = AnnotationAssignmentUseCases(
                SqlAnnotationTaskRepository(session),
                SqlAnnotationBatchRepository(session),
                assignments,
                SqlMemberDirectory(session),
                SqlAnnotationAuditRepository(session),
                SqlAnnotationOutboxRepository(session),
                SqlAnnotationIdempotencyRepository(session),
                session,
            )
            members = SqlProjectMemberRepository(session)
            for project_id in projects:
                owner_id = members.first_owner_id(project_id)
                if owner_id is None:
                    # 没有负责人就没有可审计的执行人；只影响该项目的过期推进。
                    logger.warning(
                        "跳过项目：没有负责人，无法记录系统动作执行人",
                        extra={"project_id": str(project_id)},
                    )
                    continue
                scope = ProjectScope(project_id, actor_id=owner_id)
                expired += use_cases.expire_due(scope, now=moment)
        logger.info("标注租约巡检完成", extra={"expired": expired})
        return expired

    # ---- 标注任务逾期扫描（只读） ----
    def count_overdue_tasks(self, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        with self._factory() as session:
            overdue = SqlAnnotationTaskRepository(session).count_overdue(moment)
        logger.info("标注任务逾期扫描完成", extra={"overdue_tasks": overdue})
        return overdue

    # ---- 采集任务数据检查 ----
    def check_collection_tasks(self, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        with self._factory() as session:
            use_cases = CollectionDataCheckUseCases(
                SqlCollectionTaskRepository(session),
                SqlDataSourceImportRepository(session),
                SqlMediaAssetRepository(session),
                SqlProjectMemberRepository(session),
                SqlCollectionAuditRepository(session),
                SqlCollectionOutboxRepository(session),
                session,
            )
            outcomes = use_cases.run_once(limit=self._batch_limit, now=moment)
        logger.info(
            "采集任务数据检查完成",
            extra={
                "checked": len(outcomes),
                "failed": sum(1 for item in outcomes if not item.passed),
            },
        )
        return len(outcomes)

    # ---- 数据集版本有效性 ----
    def check_dataset_validity(self, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        with self._factory() as session:
            sweep = DatasetValiditySweep(
                SqlDatasetVersionRepository(session),
                SqlDatasetItemRepository(session),
                SqlDatasetUpstreamDirectory(session),
                SqlProjectMemberRepository(session),
                SqlCollectionAuditRepository(session),
                SqlCollectionOutboxRepository(session),
                session,
            )
            outcomes = sweep.run_once(limit=self._batch_limit, now=moment)
        logger.info("数据集版本有效性巡检完成", extra={"marked_stale": len(outcomes)})
        return len(outcomes)

    def sweep_once(self, *, now: datetime | None = None) -> dict[str, int]:
        moment = now or datetime.now(UTC)
        result = {
            "expired_assignments": 0,
            "overdue_annotation_tasks": 0,
            "checked_collection_tasks": 0,
            "stale_dataset_versions": 0,
        }
        # 各类巡检互相独立：一类失败不阻止另一类（失败会在下一轮重试）。
        try:
            result["expired_assignments"] = self.expire_assignments(now=moment)
        except Exception:  # noqa: BLE001 —— 巡检失败不终止进程
            logger.exception("标注租约巡检失败，下一轮重试")
        try:
            result["overdue_annotation_tasks"] = self.count_overdue_tasks(now=moment)
        except Exception:  # noqa: BLE001
            logger.exception("标注任务逾期扫描失败，下一轮重试")
        try:
            result["checked_collection_tasks"] = self.check_collection_tasks(now=moment)
        except Exception:  # noqa: BLE001
            logger.exception("采集任务数据检查失败，下一轮重试")
        try:
            result["stale_dataset_versions"] = self.check_dataset_validity(now=moment)
        except Exception:  # noqa: BLE001
            logger.exception("数据集版本有效性巡检失败，下一轮重试")
        return result

    def run(self, *, stop_event: threading.Event, interval_seconds: float) -> None:
        while not stop_event.is_set():
            self.sweep_once()
            stop_event.wait(interval_seconds)


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="DDP 领域巡检 Worker（租约过期 / 逾期扫描 / 数据检查）"
    )
    parser.add_argument("--once", action="store_true", help="巡检一次后退出")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=60.0,
        help="两次巡检的间隔（默认 60 秒）",
    )
    args = parser.parse_args(argv)

    configure_logging()
    get_settings()  # 触发配置校验：配置非法时启动就失败，而不是跑起来才炸
    factory = create_session_factory(get_engine())
    worker = DomainSweepWorker(factory)
    if args.once:
        worker.sweep_once()
        return 0

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    logger.info("领域巡检 worker 启动", extra={"interval_seconds": args.interval_seconds})
    worker.run(stop_event=stop_event, interval_seconds=args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
