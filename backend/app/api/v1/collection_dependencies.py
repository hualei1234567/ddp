"""采集任务/批次 API 依赖装配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.application.collection import (
    CollectionBatchUseCases,
    CollectionTaskUseCases,
)
from app.infrastructure.repositories.assignment_directory import SqlAssignmentDirectory
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlIdempotencyRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.collection_batches import SqlCollectionBatchRepository
from app.infrastructure.repositories.collection_tasks import SqlCollectionTaskRepository


@dataclass(frozen=True, slots=True)
class CollectionUseCases:
    tasks: CollectionTaskUseCases
    batches: CollectionBatchUseCases


def get_collection_use_cases(session: DbSession) -> CollectionUseCases:
    tasks = SqlCollectionTaskRepository(session)
    batches = SqlCollectionBatchRepository(session)
    directory = SqlAssignmentDirectory(session)
    audit = SqlAuditRepository(session)
    outbox = SqlOutboxRepository(session)
    idempotency = SqlIdempotencyRepository(session)
    return CollectionUseCases(
        tasks=CollectionTaskUseCases(
            tasks=tasks,
            batches=batches,
            directory=directory,
            audit=audit,
            outbox=outbox,
            idempotency=idempotency,
            uow=session,
        ),
        batches=CollectionBatchUseCases(
            tasks=tasks,
            batches=batches,
            directory=directory,
            audit=audit,
            outbox=outbox,
            idempotency=idempotency,
            uow=session,
        ),
    )


CollectionUseCasesDep = Annotated[CollectionUseCases, Depends(get_collection_use_cases)]
