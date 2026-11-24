"""数据集 API 依赖装配与权限。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Path

from app.api.deps import (
    DbSession,
    get_owner_scope,
    get_project_scope,
    get_write_scope,
)
from app.api.deps import (
    # 保留同名再导出：既有调用方仍从本模块导入 request_id。
    request_id as request_id,
)
from app.api.v1.preconditions import require_expected_version
from app.api.v1.upload_dependencies import ObjectStorageDep
from app.application.datasets.datasets import DatasetUseCases as DatasetCrudUseCases
from app.application.datasets.lineage import DatasetVersionLineageRecorder
from app.application.datasets.policies import DatasetPolicyUseCases
from app.application.datasets.quality_gate import EpisodeQualityGate
from app.application.datasets.versions import DatasetVersionUseCases
from app.application.episodes.lineage import LineageService
from app.application.episodes.provenance import SourceProvenanceResolver
from app.application.episodes.session_provenance import SessionProvenanceResolver
from app.domain.identity.models import ProjectScope
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
from app.infrastructure.repositories.datasource_provenance import (
    SqlSourceProvenanceRepository,
)
from app.infrastructure.repositories.lineage import SqlLineageRepository
from app.infrastructure.repositories.quality import SqlQualityRunRepository
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from app.infrastructure.repositories.session_provenance import (
    SqlSessionProvenanceRepository,
)

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]
ActionName = Annotated[str, Path(min_length=1, max_length=64)]


# 项目负责人作用域的权威实现在 ``app.api.deps``（审计日志读取与数据集
# 归档/授权共用同一判定），这里只保留既有导出名，避免改各路由的导入路径。
OwnerScope = Annotated[ProjectScope, Depends(get_owner_scope)]


@dataclass(frozen=True, slots=True)
class DatasetServices:
    datasets: DatasetCrudUseCases
    policies: DatasetPolicyUseCases
    versions: DatasetVersionUseCases
    lineage: LineageService


def get_dataset_use_cases(
    session: DbSession,
    storage: ObjectStorageDep,
) -> DatasetServices:
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
    quality_gate = EpisodeQualityGate(SqlQualityRunRepository(session))
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
            storage=storage,
            lineage=lineage,
            quality_gate=quality_gate,
        ),
        lineage=LineageService(
            SqlLineageRepository(session),
            audit,
            outbox,
            session,
            # 版本血缘只用到持久化边，但仍需装配投影协作方：少了它，任何
            # 走到 RAW_ASSET 的遍历都会拿不到数据源链。
            SourceProvenanceResolver(
                SqlSourceProvenanceRepository(session),
                SqlRawAssetRepository(session),
            ),
            SessionProvenanceResolver(SqlSessionProvenanceRepository(session)),
        ),
    )


DatasetUseCasesDep = Annotated[DatasetServices, Depends(get_dataset_use_cases)]
