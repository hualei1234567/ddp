"""S3/MinIO 对象存储访问。大文件先落临时文件，不整块读入内存。"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings
from app.errors import StorageError

logger = logging.getLogger("ddp.local_adapter.storage")
_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size: int
    last_modified: datetime | None


@dataclass(frozen=True, slots=True)
class DownloadedObject:
    path: Path
    size: int
    sha256: str


class ObjectStorage(Protocol):
    bucket: str

    def get_bytes(self, key: str, *, max_bytes: int) -> bytes: ...

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None: ...

    def object_size(self, key: str) -> int | None: ...

    def list_objects(self, prefix: str, *, max_items: int) -> list[StoredObject]: ...

    def download_to_file(
        self, key: str, path: Path, *, max_bytes: int
    ) -> DownloadedObject: ...


def _is_not_found(exc: ClientError) -> bool:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return code in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    @property
    def bucket(self) -> str:
        return self._settings.s3_bucket

    def get_bytes(self, key: str, *, max_bytes: int) -> bytes:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                raise StorageError("object_not_found", f"对象不存在：{key}") from exc
            raise StorageError(
                "object_storage_unavailable", "对象存储读取失败"
            ) from exc
        except BotoCoreError as exc:
            raise StorageError("object_storage_unavailable", "对象存储不可达") from exc
        length = response.get("ContentLength")
        if isinstance(length, int) and length > max_bytes:
            response["Body"].close()
            raise StorageError("object_too_large", f"对象超过大小上限：{key}")
        chunks: list[bytes] = []
        total = 0
        body = response["Body"]
        try:
            for chunk in body.iter_chunks(chunk_size=_CHUNK):
                total += len(chunk)
                if total > max_bytes:
                    raise StorageError("object_too_large", f"对象超过大小上限：{key}")
                chunks.append(chunk)
        finally:
            body.close()
        return b"".join(chunks)

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(
                "object_storage_unavailable", f"对象写入失败：{key}"
            ) from exc

    def object_size(self, key: str) -> int | None:
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                return None
            raise StorageError(
                "object_storage_unavailable", "对象存储读取失败"
            ) from exc
        except BotoCoreError as exc:
            raise StorageError("object_storage_unavailable", "对象存储不可达") from exc
        length = response.get("ContentLength")
        return int(length) if isinstance(length, int) else None

    def list_objects(self, prefix: str, *, max_items: int) -> list[StoredObject]:
        results: list[StoredObject] = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    results.append(
                        StoredObject(
                            key=str(item["Key"]),
                            size=int(item.get("Size", 0)),
                            last_modified=item.get("LastModified"),
                        )
                    )
                    if len(results) > max_items:
                        raise StorageError(
                            "object_listing_truncated",
                            f"前缀 {prefix} 下对象超过 {max_items} 条，拒绝在不完整清单上做选择",
                        )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(
                "object_storage_unavailable", "对象存储列举失败"
            ) from exc
        return results

    def download_to_file(
        self, key: str, path: Path, *, max_bytes: int
    ) -> DownloadedObject:
        size = self.object_size(key)
        if size is None:
            raise StorageError("object_not_found", f"对象不存在：{key}")
        if size > max_bytes:
            raise StorageError("object_too_large", f"视频超过大小上限：{key}")
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(
                "object_storage_unavailable", f"对象下载失败：{key}"
            ) from exc
        digest = hashlib.sha256()
        total = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        body = response["Body"]
        try:
            with path.open("wb") as handle:
                for chunk in body.iter_chunks(chunk_size=_CHUNK):
                    total += len(chunk)
                    if total > max_bytes:
                        raise StorageError(
                            "object_too_large", f"视频超过大小上限：{key}"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
        finally:
            body.close()
        if total != size:
            raise StorageError(
                "object_size_mismatch", f"下载长度与对象元数据不一致：{key}"
            )
        return DownloadedObject(path=path, size=total, sha256=digest.hexdigest())

    def close(self) -> None:
        self._client.close()


__all__ = [
    "DownloadedObject",
    "ObjectStorage",
    "S3Storage",
    "StoredObject",
]
