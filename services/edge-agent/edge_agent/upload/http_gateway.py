"""真实 DDP HTTP 网关：把 ``ServerGateway`` 协议接到 V31-011 / Multipart Upload API。

**不做的事**

本模块只做「协议适配 + 传输可靠性」，不携带任何业务规则：

- 分片切分、重试策略与 Hash 校验全在 :class:`edge_agent.upload.driver.UploadDriver`；
- 幂等键由驱动生成并持久化，网关不重新生成（否则崩溃重启会造出第二个会话）。

**为什么手写 HTTP 而不是引入 SDK**

DDP 服务端就是本仓库自有的 FastAPI 应用，没有官方 SDK；这里的「协议」是六个
REST 端点（create/get/part-url/ack/complete + 预签名 PUT）。HTTP 客户端用
``urllib.request``（标准库）而不是 requests：Agent 部署在边缘工控机上，能少
一个运行时依赖就少一个；S3 预签名 PUT 也不需要 requests 的高级特性。

**错误映射（决定驱动层的重试/隔离行为）**

| 服务端 | 语义 | 映射 |
|---|---|---|
| 401 / 403 | Agent token 失效或被撤销 | ``AuthRejectedError``（永久，需重新配对） |
| 404 / 410 | UploadSession 已失效 | ``UploadSessionGoneError``（驱动允许有限重建） |
| 409 | 分片冲突 / 版本冲突 | ``ChecksumMismatchError``（隔离，不静默重试） |
| 422 | 载荷非法 | ``GatewayPermanentError`` |
| 429 / 5xx / 网络错误 | 暂时性 | ``GatewayTransientError``（驱动退避重试） |

**安全**

- Agent token 只在 ``Authorization`` 头里出现，不进日志（``_redact`` 统一处理，
  并用 ``__repr__`` 屏蔽凭据字段）；
- 预签名 URL 含签名参数，同样不入日志。
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from edge_agent.errors import (
    AuthRejectedError,
    ChecksumMismatchError,
    GatewayPermanentError,
    GatewayTransientError,
    SignedUrlExpiredError,
    UploadSessionGoneError,
)
from edge_agent.timesource import Clock, SystemClock
from edge_agent.upload.gateway import (
    CompleteSessionRequest,
    CreateSessionRequest,
    PartAckResult,
    RemotePartSummary,
    RemoteUploadSession,
    SignedPartUrl,
    UploadReceipt,
)

#: 预签名 URL 返回的 ``expires_at`` 缺失时，按服务端回执里的未知处理——
#: 用这个保守值让驱动提前重签，而不是假设「永不过期」。
_DEFAULT_URL_TTL = timedelta(seconds=60)

#: 单次 HTTP 调用的超时（秒）。分片上传受服务端 ``upload_url_ttl_seconds``
#: 约束，这里取比它小的值，确保超时先于签名过期发生。
_DEFAULT_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class HttpGatewayConfig:
    """网关连接配置；凭据只在内存里，永不写日志。"""

    base_url: str
    project_id: str
    agent_id: str
    #: Agent 专属 token（与用户 JWT 完全分离）；``repr`` 中被屏蔽。
    agent_token: str = field(repr=False)
    timeout_s: float = _DEFAULT_TIMEOUT_S
    verify_tls: bool = True

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        if not self.agent_token:
            raise ValueError("agent_token 不能为空")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s 必须 > 0")


#: 只允许 HTTP(S)。``file:`` / 自定义 scheme 一律拒绝：预签名 URL 来自服务端，
#: 但 Agent 不能假设它一定安全——scheme 收窄是纵深防御的一部分（也满足 S310）。
_ALLOWED_SCHEMES = ("http://", "https://")


def _require_http_url(url: str, where: str) -> str:
    """拒绝非 HTTP(S) URL。调用前不做 strip，保留原始字符串供 urllib 使用。"""
    if not url.startswith(_ALLOWED_SCHEMES):
        raise GatewayPermanentError(f"{where} 的 URL scheme 不被允许（只接受 http/https）")
    return url


def _redact(text: str) -> str:
    """日志/异常消息里屏蔽 URL 签名参数（``X-Amz-Signature`` 等）。"""
    if "?" not in text:
        return text
    head, _, _query = text.partition("?")
    return f"{head}?<redacted>"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随重定向。

    S3 预签名 PUT 对错误签名返回 4xx，而不是重定向；若跟随重定向，凭据会
    被带到未预期的主机。显式拒绝更安全。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, D102
        return None


def _build_opener(*, verify_tls: bool) -> urllib.request.OpenerDirector:
    """构建禁用重定向的 HTTP(S) opener，并按配置决定是否校验证书。"""
    handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
    if not verify_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


class HttpServerGateway:
    """基于标准库 urllib 的 ``ServerGateway`` 实现。"""

    def __init__(
        self,
        config: HttpGatewayConfig,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._clock = clock or SystemClock()
        self._root = config.base_url.rstrip("/")
        # 预签名 PUT 走同一个 opener（禁用重定向、不发送 Authorization）。
        self._opener = _build_opener(verify_tls=config.verify_tls)

    # -- ServerGateway 协议 -------------------------------------------------

    def create_or_resume_session(self, req: CreateSessionRequest) -> RemoteUploadSession:
        body: dict[str, Any] = {
            "original_filename": req.original_filename,
            "declared_content_type": req.declared_content_type,
            "declared_size_bytes": req.declared_size_bytes,
            "capture_session_id": req.capture_session_id,
            "expected_sha256": req.expected_sha256,
            "format_hint": req.format_hint,
        }
        if req.collection_task_id:
            body["collection_task_id"] = req.collection_task_id
        if req.part_size_hint_bytes is not None:
            body["part_size_bytes"] = req.part_size_hint_bytes
        if req.metadata:
            body["metadata"] = dict(req.metadata)
        payload = self._request_json(
            "POST",
            f"/api/v1/projects/{self._project}/upload-sessions",
            body=body,
            headers={"Idempotency-Key": req.idempotency_key},
        )
        return _session_from_payload(payload)

    def get_session(self, upload_session_id: str) -> RemoteUploadSession:
        payload = self._request_json(
            "GET",
            f"/api/v1/projects/{self._project}/upload-sessions/{upload_session_id}",
        )
        return _session_from_payload(payload)

    def get_part_url(
        self, upload_session_id: str, part_number: int, *, sha256: str
    ) -> SignedPartUrl:
        payload = self._request_json(
            "POST",
            (
                f"/api/v1/projects/{self._project}/upload-sessions/"
                f"{upload_session_id}/parts/{part_number}/url"
            ),
            body={"sha256": sha256},
        )
        headers = payload.get("headers") or {}
        expires_raw = payload.get("expires_at")
        expires_at = (
            _parse_datetime(expires_raw) if expires_raw else self._clock.now() + _DEFAULT_URL_TTL
        )
        return SignedPartUrl(
            part_number=int(payload["part_number"]),
            method=str(payload["method"]).upper(),
            url=str(payload["url"]),
            headers={str(k): str(v) for k, v in dict(headers).items()},
            expires_at=expires_at,
        )

    def upload_part(self, signed_url: SignedPartUrl, data: bytes) -> str:
        if signed_url.method != "PUT":
            raise GatewayPermanentError(f"不支持的预签名方法：{signed_url.method}（期望 PUT）")
        # noqa: S310 - URL 已由 _require_http_url 限定 http/https（见该函数）。
        request = urllib.request.Request(  # noqa: S310
            _require_http_url(signed_url.url, "分片预签名地址"),
            data=data,
            method="PUT",
            headers={**dict(signed_url.headers), "Content-Length": str(len(data))},
        )
        try:
            with self._opener.open(request, timeout=self._config.timeout_s) as response:
                etag = response.headers.get("ETag") or response.headers.get("Etag")
                # 显式读完并关闭：避免响应对象持有 socket 到 GC 才释放。
                response.read()
        except urllib.error.HTTPError as exc:
            # HTTPError 是 file-like 且持有 socket：关闭前先读完，避免泄漏。
            # 读取失败只说明连接已断，不影响下面的状态码映射（状态码在
            # HTTPError 对象上，与本响应体无关），因此不需要降级日志。
            try:
                exc.read()
            except (OSError, ValueError):  # 连接已断/已关闭
                pass  # noqa: S110 - 资源释放路径，无可映射的业务语义
            finally:
                exc.close()
            # 预签名 PUT 错误映射：
            # - 400 / 403：签名过期或时钟偏差，抛出 SignedUrlExpiredError 让驱动只重签、不重建会话；
            # - 429 / 5xx：对象存储暂时过载，抛出 GatewayTransientError 退避重试；
            # - 其余 4xx：永久性错误。
            if exc.code in (400, 403):
                raise SignedUrlExpiredError(
                    f"分片上传被拒（{exc.code}，签名可能已过期）：{_redact(signed_url.url)}"
                ) from exc
            if exc.code == 429 or exc.code >= 500:
                raise GatewayTransientError(
                    f"分片上传暂时不可用（{exc.code}）：{_redact(signed_url.url)}"
                ) from exc
            raise GatewayPermanentError(f"分片上传失败（{exc.code}）") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GatewayTransientError(f"分片上传网络错误：{exc}") from exc
        if not etag:
            raise GatewayPermanentError("分片上传成功但响应缺少 ETag")
        return etag.strip('"')

    def ack_part(
        self,
        upload_session_id: str,
        part_number: int,
        *,
        etag: str,
        size_bytes: int,
        sha256: str,
    ) -> PartAckResult:
        payload = self._request_json(
            "POST",
            (
                f"/api/v1/projects/{self._project}/upload-sessions/"
                f"{upload_session_id}/parts/{part_number}/ack"
            ),
            body={"etag": etag, "size_bytes": size_bytes, "sha256": sha256},
        )
        return PartAckResult(
            part_number=int(payload["part_number"]),
            etag=str(payload["etag"]),
            size_bytes=int(payload["size_bytes"]),
            sha256=str(payload["sha256"]),
            uploaded_bytes=int(payload["uploaded_bytes"]),
            uploaded_part_count=int(payload["uploaded_part_count"]),
        )

    def complete_session(self, req: CompleteSessionRequest) -> UploadReceipt:
        body: dict[str, Any] = {
            "expected_sha256": req.expected_sha256,
            "expected_size_bytes": req.expected_size_bytes,
            "parts": [
                {
                    "part_number": part.part_number,
                    "etag": part.etag,
                    "size_bytes": part.size_bytes,
                    "sha256": part.sha256,
                }
                for part in req.parts
            ],
        }
        if req.metadata:
            body["metadata"] = dict(req.metadata)
        payload = self._request_json(
            "POST",
            (
                f"/api/v1/projects/{self._project}/upload-sessions/"
                f"{req.upload_session_id}/complete"
            ),
            body=body,
            headers={
                "Idempotency-Key": req.idempotency_key,
                "If-Match": f'"{req.expected_version}"',
            },
        )
        asset = payload.get("raw_asset") or {}
        if not asset.get("asset_id"):
            raise GatewayPermanentError("complete 回执缺少 raw_asset.asset_id")
        return UploadReceipt(
            raw_asset_id=str(asset["asset_id"]),
            sha256=str(asset.get("sha256") or req.expected_sha256),
            ingestion_status=str(asset.get("status") or "UNKNOWN"),
            deduplicated=bool(payload.get("deduplicated", False)),
            replayed=bool(payload.get("replayed", False)),
        )

    # -- 内部 --------------------------------------------------------------

    @property
    def _project(self) -> str:
        return self._config.project_id

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._root}{path}"
        data = None
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._config.agent_token}",
            # Agent 身份显式声明，服务端据此拒绝用户 JWT 访问 Agent 面端点。
            "X-DDP-Actor-Kind": "EDGE_AGENT",
        }
        if headers:
            request_headers.update(headers)
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        # noqa: S310 - URL 已由 _require_http_url 限定 http/https（见该函数）。
        request = urllib.request.Request(  # noqa: S310
            _require_http_url(url, "服务端地址"),
            data=data,
            method=method,
            headers=request_headers,
        )
        try:
            with self._opener.open(request, timeout=self._config.timeout_s) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise self._map_http_error(exc, method=method, path=path) from exc
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

    def _map_http_error(self, exc: urllib.error.HTTPError, *, method: str, path: str) -> Exception:
        code = exc.code
        detail = _error_detail(exc)
        where = f"{method} {_redact(path)}"
        if code in (401, 403):
            return AuthRejectedError(f"{where} 身份被拒（{code}）：{detail}")
        if code in (404, 410):
            return UploadSessionGoneError(f"{where} 会话不存在或已失效（{code}）：{detail}")
        if code == 409:
            return ChecksumMismatchError(f"{where} 冲突（409）：{detail}")
        if code == 422:
            return GatewayPermanentError(f"{where} 载荷非法（422）：{detail}")
        if code == 429 or code >= 500:
            return GatewayTransientError(f"{where} 暂时不可用（{code}）：{detail}")
        return GatewayPermanentError(f"{where} 未预期状态（{code}）：{detail}")


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """从服务端错误体里取 ``error.code`` / ``message``（不泄漏敏感信息）。

    ``HTTPError`` 是 file-like 且持有连接 socket：必须读完并 ``close()``，
    否则 socket 会留到 GC 才释放（本服务把 ResourceWarning 升格为错误）。
    """
    try:
        raw = exc.read()
    except Exception:  # noqa: BLE001 - 错误体读取失败不应掩盖原始 HTTP 错误
        raw = b""
    finally:
        exc.close()
    if not raw:
        return "<unreadable>"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "<non-json>"
    if not isinstance(payload, dict):
        return "<non-object>"
    error = payload.get("error")
    if not isinstance(error, dict):
        return "<no-error-body>"
    code = error.get("code") or "?"
    message = error.get("message") or ""
    return f"{code} {message}".strip()


def _parse_datetime(value: object) -> datetime:
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _session_from_payload(payload: Mapping[str, Any]) -> RemoteUploadSession:
    parts = tuple(
        RemotePartSummary(
            part_number=int(item["part_number"]),
            size_bytes=int(item["size_bytes"]),
            etag=str(item["etag"]),
            sha256=str(item["sha256"]),
        )
        for item in (payload.get("uploaded_parts") or ())
    )
    return RemoteUploadSession(
        upload_session_id=str(payload["upload_session_id"]),
        version=int(payload["version"]),
        part_size_bytes=int(payload["part_size_bytes"]),
        expires_at=_parse_datetime(payload["expires_at"]),
        status=str(payload["status"]),
        uploaded_parts=parts,
        asset_id=payload.get("asset_id"),
    )


__all__ = ["HttpGatewayConfig", "HttpServerGateway"]
