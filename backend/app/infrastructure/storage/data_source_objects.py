"""导入器使用的对象存储写入适配器。

只暴露 ``put`` 一个方法：导入链路只需要"把已验证的字节写入不可变对象"，
不需要 multipart / 预签名等上传链路的能力。保持接口最小，避免误用。
"""

from __future__ import annotations

from app.infrastructure.storage.s3 import S3MultipartStorage


class DataSourceObjectWriter:
    def __init__(self, storage: S3MultipartStorage) -> None:
        self._storage = storage

    def put(self, object_key: str, data: bytes, *, content_type: str) -> None:
        # put_bytes 内部不做业务校验；调用方（importer）已校验大小与内容签名。
        self._storage.put_bytes(object_key, data, content_type=content_type)


class DataSourceObjectReader:
    """签发媒体资产的临时播放 URL。

    有效期刻意短（默认 15 分钟）：审看是交互行为，长 URL 一旦泄漏就是
    不受控的对外访问入口。
    """

    def __init__(self, storage: S3MultipartStorage, *, expires_seconds: int = 900) -> None:
        self._storage = storage
        self._expires = expires_seconds

    def presign_get(self, object_key: str, *, expires_seconds: int) -> str:
        presigned = self._storage.presign_download(object_key, expires_in_seconds=expires_seconds)
        return presigned.url
