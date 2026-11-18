"""处理运行应用层端口。

仓储协议只暴露本项目作用域内的操作；``save`` 必须带 ``expected_version``，
由实现层以条件更新落实乐观锁。

``InputObjectInspector`` 是阶段处理器看到的**对象存在性**能力，只暴露一个
标量查询：阶段编排不需要也没被授权读写对象本体。存储不可用与控制流分开——
前者抛 :class:`ObjectInspectionError`（可重试），对象不存在返回 ``None``
（稳定的业务结果）。
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.identity.models import ProjectScope
from app.domain.processing.models import ProcessingRun
from app.domain.processing.steps import ProcessingStep


class ObjectInspectionError(RuntimeError):
    """对象存储暂不可用；阶段失败可重试，不等同于"对象不存在"。"""


class UnitOfWork(Protocol):
    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class InputObjectInspector(Protocol):
    def object_size(self, object_key: str) -> int | None:
        """返回对象字节数；对象不存在返回 ``None``，存储故障抛 ObjectInspectionError。"""
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
    "ObjectInspectionError",
    "ProcessingRunRepository",
    "ProcessingStepRepository",
    "UnitOfWork",
]
