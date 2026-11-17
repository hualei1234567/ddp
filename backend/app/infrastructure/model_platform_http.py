"""模型平台 Adapter 的真实 HTTP 网关（评测 / 推理）。

下游 Adapter 需要实现的接口（``{endpoint}`` 来自 ``DDP_MODEL_ADAPTER_ENDPOINTS``）：

- ``POST {endpoint}/evaluations``      提交评测
- ``POST {endpoint}/inference-tasks``  提交推理

两个端口在应用层都返回 ``None``：提交只表示"已受理"，评测结论与推理结果由
既有的 HMAC 回调（``/modelops/.../callbacks``）回传，网关不解析响应体。

未在配置里登记 endpoint 的 adapter **仍委派给**
:class:`ConfiguredModelPlatformGateway`——即沿用既有的显式 503，
行为与接线前完全一致；只有登记了 endpoint 的 adapter 才会真正发出 HTTP 请求。
"""

from __future__ import annotations

from uuid import UUID

import httpx

from app.core.config import Settings
from app.domain.modelops.errors import ModelAdapterUnavailableError
from app.infrastructure.adapter_http import (
    adapter_failure_as,
    request_ack,
    shared_client,
)
from app.infrastructure.model_platform import ConfiguredModelPlatformGateway


class HttpModelPlatformGateway:
    """模型平台网关：登记了 endpoint 走 HTTP，未登记沿用 503。"""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._endpoints = {
            adapter_id: endpoint.rstrip("/")
            for adapter_id, endpoint in settings.model_adapter_endpoints.items()
        }
        self._timeout_seconds = settings.model_adapter_timeout_seconds
        # 注入的 client 由调用方负责关闭；未注入时使用进程内共享连接池。
        self._injected_client = client
        self._fallback = ConfiguredModelPlatformGateway(settings)

    def capabilities(self, adapter_id: str) -> frozenset[str]:
        return self._fallback.capabilities(adapter_id)

    def submit_evaluation(
        self,
        *,
        adapter_id: str,
        adapter_version: str,
        evaluation_id: UUID,
        model_version_id: UUID,
        dataset_version_id: UUID,
        config: dict[str, object],
    ) -> None:
        base = self._endpoints.get(adapter_id)
        if base is None:
            self._fallback.submit_evaluation(
                adapter_id=adapter_id,
                adapter_version=adapter_version,
                evaluation_id=evaluation_id,
                model_version_id=model_version_id,
                dataset_version_id=dataset_version_id,
                config=config,
            )
            return
        context = f"模型 Adapter {adapter_id} 提交评测"
        with adapter_failure_as(ModelAdapterUnavailableError):
            self._post(
                f"{base}/evaluations",
                context=context,
                payload={
                    "adapter_id": adapter_id,
                    "adapter_version": adapter_version,
                    "evaluation_id": str(evaluation_id),
                    "model_version_id": str(model_version_id),
                    "dataset_version_id": str(dataset_version_id),
                    "config": config,
                },
            )

    def submit_inference(
        self,
        *,
        adapter_id: str,
        adapter_version: str,
        task_id: UUID,
        model_version_id: UUID,
        service_id: UUID | None,
        dataset_version_id: UUID | None,
        episode_ids: list[UUID],
        config: dict[str, object],
    ) -> None:
        base = self._endpoints.get(adapter_id)
        if base is None:
            self._fallback.submit_inference(
                adapter_id=adapter_id,
                adapter_version=adapter_version,
                task_id=task_id,
                model_version_id=model_version_id,
                service_id=service_id,
                dataset_version_id=dataset_version_id,
                episode_ids=episode_ids,
                config=config,
            )
            return
        context = f"模型 Adapter {adapter_id} 提交推理"
        with adapter_failure_as(ModelAdapterUnavailableError):
            self._post(
                f"{base}/inference-tasks",
                context=context,
                payload={
                    "adapter_id": adapter_id,
                    "adapter_version": adapter_version,
                    "task_id": str(task_id),
                    "model_version_id": str(model_version_id),
                    "service_id": None if service_id is None else str(service_id),
                    "dataset_version_id": (
                        None if dataset_version_id is None else str(dataset_version_id)
                    ),
                    "episode_ids": [str(episode_id) for episode_id in episode_ids],
                    "config": config,
                },
            )

    def _post(self, url: str, *, context: str, payload: dict[str, object]) -> None:
        client = self._injected_client or shared_client(self._timeout_seconds)
        request_ack(
            client=client,
            method="POST",
            url=url,
            context=context,
            payload=payload,
        )


__all__ = ["HttpModelPlatformGateway"]
