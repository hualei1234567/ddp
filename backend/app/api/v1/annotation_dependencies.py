"""标注域 API 用例装配。

所有用例共享请求级 Session；事务由应用层用例显式提交。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.application.annotation.ai_suggestions import AnnotationAiSuggestionUseCases
from app.application.annotation.arbitrations import AnnotationArbitrationUseCases
from app.application.annotation.assignments import AnnotationAssignmentUseCases
from app.application.annotation.batches import AnnotationBatchUseCases
from app.application.annotation.consistency import AnnotationConsistencyUseCases
from app.application.annotation.gold_sets import GoldSetUseCases
from app.application.annotation.iaa import IAAUseCases
from app.application.annotation.overdue_summary import AnnotationOverdueSummaryUseCases
from app.application.annotation.progress import AnnotationProgressRefresher
from app.application.annotation.quality_checks import QualityCheckUseCases
from app.application.annotation.quality_samples import QualitySampleUseCases
from app.application.annotation.reviews import AnnotationReviewUseCases
from app.application.annotation.sample_sets import AnnotationSampleSetUseCases
from app.application.annotation.schema_presets import SchemaPresetCatalog
from app.application.annotation.schemas import AnnotationSchemaUseCases
from app.application.annotation.submissions import AnnotationSubmissionUseCases
from app.application.annotation.tasks import AnnotationTaskUseCases
from app.infrastructure.repositories.annotation_ai_suggestions import (
    SqlAnnotationAiSuggestionRepository,
)
from app.infrastructure.repositories.annotation_assignments import (
    SqlAnnotationAssignmentRepository,
)
from app.infrastructure.repositories.annotation_audit import (
    SqlAnnotationAuditRepository,
    SqlAnnotationIdempotencyRepository,
    SqlAnnotationOutboxRepository,
)
from app.infrastructure.repositories.annotation_batches import SqlAnnotationBatchRepository
from app.infrastructure.repositories.annotation_directory import (
    SqlEpisodeDirectory,
    SqlGoldSetAnswerAccessPolicy,
    SqlMemberDirectory,
    SqlSkillProfileDirectory,
)
from app.infrastructure.repositories.annotation_quality import (
    SqlGoldSetRepository,
    SqlIAAReportRepository,
    SqlQualitySampleRepository,
)
from app.infrastructure.repositories.annotation_quality_checks import (
    SqlQualityCheckRepository,
)
from app.infrastructure.repositories.annotation_results import (
    SqlAnnotationArbitrationRepository,
    SqlAnnotationRecordRepository,
    SqlAnnotationReviewRepository,
    SqlAnnotationSubmissionRepository,
)
from app.infrastructure.repositories.annotation_sample_sets import (
    SqlAnnotationSampleSetRepository,
)
from app.infrastructure.repositories.annotation_schemas import SqlAnnotationSchemaRepository
from app.infrastructure.repositories.annotation_tasks import SqlAnnotationTaskRepository
from app.infrastructure.repositories.lineage import SqlLineageRepository


@dataclass(frozen=True, slots=True)
class AnnotationUseCases:
    schemas: AnnotationSchemaUseCases
    schema_presets: SchemaPresetCatalog
    sample_sets: AnnotationSampleSetUseCases
    tasks: AnnotationTaskUseCases
    batches: AnnotationBatchUseCases
    assignments: AnnotationAssignmentUseCases
    submissions: AnnotationSubmissionUseCases
    reviews: AnnotationReviewUseCases
    arbitrations: AnnotationArbitrationUseCases
    gold_sets: GoldSetUseCases
    quality_samples: QualitySampleUseCases
    quality_checks: QualityCheckUseCases
    iaa: IAAUseCases
    ai_suggestions: AnnotationAiSuggestionUseCases
    consistency: AnnotationConsistencyUseCases
    overdue_summary: AnnotationOverdueSummaryUseCases


def get_annotation_use_cases(session: DbSession) -> AnnotationUseCases:
    schemas_repo = SqlAnnotationSchemaRepository(session)
    sample_sets_repo = SqlAnnotationSampleSetRepository(session)
    tasks_repo = SqlAnnotationTaskRepository(session)
    batches_repo = SqlAnnotationBatchRepository(session)
    assignments_repo = SqlAnnotationAssignmentRepository(session)
    submissions_repo = SqlAnnotationSubmissionRepository(session)
    records_repo = SqlAnnotationRecordRepository(session)
    reviews_repo = SqlAnnotationReviewRepository(session)
    arbitrations_repo = SqlAnnotationArbitrationRepository(session)
    gold_sets_repo = SqlGoldSetRepository(session)
    quality_repo = SqlQualitySampleRepository(session)
    quality_check_repo = SqlQualityCheckRepository(session)
    iaa_repo = SqlIAAReportRepository(session)
    ai_suggestions_repo = SqlAnnotationAiSuggestionRepository(session)
    audit = SqlAnnotationAuditRepository(session)
    outbox = SqlAnnotationOutboxRepository(session)
    idempotency = SqlAnnotationIdempotencyRepository(session)
    episodes = SqlEpisodeDirectory(session)
    members = SqlMemberDirectory(session)
    # 标注通过/质量检查写血缘边：与业务写入共用请求级 Session，同事务提交。
    lineage = SqlLineageRepository(session)

    # 先构造质量检查用例：提交用例要在提交成功后立刻跑一次检查，因此需要
    # 持有它。两者不互相持有（检查用例不依赖提交用例），先建无顺序问题。
    quality_checks = QualityCheckUseCases(
        quality_check_repo,
        submissions_repo,
        records_repo,
        tasks_repo,
        schemas_repo,
        episodes,
        audit,
        outbox,
        session,
        lineage=lineage,
    )

    # 任务进度与批次计数只在这里接线一次：提交/复核/仲裁三个推进点共用
    # 同一个 refresher，避免各写一份统计口径。
    progress = AnnotationProgressRefresher(tasks_repo, batches_repo, submissions_repo)

    reviews_use_cases = AnnotationReviewUseCases(
        reviews_repo,
        submissions_repo,
        records_repo,
        tasks_repo,
        members,
        audit,
        outbox,
        idempotency,
        session,
        episodes=episodes,
        progress=progress,
        lineage=lineage,
    )
    arbitrations_use_cases = AnnotationArbitrationUseCases(
        arbitrations_repo,
        submissions_repo,
        records_repo,
        reviews_repo,
        members,
        audit,
        outbox,
        idempotency,
        session,
        episodes=episodes,
        progress=progress,
        lineage=lineage,
    )
    consistency_use_cases = AnnotationConsistencyUseCases(
        tasks_repo,
        records_repo,
        submissions_repo,
        reviews_repo,
        arbitrations_repo,
        members,
        arbitrations_use_cases,
        reviews_use_cases,
        session,
    )

    return AnnotationUseCases(
        schemas=AnnotationSchemaUseCases(
            schemas_repo,
            audit,
            outbox,
            idempotency,
            session,
        ),
        schema_presets=SchemaPresetCatalog(),
        sample_sets=AnnotationSampleSetUseCases(
            sample_sets_repo,
            tasks_repo,
            episodes,
            audit,
            outbox,
            idempotency,
            session,
        ),
        tasks=AnnotationTaskUseCases(
            tasks_repo,
            sample_sets_repo,
            schemas_repo,
            batches_repo,
            assignments_repo,
            submissions_repo,
            audit,
            outbox,
            idempotency,
            session,
        ),
        batches=AnnotationBatchUseCases(
            tasks_repo,
            batches_repo,
            assignments_repo,
            sample_sets_repo,
            audit,
            outbox,
            idempotency,
            session,
        ),
        assignments=AnnotationAssignmentUseCases(
            tasks_repo,
            batches_repo,
            assignments_repo,
            members,
            audit,
            outbox,
            idempotency,
            session,
            skill_profiles=SqlSkillProfileDirectory(session),
        ),
        submissions=AnnotationSubmissionUseCases(
            submissions_repo,
            records_repo,
            assignments_repo,
            batches_repo,
            tasks_repo,
            reviews_repo,
            arbitrations_repo,
            members,
            sample_sets_repo,
            schemas_repo,
            episodes,
            audit,
            outbox,
            idempotency,
            session,
            quality_checks,
            progress=progress,
        ),
        reviews=reviews_use_cases,
        arbitrations=arbitrations_use_cases,
        consistency=consistency_use_cases,
        overdue_summary=AnnotationOverdueSummaryUseCases(tasks_repo),
        gold_sets=GoldSetUseCases(
            gold_sets_repo,
            schemas_repo,
            submissions_repo,
            records_repo,
            tasks_repo,
            members,
            SqlGoldSetAnswerAccessPolicy(session),
            audit,
            outbox,
            idempotency,
            session,
        ),
        quality_samples=QualitySampleUseCases(
            quality_repo,
            submissions_repo,
            records_repo,
            tasks_repo,
            members,
            audit,
            outbox,
            idempotency,
            session,
        ),
        quality_checks=quality_checks,
        iaa=IAAUseCases(
            iaa_repo,
            records_repo,
            submissions_repo,
            tasks_repo,
            schemas_repo,
            audit,
            outbox,
            session,
            idempotency,
        ),
        ai_suggestions=AnnotationAiSuggestionUseCases(
            ai_suggestions_repo,
            submissions_repo,
            records_repo,
            reviews_repo,
            arbitrations_repo,
            members,
            audit,
            outbox,
            idempotency,
            session,
        ),
    )


AnnotationUseCasesDep = Annotated[AnnotationUseCases, Depends(get_annotation_use_cases)]
