"""处理运行应用层端口。

仓储协议只暴露本项目作用域内的操作；``save`` 必须带 ``expected_version``，
由实现层以条件更新落实乐观锁。

``InputObjectInspector`` 是阶段处理器看到的**对象存在性**能力，只暴露一个
标量查询：阶段编排不需要也没被授权读写对象本体。存储不可用与控制流分开——
前者抛 :class:`ObjectInspectionError`（可重试），对象不存在返回 ``None``
（稳定的业务结果）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.domain.identity.models import ProjectScope
from app.domain.processing.models import ProcessingRun
from app.domain.processing.steps import ProcessingStep


class ObjectAccessError(RuntimeError):
    """对象读取失败的基类；阶段按 ``code`` 映射为稳定失败码。"""

    code = "object_access_failed"


class ObjectInspectionError(ObjectAccessError):
    """对象存储暂不可用；阶段失败可重试，不等同于"对象不存在"。"""

    code = "object_storage_unavailable"


class ObjectMissingError(ObjectAccessError):
    """对象不存在；属于稳定的业务结论，重试只会重复同一结果。"""

    code = "object_missing"


class ObjectTooLargeError(ObjectAccessError):
    """对象超过阶段允许读取的上限；显式失败，绝不截断后冒充完整结果。"""

    code = "object_exceeds_limit"


class UnitOfWork(Protocol):
    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class InputObjectInspector(Protocol):
    def object_size(self, object_key: str) -> int | None:
        """返回对象字节数；对象不存在返回 ``None``，存储故障抛 ObjectInspectionError。"""
        ...


class InputObjectReader(Protocol):
    """阶段可用的**有界**对象读取能力。

    只暴露按范围读取与流式落盘：大对象不得整体读入内存，也不得在 HTTP 线程
    处理。实现层负责把驱动异常翻译为本模块的 ``Object*Error``。
    """

    def object_size(self, object_key: str) -> int | None:
        """返回对象字节数；对象不存在返回 ``None``，存储故障抛 ``ObjectInspectionError``。"""
        ...

    def read_range(self, object_key: str, *, start: int, length: int) -> bytes:
        """读取 ``[start, start+length)`` 区间；用于内容签名探测（有界小片段）。"""
        ...

    def download_to(
        self,
        object_key: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> int:
        """流式下载到本地文件，返回写入字节数；超过 ``max_bytes`` 抛 ``ObjectTooLargeError``。"""
        ...


class StageArtifactStore(Protocol):
    """阶段产物的对象存储读写能力（服务端生成 Key，不信任客户端路径）。"""

    def put_bytes(self, object_key: str, data: bytes) -> None:
        """写入不可变产物；同 Key 覆盖必须幂等。"""
        ...

    def read_bytes(self, object_key: str, *, max_bytes: int) -> bytes:
        """有界读取产物；超过 ``max_bytes`` 抛 ``ObjectTooLargeError``。"""
        ...


class ProcessingRunRepository(Protocol):
    def add(self, scope: ProjectScope, run: ProcessingRun) -> ProcessingRun: ...

    def get(self, scope: ProjectScope, run_id: UUID) -> ProcessingRun | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        stage: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ProcessingRun]: ...

    def save(
        self,
        scope: ProjectScope,
        run: ProcessingRun,
        *,
        expected_version: int,
    ) -> ProcessingRun: ...


class ProcessingStepRepository(Protocol):
    def get_by_key(
        self,
        scope: ProjectScope,
        *,
        run_id: UUID,
        stage: str,
        attempt: int,
    ) -> ProcessingStep | None: ...

    def upsert(self, scope: ProjectScope, step: ProcessingStep) -> ProcessingStep:
        """按 ``(run_id, stage, attempt)`` 插入或更新，保证重试时只有一行。"""
        ...

    def list(
        self,
        scope: ProjectScope,
        *,
        run_id: UUID,
        after_id: UUID | None = None,
        limit: int = 100,
    ) -> list[ProcessingStep]: ...


__all__ = [
    "InputObjectInspector",
    "InputObjectReader",
    "ObjectAccessError",
    "ObjectInspectionError",
    "ObjectMissingError",
    "ObjectTooLargeError",
    "ProcessingRunRepository",
    "ProcessingStepRepository",
    "StageArtifactStore",
    "UnitOfWork",
]
