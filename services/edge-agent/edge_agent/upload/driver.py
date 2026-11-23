"""可续传分片上传驱动（FR-007 / FR-008）。

核心保证：
- SQLite 持久化 upload_session_id、分片号、ETag 与恢复游标；崩溃重启后自动续传。
- 预签名 URL 过期或临近过期仅重签名，绝不重建 UploadSession。
- 重复 ACK / complete 携带稳定 idempotency_key，保持幂等且不资产分叉。
- Hash / ETag 篡改或不匹配立即隔离（QUARANTINED），绝不静默重试。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import TypeVar

from edge_agent.config import UploadConfig
from edge_agent.durable_fs import sha256_file, sha256_range
from edge_agent.errors import (
    ChecksumMismatchError,
    GatewayPermanentError,
    GatewayTransientError,
    SignedUrlExpiredError,
    SpoolIntegrityError,
    UploadSessionGoneError,
)
from edge_agent.faults import CrashHook, no_crash
from edge_agent.manifest.builder import load_manifest
from edge_agent.spool.manager import SpoolManager
from edge_agent.spool.models import (
    ArtifactReceipt,
    ServerReceipt,
    SpoolArtifactRecord,
    SpoolEntry,
    UploadCursor,
    UploadPartRecord,
)
from edge_agent.timesource import Clock, SystemClock
from edge_agent.upload.gateway import (
    CompletePartSpec,
    CompleteSessionRequest,
    CreateSessionRequest,
    RemoteUploadSession,
    ServerGateway,
    SignedPartUrl,
    UploadReceipt,
)

T = TypeVar("T")


class UploadDriver:
    """驱动 Spool 条目通过 ServerGateway 完成可续传分片上传。"""

    def __init__(
        self,
        spool: SpoolManager,
        gateway: ServerGateway,
        config: UploadConfig,
        *,
        clock: Clock | None = None,
        crash_hook: CrashHook = no_crash,
    ) -> None:
        self._spool = spool
        self._gateway = gateway
        self._config = config
        self._clock: Clock = clock or SystemClock()
        self._crash_hook = crash_hook

    def upload_entry(self, entry_id: str) -> ServerReceipt:
        entry = self._spool.mark_uploading(entry_id)
        try:
            manifest = load_manifest(self._spool.manifest_path(entry).read_bytes())
            receipts = tuple(
                self._upload_single_artifact(entry, artifact, manifest.manifest_hash)
                for artifact in entry.artifacts
            )
            server_receipt = ServerReceipt(received_at=self._clock.now(), artifacts=receipts)
            self._spool.mark_uploaded(entry_id, server_receipt)
            return server_receipt
        except (ChecksumMismatchError, SpoolIntegrityError) as exc:
            self._spool.mark_quarantined(entry_id, str(exc))
            raise
        except GatewayPermanentError as exc:
            self._spool.mark_failed(entry_id, str(exc))
            raise

    def _upload_single_artifact(
        self, entry: SpoolEntry, artifact: SpoolArtifactRecord, manifest_hash: str
    ) -> ArtifactReceipt:
        payload = self._spool.payload_path(entry, artifact.rel_path)
        self._verify_local_payload(payload, artifact)

        recreations = 0
        while True:
            try:
                cursor = self._ensure_session_cursor(entry, artifact, manifest_hash)
                if cursor.raw_asset_id and cursor.server_sha256 == artifact.sha256:
                    return ArtifactReceipt(
                        artifact_path=artifact.rel_path,
                        raw_asset_id=cursor.raw_asset_id,
                        sha256=cursor.server_sha256,
                        ingestion_status=cursor.ingestion_status or "INGESTED",
                        deduplicated=cursor.deduplicated,
                    )
                completed_parts = self._upload_all_parts(cursor, payload, artifact)
                receipt = self._complete_artifact(cursor, artifact, manifest_hash, completed_parts)
                self._persist_completed_cursor(cursor, receipt)
                return ArtifactReceipt(
                    artifact_path=artifact.rel_path,
                    raw_asset_id=receipt.raw_asset_id,
                    sha256=receipt.sha256,
                    ingestion_status=receipt.ingestion_status,
                    deduplicated=receipt.deduplicated,
                )
            except UploadSessionGoneError:
                if recreations >= self._config.max_session_recreations:
                    raise
                recreations += 1
                self._spool.store.reset_upload_cursor(entry.entry_id, artifact.rel_path)

    def _verify_local_payload(self, payload: Path, artifact: SpoolArtifactRecord) -> None:
        if not payload.is_file():
            raise SpoolIntegrityError(f"上传前发现 Spool payload 缺失：{payload}")
        if payload.stat().st_size != artifact.size_bytes:
            raise SpoolIntegrityError(f"上传前发现 payload 大小被篡改：{payload}")
        if self._config.verify_hash_before_upload:
            actual = f"sha256:{sha256_file(payload)}"
            if actual != artifact.sha256:
                raise SpoolIntegrityError(
                    f"上传前校验 SHA256 失败（期望 {artifact.sha256}，实际 {actual}）"
                )

    def _ensure_session_cursor(
        self, entry: SpoolEntry, artifact: SpoolArtifactRecord, manifest_hash: str
    ) -> UploadCursor:
        existing = self._spool.store.get_upload_cursor(entry.entry_id, artifact.rel_path)
        if existing is not None:
            if existing.raw_asset_id:
                return existing
            remote = self._call_with_retry(
                lambda: self._gateway.get_session(existing.upload_session_id)
            )
            return self._sync_cursor_with_remote(existing, remote, artifact)

        idem_key = f"spool:{entry.entry_id}:{artifact.rel_path}"
        req = CreateSessionRequest(
            project_id=entry.project_id,
            capture_session_id=entry.capture_session_id,
            collection_task_id=entry.collection_task_id,
            original_filename=Path(artifact.rel_path).name,
            declared_content_type=artifact.mime_type,
            declared_size_bytes=artifact.size_bytes,
            expected_sha256=artifact.sha256,
            format_hint=artifact.format,
            idempotency_key=idem_key,
            part_size_hint_bytes=self._config.part_size_hint_bytes,
            metadata={"manifest_hash": manifest_hash, "artifact_path": artifact.rel_path},
        )
        remote = self._call_with_retry(lambda: self._gateway.create_or_resume_session(req))
        total_parts = max(1, math.ceil(artifact.size_bytes / remote.part_size_bytes))
        cursor = UploadCursor(
            entry_id=entry.entry_id,
            artifact_path=artifact.rel_path,
            upload_session_id=remote.upload_session_id,
            session_version=remote.version,
            part_size_bytes=remote.part_size_bytes,
            total_parts=total_parts,
            expires_at=remote.expires_at,
            idempotency_key=idem_key,
        )
        self._spool.store.save_upload_cursor(cursor, now=self._clock.now())
        synced = self._sync_cursor_with_remote(cursor, remote, artifact)
        self._crash_hook("after_session_init")
        return synced

    def _sync_cursor_with_remote(
        self,
        cursor: UploadCursor,
        remote: RemoteUploadSession,
        artifact: SpoolArtifactRecord,
    ) -> UploadCursor:
        now = self._clock.now()
        updated = UploadCursor(
            entry_id=cursor.entry_id,
            artifact_path=cursor.artifact_path,
            upload_session_id=remote.upload_session_id,
            session_version=remote.version,
            part_size_bytes=remote.part_size_bytes,
            total_parts=max(1, math.ceil(artifact.size_bytes / remote.part_size_bytes)),
            expires_at=remote.expires_at,
            idempotency_key=cursor.idempotency_key,
        )
        self._spool.store.save_upload_cursor(updated, now=now)
        for rp in remote.uploaded_parts:
            offset = (rp.part_number - 1) * remote.part_size_bytes
            self._spool.store.save_upload_part(
                UploadPartRecord(
                    entry_id=cursor.entry_id,
                    artifact_path=cursor.artifact_path,
                    part_number=rp.part_number,
                    offset_bytes=offset,
                    size_bytes=rp.size_bytes,
                    sha256=rp.sha256,
                    etag=rp.etag,
                    acked=True,
                ),
                now=now,
            )
        refreshed = self._spool.store.get_upload_cursor(cursor.entry_id, cursor.artifact_path)
        assert refreshed is not None
        return refreshed

    def _upload_all_parts(
        self, cursor: UploadCursor, payload: Path, artifact: SpoolArtifactRecord
    ) -> list[CompletePartSpec]:
        acked_by_num = {p.part_number: p for p in cursor.parts if p.acked and p.etag}
        completed: list[CompletePartSpec] = []
        for part_num in range(1, cursor.total_parts + 1):
            offset = (part_num - 1) * cursor.part_size_bytes
            length = min(cursor.part_size_bytes, artifact.size_bytes - offset)
            digest = sha256_range(payload, offset, length, self._config.read_chunk_bytes)
            part_sha = f"sha256:{digest}"
            existing = acked_by_num.get(part_num)
            if existing is not None:
                if existing.sha256 != part_sha or existing.size_bytes != length:
                    raise ChecksumMismatchError(
                        f"分片 {part_num} 本地与已持久化校验和不一致："
                        f"{existing.sha256} != {part_sha}"
                    )
                completed.append(
                    CompletePartSpec(
                        part_number=part_num,
                        etag=existing.etag or "",
                        size_bytes=length,
                        sha256=part_sha,
                    )
                )
                continue

            etag = self._upload_and_ack_part(cursor, payload, part_num, offset, length, part_sha)
            completed.append(
                CompletePartSpec(
                    part_number=part_num, etag=etag, size_bytes=length, sha256=part_sha
                )
            )
        return completed

    def _upload_and_ack_part(
        self,
        cursor: UploadCursor,
        payload: Path,
        part_num: int,
        offset: int,
        length: int,
        part_sha: str,
    ) -> str:
        chunk = _read_slice(payload, offset, length)
        etag = self._put_part_with_resign(cursor.upload_session_id, part_num, part_sha, chunk)
        self._crash_hook(f"after_part_upload:{part_num}")

        ack = self._call_with_retry(
            lambda: self._gateway.ack_part(
                cursor.upload_session_id,
                part_num,
                etag=etag,
                size_bytes=length,
                sha256=part_sha,
            )
        )
        if ack.etag != etag or ack.sha256 != part_sha or ack.size_bytes != length:
            raise ChecksumMismatchError(
                f"分片 {part_num} ACK 回执与本地不一致（etag={ack.etag}/{etag}）"
            )
        self._spool.store.save_upload_part(
            UploadPartRecord(
                entry_id=cursor.entry_id,
                artifact_path=cursor.artifact_path,
                part_number=part_num,
                offset_bytes=offset,
                size_bytes=length,
                sha256=part_sha,
                etag=etag,
                acked=True,
            ),
            now=self._clock.now(),
        )
        self._crash_hook(f"after_part_ack:{part_num}")
        return etag

    def _put_part_with_resign(
        self, upload_session_id: str, part_num: int, part_sha: str, chunk: bytes
    ) -> str:
        """URL 临近过期或上传时返回过期，仅重新申请预签名 URL，绝不重建 UploadSession。"""
        margin = timedelta(seconds=self._config.url_refresh_margin_s)
        for attempt in range(1, self._config.max_attempts + 1):
            signed: SignedPartUrl = self._call_with_retry(
                lambda: self._gateway.get_part_url(upload_session_id, part_num, sha256=part_sha)
            )
            if signed.expires_at - self._clock.now() <= margin:
                signed = self._call_with_retry(
                    lambda: self._gateway.get_part_url(
                        upload_session_id, part_num, sha256=part_sha
                    )
                )
            try:
                return self._call_with_retry(lambda s=signed: self._gateway.upload_part(s, chunk))
            except SignedUrlExpiredError:
                if attempt >= self._config.max_attempts:
                    raise
        raise SignedUrlExpiredError(f"分片 {part_num} 预签名 URL 重签次数耗尽")

    def _complete_artifact(
        self,
        cursor: UploadCursor,
        artifact: SpoolArtifactRecord,
        manifest_hash: str,
        parts: list[CompletePartSpec],
    ) -> UploadReceipt:
        req = CompleteSessionRequest(
            upload_session_id=cursor.upload_session_id,
            expected_version=cursor.session_version,
            expected_sha256=artifact.sha256,
            expected_size_bytes=artifact.size_bytes,
            parts=tuple(parts),
            idempotency_key=f"complete:{cursor.entry_id}:{cursor.artifact_path}",
            metadata={"manifest_hash": manifest_hash, "artifact_path": artifact.rel_path},
        )
        receipt = self._call_with_retry(lambda: self._gateway.complete_session(req))
        if not receipt.raw_asset_id or receipt.sha256 != artifact.sha256:
            raise ChecksumMismatchError(
                f"complete 回执校验失败："
                f"raw_asset_id={receipt.raw_asset_id}, sha256={receipt.sha256}"
            )
        return receipt

    def _persist_completed_cursor(self, cursor: UploadCursor, receipt: UploadReceipt) -> None:
        self._spool.store.save_upload_cursor(
            UploadCursor(
                entry_id=cursor.entry_id,
                artifact_path=cursor.artifact_path,
                upload_session_id=cursor.upload_session_id,
                session_version=cursor.session_version + 1,
                part_size_bytes=cursor.part_size_bytes,
                total_parts=cursor.total_parts,
                expires_at=cursor.expires_at,
                idempotency_key=cursor.idempotency_key,
                raw_asset_id=receipt.raw_asset_id,
                server_sha256=receipt.sha256,
                ingestion_status=receipt.ingestion_status,
                deduplicated=receipt.deduplicated,
            ),
            now=self._clock.now(),
        )

    def _call_with_retry(self, fn: Callable[[], T]) -> T:
        delay = self._config.backoff_base_s
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                return fn()
            except GatewayTransientError:
                if attempt >= self._config.max_attempts:
                    raise
                if delay > 0:
                    self._clock.sleep(min(delay, self._config.backoff_max_s))
                delay = min(delay * 2, self._config.backoff_max_s)
        raise AssertionError("unreachable")


def _read_slice(path: Path, offset: int, length: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read(length)
    if len(data) != length:
        raise SpoolIntegrityError(f"读取分片字节数不足：{path}（期望 {length}，实际 {len(data)}）")
    return data
