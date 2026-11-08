"""测试用内存 S3 multipart 替身（显式标注：非真实对象存储）。

模拟预签名 URL、分片 ETag/校验和、ListParts、Complete/Abort 与范围读取；
用于 API 流程和失败重试测试，不替代 MinIO/S3 集成验收。
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.application.ingestion.ports import (
    PresignedDownload,
    PresignedPart,
    StoredPart,
)
from app.domain.ingestion.errors import (
    MultipartUploadNotFoundError,
    ObjectNotFoundError,
    ObjectStorageUnavailableError,
)


@dataclass
class _Part:
    data: bytes
    etag: str
    sha256: str


@dataclass
class _Upload:
    key: str
    upload_id: str
    parts: dict[int, _Part] = field(default_factory=dict)
    completed: bool = False


@dataclass(frozen=True)
class PartMaterial:
    part_number: int
    etag: str
    size_bytes: int
    sha256: str


class FakeMultipartStorage:
    def __init__(self) -> None:
        self._uploads: dict[str, _Upload] = {}
        self._by_key: dict[str, str] = {}
        self._objects: dict[str, bytes] = {}
        self._failures: list[str] = []
        self._list_not_found = False
        self._lock = threading.RLock()
        self.presigned_calls: list[dict[str, object]] = []
        self.download_presigned_calls: list[dict[str, object]] = []
        self.stream_calls: list[tuple[str, int]] = []

    # ---------------------------------------------------------- 测试操作
    def fail_next(self, operation: str) -> None:
        self._failures.append(operation)

    def fail_next_list_parts_not_found(self) -> None:
        self._list_not_found = True

    def _maybe_fail(self, operation: str) -> None:
        if operation in self._failures:
            self._failures.remove(operation)
            if operation == "list_not_found":
                raise MultipartUploadNotFoundError("模拟 multipart 已被并发完成")
            raise ObjectStorageUnavailableError(f"模拟对象存储故障：{operation}")

    def put_object(self, object_key: str, data: bytes) -> None:
        """测试直接写入已完成对象，绕过 multipart 流程。"""
        with self._lock:
            self._objects[object_key] = data

    def put_part(
        self,
        object_key: str,
        part_number: int,
        data: bytes,
    ) -> PartMaterial:
        with self._lock:
            upload = self._upload_for_key(object_key)
            if upload.completed:
                raise AssertionError("multipart 已完成，不能继续上传分片")
            part = _make_part(data)
            upload.parts[part_number] = part
            return PartMaterial(
                part_number=part_number,
                etag=part.etag,
                size_bytes=len(data),
                sha256=part.sha256,
            )

    def upload_id_for(self, object_key: str) -> str:
        with self._lock:
            return self._upload_for_key(object_key).upload_id

    def object_bytes(self, object_key: str) -> bytes:
        with self._lock:
            return self._objects[object_key]

    def part_numbers(self, object_key: str) -> list[int]:
        with self._lock:
            return sorted(self._upload_for_key(object_key).parts)

    # ---------------------------------------------------------- 端口实现
    def initiate_multipart(self, object_key: str) -> str:
        with self._lock:
            self._maybe_fail("initiate")
            upload_id = f"upload-{hashlib.sha256(object_key.encode()).hexdigest()[:24]}"
            self._uploads[upload_id] = _Upload(key=object_key, upload_id=upload_id)
            self._by_key[object_key] = upload_id
            return upload_id

    def presign_upload_part(
        self,
        object_key: str,
        upload_id: str,
        *,
        part_number: int,
        content_length: int,
        checksum_sha256_base64: str | None,
        expires_in_seconds: int,
    ) -> PresignedPart:
        with self._lock:
            self._maybe_fail("presign")
            upload = self._uploads.get(upload_id)
            if upload is None or upload.key != object_key:
                raise ObjectStorageUnavailableError("multipart 不存在")
            expires_at = datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
            self.presigned_calls.append(
                {
                    "object_key": object_key,
                    "upload_id": upload_id,
                    "part_number": part_number,
                    "content_length": content_length,
                    "checksum_sha256_base64": checksum_sha256_base64,
                }
            )
            headers = {"Content-Length": str(content_length)}
            if checksum_sha256_base64 is not None:
                headers["x-amz-checksum-sha256"] = checksum_sha256_base64
            return PresignedPart(
                url=(
                    f"https://fake-s3.test/{object_key}"
                    f"?uploadId={upload_id}&partNumber={part_number}"
                    "&X-Amz-Signature=fake"
                ),
                headers=headers,
                expires_at=expires_at,
            )

    def presign_download(
        self,
        object_key: str,
        *,
        expires_in_seconds: int,
    ) -> PresignedDownload:
        with self._lock:
            self._maybe_fail("presign_download")
            self.download_presigned_calls.append(
                {
                    "object_key": object_key,
                    "expires_in_seconds": expires_in_seconds,
                }
            )
            return PresignedDownload(
                url=(
                    f"https://fake-s3.test/{object_key}"
                    "?X-Amz-Expires=300&X-Amz-Signature=fake-download"
                ),
                expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
            )

    def list_parts(self, object_key: str, upload_id: str) -> list[StoredPart]:
        with self._lock:
            self._maybe_fail("list")
            if self._list_not_found:
                self._list_not_found = False
                raise MultipartUploadNotFoundError("模拟 multipart 已被并发完成")
            upload = self._uploads.get(upload_id)
            if upload is None or upload.key != object_key:
                raise MultipartUploadNotFoundError("multipart 不存在")
            if upload.completed:
                # 与真实 S3 一致：完成后的 multipart 再 ListParts 返回 NoSuchUpload。
                raise MultipartUploadNotFoundError("multipart 已完成")
            return [
                StoredPart(
                    part_number=number,
                    etag=part.etag,
                    size_bytes=len(part.data),
                    checksum_sha256=part.sha256,
                )
                for number, part in sorted(upload.parts.items())
            ]

    def complete_multipart(
        self,
        object_key: str,
        upload_id: str,
        parts: list[StoredPart],
    ) -> None:
        with self._lock:
            self._maybe_fail("complete")
            upload = self._uploads.get(upload_id)
            if upload is None or upload.key != object_key:
                raise MultipartUploadNotFoundError("multipart 不存在")
            if upload.completed:
                # 模拟 S3 Complete 收到 NoSuchUpload 后 HeadObject 判定对象已存在，
                # 适配器按幂等成功处理。
                return
            requested = {part.part_number: part.etag for part in parts}
            stored = {number: part.etag for number, part in upload.parts.items()}
            if requested != stored:
                raise ObjectStorageUnavailableError("完成分片清单与对象存储不一致")
            content = b"".join(upload.parts[number].data for number in sorted(upload.parts))
            self._objects[object_key] = content
            upload.completed = True

    def abort_multipart(self, object_key: str, upload_id: str) -> None:
        with self._lock:
            self._maybe_fail("abort")
            upload = self._uploads.get(upload_id)
            if upload is not None:
                upload.parts.clear()
                upload.completed = True

    def read_range(self, object_key: str, *, start: int, length: int) -> bytes:
        with self._lock:
            self._maybe_fail("read")
            content = self._objects.get(object_key)
            if content is None:
                raise ObjectStorageUnavailableError("对象不存在")
            if length <= 0:
                return b""
            return content[start : start + length]

    def stream_object(self, object_key: str, *, chunk_size: int) -> Iterator[bytes]:
        with self._lock:
            self._maybe_fail("stream")
            self.stream_calls.append((object_key, chunk_size))
            content = self._objects.get(object_key)
        if content is None:
            raise ObjectNotFoundError("对象不存在")
        if chunk_size <= 0:
            raise ObjectStorageUnavailableError("流式读取块大小必须为正数")
        for start in range(0, len(content), chunk_size):
            yield content[start : start + chunk_size]

    def delete_object(self, object_key: str) -> None:
        with self._lock:
            self._maybe_fail("delete")
            self._objects.pop(object_key, None)

    def _upload_for_key(self, object_key: str) -> _Upload:
        upload_id = self._by_key.get(object_key)
        if upload_id is None:
            raise AssertionError(f"未找到 object key 对应的 multipart：{object_key}")
        return self._uploads[upload_id]


def _make_part(data: bytes) -> _Part:
    digest = hashlib.md5(data, usedforsecurity=False).hexdigest()  # noqa: S324 - 模拟 ETag
    sha = f"sha256:{hashlib.sha256(data).hexdigest()}"
    return _Part(data=data, etag=digest, sha256=sha)
