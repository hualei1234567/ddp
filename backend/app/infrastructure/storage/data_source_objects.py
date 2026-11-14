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
