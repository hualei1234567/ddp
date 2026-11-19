"""处理运行阶段的对象存储适配器（有界读取 + 阶段产物读写）。

阶段编排只依赖应用层端口：

- :class:`app.application.processing.ports.InputObjectReader`：有界读取输入对象；
- :class:`app.application.processing.ports.StageArtifactStore`：读写阶段产物。

本适配器把 S3 适配器的驱动异常翻译成应用层可识别的 ``Object*Error``，避免
Application 层 import 数据接入域的错误类型。语义严格区分：

- 对象不存在 → ``ObjectMissingError``（稳定业务结论，``retryable=False``）；
- 存储不可用 → ``ObjectInspectionError``（可重试）；
- 超过读取上限 → ``ObjectTooLargeError``（显式失败，绝不截断后冒充完整结果）。

大对象只以流式方式落盘到临时文件，不整体读入内存；HTTP 线程不接触本适配器。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.application.processing.ports import (
    ObjectInspectionError,
    ObjectMissingError,
    ObjectTooLargeError,
)
from app.domain.ingestion.errors import (
    ObjectNotFoundError,
    ObjectStorageUnavailableError,
)


class _Storage(Protocol):
    def object_size(self, object_key: str) -> int | None: ...

    def read_range(self, object_key: str, *, start: int, length: int) -> bytes: ...

    def stream_object(self, object_key: str, *, chunk_size: int): ...

    def put_bytes(self, object_key: str, data: bytes, *, content_type: str) -> None: ...


class S3InputObjectInspector:
    """仅暴露对象存在性（``object_size``）的只读视图。"""

    def __init__(self, storage: _Storage) -> None:
        self._storage = storage

    def object_size(self, object_key: str) -> int | None:
        try:
            return self._storage.object_size(object_key)
        except ObjectStorageUnavailableError as exc:
            raise ObjectInspectionError("对象存储不可用") from exc


class S3InputObjectReader:
    """有界读取输入对象：范围读取 + 流式落盘，均受调用方上限约束。"""

    #: 流式落盘块大小；与导入 Worker 的校验块一致，避免两套读放大口径。
    CHUNK_SIZE_BYTES = 1024 * 1024

    def __init__(self, storage: _Storage) -> None:
        self._storage = storage

    def object_size(self, object_key: str) -> int | None:
        try:
            return self._storage.object_size(object_key)
        except ObjectStorageUnavailableError as exc:
            raise ObjectInspectionError("对象存储不可用") from exc

    def read_range(self, object_key: str, *, start: int, length: int) -> bytes:
        if start < 0 or length < 0:
            raise ValueError("范围读取的偏移与长度不能为负")
        try:
            return self._storage.read_range(object_key, start=start, length=length)
        except ObjectStorageUnavailableError as exc:
            raise ObjectInspectionError("对象存储不可用") from exc

    def download_to(
        self,
        object_key: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> int:
        if max_bytes <= 0:
            raise ValueError("下载上限必须为正整数")
        written = 0
        try:
            chunks = self._storage.stream_object(
                object_key,
                chunk_size=self.CHUNK_SIZE_BYTES,
            )
            with destination.open("wb") as handle:
                for chunk in chunks:
                    written += len(chunk)
                    if written > max_bytes:
                        raise ObjectTooLargeError(f"对象超过 {max_bytes} 字节上限")
                    handle.write(chunk)
        except ObjectNotFoundError as exc:
            raise ObjectMissingError("对象不存在") from exc
        except ObjectStorageUnavailableError as exc:
            raise ObjectInspectionError("对象存储不可用") from exc
        return written


class S3StageArtifactStore:
    """阶段产物读写：服务端生成 Key，内容有界。"""

    def __init__(self, storage: _Storage, *, content_type: str = "application/json") -> None:
        self._storage = storage
        self._content_type = content_type

    def put_bytes(self, object_key: str, data: bytes) -> None:
        try:
            self._storage.put_bytes(object_key, data, content_type=self._content_type)
        except ObjectStorageUnavailableError as exc:
            raise ObjectInspectionError("对象存储不可用") from exc

    def read_bytes(self, object_key: str, *, max_bytes: int) -> bytes:
        if max_bytes <= 0:
            raise ValueError("读取上限必须为正整数")
        size = self.object_size(object_key)
        if size is None:
            raise ObjectMissingError("对象不存在")
        if size > max_bytes:
            raise ObjectTooLargeError(f"对象超过 {max_bytes} 字节上限")
        try:
            return self._storage.read_range(object_key, start=0, length=size)
        except ObjectStorageUnavailableError as exc:
            raise ObjectInspectionError("对象存储不可用") from exc

    def object_size(self, object_key: str) -> int | None:
        try:
            return self._storage.object_size(object_key)
        except ObjectStorageUnavailableError as exc:
            raise ObjectInspectionError("对象存储不可用") from exc


__all__ = [
    "S3InputObjectInspector",
    "S3InputObjectReader",
    "S3StageArtifactStore",
]
