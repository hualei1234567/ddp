"""签名回调客户端与 body 构造。

约束：
- 签名原文是「实际发送的原始 body 字节」，绝不重新序列化；
- 每次重试重新签名（新时间戳/nonce），但 ``event_id`` 与 body 保持不变，
  这样 DDP 的 event_id 幂等判断仍然成立；
- 训练回调可能早于 DDP 落库外部引用，404/409 属于可重试响应。
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx

from app.config import Settings
from app.signature import callback_headers

logger = logging.getLogger("ddp.local_adapter.callbacks")

TRAINING_STATUSES = {
    "SUBMITTED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "UNKNOWN",
    "UNAVAILABLE",
}
MODEL_EVENTS = {
    "evaluation.completed",
    "evaluation.failed",
    "inference.completed",
    "inference.failed",
}
MODEL_REF_FIELDS = {"model_id", "model_version", "registered_at"}
RETRYABLE_STATUS_CODES = {404, 409, 425, 429}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _finite_metrics(metrics: dict[str, object] | None) -> dict[str, float]:
    result: dict[str, float] = {}
    for name, value in (metrics or {}).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if math.isfinite(number):
            result[str(name)] = number
    return result


def _model_ref(value: dict[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    unknown = set(value) - MODEL_REF_FIELDS
    if unknown:
        raise ValueError("model_ref 包含未知字段：" + "、".join(sorted(unknown)))
    if not value.get("model_id"):
        raise ValueError("model_ref.model_id 不能为空")
    return {key: item for key, item in value.items() if item is not None}


def training_callback_payload(
    *,
    adapter_id: str,
    adapter_version: str,
    external_run_id: str,
    status: str,
    model_ref: dict[str, object] | None = None,
    evaluation_ref: dict[str, object] | None = None,
    error_code: str | None = None,
    error_summary: str | None = None,
    payload: dict[str, object] | None = None,
    event_id: str | None = None,
    occurred_at: str | None = None,
) -> dict[str, object]:
    if status not in TRAINING_STATUSES:
        raise ValueError(f"非法训练回调状态：{status}")
    body: dict[str, object] = {
        "event_id": event_id or uuid4().hex,
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "external_run_id": external_run_id,
        "external_status": status,
        "occurred_at": occurred_at or now_iso(),
    }
    filtered_ref = _model_ref(model_ref)
    if filtered_ref is not None:
        body["model_ref"] = filtered_ref
    if evaluation_ref is not None:
        body["evaluation_ref"] = evaluation_ref
    if error_code is not None:
        body["error_code"] = error_code[:100]
    if error_summary is not None:
        body["error_summary"] = error_summary[:2000]
    if payload is not None:
        body["payload"] = payload
    return body


def model_callback_payload(
    *,
    adapter_id: str,
    adapter_version: str,
    event_type: str,
    project_id: UUID | str,
    evaluation_id: UUID | str | None = None,
    task_id: UUID | str | None = None,
    metrics: dict[str, object] | None = None,
    report_ref: dict[str, object] | None = None,
    result_ref: dict[str, object] | None = None,
    log_ref: str | None = None,
    error_code: str | None = None,
    error_summary: str | None = None,
    event_id: str | None = None,
) -> dict[str, object]:
    if event_type not in MODEL_EVENTS:
        raise ValueError(f"非法模型回调事件：{event_type}")
    body: dict[str, object] = {
        "event_id": event_id or uuid4().hex,
        "adapter_version": adapter_version,
        "event_type": event_type,
        "project_id": str(project_id),
        "metrics": _finite_metrics(metrics),
    }
    if evaluation_id is not None:
        body["evaluation_id"] = str(evaluation_id)
    if task_id is not None:
        body["task_id"] = str(task_id)
    if report_ref is not None:
        body["report_ref"] = report_ref
    if result_ref is not None:
        body["result_ref"] = result_ref
    if log_ref is not None:
        body["log_ref"] = log_ref[:1024]
    if error_code is not None:
        body["error_code"] = error_code[:100]
    if error_summary is not None:
        body["error_summary"] = error_summary[:2000]
    # adapter_id 不在 DDP 模型回调 schema 中；它只用于本服务组装路径与日志。
    return body


def encode_body(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class CallbackResult:
    label: str
    ok: bool
    attempts: int
    status_code: int | None = None
    error: str | None = None


class CallbackClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._client = http_client or httpx.Client(
            timeout=settings.http_timeout_seconds
        )
        self._owns_client = http_client is None
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _post(
        self, path: str, payload: dict[str, object], *, secret: str, label: str
    ) -> CallbackResult:
        body = encode_body(payload)
        url = f"{self._settings.api_base_url}{path}"
        last_error: str | None = None
        last_status: int | None = None
        for attempt in range(1, self._settings.callback_max_attempts + 1):
            headers = callback_headers(secret, body)
            try:
                response = self._client.post(url, content=body, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_status = None
            else:
                last_status = response.status_code
                if 200 <= response.status_code < 300:
                    logger.info(
                        "回调成功 label=%s attempt=%s status=%s",
                        label,
                        attempt,
                        response.status_code,
                    )
                    return CallbackResult(
                        label=label,
                        ok=True,
                        attempts=attempt,
                        status_code=response.status_code,
                    )
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                retryable = (
                    response.status_code in RETRYABLE_STATUS_CODES
                    or response.status_code >= 500
                )
                if not retryable:
                    logger.error(
                        "回调不可重试 label=%s status=%s", label, response.status_code
                    )
                    return CallbackResult(
                        label=label,
                        ok=False,
                        attempts=attempt,
                        status_code=response.status_code,
                        error=last_error,
                    )
            if attempt < self._settings.callback_max_attempts:
                delay = min(
                    self._settings.callback_initial_delay_seconds
                    * (2 ** (attempt - 1)),
                    self._settings.callback_max_delay_seconds,
                )
                logger.warning(
                    "回调失败将重试 label=%s attempt=%s error=%s",
                    label,
                    attempt,
                    last_error,
                )
                self._sleep(delay)
        logger.error(
            "回调最终失败 label=%s attempts=%s error=%s",
            label,
            self._settings.callback_max_attempts,
            last_error,
        )
        return CallbackResult(
            label=label,
            ok=False,
            attempts=self._settings.callback_max_attempts,
            status_code=last_status,
            error=last_error,
        )

    def send_training(
        self, payload: dict[str, object], *, label: str
    ) -> CallbackResult:
        adapter_id = str(payload["adapter_id"])
        path = f"/api/v1/integrations/training-adapters/{quote(adapter_id, safe='')}/callbacks"
        return self._post(
            path, payload, secret=self._settings.training_secret, label=label
        )

    def send_model(self, payload: dict[str, object], *, label: str) -> CallbackResult:
        adapter_id = self._settings.model_adapter_id
        path = f"/api/v1/integrations/model-adapters/{quote(adapter_id, safe='')}/callbacks"
        return self._post(
            path, payload, secret=self._settings.model_secret, label=label
        )


class CallbackSink(Protocol):
    def send_training(
        self, payload: dict[str, object], *, label: str
    ) -> CallbackResult: ...

    def send_model(
        self, payload: dict[str, object], *, label: str
    ) -> CallbackResult: ...


__all__ = [
    "CallbackClient",
    "CallbackResult",
    "CallbackSink",
    "MODEL_REF_FIELDS",
    "encode_body",
    "model_callback_payload",
    "now_iso",
    "training_callback_payload",
]
