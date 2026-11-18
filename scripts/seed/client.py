"""种子脚本共用的 HTTP 客户端与断言。"""

from __future__ import annotations

import datetime
import hashlib
import http.client
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


class SeedFailure(RuntimeError):
    """某一步不符合契约。消息里带状态码与响应体，便于直接定位。"""


@dataclass
class Context:
    """一次种子运行共享的上下文。"""

    base_url: str
    token: str
    project_id: str
    user_id: str
    stamp: str
    # 各模块产出的 ID，供后续模块引用（如标注任务要用 Episode）
    ids: dict[str, dict[str, str]] = field(default_factory=dict)
    # 多身份：某些动作有职责分离要求（如采集任务创建人不得审批自己的任务），
    # 种子必须能以第二个人的身份操作。键是角色名，值是 Bearer 令牌。
    tokens: dict[str, str] = field(default_factory=dict)

    def as_(self, role: str) -> Client:
        try:
            token = self.tokens[role]
        except KeyError as exc:
            raise SeedFailure(f"缺少身份：{role}") from exc
        return Client(self.base_url, token)

    def remember(self, group: str, key: str, value: str) -> str:
        self.ids.setdefault(group, {})[key] = value
        return value

    def recall(self, group: str, key: str) -> str:
        try:
            return self.ids[group][key]
        except KeyError as exc:
            raise SeedFailure(f"缺少前置数据：{group}.{key}") from exc


class Client:
    """极简 HTTP 客户端：只做本仓库需要的 JSON 请求。"""

    def __init__(self, base_url: str, token: str, *, max_retries: int = 6) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._max_retries = max_retries

    def call(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
    ) -> tuple[int, Any]:
        data = raw
        merged = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            **(headers or {}),
        }
        if body is not None:
            data = json.dumps(body).encode()
            merged["Content-Type"] = "application/json"
        # 只连本地 API（http://127.0.0.1:8000）；scheme 由调用方传入的
        # base_url 决定，脚本不做任何 URL 拼接以外的处理。
        request = urllib.request.Request(  # noqa: S310
            self._base + path, data=data, method=method, headers=merged
        )
        # 写限流是按客户端 IP 的滑动窗口（默认 120 次/分钟）。种子脚本一次
        # 运行会发几百个写请求，撞上 429 很正常——按 Retry-After 退避重试，
        # 而不是把 429 当成业务失败。
        for attempt in range(self._max_retries + 1):
            status, payload, retry_after = self._once(request)
            if status != 429 or attempt == self._max_retries:
                return status, payload
            time.sleep(retry_after or min(2**attempt, 15))
        return status, payload  # pragma: no cover - 循环必然在上面返回

    def _once(self, request: urllib.request.Request) -> tuple[int, Any, float]:
        try:
            with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
                payload = response.read()
                return response.status, (json.loads(payload) if payload else None), 0.0
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            try:
                parsed = json.loads(payload) if payload else None
            except json.JSONDecodeError:
                parsed = payload.decode("utf-8", "replace")
            retry_after = 0.0
            header = exc.headers.get("Retry-After") if exc.headers else None
            if header:
                try:
                    retry_after = float(header)
                except ValueError:
                    retry_after = 0.0
            return exc.code, parsed, retry_after


def expect(
    label: str,
    result: tuple[int, Any],
    want: int | tuple[int, ...],
    *,
    allow: tuple[int, ...] = (),
) -> Any:
    """断言状态码；``allow`` 用于「可能已存在」等幂等场景。"""
    status, payload = result
    accepted = (want if isinstance(want, tuple) else (want,)) + allow
    if status in accepted:
        return payload
    raise SeedFailure(
        f"{label}：期望 {accepted}，实际 {status}\n"
        f"  响应：{json.dumps(payload, ensure_ascii=False)[:600]}"
    )


def _connect(url: str) -> tuple[http.client.HTTPConnection, str, str]:
    """把预签名 URL 拆成「可连接的地址 + 保留原 Host 头 + 请求路径」。

    预签名地址里的主机名是容器网络内的服务名（``minio``），宿主机解析不了；
    但 SigV4 把 Host 算进了签名，不能直接换域名。这里保持 Host 不变、只把 TCP
    连接指向本机，等价于 ``curl --resolve``。
    """
    parts = urllib.parse.urlsplit(url)
    authority = parts.netloc
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        socket.getaddrinfo(host, port)
        target = host
    except socket.gaierror:
        target = "127.0.0.1"
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    factory = (
        http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    )
    return factory(target, port, timeout=120), authority, path


def put_presigned_object(url: str, headers: dict[str, str], data: bytes, label: str) -> str:
    """PUT 字节到预签名地址；返回去掉引号的 ETag。"""
    conn, authority, path = _connect(url)
    try:
        conn.request("PUT", path, body=data, headers={**headers, "Host": authority})
        response = conn.getresponse()
        response.read()
        if response.status != 200:
            raise SeedFailure(f"{label}：上传字节失败，状态 {response.status}")
        etag = response.getheader("ETag")
    finally:
        conn.close()
    if not etag:
        raise SeedFailure(f"{label}：对象存储未返回 ETag")
    return etag.strip('"')


def _storage_quota_configured(client: Client, base: str) -> bool:
    """项目是否配置了对象存储配额（决定上传 422 的归因，不是猜测）。"""
    status, payload = client.call("GET", f"{base}/quotas")
    expect("读取资源配额", (status, payload), 200)
    assert isinstance(payload, dict)
    return any(
        str(item.get("resource_kind")) == "OBJECT_STORAGE_BYTES"
        for item in (payload.get("items") or [])
    )


def _existing_object(client: Client, base: str) -> dict[str, Any] | None:
    """取项目内已存在的 RawAsset，作为产物对象的降级来源（取最小的一个）。"""
    assets = expect(
        "读取已有原始资产", client.call("GET", f"{base}/raw-assets?limit=100"), 200
    )
    assert isinstance(assets, dict)
    candidates = [
        item
        for item in (assets.get("items") or [])
        if int(item.get("size_bytes") or 0) > 0 and item.get("object_key")
    ]
    if not candidates:
        return None
    item = min(candidates, key=lambda entry: int(entry["size_bytes"]))
    return {
        "object_key": str(item["object_key"]),
        "sha256": str(item["sha256"]),
        "size_bytes": int(item["size_bytes"]),
        "raw_asset_id": str(item["asset_id"]),
    }


def upload_object(
    client: Client,
    ctx: Context,
    *,
    filename: str,
    content: bytes,
    content_type: str,
    format_hint: str,
    label: str,
) -> dict[str, Any]:
    """走真实上传会话把字节写进对象存储，返回权威对象地址与 RawAsset ID。

    平台没有「直接写对象」的接口，只有上传会话这一条真实通路：创建会话 →
    申请分片地址 → 直接 PUT 对象存储 → 登记分片 → 完成上传。

    去重命中时（同 sha256 已存在）平台会删掉本次会话的对象并复用既有
    RawAsset；此时会话里的 ``object_key`` 指向一个**已被清理的副本**，
    必须用 RawAsset 上的 ``object_key/sha256/size_bytes``。

    项目配了对象存储配额时，治理层的历史缺陷会让上传 422；这里如实识别
    并复用项目内已有对象，没有可复用对象就失败，绝不伪造 ``object_key``。
    """
    base = f"/api/v1/projects/{ctx.project_id}"
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    status, session = client.call(
        "POST",
        f"{base}/upload-sessions",
        body={
            "original_filename": filename,
            "declared_content_type": content_type,
            "declared_size_bytes": len(content),
            "expected_sha256": digest,
            "format_hint": format_hint,
        },
        headers={"Idempotency-Key": idempotency_key()},
    )
    if status != 201:
        if status == 422 and _storage_quota_configured(client, base):
            fallback = _existing_object(client, base)
            if fallback is None:
                raise SeedFailure(
                    f"{label}：对象存储配额导致上传 422，且项目内没有可复用的 RawAsset"
                )
            print(
                f"  ! DEGRADED {label}：治理配额缺陷使上传不可用，"
                f"复用项目内已有对象 {fallback['object_key']}"
            )
            return fallback
        expect(f"{label}创建上传会话", (status, session), 201)
    assert isinstance(session, dict)
    session_id = str(session["upload_session_id"])
    part_path = f"{base}/upload-sessions/{session_id}/parts/1"
    part = expect(
        f"{label}申请分片地址",
        client.call("POST", f"{part_path}/url", body={"sha256": digest}),
        200,
    )
    assert isinstance(part, dict)
    etag = put_presigned_object(part["url"], dict(part.get("headers") or {}), content, label)
    expect(
        f"{label}登记分片",
        client.call(
            "POST",
            f"{part_path}/ack",
            body={"etag": etag, "size_bytes": len(content), "sha256": digest},
        ),
        200,
    )
    # 分片登记会推进会话版本；完成前必须读一次拿最新 If-Match。
    current = expect(
        f"{label}读取上传会话",
        client.call("GET", f"{base}/upload-sessions/{session_id}"),
        200,
    )
    assert isinstance(current, dict)
    completed = expect(
        f"{label}完成上传",
        client.call(
            "POST",
            f"{base}/upload-sessions/{session_id}/complete",
            body={
                "expected_sha256": digest,
                "expected_size_bytes": len(content),
                "parts": [
                    {
                        "part_number": 1,
                        "etag": etag,
                        "size_bytes": len(content),
                        "sha256": digest,
                    }
                ],
                "metadata": {"seed_stamp": ctx.stamp},
            },
            headers={
                "If-Match": if_match(int(current["version"]))["If-Match"],
                "Idempotency-Key": idempotency_key(),
            },
        ),
        202,
    )
    assert isinstance(completed, dict)
    stored = completed["upload_session"]
    raw_asset = completed.get("raw_asset") or {}
    if raw_asset.get("object_key"):
        return {
            "object_key": str(raw_asset["object_key"]),
            "sha256": str(raw_asset["sha256"]),
            "size_bytes": int(raw_asset["size_bytes"]),
            "raw_asset_id": str(raw_asset["asset_id"]),
        }
    return {
        "object_key": stored["object_key"],
        "sha256": digest,
        "size_bytes": len(content),
        "raw_asset_id": None,
    }


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def idempotency_key() -> str:
    return str(uuid.uuid4())


def if_match(version: int) -> dict[str, str]:
    return {"If-Match": f'"{version}"'}
