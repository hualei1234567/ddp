"""训练平台 Adapter 默认实现。

真实训练平台尚未提供：默认 Adapter 只暴露配置中的能力声明，任何真实调用都
显式返回不可用（503），绝不伪造训练成功。测试或接入实现通过依赖覆盖注入
``[MOCK]`` 或真实 Adapter。
"""

from __future__ import annotations

from uuid import UUID

from app.application.datasets.training_ports import AdapterRunRef, AdapterRunStatus
from app.core.config import Settings
from app.domain.datasets.training_enums import ExecutionMode
from app.domain.datasets.training_errors import TrainingAdapterUnavailableError


class ConfiguredTrainingAdapterGateway:
    def __init__(self, settings: Settings) -> None:
        self._capabilities = {
            adapter_id: frozenset(capabilities)
            for adapter_id, capabilities in settings.training_adapter_capabilities.items()
        }
        # 只接受启动自检通过的四种执行方式；配置层已在 validate_runtime_security
        # 拒绝非法值，这里不再静默降级。
        self._execution_modes = {
            adapter_id: ExecutionMode(mode)
            for adapter_id, mode in settings.training_adapter_execution_modes.items()
        }

    def capabilities(self, adapter_id: str) -> frozenset[str]:
        return self._capabilities.get(adapter_id, frozenset())

    def execution_mode(self, adapter_id: str) -> ExecutionMode | None:
        return self._execution_modes.get(adapter_id)

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
        raise TrainingAdapterUnavailableError(
            f"训练 Adapter {adapter_id} 未配置真实平台客户端，不能提交训练"
        )

    def get_status(
        self,
        *,
        adapter_id: str,
        external_run_id: str,
        correlation_id: str,
    ) -> AdapterRunStatus:
        raise TrainingAdapterUnavailableError(
            f"训练 Adapter {adapter_id} 未配置真实平台客户端，状态未知"
        )

    def cancel(
        self,
        *,
        adapter_id: str,
        external_run_id: str,
        correlation_id: str,
    ) -> AdapterRunStatus:
        raise TrainingAdapterUnavailableError(
            f"训练 Adapter {adapter_id} 未配置真实平台客户端，不能取消"
        )


__all__ = ["ConfiguredTrainingAdapterGateway"]
