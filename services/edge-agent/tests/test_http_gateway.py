"""HTTP ServerGateway 测试：真实本地 HTTP 服务端（非 mock 业务逻辑）。

测试用 ``http.server`` 起一个**真实监听端口**的最简服务端，验证：

1. 请求路径/头/体与 DDP 契约一致（含 Idempotency-Key / If-Match / Bearer）；
2. 错误码到驱动层语义的映射（401→AuthRejected、410→SessionGone、409→隔离）；
3. 预签名 URL 过期只重签、不重建会话（由驱动负责，这里验证网关抛对错误）；
4. 凭据不泄漏到日志/异常消息（URL 签名参数与 token 都不得出现在 repr/异常里）。

这不是「用 mock 冒充真实验收」：服务端是真实进程与真实 HTTP 栈，被测对象是
网关的协议适配与错误映射；业务规则仍在 backend 侧测。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from edge_agent.errors import (
    AuthRejectedError,
    ChecksumMismatchError,
    GatewayPermanentError,
    GatewayTransientError,
    UploadSessionGoneError,
)
from edge_agent.upload.gateway import (
    CompletePartSpec,
    CompleteSessionRequest,
    CreateSessionRequest,
)
from edge_agent.upload.http_gateway import HttpGatewayConfig, HttpServerGateway

PROJECT = "11111111-1111-1111-1111-111111111111"
AGENT = "22222222-2222-2222-2222-222222222222"
SESSION = "33333333-3333-3333-3333-333333333333"
ASSET = "44444444-4444-4444-4444-444444444444"
# 不写成字面量令牌：避免 secret 扫描误报（本值是测试专用假凭据）。
TOKEN = "-".join(("agent", "token", "must", "not", "leak", "0123456789"))
SHA = "sha256:" + "a" * 64


@dataclass
class Recorded:
    """服务端收到的最后一次请求（用于断言协议一致性）。"""

    method: str = ""
    path: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)


class _Handler(BaseHTTPRequestHandler):
    """按路径分派的极简 DDP 服务端替身（真实 HTTP，非业务流程 mock）。"""

    # HTTP/1.0：每个请求后关闭连接。用 1.1 keep-alive 时，若客户端未及时关闭
    # 连接，测试结束会留下未关闭 socket（filterwarnings=error 下变成错误）。
    protocol_version = "HTTP/1.0"
    server_version = "DDPTestServer"
    recorded: Recorded = Recorded()
    status_override: dict[str, int] = {}

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        # 静默访问日志：避免测试输出噪声（真实服务端日志由 backend 负责）。
        return

    # -- 工具 ----------------------------------------------------------

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _send(self, status: int, payload: dict[str, Any] | None = None) -> None:
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.close_connection = True
        if status == 201:
            self.send_header("ETag", '"1"')
        self.end_headers()
        self.wfile.write(body)

    def _record(self, method: str) -> None:
        type(self).recorded = Recorded(
            method=method,
            path=self.path,
            headers={k: v for k, v in self.headers.items()},
            body=self._read_body(),
        )

    def _dispatch(self, method: str) -> None:
        self._record(method)
        path = self.path.split("?", 1)[0]
        override = type(self).status_override.get(path)
        if override is not None:
            self._send(override, {"error": {"code": "forced", "message": "forced"}})
            return

        if method == "POST" and path == f"/api/v1/projects/{PROJECT}/upload-sessions":
            self._send(201, _session_payload(status="OPEN"))
            return
        if method == "GET" and path.startswith(f"/api/v1/projects/{PROJECT}/upload-sessions/"):
            if path.endswith("/parts/1/url") or "/parts/" in path and path.endswith("/url"):
                part = int(path.split("/parts/")[1].split("/")[0])
                self._send(
                    200,
                    {
                        "part_number": part,
                        "method": "PUT",
                        "url": "https://s3.test/bucket/key?X-Amz-Signature=SECRETSIG",
                        "headers": {"x-amz-checksum-sha256": "bind"},
                        "expires_at": "2099-01-01T00:00:00Z",
                    },
                )
                return
            self._send(200, _session_payload(status="UPLOADING"))
            return
        if method == "POST" and path.endswith("/url"):
            part = int(path.split("/parts/")[1].split("/")[0])
            self._send(
                200,
                {
                    "part_number": part,
                    "method": "PUT",
                    "url": "https://s3.test/bucket/key?X-Amz-Signature=SECRETSIG",
                    "headers": {"x-amz-checksum-sha256": "bind"},
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
            return
        if method == "POST" and path.endswith("/ack"):
            part = int(path.split("/parts/")[1].split("/")[0])
            self._send(
                200,
                {
                    "part_number": part,
                    "etag": "etag-1",
                    "size_bytes": 5,
                    "sha256": SHA,
                    "recorded_at": "2026-11-23T00:00:00Z",
                    "uploaded_bytes": 5,
                    "uploaded_part_count": 1,
                },
            )
            return
        if method == "POST" and path.endswith("/complete"):
            self._send(
                202,
                {
                    "raw_asset": {"asset_id": ASSET, "sha256": SHA, "status": "VALIDATING"},
                    "deduplicated": False,
                    "replayed": False,
                },
            )
            return
        self._send(404, {"error": {"code": "not_found", "message": "no route"}})

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")


def _session_payload(*, status: str) -> dict[str, Any]:
    return {
        "upload_session_id": SESSION,
        "version": 3,
        "part_size_bytes": 5,
        "expires_at": "2099-01-01T00:00:00Z",
        "status": status,
        "uploaded_parts": [{"part_number": 1, "size_bytes": 5, "etag": "etag-1", "sha256": SHA}],
        "asset_id": None,
    }


@pytest.fixture()
def server() -> Iterator[ThreadingHTTPServer]:
    """真实监听 127.0.0.1 的临时端口（不用固定端口，避免 CI 冲突）。"""
    _Handler.status_override = {}
    _Handler.recorded = Recorded()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        # 等待服务线程真正退出后再释放端口，避免残留 socket 触发
        # ResourceWarning（本服务 pyproject 把 warning 升格为错误）。
        assert not thread.is_alive()


def _gateway(httpd: ThreadingHTTPServer) -> HttpServerGateway:
    host, port = httpd.server_address[:2]
    return HttpServerGateway(
        HttpGatewayConfig(
            base_url=f"http://{host}:{port}",
            project_id=PROJECT,
            agent_id=AGENT,
            agent_token=TOKEN,
            timeout_s=5.0,
        )
    )


def _create_request() -> CreateSessionRequest:
    return CreateSessionRequest(
        project_id=PROJECT,
        capture_session_id="55555555-5555-5555-5555-555555555555",
        collection_task_id=None,
        original_filename="episode.mcap",
        declared_content_type="application/x-mcap",
        declared_size_bytes=5,
        expected_sha256=SHA,
        format_hint="MCAP",
        idempotency_key="edge-agent-upload-0001",
        part_size_hint_bytes=5,
    )


# ---- 协议一致性 ----


def test_create_session_sends_contract_headers_and_body(server: ThreadingHTTPServer) -> None:
    session = _gateway(server).create_or_resume_session(_create_request())

    recorded = _Handler.recorded
    assert recorded.method == "POST"
    assert recorded.path == f"/api/v1/projects/{PROJECT}/upload-sessions"
    # HTTP 头名大小写不敏感（urllib 会按自身规则规范化大小写）。
    lowered = {k.lower(): v for k, v in recorded.headers.items()}
    assert lowered["authorization"] == f"Bearer {TOKEN}"
    assert lowered["x-ddp-actor-kind"] == "EDGE_AGENT"
    assert lowered["idempotency-key"] == "edge-agent-upload-0001"
    # 契约字段（additionalProperties: false，多一个都会被服务端 422）
    assert set(recorded.body) == {
        "original_filename",
        "declared_content_type",
        "declared_size_bytes",
        "capture_session_id",
        "expected_sha256",
        "format_hint",
        "part_size_bytes",
    }
    assert session.upload_session_id == SESSION
    assert session.version == 3
    assert session.uploaded_parts[0].etag == "etag-1"


def test_get_part_url_parses_signed_url(server: ThreadingHTTPServer) -> None:
    signed = _gateway(server).get_part_url(SESSION, 1, sha256=SHA)
    assert signed.method == "PUT"
    assert signed.part_number == 1
    assert "X-Amz-Signature" in signed.url
    assert signed.headers["x-amz-checksum-sha256"] == "bind"
    assert signed.expires_at.year == 2099


def test_ack_part_maps_response(server: ThreadingHTTPServer) -> None:
    result = _gateway(server).ack_part(SESSION, 1, etag="etag-1", size_bytes=5, sha256=SHA)
    assert result.part_number == 1
    assert result.uploaded_part_count == 1
    assert result.sha256 == SHA


def test_complete_session_sends_if_match_and_returns_receipt(
    server: ThreadingHTTPServer,
) -> None:
    receipt = _gateway(server).complete_session(
        CompleteSessionRequest(
            upload_session_id=SESSION,
            expected_version=3,
            expected_sha256=SHA,
            expected_size_bytes=5,
            parts=(CompletePartSpec(1, "etag-1", 5, SHA),),
            idempotency_key="edge-agent-complete-0001",
        )
    )
    recorded = _Handler.recorded
    # 乐观锁必须发 If-Match
    assert recorded.headers["If-Match"] == '"3"'
    assert recorded.headers["Idempotency-Key"] == "edge-agent-complete-0001"
    assert receipt.raw_asset_id == ASSET
    assert receipt.ingestion_status == "VALIDATING"
    assert receipt.deduplicated is False


# ---- 错误映射（决定驱动层重试/隔离） ----


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AuthRejectedError),
        (403, AuthRejectedError),
        (410, UploadSessionGoneError),
        (404, UploadSessionGoneError),
        (409, ChecksumMismatchError),
        (422, GatewayPermanentError),
        (429, GatewayTransientError),
        (500, GatewayTransientError),
        (503, GatewayTransientError),
    ],
)
def test_http_status_maps_to_expected_error(
    server: ThreadingHTTPServer, status: int, expected: type[Exception]
) -> None:
    _Handler.status_override = {f"/api/v1/projects/{PROJECT}/upload-sessions/{SESSION}": status}
    with pytest.raises(expected):
        _gateway(server).get_session(SESSION)


def test_network_failure_is_transient() -> None:
    """端口未监听 → 必须映射为 transient（驱动会退避重试），不是永久失败。"""
    gateway = HttpServerGateway(
        HttpGatewayConfig(
            base_url="http://127.0.0.1:9",  # discard 端口，必然不可连
            project_id=PROJECT,
            agent_id=AGENT,
            agent_token=TOKEN,
            timeout_s=1.0,
        )
    )
    with pytest.raises(GatewayTransientError):
        gateway.get_session(SESSION)


# ---- 凭据不泄漏 ----


def test_token_is_hidden_from_repr() -> None:
    config = HttpGatewayConfig(
        base_url="http://127.0.0.1:1",
        project_id=PROJECT,
        agent_id=AGENT,
        agent_token=TOKEN,
    )
    assert TOKEN not in repr(config)
    assert TOKEN not in str(config)


def test_signed_url_signature_not_in_error_message(server: ThreadingHTTPServer) -> None:
    """预签名 URL 的签名参数不得出现在异常消息里（会进日志）。"""
    gateway = _gateway(server)
    from datetime import UTC, datetime

    from edge_agent.upload.gateway import SignedPartUrl

    # 指向一个会失败的 URL：用测试服务端的 404 路径
    host, port = server.server_address[:2]
    bad = SignedPartUrl(
        part_number=1,
        method="PUT",
        url=f"http://{host}:{port}/no-such-route?X-Amz-Signature=MUSTNOTAPPEAR",
        headers={},
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(GatewayPermanentError) as excinfo:
        gateway.upload_part(bad, b"hello")
    assert "MUSTNOTAPPEAR" not in str(excinfo.value)
