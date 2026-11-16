"""阶段 5 API 依赖装配与权限。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.api.v1.dataset_dependencies import (
    ExpectedVersion,
    IdempotencyKey,
    OwnerScope,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.upload_dependencies import SettingsDep
from app.application.modelops.callbacks import ModelCallbackUseCases
from app.application.modelops.evaluations import EvaluationUseCases
from app.application.modelops.feedback import FeedbackUseCases
from app.application.modelops.inference import (
    InferenceServiceUseCases,
    InferenceTaskUseCases,
)
from app.application.modelops.model_registry import (
    ModelUseCases,
    ModelVersionUseCases,
)
from app.application.modelops.requirements import RequirementUseCases
from app.core.config import Settings
from app.infrastructure.model_platform import (
    ConfiguredModelPlatformGateway,
    S3ArtifactInspector,
)
from app.infrastructure.repositories.episode_coverage import SqlEpisodeCoverageDirectory
from app.infrastructure.repositories.modelops import (
    SqlDatasetGate,
    SqlEvaluationRepository,
    SqlFeedbackRepository,
    SqlInferenceServiceRepository,
    SqlInferenceTaskRepository,
    SqlModelAuditRepository,
    SqlModelCallbackRepository,
    SqlModelIdempotencyRepository,
    SqlModelOutboxRepository,
    SqlModelRepository,
    SqlModelVersionRepository,
    SqlRequirementRepository,
)
from app.infrastructure.repositories.modelops_training_source import SqlTrainingSourceGate
from app.infrastructure.storage.s3 import S3MultipartStorage


@dataclass(frozen=True, slots=True)
class ModelOpsServices:
    models: ModelUseCases
    versions: ModelVersionUseCases
    evaluations: EvaluationUseCases
    services: InferenceServiceUseCases
    tasks: InferenceTaskUseCases
    feedback: FeedbackUseCases
    requirements: RequirementUseCases
    callbacks: ModelCallbackUseCases


def get_model_gateway(settings: SettingsDep):
    return ConfiguredModelPlatformGateway(settings)


def get_artifact_inspector(settings: SettingsDep):
    return S3ArtifactInspector(S3MultipartStorage(settings))


def build_modelops_services(
    session,
    settings: Settings,
    gateway=None,
    artifacts=None,
) -> ModelOpsServices:
    models_repo = SqlModelRepository(session)
    versions_repo = SqlModelVersionRepository(session)
    evaluations_repo = SqlEvaluationRepository(session)
    services_repo = SqlInferenceServiceRepository(session)
    tasks_repo = SqlInferenceTaskRepository(session)
    feedback_repo = SqlFeedbackRepository(session)
    requirements_repo = SqlRequirementRepository(session)
    audit = SqlModelAuditRepository(session)
    outbox = SqlModelOutboxRepository(session)
    idempotency = SqlModelIdempotencyRepository(session)
    datasets = SqlDatasetGate(session)
    training_sources = SqlTrainingSourceGate(session)
    gateway = gateway or ConfiguredModelPlatformGateway(settings)
    artifacts = artifacts or S3ArtifactInspector(S3MultipartStorage(settings))
    models = ModelUseCases(
        models=models_repo,
        versions=versions_repo,
        audit=audit,
        outbox=outbox,
        idempotency=idempotency,
        uow=session,
    )
    versions = ModelVersionUseCases(
        models=models_repo,
        versions=versions_repo,
        services=services_repo,
        datasets=datasets,
        artifacts=artifacts,
        training_sources=training_sources,
        audit=audit,
        outbox=outbox,
        idempotency=idempotency,
        uow=session,
    )
    evaluations = EvaluationUseCases(
        evaluations=evaluations_repo,
        models=models_repo,
        versions=versions_repo,
        datasets=datasets,
        gateway=gateway,
        audit=audit,
        outbox=outbox,
        idempotency=idempotency,
        uow=session,
    )
    services = InferenceServiceUseCases(
        services=services_repo,
        versions=versions_repo,
        audit=audit,
        outbox=outbox,
        idempotency=idempotency,
        uow=session,
    )
    tasks = InferenceTaskUseCases(
        tasks=tasks_repo,
        services=services_repo,
        versions=versions_repo,
        datasets=datasets,
        gateway=gateway,
        audit=audit,
        outbox=outbox,
        idempotency=idempotency,
        uow=session,
    )
    feedback = FeedbackUseCases(
        feedback=feedback_repo,
        requirements=requirements_repo,
        versions=versions_repo,
        evaluations=evaluations_repo,
        tasks=tasks_repo,
        audit=audit,
        outbox=outbox,
        idempotency=idempotency,
        uow=session,
        coverage=SqlEpisodeCoverageDirectory(session),
        coverage_stale_after=timedelta(seconds=settings.model_gap_freshness_stale_after_seconds),
    )
    requirements = RequirementUseCases(
        requirements=requirements_repo,
        evaluations=evaluations_repo,
        datasets=datasets,
        audit=audit,
        outbox=outbox,
        idempotency=idempotency,
        uow=session,
    )
    callbacks = ModelCallbackUseCases(
        callbacks=SqlModelCallbackRepository(session),
        evaluations=evaluations,
        tasks=tasks,
        uow=session,
        nonce_ttl_seconds=settings.model_callback_nonce_ttl_seconds,
    )
    return ModelOpsServices(
        models=models,
        versions=versions,
        evaluations=evaluations,
        services=services,
        tasks=tasks,
        feedback=feedback,
        requirements=requirements,
        callbacks=callbacks,
    )


GatewayDep = Annotated[object, Depends(get_model_gateway)]
ArtifactsDep = Annotated[object, Depends(get_artifact_inspector)]


def get_modelops_services(
    session: DbSession,
    settings: SettingsDep,
    gateway: GatewayDep,
    artifacts: ArtifactsDep,
) -> ModelOpsServices:
    return build_modelops_services(session, settings, gateway, artifacts)


ModelOpsServicesDep = Annotated[ModelOpsServices, Depends(get_modelops_services)]

__all__ = [
    "get_artifact_inspector",
    "get_model_gateway",
    "ExpectedVersion",
    "IdempotencyKey",
    "ModelOpsServices",
    "ModelOpsServicesDep",
    "OwnerScope",
    "ReadScope",
    "WriteScope",
    "build_modelops_services",
    "request_id",
]
