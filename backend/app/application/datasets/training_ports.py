"""训练需求/交付/外部引用/授权应用层端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.datasets.dataset_grants import DatasetGrant
from app.domain.datasets.training_deliveries import TrainingDelivery
from app.domain.datasets.training_enums import ExecutionMode, ExternalTrainingStatus
from app.domain.datasets.training_references import ExternalTrainingReference
from app.domain.datasets.training_requests import TrainingRequest, TrainingRequestRevision
from app.domain.identity.models import ProjectScope


class TrainingRequestRepository(Protocol):
    def add(self, scope: ProjectScope, request: TrainingRequest) -> TrainingRequest: ...

    def get(self, scope: ProjectScope, request_id: UUID) -> TrainingRequest | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> TrainingRequest | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        priority: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[TrainingRequest]: ...

    def save(
        self,
        scope: ProjectScope,
        request: TrainingRequest,
        *,
        expected_version: int,
    ) -> TrainingRequest: ...


class TrainingRequestRevisionRepository(Protocol):
    def add(
        self, scope: ProjectScope, revision: TrainingRequestRevision
    ) -> TrainingRequestRevision: ...

    def get(self, scope: ProjectScope, revision_id: UUID) -> TrainingRequestRevision | None: ...

    def get_for_request(
        self,
        scope: ProjectScope,
        *,
        request_id: UUID,
        revision_id: UUID,
    ) -> TrainingRequestRevision | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        request_id: UUID,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[TrainingRequestRevision]: ...

    def save(
        self,
        scope: ProjectScope,
        revision: TrainingRequestRevision,
        *,
        expected_version: int,
    ) -> TrainingRequestRevision: ...


class TrainingDeliveryRepository(Protocol):
    def add(self, scope: ProjectScope, delivery: TrainingDelivery) -> TrainingDelivery: ...

    def get(self, scope: ProjectScope, delivery_id: UUID) -> TrainingDelivery | None: ...

    def get_by_note_no(self, scope: ProjectScope, note_no: str) -> TrainingDelivery | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        request_id: UUID | None = None,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[TrainingDelivery]: ...

    def save(
        self,
        scope: ProjectScope,
        delivery: TrainingDelivery,
        *,
        expected_version: int,
    ) -> TrainingDelivery: ...


class ExternalReferenceRepository(Protocol):
    def add(
        self,
        scope: ProjectScope,
        reference: ExternalTrainingReference,
    ) -> ExternalTrainingReference: ...

    def get(self, scope: ProjectScope, reference_id: UUID) -> ExternalTrainingReference | None: ...

    def list_for_delivery(
        self,
        scope: ProjectScope,
        *,
        delivery_id: UUID,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ExternalTrainingReference]: ...

    def get_by_idempotency_key(
        self,
        scope: ProjectScope,
        *,
        adapter_id: str,
        idempotency_key: str,
    ) -> ExternalTrainingReference | None: ...

    def get_by_run(
        self,
        *,
        adapter_id: str,
        external_run_id: str,
    ) -> ExternalTrainingReference | None: ...

    def save(
        self,
        scope: ProjectScope,
        reference: ExternalTrainingReference,
        *,
        expected_version: int,
    ) -> ExternalTrainingReference: ...

    def latest_execution_modes(
        self,
        scope: ProjectScope,
        *,
        delivery_ids: tuple[UUID, ...],
    ) -> dict[UUID, ExecutionMode]:
        """每个交付**最近一次**外部训练引用的执行方式。

        没有引用的交付不出现在返回值里，由调用方显式填 ``UNKNOWN``——"尚未提交
        训练"不能用 REMOTE 顶替。
        """
        ...


class GrantRepository(Protocol):
    def add(self, scope: ProjectScope, grant: DatasetGrant) -> DatasetGrant: ...

    def get(self, scope: ProjectScope, grant_id: UUID) -> DatasetGrant | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        status: str | None = None,
        grantee_project_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DatasetGrant]: ...

    def find_for_user(
        self,
        *,
        project_id: UUID,
        dataset_id: UUID,
        user_id: UUID,
    ) -> list[DatasetGrant]: ...

    def save(
        self,
        scope: ProjectScope,
        grant: DatasetGrant,
        *,
        expected_version: int,
    ) -> DatasetGrant: ...


@dataclass(frozen=True, slots=True)
class CallbackRecord:
    callback_id: UUID
    adapter_id: str
    event_id: str
    nonce: str
    payload_sha256: str
    received_at: datetime


class CallbackRepository(Protocol):
    def get_by_event(self, adapter_id: str, event_id: str) -> CallbackRecord | None: ...

    def get_by_nonce(self, adapter_id: str, nonce: str) -> CallbackRecord | None: ...

    def add(self, record: CallbackRecord) -> CallbackRecord: ...

    def prune_before(self, *, received_before: datetime) -> int: ...


@dataclass(frozen=True, slots=True)
class AdapterRunRef:
    external_run_id: str
    external_run_url: str | None = None
    model_ref: dict[str, object] | None = None
    evaluation_ref: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class AdapterRunStatus:
    status: ExternalTrainingStatus
    status_stale: bool = False
    external_run_url: str | None = None
    model_ref: dict[str, object] | None = None
    evaluation_ref: dict[str, object] | None = None
    error_code: str | None = None
    error_summary: str | None = None


class TrainingAdapterGateway(Protocol):
    """Adapter Port；不支持的能力必须抛 ``TrainingAdapterUnsupportedError``。"""

    def capabilities(self, adapter_id: str) -> frozenset[str]: ...

    def execution_mode(self, adapter_id: str) -> ExecutionMode | None:
        """部署为该 Adapter 声明的执行方式；未声明返回 ``None``。

        返回 ``None`` 不是"默认 REMOTE"：调用方必须要求请求显式声明，
        否则拒绝提交。
        """
        ...

    def submit(
        self,
        *,
        adapter_id: str,
        adapter_version: str,
        correlation_id: str,
        delivery_id: UUID,
        dataset_version_id: UUID,
        config: dict[str, object],
    ) -> AdapterRunRef: ...

    def get_status(
        self,
        *,
        adapter_id: str,
        external_run_id: str,
        correlation_id: str,
    ) -> AdapterRunStatus: ...

    def cancel(
        self,
        *,
        adapter_id: str,
        external_run_id: str,
        correlation_id: str,
    ) -> AdapterRunStatus: ...


__all__ = [
    "AdapterRunRef",
    "AdapterRunStatus",
    "CallbackRecord",
    "CallbackRepository",
    "ExternalReferenceRepository",
    "GrantRepository",
    "TrainingAdapterGateway",
    "TrainingDeliveryRepository",
    "TrainingRequestRepository",
    "TrainingRequestRevisionRepository",
]
