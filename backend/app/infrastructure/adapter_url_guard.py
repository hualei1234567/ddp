"""出站 Adapter URL 的 SSRF 守卫。

Adapter endpoint 来自部署配置（当前接线范围：``DDP_TRAINING_ADAPTER_ENDPOINTS``；
模型平台网关尚未调用本守卫），不是用户输入；但配置错误、内网误配或
部署侧 DNS 被污染时，服务端请求仍可能被引向环回、内网或云元数据地址。
本模块在**每次**发请求前解析主机名并做地址筛查，失败即拒绝（fail-closed）。

规则：

- 只允许 ``http`` / ``https``；
- 拒绝 URL 内嵌凭据（``user:pass@host``），避免凭据进入日志或重定向；
- 解析主机名的全部 A/AAAA 记录，**任一**命中以下范围即拒绝：未指定地址
  （``0.0.0.0`` / ``::``）、环回、链路本地（含 ``169.254.0.0/16`` 云元数据）、
  私有网段（``10/8``、``172.16/12``、``192.168/16``、``fc00::/7``）、组播、
  保留地址；``::ffff:127.0.0.1`` 这类 IPv4-mapped 地址先还原为 IPv4 再判定，
  已废弃的 IPv4-compatible 形式（``::7f00:1``）与任何非公网地址（如
  ``100.64/10`` CGNAT）同样拒绝；
- 主机名解析失败一律拒绝，不做"解析不了就放行"；
- 不做结果缓存，每次请求都重新解析。

DNS 重绑定（TOCTOU）评估
------------------------

本守卫的解析与 httpx 实际建连的解析是两次独立查询，理论上存在窗口期。
选择不在此处"按解析出的 IP 建连"的理由：

1. httpx 0.28 没有公开的解析器 / network backend 注入点。按 IP 建连只能
   替换 ``HTTPTransport._pool``（依赖私有 API，升级会静默失效）或改用 IP
   直连并放弃 https 的 SNI 与证书主机名校验（等于削弱 TLS），两者都不可接受；
2. endpoint 只来自部署配置，攻击者必须先控制部署侧 DNS 或配置文件；
3. 每次请求重新解析且不跟随重定向（``adapter_http._send`` 强制
   ``follow_redirects=False``），可利用面被压缩到单次请求内的毫秒级竞态。

结论：接受该残余风险并在此显式记录。若未来 endpoint 允许用户配置，必须
先改为连接级 IP 固定（引入受支持的解析器注入方案），再开放该能力。

本地例外
--------

``allow_private=True`` 会**完全跳过**地址筛查（仅保留 scheme 与凭据校验），
用于本地 docker-compose 的 ``local-adapter`` 等服务名 endpoint。默认关闭，
由 ``DDP_TRAINING_ADAPTER_ALLOW_PRIVATE`` 显式开启。开启后本机可被当作
访问内网与云元数据的跳板，只能用于本地开发，不得用于共享或生产环境。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from app.infrastructure.adapter_http import AdapterHttpFailure

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}
# 已废弃的 IPv4-compatible IPv6 形式（``::7f00:1`` 表示 127.0.0.1）。它不在
# ipaddress 的 private/reserved 集合里，必须显式拦截；``::`` 与 ``::1`` 会先被
# 未指定/环回判定拦下，``::ffff:`` 映射地址会先还原成 IPv4。
_IPV4_COMPATIBLE_V6 = ipaddress.IPv6Network("::/96")

_Address = ipaddress.IPv4Address | ipaddress.IPv6Address


def ensure_safe_adapter_url(url: str, *, context: str, allow_private: bool = False) -> None:
    """校验 ``url`` 可安全出站；不安全时抛 :class:`AdapterHttpFailure`。

    ``context`` 是调用方拼好的中文前缀（如 ``"训练 Adapter mock 提交训练"``），
    错误消息直接进入领域错误摘要，便于运维定位是哪条配置被拒。

    本函数只做校验，不建立连接；位于 ``adapter_failure_as`` 上下文内调用时，
    失败会被统一映射为 ``TrainingAdapterUnavailableError``（→ HTTP 503）。
    """
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise AdapterHttpFailure(f"{context} 的地址非法：{url}") from exc

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise AdapterHttpFailure(f"{context} 的地址协议不被允许（仅 http/https）：{url}")
    if parsed.username is not None or parsed.password is not None:
        raise AdapterHttpFailure(f"{context} 的地址不得内嵌凭据：{url}")
    if not host:
        raise AdapterHttpFailure(f"{context} 的地址非法：{url}")

    if allow_private:
        # 显式本地例外：跳过地址筛查，风险见模块 docstring。
        return

    addresses = _resolve_addresses(
        host,
        port or _DEFAULT_PORTS[parsed.scheme],
        context=context,
        url=url,
    )
    for address in addresses:
        reason = _blocked_reason(address)
        if reason is not None:
            raise AdapterHttpFailure(
                f"{context} 的地址 {host} 解析到{reason} {address}，已拒绝：{url}"
            )


def _resolve_addresses(
    host: str,
    port: int,
    *,
    context: str,
    url: str,
) -> tuple[_Address, ...]:
    """解析主机名的全部 A/AAAA 地址；解析失败按不可用处理。"""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise AdapterHttpFailure(
            f"{context} 的主机名无法解析：{host}（{type(exc).__name__}）"
        ) from exc

    addresses: list[_Address] = []
    for family, _type, _proto, _canonical, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        # 带 scope id 的链路本地地址（fe80::1%en0）去掉 zone 后再判定。
        raw = str(sockaddr[0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise AdapterHttpFailure(f"{context} 的主机名 {host} 解析结果非法：{raw}") from exc
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if address not in addresses:
            addresses.append(address)

    if not addresses:
        raise AdapterHttpFailure(f"{context} 的主机名未解析出 IP 地址：{host}（{url}）")
    return tuple(addresses)


def _blocked_reason(address: _Address) -> str | None:
    """返回地址被拒的中文原因；公网地址返回 ``None``。"""
    if address.is_unspecified:
        return "未指定地址"
    if address.is_loopback:
        return "环回地址"
    if address.is_link_local:
        return "链路本地地址"
    if address.is_private:
        return "私有地址"
    if address.is_multicast:
        return "组播地址"
    if isinstance(address, ipaddress.IPv6Address) and address in _IPV4_COMPATIBLE_V6:
        return "IPv4 兼容地址"
    if address.is_reserved:
        return "保留地址"
    if not address.is_global:
        # 兜底：IANA 特殊用途网段（如 100.64/10 CGNAT、192.0.0.0/24）即使不在上面
        # 的类别里也不是可出站的公网地址。
        return "非公网地址"
    return None


__all__ = ["ensure_safe_adapter_url"]
