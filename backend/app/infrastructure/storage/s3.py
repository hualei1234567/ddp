"""S3/MinIO multipart 适配。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.application.ingestion.ports import PresignedPart, StoredPart
from app.core.config import Settings
from app.domain.ingestion.errors import ObjectStorageUnavailableError
from app.domain.ingestion.validation import sha256_from_base64

_UPLOAD_PART = "upload_part"
_LIST_PARTS = "list_parts"
_COMPLETE = "complete_multipart_upload"
_ABORT = "abort_multipart_upload"
_GET_OBJECT = "get_object"
_DELETE_OBJECT = "delete_object"


class S3MultipartStorage:
    """S3 兼容对象存储适配器。

    所有方法把 SDK 异常收敛为 ``object_storage_unavailable``，不把驱动细节
    返回给客户端。预签名 URL 不写日志。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )

    @property
    def bucket(self) -> str:
        return self._settings.s3_bucket

    def initiate_multipart(self, object_key: str) -> str:
        try:
            response = self._client.create_multipart_upload(
                Bucket=self.bucket,
                Key=object_key,
                ContentType="application/octet-stream",
            )
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageUnavailableError("对象存储不可用") from exc
        upload_id = response.get("UploadId")
        if not isinstance(upload_id, str) or not upload_id:
            raise ObjectStorageUnavailableError("对象存储未返回 multipart UploadId")
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
        params: dict[str, object] = {
            "Bucket": self.bucket,
            "Key": object_key,
            "UploadId": upload_id,
            "PartNumber": part_number,
            "ContentLength": content_length,
        }
        headers = {"Content-Length": str(content_length)}
        if checksum_sha256_base64 is not None:
            params["ChecksumSHA256"] = checksum_sha256_base64
            headers["x-amz-checksum-sha256"] = checksum_sha256_base64
        try:
            url = self._client.generate_presigned_url(
                _UPLOAD_PART,
                Params=params,
                ExpiresIn=expires_in_seconds,
                HttpMethod="PUT",
            )
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageUnavailableError("无法生成分片上传 URL") from exc
        return PresignedPart(
            url=url,
            headers=headers,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
        )

    def list_parts(self, object_key: str, upload_id: str) -> list[StoredPart]:
        parts: list[StoredPart] = []
        marker: int | None = None
        try:
            while True:
                kwargs: dict[str, object] = {
                    "Bucket": self.bucket,
                    "Key": object_key,
                    "UploadId": upload_id,
                }
                if marker is not None:
                    kwargs["PartNumberMarker"] = marker
                response = self._client.list_parts(**kwargs)
                for item in response.get("Parts", []):
                    checksum = item.get("ChecksumSHA256")
                    parts.append(
                        StoredPart(
                            part_number=int(item["PartNumber"]),
                            etag=str(item["ETag"]),
                            size_bytes=int(item["Size"]),
                            checksum_sha256=(
                                sha256_from_base64(checksum)
                                if isinstance(checksum, str) and checksum
                                else None
                            ),
                        )
                    )
                if not response.get("IsTruncated"):
                    break
                next_marker = response.get("NextPartNumberMarker")
                if next_marker is None:
                    break
                marker = int(next_marker)
        except (BotoCoreError, ClientError, KeyError, TypeError, ValueError) as exc:
            raise ObjectStorageUnavailableError("无法读取对象存储分片列表") from exc
        return parts

    def complete_multipart(
        self,
        object_key: str,
        upload_id: str,
        parts: list[StoredPart],
    ) -> None:
        try:
            self._client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=object_key,
                UploadId=upload_id,
                MultipartUpload={
                    "Parts": [
                        {
                            "PartNumber": part.part_number,
                            "ETag": _quoted_etag(part.etag),
                        }
                        for part in parts
                    ]
                },
            )
        except ClientError as exc:
            # 上一次请求可能已完成对象但事务未提交；按最终对象大小判定幂等成功。
            if _error_code(exc) == "NoSuchUpload" and self._object_matches(
                object_key, sum(part.size_bytes for part in parts)
            ):
                return
            raise ObjectStorageUnavailableError("对象存储完成 multipart 失败") from exc
        except BotoCoreError as exc:
            raise ObjectStorageUnavailableError("对象存储完成 multipart 失败") from exc

    def _object_matches(self, object_key: str, expected_size: int) -> bool:
        try:
            head = self._client.head_object(Bucket=self.bucket, Key=object_key)
        except (BotoCoreError, ClientError):
            return False
        return int(head.get("ContentLength", -1)) == expected_size

    def abort_multipart(self, object_key: str, upload_id: str) -> None:
        try:
            self._client.abort_multipart_upload(
                Bucket=self.bucket,
                Key=object_key,
                UploadId=upload_id,
            )
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageUnavailableError("对象存储终止 multipart 失败") from exc

    def read_range(self, object_key: str, *, start: int, length: int) -> bytes:
        if length <= 0:
            return b""
        try:
            response = self._client.get_object(
                Bucket=self.bucket,
                Key=object_key,
                Range=f"bytes={start}-{start + length - 1}",
            )
            body = response["Body"].read()
        except (BotoCoreError, ClientError, KeyError) as exc:
            raise ObjectStorageUnavailableError("读取对象内容失败") from exc
        return bytes(body)

    def delete_object(self, object_key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=object_key)
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStorageUnavailableError("删除对象失败") from exc


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _quoted_etag(etag: str) -> str:
    value = etag.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value
    return f'"{value}"'
