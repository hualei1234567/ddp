"""质量运行 API 依赖装配。

复用既有域仓储：质量引擎不新建检查对象读取实现，也不直接读他人的表。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession, get_project_scope, get_write_scope
from app.application.quality.diff import QualityRunDiffUseCases
from app.application.quality.registry import default_registry
from app.application.quality.use_cases import QualityRunUseCases
from app.domain.identity.models import ProjectScope
from app.infrastructure.repositories.annotation_results import (
    SqlAnnotationRecordRepository,
)
from app.infrastructure.repositories.annotation_schemas import (
    SqlAnnotationSchemaRepository,
)
from app.infrastructure.repositories.annotation_tasks import SqlAnnotationTaskRepository
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.dataset_policy_repositories import (
    SqlDatasetReportRepository,
)
from app.infrastructure.repositories.datasources import (
    SqlDataSourceImportRepository,
    SqlMediaAssetRepository,
)
from app.infrastructure.repositories.episodes import (
    SqlEpisodeRawAssetRepository,
    SqlEpisodeRepository,
)
from app.infrastructure.repositories.quality import SqlQualityRunRepository

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]


@dataclass(frozen=True, slots=True)
class QualityServices:
    runs: QualityRunUseCases
    diff: QualityRunDiffUseCases


def get_quality_services(session: DbSession) -> QualityServices:
    registry = default_registry(SqlDatasetReportRepository(session))
    runs = QualityRunUseCases(
        runs=SqlQualityRunRepository(session),
        imports=SqlDataSourceImportRepository(session),
        assets=SqlMediaAssetRepository(session),
        episodes=SqlEpisodeRepository(session),
        episode_assets=SqlEpisodeRawAssetRepository(session),
        annotation_records=SqlAnnotationRecordRepository(session),
        schemas=SqlAnnotationSchemaRepository(session),
        tasks=SqlAnnotationTaskRepository(session),
        registry=registry,
        # 运行结论与审计/outbox 同事务写入；缺任一依赖会让"检查过"无法追溯。
        audit=SqlAuditRepository(session),
        outbox=SqlOutboxRepository(session),
        uow=session,
    )
    return QualityServices(
        runs=runs,
        diff=QualityRunDiffUseCases(runs=SqlQualityRunRepository(session)),
    )


QualityServicesDep = Annotated[QualityServices, Depends(get_quality_services)]


__all__ = ["QualityServices", "QualityServicesDep", "ReadScope", "WriteScope"]
