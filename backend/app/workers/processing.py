"""处理运行 Worker 入口（N4-002）。

运行方式::

    python -m app.workers.processing

消费 outbox 中的 ``processing.run.requested`` 事件（投递类）：抢占事件 →
把运行推进到 RUNNING → 按 ``STAGE_SEQUENCE`` 逐阶段执行并写步骤 →
成功则 SUCCEEDED，失败则按既有 outbox 语义退避重试，超限进死信。

**失败顺序（与 N2-002 修复后的导入 Worker 一致）**：先判死信，再决定是否
把运行退回 ``RETRYING``。反过来会留下"事件已死信、运行仍 RETRYING/PENDING"
的孤儿——没有消费者、也没有客户端重试入口。

**不做的事**：不复制状态机（全部经 ``ProcessingWorkerUseCases``），不伪造
未实现阶段的成功（编排器显式 SKIPPED），不在 HTTP 线程处理大文件。
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.application.ingestion.ports import ClaimedValidationEvent
from app.application.observability_redaction import sanitize_log_message
from app.application.processing.pipeline import (
    ConcurrentRunUpdate,
    PipelineOutcomeStatus,
    ProcessingPipeline,
    default_handlers,
)
from app.application.processing.ports import InputObjectInspector
from app.application.processing.quality_stage import QualityCheckHandler
from app.application.processing.use_cases import (
    PROCESSING_RUN_REQUESTED_EVENT,
    ProcessingWorkerUseCases,
)
from app.application.quality.episode_checks import EpisodeQualityChecks
from app.application.quality.registry import default_registry
from app.application.quality.use_cases import QualityRunUseCases
from app.core.config import Settings, get_settings
from app.core.logging import bind_trace, configure_logging, trace_from_event_payload
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import ProcessingRunStatus
from app.domain.processing.errors import (
    InvalidStateTransitionError,
    ResourceNotFoundError,
)
from app.domain.processing.models import ERROR_SUMMARY_MAX_LENGTH
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
from app.infrastructure.repositories.processing import (
    SqlProcessingRunRepository,
    SqlProcessingRunStepRepository,
)
from app.infrastructure.repositories.quality import SqlQualityRunRepository
from app.infrastructure.storage.processing_objects import S3InputObjectInspector
from app.infrastructure.storage.s3 import S3MultipartStorage

logger = logging.getLogger("ddp.worker.processing")

#: 消费 ``processing.run.requested``；字符串只在生产侧定义一次（导入该常量），
#: 避免两处手写事件名后悄然失配。
RUN_REQUESTED_EVENT = PROCESSING_RUN_REQUESTED_EVENT


class RunProcessingStatus(StrEnum):
    IDLE = "IDLE"
    PUBLISHED = "PUBLISHED"
    RETRIED = "RETRIED"
    DEAD_LETTER = "DEAD_LETTER"


class ProcessingRunWorker:
    def __init__(
        self,
        factory: sessionmaker[Session],
        settings: Settings,
        *,
        inspector: InputObjectInspector | None = None,
    ) -> None:
        self._factory = factory
        self._settings = settings
        # 存储适配器按 Worker 进程复用：boto3 client 构造会读配置，每事件重建
        # 既浪费也放大连接抖动。
        self._inspector = inspector or S3InputObjectInspector(S3MultipartStorage(settings))

    def process_once(self, *, now: datetime | None = None) -> dict[str, object]:
        moment = now or datetime.now(UTC)
        with self._factory() as session:
            queue = SqlOutboxValidationQueue(session)
            claimed = queue.claim_next(now=moment, event_type=RUN_REQUESTED_EVENT)
            if claimed is None:
                return {"status": RunProcessingStatus.IDLE.value}
            # 追踪上下文覆盖处理与结果日志；来源是事件 payload 的 request_id，
            # 缺失时生成并标注 generated，退出 with 后复位。
            with bind_trace(trace_from_event_payload(claimed.payload)) as trace:
                result = self._process_claimed(session, queue, claimed, moment)
                _log_result(result)
                if trace.is_generated:
                    logger.info(
                        "处理运行事件缺少可用 request_id，已生成本地 trace_id event_id=%s",
                        claimed.event_id,
                    )
                return result

    def _process_claimed(
        self,
        session: Session,
        queue: SqlOutboxValidationQueue,
        claimed: ClaimedValidationEvent,
        moment: datetime,
    ) -> dict[str, object]:
        # claim 的行锁只在当前事务内有效；pipeline 会按阶段提交，锁会释放。
        # 先落一个租约再提交，避免第二个 Worker 在同一运行上并发执行阶段。
        if not queue.mark_claimed(
            claimed.event_id,
            now=moment,
            lease_seconds=self._settings.processing_event_lease_seconds,
        ):
            session.commit()
            return {
                "status": RunProcessingStatus.PUBLISHED.value,
                "skipped": "事件已被另一 Worker 领取",
            }
        session.commit()

        raw_id = claimed.payload.get("run_id")
        try:
            run_id = UUID(str(raw_id))
        except (TypeError, ValueError):
            queue.mark_dead_letter(
                claimed.event_id,
                attempts=claimed.attempts + 1,
                last_error=f"payload 缺少合法 run_id：{raw_id!r}",
            )
            session.commit()
            return {
                "status": RunProcessingStatus.DEAD_LETTER.value,
                "event_id": str(claimed.event_id),
            }

        runs = SqlProcessingRunRepository(session)
        run = runs.get(
            # 读只需要项目范围；写用运行创建人（updated_by 有 FK 到 users）。
            ProjectScope(claimed.project_id, actor_id=claimed.project_id),
            run_id,
        )
        if run is None:
            queue.mark_dead_letter(
                claimed.event_id,
                attempts=claimed.attempts + 1,
                last_error=f"处理运行不存在或不属于该项目：{run_id}",
            )
            session.commit()
            return {
                "status": RunProcessingStatus.DEAD_LETTER.value,
                "run_id": str(run_id),
            }

        scope = ProjectScope(claimed.project_id, actor_id=run.created_by or run.updated_by)
        pipeline = self._build_pipeline(session)

        try:
            outcome = pipeline.execute(scope, run_id, now=moment)
        except ConcurrentRunUpdate as exc:
            # 另一 Worker 已推进或客户端已取消：本事件不需要再投递。
            session.rollback()
            queue.mark_published(claimed.event_id, now=moment)
            session.commit()
            return {
                "status": RunProcessingStatus.PUBLISHED.value,
                "run_id": str(run_id),
                "skipped": f"运行被并发推进或取消：{exc}",
            }
        except Exception as exc:  # noqa: BLE001 - 单事件异常不得终止 Worker
            logger.exception("处理运行执行异常", extra={"run_id": str(run_id)})
            session.rollback()
            self._force_fail(session, scope, run_id, moment, exc)
            return self._settle_failure(
                session,
                queue,
                claimed=claimed,
                scope=scope,
                run_id=run_id,
                moment=moment,
                last_error=str(exc),
            )

        if outcome.status == PipelineOutcomeStatus.FAILED.value:
            return self._settle_failure(
                session,
                queue,
                claimed=claimed,
                scope=scope,
                run_id=run_id,
                moment=moment,
                last_error=outcome.error_summary or outcome.error_code or "阶段失败",
                retryable=outcome.retryable,
            )

        queue.mark_published(claimed.event_id, now=moment)
        session.commit()
        return {
            "status": RunProcessingStatus.PUBLISHED.value,
            "run_id": str(run_id),
            "outcome": outcome.status,
            "note": outcome.reason,
        }

    def _build_pipeline(self, session: Session) -> ProcessingPipeline:
        runs = SqlProcessingRunRepository(session)
        # QUALITY 阶段复用统一质量引擎：规则源、六类聚合与规则集版本都由
        # N4-004 的实现提供，本 Worker 只做装配，不复制任何规则。
        quality_runs = QualityRunUseCases(
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
        quality_check = QualityCheckHandler(
            episode_assets=SqlEpisodeRawAssetRepository(session),
            quality=EpisodeQualityChecks(quality_runs),
            uow=session,
        )
        return ProcessingPipeline(
            runs,
            SqlProcessingRunStepRepository(session),
            ProcessingWorkerUseCases(runs, session),
            default_handlers(self._inspector, quality_check=quality_check),
            session,
        )

    def _force_fail(
        self,
        session: Session,
        scope: ProjectScope,
        run_id: UUID,
        moment: datetime,
        exc: Exception,
    ) -> None:
        """把因 Worker 内部异常而悬停的运行落到 FAILED，避免留下 RUNNING 孤儿。"""
        summary = sanitize_log_message(str(exc), limit=ERROR_SUMMARY_MAX_LENGTH)
        try:
            runs = SqlProcessingRunRepository(session)
            worker = ProcessingWorkerUseCases(runs, session)
            run = runs.get(scope, run_id)
            if run is None or run.is_terminal or run.status == ProcessingRunStatus.FAILED.value:
                return
            if run.status == ProcessingRunStatus.CREATED.value:
                run = worker.enqueue(scope, run_id, expected_version=run.row_version, now=moment)
            if run.status in {
                ProcessingRunStatus.QUEUED.value,
                ProcessingRunStatus.RETRYING.value,
            }:
                run = worker.claim(
                    scope,
                    run_id,
                    expected_version=run.row_version,
                    stage=run.stage,
                    now=moment,
                )
            if run.status == ProcessingRunStatus.RUNNING.value:
                worker.fail(
                    scope,
                    run_id,
                    expected_version=run.row_version,
                    error_code="worker_unexpected_error",
                    error_summary=summary,
                    now=moment,
                )
        except Exception:  # noqa: BLE001 - 收口失败必须留痕，但不能掩盖原始异常
            session.rollback()
            logger.exception("收口异常运行失败", extra={"run_id": str(run_id)})

    def _settle_failure(
        self,
        session: Session,
        queue: SqlOutboxValidationQueue,
        *,
        claimed,
        scope: ProjectScope,
        run_id: UUID,
        moment: datetime,
        last_error: str,
        retryable: bool = True,
    ) -> dict[str, object]:
        """按"先判死信、再退回 RETRYING"的顺序收口失败事件与运行。"""
        attempts = claimed.attempts + 1
        if not retryable:
            # 永久性失败：运行已由 pipeline 落 FAILED，事件正常投递完成即可。
            # 不重试、不记死信噪声；错误码与步骤日志留在运行与步骤上。
            queue.mark_published(claimed.event_id, now=moment)
            session.commit()
            return {
                "status": RunProcessingStatus.PUBLISHED.value,
                "run_id": str(run_id),
                "attempts": attempts,
                "note": "永久性失败，不重试",
                "error": sanitize_log_message(last_error, limit=500),
            }
        safe_error = sanitize_log_message(last_error, limit=ERROR_SUMMARY_MAX_LENGTH)
        if attempts >= self._settings.validation_max_attempts:
            # 死信时运行保持 FAILED：错误码与步骤日志都留在运行/步骤上，人工可查。
            queue.mark_dead_letter(
                claimed.event_id,
                attempts=attempts,
                last_error=safe_error,
            )
            session.commit()
            return {
                "status": RunProcessingStatus.DEAD_LETTER.value,
                "run_id": str(run_id),
                "attempts": attempts,
                "released_for_retry": False,
                "error": safe_error[:500],
            }

        try:
            runs = SqlProcessingRunRepository(session)
            run = runs.get(scope, run_id)
            if run is None:
                raise ResourceNotFoundError("处理运行不存在")
            if run.status == ProcessingRunStatus.FAILED.value:
                run = ProcessingWorkerUseCases(runs, session).retry(
                    scope,
                    run_id,
                    expected_version=run.row_version,
                    now=moment,
                )
            if run.status != ProcessingRunStatus.RETRYING.value:
                raise InvalidStateTransitionError(run.status, "worker-retry")
        except Exception as exc:  # noqa: BLE001 - 无法退回重试时收口，避免无限撞墙
            session.rollback()
            logger.exception(
                "运行无法退回重试",
                extra={"run_id": str(run_id), "error": str(exc)},
            )
            current = None
            try:
                current = SqlProcessingRunRepository(session).get(scope, run_id)
            except Exception:  # noqa: BLE001 - 读不到时按需要人工核查的死信处理
                logger.exception("收口时读取运行失败", extra={"run_id": str(run_id)})
            if current is not None and current.is_terminal:
                # 运行已被取消/已完成：不需要重试，事件正常投递完成。
                queue.mark_published(claimed.event_id, now=moment)
                session.commit()
                return {
                    "status": RunProcessingStatus.PUBLISHED.value,
                    "run_id": str(run_id),
                    "skipped": f"运行已处于终态 {current.status}",
                }
            queue.mark_dead_letter(
                claimed.event_id,
                attempts=attempts,
                last_error=safe_error,
            )
            session.commit()
            return {
                "status": RunProcessingStatus.DEAD_LETTER.value,
                "run_id": str(run_id),
                "attempts": attempts,
                "released_for_retry": False,
                "error": safe_error[:500],
            }

        delay = min(
            self._settings.validation_retry_max_seconds,
            self._settings.validation_retry_base_seconds * (2 ** (attempts - 1)),
        )
        queue.mark_retry(
            claimed.event_id,
            attempts=attempts,
            available_at=moment + timedelta(seconds=delay),
            last_error=safe_error,
        )
        session.commit()
        return {
            "status": RunProcessingStatus.RETRIED.value,
            "run_id": str(run_id),
            "attempts": attempts,
            "released_for_retry": True,
            "error": safe_error[:500],
        }

    def run(self, *, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            result = self.process_once()
            if result["status"] == RunProcessingStatus.IDLE.value:
                stop_event.wait(self._settings.validation_worker_poll_interval_seconds)


def _log_result(result: dict[str, object]) -> None:
    """在已绑定 trace 的上下文内记录非成功结果。"""
    status = result.get("status")
    if status == RunProcessingStatus.RETRIED.value:
        logger.warning(
            "处理运行事件将重试 run_id=%s attempts=%s error=%s",
            result.get("run_id"),
            result.get("attempts"),
            result.get("error"),
        )
    elif status == RunProcessingStatus.DEAD_LETTER.value:
        logger.error(
            "处理运行事件进入死信 run_id=%s attempts=%s error=%s",
            result.get("run_id"),
            result.get("attempts"),
            result.get("error"),
        )


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DDP 处理运行 Worker")
    parser.add_argument("--once", action="store_true", help="处理一次后退出（用于测试）")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging()
    factory = create_session_factory(get_engine())
    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    worker = ProcessingRunWorker(factory, settings)
    if args.once:
        logger.info("单次处理完成", extra={"result": worker.process_once()})
        return 0
    logger.info(
        "处理运行 Worker 已启动",
        extra={
            "poll_interval": settings.validation_worker_poll_interval_seconds,
            "max_attempts": settings.validation_max_attempts,
            "event_type": RUN_REQUESTED_EVENT,
        },
    )
    worker.run(stop_event=stop_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
