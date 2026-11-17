"""外部 Adapter HTTP 网关的共享传输层。

训练网关（``training_adapter_http``）与模型平台网关（``model_platform_http``）
共用这一层的唯一目的：**错误映射只有一处权威实现**。若两个网关各写一份
"超时/连接失败/5xx 怎么判"，它们迟早会漂移，而这类漂移正是"假成功"的来源。

约定：

- 本层只负责 HTTP 语义（超时、传输故障、状态码、JSON 解析）；
- 失败一律抛 :class:`AdapterHttpFailure`，携带可直接排障的中文消息；
- 调用方**必须**用 :func:`adapter_failure_as` 把它映射为本域的
  ``*AdapterUnavailableError``（→ HTTP 503），不得让它冒泡成 500，
  也不得吞掉后返回假成功；
- 不做重试：Adapter 不可用时应让调用方立刻看到 503，而不是被隐藏成长尾等待。
  显式超时由配置注入（``DDP_*_ADAPTER_TIMEOUT_SECONDS``），不在此硬编码。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import lru_cache

import httpx

# 非 2xx 响应体只截取前若干字符带进错误消息：既够排障，又避免把上游整页
# HTML/堆栈灌进 API 错误体（错误消息会进入领域错误摘要与审计记录）。
_MAX_ERROR_BODY_CHARS = 300


# 进程内共享的连接池。Adapter 调用是低频写路径，但若每个请求新建
# ``httpx.Client`` 就每个请求泄漏一个连接池；按超时分档缓存即可。
# 该 client 生命周期与进程一致，不做显式 close（进程退出由 OS 回收）。
@lru_cache(maxsize=8)
def shared_client(timeout_seconds: float) -> httpx.Client:
    return httpx.Client(timeout=timeout_seconds)


class AdapterHttpFailure(Exception):
    """Adapter HTTP 调用失败；由 :func:`adapter_failure_as` 翻译为本域错误。"""


@contextmanager
def adapter_failure_as(error_type: type[Exception]) -> Iterator[None]:
    """把块内抛出的 :class:`AdapterHttpFailure` 统一翻译为 ``error_type``。

    这是"什么情况 → 什么领域错误"的唯一实现点：两个网关各自只声明自己
    该用哪个错误类型，不各写一套映射。
    """
    try:
        yield
    except AdapterHttpFailure as exc:
        raise error_type(str(exc)) from exc


def _send(
    *,
    client: httpx.Client,
    method: str,
    url: str,
    context: str,
    payload: Mapping[str, object] | None = None,
) -> httpx.Response:
    # 先单独校验 URL：非法地址必须在"构造请求"这一步变成明确的不可用，
    # 而不是让 httpx/urllib 的 ValueError 冒泡成 500。``httpx.URL`` 自己不会
    # 拒绝空 host（``http://``），所以这里显式检查 scheme 与 host。
    # 启动自检已拦掉这类配置，这里只是兜底（Settings 也可能被测试或 CLI 直接构造）。
    try:
        target = httpx.URL(url)
    except (httpx.InvalidURL, ValueError) as exc:
        raise AdapterHttpFailure(f"{context} 的地址非法：{url}") from exc
    if target.scheme not in {"http", "https"} or not target.host:
        raise AdapterHttpFailure(f"{context} 的地址非法：{url}")

    try:
        response = client.request(method, target, json=payload)
    except httpx.TimeoutException as exc:
        raise AdapterHttpFailure(f"{context} 超时：{url}（{type(exc).__name__}）") from exc
    except httpx.HTTPError as exc:
        # 连接拒绝、DNS 失败、TLS/协议错误等传输层故障。
        raise AdapterHttpFailure(f"{context} 连接失败：{url}（{type(exc).__name__}）") from exc

    if not 200 <= response.status_code < 300:
        detail = response.text[:_MAX_ERROR_BODY_CHARS].strip()
        suffix = f"：{detail}" if detail else ""
        raise AdapterHttpFailure(f"{context} 返回 HTTP {response.status_code}{suffix}")
    return response


def request_json(
    *,
    client: httpx.Client,
    method: str,
    url: str,
    context: str,
    payload: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """发起一次请求并返回 JSON 对象。

    ``context`` 是错误消息前缀（如 ``"训练 Adapter mock 提交训练"``），由调用方
    拼好，保证日志里能直接看出是哪个 adapter、哪个动作、哪个地址。
    """
    response = _send(client=client, method=method, url=url, context=context, payload=payload)
    try:
        body = response.json()
    except ValueError as exc:
        raise AdapterHttpFailure(
            f"{context} 返回的响应不是合法 JSON（HTTP {response.status_code}）"
        ) from exc
    if not isinstance(body, dict):
        raise AdapterHttpFailure(f"{context} 返回的 JSON 不是对象（HTTP {response.status_code}）")
    return body


def request_ack(
    *,
    client: httpx.Client,
    method: str,
    url: str,
    context: str,
    payload: Mapping[str, object] | None = None,
) -> None:
    """发起一次请求，2xx 即视为已受理（不解析响应体）。

    用于"提交后结果经既有 HMAC 回调回传"的接口：这类接口的返回值没有承载
    领域信息的通道，强行解析响应体只会把合法的空 202 误判成故障。
    """
    _send(client=client, method=method, url=url, context=context, payload=payload)


def optional_str(body: Mapping[str, object], key: str, *, context: str) -> str | None:
    """读取可选字符串字段；类型不符按协议违规处理，不做静默转换。"""
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AdapterHttpFailure(f"{context} 响应字段 {key} 不是字符串：{value!r}")
    return value


def optional_mapping(
    body: Mapping[str, object],
    key: str,
    *,
    context: str,
) -> dict[str, object] | None:
    """读取可选对象字段（``model_ref`` / ``evaluation_ref``）。"""
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AdapterHttpFailure(f"{context} 响应字段 {key} 不是 JSON 对象")
    return dict(value)


def optional_bool(
    body: Mapping[str, object],
    key: str,
    *,
    context: str,
    default: bool = False,
) -> bool:
    value = body.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise AdapterHttpFailure(f"{context} 响应字段 {key} 不是布尔值：{value!r}")
    return value


__all__ = [
    "AdapterHttpFailure",
    "adapter_failure_as",
    "optional_bool",
    "optional_mapping",
    "optional_str",
    "request_ack",
    "request_json",
    "shared_client",
]
