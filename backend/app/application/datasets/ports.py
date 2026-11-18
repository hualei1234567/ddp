"""数据集应用层端口与 DTO。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.application.audit import OutboxEvent
from app.application.idempotency import (
    IdempotencyRecord as _IdempotencyRecord,
)
from app.application.idempotency import (
    IdempotencyRepository as _IdempotencyRepository,
)
from app.domain.datasets.leakage import LeakageReport
from app.domain.datasets.models import Dataset, DatasetItem, DatasetVersion
from app.domain.datasets.policies import (
    DedupPolicy,
    LeakagePolicy,
    QualityPolicy,
    SplitPolicy,
)
from app.domain.datasets.quality import QualityReport
from app.domain.datasets.selection import EpisodeCandidate, SelectionFilters
from app.domain.identity.models import ProjectScope


class UnitOfWork(Protocol):
    def commit(self) -> None: ...


class DatasetRepository(Protocol):
    def add(self, scope: ProjectScope, dataset: Dataset) -> Dataset: ...

    def get(self, scope: ProjectScope, dataset_id: UUID) -> Dataset | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> Dataset | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        purpose: str | None = None,
        usage_policy: str | None = None,
        tags: tuple[str, ...] = (),
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Dataset]: ...

    def save(
        self,
        scope: ProjectScope,
        dataset: Dataset,
        *,
        expected_version: int,
    ) -> Dataset: ...


class DatasetVersionRepository(Protocol):
    def add(self, scope: ProjectScope, version: DatasetVersion) -> DatasetVersion: ...

    def get(self, scope: ProjectScope, version_id: UUID) -> DatasetVersion | None: ...

    def get_for_dataset(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        version_id: UUID,
    ) -> DatasetVersion | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        status: str | None = None,
        validity_status: str | None = None,
        leakage_status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DatasetVersion]: ...

    def save(
        self,
        scope: ProjectScope,
        version: DatasetVersion,
        *,
        expected_version: int,
    ) -> DatasetVersion: ...

    def next_version_number(self, scope: ProjectScope, dataset_id: UUID) -> int: ...

    def latest_frozen(self, scope: ProjectScope, dataset_id: UUID) -> DatasetVersion | None: ...

    def list_validity_candidates(
        self,
        *,
        after_id: UUID | None = None,
        limit: int = 200,
    ) -> list[tuple[UUID, UUID]]:
        """跨项目列出待做有效性巡检的版本 ``(version_id, project_id)``。

        只返回 ID；调用方必须用 ``ProjectScope`` 重新读取，项目隔离仍由仓储强制。
        """
        ...


class DatasetItemRepository(Protocol):
    def add_many(
        self,
        scope: ProjectScope,
        items: tuple[DatasetItem, ...],
    ) -> tuple[DatasetItem, ...]: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        dataset_version_id: UUID,
        split: str | None = None,
        sample_role: str | None = None,
        episode_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DatasetItem]: ...

    def get(
        self,
        scope: ProjectScope,
        *,
        dataset_version_id: UUID,
        item_id: UUID,
    ) -> DatasetItem | None: ...

    def list_all(
        self,
        scope: ProjectScope,
        dataset_version_id: UUID,
    ) -> list[DatasetItem]: ...


class DatasetPolicyRepository(Protocol):
    def add_dedup(self, scope: ProjectScope, policy: DedupPolicy) -> DedupPolicy: ...
    def get_dedup(self, scope: ProjectScope, policy_id: UUID) -> DedupPolicy | None: ...
    def get_dedup_version(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        policy_id: UUID,
        version: int,
    ) -> DedupPolicy | None: ...
    def list_dedup(
        self, scope: ProjectScope, *, dataset_id: UUID, limit: int = 20
    ) -> list[DedupPolicy]: ...
    def next_dedup_version(self, scope: ProjectScope, dataset_id: UUID) -> int: ...
    def add_split(self, scope: ProjectScope, policy: SplitPolicy) -> SplitPolicy: ...
    def get_split(self, scope: ProjectScope, policy_id: UUID) -> SplitPolicy | None: ...
    def get_split_version(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        policy_id: UUID,
        version: int,
    ) -> SplitPolicy | None: ...
    def list_split(
        self, scope: ProjectScope, *, dataset_id: UUID, limit: int = 20
    ) -> list[SplitPolicy]: ...
    def next_split_version(self, scope: ProjectScope, dataset_id: UUID) -> int: ...
    def add_leakage(self, scope: ProjectScope, policy: LeakagePolicy) -> LeakagePolicy: ...
    def get_leakage(self, scope: ProjectScope, policy_id: UUID) -> LeakagePolicy | None: ...
    def get_leakage_version(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        policy_id: UUID,
        version: int,
    ) -> LeakagePolicy | None: ...
    def list_leakage(
        self, scope: ProjectScope, *, dataset_id: UUID, limit: int = 20
    ) -> list[LeakagePolicy]: ...
    def next_leakage_version(self, scope: ProjectScope, dataset_id: UUID) -> int: ...
    def add_quality(self, scope: ProjectScope, policy: QualityPolicy) -> QualityPolicy: ...
    def get_quality(self, scope: ProjectScope, policy_id: UUID) -> QualityPolicy | None: ...
    def get_quality_version(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        policy_id: UUID,
        version: int,
    ) -> QualityPolicy | None: ...
    def list_quality(
        self, scope: ProjectScope, *, dataset_id: UUID, limit: int = 20
    ) -> list[QualityPolicy]: ...
    def next_quality_version(self, scope: ProjectScope, dataset_id: UUID) -> int: ...


class DatasetReportRepository(Protocol):
    def add_leakage(self, scope: ProjectScope, report: LeakageReport) -> LeakageReport: ...
    def latest_leakage(
        self,
        scope: ProjectScope,
        dataset_version_id: UUID,
    ) -> LeakageReport | None: ...
    def add_quality(self, scope: ProjectScope, report: QualityReport) -> QualityReport: ...
    def latest_quality(
        self,
        scope: ProjectScope,
        dataset_version_id: UUID,
    ) -> QualityReport | None: ...


class EpisodeCandidateDirectory(Protocol):
    def list_candidates(
        self,
        scope: ProjectScope,
        filters: SelectionFilters,
    ) -> tuple[EpisodeCandidate, ...]: ...


class LineageRecorder(Protocol):
    """数据集域需要的**血缘登记能力**（只暴露写边，不暴露遍历）。

    Episode → DatasetVersion 的 ``INCLUDED_IN`` 边在版本转 READY 时写入。
    端口只声明两个动作，避免数据集域直接依赖 Episode 域的仓储实现：

    - ``ensure_node``：登记节点投影，节点必须存在才能写边（复合外键兜底）；
    - ``ensure_edge``：幂等追加一条边，已存在时不重复写。
    """

    def ensure_node(
        self,
        scope: ProjectScope,
        *,
        node_type: object,
        node_id: UUID,
        now: datetime,
    ) -> None: ...

    def ensure_edge(
        self,
        scope: ProjectScope,
        *,
        from_node_type: object,
        from_node_id: UUID,
        to_node_type: object,
        to_node_id: UUID,
        relation: object,
        metadata: Mapping[str, object],
        now: datetime,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class AuditRecord:
    audit_id: UUID
    project_id: UUID
    actor_user_id: UUID
    actor_role: str | None
    resource_type: str
    resource_id: UUID
    action: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    request_id: str | None
    result: str
    details: Mapping[str, object]
    occurred_at: datetime


class AuditRepository(Protocol):
    def record(self, scope: ProjectScope, record: AuditRecord) -> None: ...


class OutboxRepository(Protocol):
    def enqueue(self, scope: ProjectScope, event: OutboxEvent) -> None: ...


# 幂等记录类型与仓储协议由 application 顶层统一声明，避免四个域各写一份。
# 这里重新导出，保持本域既有的 ``from app.application.<domain>.ports import
# IdempotencyRecord`` 调用点不变。
IdempotencyRecord = _IdempotencyRecord
IdempotencyRepository = _IdempotencyRepository
