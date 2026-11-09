"""模型平台 Adapter 回调路由：HMAC 验签、时间窗与 nonce 防重放。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response
from pydantic import ValidationError

from app.api.v1.modelops_dependencies import ModelOpsServicesDep
from app.api.v1.modelops_schemas import ModelAdapterCallbackPayload
from app.api.v1.upload_dependencies import SettingsDep
from app.core.errors import ValidationFailedError
from app.domain.modelops.callback_security import verify_callback_signature

router = APIRouter(prefix="/integrations/model-adapters", tags=["model-adapters"])


@router.post("/{adapter_id}/callbacks", status_code=204)
async def receive_model_adapter_callback(
    adapter_id: str,
    request: Request,
    services: ModelOpsServicesDep,
    settings: SettingsDep,
    signature: Annotated[str | None, Header(alias="X-DDP-Signature")] = None,
    timestamp: Annotated[str | None, Header(alias="X-DDP-Timestamp")] = None,
    nonce: Annotated[str | None, Header(alias="X-DDP-Nonce")] = None,
) -> Response:
    body = await request.body()
    if len(body) > 1024 * 1024:
        raise ValidationFailedError("回调请求体过大")
    try:
        payload = ModelAdapterCallbackPayload.model_validate_json(body)
    except (ValidationError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationFailedError("回调请求体非法") from exc
    secret = settings.model_adapter_shared_secrets.get(adapter_id)
    normalized_nonce = verify_callback_signature(
        secret=secret,
        timestamp=timestamp,
        nonce=nonce,
        signature=signature,
        body=body,
        now=datetime.now(UTC),
        window_seconds=settings.model_callback_time_window_seconds,
    )
    services.callbacks.handle(
        adapter_id=adapter_id,
        nonce=normalized_nonce,
        event_id=payload.event_id,
        event_type=payload.event_type,
        project_id=payload.project_id,
        evaluation_id=payload.evaluation_id,
        task_id=payload.task_id,
        metrics=payload.metrics,
        report_ref=payload.report_ref,
        result_ref=payload.result_ref,
        log_ref=payload.log_ref,
        error_code=payload.error_code,
        error_summary=payload.error_summary,
    )
    return Response(status_code=204)


__all__ = ["router"]
