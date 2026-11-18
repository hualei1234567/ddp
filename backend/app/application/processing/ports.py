"""处理运行应用层端口。

仓储协议只暴露本项目作用域内的操作；``save`` 必须带 ``expected_version``，
由实现层以条件更新落实乐观锁。
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.identity.models import ProjectScope
from app.domain.processing.models import ProcessingRun


class UnitOfWork(Protocol):
    def commit(self) -> None: ...


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


__all__ = ["ProcessingRunRepository", "UnitOfWork"]
