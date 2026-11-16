"""治理域 SQLAlchemy 仓储（N9 规模化运营）。

测量口径（权威来源）
--------------------
- ``EPISODES`` / ``DATASET_VERSIONS`` / ``INGESTION_JOBS`` / ``INFERENCE_TASKS``：
  对应业务表的**真实行数**，按 ``project_id`` 过滤。
- ``OBJECT_STORAGE_BYTES``：``raw_assets.size_bytes`` 的求和。这是上传完成时
  已校验并落库的权威元数据；不依赖对象存储列目录（那需要凭据、慢、且与
  数据库可能暂时不一致）。``REJECTED`` / ``TOMBSTONED`` 不占存储空间，
  故排除；``VALIDATING`` / ``VALID`` / ``QUARANTINED`` / ``ARCHIVED`` 计入。

查询失败必须显式失败
--------------------
``measure`` / ``measure_all`` 捕获数据库异常后统一抛
:class:`QuotaMeasurementUnavailableError`，由报表或 API 返回 503 / 不写快照。
**绝不**返回 0：那会把"测不到"伪装成"没用量"，让配额永久失效。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.domain.governance.enums import ResourceKind
from app.domain.governance.errors import (
    DuplicateResourceError,
    QuotaMeasurementUnavailableError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.governance.models import ResourceQuota, ResourceUsage, UsageSnapshot
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import RawAssetStatus
from app.infrastructure.db.models.datasets import DatasetVersionModel
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.governance import (
    ResourceQuotaModel,
    UsageSnapshotModel,
)
from app.infrastructure.db.models.identity import Project
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.db.models.jobs import IngestionJobModel
from app.infrastructure.db.models.modelops import InferenceTaskRow
from app.infrastructure.repositories.idempotency import SqlIdempotencyRepository

logger = logging.getLogger("ddp.governance.usage")

_QUOTA_LABEL = "资源配额"

# 占据对象存储的状态。``REJECTED`` 与 ``TOMBSTONED`` 是"内容已丢弃"的终态，
# 其余状态（含 ``VALIDATING``）都已有落库字节。直接由领域枚举推导，
# 避免在查询里硬编码字符串而与枚举漂移。
_STORAGE_OCCUPYING_STATUSES = tuple(
    status.value
    for status in RawAssetStatus
    if status not in {RawAssetStatus.REJECTED, RawAssetStatus.TOMBSTONED}
)

_SOURCE_BY_KIND: Mapping[ResourceKind, str] = {
    ResourceKind.OBJECT_STORAGE_BYTES: "raw_assets.size_bytes",
    ResourceKind.EPISODES: "episodes.count",
    ResourceKind.DATASET_VERSIONS: "dataset_versions.count",
    ResourceKind.INGESTION_JOBS: "ingestion_jobs.count",
    ResourceKind.INFERENCE_TASKS: "inference_tasks.count",
}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


class SqlResourceQuotaRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, scope: ProjectScope, kind: ResourceKind) -> ResourceQuota | None:
        row = self._session.scalar(
            select(ResourceQuotaModel).where(
                ResourceQuotaModel.project_id == scope.project_id,
                ResourceQuotaModel.resource_kind == kind.value,
            )
        )
        return None if row is None else _quota(row)

    def list(self, scope: ProjectScope) -> list[ResourceQuota]:
        rows = self._session.scalars(
            select(ResourceQuotaModel)
            .where(ResourceQuotaModel.project_id == scope.project_id)
            .order_by(ResourceQuotaModel.resource_kind)
        )
        return [_quota(row) for row in rows]

    def create(self, scope: ProjectScope, quota: ResourceQuota) -> ResourceQuota:
        scope.ensure(quota.project_id)
        self._session.add(
            ResourceQuotaModel(
                id=quota.quota_id,
                project_id=quota.project_id,
                resource_kind=quota.resource_kind.value,
                limit_value=quota.limit_value,
                version=quota.version,
                created_at=quota.created_at or datetime.now(UTC),
                updated_at=quota.updated_at or datetime.now(UTC),
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            message = str(exc.orig).lower()
            if any(token in message for token in ("unique", "duplicate", "uq_")):
                raise DuplicateResourceError("项目内该资源类型已存在配额") from exc
            raise
        return quota

    def save(
        self,
        scope: ProjectScope,
        quota: ResourceQuota,
        *,
        expected_version: int,
    ) -> ResourceQuota:
        scope.ensure(quota.project_id)
        result = self._session.execute(
            update(ResourceQuotaModel)
            .where(
                ResourceQuotaModel.id == quota.quota_id,
                ResourceQuotaModel.project_id == scope.project_id,
                ResourceQuotaModel.version == expected_version,
            )
            .values(
                limit_value=quota.limit_value,
                updated_at=quota.updated_at or datetime.now(UTC),
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            _raise_update_failure(self._session, scope, quota.quota_id)
        return ResourceQuota(
            quota_id=quota.quota_id,
            project_id=quota.project_id,
            resource_kind=quota.resource_kind,
            limit_value=quota.limit_value,
            version=expected_version + 1,
            created_at=quota.created_at,
            updated_at=quota.updated_at,
            created_by=quota.created_by,
            updated_by=scope.actor_id,
        )


class SqlUsageReader:
    def __init__(self, session: Session) -> None:
        self._session = session

    def measure(
        self,
        scope: ProjectScope,
        kind: ResourceKind,
        *,
        at: datetime,
    ) -> ResourceUsage:
        try:
            value = _project_value(self._session, kind, scope.project_id) or 0
        except SQLAlchemyError as exc:
            logger.warning("用量测量失败：%s", kind.value, exc_info=True)
            raise QuotaMeasurementUnavailableError(kind) from exc
        return ResourceUsage(
            resource_kind=kind,
            value=value,
            measured_at=at,
            source=_SOURCE_BY_KIND[kind],
        )

    def measure_all(
        self,
        *,
        at: datetime,
    ) -> Mapping[UUID, Mapping[ResourceKind, ResourceUsage]]:
        try:
            project_ids = list(self._session.scalars(select(Project.id)))
            grouped = {kind: _grouped_values(self._session, kind) for kind in ResourceKind}
        except SQLAlchemyError as exc:
            logger.warning("全量用量测量失败", exc_info=True)
            raise QuotaMeasurementUnavailableError() from exc

        result: dict[UUID, dict[ResourceKind, ResourceUsage]] = {}
        for project_id in project_ids:
            result[project_id] = {
                kind: ResourceUsage(
                    resource_kind=kind,
                    value=grouped[kind].get(project_id, 0),
                    measured_at=at,
                    source=_SOURCE_BY_KIND[kind],
                )
                for kind in ResourceKind
            }
        return result


class SqlIdempotencyReplayDetector:
    """基于共享幂等表的"是否重放"判定。

    只复用 ``SqlIdempotencyRepository.get``，不重写作用域与过期语义。
    请求体是否一致由各用例的 ``IdempotencyGuard`` 判断：即使本方法因
    "同键不同请求体"返回 True 而跳过配额判定，用例随后也会在写入前抛
    ``idempotency_key_reused``，不会产生越权写入。
    """

    def __init__(self, session: Session) -> None:
        self._repository = SqlIdempotencyRepository(session)

    def is_replay(
        self,
        scope: ProjectScope,
        key: str,
        *,
        method: str,
        path: str,
        now: datetime,
    ) -> bool:
        record = self._repository.get(scope, key)
        if record is None:
            return False
        if record.method != method or record.path != path:
            return False
        return record.expires_at > now


class SqlUsageSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def latest(self, scope: ProjectScope) -> Mapping[ResourceKind, ResourceUsage]:
        rows = self._session.scalars(
            select(UsageSnapshotModel).where(UsageSnapshotModel.project_id == scope.project_id)
        )
        measured: dict[ResourceKind, ResourceUsage] = {}
        for row in rows:
            try:
                kind = ResourceKind(row.resource_kind)
            except ValueError:
                # 绕过应用/CHECK 的直接改库才会出现；不猜测、不归入任何已知类型。
                logger.warning("用量快照含未知资源类型，已忽略：%r", row.resource_kind)
                continue
            measured[kind] = ResourceUsage(
                resource_kind=kind,
                value=row.used_value,
                measured_at=_as_utc(row.measured_at),
                source=row.measurement_source,
            )
        return measured

    def replace_all(self, rows: Sequence[UsageSnapshot]) -> None:
        """整体替换快照（仅 Worker 调用）。

        先删后插在同一事务内完成：读者要么看到上一版、要么看到新版。
        """
        self._session.execute(delete(UsageSnapshotModel))
        self._session.add_all(
            [
                UsageSnapshotModel(
                    project_id=row.project_id,
                    resource_kind=row.usage.resource_kind.value,
                    used_value=row.usage.value,
                    measurement_source=row.usage.source,
                    measured_at=row.usage.measured_at,
                )
                for row in rows
            ]
        )
        self._session.flush()


def _project_value(session: Session, kind: ResourceKind, project_id: UUID) -> int | None:
    """单项目、单资源类型的实时聚合。"""
    if kind is ResourceKind.EPISODES:
        return session.scalar(
            select(func.count())
            .select_from(EpisodeModel)
            .where(EpisodeModel.project_id == project_id)
        )
    if kind is ResourceKind.DATASET_VERSIONS:
        return session.scalar(
            select(func.count())
            .select_from(DatasetVersionModel)
            .where(DatasetVersionModel.project_id == project_id)
        )
    if kind is ResourceKind.INGESTION_JOBS:
        return session.scalar(
            select(func.count())
            .select_from(IngestionJobModel)
            .where(IngestionJobModel.project_id == project_id)
        )
    if kind is ResourceKind.INFERENCE_TASKS:
        return session.scalar(
            select(func.count())
            .select_from(InferenceTaskRow)
            .where(InferenceTaskRow.project_id == project_id)
        )
    if kind is ResourceKind.OBJECT_STORAGE_BYTES:
        return session.scalar(
            select(func.coalesce(func.sum(RawAssetModel.size_bytes), 0)).where(
                RawAssetModel.project_id == project_id,
                RawAssetModel.status.in_(_STORAGE_OCCUPYING_STATUSES),
            )
        )
    raise AssertionError(f"未实现的资源类型：{kind}")


def _grouped_values(session: Session, kind: ResourceKind) -> dict[UUID, int]:
    """全部项目的用量分布；每个资源类型一次 ``GROUP BY``，避免 N+1。"""
    if kind is ResourceKind.OBJECT_STORAGE_BYTES:
        statement = (
            select(
                RawAssetModel.project_id,
                func.coalesce(func.sum(RawAssetModel.size_bytes), 0),
            )
            .where(RawAssetModel.status.in_(_STORAGE_OCCUPYING_STATUSES))
            .group_by(RawAssetModel.project_id)
        )
        return {
            UUID(str(project_id)) if not isinstance(project_id, UUID) else project_id: int(value)
            for project_id, value in session.execute(statement)
        }

    model = {
        ResourceKind.EPISODES: EpisodeModel,
        ResourceKind.DATASET_VERSIONS: DatasetVersionModel,
        ResourceKind.INGESTION_JOBS: IngestionJobModel,
        ResourceKind.INFERENCE_TASKS: InferenceTaskRow,
    }[kind]
    statement = select(model.project_id, func.count()).group_by(model.project_id)
    return {
        UUID(str(project_id)) if not isinstance(project_id, UUID) else project_id: int(value)
        for project_id, value in session.execute(statement)
    }


def _quota(row: ResourceQuotaModel) -> ResourceQuota:
    return ResourceQuota(
        quota_id=row.id,
        project_id=row.project_id,
        resource_kind=row.resource_kind,
        limit_value=row.limit_value,
        version=row.version,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


def _raise_update_failure(session: Session, scope: ProjectScope, quota_id: UUID) -> None:
    exists = session.scalar(
        select(ResourceQuotaModel.id).where(
            ResourceQuotaModel.id == quota_id,
            ResourceQuotaModel.project_id == scope.project_id,
        )
    )
    if exists is None:
        raise ResourceNotFoundError(_QUOTA_LABEL, quota_id)
    raise VersionConflictError(_QUOTA_LABEL)


__all__ = [
    "SqlIdempotencyReplayDetector",
    "SqlResourceQuotaRepository",
    "SqlUsageReader",
    "SqlUsageSnapshotRepository",
]
