"""训练需求/交付/外部引用/授权 API 依赖装配与跨项目授权。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Path
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentPrincipal,
    DbSession,
    get_project_access_service,
)
from app.api.v1.dataset_dependencies import (
    ExpectedVersion,
    IdempotencyKey,
    OwnerScope,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.export_schemas import ExportJobCreate
from app.api.v1.upload_dependencies import ObjectStorageDep, SettingsDep
from app.application.datasets.dataset_grants import (
    DatasetGrantAccessService,
    DatasetGrantUseCases,
    GrantDecision,
)
from app.application.datasets.lineage import DatasetVersionLineageRecorder
from app.application.datasets.training_deliveries import TrainingDeliveryUseCases
from app.application.datasets.training_evidence import TrainingEvidenceUseCases
from app.application.datasets.training_ports import TrainingAdapterGateway
from app.application.datasets.training_references import (
    AdapterCallbackUseCases,
    ExternalTrainingReferenceUseCases,
)
from app.application.datasets.training_requests import TrainingRequestUseCases
from app.application.identity.access import ProjectAccessService
from app.core.errors import ForbiddenError
from app.domain.datasets.errors import DatasetAccessDeniedError
from app.domain.datasets.requirement_match import MatchPolicy
from app.domain.datasets.training_enums import GrantScope
from app.domain.identity.models import Principal, ProjectAction, ProjectScope
from app.infrastructure.repositories.dataset_audit import (
    SqlDatasetAuditRepository,
    SqlDatasetIdempotencyRepository,
    SqlDatasetOutboxRepository,
)
from app.infrastructure.repositories.dataset_repositories import (
    SqlDatasetRepository,
    SqlDatasetVersionRepository,
)
from app.infrastructure.repositories.export_jobs import SqlExportJobRepository
from app.infrastructure.repositories.identity import SqlProjectRepository
from app.infrastructure.repositories.lineage import SqlLineageRepository
from app.infrastructure.repositories.training_delivery_repositories import (
    SqlExternalReferenceRepository,
    SqlTrainingCallbackRepository,
    SqlTrainingDeliveryRepository,
)
from app.infrastructure.repositories.training_evidence import (
    SqlTrainingEvidenceRepository,
)
from app.infrastructure.repositories.training_grant_repositories import (
    SqlDatasetGrantRepository,
)
from app.infrastructure.repositories.training_request_repositories import (
    SqlTrainingRequestRepository,
    SqlTrainingRequestRevisionRepository,
)
from app.infrastructure.repositories.version_fact_directory import (
    SqlVersionFactDirectory,
)
from app.infrastructure.training_adapter_http import HttpTrainingAdapterGateway


def get_training_adapter_gateway(settings: SettingsDep) -> TrainingAdapterGateway:
    # 登记了 endpoint 的 adapter 走真实 HTTP；未登记的仍抛 503（行为不变）。
    return HttpTrainingAdapterGateway(settings)


TrainingAdapterDep = Annotated[TrainingAdapterGateway, Depends(get_training_adapter_gateway)]


@dataclass(frozen=True, slots=True)
class TrainingServices:
    requests: TrainingRequestUseCases
    deliveries: TrainingDeliveryUseCases
    references: ExternalTrainingReferenceUseCases
    evidence: TrainingEvidenceUseCases
    callbacks: AdapterCallbackUseCases
    grants: DatasetGrantUseCases
    grant_access: DatasetGrantAccessService


def get_training_services(
    session: DbSession,
    settings: SettingsDep,
    storage: ObjectStorageDep,
    gateway: TrainingAdapterDep,
) -> TrainingServices:
    requests_repo = SqlTrainingRequestRepository(session)
    revisions_repo = SqlTrainingRequestRevisionRepository(session)
    deliveries_repo = SqlTrainingDeliveryRepository(session)
    references_repo = SqlExternalReferenceRepository(session)
    evidence_repo = SqlTrainingEvidenceRepository(session)
    callbacks_repo = SqlTrainingCallbackRepository(session)
    grants_repo = SqlDatasetGrantRepository(session)
    datasets_repo = SqlDatasetRepository(session)
    versions_repo = SqlDatasetVersionRepository(session)
    exports_repo = SqlExportJobRepository(session)
    audit = SqlDatasetAuditRepository(session)
    outbox = SqlDatasetOutboxRepository(session)
    idempotency = SqlDatasetIdempotencyRepository(session)
    lineage = DatasetVersionLineageRecorder(SqlLineageRepository(session))
    return TrainingServices(
        requests=TrainingRequestUseCases(
            requests_repo,
            revisions_repo,
            audit,
            outbox,
            idempotency,
            session,
        ),
        deliveries=TrainingDeliveryUseCases(
            requests=requests_repo,
            revisions=revisions_repo,
            deliveries=deliveries_repo,
            datasets=datasets_repo,
            versions=versions_repo,
            exports=exports_repo,
            storage=storage,
            audit=audit,
            outbox=outbox,
            idempotency=idempotency,
            uow=session,
            chunk_size_bytes=settings.export_artifact_chunk_size_bytes,
            fact_directory=SqlVersionFactDirectory(session),
            match_policy=MatchPolicy(
                on_failed=settings.training_delivery_match_on_failed,
                on_unverifiable=settings.training_delivery_match_on_unverifiable,
                split_ratio_tolerance=(settings.training_delivery_match_split_ratio_tolerance),
            ),
        ),
        references=ExternalTrainingReferenceUseCases(
            deliveries=deliveries_repo,
            references=references_repo,
            gateway=gateway,
            audit=audit,
            outbox=outbox,
            idempotency=idempotency,
            uow=session,
            lineage=lineage,
        ),
        evidence=TrainingEvidenceUseCases(
            deliveries=deliveries_repo,
            references=references_repo,
            versions=versions_repo,
            evidence=evidence_repo,
            uow=session,
        ),
        callbacks=AdapterCallbackUseCases(
            references=references_repo,
            callbacks=callbacks_repo,
            outbox=outbox,
            uow=session,
            nonce_ttl_seconds=settings.training_callback_nonce_ttl_seconds,
        ),
        grants=DatasetGrantUseCases(
            grants=grants_repo,
            datasets=datasets_repo,
            versions=versions_repo,
            projects_exist=SqlProjectRepository(session).exists,
            audit=audit,
            outbox=outbox,
            idempotency=idempotency,
            uow=session,
        ),
        grant_access=DatasetGrantAccessService(grants_repo),
    )


TrainingServicesDep = Annotated[TrainingServices, Depends(get_training_services)]

RequiredIdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]
ActionName = Annotated[str, Path(min_length=1, max_length=64)]


def _member_or_grant_scope(
    *,
    principal: Principal,
    project_id: UUID,
    dataset_id: UUID,
    version_id: UUID | None,
    required: GrantScope,
    session: Session,
    access: ProjectAccessService,
) -> ProjectScope:
    original: ForbiddenError | None = None
    try:
        context = access.resolve_context(principal, project_id)
    except ForbiddenError as exc:
        original = exc
    else:
        if not context.can(ProjectAction.READ):
            raise ForbiddenError("当前角色无权读取该项目")
        return ProjectScope.from_context(context)
    service = DatasetGrantAccessService(SqlDatasetGrantRepository(session))
    decision = service.evaluate(
        project_id=project_id,
        dataset_id=dataset_id,
        version_id=version_id,
        user_id=principal.user_id,
        required=required,
    )
    if decision is GrantDecision.GRANTED:
        return ProjectScope(project_id=project_id, actor_id=principal.user_id)
    if decision is GrantDecision.SCOPE_MISMATCH:
        # 该接收方对此数据集确有显式 Grant，只是不含本次所需动作。
        # 存在性对接收方已知，因此用可按 code 分支的 403，而不是伪装成 404。
        raise DatasetAccessDeniedError(
            f"数据集授权不包含所需动作 {required.value}",
            details={
                "dataset_id": str(dataset_id),
                "required_scope": required.value,
            },
        )
    if original is not None:
        raise original
    raise ForbiddenError("无权访问该项目")


def get_dataset_read_scope(
    project_id: UUID,
    dataset_id: UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    access: Annotated[ProjectAccessService, Depends(get_project_access_service)],
) -> ProjectScope:
    return _member_or_grant_scope(
        principal=principal,
        project_id=project_id,
        dataset_id=dataset_id,
        version_id=None,
        required=GrantScope.VIEW,
        session=session,
        access=access,
    )


DatasetReadScope = Annotated[ProjectScope, Depends(get_dataset_read_scope)]


def get_dataset_version_read_scope(
    project_id: UUID,
    dataset_id: UUID,
    version_id: UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    access: Annotated[ProjectAccessService, Depends(get_project_access_service)],
) -> ProjectScope:
    return _member_or_grant_scope(
        principal=principal,
        project_id=project_id,
        dataset_id=dataset_id,
        version_id=version_id,
        required=GrantScope.VIEW,
        session=session,
        access=access,
    )


DatasetVersionReadScope = Annotated[ProjectScope, Depends(get_dataset_version_read_scope)]


def get_export_create_scope(
    project_id: UUID,
    payload: ExportJobCreate,
    principal: CurrentPrincipal,
    session: DbSession,
    access: Annotated[ProjectAccessService, Depends(get_project_access_service)],
) -> ProjectScope:
    """项目成员按 WRITE；跨项目用户必须持有有效 EXPORT 授权。"""
    try:
        context = access.resolve_context(principal, project_id)
    except ForbiddenError as original:
        scope = ProjectScope(project_id=project_id, actor_id=principal.user_id)
        version = SqlDatasetVersionRepository(session).get(scope, payload.dataset_version_id)
        if version is None:
            raise original from None
        return _member_or_grant_scope(
            principal=principal,
            project_id=project_id,
            dataset_id=version.dataset_id,
            version_id=version.dataset_version_id,
            required=GrantScope.EXPORT,
            session=session,
            access=access,
        )
    if not context.can(ProjectAction.WRITE):
        raise ForbiddenError("当前角色无权创建导出任务")
    return ProjectScope.from_context(context)


ExportGrantScope = Annotated[ProjectScope, Depends(get_export_create_scope)]


def get_export_download_scope(
    project_id: UUID,
    export_job_id: UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    access: Annotated[ProjectAccessService, Depends(get_project_access_service)],
) -> ProjectScope:
    """项目成员按 WRITE；跨项目用户必须持有有效 DOWNLOAD 授权。"""
    try:
        context = access.resolve_context(principal, project_id)
    except ForbiddenError as original:
        scope = ProjectScope(project_id=project_id, actor_id=principal.user_id)
        job = SqlExportJobRepository(session).get(scope, export_job_id)
        if job is None:
            raise original from None
        version = SqlDatasetVersionRepository(session).get(scope, job.dataset_version_id)
        if version is None:
            raise original from None
        return _member_or_grant_scope(
            principal=principal,
            project_id=project_id,
            dataset_id=version.dataset_id,
            version_id=version.dataset_version_id,
            required=GrantScope.DOWNLOAD,
            session=session,
            access=access,
        )
    if not context.can(ProjectAction.READ):
        raise ForbiddenError("当前角色无权下载导出产物")
    return ProjectScope.from_context(context)


ExportDownloadScope = Annotated[ProjectScope, Depends(get_export_download_scope)]

__all__ = [
    "ActionName",
    "DatasetReadScope",
    "DatasetVersionReadScope",
    "ExpectedVersion",
    "ExportDownloadScope",
    "ExportGrantScope",
    "IdempotencyKey",
    "OwnerScope",
    "ReadScope",
    "RequiredIdempotencyKey",
    "TrainingAdapterDep",
    "TrainingServices",
    "TrainingServicesDep",
    "WriteScope",
    "request_id",
]
