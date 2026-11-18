"""对象存在性检查适配器（处理运行阶段用）。

阶段编排只依赖 :class:`app.application.processing.ports.InputObjectInspector`；
本适配器把 S3 适配器的驱动异常翻译成应用层可识别的
:class:`ObjectInspectionError`，避免 Application 层 import 数据接入域的错误类型。

语义严格区分：

- 对象不存在 → 返回 ``None``（稳定的业务结论，阶段失败码 ``object_missing``）；
- 存储不可用 → 抛 ``ObjectInspectionError``（可重试，失败码
  ``object_storage_unavailable``）。
"""

from __future__ import annotations

from typing import Protocol

from app.application.processing.ports import ObjectInspectionError
from app.domain.ingestion.errors import ObjectStorageUnavailableError


class _SizeReader(Protocol):
    def object_size(self, object_key: str) -> int | None: ...


class S3InputObjectInspector:
    def __init__(self, storage: _SizeReader) -> None:
        self._storage = storage

    def object_size(self, object_key: str) -> int | None:
        try:
            return self._storage.object_size(object_key)
        except ObjectStorageUnavailableError as exc:
            raise ObjectInspectionError("对象存储不可用") from exc


__all__ = ["S3InputObjectInspector"]
