"""场地域 API 依赖装配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.application.locations import LocationUseCases
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlIdempotencyRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.locations import SqlLocationRepository


@dataclass(frozen=True, slots=True)
class LocationServices:
    locations: LocationUseCases


def get_location_services(session: DbSession) -> LocationServices:
    return LocationServices(
        locations=LocationUseCases(
            locations=SqlLocationRepository(session),
            # 复用采集域的审计/outbox/幂等 SQL 适配器：它们只是
            # ``audit_logs`` / ``outbox_events`` / ``idempotency_records``
            # 三张共享表上的薄适配，规则本体在 ``app.application.audit`` 与
            # ``app.application.idempotency``。另写一份等于复制这段 SQL。
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
            idempotency=SqlIdempotencyRepository(session),
            uow=session,
        )
    )


LocationServicesDep = Annotated[LocationServices, Depends(get_location_services)]
