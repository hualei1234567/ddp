"""模型平台 Adapter 默认实现与产物检查适配。"""

from __future__ import annotations

from uuid import UUID

from app.application.modelops.ports import (
    ArtifactInspector,
    ModelPlatformGateway,
)
from app.core.config import Settings
from app.domain.modelops.errors import ModelAdapterUnavailableError
from app.infrastructure.storage.s3 import S3MultipartStorage


class ConfiguredModelPlatformGateway:
    """真实模型平台尚未提供：只暴露配置能力，任何执行返回 503。"""

    def __init__(self, settings: Settings) -> None:
        self._capabilities = {
            adapter_id: frozenset(capabilities)
            for adapter_id, capabilities in settings.model_adapter_capabilities.items()
        }

    def capabilities(self, adapter_id: str) -> frozenset[str]:
        return self._capabilities.get(adapter_id, frozenset())

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
        raise ModelAdapterUnavailableError(
            f"模型 Adapter {adapter_id} 未配置真实评测客户端，不能执行评测"
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
        raise ModelAdapterUnavailableError(
            f"模型 Adapter {adapter_id} 未配置真实推理客户端，不能执行推理"
        )


class S3ArtifactInspector(ArtifactInspector):
    def __init__(self, storage: S3MultipartStorage) -> None:
        self._storage = storage

    def object_size(self, object_key: str) -> int | None:
        return self._storage.object_size(object_key)


__all__ = ["ConfiguredModelPlatformGateway", "S3ArtifactInspector", "ModelPlatformGateway"]
