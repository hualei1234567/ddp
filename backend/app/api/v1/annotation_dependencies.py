"""标注域 API 用例装配。

所有用例共享请求级 Session；事务由应用层用例显式提交。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.application.annotation.arbitrations import AnnotationArbitrationUseCases
from app.application.annotation.assignments import AnnotationAssignmentUseCases
from app.application.annotation.batches import AnnotationBatchUseCases
from app.application.annotation.gold_sets import GoldSetUseCases
from app.application.annotation.iaa import IAAUseCases
from app.application.annotation.quality_samples import QualitySampleUseCases
from app.application.annotation.reviews import AnnotationReviewUseCases
from app.application.annotation.sample_sets import AnnotationSampleSetUseCases
from app.application.annotation.schemas import AnnotationSchemaUseCases
from app.application.annotation.submissions import AnnotationSubmissionUseCases
from app.application.annotation.tasks import AnnotationTaskUseCases
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
)
from app.infrastructure.repositories.annotation_quality import (
    SqlGoldSetRepository,
    SqlIAAReportRepository,
    SqlQualitySampleRepository,
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


@dataclass(frozen=True, slots=True)
class AnnotationUseCases:
    schemas: AnnotationSchemaUseCases
    sample_sets: AnnotationSampleSetUseCases
    tasks: AnnotationTaskUseCases
    batches: AnnotationBatchUseCases
    assignments: AnnotationAssignmentUseCases
    submissions: AnnotationSubmissionUseCases
    reviews: AnnotationReviewUseCases
    arbitrations: AnnotationArbitrationUseCases
    gold_sets: GoldSetUseCases
    quality_samples: QualitySampleUseCases
    iaa: IAAUseCases


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
    iaa_repo = SqlIAAReportRepository(session)
    audit = SqlAnnotationAuditRepository(session)
    outbox = SqlAnnotationOutboxRepository(session)
    idempotency = SqlAnnotationIdempotencyRepository(session)
    episodes = SqlEpisodeDirectory(session)
    members = SqlMemberDirectory(session)

    return AnnotationUseCases(
        schemas=AnnotationSchemaUseCases(
            schemas_repo,
            audit,
            outbox,
            idempotency,
            session,
        ),
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
        ),
        reviews=AnnotationReviewUseCases(
            reviews_repo,
            submissions_repo,
            records_repo,
            tasks_repo,
            members,
            audit,
            outbox,
            idempotency,
            session,
        ),
        arbitrations=AnnotationArbitrationUseCases(
            arbitrations_repo,
            submissions_repo,
            records_repo,
            reviews_repo,
            members,
            audit,
            outbox,
            idempotency,
            session,
        ),
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
    )


AnnotationUseCasesDep = Annotated[AnnotationUseCases, Depends(get_annotation_use_cases)]
