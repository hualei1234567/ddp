"""Edge Agent 控制面 HTTP 客户端（对齐 V31-011 ``edge_agent_routes.py``）。

实现 ADR-0047 纯出站拉取协议：

1. ``pair_agent``：使用一次性 ``pairing_token`` 向 ``POST /api/v1/edge-agents/pair``
   换取专属 ``ddp_agt_`` Agent Token 与服务端策略（心跳周期、Lease TTL、采集宽限期）；
2. ``EdgeControlPlaneClient.send_heartbeat``：上报磁盘/Spool/网络健康状态并续租 Lease；
3. ``EdgeControlPlaneClient.list_assignments``：拉取分配给本机机器人的待采集会话；
4. ``EdgeControlPlaneClient.acquire_lease`` / ``release_lease``：获取或释放采集会话互斥租约。

安全边界：
- 凭据（``pairing_token`` / ``agent_token``）在 ``repr`` 中屏蔽，不进异常消息或日志；
- 禁用 HTTP 重定向，仅允许 ``http://`` 与 ``https://`` scheme；
- 401/403 映射为 ``AuthRejectedError``（不可静默重试，需人工重新配对或轮换令牌）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from edge_agent.errors import (
    AuthRejectedError,
    GatewayPermanentError,
    GatewayTransientError,
)
from edge_agent.upload.http_gateway import (
    _build_opener,
    _error_detail,
    _parse_datetime,
    _redact,
    _require_http_url,
)

_DEFAULT_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class PairedAgentCredentials:
    """配对成功后服务端下发的 Agent 凭据与策略；``agent_token`` 不进 repr。"""

    agent_id: str
    project_id: str
    robot_id: str
    display_name: str
    device_ids: tuple[str, ...]
    agent_token: str = field(repr=False)
    heartbeat_interval_seconds: int
    lease_ttl_seconds: int
    capture_grace_seconds: int


@dataclass(frozen=True)
class ControlPlaneConfig:
    """控制面连接配置；``agent_token`` 不进 repr。"""

    base_url: str
    agent_id: str
    agent_token: str = field(repr=False)
    timeout_s: float = _DEFAULT_TIMEOUT_S
    verify_tls: bool = True

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        if not self.agent_id:
            raise ValueError("agent_id 不能为空")
        if not self.agent_token:
            raise ValueError("agent_token 不能为空")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s 必须 > 0")


@dataclass(frozen=True)
class HeartbeatPayload:
    """上报给服务端的 Edge Agent 心跳快照。"""

    reported_at: datetime
    version: str
    os: str
    arch: str
    disk_free_bytes: int
    network_reachable: bool
    spool_pending_count: int = 0
    spool_pending_bytes: int = 0
    upload_queue_depth: int = 0
    adapter_versions: Mapping[str, str] = field(default_factory=dict)
    current_session_id: str | None = None
    last_error: str | None = None
    renew_lease_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class LeaseRecord:
    """服务端返回的采集会话 Lease 记录。"""

    lease_id: str
    project_id: str
    agent_id: str
    capture_session_id: str
    device_ids: tuple[str, ...]
    status: str
    acquired_at: datetime
    expires_at: datetime
    last_renewed_at: datetime
    released_at: datetime | None = None
    release_reason: str | None = None
    release_note: str | None = None


@dataclass(frozen=True)
class HeartbeatAck:
    """服务端心跳回执，含续租与过期 Lease 清单。"""

    agent_id: str
    status: str
    received_at: datetime
    dispatch_paused: bool
    renewed_leases: tuple[LeaseRecord, ...]
    expired_lease_ids: tuple[str, ...]


@dataclass(frozen=True)
class AssignmentItem:
    """分配给本机机器人的单个采集会话。"""

    capture_session_id: str
    code: str
    status: str
    task_id: str | None = None
    batch_id: str | None = None
    robot_id: str | None = None
    location_id: str | None = None
    active_lease_id: str | None = None


@dataclass(frozen=True)
class AssignmentList:
    """服务端返回的待采集会话列表。"""

    agent_id: str
    dispatch_paused: bool
    items: tuple[AssignmentItem, ...]


def pair_agent(
    base_url: str,
    *,
    pairing_token: str,
    agent_version: str | None = None,
    os_name: str | None = None,
    arch: str | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    verify_tls: bool = True,
) -> PairedAgentCredentials:
    """使用一次性配对码向 DDP 服务端完成设备配对，返回 Agent 专属 Token 与策略。"""
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base_url 必须以 http:// 或 https:// 开头")
    if not pairing_token:
        raise ValueError("pairing_token 不能为空")
    if timeout_s <= 0:
        raise ValueError("timeout_s 必须 > 0")

    body: dict[str, Any] = {"pairing_token": pairing_token}
    if agent_version is not None:
        body["agent_version"] = agent_version
    if os_name is not None:
        body["os"] = os_name
    if arch is not None:
        body["arch"] = arch

    opener = _build_opener(verify_tls=verify_tls)
    payload = _request_json(
        opener,
        method="POST",
        root=base_url.rstrip("/"),
        path="/api/v1/edge-agents/pair",
        timeout_s=timeout_s,
        body=body,
    )
    return PairedAgentCredentials(
        agent_id=str(payload["agent_id"]),
        project_id=str(payload["project_id"]),
        robot_id=str(payload["robot_id"]),
        display_name=str(payload["display_name"]),
        device_ids=tuple(str(item) for item in (payload.get("device_ids") or ())),
        agent_token=str(payload["agent_token"]),
        heartbeat_interval_seconds=int(payload["heartbeat_interval_seconds"]),
        lease_ttl_seconds=int(payload["lease_ttl_seconds"]),
        capture_grace_seconds=int(payload["capture_grace_seconds"]),
    )


class EdgeControlPlaneClient:
    """基于标准库 urllib 的 Edge Agent 控制面客户端。"""

    def __init__(self, config: ControlPlaneConfig) -> None:
        self._config = config
        self._root = config.base_url.rstrip("/")
        self._opener = _build_opener(verify_tls=config.verify_tls)

    def send_heartbeat(self, heartbeat: HeartbeatPayload) -> HeartbeatAck:
        body: dict[str, Any] = {
            "reported_at": heartbeat.reported_at.isoformat(),
            "version": heartbeat.version,
            "os": heartbeat.os,
            "arch": heartbeat.arch,
            "adapter_versions": dict(heartbeat.adapter_versions),
            "disk_free_bytes": heartbeat.disk_free_bytes,
            "network_reachable": heartbeat.network_reachable,
            "spool_pending_count": heartbeat.spool_pending_count,
            "spool_pending_bytes": heartbeat.spool_pending_bytes,
            "upload_queue_depth": heartbeat.upload_queue_depth,
        }
        if heartbeat.current_session_id is not None:
            body["current_session_id"] = heartbeat.current_session_id
        if heartbeat.last_error is not None:
            body["last_error"] = heartbeat.last_error
        if heartbeat.renew_lease_ids is not None:
            body["renew_lease_ids"] = list(heartbeat.renew_lease_ids)

        payload = self._authed_request(
            "POST",
            f"/api/v1/edge-agents/{self._config.agent_id}/heartbeat",
            body=body,
        )
        return HeartbeatAck(
            agent_id=str(payload["agent_id"]),
            status=str(payload["status"]),
            received_at=_parse_datetime(payload["received_at"]),
            dispatch_paused=bool(payload["dispatch_paused"]),
            renewed_leases=tuple(
                _lease_from_payload(item) for item in (payload.get("renewed_leases") or ())
            ),
            expired_lease_ids=tuple(str(item) for item in (payload.get("expired_lease_ids") or ())),
        )

    def list_assignments(self) -> AssignmentList:
        payload = self._authed_request(
            "GET",
            f"/api/v1/edge-agents/{self._config.agent_id}/assignments",
        )
        items = tuple(
            AssignmentItem(
                capture_session_id=str(item["capture_session_id"]),
                code=str(item["code"]),
                status=str(item["status"]),
                task_id=str(item["task_id"]) if item.get("task_id") else None,
                batch_id=str(item["batch_id"]) if item.get("batch_id") else None,
                robot_id=str(item["robot_id"]) if item.get("robot_id") else None,
                location_id=str(item["location_id"]) if item.get("location_id") else None,
                active_lease_id=(
                    str(item["active_lease_id"]) if item.get("active_lease_id") else None
                ),
            )
            for item in (payload.get("items") or ())
        )
        return AssignmentList(
            agent_id=str(payload["agent_id"]),
            dispatch_paused=bool(payload["dispatch_paused"]),
            items=items,
        )

    def acquire_lease(
        self,
        capture_session_id: str,
        *,
        device_ids: tuple[str, ...] | None = None,
    ) -> LeaseRecord:
        body: dict[str, Any] = {
            "action": "acquire",
            "capture_session_id": capture_session_id,
        }
        if device_ids is not None:
            body["device_ids"] = list(device_ids)
        payload = self._authed_request(
            "POST",
            f"/api/v1/edge-agents/{self._config.agent_id}/leases",
            body=body,
        )
        return _lease_from_payload(payload)

    def release_lease(
        self,
        *,
        release_reason: str,
        lease_id: str | None = None,
        capture_session_id: str | None = None,
        release_note: str | None = None,
    ) -> LeaseRecord:
        body: dict[str, Any] = {
            "action": "release",
            "release_reason": release_reason,
        }
        if lease_id is not None:
            body["lease_id"] = lease_id
        if capture_session_id is not None:
            body["capture_session_id"] = capture_session_id
        if release_note is not None:
            body["release_note"] = release_note
        payload = self._authed_request(
            "POST",
            f"/api/v1/edge-agents/{self._config.agent_id}/leases",
            body=body,
        )
        return _lease_from_payload(payload)

    def _authed_request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _request_json(
            self._opener,
            method=method,
            root=self._root,
            path=path,
            timeout_s=self._config.timeout_s,
            body=body,
            headers={
                "Authorization": f"Bearer {self._config.agent_token}",
                "X-DDP-Actor-Kind": "EDGE_AGENT",
            },
        )


def _request_json(
    opener: urllib.request.OpenerDirector,
    *,
    method: str,
    root: str,
    path: str,
    timeout_s: float,
    body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{root}{path}"
    data = None
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(  # noqa: S310
        _require_http_url(url, "控制面地址"),
        data=data,
        method=method,
        headers=request_headers,
    )
    try:
        with opener.open(request, timeout=timeout_s) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise _map_control_error(exc, method=method, path=path) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GatewayTransientError(f"{method} {_redact(url)} 网络错误：{exc}") from exc
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GatewayPermanentError(f"{method} {_redact(url)} 响应不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise GatewayPermanentError(f"{method} {_redact(url)} 响应不是 JSON 对象")
    return payload


def _map_control_error(exc: urllib.error.HTTPError, *, method: str, path: str) -> Exception:
    code = exc.code
    detail = _error_detail(exc)
    where = f"{method} {_redact(path)}"
    if code in (401, 403):
        return AuthRejectedError(f"{where} 身份被拒（{code}）：{detail}")
    if code == 429 or code >= 500:
        return GatewayTransientError(f"{where} 暂时不可用（{code}）：{detail}")
    return GatewayPermanentError(f"{where} 拒绝请求（{code}）：{detail}")


def _lease_from_payload(payload: Mapping[str, Any]) -> LeaseRecord:
    return LeaseRecord(
        lease_id=str(payload["lease_id"]),
        project_id=str(payload["project_id"]),
        agent_id=str(payload["agent_id"]),
        capture_session_id=str(payload["capture_session_id"]),
        device_ids=tuple(str(item) for item in (payload.get("device_ids") or ())),
        status=str(payload["status"]),
        acquired_at=_parse_datetime(payload["acquired_at"]),
        expires_at=_parse_datetime(payload["expires_at"]),
        last_renewed_at=_parse_datetime(payload["last_renewed_at"]),
        released_at=_parse_datetime(payload["released_at"]) if payload.get("released_at") else None,
        release_reason=str(payload["release_reason"]) if payload.get("release_reason") else None,
        release_note=str(payload["release_note"]) if payload.get("release_note") else None,
    )


__all__ = [
    "AssignmentItem",
    "AssignmentList",
    "ControlPlaneConfig",
    "EdgeControlPlaneClient",
    "HeartbeatAck",
    "HeartbeatPayload",
    "LeaseRecord",
    "PairedAgentCredentials",
    "pair_agent",
]
