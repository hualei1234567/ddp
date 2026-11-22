"""质检 Worker：消费 ``quality.run.requested``，推进质量运行的生命周期。

运行方式::

    python -m app.workers.quality --once
    python -m app.workers.quality --drain

**为什么需要它**：``POST /quality/runs`` 与标注提交质检都是同步执行——它们的
输入是已加载的事实、没有大文件 IO，同步是合理的（见 ADR-0043）。但"提交后
自动质检"这类入口需要**可排队、可重试、可观测进度**，于是运行先落
``QUEUED``，由本 Worker 领取执行。

**幂等与并发**：事件用 ``SELECT ... FOR UPDATE SKIP LOCKED`` 抢占（SQLite 退化为
单写事务串行化），并用 ``available_at`` 做租约；运行的状态推进以 ``lifecycle``
做 CAS，两个 Worker 领到同一运行时后到者抛 ``QualityRunConflictError``，不会
产出两条互相矛盾的结论。

**失败语义**：

- 规则实现崩溃、数据库/对象存储抖动 → 运行 ``FAILED`` 后按 ``max_attempts``
  重投（``RETRYING``），事件退避后重新可见；
- 超过重投上限、或 payload 非法（永久性错误）→ 运行 ``FAILED``、事件进死信，
  保留 ``error_code`` / ``error_summary`` / ``last_error`` 供人工处理。

**绝不把失败降级成通过**：任何异常路径都不会写入 ``SUCCEEDED``，也不会把
"没算完"写成 ``INCOMPLETE``（后者是"算过但无结论"）。
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.application.quality.registry import default_registry
from app.application.quality.use_cases import QualityRunUseCases
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.domain.identity.models import ProjectScope
from app.domain.quality.enums import QualityRunLifecycle
from app.domain.quality.errors import (
    DomainValidationError,
    InvalidQualityRunTransitionError,
    QualityDomainError,
    QualityEngineError,
    QualityRunConflictError,
    ResourceNotFoundError,
)
from app.domain.quality.models import QualityRun
from app.infrastructure.db.models.quality import QualityRunModel
from app.infrastructure.db.session import create_session_factory, get_engine
from app.infrastructure.repositories.annotation_results import (
    SqlAnnotationRecordRepository,
)
from app.infrastructure.repositories.annotation_schemas import (
    SqlAnnotationSchemaRepository,
)
from app.infrastructure.repositories.annotation_tasks import SqlAnnotationTaskRepository
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.dataset_policy_repositories import (
    SqlDatasetReportRepository,
)
from app.infrastructure.repositories.datasources import (
    SqlDataSourceImportRepository,
    SqlMediaAssetRepository,
)
from app.infrastructure.repositories.episodes import (
    SqlEpisodeRawAssetRepository,
    SqlEpisodeRepository,
)
from app.infrastructure.repositories.outbox_validation import SqlOutboxValidationQueue
from app.infrastructure.repositories.quality import SqlQualityRunRepository

logger = logging.getLogger("ddp.worker.quality")

REQUESTED_EVENT_TYPE = "quality.run.requested"
#: 事件租约：处理期间把 ``available_at`` 推后，避免第二个 Worker 再领同一事件。
#: 与校验 Worker 同口径，崩溃后租约到期自动重新可见。
LEASE_SECONDS = 60.0


class QualityRunExecutionStatus:
    """单次处理的终态（字符串常量，便于日志与测试直接比较）。"""

    IDLE = "IDLE"
    PUBLISHED = "PUBLISHED"
    RETRIED = "RETRIED"
    DEAD_LETTER = "DEAD_LETTER"


@dataclass(frozen=True, slots=True)
class QualityRunProcessResult:
    status: str
    run_id: UUID | None = None
    attempts: int = 0
    error: str | None = None


#: 执行器：在给定会话内把一条**已领取**（``RUNNING``）的运行推进到结论。
#: 默认走真实用例；测试注入故障实现，避免把测试专用分支塞进生产类。
#:
#: 领取（``QUEUED``/``RETRYING`` → ``RUNNING``）由 Worker 自己完成并**先提交**，
#: 因此执行器只负责"算 + 落结论"。这样规则实现崩溃时 ``RUNNING`` 已是持久
#: 事实，失败路径能把它推进到 ``FAILED`` 并留下开始时间；若领取与计算同处
#: 一个未提交事务，回滚会把 ``RUNNING`` 一起抹掉，运行退回 ``QUEUED``，而
#: 状态机不允许 ``QUEUED → FAILED``（未开始不能算失败），失败就无法落库。
Executor = Callable[[Session, ProjectScope, UUID, datetime], QualityRun]


def _default_executor(session: Session, scope: ProjectScope, run_id: UUID, now: datetime):
    return _use_cases(session).conclude(scope, run_id, now=now)


def _use_cases(session: Session) -> QualityRunUseCases:
    """与 HTTP 依赖注入相同的装配；不复制规则，只复用现有仓储。"""
    return QualityRunUseCases(
        runs=SqlQualityRunRepository(session),
        imports=SqlDataSourceImportRepository(session),
        assets=SqlMediaAssetRepository(session),
        episodes=SqlEpisodeRepository(session),
        episode_assets=SqlEpisodeRawAssetRepository(session),
        annotation_records=SqlAnnotationRecordRepository(session),
        schemas=SqlAnnotationSchemaRepository(session),
        tasks=SqlAnnotationTaskRepository(session),
        registry=default_registry(SqlDatasetReportRepository(session)),
        audit=SqlAuditRepository(session),
        outbox=SqlOutboxRepository(session),
        uow=session,
    )


#: 永久性错误：同样的输入重试只会得到同样的拒绝，直接死信。
_PERMANENT_ERRORS = (
    DomainValidationError,
    InvalidQualityRunTransitionError,
    ResourceNotFoundError,
    QualityEngineError,
)


class QualityRunWorker:
    def __init__(
        self,
        factory: sessionmaker[Session],
        *,
        max_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
        execute: Executor | None = None,
    ) -> None:
        self._factory = factory
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._execute = execute or _default_executor

    def process_once(self, *, now: datetime | None = None) -> QualityRunProcessResult:
        moment = now or datetime.now(UTC)
        with self._factory() as session:
            queue = SqlOutboxValidationQueue(session)
            claimed = queue.claim_next(now=moment, event_type=REQUESTED_EVENT_TYPE)
            if claimed is None:
                return QualityRunProcessResult(status=QualityRunExecutionStatus.IDLE)
            queue.mark_claimed(claimed.event_id, now=moment, lease_seconds=LEASE_SECONDS)

            run_id = _run_id_from(claimed.payload)
            if run_id is None:
                return self._dead_letter(
                    session,
                    queue,
                    claimed.event_id,
                    attempts=claimed.attempts + 1,
                    error=f"payload 缺少合法 run_id：{claimed.payload.get('run_id')!r}",
                )

            row = session.get(QualityRunModel, run_id)
            if row is None or row.project_id != claimed.project_id:
                return self._dead_letter(
                    session,
                    queue,
                    claimed.event_id,
                    attempts=claimed.attempts + 1,
                    error=f"质量运行不存在或不属于该项目：{run_id}",
                )
            # Worker 以运行创建人的名义执行：审计列满足 users 外键，也留下
            # "谁的数据被检查"的语义。
            scope = ProjectScope(project_id=row.project_id, actor_id=row.created_by)

            current = SqlQualityRunRepository(session).find_any(scope, run_id)
            if current is None:
                # 上面的 row 检查刚确认存在；走到这里说明并发删除了行。
                return self._dead_letter(
                    session,
                    queue,
                    claimed.event_id,
                    attempts=claimed.attempts + 1,
                    error=f"质量运行在领取前被删除：{run_id}",
                )
            if current.lifecycle in (
                QualityRunLifecycle.SUCCEEDED,
                QualityRunLifecycle.CANCELLED,
            ):
                # 重复投递：运行已到终态，这次执行没有可做的事。事件按已发布
                # 收口而不是死信——事件本身没有错，只是来晚了。
                queue.mark_published(claimed.event_id, now=moment)
                session.commit()
                return QualityRunProcessResult(
                    status=QualityRunExecutionStatus.PUBLISHED,
                    run_id=run_id,
                    attempts=claimed.attempts + 1,
                )
            if current.lifecycle is QualityRunLifecycle.RUNNING:
                # 另一个 Worker 已领取且正在执行。绝不干预那一行：只让事件
                # 退避后重来，届时按当时的 lifecycle 重新判断。
                return self._retry_or_dead_letter(
                    session,
                    queue,
                    claimed.event_id,
                    scope,
                    run_id,
                    QualityDomainError(f"质量运行 {run_id} 正在被其他执行者处理，稍后重试"),
                    moment,
                    mark_run_failed=False,
                )

            # 第一步：领取并**提交** ``RUNNING``。执行开始是持久事实，必须
            # 先落盘再做计算——否则计算崩溃后的回滚会把开始时间抹掉，运行
            # 退回 QUEUED，失败路径无法落 FAILED（见 Executor 注释）。
            try:
                _use_cases(session).start(scope, run_id, now=moment)
                session.commit()
            except QualityRunConflictError as exc:
                # 另一个 Worker 已领取这一行（RUNNING 已提交）。**不能**把运行
                # 标成 FAILED——它会误伤正在执行的那次；只让事件退避重投，
                # 等它回来时按当前 lifecycle 重新判断。
                return self._retry_or_dead_letter(
                    session,
                    queue,
                    claimed.event_id,
                    scope,
                    run_id,
                    exc,
                    moment,
                    mark_run_failed=False,
                )
            except _PERMANENT_ERRORS as exc:
                return self._fail_run_and_dead_letter(
                    session, queue, claimed.event_id, scope, run_id, exc, moment
                )
            except Exception as exc:  # noqa: BLE001 - 单事件异常不得终止 Worker
                return self._retry_or_dead_letter(
                    session, queue, claimed.event_id, scope, run_id, exc, moment
                )

            # 第二步：执行计算并落结论。失败时 ``RUNNING`` 已提交，失败路径
            # 能把它推进到 FAILED / 重投。
            try:
                executed = self._execute(session, scope, run_id, moment)
            except _PERMANENT_ERRORS as exc:
                return self._fail_run_and_dead_letter(
                    session, queue, claimed.event_id, scope, run_id, exc, moment
                )
            except Exception as exc:  # noqa: BLE001 - 单事件异常不得终止 Worker
                return self._retry_or_dead_letter(
                    session, queue, claimed.event_id, scope, run_id, exc, moment
                )

            queue.mark_published(claimed.event_id, now=moment)
            session.commit()
            return QualityRunProcessResult(
                status=QualityRunExecutionStatus.PUBLISHED,
                run_id=executed.run_id,
                attempts=claimed.attempts + 1,
            )

    # ------------------------------------------------------------------ #
    # 失败路径
    # ------------------------------------------------------------------ #

    def _retry_or_dead_letter(
        self,
        session: Session,
        queue: SqlOutboxValidationQueue,
        event_id: UUID,
        scope: ProjectScope,
        run_id: UUID,
        exc: Exception,
        moment: datetime,
        *,
        mark_run_failed: bool = True,
    ) -> QualityRunProcessResult:
        session.rollback()
        attempts = _event_attempts(session, event_id) + 1
        if attempts >= self._max_attempts:
            return self._fail_run_and_dead_letter(
                session,
                queue,
                event_id,
                scope,
                run_id,
                exc,
                moment,
                attempts=attempts,
                mark_run_failed=mark_run_failed,
            )
        delay = min(
            self._retry_base_seconds * (2 ** (attempts - 1)),
            self._retry_max_seconds,
        )
        if mark_run_failed:
            self._mark_run_failed(session, scope, run_id, exc, moment)
            self._schedule_run_retry(session, scope, run_id, moment)
        queue.mark_retry(
            event_id,
            attempts=attempts,
            available_at=moment + timedelta(seconds=delay),
            last_error=str(exc)[:2000],
        )
        session.commit()
        logger.warning(
            "质检运行将重试 run_id=%s attempts=%s error=%s",
            run_id,
            attempts,
            exc,
        )
        return QualityRunProcessResult(
            status=QualityRunExecutionStatus.RETRIED,
            run_id=run_id,
            attempts=attempts,
            error=str(exc)[:500],
        )

    def _fail_run_and_dead_letter(
        self,
        session: Session,
        queue: SqlOutboxValidationQueue,
        event_id: UUID,
        scope: ProjectScope,
        run_id: UUID,
        exc: Exception,
        moment: datetime,
        *,
        attempts: int | None = None,
        mark_run_failed: bool = True,
    ) -> QualityRunProcessResult:
        session.rollback()
        resolved = attempts if attempts is not None else _event_attempts(session, event_id) + 1
        if mark_run_failed:
            self._mark_run_failed(session, scope, run_id, exc, moment)
        queue.mark_dead_letter(
            event_id,
            attempts=resolved,
            last_error=str(exc)[:2000],
        )
        session.commit()
        logger.error(
            "质检运行进入死信 run_id=%s attempts=%s error=%s",
            run_id,
            resolved,
            exc,
        )
        return QualityRunProcessResult(
            status=QualityRunExecutionStatus.DEAD_LETTER,
            run_id=run_id,
            attempts=resolved,
            error=str(exc)[:500],
        )

    def _dead_letter(
        self,
        session: Session,
        queue: SqlOutboxValidationQueue,
        event_id: UUID,
        *,
        attempts: int,
        error: str,
    ) -> QualityRunProcessResult:
        queue.mark_dead_letter(event_id, attempts=attempts, last_error=error)
        session.commit()
        logger.error("质检事件进入死信 event_id=%s error=%s", event_id, error)
        return QualityRunProcessResult(
            status=QualityRunExecutionStatus.DEAD_LETTER,
            attempts=attempts,
            error=error,
        )

    def _mark_run_failed(
        self,
        session: Session,
        scope: ProjectScope,
        run_id: UUID,
        exc: Exception,
        moment: datetime,
    ) -> None:
        """把仍在执行中的运行推进到 ``FAILED``；已终态则保持原样。

        只处理 ``RUNNING``：``QUEUED`` 说明执行尚未开始（例如 CAS 冲突），
        此时不应伪造开始时间；``SUCCEEDED`` / ``CANCELLED`` 是终态。

        ``finished_at`` 用 Worker 的 ``moment`` 而不是真实时钟：测试与批处理
        会传入固定时间，若这里取 ``now()``，固定时刻可能早于 ``started_at``，
        撞 ``ck_quality_runs_finished_after_started``。
        """
        runs = SqlQualityRunRepository(session)
        current = runs.find_any(scope, run_id)
        if current is None or current.lifecycle.value != "RUNNING":
            return
        try:
            runs.transition(
                scope,
                current.mark_failed(
                    error_code=_error_code(exc),
                    error_summary=str(exc)[:1000] or exc.__class__.__name__,
                    now=moment,
                ),
                expected=current.lifecycle,
            )
        except QualityDomainError:
            # 并发方已推进这一行（含 QualityRunConflictError）：结论以先到者
            # 为准，事件仍按失败处理。
            logger.warning("运行状态已被并发方推进 run_id=%s", run_id)

    def _schedule_run_retry(
        self,
        session: Session,
        scope: ProjectScope,
        run_id: UUID,
        moment: datetime,
    ) -> None:
        runs = SqlQualityRunRepository(session)
        current = runs.find_any(scope, run_id)
        if current is None or current.lifecycle.value != "FAILED":
            return
        try:
            runs.transition(
                scope,
                current.schedule_retry(now=moment),
                expected=current.lifecycle,
            )
        except QualityDomainError:
            # 达到重投上限：保持 FAILED，由死信路径暴露。
            return

    def run(
        self,
        *,
        stop_event: threading.Event | None = None,
        once: bool = False,
        drain: bool = False,
    ) -> None:
        stop = stop_event or threading.Event()
        logger.info(
            "质检 Worker 已启动 poll_interval=%s max_attempts=%s",
            get_settings().quality_worker_poll_interval_seconds,
            self._max_attempts,
        )
        while not stop.is_set():
            try:
                result = self.process_once()
            except Exception:  # noqa: BLE001 - 单事件异常不得终止 Worker
                logger.exception("处理质检事件时发生未预期异常，将在下一轮重试")
                if once or drain:
                    return
                stop.wait(get_settings().quality_worker_poll_interval_seconds)
                continue
            if once:
                return
            if drain and result.status == QualityRunExecutionStatus.IDLE:
                return
            if result.status == QualityRunExecutionStatus.IDLE:
                stop.wait(get_settings().quality_worker_poll_interval_seconds)
        logger.info("质检 Worker 已停止")


def _run_id_from(payload: dict[str, object]) -> UUID | None:
    raw = payload.get("run_id")
    if raw is None:
        return None
    try:
        return UUID(str(raw))
    except (TypeError, ValueError):
        return None


def _event_attempts(session: Session, event_id: UUID) -> int:
    from app.infrastructure.db.models.collection import (  # noqa: PLC0415 - 避免与仓储循环
        OutboxEvent as OutboxEventModel,
    )

    row = session.get(OutboxEventModel, event_id)
    return int(row.attempts) if row is not None else 0


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code[:64]
    return type(exc).__name__[:64]


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)


def _worker_from_settings(
    settings: Settings,
    factory: sessionmaker[Session],
) -> QualityRunWorker:
    return QualityRunWorker(
        factory=factory,
        max_attempts=settings.quality_worker_max_attempts,
        retry_base_seconds=settings.quality_worker_retry_base_seconds,
        retry_max_seconds=settings.quality_worker_retry_max_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DDP 质检 outbox Worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="最多处理一个事件后退出")
    mode.add_argument("--drain", action="store_true", help="处理到当前无可用事件后退出")
    args = parser.parse_args(argv)

    configure_logging()
    settings = get_settings()
    settings.validate_runtime_security()
    worker = _worker_from_settings(settings, create_session_factory(get_engine()))

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    worker.run(stop_event=stop_event, once=args.once, drain=args.drain)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "REQUESTED_EVENT_TYPE",
    "QualityRunExecutionStatus",
    "QualityRunProcessResult",
    "QualityRunWorker",
    "main",
]
