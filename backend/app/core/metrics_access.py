"""``/metrics`` 访问控制策略（纯函数，无 HTTP / 配置依赖）。

## 为什么不是用户 JWT

Prometheus / VictoriaMetrics / Grafana Agent 是**机器**：固定 IP、无用户会话、
没有令牌签发流程，也无法计算「时间戳 + nonce + body」的 HMAC 回调签名
（``app/domain/modelops/callback_security.py`` 那套是给 Adapter 回调用的，
抓取端不支持，强行套用等于让所有抓取配置失效）。因此这里用**静态 Bearer 令牌**
（HTTP 标准认证方案，抓取端配置里一行 ``authorization.credentials`` 即可），
外加可选的来源网段白名单做纵深防御。

## 失败关闭（fail-closed）的边界

- 配置了令牌 → 任何环境、任何来源都必须带对令牌，否则 401。**不存在**
  「配置了但放行匿名」的分支：这一条是本模块最危险的失败模式，测试用变异
  验证过（把比较改成恒真，用例必须变红）。
- 未配置令牌 → ``local`` 环境放行（开发便利，与 ``local_header_auth`` 同一口径），
  非本地环境**请求级拒绝**（503），而不是启动失败。理由：
  - 「升级即启动失败」会让一次安全加固变成部署事故，且会连带影响 API/Worker
    一起起不来；
  - 请求级 503 只会让抓取端明确报错（Prometheus 记 ``up == 0``），其余接口
    完全不受影响，运维看到告警后补配置即可。
- 令牌比较用 :func:`hmac.compare_digest`（恒定时间），与仓库既有验签实现一致。
- 来源地址只取 ASGI 对端地址，**不读 X-Forwarded-For**：该头可伪造，
  一旦采信等于把网段白名单交给客户端决定。反向代理场景应把代理地址写进白名单，
  或只用令牌。

本模块不记录、不回显令牌：错误文案与日志由 API 层统一生成。
"""

from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class MetricsAccessOutcome(StrEnum):
    """一次 ``/metrics`` 请求的判定结果。"""

    ALLOWED = "allowed"
    # 未带令牌或令牌不匹配：两种情况必须对外**无法区分**。
    UNAUTHENTICATED = "unauthenticated"
    # 客户端地址不在配置的网段内（仅在配置了白名单时可能出现）。
    SOURCE_NOT_ALLOWED = "source_not_allowed"
    # 非本地环境且未配置令牌：服务端配置缺失，拒绝抓取。
    NOT_CONFIGURED = "not_configured"


def parse_source_networks(values: Iterable[str]) -> tuple[IPNetwork, ...]:
    """把配置里的 CIDR/单地址解析为网段元组。

    ``strict=True``：拒绝 ``10.1.2.3/8`` 这类带主机位的写法——它多半是手误，
    静默按 ``10.0.0.0/8`` 生效会让白名单比配置者以为的更宽。
    不带前缀的地址（如 ``10.1.2.3``）按单主机 /32（或 /128）处理。
    """
    networks: list[IPNetwork] = []
    for raw in values:
        value = raw.strip()
        if not value:
            raise ValueError("来源网段不得为空")
        if value != raw:
            raise ValueError("来源网段不得包含首尾空白")
        try:
            networks.append(ipaddress.ip_network(value, strict=True))
        except ValueError as exc:
            raise ValueError(f"来源网段格式非法：{raw}") from exc
    return tuple(networks)


def token_matches(presented: str | None, expected: str) -> bool:
    """恒定时间比较；未配置期望值或未提供令牌一律不匹配。"""
    if not expected or presented is None:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def source_allowed(client_host: str | None, networks: tuple[IPNetwork, ...]) -> bool:
    """客户端地址是否落在白名单内；未配置白名单时不限制来源。

    地址无法解析（``None``、ASGI 测试客户端的 ``"testclient"``、代理注入的
    非法值）时，只要配置了白名单就判为不允许——宁可可抓取失败，也不放行
    一个来源不明的请求。
    """
    if not networks:
        return True
    if not client_host:
        return False
    try:
        address = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    return any(address in network for network in networks)


@dataclass(frozen=True, slots=True)
class MetricsAccessPolicy:
    """由配置解析出的不可变访问策略。"""

    token: str
    networks: tuple[IPNetwork, ...]
    is_local: bool

    @property
    def requires_token(self) -> bool:
        return bool(self.token)


def build_metrics_access_policy(
    *,
    token: str,
    allowed_source_networks: Iterable[str],
    is_local: bool,
) -> MetricsAccessPolicy:
    """装配策略；网段非法时抛 ``ValueError``（启动自检已提前拦截）。"""
    return MetricsAccessPolicy(
        token=token,
        networks=parse_source_networks(allowed_source_networks),
        is_local=is_local,
    )


def decide_metrics_access(
    policy: MetricsAccessPolicy,
    *,
    presented_token: str | None,
    client_host: str | None,
) -> MetricsAccessOutcome:
    """按「来源 → 是否配置 → 令牌」的顺序判定。

    来源先判：来源被拒时无论令牌对错都返回同一个结果，不给远端探测
    「令牌是否正确」的信号。
    """
    if not source_allowed(client_host, policy.networks):
        return MetricsAccessOutcome.SOURCE_NOT_ALLOWED
    if not policy.requires_token:
        if policy.is_local:
            return MetricsAccessOutcome.ALLOWED
        return MetricsAccessOutcome.NOT_CONFIGURED
    if not token_matches(presented_token, policy.token):
        return MetricsAccessOutcome.UNAUTHENTICATED
    return MetricsAccessOutcome.ALLOWED


__all__ = [
    "IPNetwork",
    "MetricsAccessOutcome",
    "MetricsAccessPolicy",
    "build_metrics_access_policy",
    "decide_metrics_access",
    "parse_source_networks",
    "source_allowed",
    "token_matches",
]
