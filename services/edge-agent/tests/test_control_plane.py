"""Edge Agent 控制面客户端测试：真实本地 HTTP 服务端验证配对/心跳/任务拉取/Lease。"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from edge_agent.control_plane import (
    ControlPlaneConfig,
    EdgeControlPlaneClient,
    HeartbeatPayload,
    pair_agent,
)
from edge_agent.errors import AuthRejectedError, GatewayPermanentError, GatewayTransientError

PROJECT = "11111111-1111-1111-1111-111111111111"
AGENT = "22222222-2222-2222-2222-222222222222"
ROBOT = "33333333-3333-3333-3333-333333333333"
DEVICE = "44444444-4444-4444-4444-444444444444"
SESSION = "55555555-5555-5555-5555-555555555555"
LEASE = "66666666-6666-6666-6666-666666666666"
# 拼装测试假凭据，避免触发 secret 扫描误报。
PAIR_CODE = "_".join(("ddp", "pair", "test", "code", "0001"))
AGENT_TOKEN = "_".join(("ddp", "agt", "test", "secret", "0001"))


@dataclass
class Recorded:
    method: str = ""
    path: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)


class _ControlHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "DDPControlTest"
    recorded: Recorded = Recorded()
    status_override: dict[str, int] = {}

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        return

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        return payload if isinstance(payload, dict) else {}

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def _dispatch(self, method: str) -> None:
        type(self).recorded = Recorded(
            method=method,
            path=self.path,
            headers={k: v for k, v in self.headers.items()},
            body=self._read_body(),
        )
        path = self.path.split("?", 1)[0]
        override = type(self).status_override.get(path)
        if override is not None:
            self._send(override, {"error": {"code": "forced", "message": "forced"}})
            return

        if method == "POST" and path == "/api/v1/edge-agents/pair":
            self._send(
                201,
                {
                    "agent_id": AGENT,
                    "project_id": PROJECT,
                    "robot_id": ROBOT,
                    "display_name": "pilot-ws-01",
                    "device_ids": [DEVICE],
                    "agent_token": AGENT_TOKEN,
                    "heartbeat_interval_seconds": 30,
                    "lease_ttl_seconds": 120,
                    "capture_grace_seconds": 60,
                },
            )
            return

        if method == "POST" and path == f"/api/v1/edge-agents/{AGENT}/heartbeat":
            self._send(
                200,
                {
                    "agent_id": AGENT,
                    "status": "ACTIVE",
                    "received_at": "2026-11-28T10:00:00Z",
                    "dispatch_paused": False,
                    "renewed_leases": [_lease_json("ACTIVE")],
                    "expired_lease_ids": [],
                },
            )
            return

        if method == "GET" and path == f"/api/v1/edge-agents/{AGENT}/assignments":
            self._send(
                200,
                {
                    "agent_id": AGENT,
                    "dispatch_paused": False,
                    "items": [
                        {
                            "capture_session_id": SESSION,
                            "code": "CAP-PILOT-001",
                            "status": "SCHEDULED",
                            "robot_id": ROBOT,
                            "active_lease_id": None,
                        }
                    ],
                },
            )
            return

        if method == "POST" and path == f"/api/v1/edge-agents/{AGENT}/leases":
            body = type(self).recorded.body
            if body.get("action") == "acquire":
                self._send(200, _lease_json("ACTIVE"))
                return
            self._send(
                200,
                {
                    **_lease_json("RELEASED"),
                    "released_at": "2026-11-28T10:05:00Z",
                    "release_reason": body.get("release_reason"),
                    "release_note": body.get("release_note"),
                },
            )
            return

        self._send(404, {"error": {"code": "not_found", "message": path}})

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")


def _lease_json(status: str) -> dict[str, Any]:
    return {
        "lease_id": LEASE,
        "project_id": PROJECT,
        "agent_id": AGENT,
        "capture_session_id": SESSION,
        "device_ids": [DEVICE],
        "status": status,
        "acquired_at": "2026-11-28T10:00:00Z",
        "expires_at": "2026-11-28T10:02:00Z",
        "last_renewed_at": "2026-11-28T10:00:00Z",
    }


@pytest.fixture()
def control_server() -> Iterator[ThreadingHTTPServer]:
    _ControlHandler.status_override = {}
    _ControlHandler.recorded = Recorded()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ControlHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _base_url(httpd: ThreadingHTTPServer) -> str:
    host, port = httpd.server_address[:2]
    return f"http://{host}:{port}"


def test_pair_agent_contract_and_token_redaction(control_server: ThreadingHTTPServer) -> None:
    creds = pair_agent(
        _base_url(control_server),
        pairing_token=PAIR_CODE,
        agent_version="0.1.0",
        os_name="linux",
        arch="arm64",
    )
    assert creds.agent_id == AGENT
    assert creds.project_id == PROJECT
    assert creds.device_ids == (DEVICE,)
    assert creds.heartbeat_interval_seconds == 30
    assert creds.agent_token == AGENT_TOKEN
    # 凭据不得出现在 repr/str
    assert AGENT_TOKEN not in repr(creds)
    assert AGENT_TOKEN not in str(creds)
    assert _ControlHandler.recorded.body == {
        "pairing_token": PAIR_CODE,
        "agent_version": "0.1.0",
        "os": "linux",
        "arch": "arm64",
    }


def test_heartbeat_assignments_and_lease_lifecycle(control_server: ThreadingHTTPServer) -> None:
    cfg = ControlPlaneConfig(
        base_url=_base_url(control_server),
        agent_id=AGENT,
        agent_token=AGENT_TOKEN,
    )
    assert AGENT_TOKEN not in repr(cfg)
    client = EdgeControlPlaneClient(cfg)

    assignments = client.list_assignments()
    assert assignments.dispatch_paused is False
    assert len(assignments.items) == 1
    assert assignments.items[0].capture_session_id == SESSION

    acquired = client.acquire_lease(SESSION, device_ids=(DEVICE,))
    assert acquired.lease_id == LEASE
    assert acquired.status == "ACTIVE"

    ack = client.send_heartbeat(
        HeartbeatPayload(
            reported_at=datetime(2026, 11, 28, 10, 0, 0, tzinfo=UTC),
            version="0.1.0",
            os="linux",
            arch="arm64",
            disk_free_bytes=10 * 1024**3,
            network_reachable=True,
            spool_pending_count=1,
            spool_pending_bytes=4096,
            upload_queue_depth=1,
            adapter_versions={"filesystem_export": "0.1.0"},
            current_session_id=SESSION,
            renew_lease_ids=(LEASE,),
        )
    )
    assert ack.status == "ACTIVE"
    assert len(ack.renewed_leases) == 1
    lowered = {k.lower(): v for k, v in _ControlHandler.recorded.headers.items()}
    assert lowered["authorization"] == f"Bearer {AGENT_TOKEN}"

    released = client.release_lease(
        release_reason="COMPLETED",
        lease_id=LEASE,
        release_note="pilot capture finished",
    )
    assert released.status == "RELEASED"
    assert released.release_reason == "COMPLETED"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AuthRejectedError),
        (403, AuthRejectedError),
        (409, GatewayPermanentError),
        (422, GatewayPermanentError),
        (429, GatewayTransientError),
        (503, GatewayTransientError),
    ],
)
def test_control_plane_error_mapping(
    control_server: ThreadingHTTPServer, status: int, expected: type[Exception]
) -> None:
    _ControlHandler.status_override = {f"/api/v1/edge-agents/{AGENT}/assignments": status}
    client = EdgeControlPlaneClient(
        ControlPlaneConfig(
            base_url=_base_url(control_server),
            agent_id=AGENT,
            agent_token=AGENT_TOKEN,
        )
    )
    with pytest.raises(expected):
        client.list_assignments()
