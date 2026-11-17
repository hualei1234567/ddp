"""后台任务编排：状态流转与签名回调。

HTTP 请求线程只负责登记与投递；真实数据读取、ffmpeg 抽帧与训练都在线程池里。
所有可预期失败都回调 FAILED（模型侧为 evaluation.failed / inference.failed），
并带 error_code/error_summary，不静默。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from uuid import UUID

from app.callbacks import (
    CallbackResult,
    CallbackSink,
    model_callback_payload,
    training_callback_payload,
)
from app.config import Settings
from app.errors import AdapterError, BlockedError, JobCancelledError, JobTimedOutError
from app.evaluation import run_evaluation, run_inference
from app.export_dataset import resolve_export
from app.runs import RunRegistry, now_iso
from app.storage import ObjectStorage
from app.training import run_training

logger = logging.getLogger("ddp.local_adapter.tasks")


@dataclass
class JobContext:
    run_id: str
    kind: str
    storage: ObjectStorage
    settings: Settings
    registry: RunRegistry
    callbacks: CallbackSink
    cancel_event: object
    deadline_monotonic: float = field(default_factory=lambda: time.monotonic() + 1800)

    def check_cancel(self) -> None:
        if getattr(self.cancel_event, "is_set", lambda: False)():
            raise JobCancelledError()
        if time.monotonic() > self.deadline_monotonic:
            raise JobTimedOutError()


def _record_callback(ctx: JobContext, result: CallbackResult) -> None:
    ctx.registry.append_callback(
        ctx.run_id,
        {
            "label": result.label,
            "ok": result.ok,
            "attempts": result.attempts,
            "status_code": result.status_code,
            "error": result.error,
            "at": now_iso(),
        },
    )


def _send_training(
    ctx: JobContext,
    *,
    status: str,
    model_ref: dict[str, object] | None = None,
    error_code: str | None = None,
    error_summary: str | None = None,
    payload: dict[str, object] | None = None,
) -> None:
    record = ctx.registry.get(ctx.run_id)
    adapter_version = (
        record.adapter_version if record else ctx.settings.training_adapter_version
    )
    body = training_callback_payload(
        adapter_id=ctx.settings.training_adapter_id,
        adapter_version=adapter_version,
        external_run_id=ctx.run_id,
        status=status,
        model_ref=model_ref,
        error_code=error_code,
        error_summary=error_summary,
        payload=payload,
    )
    try:
        result = ctx.callbacks.send_training(body, label=f"training:{status}")
    except Exception as exc:  # 回调客户端异常不能掩盖任务状态
        logger.exception("训练回调发送异常 run=%s status=%s", ctx.run_id, status)
        ctx.registry.append_callback(
            ctx.run_id,
            {
                "label": f"training:{status}",
                "ok": False,
                "error": str(exc),
                "at": now_iso(),
            },
        )
        return
    _record_callback(ctx, result)


def _send_model(
    ctx: JobContext,
    *,
    event_type: str,
    project_id: str | None,
    evaluation_id: UUID | None = None,
    task_id: UUID | None = None,
    metrics: dict[str, float] | None = None,
    report_ref: dict[str, object] | None = None,
    result_ref: dict[str, object] | None = None,
    log_ref: str | None = None,
    error_code: str | None = None,
    error_summary: str | None = None,
) -> None:
    if not project_id:
        logger.error(
            "模型回调缺少 project_id，未发送 run=%s %s", ctx.run_id, event_type
        )
        ctx.registry.append_callback(
            ctx.run_id,
            {
                "label": event_type,
                "ok": False,
                "error": "project_id_unknown：无法构造合法回调",
                "at": now_iso(),
            },
        )
        return
    record = ctx.registry.get(ctx.run_id)
    adapter_version = (
        record.adapter_version if record else ctx.settings.model_adapter_version
    )
    body = model_callback_payload(
        adapter_id=ctx.settings.model_adapter_id,
        adapter_version=adapter_version,
        event_type=event_type,
        project_id=project_id,
        evaluation_id=evaluation_id,
        task_id=task_id,
        metrics=metrics,
        report_ref=report_ref,
        result_ref=result_ref,
        log_ref=log_ref,
        error_code=error_code,
        error_summary=error_summary,
    )
    try:
        result = ctx.callbacks.send_model(body, label=event_type)
    except Exception as exc:
        logger.exception("模型回调发送异常 run=%s event=%s", ctx.run_id, event_type)
        ctx.registry.append_callback(
            ctx.run_id,
            {"label": event_type, "ok": False, "error": str(exc), "at": now_iso()},
        )
        return
    _record_callback(ctx, result)


def _failure(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, AdapterError):
        return exc.code, exc.summary
    logger.exception("未预期异常 run 失败")
    message = f"{type(exc).__name__}: {exc}"
    return "adapter_internal_error", message[:500]


def _mark_running(ctx: JobContext) -> None:
    ctx.registry.update(ctx.run_id, status="RUNNING", started_at=now_iso())


def _mark_finished(
    ctx: JobContext,
    *,
    status: str,
    metrics: dict[str, float] | None = None,
    artifacts: dict[str, object] | None = None,
    resolution: dict[str, object] | None = None,
    error_code: str | None = None,
    error_summary: str | None = None,
    project_id: str | None = None,
    export_job_id: str | None = None,
    export_prefix: str | None = None,
) -> None:
    fields: dict[str, object] = {"status": status, "finished_at": now_iso()}
    if project_id:
        fields["project_id"] = project_id
    if export_job_id:
        fields["export_job_id"] = export_job_id
    if export_prefix:
        fields["export_prefix"] = export_prefix
    if metrics is not None:
        fields["metrics"] = metrics
    if artifacts is not None:
        fields["artifacts"] = artifacts
    if resolution is not None:
        fields["resolution"] = resolution
    if error_code is not None:
        fields["error_code"] = error_code
    if error_summary is not None:
        fields["error_summary"] = error_summary
    ctx.registry.update(ctx.run_id, **fields)


def execute_training(
    ctx: JobContext,
    *,
    delivery_id: UUID,
    dataset_version_id: UUID,
    config: dict[str, object],
) -> None:
    _mark_running(ctx)
    _send_training(ctx, status="RUNNING")
    try:
        outcome = run_training(
            storage=ctx.storage,
            settings=ctx.settings,
            run_id=ctx.run_id,
            delivery_id=delivery_id,
            dataset_version_id=dataset_version_id,
            config=config,
            check_cancel=ctx.check_cancel,
        )
        resolution = {
            "project_id": str(outcome.layout.project_id),
            "dataset_version_id": str(outcome.layout.dataset_version_id),
            "export_job_id": outcome.layout.export_job_id,
            "export_prefix": outcome.layout.prefix,
            "export_resolution": outcome.layout.resolution,
        }
        _mark_finished(
            ctx,
            status="SUCCEEDED",
            metrics=outcome.metrics,
            artifacts=outcome.detail,
            resolution=resolution,
            project_id=str(outcome.layout.project_id),
            export_job_id=outcome.layout.export_job_id,
            export_prefix=outcome.layout.prefix,
        )
        _send_training(
            ctx,
            status="SUCCEEDED",
            model_ref=outcome.model_ref,
            payload={
                "metrics": outcome.metrics,
                "model": outcome.detail["model"],
                "dataset": outcome.detail["layout"],
                "classes": outcome.detail["classes"],
            },
        )
    except BlockedError as exc:
        logger.warning("训练 BLOCKED run=%s code=%s", ctx.run_id, exc.code)
        _mark_finished(
            ctx, status="BLOCKED", error_code=exc.code, error_summary=exc.summary
        )
        _send_training(
            ctx, status="FAILED", error_code=exc.code, error_summary=exc.summary
        )
    except JobCancelledError as exc:
        _mark_finished(
            ctx, status="CANCELLED", error_code=exc.code, error_summary=exc.summary
        )
        _send_training(
            ctx, status="CANCELLED", error_code=exc.code, error_summary=exc.summary
        )
    except BaseException as exc:  # noqa: BLE001 - 全部转为显式失败回调
        code, summary = _failure(exc)
        _mark_finished(ctx, status="FAILED", error_code=code, error_summary=summary)
        _send_training(ctx, status="FAILED", error_code=code, error_summary=summary)


def execute_evaluation(
    ctx: JobContext,
    *,
    evaluation_id: UUID,
    model_version_id: UUID,
    dataset_version_id: UUID,
    config: dict[str, object],
) -> None:
    _mark_running(ctx)
    project_id = _project_for_dataset(
        ctx, dataset_version_id=dataset_version_id, config=config
    )
    try:
        outcome = run_evaluation(
            storage=ctx.storage,
            settings=ctx.settings,
            evaluation_id=evaluation_id,
            model_version_id=model_version_id,
            dataset_version_id=dataset_version_id,
            config=config,
            check_cancel=ctx.check_cancel,
        )
        project_id = str(outcome.layout.project_id)
        resolution = {
            "project_id": project_id,
            "export_job_id": outcome.layout.export_job_id,
            "export_prefix": outcome.layout.prefix,
            "export_resolution": outcome.layout.resolution,
            "model_object_key": outcome.model.object_key,
            "model_resolution": outcome.model.resolution,
        }
        _mark_finished(
            ctx,
            status="SUCCEEDED",
            metrics=outcome.metrics,
            artifacts=outcome.detail,
            resolution=resolution,
            project_id=project_id,
            export_job_id=outcome.layout.export_job_id,
            export_prefix=outcome.layout.prefix,
        )
        _send_model(
            ctx,
            event_type="evaluation.completed",
            project_id=project_id,
            evaluation_id=evaluation_id,
            metrics=outcome.metrics,
            report_ref={
                "object_key": outcome.report_key,
                "sha256": f"sha256:{outcome.report_sha256}",
                "size_bytes": outcome.report_size_bytes,
                "content_type": "application/json",
            },
        )
    except JobCancelledError as exc:
        _mark_finished(
            ctx, status="CANCELLED", error_code=exc.code, error_summary=exc.summary
        )
        _send_model(
            ctx,
            event_type="evaluation.failed",
            project_id=project_id,
            evaluation_id=evaluation_id,
            error_code=exc.code,
            error_summary=exc.summary,
        )
    except BaseException as exc:  # noqa: BLE001
        code, summary = _failure(exc)
        _mark_finished(ctx, status="FAILED", error_code=code, error_summary=summary)
        _send_model(
            ctx,
            event_type="evaluation.failed",
            project_id=project_id,
            evaluation_id=evaluation_id,
            error_code=code,
            error_summary=summary,
        )


def execute_inference(
    ctx: JobContext,
    *,
    task_id: UUID,
    model_version_id: UUID,
    service_id: UUID | None,
    dataset_version_id: UUID | None,
    episode_ids: list[UUID],
    config: dict[str, object],
) -> None:
    _mark_running(ctx)
    config_dataset = config.get("dataset_version_id")
    discovery_dataset = dataset_version_id
    if discovery_dataset is None and isinstance(config_dataset, str) and config_dataset:
        try:
            discovery_dataset = UUID(config_dataset)
        except ValueError:
            discovery_dataset = None
    project_id = (
        _project_for_dataset(ctx, dataset_version_id=discovery_dataset, config=config)
        if discovery_dataset is not None
        else _configured_project(config)
    )
    try:
        outcome = run_inference(
            storage=ctx.storage,
            settings=ctx.settings,
            task_id=task_id,
            model_version_id=model_version_id,
            service_id=service_id,
            dataset_version_id=dataset_version_id,
            episode_ids=episode_ids,
            config=config,
            check_cancel=ctx.check_cancel,
        )
        project_id = str(outcome.layout.project_id)
        resolution = {
            "project_id": project_id,
            "export_job_id": outcome.layout.export_job_id,
            "export_prefix": outcome.layout.prefix,
            "export_resolution": outcome.layout.resolution,
            "model_object_key": outcome.model.object_key,
            "model_resolution": outcome.model.resolution,
        }
        _mark_finished(
            ctx,
            status="SUCCEEDED",
            metrics=outcome.metrics,
            artifacts=outcome.detail,
            resolution=resolution,
            project_id=project_id,
            export_job_id=outcome.layout.export_job_id,
            export_prefix=outcome.layout.prefix,
        )
        _send_model(
            ctx,
            event_type="inference.completed",
            project_id=project_id,
            task_id=task_id,
            metrics=outcome.metrics,
            result_ref={
                "object_key": outcome.predictions_key,
                "sha256": f"sha256:{outcome.predictions_sha256}",
                "size_bytes": outcome.predictions_size_bytes,
                "row_count": outcome.row_count,
                "content_type": "application/x-ndjson",
            },
            log_ref=f"s3://{ctx.storage.bucket}/{outcome.predictions_key}",
        )
    except JobCancelledError as exc:
        _mark_finished(
            ctx, status="CANCELLED", error_code=exc.code, error_summary=exc.summary
        )
        _send_model(
            ctx,
            event_type="inference.failed",
            project_id=project_id,
            task_id=task_id,
            error_code=exc.code,
            error_summary=exc.summary,
        )
    except BaseException as exc:  # noqa: BLE001
        code, summary = _failure(exc)
        _mark_finished(ctx, status="FAILED", error_code=code, error_summary=summary)
        _send_model(
            ctx,
            event_type="inference.failed",
            project_id=project_id,
            task_id=task_id,
            error_code=code,
            error_summary=summary,
        )


def _configured_project(config: dict[str, object]) -> str | None:
    value = config.get("project_id")
    if value in (None, ""):
        return None
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _project_for_dataset(
    ctx: JobContext,
    *,
    dataset_version_id: UUID,
    config: dict[str, object],
) -> str | None:
    """尽早确定 project_id，保证失败回调也能构造合法 body。"""
    configured = _configured_project(config)
    if configured:
        return configured
    try:
        layout = resolve_export(
            ctx.storage,
            ctx.settings,
            dataset_version_id=dataset_version_id,
            project_id=None,
            export_job_id=(
                str(config["export_job_id"])
                if isinstance(config.get("export_job_id"), str)
                else None
            ),
            explicit_prefix=(
                str(config["export_prefix"])
                if isinstance(config.get("export_prefix"), str)
                else None
            ),
        )
        return str(layout.project_id)
    except Exception:  # noqa: BLE001 - 诊断用；主流程会给出正式错误
        return None


__all__ = [
    "JobContext",
    "execute_evaluation",
    "execute_inference",
    "execute_training",
]
