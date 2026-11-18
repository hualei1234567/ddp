"""Dataset / DatasetVersion / DatasetItem SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.datasets.enums import (
    DatasetPurpose,
    DatasetStatus,
    DatasetUsagePolicy,
    DatasetVersionStatus,
    LeakageStatus,
    SampleRole,
    Split,
    ValidityStatus,
)
from app.domain.datasets.models import Dataset, DatasetItem, DatasetVersion
from app.domain.datasets.policies import PolicyRef
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.datasets import (
    DatasetItemModel,
    DatasetModel,
    DatasetVersionModel,
)
from app.infrastructure.repositories._dataset_sql import (
    as_utc,
    bounded_limit,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    raise_update_failure,
    scoped_select,
)
from app.infrastructure.repositories._json_array_predicates import (
    json_array_any_of,
    session_dialect,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _ref(policy_id: UUID, version: int, sha: str) -> PolicyRef:
    return PolicyRef(policy_id=policy_id, version=version, policy_sha256=sha)


class SqlDatasetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, dataset: Dataset) -> Dataset:
        scope.ensure(dataset.project_id)
        ensure_initial_version(dataset.row_version)
        now = _now()
        row = DatasetModel(
            id=dataset.dataset_id,
            project_id=dataset.project_id,
            dataset_lineage_id=dataset.dataset_lineage_id,
            code=dataset.code,
            name=dataset.name,
            description=dataset.description,
            purpose=enum_value(dataset.purpose),
            owner_id=dataset.owner_id,
            status=enum_value(dataset.status),
            current_version_id=dataset.current_version_id,
            tags=list(dataset.tags),
            usage_policy=enum_value(dataset.usage_policy),
            retention_policy=dict(dataset.retention_policy or {}) or None,
            default_dedup_policy_id=dataset.default_dedup_policy_id,
            default_split_policy_id=dataset.default_split_policy_id,
            default_leakage_policy_id=dataset.default_leakage_policy_id,
            frozen_version_count=dataset.frozen_version_count,
            archived_at=dataset.archived_at,
            archived_by=dataset.archived_by,
            archive_reason=dataset.archive_reason,
            version=1,
            created_at=dataset.created_at or now,
            updated_at=dataset.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session, "项目内数据集编码重复")
        return replace(dataset, created_by=scope.actor_id, updated_by=scope.actor_id)

    def get(self, scope: ProjectScope, dataset_id: UUID) -> Dataset | None:
        row = self._session.scalar(
            select(DatasetModel).where(
                DatasetModel.id == dataset_id,
                DatasetModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_code(self, scope: ProjectScope, code: str) -> Dataset | None:
        row = self._session.scalar(
            select(DatasetModel).where(
                DatasetModel.project_id == scope.project_id,
                DatasetModel.code == code,
            )
        )
        return self._from_row(row) if row is not None else None

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
    ) -> list[Dataset]:
        """列出数据集；``tags`` 语义为"任一命中"，谓词下推到 SQL。"""
        limit = bounded_limit(limit, maximum=101)
        statement = scoped_select(DatasetModel, scope)
        if status is not None:
            statement = statement.where(DatasetModel.status == status)
        if purpose is not None:
            statement = statement.where(DatasetModel.purpose == purpose)
        if usage_policy is not None:
            statement = statement.where(DatasetModel.usage_policy == usage_policy)
        if tags:
            statement = statement.where(
                json_array_any_of(
                    DatasetModel.tags,
                    tags,
                    dialect=session_dialect(self._session),
                )
            )
        if after_id is not None:
            statement = statement.where(DatasetModel.id > after_id)
        statement = statement.order_by(DatasetModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        dataset: Dataset,
        *,
        expected_version: int,
    ) -> Dataset:
        scope.ensure(dataset.project_id)
        ensure_expected_version(dataset.row_version, expected_version)
        now = _now()
        result = self._session.execute(
            update(DatasetModel)
            .where(
                DatasetModel.id == dataset.dataset_id,
                DatasetModel.project_id == scope.project_id,
                DatasetModel.version == expected_version,
            )
            .values(
                name=dataset.name,
                description=dataset.description,
                purpose=enum_value(dataset.purpose),
                status=enum_value(dataset.status),
                current_version_id=dataset.current_version_id,
                tags=list(dataset.tags),
                usage_policy=enum_value(dataset.usage_policy),
                retention_policy=dict(dataset.retention_policy or {}) or None,
                default_dedup_policy_id=dataset.default_dedup_policy_id,
                default_split_policy_id=dataset.default_split_policy_id,
                default_leakage_policy_id=dataset.default_leakage_policy_id,
                frozen_version_count=dataset.frozen_version_count,
                archived_at=dataset.archived_at,
                archived_by=dataset.archived_by,
                archive_reason=dataset.archive_reason,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                DatasetModel,
                scope,
                dataset.dataset_id,
                "数据集",
                expected_version,
            )
        return replace(
            dataset,
            row_version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: DatasetModel) -> Dataset:
        return Dataset(
            dataset_id=row.id,
            project_id=row.project_id,
            dataset_lineage_id=row.dataset_lineage_id,
            code=row.code,
            name=row.name,
            description=row.description,
            purpose=DatasetPurpose(row.purpose),
            owner_id=row.owner_id,
            usage_policy=DatasetUsagePolicy(row.usage_policy),
            status=DatasetStatus(row.status),
            current_version_id=row.current_version_id,
            tags=tuple(row.tags or ()),
            retention_policy=dict(row.retention_policy or {}) or None,
            default_dedup_policy_id=row.default_dedup_policy_id,
            default_split_policy_id=row.default_split_policy_id,
            default_leakage_policy_id=row.default_leakage_policy_id,
            frozen_version_count=row.frozen_version_count,
            archived_at=as_utc(row.archived_at),
            archived_by=row.archived_by,
            archive_reason=row.archive_reason,
            row_version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )


class SqlDatasetVersionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, version: DatasetVersion) -> DatasetVersion:
        scope.ensure(version.project_id)
        ensure_initial_version(version.row_version)
        now = _now()
        row = DatasetVersionModel(
            id=version.dataset_version_id,
            project_id=version.project_id,
            dataset_id=version.dataset_id,
            dataset_lineage_id=version.dataset_lineage_id,
            dataset_version=version.dataset_version,
            status=enum_value(version.status),
            validity_status=enum_value(version.validity_status),
            selection_rule_version=version.selection_rule_version,
            selection_filters=dict(version.selection_filters),
            selection_snapshot_at=version.selection_snapshot_at,
            dedup_policy_id=version.dedup_policy_ref.policy_id,
            dedup_policy_version=version.dedup_policy_ref.version,
            dedup_policy_sha256=version.dedup_policy_ref.policy_sha256,
            split_policy_id=version.split_policy_ref.policy_id,
            split_policy_version=version.split_policy_ref.version,
            split_policy_sha256=version.split_policy_ref.policy_sha256,
            leakage_policy_id=version.leakage_policy_ref.policy_id,
            leakage_policy_version=version.leakage_policy_ref.version,
            leakage_policy_sha256=version.leakage_policy_ref.policy_sha256,
            quality_policy_id=version.quality_policy_ref.policy_id,
            quality_policy_version=version.quality_policy_ref.version,
            quality_policy_sha256=version.quality_policy_ref.policy_sha256,
            candidate_count=version.candidate_count,
            selected_count=version.selected_count,
            excluded_count=version.excluded_count,
            duplicated_count=version.duplicated_count,
            item_count=version.item_count,
            train_count=version.train_count,
            validation_count=version.validation_count,
            test_count=version.test_count,
            total_duration_ns=version.total_duration_ns,
            total_bytes=version.total_bytes,
            episode_version_digest=version.episode_version_digest,
            annotation_schema_versions=[dict(item) for item in version.annotation_schema_versions],
            annotation_submission_refs_digest=version.annotation_submission_refs_digest,
            manifest_key=version.manifest_key,
            manifest_sha256=version.manifest_sha256,
            quality_report_id=version.quality_report_id,
            quality_report_sha256=version.quality_report_sha256,
            leakage_report_id=version.leakage_report_id,
            leakage_report_sha256=version.leakage_report_sha256,
            leakage_status=enum_value(version.leakage_status) if version.leakage_status else None,
            source_snapshot_at=version.source_snapshot_at,
            build_attempt=version.build_attempt,
            freeze_approval_id=version.freeze_approval_id,
            frozen_by=version.frozen_by,
            frozen_at=version.frozen_at,
            supersedes_version_id=version.supersedes_version_id,
            superseded_by_version_id=version.superseded_by_version_id,
            archived_at=version.archived_at,
            archive_reason=version.archive_reason,
            blocking_issues=list(version.blocking_issues),
            warnings=list(version.warnings),
            exclusion_summary=dict(version.exclusion_summary),
            version=1,
            created_at=version.created_at or now,
            updated_at=version.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session, "同一数据集版本号已存在")
        return replace(version, created_by=scope.actor_id, updated_by=scope.actor_id)

    def get(self, scope: ProjectScope, version_id: UUID) -> DatasetVersion | None:
        row = self._session.scalar(
            select(DatasetVersionModel).where(
                DatasetVersionModel.id == version_id,
                DatasetVersionModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_for_dataset(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        version_id: UUID,
    ) -> DatasetVersion | None:
        row = self._session.scalar(
            select(DatasetVersionModel).where(
                DatasetVersionModel.id == version_id,
                DatasetVersionModel.project_id == scope.project_id,
                DatasetVersionModel.dataset_id == dataset_id,
            )
        )
        return self._from_row(row) if row is not None else None

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
    ) -> list[DatasetVersion]:
        limit = bounded_limit(limit, maximum=101)
        statement = select(DatasetVersionModel).where(
            DatasetVersionModel.project_id == scope.project_id,
            DatasetVersionModel.dataset_id == dataset_id,
        )
        if status is not None:
            statement = statement.where(DatasetVersionModel.status == status)
        if validity_status is not None:
            statement = statement.where(DatasetVersionModel.validity_status == validity_status)
        if leakage_status is not None:
            statement = statement.where(DatasetVersionModel.leakage_status == leakage_status)
        if after_id is not None:
            statement = statement.where(DatasetVersionModel.id > after_id)
        statement = statement.order_by(DatasetVersionModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def list_validity_candidates(
        self,
        *,
        after_id: UUID | None = None,
        limit: int = 200,
    ) -> list[tuple[UUID, UUID]]:
        """跨项目列出"有效性=VALID 且仍在活跃生命周期"的版本 (id, project_id)。

        只给系统巡检用：返回 ID 不返回业务字段，调用方必须自己用
        ``ProjectScope`` 重新读取，项目隔离仍由仓储强制。
        """
        statement = (
            select(DatasetVersionModel.id, DatasetVersionModel.project_id)
            .where(
                DatasetVersionModel.validity_status == ValidityStatus.VALID.value,
                DatasetVersionModel.status.in_(
                    [
                        DatasetVersionStatus.READY.value,
                        DatasetVersionStatus.FROZEN.value,
                    ]
                ),
            )
            .order_by(DatasetVersionModel.id)
            .limit(limit)
        )
        if after_id is not None:
            statement = statement.where(DatasetVersionModel.id > after_id)
        return [(row[0], row[1]) for row in self._session.execute(statement)]

    def save(
        self,
        scope: ProjectScope,
        version: DatasetVersion,
        *,
        expected_version: int,
    ) -> DatasetVersion:
        scope.ensure(version.project_id)
        ensure_expected_version(version.row_version, expected_version)
        now = _now()
        result = self._session.execute(
            update(DatasetVersionModel)
            .where(
                DatasetVersionModel.id == version.dataset_version_id,
                DatasetVersionModel.project_id == scope.project_id,
                DatasetVersionModel.version == expected_version,
            )
            .values(
                status=enum_value(version.status),
                validity_status=enum_value(version.validity_status),
                candidate_count=version.candidate_count,
                selected_count=version.selected_count,
                excluded_count=version.excluded_count,
                duplicated_count=version.duplicated_count,
                item_count=version.item_count,
                train_count=version.train_count,
                validation_count=version.validation_count,
                test_count=version.test_count,
                total_duration_ns=version.total_duration_ns,
                total_bytes=version.total_bytes,
                episode_version_digest=version.episode_version_digest,
                annotation_schema_versions=[
                    dict(item) for item in version.annotation_schema_versions
                ],
                annotation_submission_refs_digest=version.annotation_submission_refs_digest,
                manifest_key=version.manifest_key,
                manifest_sha256=version.manifest_sha256,
                quality_report_id=version.quality_report_id,
                quality_report_sha256=version.quality_report_sha256,
                leakage_report_id=version.leakage_report_id,
                leakage_report_sha256=version.leakage_report_sha256,
                leakage_status=(
                    enum_value(version.leakage_status) if version.leakage_status else None
                ),
                source_snapshot_at=version.source_snapshot_at,
                build_attempt=version.build_attempt,
                freeze_approval_id=version.freeze_approval_id,
                frozen_by=version.frozen_by,
                frozen_at=version.frozen_at,
                supersedes_version_id=version.supersedes_version_id,
                superseded_by_version_id=version.superseded_by_version_id,
                archived_at=version.archived_at,
                archive_reason=version.archive_reason,
                blocking_issues=list(version.blocking_issues),
                warnings=list(version.warnings),
                exclusion_summary=dict(version.exclusion_summary),
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                DatasetVersionModel,
                scope,
                version.dataset_version_id,
                "数据集版本",
                expected_version,
            )
        return replace(
            version,
            row_version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def next_version_number(self, scope: ProjectScope, dataset_id: UUID) -> int:
        current = self._session.scalar(
            select(func.max(DatasetVersionModel.dataset_version)).where(
                DatasetVersionModel.project_id == scope.project_id,
                DatasetVersionModel.dataset_id == dataset_id,
            )
        )
        return int(current or 0) + 1

    def latest_frozen(self, scope: ProjectScope, dataset_id: UUID) -> DatasetVersion | None:
        row = self._session.scalar(
            select(DatasetVersionModel)
            .where(
                DatasetVersionModel.project_id == scope.project_id,
                DatasetVersionModel.dataset_id == dataset_id,
                DatasetVersionModel.status == DatasetVersionStatus.FROZEN.value,
            )
            .order_by(DatasetVersionModel.dataset_version.desc())
            .limit(1)
        )
        return self._from_row(row) if row is not None else None

    def _from_row(self, row: DatasetVersionModel) -> DatasetVersion:
        return DatasetVersion(
            dataset_version_id=row.id,
            project_id=row.project_id,
            dataset_id=row.dataset_id,
            dataset_lineage_id=row.dataset_lineage_id,
            dataset_version=row.dataset_version,
            status=DatasetVersionStatus(row.status),
            validity_status=ValidityStatus(row.validity_status),
            selection_rule_version=row.selection_rule_version,
            selection_filters=dict(row.selection_filters or {}),
            selection_snapshot_at=as_utc(row.selection_snapshot_at),  # type: ignore[arg-type]
            dedup_policy_ref=_ref(
                row.dedup_policy_id,
                row.dedup_policy_version,
                row.dedup_policy_sha256,
            ),
            split_policy_ref=_ref(
                row.split_policy_id,
                row.split_policy_version,
                row.split_policy_sha256,
            ),
            leakage_policy_ref=_ref(
                row.leakage_policy_id,
                row.leakage_policy_version,
                row.leakage_policy_sha256,
            ),
            quality_policy_ref=_ref(
                row.quality_policy_id,
                row.quality_policy_version,
                row.quality_policy_sha256,
            ),
            candidate_count=row.candidate_count,
            selected_count=row.selected_count,
            excluded_count=row.excluded_count,
            duplicated_count=row.duplicated_count,
            item_count=row.item_count,
            train_count=row.train_count,
            validation_count=row.validation_count,
            test_count=row.test_count,
            total_duration_ns=row.total_duration_ns,
            total_bytes=row.total_bytes,
            episode_version_digest=row.episode_version_digest,
            annotation_schema_versions=tuple(
                dict(item) for item in (row.annotation_schema_versions or [])
            ),
            annotation_submission_refs_digest=row.annotation_submission_refs_digest,
            manifest_key=row.manifest_key,
            manifest_sha256=row.manifest_sha256,
            quality_report_id=row.quality_report_id,
            quality_report_sha256=row.quality_report_sha256,
            leakage_report_id=row.leakage_report_id,
            leakage_report_sha256=row.leakage_report_sha256,
            leakage_status=LeakageStatus(row.leakage_status) if row.leakage_status else None,
            source_snapshot_at=as_utc(row.source_snapshot_at),
            build_attempt=row.build_attempt,
            freeze_approval_id=row.freeze_approval_id,
            frozen_by=row.frozen_by,
            frozen_at=as_utc(row.frozen_at),
            supersedes_version_id=row.supersedes_version_id,
            superseded_by_version_id=row.superseded_by_version_id,
            archived_at=as_utc(row.archived_at),
            archive_reason=row.archive_reason,
            blocking_issues=tuple(row.blocking_issues or ()),
            warnings=tuple(row.warnings or ()),
            exclusion_summary=dict(row.exclusion_summary or {}),
            row_version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )


class SqlDatasetItemRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(
        self,
        scope: ProjectScope,
        items: tuple[DatasetItem, ...],
    ) -> tuple[DatasetItem, ...]:
        for item in items:
            scope.ensure(item.project_id)
            self._session.add(
                DatasetItemModel(
                    id=item.dataset_item_id,
                    project_id=item.project_id,
                    dataset_version_id=item.dataset_version_id,
                    episode_id=item.episode_id,
                    episode_version=item.episode_version,
                    episode_lineage_id=item.episode_lineage_id,
                    manifest_sha256=item.manifest_sha256,
                    raw_asset_ids=[str(asset) for asset in item.raw_asset_ids],
                    annotation_submission_id=item.annotation_submission_id,
                    annotation_version=item.annotation_version,
                    annotation_record_digest=item.annotation_record_digest,
                    schema_id=item.schema_id,
                    schema_version=item.schema_version,
                    schema_sha256=item.schema_sha256,
                    split=enum_value(item.split),
                    stratum=item.stratum,
                    sample_role=enum_value(item.sample_role),
                    weight=item.weight,
                    dedup_group_id=item.dedup_group_id,
                    similarity_group_id=item.similarity_group_id,
                    scene_group_id=item.scene_group_id,
                    selected_reason=item.selected_reason,
                    item_sha256=item.item_sha256,
                    time_range=dict(item.time_range or {}) or None,
                    created_at=item.created_at or _now(),
                )
            )
        flush_or_duplicate(self._session, "数据集中存在重复条目")
        return items

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
    ) -> list[DatasetItem]:
        limit = bounded_limit(limit, maximum=101)
        statement = select(DatasetItemModel).where(
            DatasetItemModel.project_id == scope.project_id,
            DatasetItemModel.dataset_version_id == dataset_version_id,
        )
        if split is not None:
            statement = statement.where(DatasetItemModel.split == split)
        if sample_role is not None:
            statement = statement.where(DatasetItemModel.sample_role == sample_role)
        if episode_id is not None:
            statement = statement.where(DatasetItemModel.episode_id == episode_id)
        if after_id is not None:
            statement = statement.where(DatasetItemModel.id > after_id)
        statement = statement.order_by(DatasetItemModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def get(
        self,
        scope: ProjectScope,
        *,
        dataset_version_id: UUID,
        item_id: UUID,
    ) -> DatasetItem | None:
        row = self._session.scalar(
            select(DatasetItemModel).where(
                DatasetItemModel.id == item_id,
                DatasetItemModel.project_id == scope.project_id,
                DatasetItemModel.dataset_version_id == dataset_version_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list_all(
        self,
        scope: ProjectScope,
        dataset_version_id: UUID,
    ) -> list[DatasetItem]:
        statement = (
            select(DatasetItemModel)
            .where(
                DatasetItemModel.project_id == scope.project_id,
                DatasetItemModel.dataset_version_id == dataset_version_id,
            )
            .order_by(DatasetItemModel.id)
        )
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def _from_row(self, row: DatasetItemModel) -> DatasetItem:
        return DatasetItem(
            dataset_item_id=row.id,
            project_id=row.project_id,
            dataset_version_id=row.dataset_version_id,
            episode_id=row.episode_id,
            episode_version=row.episode_version,
            episode_lineage_id=row.episode_lineage_id,
            manifest_sha256=row.manifest_sha256,
            raw_asset_ids=tuple(UUID(item) for item in (row.raw_asset_ids or [])),
            annotation_submission_id=row.annotation_submission_id,
            annotation_version=row.annotation_version,
            annotation_record_digest=row.annotation_record_digest,
            schema_id=row.schema_id,
            schema_version=row.schema_version,
            schema_sha256=row.schema_sha256,
            split=Split(row.split),
            stratum=row.stratum,
            sample_role=SampleRole(row.sample_role),
            weight=row.weight,
            dedup_group_id=row.dedup_group_id,
            similarity_group_id=row.similarity_group_id,
            scene_group_id=row.scene_group_id,
            selected_reason=row.selected_reason,
            item_sha256=row.item_sha256,
            time_range=dict(row.time_range or {}) or None,
            created_at=as_utc(row.created_at),
        )
