"""模型平台 Adapter 能力规则。

契约 `model_adapter_unsupported`(422)：「Adapter 未声明所需能力」。
能力声明来自配置（`DDP_MODEL_ADAPTER_CAPABILITIES`），本模块只做判定，
不读取配置、不访问网络。
"""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType

from app.domain.modelops.errors import ModelAdapterUnsupportedError

# 与 `app.core.config._model_adapter_config_problems` 的已知能力集合保持一致。
EVALUATE_CAPABILITY = "evaluate"
INFERENCE_CAPABILITY = "inference"

KNOWN_CAPABILITIES: frozenset[str] = frozenset(
    {EVALUATE_CAPABILITY, INFERENCE_CAPABILITY, "callback", "status", "cancel"}
)

# 动作 → 必需能力。新增动作必须在此登记，否则 `ensure_capability` 无参可查。
ACTION_CAPABILITY: MappingProxyType[str, str] = MappingProxyType(
    {
        "submit_evaluation": EVALUATE_CAPABILITY,
        "submit_inference": INFERENCE_CAPABILITY,
    }
)


def ensure_capability(capabilities: Iterable[str], action: str) -> None:
    """Adapter 未声明该动作所需能力时抛 422 `model_adapter_unsupported`。

    **能力声明为空时不在此判定**：空声明意味着该 Adapter 在配置里根本没有登记
    （`DDP_MODEL_ADAPTER_CAPABILITIES` 无此项），契约对这种情形给的是 503
    `model_adapter_unavailable`（「未配置或不可达」），不是 422。因此这里放行，
    由后续 `submit_*` 抛 503，两个码各自保留独立语义。
    """
    required = ACTION_CAPABILITY.get(action)
    if required is None:
        raise KeyError(f"未登记的动作能力映射：{action}")
    supported = {str(item) for item in capabilities}
    if not supported:
        return
    if required not in supported:
        raise ModelAdapterUnsupportedError(f"模型 Adapter 未声明所需能力 {required}")


__all__ = [
    "ACTION_CAPABILITY",
    "EVALUATE_CAPABILITY",
    "INFERENCE_CAPABILITY",
    "KNOWN_CAPABILITIES",
    "ensure_capability",
]
