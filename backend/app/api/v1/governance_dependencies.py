"""经营治理 API 依赖装配（N9 规模化运营）。

两类依赖：

1. :class:`GovernanceServices`：查询/配置配额的用例装配；
2. ``enforce_*_quota``：**写操作前置判定**。路由显式声明依赖后，判定在
   进入用例之前执行，超限直接返回契约错误码，业务写入不会发生。

权威规则不在本文件：操作 → 资源类型的映射在
``app/domain/governance/policy.py``，判定逻辑在
``app/application/governance/use_cases.py``。本文件只做装配与 HTTP 取参。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request

from app.api.deps import DbSession, get_write_scope
from app.api.v1.upload_schemas import UploadSessionCreate
from app.application.governance import GovernanceUseCases
from app.domain.governance.policy import (
    DATASET_VERSION_CREATE,
    EPISODE_CREATE,
    INFERENCE_TASK_CREATE,
    INGESTION_JOB_CREATE,
    OBJECT_STORAGE_UPLOAD,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.governance import (
    SqlIdempotencyReplayDetector,
    SqlResourceQuotaRepository,
    SqlUsageReader,
    SqlUsageSnapshotRepository,
)

WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
IdempotencyKeyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


@dataclass(frozen=True, slots=True)
class GovernanceServices:
    governance: GovernanceUseCases


def get_governance_services(session: DbSession) -> GovernanceServices:
    return GovernanceServices(
        governance=GovernanceUseCases(
            quotas=SqlResourceQuotaRepository(session),
            usage=SqlUsageReader(session),
            snapshots=SqlUsageSnapshotRepository(session),
            # 复用共享审计/outbox 适配器：规则本体在 ``app.application.audit``。
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
            replays=SqlIdempotencyReplayDetector(session),
            uow=session,
        )
    )


GovernanceServicesDep = Annotated[GovernanceServices, Depends(get_governance_services)]


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def _enforce(
    scope: ProjectScope,
    services: GovernanceServices,
    request: Request,
    operation: str,
    *,
    idempotency_key: str | None = None,
    quantity: int = 1,
) -> None:
    services.governance.enforce(
        scope,
        operation,
        quantity=quantity,
        request_id=_request_id(request),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
    )


def enforce_episode_create_quota(
    request: Request,
    scope: WriteScope,
    services: GovernanceServicesDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> None:
    _enforce(scope, services, request, EPISODE_CREATE, idempotency_key=idempotency_key)


def enforce_dataset_version_create_quota(
    request: Request,
    scope: WriteScope,
    services: GovernanceServicesDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> None:
    _enforce(scope, services, request, DATASET_VERSION_CREATE, idempotency_key=idempotency_key)


def enforce_ingestion_job_create_quota(
    request: Request,
    scope: WriteScope,
    services: GovernanceServicesDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> None:
    _enforce(scope, services, request, INGESTION_JOB_CREATE, idempotency_key=idempotency_key)


def enforce_inference_task_create_quota(
    request: Request,
    scope: WriteScope,
    services: GovernanceServicesDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> None:
    _enforce(scope, services, request, INFERENCE_TASK_CREATE, idempotency_key=idempotency_key)


def enforce_object_storage_upload_quota(
    payload: UploadSessionCreate,
    request: Request,
    scope: WriteScope,
    services: GovernanceServicesDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> None:
    """按本次上传的**声明字节数**预扣对象存储配额。

    判定发生在创建上传会话之前：超限时客户端不会拿到预签名地址、也不会
    开始传输大文件。``declared_size_bytes`` 由 Pydantic 保证 ``>0``。
    """
    _enforce(
        scope,
        services,
        request,
        OBJECT_STORAGE_UPLOAD,
        quantity=payload.declared_size_bytes,
        idempotency_key=idempotency_key,
    )


EnforceEpisodeCreateQuota = Annotated[None, Depends(enforce_episode_create_quota)]
EnforceDatasetVersionCreateQuota = Annotated[None, Depends(enforce_dataset_version_create_quota)]
EnforceIngestionJobCreateQuota = Annotated[None, Depends(enforce_ingestion_job_create_quota)]
EnforceInferenceTaskCreateQuota = Annotated[None, Depends(enforce_inference_task_create_quota)]
EnforceObjectStorageUploadQuota = Annotated[None, Depends(enforce_object_storage_upload_quota)]

__all__ = [
    "EnforceDatasetVersionCreateQuota",
    "EnforceEpisodeCreateQuota",
    "EnforceInferenceTaskCreateQuota",
    "EnforceIngestionJobCreateQuota",
    "EnforceObjectStorageUploadQuota",
    "GovernanceServices",
    "GovernanceServicesDep",
    "enforce_dataset_version_create_quota",
    "enforce_episode_create_quota",
    "enforce_inference_task_create_quota",
    "enforce_ingestion_job_create_quota",
    "enforce_object_storage_upload_quota",
    "get_governance_services",
]
