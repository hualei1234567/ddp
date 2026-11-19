"""基础设施层：Hugging Face 远端元信息校验适配。

只做"协议适配 + 异常收敛"：把 ``ddp_data`` 的 Adapter 接到应用层端口，
不在这里做 gated 一致性等业务判断（那属于用例层的权威规则）。

本层承担四件事，全部集中在此以便单测隔离（不联网、可注入 fake）：

1. **有界调用**：显式超时 + 有界重试（仅瞬时错误），绝不无限等待；
2. **异常收敛**：把外部 SDK 的异常类型收敛成稳定的
   :class:`RemoteCheckFailureReason`，供 API 输出机器可读原因；
3. **脱敏**：远端报错文本经 :func:`sanitize_log_message` 处理后才进入结果，
   凭据 / URL / 绝对路径不会外泄到日志或响应；
4. **字段上界**：只保留白名单字段并限制长度，超大或畸形响应视为不可用，
   不把任意远端 JSON 原样落库/回显。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from app.application.datasources.verification import (
    DATASET_CARD_URL_MAX_LENGTH,
    LICENSE_MAX_LENGTH,
    RemoteCheckError,
    RemoteCheckFailureReason,
    RemoteCheckResult,
)
from app.core.logging import sanitize_log_message


# 外部 Adapter 的最小协议；生产实现是 ``ddp_data.huggingface.HuggingFaceAdapter``。
# 用 Protocol 而非具体类型，测试可注入 fake，生产仍走真实实现。
class _AdapterProtocol(Protocol):
    def describe(self, repo_id: str, *, revision: str | None = None) -> object: ...


# 工厂签名：给定只读 token 与超时秒数，返回一个 Adapter。生产实现延迟导入
# ``ddp_data``；测试注入 fake 即可完全离线。
AdapterFactory = Callable[[str | None, float], _AdapterProtocol]


def _default_adapter_factory(token: str | None, timeout_seconds: float) -> _AdapterProtocol:
    """生产装配：延迟导入 ``ddp_data`` / ``huggingface_hub``。

    API 进程默认不需要这两个依赖，只有显式 ``verify_remote=true`` 的登记请求
    才会加载；缺失时给出可执行提示而不是崩溃。
    """
    try:
        from ddp_data.huggingface import HuggingFaceAdapter
        from ddp_data.huggingface.hub_client import HuggingFaceHubClient
    except ImportError as exc:  # pragma: no cover - 部署缺依赖时给出可执行提示
        raise RemoteCheckError(
            "未安装 data-platform/huggingface_hub，无法执行远端校验",
            reason=RemoteCheckFailureReason.UNAVAILABLE,
        ) from exc

    return HuggingFaceAdapter(
        HuggingFaceHubClient(token=token, timeout_seconds=timeout_seconds),
    )


# 外部异常类型不稳定，用 ``__cause__`` 链上的类型名 + 文本做保守分类；
# 不 import 具体异常类，避免把 ``huggingface_hub`` 变成硬依赖。
_TIMEOUT_MARKERS = ("timeout", "timed out", "time out")
_NOT_FOUND_MARKERS = ("404", "not found", "repositorynotfound", "repoidnotfound")
_UNAUTHORIZED_MARKERS = ("401", "403", "unauthorized", "forbidden", "gated", "private")
_SERVER_ERROR_MARKERS = ("500", "502", "503", "504", "server error", "internal server")
_INVALID_MARKERS = ("json", "decode", "expecting value", "invalid response")


def _classify(exc: BaseException) -> RemoteCheckFailureReason:
    """把外部异常收敛为稳定原因；无法判定时归 ``UNAVAILABLE``。"""
    names: list[str] = []
    texts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names.append(type(current).__name__.lower())
        texts.append(str(current).lower())
        current = current.__cause__ or current.__context__

    haystack = " ".join(names + texts)

    def _has(markers: tuple[str, ...]) -> bool:
        return any(marker in haystack for marker in markers)

    # 顺序即优先级：超时/不存在/无权限/服务端错误/非法响应。
    if _has(_TIMEOUT_MARKERS):
        return RemoteCheckFailureReason.TIMEOUT
    if _has(_NOT_FOUND_MARKERS):
        return RemoteCheckFailureReason.NOT_FOUND
    if _has(_UNAUTHORIZED_MARKERS):
        return RemoteCheckFailureReason.UNAUTHORIZED
    if _has(_SERVER_ERROR_MARKERS):
        return RemoteCheckFailureReason.SERVER_ERROR
    if _has(_INVALID_MARKERS):
        return RemoteCheckFailureReason.INVALID_RESPONSE
    return RemoteCheckFailureReason.UNAVAILABLE


def _bounded_result(ref: object) -> RemoteCheckResult:
    """把 Adapter 返回值收敛为白名单结果；字段越界视为响应不可用。"""
    repo_id = getattr(ref, "repo_id", None)
    revision = getattr(ref, "revision", None)
    license_value = getattr(ref, "license", None)
    is_gated = getattr(ref, "is_gated", None)
    card_url = getattr(ref, "dataset_card_url", None)

    if not isinstance(repo_id, str) or not isinstance(revision, str):
        raise RemoteCheckError(
            "远端返回缺少 repo_id 或 revision",
            reason=RemoteCheckFailureReason.INVALID_RESPONSE,
        )
    if not isinstance(is_gated, bool):
        raise RemoteCheckError(
            "远端返回的 gated 标记不是布尔值",
            reason=RemoteCheckFailureReason.INVALID_RESPONSE,
        )
    if license_value is not None and not isinstance(license_value, str):
        raise RemoteCheckError(
            "远端返回的 license 不是字符串",
            reason=RemoteCheckFailureReason.INVALID_RESPONSE,
        )
    if not isinstance(card_url, str):
        raise RemoteCheckError(
            "远端返回的 dataset_card_url 不是字符串",
            reason=RemoteCheckFailureReason.INVALID_RESPONSE,
        )
    # 超大响应：只保留白名单字段，且限制长度；超出视为不可用，不截断后冒充完整。
    if license_value is not None and len(license_value) > LICENSE_MAX_LENGTH:
        raise RemoteCheckError(
            f"远端 license 超过 {LICENSE_MAX_LENGTH} 字符上限",
            reason=RemoteCheckFailureReason.RESPONSE_TOO_LARGE,
        )
    if len(card_url) > DATASET_CARD_URL_MAX_LENGTH:
        raise RemoteCheckError(
            f"远端 dataset_card_url 超过 {DATASET_CARD_URL_MAX_LENGTH} 字符上限",
            reason=RemoteCheckFailureReason.RESPONSE_TOO_LARGE,
        )

    return RemoteCheckResult(
        repo_id=repo_id,
        resolved_revision=revision,
        license=license_value,
        is_gated=is_gated,
        dataset_card_url=card_url,
    )


class HuggingFaceRemoteVerifier:
    """基于 ``data-platform`` Adapter 的只读校验实现。

    ``adapter_factory`` / ``sleep`` 可注入：单测用 fake 覆盖超时、404、5xx、
    非法 JSON、超大响应与凭据脱敏，**禁止真连外网**。
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        timeout_seconds: float = 10.0,
        max_attempts: int = 1,
        adapter_factory: AdapterFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token or None
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max(1, max_attempts)
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self._sleep = sleep

    def describe(self, repo_id: str, *, revision: str) -> RemoteCheckResult:
        last_error: RemoteCheckError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                adapter = self._adapter_factory(self._token, self._timeout_seconds)
                ref = adapter.describe(repo_id, revision=revision)
            except RemoteCheckError:
                raise
            except Exception as exc:  # noqa: BLE001 - 外部异常类型不稳定，统一收敛
                reason = _classify(exc)
                # 脱敏后才进入异常文本：凭据 / URL / 绝对路径不外泄。
                last_error = RemoteCheckError(
                    sanitize_log_message(exc),
                    reason=reason,
                )
                # 仅瞬时错误且仍有重试预算时重试；确定性错误立即失败。
                if reason.is_transient and attempt < self._max_attempts:
                    self._sleep(0.0)
                    continue
                raise last_error from exc

            return _bounded_result(ref)

        # 理论上不可达（循环内必 raise 或 return），保留以防未来改动。
        raise last_error or RemoteCheckError(
            "远端校验未产生结果",
            reason=RemoteCheckFailureReason.UNAVAILABLE,
        )


__all__ = ["HuggingFaceRemoteVerifier"]
