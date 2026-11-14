"""``huggingface_hub`` 的生产实现。

把外部库的返回结构收敛成 :class:`DatasetCardInfo`。**唯一 import 外部 SDK
的地方**，便于测试替换、也便于将来换库时只改这一个文件。
"""

from __future__ import annotations

from typing import Any

from ddp_data.huggingface.adapter import DatasetCardInfo, HfAdapterError


class HuggingFaceHubClient:
    """基于 ``huggingface_hub.HfApi`` 的只读客户端。

    只读元信息，不下载任何文件——`docs/ddp/07` 明确"不默认全量下载"。
    """

    def __init__(self, token: str | None = None) -> None:
        try:
            from huggingface_hub import HfApi
        except ImportError as exc:  # pragma: no cover - 依赖缺失时给出可执行提示
            raise HfAdapterError(
                "未安装 huggingface_hub；请执行 pip install -r data-platform/requirements.txt"
            ) from exc
        self._api = HfApi(token=token)

    def dataset_info(self, repo_id: str, *, revision: str | None = None) -> DatasetCardInfo:
        try:
            info = self._api.dataset_info(repo_id, revision=revision)
        except Exception as exc:  # noqa: BLE001 - 外部异常类型不稳定
            raise HfAdapterError(f"读取 {repo_id} 元信息失败：{exc}") from exc

        card_data = _card_data_dict(getattr(info, "card_data", None))
        return DatasetCardInfo(
            repo_id=str(getattr(info, "id", repo_id)),
            sha=str(getattr(info, "sha", "") or ""),
            card_data=card_data,
            gated=getattr(info, "gated", False),
            tags=tuple(str(tag) for tag in (getattr(info, "tags", None) or ())),
        )


def _card_data_dict(value: Any) -> dict[str, object]:
    """``card_data`` 在旧版本是 ``dict``，新版本是带 ``to_dict()`` 的模型。"""
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        return dict(result) if isinstance(result, dict) else {}
    return {}
