"""训练 Adapter 的真实 HTTP 网关。

下游 Adapter 需要实现的接口（``{endpoint}`` 来自 ``DDP_TRAINING_ADAPTER_ENDPOINTS``）：

- ``POST {endpoint}/training-runs``                          提交训练
- ``GET  {endpoint}/training-runs/{external_run_id}``        查询状态
- ``POST {endpoint}/training-runs/{external_run_id}/cancel`` 取消训练

未在配置里登记 endpoint 的 adapter **仍委派给**
:class:`ConfiguredTrainingAdapterGateway`——即沿用既有的显式 503，
行为与接线前完全一致；只有登记了 endpoint 的 adapter 才会真正发出 HTTP 请求。
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote
from uuid import UUID

import httpx

from app.application.datasets.training_ports import AdapterRunRef, AdapterRunStatus
from app.core.config import Settings
from app.domain.datasets.training_enums import ExternalTrainingStatus
from app.domain.datasets.training_errors import TrainingAdapterUnavailableError
from app.infrastructure.adapter_http import (
    AdapterHttpFailure,
    adapter_failure_as,
    optional_bool,
    optional_mapping,
    optional_str,
    request_json,
    shared_client,
)
from app.infrastructure.adapter_url_guard import ensure_safe_adapter_url
from app.infrastructure.training_adapters import ConfiguredTrainingAdapterGateway

_STATUS_VALUES = frozenset(item.value for item in ExternalTrainingStatus)


class HttpTrainingAdapterGateway:
    """训练 Adapter 网关：登记了 endpoint 走 HTTP，未登记沿用 503。"""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self._endpoints = {
            adapter_id: endpoint.rstrip("/")
            for adapter_id, endpoint in settings.training_adapter_endpoints.items()
        }
        self._timeout_seconds = settings.training_adapter_timeout_seconds
        # SSRF 守卫的显式本地例外（默认关闭）；见 ``adapter_url_guard`` docstring。
        self._allow_private_endpoints = settings.training_adapter_allow_private
        # 注入的 client 由调用方负责关闭；未注入时使用进程内共享连接池。
        self._injected_client = client
        self._fallback = ConfiguredTrainingAdapterGateway(settings)

    def capabilities(self, adapter_id: str) -> frozenset[str]:
        return self._fallback.capabilities(adapter_id)

    def submit(
        self,
        *,
        adapter_id: str,
        adapter_version: str,
        correlation_id: str,
        delivery_id: UUID,
        dataset_version_id: UUID,
        config: dict[str, object],
    ) -> AdapterRunRef:
        base = self._endpoints.get(adapter_id)
        if base is None:
            # 未配置 endpoint：沿用既有实现抛 503，消息与接线前一致。
            return self._fallback.submit(
                adapter_id=adapter_id,
                adapter_version=adapter_version,
                correlation_id=correlation_id,
                delivery_id=delivery_id,
                dataset_version_id=dataset_version_id,
                config=config,
            )
        context = f"训练 Adapter {adapter_id} 提交训练"
        with adapter_failure_as(TrainingAdapterUnavailableError):
            body = self._request(
                "POST",
                f"{base}/training-runs",
                context=context,
                payload={
                    "adapter_id": adapter_id,
                    "adapter_version": adapter_version,
                    "correlation_id": correlation_id,
                    "training_delivery_id": str(delivery_id),
                    "dataset_version_id": str(dataset_version_id),
                    "config": config,
                },
            )
            external_run_id = body.get("external_run_id")
            if not isinstance(external_run_id, str) or not external_run_id.strip():
                raise AdapterHttpFailure(f"{context} 响应缺少 external_run_id")
            return AdapterRunRef(
                external_run_id=external_run_id,
                external_run_url=optional_str(body, "external_run_url", context=context),
                model_ref=optional_mapping(body, "model_ref", context=context),
                evaluation_ref=optional_mapping(body, "evaluation_ref", context=context),
            )

    def get_status(
        self,
        *,
        adapter_id: str,
        external_run_id: str,
        correlation_id: str,
    ) -> AdapterRunStatus:
        base = self._endpoints.get(adapter_id)
        if base is None:
            return self._fallback.get_status(
                adapter_id=adapter_id,
                external_run_id=external_run_id,
                correlation_id=correlation_id,
            )
        context = f"训练 Adapter {adapter_id} 查询状态"
        with adapter_failure_as(TrainingAdapterUnavailableError):
            body = self._request(
                "GET",
                f"{base}/training-runs/{quote(external_run_id, safe='')}",
                context=context,
            )
            return _run_status(body, context=context)

    def cancel(
        self,
        *,
        adapter_id: str,
        external_run_id: str,
        correlation_id: str,
    ) -> AdapterRunStatus:
        base = self._endpoints.get(adapter_id)
        if base is None:
            return self._fallback.cancel(
                adapter_id=adapter_id,
                external_run_id=external_run_id,
                correlation_id=correlation_id,
            )
        context = f"训练 Adapter {adapter_id} 取消训练"
        with adapter_failure_as(TrainingAdapterUnavailableError):
            body = self._request(
                "POST",
                f"{base}/training-runs/{quote(external_run_id, safe='')}/cancel",
                context=context,
                payload={"correlation_id": correlation_id},
            )
            return _run_status(body, context=context)

    def _request(
        self,
        method: str,
        url: str,
        *,
        context: str,
        payload: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        # 每次请求前做 SSRF 筛查（不缓存解析结果）：拒绝环回/私网/链路本地/
        # 云元数据等地址。失败抛 ``AdapterHttpFailure``，由调用处的
        # ``adapter_failure_as`` 统一映射为 ``TrainingAdapterUnavailableError``。
        ensure_safe_adapter_url(
            url,
            context=context,
            allow_private=self._allow_private_endpoints,
        )
        client = self._injected_client or shared_client(self._timeout_seconds)
        return request_json(
            client=client,
            method=method,
            url=url,
            context=context,
            payload=payload,
        )


def _run_status(body: Mapping[str, object], *, context: str) -> AdapterRunStatus:
    raw_status = body.get("status")
    # 未知状态一律按协议违规处理（→ 503），绝不映射成"成功"或静默降级：
    # 领域层 ``ExternalTrainingStatus(...)`` 遇到非法值会直接抛 ValueError，
    # 那会变成 500，比明确的不可用更糟。
    if not isinstance(raw_status, str) or raw_status not in _STATUS_VALUES:
        raise AdapterHttpFailure(f"{context} 响应状态非法：{raw_status!r}")
    return AdapterRunStatus(
        status=ExternalTrainingStatus(raw_status),
        status_stale=optional_bool(body, "status_stale", context=context),
        external_run_url=optional_str(body, "external_run_url", context=context),
        model_ref=optional_mapping(body, "model_ref", context=context),
        evaluation_ref=optional_mapping(body, "evaluation_ref", context=context),
        error_code=optional_str(body, "error_code", context=context),
        error_summary=optional_str(body, "error_summary", context=context),
    )


__all__ = ["HttpTrainingAdapterGateway"]
