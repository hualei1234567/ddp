"""基础设施层：Hugging Face 远端元信息校验适配。

只做"协议适配 + 异常收敛"：把 ``ddp_data`` 的 Adapter 接到应用层端口，
不在这里做 gated 一致性等业务判断（那属于用例层的权威规则）。
"""

from __future__ import annotations

from app.application.datasources.verification import (
    RemoteCheckError,
    RemoteCheckResult,
)


class HuggingFaceRemoteVerifier:
    """基于 ``data-platform`` Adapter 的只读校验实现。

    ``ddp_data`` / ``huggingface_hub`` 采用**延迟导入**：API 进程默认不需要
    这两个依赖，只有显式 ``verify_remote=true`` 的登记请求才会加载。
    """

    def __init__(self, *, token: str | None = None, timeout_seconds: float = 10.0) -> None:
        self._token = token or None
        self._timeout_seconds = timeout_seconds

    def describe(self, repo_id: str, *, revision: str) -> RemoteCheckResult:
        try:
            from ddp_data.huggingface import HfAdapterError, HuggingFaceAdapter
            from ddp_data.huggingface.hub_client import HuggingFaceHubClient
        except ImportError as exc:  # pragma: no cover - 部署缺依赖时给出可执行提示
            raise RemoteCheckError(
                "未安装 data-platform/huggingface_hub，无法执行远端校验"
            ) from exc

        adapter = HuggingFaceAdapter(
            HuggingFaceHubClient(
                token=self._token,
                timeout_seconds=self._timeout_seconds,
            )
        )
        try:
            ref = adapter.describe(repo_id, revision=revision)
        except HfAdapterError as exc:
            # 超时、404、401、形状异常都已由 Adapter 收敛为同一类错误；
            # 这里保留原文供日志排查，对外仍走统一错误映射，不回显原文。
            raise RemoteCheckError(str(exc)) from exc

        return RemoteCheckResult(
            repo_id=ref.repo_id,
            resolved_revision=ref.revision,
            license=ref.license,
            is_gated=ref.is_gated,
            dataset_card_url=ref.dataset_card_url,
        )


__all__ = ["HuggingFaceRemoteVerifier"]
