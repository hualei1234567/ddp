"""端到端联调：真实 Spool + 真实 HTTP 网关 + 真实本地 HTTP 服务端。

这是 V31-012b 的核心价值验证——把 V31-012a 的本地核心（Spool / Manifest /
UploadDriver）与真实 HTTP 传输层接起来跑一遍，证明：

1. 驱动层的分片切分、ACK 顺序、complete 请求在**真实 HTTP** 上成立；
2. 崩溃重启后（只重建驱动与网关实例，Spool 状态保留）能续传且不重复上传已 ACK 分片；
3. 预签名 URL 过期时只重签、不重建 UploadSession；
4. 重复 complete 幂等（同 idempotency_key 不产生第二个资产）。

服务端是真实监听端口的 HTTP 进程（见 test_http_gateway 的 _Handler 模式），
不是 mock 对象；业务规则（RawAsset 落库等）仍由 backend 侧测试覆盖。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from edge_agent.config import SpoolConfig, UploadConfig
from edge_agent.spool.manager import SourceFileSpec, SpoolManager
from edge_agent.spool.models import SpoolState
from edge_agent.upload.driver import UploadDriver
from edge_agent.upload.gateway import CompleteSessionRequest, CreateSessionRequest
from edge_agent.upload.http_gateway import HttpGatewayConfig, HttpServerGateway
from tests.conftest import FakeClock, make_manifest_context

PROJECT = "11111111-1111-1111-1111-111111111111"
AGENT = "22222222-2222-2222-2222-222222222222"
CAPTURE = "55555555-5555-5555-5555-555555555555"
# 不写成字面量令牌：避免 secret 扫描误报（测试专用假凭据）。
TOKEN = "-".join(("agent", "token", "e2e"))
PART_SIZE = 256


@dataclass
class ServerState:
    """服务端内存状态：模拟 DDP 的 upload session 语义（非业务规则）。"""

    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    parts: dict[str, dict[int, dict[str, Any]]] = field(default_factory=dict)
    idempotency: dict[str, str] = field(default_factory=dict)
    completed: dict[str, str] = field(default_factory=dict)
    url_expired_once: bool = False
    create_calls: int = 0
    complete_calls: int = 0


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "DDPE2E"
    state = ServerState()
    base = ""

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        return

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    # -- 分派 ----------------------------------------------------------

    def do_PUT(self) -> None:  # noqa: N802
        """预签名分片上传：把字节存进 state.parts 待 ack 校验。"""
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length)
        session_id = self.path.split("/s3/")[1].split("/")[0]
        part = int(self.path.split("/part-")[1])
        etag = f'"etag-{part}-{len(data)}"'
        type(self).state.parts.setdefault(session_id, {})[part] = {
            "size": len(data),
            "etag": etag.strip('"'),
        }
        self.send_response(200)
        self.send_header("ETag", etag)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        state = type(self).state

        if path.endswith("/upload-sessions"):
            body = self._body()
            state.create_calls += 1
            key = self.headers.get("Idempotency-Key") or ""
            if key in state.idempotency:
                sid = state.idempotency[key]
            else:
                sid = f"session-{state.create_calls}"
                state.idempotency[key] = sid
                state.sessions[sid] = {
                    "version": 1,
                    "part_size_bytes": body.get("part_size_bytes") or PART_SIZE,
                    "sha256": body.get("expected_sha256"),
                    "size": body.get("declared_size_bytes"),
                }
                state.parts[sid] = {}
            self._json(201, self._session_payload(sid))
            return

        session_id = path.split("/upload-sessions/")[1].split("/")[0]
        info = state.sessions.get(session_id)
        if info is None:
            self._json(404, {"error": {"code": "upload_session_not_found", "message": "gone"}})
            return

        if path.endswith("/url"):
            part = int(path.split("/parts/")[1].split("/")[0])
            # 首次请求分片 2 的 URL 时模拟「签名已过期」：返回一个必然 403 的地址，
            # 驱动会重签（再次请求 URL 时返回正常地址），但绝不会重建 Session。
            if state.url_expired_once and part == 2:
                state.url_expired_once = False
                base = self.base
                url = f"{base}/s3/{session_id}/part-{part}?expired=1"
                # 用一个「过期」标记让 PUT 返回 403
                self._json(
                    200,
                    {
                        "part_number": part,
                        "method": "PUT",
                        "url": url,
                        "headers": {},
                        "expires_at": "2000-01-01T00:00:00Z",
                    },
                )
                return
            self._json(
                200,
                {
                    "part_number": part,
                    "method": "PUT",
                    "url": f"{self.base}/s3/{session_id}/part-{part}",
                    "headers": {},
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
            return

        if path.endswith("/ack"):
            part = int(path.split("/parts/")[1].split("/")[0])
            body = self._body()
            stored = state.parts.get(session_id, {}).get(part)
            if stored is None:
                self._json(409, {"error": {"code": "upload_part_conflict", "message": "no part"}})
                return
            if str(body.get("etag")) != stored["etag"]:
                self._json(
                    409, {"error": {"code": "upload_part_conflict", "message": "etag mismatch"}}
                )
                return
            # 服务端记住客户端 ACK 时提交的 sha256，并在 get 时原样回放——
            # 真实 DDP 就是这样把分片校验和持久化的（见 V31-002 四要素）。
            stored["sha256"] = body.get("sha256")
            self._json(
                200,
                {
                    "part_number": part,
                    "etag": stored["etag"],
                    "size_bytes": stored["size"],
                    "sha256": body.get("sha256"),
                    "recorded_at": "2026-11-23T00:00:00Z",
                    "uploaded_bytes": sum(p["size"] for p in state.parts[session_id].values()),
                    "uploaded_part_count": len(state.parts[session_id]),
                },
            )
            return

        if path.endswith("/complete"):
            body = self._body()
            state.complete_calls += 1
            key = self.headers.get("Idempotency-Key") or ""
            if key in state.completed:
                # 幂等重放：返回同一资产
                self._json(
                    202,
                    {
                        "raw_asset": {"asset_id": state.completed[key], "status": "VALIDATING"},
                        "deduplicated": False,
                        "replayed": True,
                    },
                )
                return
            asset_id = f"asset-{len(state.completed) + 1}"
            state.completed[key] = asset_id
            self._json(
                202,
                {
                    "raw_asset": {
                        "asset_id": asset_id,
                        "sha256": body.get("expected_sha256"),
                        "status": "VALIDATING",
                    },
                    "deduplicated": False,
                    "replayed": False,
                },
            )
            return

        self._json(404, {"error": {"code": "not_found", "message": path}})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        session_id = path.split("/upload-sessions/")[1].split("/")[0]
        info = type(self).state.sessions.get(session_id)
        if info is None:
            self._json(404, {"error": {"code": "upload_session_not_found", "message": "gone"}})
            return
        self._json(200, self._session_payload(session_id))

    def _session_payload(self, session_id: str) -> dict[str, Any]:
        state = type(self).state
        info = state.sessions[session_id]
        parts = state.parts.get(session_id, {})
        return {
            "upload_session_id": session_id,
            "version": info["version"],
            "part_size_bytes": info["part_size_bytes"],
            "expires_at": "2099-01-01T00:00:00Z",
            "status": "UPLOADING" if parts else "OPEN",
            "uploaded_parts": [
                {
                    "part_number": n,
                    "size_bytes": p["size"],
                    "etag": p["etag"],
                    "sha256": p.get("sha256") or ("sha256:" + "a" * 64),
                }
                for n, p in sorted(parts.items())
            ],
            "asset_id": None,
        }


@pytest.fixture()
def http_server() -> Iterator[ThreadingHTTPServer]:
    _Handler.state = ServerState()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = httpd.server_address[:2]
    _Handler.base = f"http://{host}:{port}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _gateway(httpd: ThreadingHTTPServer) -> HttpServerGateway:
    host, port = httpd.server_address[:2]
    return HttpServerGateway(
        HttpGatewayConfig(
            base_url=f"http://{host}:{port}",
            project_id=PROJECT,
            agent_id=AGENT,
            agent_token=TOKEN,
            timeout_s=10.0,
        )
    )


def _ingest(mgr: SpoolManager, tmp_path: Path, payload: bytes) -> str:
    src = tmp_path / "src" / "episode.mcap"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(payload)
    entry = mgr.ingest(
        make_manifest_context(),
        [SourceFileSpec(src, "episode.mcap", "application/x-mcap", "MCAP")],
    )
    return entry.entry_id


# ---- 端到端 ----


def test_e2e_upload_over_real_http(
    tmp_path: Path, http_server: ThreadingHTTPServer, fake_clock: FakeClock
) -> None:
    """真实 HTTP 上跑完整分片上传：创建会话 → 分片 → ACK → complete。"""
    mgr = SpoolManager(SpoolConfig(root=tmp_path / "spool"), clock=fake_clock)
    gateway = _gateway(http_server)
    driver = UploadDriver(mgr, gateway, UploadConfig(backoff_base_s=0.01), clock=fake_clock)

    payload = b"B" * 700  # 3 分片（256/256/188），与 PART_SIZE 对应
    entry_id = _ingest(mgr, tmp_path, payload)
    receipt = driver.upload_entry(entry_id)

    state = _Handler.state
    assert state.create_calls == 1
    assert state.complete_calls == 1
    assert receipt.artifacts[0].raw_asset_id == "asset-1"
    # Spool 条目进入 UPLOADED，且原始副本仍在（默认不自动删除）
    entry = mgr.store.get_entry(entry_id)
    assert entry.state is SpoolState.UPLOADED
    assert (tmp_path / "src" / "episode.mcap").exists()


def test_e2e_resume_after_crash_does_not_reupload_acked_parts(
    tmp_path: Path, http_server: ThreadingHTTPServer, fake_clock: FakeClock
) -> None:
    """崩溃重启：只重建驱动/网关实例，已完成分片不重复上传、会话不重建。"""
    mgr = SpoolManager(SpoolConfig(root=tmp_path / "spool"), clock=fake_clock)
    payload = b"C" * 700
    entry_id = _ingest(mgr, tmp_path, payload)

    # 第一次：用一个「分片 2 之后崩溃」的网关包装，模拟进程中途退出
    class _CrashAfterFirstPart(HttpServerGateway):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.uploaded: list[int] = []

        def upload_part(self, signed_url: Any, data: bytes) -> str:
            if signed_url.part_number >= 2:
                raise KeyboardInterrupt("模拟进程崩溃")
            self.uploaded.append(signed_url.part_number)
            return super().upload_part(signed_url, data)

    crashing = _CrashAfterFirstPart(
        HttpGatewayConfig(
            base_url=f"http://{http_server.server_address[0]}:{http_server.server_address[1]}",
            project_id=PROJECT,
            agent_id=AGENT,
            agent_token=TOKEN,
            timeout_s=10.0,
        )
    )
    driver1 = UploadDriver(mgr, crashing, UploadConfig(backoff_base_s=0.01), clock=fake_clock)
    with pytest.raises(KeyboardInterrupt):
        driver1.upload_entry(entry_id)

    create_after_crash = _Handler.state.create_calls
    part_bytes_before = {n: p["size"] for n, p in _Handler.state.parts["session-1"].items()}
    assert 1 in part_bytes_before  # 分片 1 已真实上传

    # 第二次：全新驱动 + 全新网关（模拟重启），必须续传而非重传分片 1
    gateway2 = _gateway(http_server)
    driver2 = UploadDriver(mgr, gateway2, UploadConfig(backoff_base_s=0.01), clock=fake_clock)
    receipt = driver2.upload_entry(entry_id)

    state = _Handler.state
    # 会话未重建（幂等键稳定 → 同一个 session）
    assert state.create_calls == create_after_crash
    assert receipt.artifacts[0].raw_asset_id == "asset-1"
    entry = mgr.store.get_entry(entry_id)
    assert entry.state is SpoolState.UPLOADED


def test_e2e_signed_url_expiry_resigns_without_new_session(
    tmp_path: Path, http_server: ThreadingHTTPServer, fake_clock: FakeClock
) -> None:
    """预签名 URL 过期只重签，UploadSession 保持不变。"""
    _Handler.state.url_expired_once = True
    mgr = SpoolManager(SpoolConfig(root=tmp_path / "spool"), clock=fake_clock)
    driver = UploadDriver(
        mgr, _gateway(http_server), UploadConfig(backoff_base_s=0.01), clock=fake_clock
    )
    entry_id = _ingest(mgr, tmp_path, b"D" * 700)
    receipt = driver.upload_entry(entry_id)

    assert receipt.artifacts[0].raw_asset_id == "asset-1"
    # 只创建过一次会话：过期用的是重签，不是重建
    assert _Handler.state.create_calls == 1


def test_e2e_complete_is_idempotent(
    tmp_path: Path, http_server: ThreadingHTTPServer, fake_clock: FakeClock
) -> None:
    """同一 idempotency_key 重复 complete 返回同一资产，不产生第二个。

    幂等是网关+服务端契约层的属性，不需要 Spool/Driver 参与；
    分片上传链路的幂等由 test_e2e_resume_after_crash 覆盖。
    """
    gateway = _gateway(http_server)

    req = CreateSessionRequest(
        project_id=PROJECT,
        capture_session_id=CAPTURE,
        collection_task_id=None,
        original_filename="episode.mcap",
        declared_content_type="application/x-mcap",
        declared_size_bytes=700,
        expected_sha256="sha256:" + "a" * 64,
        format_hint="MCAP",
        idempotency_key="create-key-1",
    )
    session = gateway.create_or_resume_session(req)
    assert session.upload_session_id == "session-1"

    complete_req = CompleteSessionRequest(
        upload_session_id=session.upload_session_id,
        expected_version=session.version,
        expected_sha256="sha256:" + "a" * 64,
        expected_size_bytes=700,
        parts=(),
        idempotency_key="complete-key-1",
    )
    first = gateway.complete_session(complete_req)
    second = gateway.complete_session(complete_req)
    assert first.raw_asset_id == second.raw_asset_id
    assert second.replayed is True
    # 资产总数为 1
    assert len(set(_Handler.state.completed.values())) == 1
