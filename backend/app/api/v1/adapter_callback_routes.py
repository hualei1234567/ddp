"""训练平台 Adapter 回调路由：HMAC 验签、时间窗、nonce 防重放与幂等。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response
from pydantic import ValidationError

from app.api.v1.training_dependencies import TrainingAdapterDep, TrainingServicesDep
from app.api.v1.training_schemas import AdapterCallbackPayload
from app.api.v1.upload_dependencies import SettingsDep
from app.core.errors import ValidationFailedError
from app.domain.datasets.adapter_callback import ensure_capabilities, verify_callback_signature
from app.domain.datasets.training_enums import AdapterCapability

router = APIRouter(prefix="/integrations/training-adapters", tags=["training-adapters"])


@router.post("/{adapter_id}/callbacks", status_code=204)
async def receive_adapter_callback(
    adapter_id: str,
    request: Request,
    services: TrainingServicesDep,
    gateway: TrainingAdapterDep,
    settings: SettingsDep,
    signature: Annotated[str | None, Header(alias="X-DDP-Signature")] = None,
    timestamp: Annotated[str | None, Header(alias="X-DDP-Timestamp")] = None,
    nonce: Annotated[str | None, Header(alias="X-DDP-Nonce")] = None,
) -> Response:
    body = await request.body()
    if len(body) > 1024 * 1024:
        raise ValidationFailedError("回调请求体过大")
    try:
        payload = AdapterCallbackPayload.model_validate_json(body)
    except (ValidationError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationFailedError("回调请求体非法") from exc
    if payload.adapter_id != adapter_id:
        raise ValidationFailedError("回调 adapter_id 与路径不一致")
    secret = settings.training_adapter_shared_secrets.get(adapter_id)
    normalized_nonce = verify_callback_signature(
        secret=secret,
        timestamp=timestamp,
        nonce=nonce,
        signature=signature,
        body=body,
        now=datetime.now(UTC),
        window_seconds=settings.training_callback_time_window_seconds,
    )
    ensure_capabilities(
        gateway.capabilities(adapter_id),
        AdapterCapability.CALLBACK,
        adapter_id=adapter_id,
    )
    services.callbacks.handle(
        adapter_id=adapter_id,
        nonce=normalized_nonce,
        event_id=payload.event_id,
        adapter_version=payload.adapter_version,
        external_run_id=payload.external_run_id,
        external_status=payload.external_status,
        model_ref=payload.model_ref,
        evaluation_ref=payload.evaluation_ref,
        error_code=payload.error_code,
        error_summary=payload.error_summary,
        payload=payload.model_dump(mode="json"),
    )
    return Response(status_code=204)


__all__ = ["router"]
