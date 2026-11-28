"""DDP 告警真实接收端（Alertmanager webhook 接收方）。

只用 Python 标准库（``http.server`` / ``socketserver`` / ``json``）实现，
不引入 FastAPI 等第三方框架——理由是「不新增依赖」与镜像体积最小。

端点：

============  ==========================================================
方法 / 路径    用途
============  ==========================================================
POST /alerts  接收 Alertmanager webhook payload（Alertmanager v4 格式）
GET  /alerts  列出已收到的告警（JSON 数组，最新在前，支持 ``?limit=N``）
GET  /health  健康检查（compose healthcheck 用）
GET  /stats   计数统计（总数 / firing / resolved / 按 alertname 分组）
============  ==========================================================

安全与健壮性约束：

- 请求体上限 1 MiB：超过直接 413，防止超大 body 打爆内存；
- Content-Type 必须是 ``application/json``（Alertmanager 的固定行为），否则 400；
- **不记录任何凭据**：不打印请求头、不记录来源 IP、不落盘任何 token；
- 只记告警内容本身。

启动方式：``python -m app.main``（需要工作目录在服务根下，``app`` 包才可导入）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.store import DEFAULT_DATA_DIR, AlertStore, build_stats

logger = logging.getLogger("ddp.alert-receiver")

#: 请求体大小上限（1 MiB）。Alertmanager 单次 webhook 的实际体积远小于此，
#: 超过即视为异常流量，直接拒绝而不是读进来再判断。
MAX_BODY_BYTES = 1024 * 1024

#: 判定超限后允许「丢弃」的额外字节数（分块读，不进内存）。
#:
#: 为什么需要它：HTTP/1.1 复用连接时，服务端必须在响应前消费完请求体，
#: 否则连接上的下一个请求会与残留 body 交错，协议直接损坏。
#: 这里只**丢弃**（固定 64 KiB 分块读，不保留），且总量有上限——
#: 内存占用仍是 O(1)，与真实 body 大小无关。
DRAIN_SLACK_BYTES = 64 * 1024

#: 丢弃时的分块大小
DRAIN_CHUNK_BYTES = 64 * 1024

#: 丢弃残余 body 的最长耗时（秒）。超过就放弃——宁可让连接断开，
#: 也不能让一个恶意/异常客户端把工作线程占死。
DRAIN_TIMEOUT_SECONDS = 5

#: 单次请求的 socket 超时（秒）。防止慢速连接长期占住线程。
SOCKET_TIMEOUT_SECONDS = 30

#: 允许的 Content-Type（Alertmanager 固定发 ``application/json``）
_JSON_CONTENT_TYPE = "application/json"

#: 可选 HMAC-SHA256 签名请求头名（格式：``sha256=<64-hex>``）
SIGNATURE_HEADER = "X-Alertmanager-Signature"


class _BadRequest(Exception):
    """请求不合法（返回 400）。"""


class _Unauthorized(Exception):
    """鉴权或签名校验失败（返回 401）。"""


class _PayloadTooLarge(Exception):
    """请求体超限（返回 413）。"""


def compute_hmac_signature(secret: str, body: bytes) -> str:
    """计算告警请求体的 HMAC-SHA256 签名（``sha256=<hex>``）。"""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_dedup_key(payload: dict[str, Any]) -> str:
    """为告警载荷构造去重键：``(groupKey, status, sorted fingerprints)``。

    同一告警从 ``firing`` 转为 ``resolved`` 时 ``status`` 变化，去重键不同，
    因此恢复事件永远不会被误判为重复。
    """
    group_key = str(payload.get("groupKey") or "")
    status = str(payload.get("status") or "")
    fingerprints: list[str] = []
    alerts = payload.get("alerts")
    if isinstance(alerts, list):
        for item in alerts:
            if isinstance(item, dict):
                fp = item.get("fingerprint")
                if isinstance(fp, str) and fp:
                    fingerprints.append(fp)
    fingerprints.sort()
    return json.dumps(
        [group_key, status, fingerprints], ensure_ascii=False, separators=(",", ":")
    )


class _DedupCache:
    """线程安全的滑动窗口去重表（仅在 ``window_seconds > 0`` 时启用）。"""

    def __init__(self, window_seconds: float) -> None:
        self._window = max(0.0, float(window_seconds))
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return self._window > 0.0

    def is_duplicate(self, key: str, *, now: float | None = None) -> bool:
        """若 ``key`` 在窗口内已出现过返回 True，否则登记并返回 False。"""
        if not self.enabled:
            return False
        current = time.monotonic() if now is None else now
        with self._lock:
            expired = [k for k, ts in self._seen.items() if current - ts > self._window]
            for k in expired:
                del self._seen[k]
            prev = self._seen.get(key)
            if prev is not None and current - prev <= self._window:
                return True
            self._seen[key] = current
            return False


class AlertReceiverHandler(BaseHTTPRequestHandler):
    """HTTP 处理基类：路由到具体端点。

    协议版本设为 HTTP/1.1 并显式写 ``Content-Length``，否则 keep-alive 下
    客户端会等不到响应结束。
    """

    protocol_version = "HTTP/1.1"
    server_version = "ddp-alert-receiver/1.0"

    #: 由 build_server 注入的存储与可选安全/去重配置
    store: AlertStore
    bearer_token: str | None = None
    hmac_secret: str | None = None
    dedup_cache: _DedupCache = _DedupCache(0.0)

    # ── HTTP 方法实现 ────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定的方法名
        """GET 路由。"""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/health":
            self._send_json(HTTPStatus.OK, self._health_payload())
        elif path == "/alerts":
            self._send_json(HTTPStatus.OK, self._alerts_payload(parsed.query))
        elif path == "/stats":
            self._send_json(HTTPStatus.OK, build_stats(self.store.read_all()))
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found", "path": path})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定的方法名
        """POST 路由：只接受 ``/alerts``。"""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path != "/alerts":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found", "path": path})
            return

        try:
            raw_body = self._read_raw_body()
            self._verify_auth(raw_body)
            payload = self._parse_json_body(raw_body)
        except _PayloadTooLarge as exc:
            # 先回 413 再丢弃残余 body：顺序很重要——若先丢弃再回，
            # 遇到「Content-Length 撒谎」的客户端（声明 100 MB 但只发 10 字节）
            # 会一直等在对端，服务端线程被占死。先回响应，客户端看到 413 即收工。
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": str(exc)})
            self._drain()
            return
        except _Unauthorized as exc:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": str(exc)})
            return
        except _BadRequest as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if self.dedup_cache.enabled and self.dedup_cache.is_duplicate(
            build_dedup_key(payload)
        ):
            logger.info(
                "去重跳过重复告警：alertname=%s status=%s",
                _common_label(payload, "alertname"),
                payload.get("status"),
            )
            self._send_json(HTTPStatus.OK, {"status": "deduplicated", "stored": False})
            return

        # 落盘：告警是「证据」，写失败必须让上游知道（Alertmanager 会重试）。
        try:
            self.store.append(payload)
        except (OSError, ValueError) as exc:
            logger.error("落盘失败：%s", exc)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "persist_failed"}
            )
            return

        # 只记告警内容本身：不打印整个 payload（含全部标签/注解）。
        logger.info(
            "已接收告警：alertname=%s status=%s",
            _common_label(payload, "alertname"),
            payload.get("status"),
        )
        self._send_json(HTTPStatus.OK, {"status": "accepted", "stored": True})

    # ── 内部辅助 ─────────────────────────────────────────────────────

    def _health_payload(self) -> dict[str, Any]:
        """健康检查载荷。"""
        return {
            "status": "ok",
            "service": "ddp-alert-receiver",
            "data_dir": str(self.store.data_dir),
            "alerts_file": str(self.store.path),
        }

    def _alerts_payload(self, query: str) -> dict[str, Any]:
        """列出告警（从落盘文件读回，最新在前）。"""
        limit = self._parse_limit(query)
        records = self.store.read_recent(limit)
        return {"count": len(records), "alerts": records}

    @staticmethod
    def _parse_limit(query: str) -> int | None:
        """解析 ``?limit=N``；非法或缺省返回 None（不限）。"""
        values = parse_qs(query).get("limit")
        if not values:
            return None
        try:
            limit = int(values[0])
        except ValueError:
            return None
        return limit if limit >= 0 else None

    def _read_raw_body(self) -> bytes:
        """读取原始请求体字节。

        :raises _PayloadTooLarge: body 超过上限。
        :raises _BadRequest: Content-Type 或 Content-Length 不合法。
        """
        content_type = self.headers.get("Content-Type", "")
        base_type = content_type.split(";")[0].strip().lower()
        if base_type != _JSON_CONTENT_TYPE:
            raise _BadRequest(
                f"Content-Type 必须是 {_JSON_CONTENT_TYPE}（收到 {content_type or '空'}）"
            )

        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            # 没有 Content-Length 即 chunked 传输。http.server 不支持 chunked，
            # 直接拒绝比读一半再失败更清晰。
            raise _BadRequest("缺少 Content-Length（不支持 chunked 传输）")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise _BadRequest("Content-Length 不是合法整数") from exc
        if length < 0:
            raise _BadRequest("Content-Length 为负数")
        if length > MAX_BODY_BYTES:
            # 超限：不在这里分配内存，交给调用方先回 413。
            raise _PayloadTooLarge(f"请求体超过上限（{MAX_BODY_BYTES} 字节）")

        body = self.rfile.read(length)
        if len(body) != length:
            raise _BadRequest("请求体长度与 Content-Length 不一致")
        return body

    def _verify_auth(self, raw_body: bytes) -> None:
        """可选鉴权校验（默认关闭，配置对应令牌/密钥后强制校验）。"""
        if self.bearer_token:
            auth_header = self.headers.get("Authorization", "")
            parts = auth_header.split(None, 1)
            if (
                len(parts) != 2
                or parts[0].lower() != "bearer"
                or not hmac.compare_digest(parts[1], self.bearer_token)
            ):
                raise _Unauthorized("Bearer 令牌缺失或无效")

        if self.hmac_secret:
            provided = self.headers.get(SIGNATURE_HEADER, "")
            expected = compute_hmac_signature(self.hmac_secret, raw_body)
            if not provided or not hmac.compare_digest(provided, expected):
                raise _Unauthorized("HMAC 签名缺失或校验失败")

    @staticmethod
    def _parse_json_body(body: bytes) -> dict[str, Any]:
        """解析 JSON 对象请求体。"""
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _BadRequest(f"请求体不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise _BadRequest("请求体必须是 JSON 对象")
        return payload

    def _drain(self) -> None:
        """丢弃请求体中超出上限的部分（分块读，内存占用 O(1)）。

        用于「已回 413、但客户端还在发 body」的场景：读掉残余数据，
        连接才能干净复用。设有字节上限与超时双重保护——
        遇到撒谎的 Content-Length（声明 100 MB 实际只发几字节）时，
        靠 socket 超时退出，不会把线程占死。
        """
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or 0)
        except ValueError:
            return
        remaining = min(length, MAX_BODY_BYTES + DRAIN_SLACK_BYTES)
        deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
        while remaining > 0:
            if time.monotonic() > deadline:
                return
            try:
                chunk = self.rfile.read(min(DRAIN_CHUNK_BYTES, remaining))
            except (TimeoutError, OSError):
                return
            if not chunk:
                return
            remaining -= len(chunk)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        """写出 JSON 响应。"""
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 覆写标准库签名
        """把访问日志接到标准 logging（默认实现写 stderr 且不记级别）。"""
        logger.info("%s - %s", self.address_string(), format % args)

    def address_string(self) -> str:
        """不暴露来源 IP：告警接收端只关心内容，不做来源分析。

        覆写 ``BaseHTTPRequestHandler.address_string``（默认返回 client IP）。
        """
        return "-"


def _common_label(payload: dict[str, Any], name: str) -> Any:
    """取 Alertmanager webhook 顶层 ``commonLabels`` 里的某个标签。"""
    common = payload.get("commonLabels")
    if isinstance(common, dict):
        return common.get(name)
    return None


class _AlertReceiverServer(ThreadingHTTPServer):
    """带超时设置的线程化 HTTP 服务器。

    ``ThreadingHTTPServer`` 默认 ``daemon_threads = True``，主进程退出时
    不会因为挂起的连接卡住。
    """

    daemon_threads = True
    allow_reuse_address = True
    #: 单个连接的处理超时
    timeout = SOCKET_TIMEOUT_SECONDS


def build_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    data_dir: str | os.PathLike[str] | None = None,
    *,
    bearer_token: str | None = None,
    hmac_secret: str | None = None,
    dedup_window_seconds: float | None = None,
) -> _AlertReceiverServer:
    """构建 HTTP 服务器（不启动）。

    :param host: 监听地址。
    :param port: 监听端口。
    :param data_dir: 落盘目录；``None`` 时用 ``ALERT_RECEIVER_DIR`` 环境变量，
        再缺省到 ``/var/lib/ddp-alerts``。
    :param bearer_token: 可选 Bearer 鉴权令牌；``None`` 时读取
        ``ALERT_RECEIVER_BEARER_TOKEN``，为空则关闭。
    :param hmac_secret: 可选 HMAC-SHA256 验签密钥；``None`` 时读取
        ``ALERT_RECEIVER_HMAC_SECRET``，为空则关闭。
    :param dedup_window_seconds: 可选去重窗口（秒）；``None`` 时读取
        ``ALERT_RECEIVER_DEDUP_WINDOW_SECONDS``，为 0 则关闭（全量落盘）。
    """
    resolved_dir = data_dir or os.environ.get("ALERT_RECEIVER_DIR") or DEFAULT_DATA_DIR
    resolved_bearer = (
        bearer_token
        if bearer_token is not None
        else os.environ.get("ALERT_RECEIVER_BEARER_TOKEN")
    ) or None
    resolved_hmac = (
        hmac_secret
        if hmac_secret is not None
        else os.environ.get("ALERT_RECEIVER_HMAC_SECRET")
    ) or None
    if dedup_window_seconds is None:
        raw_window = os.environ.get("ALERT_RECEIVER_DEDUP_WINDOW_SECONDS", "0")
        resolved_window = float(raw_window) if raw_window.strip() else 0.0
    else:
        resolved_window = float(dedup_window_seconds)

    store = AlertStore(resolved_dir)
    dedup_cache = _DedupCache(resolved_window)

    handler = type(
        "BoundAlertReceiverHandler",
        (AlertReceiverHandler,),
        {
            "store": store,
            "bearer_token": resolved_bearer,
            "hmac_secret": resolved_hmac,
            "dedup_cache": dedup_cache,
        },
    )

    server = _AlertReceiverServer((host, port), handler)
    # socket 超时：防止半开的连接长期占用线程。
    server.socket.settimeout(SOCKET_TIMEOUT_SECONDS)
    logger.info("告警接收端就绪：监听 %s:%s，落盘目录 %s", host, port, store.data_dir)
    return server


def main() -> None:
    """服务入口。"""
    logging.basicConfig(
        level=os.environ.get("ALERT_RECEIVER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.environ.get("ALERT_RECEIVER_HOST", "0.0.0.0")
    port = int(os.environ.get("ALERT_RECEIVER_PORT", "8080"))
    server = build_server(host=host, port=port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - 交互式退出路径
        logger.info("收到中断信号，准备退出")
    finally:
        server.server_close()


if __name__ == "__main__":  # pragma: no cover - 容器入口
    main()
