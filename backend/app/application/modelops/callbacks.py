"""模型平台回调处理：事件幂等、nonce 防重放与结果分发。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.application.modelops.evaluations import EvaluationUseCases
from app.application.modelops.inference import InferenceTaskUseCases
from app.application.modelops.ports import (
    CallbackRecord,
    CallbackRepository,
    UnitOfWork,
)
from app.domain.modelops.canonical import sha256_value
from app.domain.modelops.errors import (
    DomainValidationError,
    ModelCallbackReplayedError,
    ResourceNotFoundError,
)


@dataclass(frozen=True, slots=True)
class CallbackRecordData:
    callback_id: UUID
    adapter_id: str
    event_id: str
    nonce: str
    payload_sha256: str
    received_at: datetime


class ModelCallbackUseCases:
    def __init__(
        self,
        *,
        callbacks: CallbackRepository,
        evaluations: EvaluationUseCases,
        tasks: InferenceTaskUseCases,
        uow: UnitOfWork,
        nonce_ttl_seconds: int,
    ) -> None:
        self._callbacks = callbacks
        self._evaluations = evaluations
        self._tasks = tasks
        self._uow = uow
        self._nonce_ttl = nonce_ttl_seconds

    def handle(
        self,
        *,
        adapter_id: str,
        nonce: str,
        event_id: str,
        event_type: str,
        project_id: UUID,
        evaluation_id: UUID | None,
        task_id: UUID | None,
        metrics: dict[str, float] | None,
        report_ref: dict[str, object] | None,
        result_ref: dict[str, object] | None,
        log_ref: str | None,
        error_code: str | None,
        error_summary: str | None,
        now: datetime | None = None,
    ) -> str:
        now = now or datetime.now(UTC)
        payload_sha = sha256_value(
            {
                "event_type": event_type,
                "project_id": str(project_id),
                "evaluation_id": str(evaluation_id) if evaluation_id else None,
                "task_id": str(task_id) if task_id else None,
                "metrics": metrics,
                "report_ref": report_ref,
                "result_ref": result_ref,
                "log_ref": log_ref,
                "error_code": error_code,
                "error_summary": error_summary,
            }
        )
        existing = self._callbacks.get_by_event(adapter_id, event_id)
        if existing is not None:
            if existing.payload_sha256 != payload_sha:
                raise ModelCallbackReplayedError("同一 event_id 对应不同回调内容")
            return "IDEMPOTENT"
        if self._callbacks.get_by_nonce(adapter_id, nonce) is not None:
            raise ModelCallbackReplayedError("nonce 在时间窗内已被使用")
        self._callbacks.add(
            CallbackRecordData(
                callback_id=uuid4(),
                adapter_id=adapter_id,
                event_id=event_id,
                nonce=nonce,
                payload_sha256=payload_sha,
                received_at=now,
            )
        )
        if event_type in {"evaluation.completed", "evaluation.failed"}:
            if evaluation_id is None:
                raise DomainValidationError("评测回调缺少 evaluation_id")
            evaluation = self._evaluations.get(_scope(project_id), evaluation_id)
            self._evaluations.complete_from_callback(
                project_id=evaluation.project_id,
                evaluation_id=evaluation_id,
                metrics=metrics or {},
                report_ref=report_ref,
                error_code=error_code
                or ("adapter_reported_failure" if event_type.endswith("failed") else None),
                error_summary=error_summary,
                now=now,
            )
        elif event_type in {"inference.completed", "inference.failed"}:
            if task_id is None:
                raise DomainValidationError("推理回调缺少 task_id")
            task = self._tasks.get(_scope(project_id), task_id)
            self._tasks.complete_from_callback(
                project_id=task.project_id,
                task_id=task_id,
                result_ref=result_ref,
                metrics=metrics,
                log_ref=log_ref,
                error_code=error_code
                or ("adapter_reported_failure" if event_type.endswith("failed") else None),
                error_summary=error_summary,
                now=now,
            )
        else:
            raise DomainValidationError("不支持的模型回调事件类型")
        self._callbacks.prune_before(received_before=now - timedelta(seconds=self._nonce_ttl))
        self._uow.commit()
        return "UPDATED"


def _scope(project_id: UUID):
    from app.domain.identity.models import ProjectScope

    return ProjectScope(project_id=project_id, actor_id=project_id)


__all__ = ["CallbackRecord", "ModelCallbackUseCases", "ResourceNotFoundError"]
