"""本地真实 Adapter HTTP 入口（端口 9100）。

HTTP 线程只做参数校验与入队；训练/推理/评测在后台线程执行并发送签名回调。
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from app.config import Settings
from app.container import ServiceContainer
from app.runs import RUN_TERMINAL, RunRecord
from app.schemas import EvaluationRunRequest, InferenceRunRequest, TrainingRunRequest
from app.tasks import (
    JobContext,
    execute_evaluation,
    execute_inference,
    execute_training,
)

logger = logging.getLogger("ddp.local_adapter")


def _configure_logging(settings: Settings) -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _context(container: ServiceContainer, record: RunRecord) -> JobContext:
    return JobContext(
        run_id=record.run_id,
        kind=record.kind,
        storage=container.storage,
        settings=container.settings,
        registry=container.registry,
        callbacks=container.callbacks,
        cancel_event=record.cancel_event,
        deadline_monotonic=time.monotonic() + container.settings.run_timeout_seconds,
    )


def _require_adapter(expected: str, actual: str, kind: str) -> None:
    if actual != expected:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "unknown_adapter",
                    "message": f"{kind} adapter_id={actual!r} 与本服务配置 {expected!r} 不一致",
                }
            },
        )


def _queue_full() -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={
            "error": {
                "code": "adapter_queue_full",
                "message": "Adapter 队列已满，请稍后重试",
            }
        },
    )


_DDP_STATUS = {
    "QUEUED": "SUBMITTED",
    "RUNNING": "RUNNING",
    "SUCCEEDED": "SUCCEEDED",
    "FAILED": "FAILED",
    "BLOCKED": "FAILED",
    "CANCELLED": "CANCELLED",
    "REJECTED": "FAILED",
}


def _ddp_training_view(
    snapshot: dict[str, object], settings: Settings
) -> dict[str, object]:
    """DDP 训练网关只接受 ExternalTrainingStatus 枚举；内部 BLOCKED 映射为 FAILED。"""
    internal = str(snapshot.get("status", ""))
    view = dict(snapshot)
    view["status"] = _DDP_STATUS.get(internal, "UNKNOWN")
    view["internal_status"] = internal
    view["external_run_id"] = snapshot["run_id"]
    view["external_run_url"] = (
        f"{settings.public_base_url}/training-runs/{snapshot['run_id']}"
    )
    if view["status"] == "SUCCEEDED" and snapshot.get("artifacts", {}).get("model"):
        view["model_ref"] = {
            "model_id": f"local-adapter/{snapshot['run_id']}",
            "model_version": snapshot.get("adapter_version"),
            "registered_at": snapshot.get("finished_at") or snapshot.get("updated_at"),
        }
    return view


def create_app(container: ServiceContainer | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active = container or ServiceContainer.build(Settings.from_env())
        _configure_logging(active.settings)
        app.state.container = active
        if active.ffmpeg_info is None:
            logger.warning(
                "ffmpeg 不可用（%s）；训练/评测/推理会以 ffmpeg_unavailable 失败",
                active.settings.ffmpeg_path,
            )
        else:
            logger.info("local-adapter 启动 ffmpeg=%s", active.ffmpeg_info)
        try:
            yield
        finally:
            active.close()

    app = FastAPI(title="DDP Local Adapter", version="1.0.0", lifespan=lifespan)

    def active_container() -> ServiceContainer:
        return app.state.container

    @app.get("/health")
    def health() -> dict[str, object]:
        current = active_container()
        counts = current.registry.counts()
        return {
            "status": "ok",
            "service": "ddp-local-adapter",
            "adapters": {
                "training": {
                    "adapter_id": current.settings.training_adapter_id,
                    "adapter_version": current.settings.training_adapter_version,
                    "callback_secret_configured": bool(
                        current.settings.training_secret
                    ),
                },
                "model": {
                    "adapter_id": current.settings.model_adapter_id,
                    "adapter_version": current.settings.model_adapter_version,
                    "callback_secret_configured": bool(current.settings.model_secret),
                },
            },
            "ffmpeg": {
                "available": current.ffmpeg_info is not None,
                "version": current.ffmpeg_info,
            },
            "storage": {
                "bucket": current.storage.bucket,
                "endpoint": current.settings.s3_endpoint,
            },
            "queue": counts,
            "capabilities": {
                "training": ["submit", "status", "cancel", "callback", "model_ref"],
                "model": ["evaluate", "inference", "callback", "status", "cancel"],
            },
            "execution": "cpu_reference_frame_classifier",
        }

    @app.post("/training-runs")
    def create_training_run(payload: TrainingRunRequest) -> dict[str, object]:
        current = active_container()
        _require_adapter(
            current.settings.training_adapter_id, payload.adapter_id, "训练"
        )
        if payload.training_delivery_id is not None and payload.delivery_id is not None:
            if payload.training_delivery_id != payload.delivery_id:
                raise HTTPException(
                    status_code=422, detail="training_delivery_id 与 delivery_id 不一致"
                )
        delivery_id = payload.training_delivery_id or payload.delivery_id
        if delivery_id is None:
            raise HTTPException(status_code=422, detail="缺少 training_delivery_id")
        record = RunRecord(
            run_id=f"train-{uuid4().hex}",
            kind="training",
            adapter_id=payload.adapter_id,
            adapter_version=payload.adapter_version,
            correlation_id=payload.correlation_id,
            delivery_id=str(delivery_id),
            dataset_version_id=str(payload.dataset_version_id),
            config=dict(payload.config),
        )
        record.project_id = (
            str(payload.config["project_id"])
            if isinstance(payload.config.get("project_id"), str)
            else None
        )
        record.export_job_id = (
            str(payload.config["export_job_id"])
            if isinstance(payload.config.get("export_job_id"), str)
            else None
        )
        record.export_prefix = (
            str(payload.config["export_prefix"])
            if isinstance(payload.config.get("export_prefix"), str)
            else None
        )
        ctx = _context(current, record)
        accepted = current.submit(
            record,
            lambda: execute_training(
                ctx,
                delivery_id=delivery_id,
                dataset_version_id=payload.dataset_version_id,
                config=dict(payload.config),
            ),
        )
        if not accepted:
            raise _queue_full()
        return {
            "external_run_id": record.run_id,
            "external_run_url": f"{current.settings.public_base_url}/training-runs/{record.run_id}",
            "status": "QUEUED",
            "accepted": True,
        }

    @app.get("/training-runs/{run_id}")
    def get_training_run(run_id: str) -> dict[str, object]:
        current = active_container()
        snapshot = current.registry.snapshot(run_id)
        if snapshot is None or snapshot["kind"] != "training":
            raise HTTPException(status_code=404, detail="训练运行不存在")
        return _ddp_training_view(snapshot, current.settings)

    @app.post("/training-runs/{run_id}/cancel")
    def cancel_training_run(run_id: str) -> dict[str, object]:
        registry = active_container().registry
        record = registry.get(run_id)
        if record is None or record.kind != "training":
            raise HTTPException(status_code=404, detail="训练运行不存在")
        accepted, updated = registry.request_cancel(run_id)
        assert updated is not None
        snapshot = updated.to_dict()
        view = _ddp_training_view(snapshot, active_container().settings)
        view["cancel_requested"] = updated.cancel_requested
        view["accepted"] = accepted
        return view

    @app.post("/evaluations", status_code=202)
    def create_evaluation_run(payload: EvaluationRunRequest) -> dict[str, object]:
        current = active_container()
        _require_adapter(current.settings.model_adapter_id, payload.adapter_id, "模型")
        record = RunRecord(
            run_id=f"eval-{uuid4().hex}",
            kind="evaluation",
            adapter_id=payload.adapter_id,
            adapter_version=payload.adapter_version,
            evaluation_id=str(payload.evaluation_id),
            model_version_id=str(payload.model_version_id),
            dataset_version_id=str(payload.dataset_version_id),
            config=dict(payload.config),
        )
        record.project_id = (
            str(payload.config["project_id"])
            if isinstance(payload.config.get("project_id"), str)
            else None
        )
        ctx = _context(current, record)
        accepted = current.submit(
            record,
            lambda: execute_evaluation(
                ctx,
                evaluation_id=payload.evaluation_id,
                model_version_id=payload.model_version_id,
                dataset_version_id=payload.dataset_version_id,
                config=dict(payload.config),
            ),
        )
        if not accepted:
            raise _queue_full()
        return {"run_id": record.run_id, "status": "QUEUED", "accepted": True}

    @app.post("/inference-runs", status_code=202)
    @app.post("/inference-tasks", status_code=202, include_in_schema=False)
    def create_inference_run(payload: InferenceRunRequest) -> dict[str, object]:
        current = active_container()
        _require_adapter(current.settings.model_adapter_id, payload.adapter_id, "模型")
        record = RunRecord(
            run_id=f"infer-{uuid4().hex}",
            kind="inference",
            adapter_id=payload.adapter_id,
            adapter_version=payload.adapter_version,
            service_id=str(payload.service_id) if payload.service_id else None,
            task_id=str(payload.task_id),
            model_version_id=str(payload.model_version_id),
            dataset_version_id=(
                str(payload.dataset_version_id) if payload.dataset_version_id else None
            ),
            episode_ids=[str(item) for item in payload.episode_ids],
            config=dict(payload.config),
        )
        record.project_id = (
            str(payload.config["project_id"])
            if isinstance(payload.config.get("project_id"), str)
            else None
        )
        ctx = _context(current, record)
        accepted = current.submit(
            record,
            lambda: execute_inference(
                ctx,
                task_id=payload.task_id,
                model_version_id=payload.model_version_id,
                service_id=payload.service_id,
                dataset_version_id=payload.dataset_version_id,
                episode_ids=list(payload.episode_ids),
                config=dict(payload.config),
            ),
        )
        if not accepted:
            raise _queue_full()
        return {"run_id": record.run_id, "status": "QUEUED", "accepted": True}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        snapshot = active_container().registry.snapshot(run_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="运行不存在")
        return snapshot

    @app.get("/runs")
    def list_runs(status: str | None = None, limit: int = 20) -> dict[str, object]:
        current = active_container()
        limit = max(1, min(limit, 100))
        # 仅用于本地运维观察；生产应由 DDP 作为权威记录。
        items = current.registry.list_recent(limit)
        if status:
            items = [item for item in items if item["status"] == status.upper()]
        return {"items": items, "terminal_states": sorted(RUN_TERMINAL)}

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=9100)
