"""采集任务/批次/素材 API 依赖装配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.application.collection import (
    ActionSpecUseCases,
    CollectionBatchUseCases,
    CollectionPlanUseCases,
    CollectionSopUseCases,
    CollectionTaskUseCases,
    ScenarioUseCases,
)
from app.infrastructure.repositories.assignment_directory import SqlAssignmentDirectory
from app.infrastructure.repositories.collection_asset_directory import (
    SqlCollectionAssetDirectory,
)
from app.infrastructure.repositories.collection_assets import (
    SqlActionSpecRepository,
    SqlCollectionPlanRepository,
    SqlCollectionSopRepository,
    SqlScenarioRepository,
)
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlIdempotencyRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.collection_batches import SqlCollectionBatchRepository
from app.infrastructure.repositories.collection_tasks import SqlCollectionTaskRepository
from app.infrastructure.repositories.locations import SqlLocationDirectory


@dataclass(frozen=True, slots=True)
class CollectionUseCases:
    tasks: CollectionTaskUseCases
    batches: CollectionBatchUseCases
    plans: CollectionPlanUseCases
    sops: CollectionSopUseCases
    scenarios: ScenarioUseCases
    action_specs: ActionSpecUseCases


def get_collection_use_cases(session: DbSession) -> CollectionUseCases:
    tasks = SqlCollectionTaskRepository(session)
    batches = SqlCollectionBatchRepository(session)
    directory = SqlAssignmentDirectory(session)
    asset_directory = SqlCollectionAssetDirectory(session)
    location_directory = SqlLocationDirectory(session)
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
            asset_directory=asset_directory,
            location_directory=location_directory,
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
        plans=CollectionPlanUseCases(
            repository=SqlCollectionPlanRepository(session),
            audit=audit,
            outbox=outbox,
            idempotency=idempotency,
            uow=session,
        ),
        sops=CollectionSopUseCases(
            repository=SqlCollectionSopRepository(session),
            audit=audit,
            outbox=outbox,
            idempotency=idempotency,
            uow=session,
        ),
        scenarios=ScenarioUseCases(
            repository=SqlScenarioRepository(session),
            audit=audit,
            outbox=outbox,
            idempotency=idempotency,
            uow=session,
        ),
        action_specs=ActionSpecUseCases(
            repository=SqlActionSpecRepository(session),
            audit=audit,
            outbox=outbox,
            idempotency=idempotency,
            uow=session,
        ),
    )


CollectionUseCasesDep = Annotated[CollectionUseCases, Depends(get_collection_use_cases)]
