"""项目管理 API 依赖装配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.application.projects import ProjectUseCases
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.projects import (
    SqlProjectMemberRepository,
    SqlProjectRepository,
    SqlUserDirectory,
)


@dataclass(frozen=True, slots=True)
class ProjectServices:
    projects: ProjectUseCases


def get_project_services(session: DbSession) -> ProjectServices:
    return ProjectServices(
        projects=ProjectUseCases(
            projects=SqlProjectRepository(session),
            members=SqlProjectMemberRepository(session),
            users=SqlUserDirectory(session),
            # 复用采集域的审计/outbox 薄适配：规则本体在 ``app.application.audit``，
            # 它们只是 ``audit_logs`` / ``outbox_events`` 两张共享表上的 SQL 适配。
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
            uow=session,
        )
    )


ProjectServicesDep = Annotated[ProjectServices, Depends(get_project_services)]
