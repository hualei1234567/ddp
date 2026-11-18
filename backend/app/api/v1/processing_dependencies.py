"""处理运行 API 依赖装配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession, get_project_scope, get_write_scope
from app.api.v1.preconditions import require_expected_version
from app.application.processing.use_cases import (
    ProcessingRunUseCases,
    ProcessingWorkerUseCases,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.processing import (
    SqlProcessingRunRepository,
    SqlProcessingRunStepRepository,
)

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]


@dataclass(frozen=True, slots=True)
class ProcessingServices:
    runs: ProcessingRunUseCases
    worker: ProcessingWorkerUseCases


def get_processing_services(session: DbSession) -> ProcessingServices:
    runs = SqlProcessingRunRepository(session)
    return ProcessingServices(
        runs=ProcessingRunUseCases(
            runs,
            SqlProcessingRunStepRepository(session),
            session,
            # 创建时必须同事务写审计 + 投递类事件：缺任一依赖会静默留下
            # "永远不会被执行"的运行，因此这里显式装配，不提供默认值。
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
        ),
        worker=ProcessingWorkerUseCases(runs, session),
    )


ProcessingServicesDep = Annotated[ProcessingServices, Depends(get_processing_services)]


__all__ = [
    "ExpectedVersion",
    "ProcessingServices",
    "ProcessingServicesDep",
    "ReadScope",
    "WriteScope",
]
