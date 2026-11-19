"""处理阶段测试替身（显式标注：内存实现，非真实对象存储）。

只实现应用层端口（``InputObjectReader`` / ``StageArtifactStore``），用于验证
阶段的只读、有界、脱敏与幂等语义；真实对象存储行为由 MinIO 集成验收覆盖。
"""

from __future__ import annotations

from pathlib import Path

from app.application.processing.ports import (
    ObjectMissingError,
    ObjectTooLargeError,
)


class FakeObjectReader:
    """按 Key 提供对象内容；``object_size`` 与范围/流式读取口径一致。"""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self._objects = dict(objects or {})
        self.size_calls: list[str] = []
        self.range_calls: list[tuple[str, int, int]] = []
        self.download_calls: list[tuple[str, int]] = []

    def put(self, object_key: str, data: bytes) -> None:
        self._objects[object_key] = data

    def object_size(self, object_key: str) -> int | None:
        self.size_calls.append(object_key)
        data = self._objects.get(object_key)
        return None if data is None else len(data)

    def read_range(self, object_key: str, *, start: int, length: int) -> bytes:
        self.range_calls.append((object_key, start, length))
        data = self._objects.get(object_key)
        if data is None:
            raise ObjectMissingError("对象不存在")
        if start < 0 or length < 0:
            raise ValueError("范围读取的偏移与长度不能为负")
        return data[start : start + length]

    def download_to(self, object_key: str, destination: Path, *, max_bytes: int) -> int:
        self.download_calls.append((object_key, max_bytes))
        data = self._objects.get(object_key)
        if data is None:
            raise ObjectMissingError("对象不存在")
        if len(data) > max_bytes:
            raise ObjectTooLargeError(f"对象超过 {max_bytes} 字节上限")
        destination.write_bytes(data)
        return len(data)


class FakeArtifactStore:
    """内存产物存储；同 Key 覆盖幂等，读取受 ``max_bytes`` 约束。"""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []
        self.read_calls: list[str] = []

    def put_bytes(self, object_key: str, data: bytes) -> None:
        self.put_calls.append(object_key)
        self._objects[object_key] = data

    def read_bytes(self, object_key: str, *, max_bytes: int) -> bytes:
        self.read_calls.append(object_key)
        data = self._objects.get(object_key)
        if data is None:
            raise ObjectMissingError("对象不存在")
        if len(data) > max_bytes:
            raise ObjectTooLargeError(f"对象超过 {max_bytes} 字节上限")
        return data

    def keys(self) -> list[str]:
        return sorted(self._objects)


__all__ = ["FakeArtifactStore", "FakeObjectReader"]
