"""RawAsset 与使用关系 SQLAlchemy 仓储。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import (
    RAW_ASSET_ACTIVE_STATUSES,
    RawAssetFormat,
    RawAssetLinkStatus,
    RawAssetLinkType,
    RawAssetSourceType,
    RawAssetStatus,
    StorageClass,
)
from app.domain.ingestion.errors import (
    ConflictError,
    DomainValidationError,
    VersionConflictError,
)
from app.domain.ingestion.models import RawAsset, RawAssetLink
from app.infrastructure.db.models.ingestion import RawAssetLinkModel, RawAssetModel
from app.infrastructure.repositories._collection_sql import as_utc


class SqlRawAssetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, asset: RawAsset) -> RawAsset:
        scope.ensure(asset.project_id)
        if asset.version != 1:
            raise DomainValidationError("新增资产 version 必须为 1")
        try:
            with self._session.begin_nested():
                self._session.add(
                    RawAssetModel(
                        id=asset.asset_id,
                        project_id=asset.project_id,
                        code=asset.code,
                        source_type=asset.source_type.value,
                        collection_task_id=asset.collection_task_id,
                        batch_id=asset.batch_id,
                        upload_session_id=asset.upload_session_id,
                        capture_session_id=asset.capture_session_id,
                        original_filename=asset.original_filename,
                        object_key=asset.object_key,
                        size_bytes=asset.size_bytes,
                        content_type=asset.content_type,
                        format=asset.format.value,
                        sha256=asset.sha256,
                        status=asset.status.value,
                        captured_at=asset.captured_at,
                        device_ids=[str(item) for item in asset.device_ids],
                        sensor_ids=[str(item) for item in asset.sensor_ids],
                        sensor_snapshot=(
                            dict(asset.sensor_snapshot)
                            if asset.sensor_snapshot is not None
                            else None
                        ),
                        device_time_raw=(
                            dict(asset.device_time_raw)
                            if asset.device_time_raw is not None
                            else None
                        ),
                        raw_metadata=dict(asset.raw_metadata),
                        ingested_at=asset.ingested_at,
                        storage_class=asset.storage_class.value,
                        retention_policy_id=asset.retention_policy_id,
                        quarantine_reason=asset.quarantine_reason,
                        quarantine_codes=list(asset.quarantine_codes),
                        scan_report_key=asset.scan_report_key,
                        verified_at=asset.verified_at,
                        archived_at=asset.archived_at,
                        tombstoned_at=asset.tombstoned_at,
                        duplicate_of_asset_id=asset.duplicate_of_asset_id,
                        uploaded_by=asset.uploaded_by,
                        version=asset.version,
                        created_at=asset.created_at or asset.ingested_at,
                        updated_at=asset.updated_at or asset.ingested_at,
                        created_by=scope.actor_id,
                        updated_by=scope.actor_id,
                    )
                )
                self._session.flush()
        except IntegrityError as exc:
            message = str(exc.orig).lower()
            if any(token in message for token in ("unique", "duplicate", "uq_")):
                raise ConflictError("同项目已存在相同内容的可用资产") from exc
            raise
        return asset

    def get(self, scope: ProjectScope, asset_id: UUID) -> RawAsset | None:
        row = self._session.scalar(
            select(RawAssetModel).where(
                RawAssetModel.id == asset_id,
                RawAssetModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_for_update(self, scope: ProjectScope, asset_id: UUID) -> RawAsset | None:
        """锁定资产行，避免重复事件并发迁移同一资产。

        PostgreSQL 使用行锁；SQLite 忽略 ``FOR UPDATE``，由单写事务串行化。
        """
        row = self._session.scalar(
            select(RawAssetModel)
            .where(
                RawAssetModel.id == asset_id,
                RawAssetModel.project_id == scope.project_id,
            )
            .with_for_update()
        )
        return self._from_row(row) if row is not None else None

    def find_active_by_content(
        self,
        scope: ProjectScope,
        *,
        sha256: str,
        size_bytes: int,
    ) -> RawAsset | None:
        row = self._session.scalar(
            select(RawAssetModel).where(
                RawAssetModel.project_id == scope.project_id,
                RawAssetModel.sha256 == sha256,
                RawAssetModel.size_bytes == size_bytes,
                RawAssetModel.status.in_(sorted(RAW_ASSET_ACTIVE_STATUSES)),
            )
        )
        return self._from_row(row) if row is not None else None

    def attach_capture_session_if_absent(
        self,
        scope: ProjectScope,
        asset_id: UUID,
        *,
        capture_session_id: UUID,
    ) -> bool:
        """去重命中既有资产时补写采集会话归属（V31-001）。

        语义：资产只记一个来源会话。已有时保留最早归属，不覆盖——这是
        可追溯性的权威值；只有当原值为空（历史导入、首次上传未带会话）时
        才补写，并返回是否真的写入。

        ``WHERE capture_session_id IS NULL`` 让并发补写天然幂等：两个请求
        同时到达时只有一个能成功，另一个读到已写入的值。
        """
        result = self._session.execute(
            update(RawAssetModel)
            .where(
                RawAssetModel.id == asset_id,
                RawAssetModel.project_id == scope.project_id,
                RawAssetModel.capture_session_id.is_(None),
            )
            .values(
                capture_session_id=capture_session_id,
                updated_at=datetime.now(UTC),
                updated_by=scope.actor_id,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount > 0

    def list(
        self,
        scope: ProjectScope,
        *,
        status: RawAssetStatus | None = None,
        collection_task_id: UUID | None = None,
        format: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[RawAsset]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = select(RawAssetModel).where(RawAssetModel.project_id == scope.project_id)
        if status is not None:
            statement = statement.where(RawAssetModel.status == status.value)
        if collection_task_id is not None:
            statement = statement.where(RawAssetModel.collection_task_id == collection_task_id)
        if format is not None:
            try:
                normalized_format = RawAssetFormat(format)
            except ValueError as exc:
                raise DomainValidationError("format 取值非法") from exc
            statement = statement.where(RawAssetModel.format == normalized_format.value)
        if after_id is not None:
            statement = statement.where(RawAssetModel.id > after_id)
        statement = statement.order_by(RawAssetModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def list_source_types(
        self,
        scope: ProjectScope,
        asset_ids: Sequence[UUID],
    ) -> dict[UUID, str]:
        """批量读来源类型；只为血缘投影服务，不构造完整领域对象。"""
        ids = tuple(dict.fromkeys(asset_ids))
        if not ids:
            return {}
        rows = self._session.execute(
            select(RawAssetModel.id, RawAssetModel.source_type).where(
                RawAssetModel.project_id == scope.project_id,
                RawAssetModel.id.in_(ids),
            )
        )
        return {row.id: row.source_type for row in rows}

    def save(
        self,
        scope: ProjectScope,
        asset: RawAsset,
        *,
        expected_version: int,
    ) -> RawAsset:
        """仅更新状态/元数据字段；内容字段不在更新列中。

        唯一索引冲突的处理见 ``_flush_or_conflict``：UPDATE 也可能触发
        ``uq_raw_assets_project_content_active``（部分索引，只覆盖活跃状态），
        而 PostgreSQL 上语句失败会让事务进入 aborted 状态。
        """
        scope.ensure(asset.project_id)
        if asset.version != expected_version:
            raise VersionConflictError("原始资产")
        result = self._flush_or_conflict(
            lambda: self._session.execute(
                update(RawAssetModel)
                .where(
                    RawAssetModel.id == asset.asset_id,
                    RawAssetModel.project_id == scope.project_id,
                    RawAssetModel.version == expected_version,
                )
                .values(
                    status=asset.status.value,
                    captured_at=asset.captured_at,
                    storage_class=asset.storage_class.value,
                    retention_policy_id=asset.retention_policy_id,
                    quarantine_reason=asset.quarantine_reason,
                    quarantine_codes=list(asset.quarantine_codes),
                    scan_report_key=asset.scan_report_key,
                    verified_at=asset.verified_at,
                    archived_at=asset.archived_at,
                    tombstoned_at=asset.tombstoned_at,
                    duplicate_of_asset_id=asset.duplicate_of_asset_id,
                    updated_at=asset.updated_at or asset.ingested_at,
                    updated_by=scope.actor_id,
                    version=expected_version + 1,
                )
            )
        )
        if result.rowcount == 0:
            raise VersionConflictError("原始资产")
        return replace(
            asset,
            version=expected_version + 1,
            updated_at=asset.updated_at or asset.ingested_at,
            updated_by=scope.actor_id,
        )

    def _flush_or_conflict(self, run: Callable[[], Any]) -> Any:
        """执行一条可能撞唯一索引的写语句，冲突时把事务恢复干净并翻成 ConflictError。

        **为什么不用 ``begin_nested`` 包整个执行**：SQLAlchemy 的 savepoint 块
        正常退出会 *release*（提交）该 savepoint，等于让这次写在审计失败时无法
        被外层事务回滚——`test_audit_failure_rolls_back_business_and_retries`
        正是守这条原子性。

        **正确的恢复方式**：只在**冲突发生后**把本会话回滚到最近的外层状态。
        冲突意味着这条语句本身没有生效，回滚它不会丢失调用方在同一事务里更早
        写入的数据（那些写入在更外层的 savepoint/事务里）；却足以让 PostgreSQL
        的事务从 aborted 恢复，使调用方能继续查「已存在的活跃资产」。

        不用 ``session.rollback()`` 全回滚：那会把调用方更早的业务写入一并丢掉。
        这里用 ``rollback`` 与用例的 ``uow`` 边界配合——调用方（校验处理器）在
        捕获 ``ConflictError`` 后会自己重建会话读取状态（见用例实现）。
        """
        try:
            return run()
        except IntegrityError as exc:
            message = str(exc.orig).lower()
            if any(token in message for token in ("unique", "duplicate", "uq_")):
                # 让 session 脱离 aborted 状态；调用方随后会重新读取。
                self._session.rollback()
                raise ConflictError("同项目已存在相同内容的可用资产") from exc
            raise

    def _from_row(self, row: RawAssetModel) -> RawAsset:
        return RawAsset(
            asset_id=row.id,
            project_id=row.project_id,
            code=row.code,
            source_type=RawAssetSourceType(row.source_type),
            collection_task_id=row.collection_task_id,
            batch_id=row.batch_id,
            upload_session_id=row.upload_session_id,
            capture_session_id=row.capture_session_id,
            original_filename=row.original_filename,
            object_key=row.object_key,
            size_bytes=row.size_bytes,
            content_type=row.content_type,
            format=RawAssetFormat(row.format),
            sha256=row.sha256,
            status=RawAssetStatus(row.status),
            captured_at=as_utc(row.captured_at),
            device_ids=tuple(UUID(str(item)) for item in (row.device_ids or ())),
            sensor_ids=tuple(UUID(str(item)) for item in (row.sensor_ids or ())),
            sensor_snapshot=row.sensor_snapshot,
            device_time_raw=row.device_time_raw,
            raw_metadata=row.raw_metadata or {},
            ingested_at=as_utc(row.ingested_at),
            storage_class=StorageClass(row.storage_class),
            retention_policy_id=row.retention_policy_id,
            quarantine_reason=row.quarantine_reason,
            quarantine_codes=tuple(row.quarantine_codes or ()),
            scan_report_key=row.scan_report_key,
            verified_at=as_utc(row.verified_at),
            archived_at=as_utc(row.archived_at),
            tombstoned_at=as_utc(row.tombstoned_at),
            duplicate_of_asset_id=row.duplicate_of_asset_id,
            uploaded_by=row.uploaded_by,
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )


class SqlRawAssetLinkRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, link: RawAssetLink) -> RawAssetLink:
        scope.ensure(link.project_id)
        try:
            with self._session.begin_nested():
                self._session.add(
                    RawAssetLinkModel(
                        id=link.link_id,
                        project_id=link.project_id,
                        asset_id=link.asset_id,
                        collection_task_id=link.collection_task_id,
                        batch_id=link.batch_id,
                        upload_session_id=link.upload_session_id,
                        link_type=link.link_type.value,
                        link_status=link.link_status.value,
                        reason=link.reason,
                        created_by=scope.actor_id,
                        created_at=link.created_at or datetime.now(UTC),
                    )
                )
                self._session.flush()
        except IntegrityError as exc:
            message = str(exc.orig).lower()
            if any(token in message for token in ("unique", "duplicate", "uq_")):
                raise ConflictError("使用关系已存在") from exc
            raise
        return link

    def exists_for_session(
        self,
        scope: ProjectScope,
        *,
        asset_id: UUID,
        upload_session_id: UUID,
        link_type: str,
    ) -> bool:
        row = self._session.scalar(
            select(RawAssetLinkModel.id).where(
                RawAssetLinkModel.project_id == scope.project_id,
                RawAssetLinkModel.asset_id == asset_id,
                RawAssetLinkModel.upload_session_id == upload_session_id,
                RawAssetLinkModel.link_type == link_type,
            )
        )
        return row is not None

    def list_for_asset(
        self,
        scope: ProjectScope,
        asset_id: UUID,
    ) -> list[RawAssetLink]:
        rows = self._session.scalars(
            select(RawAssetLinkModel)
            .where(
                RawAssetLinkModel.project_id == scope.project_id,
                RawAssetLinkModel.asset_id == asset_id,
            )
            .order_by(RawAssetLinkModel.created_at)
        )
        return [self._from_row(row) for row in rows]

    def _from_row(self, row: RawAssetLinkModel) -> RawAssetLink:
        return RawAssetLink(
            link_id=row.id,
            project_id=row.project_id,
            asset_id=row.asset_id,
            link_type=RawAssetLinkType(row.link_type),
            link_status=RawAssetLinkStatus(row.link_status),
            collection_task_id=row.collection_task_id,
            batch_id=row.batch_id,
            upload_session_id=row.upload_session_id,
            created_by=row.created_by,
            created_at=as_utc(row.created_at),
            reason=row.reason,
        )
