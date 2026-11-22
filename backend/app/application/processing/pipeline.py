"""八阶段处理流水线编排（N4-002）。

**职责边界**

- 状态机仍在 :class:`ProcessingWorkerUseCases` / ``ProcessingRun.transition``：
  本模块只决定"调哪个动作"，不复制迁移表；
- 每个阶段写一条 ``processing_run_steps``（``RUNNING`` → 终态），失败原因
  与跳过原因都落库，不依赖日志文件才能解释结果；
- 阶段能力由 :mod:`app.application.processing.stages` 组装：``FILE_VALIDATION``
  （输入对象存在性）、``METADATA_EXTRACTION`` / ``FORMAT_PARSING``（对象元数据
  抽取与解析，N3）、``TIME_ALIGNMENT``、``CALIBRATION_VALIDATION``、
  ``EPISODE_SEGMENTATION``（N4）与 ``QUALITY_PRE_CHECK``（N4-005 统一质量引擎）
  均有真实落点；各处理器由组合根显式注入，不静默回退。
- ``DATA_CLEANING`` 仍**没有真实实现**，一律显式 ``SKIPPED`` 并写明原因
  （见 ``STAGE_SKIP_REASONS``）：把未实现写成 ``SUCCEEDED`` 会让"处理完成"
  变成谎话。

**重试与恢复**

- 运行进入 ``FAILED`` 后由 Worker 决定重试或死信；重试会把 attempt +1，
  下一轮从失败阶段继续（阶段只前进、不回退）；
- Worker 在阶段执行途中崩溃时，``RUNNING`` 行与运行阶段都已提交；下一次
  事件重投会 ``restart`` 同一 ``(run, stage, attempt)`` 行，不会产生重复记录；
- 运行版本冲突（并发 Worker 或客户端取消）抛 :class:`ConcurrentRunUpdate`，
  由 Worker 把事件标为 PUBLISHED 而不是重试——重试只会再次撞上同一冲突。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from app.application.observability_redaction import sanitize_log_message
from app.application.processing.ports import (
    ProcessingArtifactRepository,
    ProcessingRunRepository,
    ProcessingStepRepository,
    UnitOfWork,
)
from app.application.processing.stages import (
    STAGE_SKIP_REASONS,
    FileValidationHandler,
    SkippedStage,
    StageContext,
    StageFailure,
    StageHandler,
    StageOutcome,
    default_handlers,
)
from app.application.processing.use_cases import ProcessingWorkerUseCases
from app.domain.common.ids import new_id
from app.domain.identity.models import ProjectScope
from app.domain.processing.artifacts import ProcessingArtifact
from app.domain.processing.enums import (
    STAGE_SEQUENCE,
    ProcessingRunStatus,
    ProcessingStage,
    ProcessingStepStatus,
)
from app.domain.processing.errors import (
    InvalidStateTransitionError,
    ProcessingDomainError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.processing.models import ProcessingRun
from app.domain.processing.state_machines import first_stage, stage_index
from app.domain.processing.steps import (
    STEP_ERROR_SUMMARY_MAX_LENGTH,
    STEP_LOG_MAX_LENGTH,
    ProcessingStep,
)

logger = logging.getLogger("ddp.processing.pipeline")

_NOT_FOUND_MESSAGE = "处理运行不存在或不属于当前项目"


class PipelineOutcomeStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NOOP = "NOOP"


class ConcurrentRunUpdate(RuntimeError):
    """运行被并发推进（另一 Worker 或客户端取消）；本事件不再重试。"""


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    status: str
    run: ProcessingRun
    failed_stage: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    reason: str | None = None
    retryable: bool = True


class ProcessingPipeline:
    """按 ``STAGE_SEQUENCE`` 逐阶段推进一次运行。"""

    def __init__(
        self,
        runs: ProcessingRunRepository,
        steps: ProcessingStepRepository,
        worker: ProcessingWorkerUseCases,
        handlers: Mapping[ProcessingStage, StageHandler],
        uow: UnitOfWork,
        *,
        artifacts: ProcessingArtifactRepository | None = None,
    ) -> None:
        self._runs = runs
        self._steps = steps
        self._worker = worker
        self._handlers = handlers
        self._uow = uow
        #: 产物目录（V31-016）。为 ``None`` 时阶段产物只进 ``output_refs``，
        #: 用于只验证编排语义的测试；生产装配必须显式注入。
        self._artifacts = artifacts

    def execute(
        self,
        scope: ProjectScope,
        run_id: UUID,
        *,
        now: datetime | None = None,
        request_id: str | None = None,
    ) -> PipelineOutcome:
        moment = now or datetime.now(UTC)
        run = self._runs.get(scope, run_id)
        if run is None:
            raise ResourceNotFoundError(_NOT_FOUND_MESSAGE)
        if run.is_terminal:
            return PipelineOutcome(
                status=PipelineOutcomeStatus.NOOP.value,
                run=run,
                reason=f"运行已处于终态 {run.status}",
            )

        try:
            run = self._prepare(scope, run, now=moment, request_id=request_id)
        except (VersionConflictError, InvalidStateTransitionError) as exc:
            # 另一 Worker 已把运行推进到终态，或客户端刚取消：本事件无需重试。
            raise ConcurrentRunUpdate(str(exc)) from exc

        while True:
            stage = self._current_stage(run)
            step = self._begin_step(scope, run, stage, now=moment)
            logger.info(
                "processing.stage.started",
                extra={
                    "run_id": str(run.run_id),
                    "project_id": str(run.project_id),
                    "stage": stage.value,
                    "attempt": run.attempt,
                },
            )
            try:
                outcome = self._handlers[stage](StageContext(run=run, scope=scope, now=moment))
            except StageFailure as failure:
                return self._fail_stage(
                    scope,
                    run,
                    step,
                    stage,
                    error_code=failure.error_code,
                    summary=failure.summary,
                    retryable=failure.retryable,
                    now=moment,
                    request_id=request_id,
                )
            except Exception as exc:  # noqa: BLE001 - 阶段边界：记录后失败，不吞
                logger.exception(
                    "处理阶段执行异常",
                    extra={"run_id": str(run.run_id), "stage": stage.value},
                )
                return self._fail_stage(
                    scope,
                    run,
                    step,
                    stage,
                    error_code="stage_execution_failed",
                    summary=str(exc),
                    now=moment,
                    request_id=request_id,
                )

            next_stage = self._next_stage(stage)
            try:
                if next_stage is None:
                    self._finish_step(scope, step, outcome, now=moment)
                    run = self._worker.succeed(
                        scope,
                        run.run_id,
                        expected_version=run.row_version,
                        output_refs=outcome.output_refs,
                        now=moment,
                    )
                    logger.info(
                        "processing.run.succeeded",
                        extra={"run_id": str(run.run_id), "stage": stage.value},
                    )
                    return PipelineOutcome(
                        status=PipelineOutcomeStatus.SUCCEEDED.value,
                        run=run,
                    )
                # 步骤结果与运行推进必须同一事务提交：否则会出现"步骤说成功、
                # 运行还停在上一阶段"的可观察性裂缝。
                self._finish_step(scope, step, outcome, now=moment)
                run = self._worker.advance(
                    scope,
                    run.run_id,
                    next_stage.value,
                    expected_version=run.row_version,
                    output_refs=outcome.output_refs,
                    now=moment,
                )
            except VersionConflictError as exc:
                self._uow.rollback()
                self._abort_step(scope, run, stage, now=moment)
                raise ConcurrentRunUpdate(str(exc)) from exc
            except InvalidStateTransitionError as exc:
                # 客户端在阶段执行期间取消：步骤没有结论，但也不该重试。
                self._uow.rollback()
                self._abort_step(scope, run, stage, now=moment)
                raise ConcurrentRunUpdate(str(exc)) from exc
            logger.info(
                "processing.stage.finished",
                extra={
                    "run_id": str(run.run_id),
                    "stage": stage.value,
                    "status": outcome.status,
                    "next_stage": next_stage.value,
                },
            )

    def _prepare(
        self,
        scope: ProjectScope,
        run: ProcessingRun,
        *,
        now: datetime,
        request_id: str | None = None,
    ) -> ProcessingRun:
        """把运行推进到可执行的 RUNNING；崩溃恢复沿用当前阶段与 attempt。"""
        if run.status == ProcessingRunStatus.CREATED.value:
            run = self._worker.enqueue(scope, run.run_id, expected_version=run.row_version, now=now)
        if run.status == ProcessingRunStatus.FAILED.value:
            run = self._worker.retry(
                scope,
                run.run_id,
                expected_version=run.row_version,
                request_id=request_id,
                now=now,
            )
        if run.status in {
            ProcessingRunStatus.QUEUED.value,
            ProcessingRunStatus.RETRYING.value,
        }:
            return self._worker.claim(
                scope,
                run.run_id,
                expected_version=run.row_version,
                stage=run.stage,
                now=now,
            )
        if run.status == ProcessingRunStatus.RUNNING.value:
            return run
        raise ProcessingDomainError(f"运行状态 {run.status} 不可执行")

    def _current_stage(self, run: ProcessingRun) -> ProcessingStage:
        stage = run.stage or first_stage()
        return ProcessingStage(stage)

    def _begin_step(
        self,
        scope: ProjectScope,
        run: ProcessingRun,
        stage: ProcessingStage,
        *,
        now: datetime,
    ) -> ProcessingStep:
        existing = self._steps.get_by_key(
            scope,
            run_id=run.run_id,
            stage=stage.value,
            attempt=run.attempt,
        )
        if existing is not None:
            step = existing.restart(now=now, actor_id=scope.actor_id)
        else:
            step = ProcessingStep.starting(
                step_id=new_id(),
                project_id=run.project_id,
                run_id=run.run_id,
                stage=stage.value,
                attempt=run.attempt,
                now=now,
                actor_id=scope.actor_id,
            )
        self._steps.upsert(scope, step)
        # 先提交 RUNNING：阶段执行可能很慢甚至崩溃，步骤行必须立即可观察。
        self._uow.commit()
        return step

    def _finish_step(
        self,
        scope: ProjectScope,
        step: ProcessingStep,
        outcome: StageOutcome,
        *,
        now: datetime,
    ) -> None:
        finished = step.finish(
            status=outcome.status,
            now=now,
            log_summary=sanitize_log_message(outcome.log_summary, limit=STEP_LOG_MAX_LENGTH),
            actor_id=scope.actor_id,
        )
        self._steps.upsert(scope, finished)
        self._register_artifacts(scope, step, outcome, now=now)

    def _register_artifacts(
        self,
        scope: ProjectScope,
        step: ProcessingStep,
        outcome: StageOutcome,
        *,
        now: datetime,
    ) -> None:
        """把阶段产物登记进目录（V31-016）。

        与步骤收尾同事务：步骤说成功、目录却没有产物，会让"产物在哪"变成
        只能翻对象存储才能回答的问题。``(run, stage, attempt, kind, key)``
        唯一，重复执行同一阶段（崩溃恢复）会跳过已登记行而不是撞唯一键。
        """
        if self._artifacts is None or not outcome.artifacts:
            return
        existing = {
            (item.kind, item.object_key)
            for item in self._artifacts.list(
                scope,
                run_id=step.run_id,
                stage=step.stage,
                attempt=step.attempt,
            )
        }
        for description in outcome.artifacts:
            if (description.kind.value, description.object_key) in existing:
                continue
            self._artifacts.add(
                scope,
                ProcessingArtifact(
                    artifact_id=new_id(),
                    project_id=step.project_id,
                    run_id=step.run_id,
                    stage=step.stage,
                    attempt=step.attempt,
                    kind=description.kind.value,
                    object_key=description.object_key,
                    schema_version=description.schema_version,
                    content_type=description.content_type,
                    size_bytes=description.size_bytes,
                    checksum_sha256=description.checksum_sha256,
                    source_ref=description.source_ref,
                    created_at=now,
                    created_by=scope.actor_id,
                ),
            )

    def _fail_stage(
        self,
        scope: ProjectScope,
        run: ProcessingRun,
        step: ProcessingStep,
        stage: ProcessingStage,
        *,
        error_code: str,
        summary: str,
        retryable: bool = True,
        now: datetime,
        request_id: str | None = None,
    ) -> PipelineOutcome:
        safe_summary = sanitize_log_message(
            summary,
            limit=STEP_ERROR_SUMMARY_MAX_LENGTH,
        )
        try:
            failed_step = step.finish(
                status=ProcessingStepStatus.FAILED.value,
                now=now,
                log_summary=f"阶段失败：{safe_summary}",
                error_code=error_code,
                error_summary=safe_summary,
                actor_id=scope.actor_id,
            )
            self._steps.upsert(scope, failed_step)
            run = self._worker.fail(
                scope,
                run.run_id,
                expected_version=run.row_version,
                error_code=error_code,
                error_summary=safe_summary,
                request_id=request_id,
                now=now,
            )
        except (VersionConflictError, InvalidStateTransitionError) as exc:
            self._uow.rollback()
            self._abort_step(scope, run, stage, now=now)
            raise ConcurrentRunUpdate(str(exc)) from exc
        logger.warning(
            "processing.stage.failed",
            extra={
                "run_id": str(run.run_id),
                "stage": stage.value,
                "error_code": error_code,
            },
        )
        return PipelineOutcome(
            status=PipelineOutcomeStatus.FAILED.value,
            run=run,
            failed_stage=stage.value,
            error_code=error_code,
            error_summary=safe_summary,
            retryable=retryable,
        )

    def _abort_step(
        self,
        scope: ProjectScope,
        run: ProcessingRun,
        stage: ProcessingStage,
        *,
        now: datetime,
    ) -> None:
        """运行被并发推进/取消时收口残留 RUNNING 步骤，避免永久"进行中"。"""
        try:
            existing = self._steps.get_by_key(
                scope,
                run_id=run.run_id,
                stage=stage.value,
                attempt=run.attempt,
            )
            if existing is None or existing.is_terminal:
                return
            aborted = existing.finish(
                status=ProcessingStepStatus.FAILED.value,
                now=now,
                log_summary="运行被并发推进或取消，阶段中止",
                error_code="run_state_changed",
                error_summary="运行被并发推进或取消，阶段中止",
                actor_id=scope.actor_id,
            )
            self._steps.upsert(scope, aborted)
            self._uow.commit()
        except Exception:  # noqa: BLE001 - 收口失败不得掩盖原始并发冲突
            logger.exception(
                "收口残留阶段失败",
                extra={"run_id": str(run.run_id), "stage": stage.value},
            )
            self._uow.rollback()

    @staticmethod
    def _next_stage(stage: ProcessingStage) -> ProcessingStage | None:
        index = stage_index(stage.value) + 1
        if index >= len(STAGE_SEQUENCE):
            return None
        return STAGE_SEQUENCE[index]


__all__ = [
    "STAGE_SKIP_REASONS",
    "ConcurrentRunUpdate",
    "FileValidationHandler",
    "PipelineOutcome",
    "PipelineOutcomeStatus",
    "ProcessingPipeline",
    "SkippedStage",
    "StageContext",
    "StageFailure",
    "StageHandler",
    "StageOutcome",
    "default_handlers",
]
