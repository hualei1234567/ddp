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
from app.infrastructure.repositories.processing import SqlProcessingRunRepository

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
        runs=ProcessingRunUseCases(runs, session),
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
