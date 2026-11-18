"""outbox 校验事件处理器：抢占、幂等、重试退避与死信。

一条事件一个事务：
- 成功：资产迁移、审计、``validated`` 事件与源事件 PUBLISHED 一起提交；
- 可重试失败：回滚业务，另起事务写 attempts/available_at；
- 永久失败或达到最大次数：回滚业务并置为 FAILED（死信）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from app.application.ingestion.ports import (
    ClaimedValidationEvent,
    UnitOfWork,
    ValidationEventQueue,
)
from app.application.ingestion.raw_asset_validation import (
    VALIDATION_EVENT_TYPE,
    RawAssetValidationService,
    ValidationOutcome,
)
from app.core.logging import bind_trace, trace_from_event_payload
from app.domain.ingestion.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
    ObjectStorageUnavailableError,
    ResourceImmutableError,
    ResourceNotFoundError,
)

LAST_ERROR_MAX_LENGTH = 2000


class InvalidValidationEventError(DomainValidationError):
    """事件载荷不可解析；属于永久错误，直接进入死信。"""


class ProcessingStatus(StrEnum):
    IDLE = "IDLE"
    PUBLISHED = "PUBLISHED"
    RETRIED = "RETRIED"
    DEAD_LETTERED = "DEAD_LETTERED"


@dataclass(frozen=True, slots=True)
class ProcessResult:
    status: ProcessingStatus
    event_id: UUID | None = None
    attempts: int = 0
    outcome: ValidationOutcome | None = None
    error: str | None = None


class ValidationEventProcessor:
    def __init__(
        self,
        validation: RawAssetValidationService,
        queue: ValidationEventQueue,
        uow: UnitOfWork,
        *,
        max_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
        chunk_size_bytes: int,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts 必须大于等于 1")
        if retry_base_seconds <= 0 or retry_max_seconds < retry_base_seconds:
            raise ValueError("重试退避参数非法")
        if chunk_size_bytes <= 0:
            raise ValueError("chunk_size_bytes 必须是正整数")
        self._validation = validation
        self._queue = queue
        self._uow = uow
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._chunk_size_bytes = chunk_size_bytes

    def claim_next(self, *, now: datetime | None = None) -> ClaimedValidationEvent | None:
        """抢占一条待处理的校验事件（不推进业务状态）。

        与 :meth:`process_claimed` 分开，是为了让 Worker 能在**事件处理前**
        绑定追踪上下文，并让处理结果日志与业务日志落在同一 ``trace_id`` 下。
        """
        return self._queue.claim_next(
            now=now or datetime.now(UTC),
            event_type=VALIDATION_EVENT_TYPE,
        )

    def process_next(self, *, now: datetime | None = None) -> ProcessResult:
        """抢占并处理一条事件；供测试与不关心日志上下文的调用方使用。"""
        occurred_at = now or datetime.now(UTC)
        event = self.claim_next(now=occurred_at)
        if event is None:
            return ProcessResult(status=ProcessingStatus.IDLE)
        with bind_trace(trace_from_event_payload(event.payload)) as trace:
            return self.process_claimed(
                event,
                trace_id=trace.trace_id,
                now=occurred_at,
            )

    def process_claimed(
        self,
        event: ClaimedValidationEvent,
        *,
        trace_id: str,
        now: datetime | None = None,
    ) -> ProcessResult:
        """处理一条已抢占事件；``trace_id`` 写入审计与后续事件。"""
        occurred_at = now or datetime.now(UTC)
        try:
            asset_id, actor_user_id, validation_job_id = _parse_payload(event)
            result = self._validation.validate(
                project_id=event.project_id,
                asset_id=asset_id,
                actor_user_id=actor_user_id,
                validation_job_id=validation_job_id,
                # 沿用请求/事件恢复的 trace_id（此前用 event_id 顶替，
                # 让「请求 → 事件 → 审计」无法用同一个 ID 串起来）。
                request_id=trace_id,
                chunk_size_bytes=self._chunk_size_bytes,
                now=occurred_at,
            )
            # 业务写入、审计与 outbox 均在同一事务；此处一起提交。
            self._queue.mark_published(event.event_id, now=occurred_at)
            self._uow.commit()
        except Exception as exc:  # noqa: BLE001 - 必须记录并分类所有失败
            # 审计/业务任一失败都整体回滚，源事件保持可重试或进入死信。
            self._uow.rollback()
            return self._handle_failure(event, exc, now=occurred_at)

        return ProcessResult(
            status=ProcessingStatus.PUBLISHED,
            event_id=event.event_id,
            attempts=event.attempts,
            outcome=result.outcome,
        )

    def _handle_failure(
        self,
        event: ClaimedValidationEvent,
        exc: Exception,
        *,
        now: datetime,
    ) -> ProcessResult:
        attempts = event.attempts + 1
        error = _format_error(exc)
        permanent = _is_permanent(exc)
        if permanent or attempts >= self._max_attempts:
            self._queue.mark_dead_letter(
                event.event_id,
                attempts=attempts,
                last_error=error,
            )
            self._uow.commit()
            return ProcessResult(
                status=ProcessingStatus.DEAD_LETTERED,
                event_id=event.event_id,
                attempts=attempts,
                error=error,
            )

        delay_seconds = min(
            self._retry_base_seconds * (2 ** (attempts - 1)),
            self._retry_max_seconds,
        )
        self._queue.mark_retry(
            event.event_id,
            attempts=attempts,
            available_at=now + timedelta(seconds=delay_seconds),
            last_error=error,
        )
        self._uow.commit()
        return ProcessResult(
            status=ProcessingStatus.RETRIED,
            event_id=event.event_id,
            attempts=attempts,
            error=error,
        )


def _parse_payload(
    event: ClaimedValidationEvent,
) -> tuple[UUID, UUID | None, UUID | None]:
    asset_id = _required_uuid(event.payload.get("asset_id"), "asset_id")
    actor_user_id = _optional_uuid(event.payload.get("actor_user_id"), "actor_user_id")
    validation_job_id = _optional_uuid(
        event.payload.get("validation_job_id"),
        "validation_job_id",
    )
    if asset_id != event.aggregate_id:
        raise InvalidValidationEventError("asset_id 与 aggregate_id 不一致")
    return asset_id, actor_user_id, validation_job_id


def _required_uuid(value: object, field_name: str) -> UUID:
    parsed = _optional_uuid(value, field_name)
    if parsed is None:
        raise InvalidValidationEventError(f"事件缺少 {field_name}")
    return parsed


def _optional_uuid(value: object, field_name: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidValidationEventError(f"{field_name} 必须是 UUID 字符串")
    try:
        return UUID(value)
    except ValueError as exc:
        raise InvalidValidationEventError(f"{field_name} 格式非法") from exc


def _format_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}"
    return message[:LAST_ERROR_MAX_LENGTH]


def _is_permanent(exc: Exception) -> bool:
    if isinstance(exc, ObjectStorageUnavailableError):
        return False
    return isinstance(
        exc,
        InvalidValidationEventError
        | ResourceNotFoundError
        | DomainValidationError
        | InvalidStateTransitionError
        | ResourceImmutableError,
    )
