"""显式测试替身：InMemoryServerGateway（严禁冒充真实服务端）。

仅用于 V31-012a 本地核心单元测试与故障注入测试；待 V31-012b 接入真实 DDP
V31-011 + Multipart Upload HTTP API。忠实复现 V31-002 四要素约束：
1. create_or_resume_session 按 idempotency_key 幂等；
2. 预签名 URL 过期仅抛 SignedUrlExpiredError，不销毁 UploadSession；
3. ack_part 幂等且对篡改 ETag/SHA256 抛 ChecksumMismatchError（模拟 409）；
4. complete_session 幂等（不资产分叉）并执行全量字节 SHA256 二次校验。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from edge_agent.errors import (
    ChecksumMismatchError,
    GatewayPermanentError,
    GatewayTransientError,
    SignedUrlExpiredError,
    UploadSessionGoneError,
)
from edge_agent.timesource import Clock
from edge_agent.upload.gateway import (
    CompleteSessionRequest,
    CreateSessionRequest,
    PartAckResult,
    RemotePartSummary,
    RemoteUploadSession,
    SignedPartUrl,
    UploadReceipt,
)


@dataclass
class _SessionRecord:
    upload_session_id: str
    version: int
    part_size_bytes: int
    expires_at: datetime
    expected_size_bytes: int
    expected_sha256: str
    status: str = "OPEN"
    raw_parts: dict[int, tuple[bytes, str]] = field(default_factory=dict)
    acked_parts: dict[int, RemotePartSummary] = field(default_factory=dict)
    receipt: UploadReceipt | None = None


class InMemoryServerGateway:
    """显式标注的内存级测试替身（FAKE_SERVER_GATEWAY_FOR_V31_012A_ONLY）。"""

    IS_TEST_DOUBLE = True

    def __init__(
        self,
        clock: Clock,
        *,
        part_size_bytes: int = 1024,
        url_ttl_s: float = 300.0,
        session_ttl_s: float = 3600.0,
    ) -> None:
        self._clock = clock
        self._part_size_bytes = part_size_bytes
        self._url_ttl_s = url_ttl_s
        self._session_ttl_s = session_ttl_s

        self._sessions: dict[str, _SessionRecord] = {}
        self._by_create_idem: dict[str, str] = {}
        self._by_complete_idem: dict[str, UploadReceipt] = {}
        self._url_registry: dict[str, tuple[str, int, datetime]] = {}

        # 调用计数与故障注入开关
        self.create_session_calls = 0
        self.get_part_url_calls = 0
        self.upload_part_calls: list[int] = []
        self.ack_part_calls: list[int] = []
        self.complete_calls = 0

        self.transient_failures_left: dict[str, int] = {}
        self.expire_url_on_put_once: set[int] = set()
        self.return_nearly_expired_url_once: set[int] = set()
        self.tamper_etag_on_part: set[int] = set()
        self.tamper_receipt_sha256 = False
        self.permanent_fail_on: set[str] = set()
        self.gone_session_ids: set[str] = set()

    def _maybe_inject(self, op: str) -> None:
        if op in self.permanent_fail_on:
            raise GatewayPermanentError(f"测试注入永久拒绝：{op}")
        left = self.transient_failures_left.get(op, 0)
        if left > 0:
            self.transient_failures_left[op] = left - 1
            raise GatewayTransientError(f"测试注入临时断网：{op}")

    def create_or_resume_session(self, req: CreateSessionRequest) -> RemoteUploadSession:
        self._maybe_inject("create_session")
        self.create_session_calls += 1
        existing_id = self._by_create_idem.get(req.idempotency_key)
        if existing_id and existing_id not in self.gone_session_ids:
            return self._to_remote(self._sessions[existing_id])

        sid = str(uuid.uuid4())
        part_size = req.part_size_hint_bytes or self._part_size_bytes
        rec = _SessionRecord(
            upload_session_id=sid,
            version=1,
            part_size_bytes=part_size,
            expires_at=self._clock.now() + timedelta(seconds=self._session_ttl_s),
            expected_size_bytes=req.declared_size_bytes,
            expected_sha256=req.expected_sha256,
        )
        self._sessions[sid] = rec
        self._by_create_idem[req.idempotency_key] = sid
        return self._to_remote(rec)

    def get_session(self, upload_session_id: str) -> RemoteUploadSession:
        self._maybe_inject("get_session")
        rec = self._require_session(upload_session_id)
        return self._to_remote(rec)

    def get_part_url(
        self, upload_session_id: str, part_number: int, *, sha256: str
    ) -> SignedPartUrl:
        self._maybe_inject("get_part_url")
        self._require_session(upload_session_id)
        self.get_part_url_calls += 1
        ttl = self._url_ttl_s
        if part_number in self.return_nearly_expired_url_once:
            self.return_nearly_expired_url_once.remove(part_number)
            ttl = 5.0  # 低于默认 url_refresh_margin_s=30s，触发主动重签
        expires_at = self._clock.now() + timedelta(seconds=ttl)
        token = uuid.uuid4().hex
        url = f"https://fake-storage.invalid/{upload_session_id}/part/{part_number}?tok={token}"
        self._url_registry[url] = (upload_session_id, part_number, expires_at)
        return SignedPartUrl(
            part_number=part_number,
            method="PUT",
            url=url,
            headers={"x-fake-sha256": sha256},
            expires_at=expires_at,
        )

    def upload_part(self, signed_url: SignedPartUrl, data: bytes) -> str:
        self._maybe_inject("upload_part")
        meta = self._url_registry.get(signed_url.url)
        if meta is None:
            raise SignedUrlExpiredError("未知或已失效的预签名 URL")
        sid, part_number, expires_at = meta
        self._require_session(sid)
        if part_number in self.expire_url_on_put_once:
            self.expire_url_on_put_once.remove(part_number)
            raise SignedUrlExpiredError(f"分片 {part_number} 预签名 URL 在上传时过期")
        if self._clock.now() >= expires_at:
            raise SignedUrlExpiredError(f"分片 {part_number} 预签名 URL 已过期")

        self.upload_part_calls.append(part_number)
        etag = f'"{hashlib.sha256(data).hexdigest()[:32]}"'
        self._sessions[sid].raw_parts[part_number] = (data, etag)
        if part_number in self.tamper_etag_on_part:
            return '"tampered-etag-mismatch"'
        return etag

    def ack_part(
        self,
        upload_session_id: str,
        part_number: int,
        *,
        etag: str,
        size_bytes: int,
        sha256: str,
    ) -> PartAckResult:
        self._maybe_inject("ack_part")
        rec = self._require_session(upload_session_id)
        self.ack_part_calls.append(part_number)

        raw = rec.raw_parts.get(part_number)
        if raw is None:
            raise GatewayPermanentError(f"分片 {part_number} 尚未上传到对象存储")
        stored_bytes, stored_etag = raw
        actual_sha = f"sha256:{hashlib.sha256(stored_bytes).hexdigest()}"
        if etag != stored_etag or sha256 != actual_sha or size_bytes != len(stored_bytes):
            raise ChecksumMismatchError(f"409 分片 {part_number} ETag/SHA256 校验冲突")

        existing = rec.acked_parts.get(part_number)
        if existing is not None:
            if existing.etag != etag or existing.sha256 != sha256:
                raise ChecksumMismatchError(f"409 重复 ACK 分片 {part_number} 参数冲突")
        else:
            rec.acked_parts[part_number] = RemotePartSummary(
                part_number=part_number, size_bytes=size_bytes, etag=etag, sha256=sha256
            )
        uploaded_bytes = sum(p.size_bytes for p in rec.acked_parts.values())
        return PartAckResult(
            part_number=part_number,
            etag=etag,
            size_bytes=size_bytes,
            sha256=sha256,
            uploaded_bytes=uploaded_bytes,
            uploaded_part_count=len(rec.acked_parts),
        )

    def complete_session(self, req: CompleteSessionRequest) -> UploadReceipt:
        self._maybe_inject("complete_session")
        self.complete_calls += 1
        replayed = self._by_complete_idem.get(req.idempotency_key)
        if replayed is not None:
            return UploadReceipt(
                raw_asset_id=replayed.raw_asset_id,
                sha256=replayed.sha256,
                ingestion_status=replayed.ingestion_status,
                deduplicated=replayed.deduplicated,
                replayed=True,
            )

        rec = self._require_session(req.upload_session_id)
        combined = bytearray()
        for part in sorted(req.parts, key=lambda p: p.part_number):
            acked = rec.acked_parts.get(part.part_number)
            if acked is None or acked.etag != part.etag or acked.sha256 != part.sha256:
                raise ChecksumMismatchError(f"complete 时分片 {part.part_number} 校验失败")
            combined.extend(rec.raw_parts[part.part_number][0])

        full_sha = f"sha256:{hashlib.sha256(combined).hexdigest()}"
        if full_sha != req.expected_sha256 or len(combined) != req.expected_size_bytes:
            raise ChecksumMismatchError("服务端整文件 SHA256 二次校验失败")

        returned_sha = "sha256:" + ("0" * 64) if self.tamper_receipt_sha256 else full_sha
        receipt = UploadReceipt(
            raw_asset_id=str(uuid.uuid4()),
            sha256=returned_sha,
            ingestion_status="VERIFIED_PENDING_INGESTION",
            deduplicated=False,
            replayed=False,
        )
        rec.status = "COMPLETED"
        rec.version += 1
        rec.receipt = receipt
        self._by_complete_idem[req.idempotency_key] = receipt
        return receipt

    def _require_session(self, upload_session_id: str) -> _SessionRecord:
        if upload_session_id in self.gone_session_ids:
            raise UploadSessionGoneError(f"UploadSession 已失效：{upload_session_id}")
        rec = self._sessions.get(upload_session_id)
        if rec is None or self._clock.now() >= rec.expires_at:
            raise UploadSessionGoneError(f"UploadSession 不存在或已过期：{upload_session_id}")
        return rec

    def _to_remote(self, rec: _SessionRecord) -> RemoteUploadSession:
        return RemoteUploadSession(
            upload_session_id=rec.upload_session_id,
            version=rec.version,
            part_size_bytes=rec.part_size_bytes,
            expires_at=rec.expires_at,
            status=rec.status,
            uploaded_parts=tuple(
                rec.acked_parts[k] for k in sorted(rec.acked_parts)
            ),
            asset_id=rec.receipt.raw_asset_id if rec.receipt else None,
        )
