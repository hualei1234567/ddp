"""ProcessingRun 的阶段执行记录（N4-002）。

一个 :class:`ProcessingStep` 是"某次执行尝试中某个阶段"的不可变快照：

- 唯一键语义 ``(run_id, stage, attempt)``：同一运行、同一阶段、同一尝试
  只有一行；重试会换 ``attempt``，因此历史失败原因不会被覆盖；
- ``RUNNING`` 是唯一可变（非终态）状态；``SUCCEEDED`` / ``FAILED`` /
  ``SKIPPED`` 都是终态，必须带 ``finished_at``；
- ``SKIPPED`` 必须带非空 ``log_summary``——"为什么跳过"是这条记录存在的
  全部意义，不允许出现没有原因的跳过；
- ``FAILED`` 必须同时带 ``error_code`` 与 ``error_summary``，其余状态不得
  携带错误信息（与运行实体同一口径）。

领域层不依赖 ORM/队列；持久化由仓储把本对象映射为表行。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.domain.processing.enums import (
    PROCESSING_STEP_TERMINAL_STATUSES,
    ProcessingStepStatus,
)
from app.domain.processing.errors import DomainValidationError
from app.domain.processing.state_machines import coerce_stage
from app.domain.processing.validation import (
    optional_aware,
    optional_text,
    positive_int,
    required_uuid,
)

STEP_LOG_MAX_LENGTH = 1000
STEP_ERROR_CODE_MAX_LENGTH = 64
STEP_ERROR_SUMMARY_MAX_LENGTH = 2000


def _coerce_step_status(value: object) -> str:
    try:
        return ProcessingStepStatus(value).value  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in ProcessingStepStatus)
        raise DomainValidationError(f"step status 取值非法，允许值：{allowed}") from exc


@dataclass(frozen=True, slots=True)
class ProcessingStep:
    step_id: UUID
    project_id: UUID
    run_id: UUID
    stage: str
    attempt: int
    status: str = ProcessingStepStatus.RUNNING.value
    log_summary: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", required_uuid(self.step_id, "step_id"))
        object.__setattr__(self, "project_id", required_uuid(self.project_id, "project_id"))
        object.__setattr__(self, "run_id", required_uuid(self.run_id, "run_id"))
        object.__setattr__(self, "stage", coerce_stage(self.stage))
        object.__setattr__(self, "attempt", positive_int(self.attempt, "attempt"))
        object.__setattr__(self, "status", _coerce_step_status(self.status))
        object.__setattr__(
            self,
            "log_summary",
            optional_text(self.log_summary, "log_summary", max_length=STEP_LOG_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "error_code",
            optional_text(
                self.error_code,
                "error_code",
                max_length=STEP_ERROR_CODE_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "error_summary",
            optional_text(
                self.error_summary,
                "error_summary",
                max_length=STEP_ERROR_SUMMARY_MAX_LENGTH,
            ),
        )
        object.__setattr__(self, "started_at", optional_aware(self.started_at, "started_at"))
        object.__setattr__(self, "finished_at", optional_aware(self.finished_at, "finished_at"))
        object.__setattr__(self, "created_at", optional_aware(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", optional_aware(self.updated_at, "updated_at"))
        object.__setattr__(self, "created_by", self._optional_uuid(self.created_by, "created_by"))
        object.__setattr__(self, "updated_by", self._optional_uuid(self.updated_by, "updated_by"))
        self._validate_invariants()

    @staticmethod
    def _optional_uuid(value: object, field_name: str) -> UUID | None:
        if value is None:
            return None
        return required_uuid(value, field_name)

    def _validate_invariants(self) -> None:
        if self.started_at is None:
            raise DomainValidationError("started_at 不能为空")
        status = self.status
        if status == ProcessingStepStatus.RUNNING.value:
            if self.finished_at is not None:
                raise DomainValidationError("RUNNING 不得携带 finished_at")
        else:
            if self.finished_at is None:
                raise DomainValidationError(f"{status} 必须有 finished_at")
            if self.finished_at < self.started_at:
                raise DomainValidationError("finished_at 不得早于 started_at")
        if status == ProcessingStepStatus.FAILED.value:
            if self.error_code is None or self.error_summary is None:
                raise DomainValidationError("FAILED 必须同时记录 error_code 与 error_summary")
        elif self.error_code is not None or self.error_summary is not None:
            raise DomainValidationError("只有 FAILED 状态可以携带错误信息")
        if status == ProcessingStepStatus.SKIPPED.value and self.log_summary is None:
            raise DomainValidationError("SKIPPED 必须记录跳过原因（log_summary）")
        if status == ProcessingStepStatus.RUNNING.value and self.log_summary is not None:
            raise DomainValidationError("RUNNING 阶段尚无结论，不得提前写入 log_summary")

    @property
    def is_terminal(self) -> bool:
        return self.status in PROCESSING_STEP_TERMINAL_STATUSES

    @classmethod
    def starting(
        cls,
        *,
        step_id: UUID,
        project_id: UUID,
        run_id: UUID,
        stage: str,
        attempt: int,
        now: datetime,
        actor_id: UUID,
    ) -> ProcessingStep:
        """新建一个 RUNNING 步骤；阶段合法性由 :func:`coerce_stage` 保证。"""
        return cls(
            step_id=step_id,
            project_id=project_id,
            run_id=run_id,
            stage=stage,
            attempt=attempt,
            status=ProcessingStepStatus.RUNNING.value,
            started_at=now,
            created_at=now,
            updated_at=now,
            created_by=actor_id,
            updated_by=actor_id,
        )

    def restart(self, *, now: datetime, actor_id: UUID) -> ProcessingStep:
        """崩溃恢复：把同一 ``(run, stage, attempt)`` 的残留行重置为 RUNNING。

        只允许重置**未终态**的步骤；已成功的步骤不得回退重跑（阶段只前进）。
        """
        if self.is_terminal:
            raise DomainValidationError("已终态的步骤不得重新开始")
        return replace(
            self,
            status=ProcessingStepStatus.RUNNING.value,
            log_summary=None,
            error_code=None,
            error_summary=None,
            started_at=now,
            finished_at=None,
            updated_at=now,
            updated_by=actor_id,
        )

    def finish(
        self,
        *,
        status: object,
        now: datetime,
        log_summary: str | None,
        error_code: str | None = None,
        error_summary: str | None = None,
        actor_id: UUID,
    ) -> ProcessingStep:
        """把 RUNNING 步骤落到终态；非法状态或字段组合一律拒绝。"""
        if self.status != ProcessingStepStatus.RUNNING.value:
            raise DomainValidationError("只有 RUNNING 步骤可以结束")
        target = _coerce_step_status(status)
        if target not in PROCESSING_STEP_TERMINAL_STATUSES:
            raise DomainValidationError(f"步骤终态必须是终态集合之一，收到 {target}")
        return replace(
            self,
            status=target,
            log_summary=log_summary,
            error_code=error_code,
            error_summary=error_summary,
            finished_at=now,
            updated_at=now,
            updated_by=actor_id,
        )


__all__ = [
    "STEP_ERROR_CODE_MAX_LENGTH",
    "STEP_ERROR_SUMMARY_MAX_LENGTH",
    "STEP_LOG_MAX_LENGTH",
    "ProcessingStep",
]
