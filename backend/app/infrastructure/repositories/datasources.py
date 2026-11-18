"""数据源与导入记录的 SQLAlchemy 仓储。

所有查询强制带 ``project_id``：跨项目资源返回 ``None``，由用例统一映射为 404，
避免通过 ID 枚举探测其他项目的数据源。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from uuid import UUID

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.datasources.enums import (
    AccessCondition,
    DataSourceKind,
    ImportLogLevel,
    ImportScope,
    ImportStage,
    ImportStatus,
)
from app.domain.datasources.errors import (
    DomainValidationError,
    DuplicateResourceError,
    VersionConflictError,
)
from app.domain.datasources.media import MediaAsset
from app.domain.datasources.models import DataSource, DataSourceImport, DataSourceImportLog
from app.domain.datasources.review import DataReview
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.datasources import (
    DataReviewModel,
    DataSourceImportLogModel,
    DataSourceImportModel,
    DataSourceModel,
    MediaAssetModel,
)
from app.infrastructure.repositories._sql_support import (
    as_utc,
    bounded_limit,
    raise_update_failure,
    scoped_select,
)


def _source_values(item: DataSource) -> dict[str, object]:
    return {
        "code": item.code,
        "name": item.name,
        "kind": DataSourceKind(item.kind).value,
        "repo_id": item.repo_id,
        "revision": item.revision,
        "license": item.license,
        "access_condition": AccessCondition(item.access_condition).value,
        "is_gated": item.is_gated,
        "config_name": item.config_name,
        "split": item.split,
        "dataset_card_url": item.dataset_card_url,
        "notes": item.notes,
        "metadata_json": dict(item.metadata),
    }


def _source_from_row(row: DataSourceModel) -> DataSource:
    return DataSource(
        data_source_id=row.id,
        project_id=row.project_id,
        code=row.code,
        name=row.name,
        kind=row.kind,
        repo_id=row.repo_id,
        revision=row.revision,
        license=row.license,
        access_condition=row.access_condition,
        is_gated=row.is_gated,
        config_name=row.config_name,
        split=row.split,
        dataset_card_url=row.dataset_card_url,
        notes=row.notes,
        metadata=row.metadata_json or {},
        row_version=row.version,
    )


def _import_values(item: DataSourceImport) -> dict[str, object]:
    return {
        "data_source_id": item.data_source_id,
        "status": ImportStatus(item.status).value,
        "scope": ImportScope(item.scope).value,
        "tool_name": item.tool_name,
        "tool_version": item.tool_version,
        "sample_limit": item.sample_limit,
        "cache_location": item.cache_location,
        "collection_task_id": item.collection_task_id,
        "batch_id": item.batch_id,
        "imported_row_count": item.imported_row_count,
        "imported_file_count": item.imported_file_count,
        "error_message": item.error_message,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
        "stage": ImportStage(item.stage).value if item.stage is not None else None,
        "processed_items": item.processed_items,
        "total_items": item.total_items,
        "attempt": item.attempt,
        "max_attempts": item.max_attempts,
    }


def _import_from_row(row: DataSourceImportModel) -> DataSourceImport:
    return DataSourceImport(
        import_id=row.id,
        project_id=row.project_id,
        data_source_id=row.data_source_id,
        status=row.status,
        scope=row.scope,
        tool_name=row.tool_name,
        tool_version=row.tool_version,
        sample_limit=row.sample_limit,
        cache_location=row.cache_location,
        collection_task_id=row.collection_task_id,
        batch_id=row.batch_id,
        imported_row_count=row.imported_row_count,
        imported_file_count=row.imported_file_count,
        error_message=row.error_message,
        started_at=as_utc(row.started_at),
        finished_at=as_utc(row.finished_at),
        stage=row.stage,
        processed_items=row.processed_items,
        total_items=row.total_items,
        attempt=row.attempt,
        max_attempts=row.max_attempts,
        row_version=row.version,
    )


class SqlDataSourceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, item: DataSource) -> DataSource:
        scope.ensure(item.project_id)
        self._session.add(
            DataSourceModel(
                id=item.data_source_id,
                project_id=item.project_id,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
                **_source_values(item),
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise DuplicateResourceError("数据源编码或 repo/revision 已登记") from exc
        return item

    def get(self, scope: ProjectScope, data_source_id: UUID) -> DataSource | None:
        row = self._session.scalar(
            select(DataSourceModel).where(
                DataSourceModel.id == data_source_id,
                DataSourceModel.project_id == scope.project_id,
            )
        )
        return _source_from_row(row) if row is not None else None

    def get_by_code(self, scope: ProjectScope, code: str) -> DataSource | None:
        row = self._session.scalar(
            select(DataSourceModel).where(
                DataSourceModel.code == code.strip(),
                DataSourceModel.project_id == scope.project_id,
            )
        )
        return _source_from_row(row) if row is not None else None

    def get_by_revision(
        self,
        scope: ProjectScope,
        *,
        repo_id: str,
        revision: str,
        config_name: str | None,
        split: str | None,
    ) -> DataSource | None:
        statement = select(DataSourceModel).where(
            DataSourceModel.project_id == scope.project_id,
            DataSourceModel.repo_id == repo_id,
            DataSourceModel.revision == revision,
        )
        # NULL 在唯一约束里视为不同值，这里必须显式用 IS NULL 才能与库守卫一致。
        statement = (
            statement.where(DataSourceModel.config_name.is_(None))
            if config_name is None
            else statement.where(DataSourceModel.config_name == config_name)
        )
        statement = (
            statement.where(DataSourceModel.split.is_(None))
            if split is None
            else statement.where(DataSourceModel.split == split)
        )
        row = self._session.scalar(statement)
        return _source_from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        kind: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DataSource]:
        statement = scoped_select(DataSourceModel, scope)
        if kind is not None:
            statement = statement.where(DataSourceModel.kind == DataSourceKind(kind).value)
        if after_id is not None:
            statement = statement.where(DataSourceModel.id > after_id)
        statement = statement.order_by(DataSourceModel.id).limit(bounded_limit(limit, maximum=101))
        return [_source_from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        item: DataSource,
        *,
        expected_version: int,
    ) -> DataSource:
        scope.ensure(item.project_id)
        result = self._session.execute(
            update(DataSourceModel)
            .where(
                DataSourceModel.id == item.data_source_id,
                DataSourceModel.project_id == scope.project_id,
                DataSourceModel.version == expected_version,
            )
            .values(
                **_source_values(item),
                version=expected_version + 1,
                updated_by=scope.actor_id,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                DataSourceModel,
                scope,
                item.data_source_id,
                "数据源",
                expected_version,
            )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise VersionConflictError("数据源") from exc
        # slots dataclass 没有 __dict__，必须用 replace 构造新实例。
        return replace(item, row_version=expected_version + 1)


class SqlDataSourceImportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, item: DataSourceImport) -> DataSourceImport:
        scope.ensure(item.project_id)
        self._session.add(
            DataSourceImportModel(
                id=item.import_id,
                project_id=item.project_id,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
                **_import_values(item),
            )
        )
        self._session.flush()
        return item

    def get(self, scope: ProjectScope, import_id: UUID) -> DataSourceImport | None:
        row = self._session.scalar(
            select(DataSourceImportModel).where(
                DataSourceImportModel.id == import_id,
                DataSourceImportModel.project_id == scope.project_id,
            )
        )
        return _import_from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        data_source_id: UUID | None = None,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DataSourceImport]:
        statement = scoped_select(DataSourceImportModel, scope)
        if data_source_id is not None:
            statement = statement.where(DataSourceImportModel.data_source_id == data_source_id)
        if status is not None:
            statement = statement.where(DataSourceImportModel.status == ImportStatus(status).value)
        if after_id is not None:
            statement = statement.where(DataSourceImportModel.id > after_id)
        statement = statement.order_by(DataSourceImportModel.id).limit(
            bounded_limit(limit, maximum=101)
        )
        return [_import_from_row(row) for row in self._session.scalars(statement)]

    def list_for_collection_task(
        self,
        scope: ProjectScope,
        collection_task_id: UUID,
        *,
        limit: int = 100,
    ) -> list[DataSourceImport]:
        """该采集任务名下的全部导入（迁移 0050 写入的归属）。"""
        statement = (
            scoped_select(DataSourceImportModel, scope)
            .where(DataSourceImportModel.collection_task_id == collection_task_id)
            .order_by(DataSourceImportModel.id)
            .limit(bounded_limit(limit, maximum=101))
        )
        return [_import_from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        item: DataSourceImport,
        *,
        expected_version: int,
    ) -> DataSourceImport:
        scope.ensure(item.project_id)
        result = self._session.execute(
            update(DataSourceImportModel)
            .where(
                DataSourceImportModel.id == item.import_id,
                DataSourceImportModel.project_id == scope.project_id,
                DataSourceImportModel.version == expected_version,
            )
            .values(
                **_import_values(item),
                version=expected_version + 1,
                updated_by=scope.actor_id,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                DataSourceImportModel,
                scope,
                item.import_id,
                "导入任务",
                expected_version,
            )
        self._session.flush()
        return replace(item, row_version=expected_version + 1)


def _log_from_row(row: DataSourceImportLogModel) -> DataSourceImportLog:
    occurred_at = as_utc(row.occurred_at)
    if occurred_at is None:
        # occurred_at 是 NOT NULL 列；显式兜底，避免把 None 带进领域对象。
        raise ValueError("data_source_import_logs.occurred_at 不允许为空")
    return DataSourceImportLog(
        log_id=row.id,
        project_id=row.project_id,
        import_id=row.import_id,
        stage=ImportStage(row.stage),
        level=ImportLogLevel(row.level),
        message=row.message,
        occurred_at=occurred_at,
        sequence_no=row.sequence_no,
        attempt=row.attempt,
    )


class SqlDataSourceImportLogRepository:
    """导入步骤日志读写；只追加，项目隔离由 ``scoped_select`` 强制。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, item: DataSourceImportLog) -> DataSourceImportLog:
        scope.ensure(item.project_id)
        self._session.add(
            DataSourceImportLogModel(
                id=item.log_id,
                project_id=item.project_id,
                import_id=item.import_id,
                stage=ImportStage(item.stage).value,
                level=ImportLogLevel(item.level).value,
                message=item.message,
                attempt=item.attempt,
                occurred_at=item.occurred_at,
                sequence_no=item.sequence_no,
            )
        )
        self._session.flush()
        return item

    def list_recent(
        self,
        scope: ProjectScope,
        import_id: UUID,
        *,
        limit: int = 100,
        before_log_id: UUID | None = None,
    ) -> tuple[list[DataSourceImportLog], bool]:
        """取最近 ``limit`` 条（时间正序返回），并如实报告是否还有更早日志。

        查询按 ``(occurred_at, attempt, sequence_no, id)`` 倒序多取一条判断
        是否还有更早的日志，再把结果反转成正序：运维要看的通常是"最近发生了
        什么"，而界面上按时间线阅读最自然。四个字段共同构成全序，避免时间戳
        或序号相同导致翻页丢行/重复。

        ``before_log_id`` 用于加载更早的历史：先取出该日志的排序键，再查严格
        早于它的记录。游标行不存在（跨导入、已删除）时返回空页，避免把另一
        个导入的日志通过游标读出来。
        """
        statement = scoped_select(DataSourceImportLogModel, scope).where(
            DataSourceImportLogModel.import_id == import_id
        )
        if before_log_id is not None:
            anchor = self._session.execute(
                select(
                    DataSourceImportLogModel.occurred_at,
                    DataSourceImportLogModel.attempt,
                    DataSourceImportLogModel.sequence_no,
                ).where(
                    DataSourceImportLogModel.project_id == scope.project_id,
                    DataSourceImportLogModel.import_id == import_id,
                    DataSourceImportLogModel.id == before_log_id,
                )
            ).first()
            if anchor is None:
                return [], False
            statement = statement.where(
                tuple_(
                    DataSourceImportLogModel.occurred_at,
                    DataSourceImportLogModel.attempt,
                    DataSourceImportLogModel.sequence_no,
                    DataSourceImportLogModel.id,
                )
                < tuple_(
                    anchor.occurred_at,
                    anchor.attempt,
                    anchor.sequence_no,
                    before_log_id,
                )
            )

        statement = statement.order_by(
            DataSourceImportLogModel.occurred_at.desc(),
            DataSourceImportLogModel.attempt.desc(),
            DataSourceImportLogModel.sequence_no.desc(),
            DataSourceImportLogModel.id.desc(),
        ).limit(bounded_limit(limit + 1, maximum=501))
        rows = list(self._session.scalars(statement))
        has_older = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()
        return [_log_from_row(row) for row in rows], has_older


def _asset_from_row(row: MediaAssetModel) -> MediaAsset:
    return MediaAsset(
        asset_id=row.id,
        project_id=row.project_id,
        data_source_id=row.data_source_id,
        import_id=row.import_id,
        episode_key=row.episode_key,
        path=row.path,
        camera=row.camera,
        object_key=row.object_key,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        content_type=row.content_type,
        task_name=row.task_name,
        created_at=as_utc(row.created_at),  # type: ignore[arg-type]
        raw_asset_id=row.raw_asset_id,
        excerpt_start_seconds=row.excerpt_start_seconds,
        excerpt_seconds=row.excerpt_seconds,
        source_fps=row.source_fps,
    )


class SqlMediaAssetRepository:
    """导入产出的媒体资产读写；所有查询强制 project 过滤。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, asset: MediaAsset) -> MediaAsset:
        scope.ensure(asset.project_id)
        self._session.add(
            MediaAssetModel(
                id=asset.asset_id,
                project_id=asset.project_id,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
                data_source_id=asset.data_source_id,
                import_id=asset.import_id,
                episode_key=asset.episode_key,
                path=asset.path,
                camera=asset.camera,
                object_key=asset.object_key,
                size_bytes=asset.size_bytes,
                sha256=asset.sha256,
                content_type=asset.content_type,
                task_name=asset.task_name,
                raw_asset_id=asset.raw_asset_id,
                excerpt_start_seconds=asset.excerpt_start_seconds,
                excerpt_seconds=asset.excerpt_seconds,
                source_fps=asset.source_fps,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise DuplicateResourceError(f"媒体资产重复：{asset.path}") from exc
        return asset

    def get_by_import_path(
        self, scope: ProjectScope, import_id: UUID, path: str
    ) -> MediaAsset | None:
        row = self._session.scalar(
            select(MediaAssetModel).where(
                MediaAssetModel.project_id == scope.project_id,
                MediaAssetModel.import_id == import_id,
                MediaAssetModel.path == path,
            )
        )
        return _asset_from_row(row) if row is not None else None

    def list_by_import(
        self, scope: ProjectScope, import_id: UUID, *, limit: int = 200
    ) -> list[MediaAsset]:
        statement = (
            scoped_select(MediaAssetModel, scope)
            .where(MediaAssetModel.import_id == import_id)
            .order_by(MediaAssetModel.episode_key, MediaAssetModel.camera)
            .limit(bounded_limit(limit, maximum=1000))
        )
        return [_asset_from_row(row) for row in self._session.scalars(statement)]

    def count_by_import(self, scope: ProjectScope, import_id: UUID) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(MediaAssetModel)
                .where(
                    MediaAssetModel.project_id == scope.project_id,
                    MediaAssetModel.import_id == import_id,
                )
            )
            or 0
        )

    def list_by_raw_asset_ids(
        self, scope: ProjectScope, raw_asset_ids: tuple[UUID, ...]
    ) -> list[MediaAsset]:
        if not raw_asset_ids:
            return []
        # 去重后作为 IN 条件；同一 RawAsset 若被多次导入，会返回多条资产，
        # 这是事实（不同导入各自产出了媒体文件），不做合并。
        ids = tuple(dict.fromkeys(raw_asset_ids))
        statement = (
            scoped_select(MediaAssetModel, scope)
            .where(MediaAssetModel.raw_asset_id.in_(ids))
            .order_by(MediaAssetModel.raw_asset_id, MediaAssetModel.camera)
            # 多取一条用于上层判断"是否超过检查上限"；截断本身不静默通过。
            .limit(bounded_limit(len(ids) + 1, maximum=1001))
        )
        return [_asset_from_row(row) for row in self._session.scalars(statement)]

    def get(self, scope: ProjectScope, asset_id: UUID) -> MediaAsset | None:
        row = self._session.scalar(
            select(MediaAssetModel).where(
                MediaAssetModel.id == asset_id,
                MediaAssetModel.project_id == scope.project_id,
            )
        )
        return _asset_from_row(row) if row is not None else None


def _review_evidence_from_row(raw: object) -> dict[str, object] | None:
    """把 ``data_source_reviews.evidence`` 列还原为领域证据对象。

    ``NULL`` 原样返回 None（历史记录没有证据，交给用例回退审计留痕）。
    非对象值说明行被外部篡改：宁可让契约校验拒绝，也不静默当作无证据。
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise DomainValidationError("审核记录的 evidence 不是对象")
    return dict(raw)


def _review_from_row(row: DataReviewModel) -> DataReview:
    return DataReview(
        review_id=row.id,
        project_id=row.project_id,
        import_id=row.import_id,
        status=row.status,
        decision=row.decision,
        reason=row.reason,
        reviewer_id=row.reviewer_id,
        submitted_by=row.submitted_by,
        created_at=as_utc(row.created_at),  # type: ignore[arg-type]
        decided_at=as_utc(row.decided_at),
        row_version=row.version,
        evidence=_review_evidence_from_row(row.evidence),
    )


class SqlDataReviewRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, review: DataReview) -> DataReview:
        scope.ensure(review.project_id)
        self._session.add(
            DataReviewModel(
                id=review.review_id,
                project_id=review.project_id,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
                import_id=review.import_id,
                status=review.status.value,
                decision=review.decision.value if review.decision else None,
                reason=review.reason,
                reviewer_id=review.reviewer_id,
                submitted_by=review.submitted_by,
                decided_at=review.decided_at,
                evidence=dict(review.evidence) if review.evidence is not None else None,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise DuplicateResourceError("该导入已存在审核记录") from exc
        return review

    def get_by_import(self, scope: ProjectScope, import_id: UUID) -> DataReview | None:
        row = self._session.scalar(
            select(DataReviewModel).where(
                DataReviewModel.project_id == scope.project_id,
                DataReviewModel.import_id == import_id,
            )
        )
        return _review_from_row(row) if row is not None else None

    def save(self, scope: ProjectScope, review: DataReview, *, expected_version: int) -> DataReview:
        scope.ensure(review.project_id)
        result = self._session.execute(
            update(DataReviewModel)
            .where(
                DataReviewModel.id == review.review_id,
                DataReviewModel.project_id == scope.project_id,
                DataReviewModel.version == expected_version,
            )
            .values(
                status=review.status.value,
                decision=review.decision.value if review.decision else None,
                reason=review.reason,
                reviewer_id=review.reviewer_id,
                decided_at=review.decided_at,
                evidence=dict(review.evidence) if review.evidence is not None else None,
                version=expected_version + 1,
                updated_by=scope.actor_id,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                DataReviewModel,
                scope,
                review.review_id,
                "审核记录",
                expected_version,
            )
        self._session.flush()
        return replace(review, row_version=expected_version + 1)
