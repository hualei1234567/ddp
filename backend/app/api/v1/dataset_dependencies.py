"""数据集 API 依赖装配与权限。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Path, Request

from app.api.deps import DbSession, get_project_context, get_project_scope, get_write_scope
from app.api.v1.preconditions import require_expected_version
from app.application.datasets.datasets import DatasetUseCases as DatasetCrudUseCases
from app.application.datasets.lineage import DatasetVersionLineageRecorder
from app.application.datasets.policies import DatasetPolicyUseCases
from app.application.datasets.versions import DatasetVersionUseCases
from app.application.episodes.lineage import LineageService
from app.core.errors import ForbiddenError
from app.domain.identity.models import ProjectContext, ProjectRole, ProjectScope
from app.infrastructure.repositories.dataset_audit import (
    SqlDatasetAuditRepository,
    SqlDatasetIdempotencyRepository,
    SqlDatasetOutboxRepository,
)
from app.infrastructure.repositories.dataset_directory import SqlEpisodeCandidateDirectory
from app.infrastructure.repositories.dataset_policy_repositories import (
    SqlDatasetPolicyRepository,
    SqlDatasetReportRepository,
)
from app.infrastructure.repositories.dataset_repositories import (
    SqlDatasetItemRepository,
    SqlDatasetRepository,
    SqlDatasetVersionRepository,
)
from app.infrastructure.repositories.lineage import SqlLineageRepository

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]
ActionName = Annotated[str, Path(min_length=1, max_length=64)]


def get_owner_scope(
    context: Annotated[ProjectContext, Depends(get_project_context)],
) -> ProjectScope:
    """归档/恢复等高风险动作要求项目 Owner（或平台管理员）。"""
    if not context.is_platform_admin and context.role is not ProjectRole.OWNER:
        raise ForbiddenError("该动作要求项目负责人权限")
    return ProjectScope.from_context(context)


OwnerScope = Annotated[ProjectScope, Depends(get_owner_scope)]


@dataclass(frozen=True, slots=True)
class DatasetServices:
    datasets: DatasetCrudUseCases
    policies: DatasetPolicyUseCases
    versions: DatasetVersionUseCases
    lineage: LineageService


def get_dataset_use_cases(session: DbSession) -> DatasetServices:
    datasets_repo = SqlDatasetRepository(session)
    versions_repo = SqlDatasetVersionRepository(session)
    items_repo = SqlDatasetItemRepository(session)
    policies_repo = SqlDatasetPolicyRepository(session)
    reports_repo = SqlDatasetReportRepository(session)
    episodes = SqlEpisodeCandidateDirectory(session)
    audit = SqlDatasetAuditRepository(session)
    outbox = SqlDatasetOutboxRepository(session)
    idempotency = SqlDatasetIdempotencyRepository(session)
    lineage = DatasetVersionLineageRecorder(SqlLineageRepository(session))
    return DatasetServices(
        datasets=DatasetCrudUseCases(
            datasets_repo,
            audit,
            outbox,
            idempotency,
            session,
        ),
        policies=DatasetPolicyUseCases(
            datasets_repo,
            policies_repo,
            audit,
            outbox,
            idempotency,
            session,
        ),
        versions=DatasetVersionUseCases(
            datasets_repo,
            versions_repo,
            items_repo,
            policies_repo,
            reports_repo,
            episodes,
            audit,
            outbox,
            idempotency,
            session,
            lineage=lineage,
        ),
        lineage=LineageService(
            SqlLineageRepository(session),
            audit,
            outbox,
            session,
        ),
    )


DatasetUseCasesDep = Annotated[DatasetServices, Depends(get_dataset_use_cases)]


def request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None
