"""Episode 与 Episode-RawAsset SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.episodes.enums import (
    EpisodeAnnotationStatus,
    EpisodeQualityStatus,
    EpisodeStatus,
)
from app.domain.episodes.errors import (
    ConflictError,
    DomainValidationError,
    VersionConflictError,
)
from app.domain.episodes.filters import EpisodeListFilters
from app.domain.episodes.models import Episode, EpisodeRawAsset
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.episodes import EpisodeModel, EpisodeRawAssetModel
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.repositories._collection_sql import as_utc
from app.infrastructure.repositories._json_array_predicates import (
    json_array_any_of,
    session_dialect,
)

_TERMINAL_FOR_OVERLAP = ("INVALIDATED", "REJECTED")


class SqlEpisodeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, episode: Episode) -> Episode:
        scope.ensure(episode.project_id)
        if episode.version != 1:
            raise DomainValidationError("新增 Episode version 必须为 1")
        now = datetime.now(UTC)
        row = EpisodeModel(
            id=episode.episode_id,
            project_id=episode.project_id,
            episode_lineage_id=episode.episode_lineage_id,
            episode_version=episode.episode_version,
            code=episode.code,
            task_id=episode.task_id,
            batch_id=episode.batch_id,
            robot_id=episode.robot_id,
            device_ids=[str(item) for item in episode.device_ids],
            sensor_ids=[str(item) for item in episode.sensor_ids],
            status=episode.status.value,
            quality_status=episode.quality_status.value,
            annotation_status=episode.annotation_status.value,
            success=episode.success,
            failure_reason=episode.failure_reason,
            scene_labels=list(episode.scene_labels),
            language_instruction=episode.language_instruction,
            start_time_ns=episode.start_time_ns,
            end_time_ns=episode.end_time_ns,
            duration_ns=episode.duration_ns,
            time_domain="UTC",
            time_base=dict(episode.time_base),
            canonical_schema_version=episode.canonical_schema_version,
            pipeline_id=episode.pipeline_id,
            pipeline_version=episode.pipeline_version,
            segmentation_rule_version=episode.segmentation_rule_version,
            frame_count=episode.frame_count,
            calibration_snapshot=[dict(item) for item in episode.calibration_snapshot],
            manifest_key=episode.manifest_key,
            manifest_sha256=episode.manifest_sha256,
            readiness_blockers=[item.as_dict() for item in episode.readiness_blockers],
            preview_refs=[dict(item) for item in episode.preview_refs],
            supersedes_id=episode.supersedes_id,
            attempt=episode.attempt,
            max_attempts=episode.max_attempts,
            version=episode.version,
            created_at=episode.created_at or now,
            updated_at=episode.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError as exc:
            message = str(exc.orig).lower()
            if any(token in message for token in ("unique", "duplicate", "uq_")):
                raise ConflictError("同项目 Episode 编号、谱系版本或前驱已存在") from exc
            raise
        return episode

    def get(self, scope: ProjectScope, episode_id: UUID) -> Episode | None:
        row = self._session.scalar(
            select(EpisodeModel).where(
                EpisodeModel.id == episode_id,
                EpisodeModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_code(self, scope: ProjectScope, code: str) -> Episode | None:
        row = self._session.scalar(
            select(EpisodeModel).where(
                EpisodeModel.project_id == scope.project_id,
                EpisodeModel.code == code,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_lineage_version(
        self,
        scope: ProjectScope,
        *,
        episode_lineage_id: UUID,
        episode_version: int,
    ) -> Episode | None:
        row = self._session.scalar(
            select(EpisodeModel).where(
                EpisodeModel.project_id == scope.project_id,
                EpisodeModel.episode_lineage_id == episode_lineage_id,
                EpisodeModel.episode_version == episode_version,
            )
        )
        return self._from_row(row) if row is not None else None

    def count_by_task(self, scope: ProjectScope, task_id: UUID) -> dict[str, int]:
        rows = self._session.execute(
            select(EpisodeModel.status, func.count())
            .where(
                EpisodeModel.project_id == scope.project_id,
                EpisodeModel.task_id == task_id,
            )
            .group_by(EpisodeModel.status)
        ).all()
        counts = {str(status): int(count) for status, count in rows}
        accepted = counts.get("ACCEPTED", 0) + counts.get("COMPLETED", 0)
        return {"total": sum(counts.values()), "accepted": accepted}

    def counts_by_task(
        self,
        scope: ProjectScope,
        task_ids: list[UUID],
    ) -> dict[UUID, dict[str, int]]:
        """批量按任务统计：列表页一次 GROUP BY 取完，不做 N+1 查询。"""
        if not task_ids:
            return {}
        rows = self._session.execute(
            select(EpisodeModel.task_id, EpisodeModel.status, func.count())
            .where(
                EpisodeModel.project_id == scope.project_id,
                EpisodeModel.task_id.in_(task_ids),
            )
            .group_by(EpisodeModel.task_id, EpisodeModel.status)
        ).all()
        result: dict[UUID, dict[str, int]] = {
            task_id: {"total": 0, "accepted": 0} for task_id in task_ids
        }
        for task_id, status, count in rows:
            if task_id is None:
                continue
            bucket = result.setdefault(task_id, {"total": 0, "accepted": 0})
            bucket["total"] += int(count)
            if str(status) in ("ACCEPTED", "COMPLETED"):
                bucket["accepted"] += int(count)
        return result

    def list(
        self,
        scope: ProjectScope,
        *,
        status: EpisodeStatus | None = None,
        task_id: UUID | None = None,
        batch_id: UUID | None = None,
        filters: EpisodeListFilters | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Episode]:
        """按游标分页列出 Episode；多维条件全部在 SQL 层下推。

        过滤不能放到 Python 侧：一旦先按 ``limit`` 取回再筛，游标语义会失真
        （返回 0 条却仍给出 next_cursor，或跳过本应命中的行）。
        """
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = select(EpisodeModel).where(EpisodeModel.project_id == scope.project_id)
        if status is not None:
            statement = statement.where(EpisodeModel.status == status.value)
        if task_id is not None:
            statement = statement.where(EpisodeModel.task_id == task_id)
        if batch_id is not None:
            statement = statement.where(EpisodeModel.batch_id == batch_id)
        if filters is not None:
            statement = self._apply_filters(statement, filters)
        if after_id is not None:
            statement = statement.where(EpisodeModel.id > after_id)
        statement = statement.order_by(EpisodeModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def _apply_filters(
        self,
        statement: Select[tuple[EpisodeModel]],
        filters: EpisodeListFilters,
    ) -> Select[tuple[EpisodeModel]]:
        """把多维检索条件翻译成 WHERE 子句（不取数、不内存筛选）。"""
        dialect = session_dialect(self._session)
        if filters.quality_status is not None:
            statement = statement.where(EpisodeModel.quality_status == filters.quality_status.value)
        if filters.annotation_status is not None:
            statement = statement.where(
                EpisodeModel.annotation_status == filters.annotation_status.value
            )
        if filters.scene_labels:
            statement = statement.where(
                json_array_any_of(
                    EpisodeModel.scene_labels,
                    filters.scene_labels,
                    dialect=dialect,
                )
            )
        if filters.device_id is not None:
            # device_ids 以 str(UUID) 存储，比较值必须同形。
            statement = statement.where(
                json_array_any_of(
                    EpisodeModel.device_ids,
                    (str(filters.device_id),),
                    dialect=dialect,
                )
            )
        if filters.robot_id is not None:
            statement = statement.where(EpisodeModel.robot_id == filters.robot_id)
        if filters.camera is not None:
            statement = statement.where(self._camera_predicate(filters.camera))
        if filters.captured_from is not None or filters.captured_to is not None:
            statement = statement.where(
                self._captured_at_predicate(filters.captured_from, filters.captured_to)
            )
        return statement

    def _camera_predicate(self, camera: str) -> ColumnElement[bool]:
        """机位条件：任一关联原始资产的来源事实 ``raw_metadata.camera`` 命中。

        机位事实由导入链写入 ``RawAsset.raw_metadata``（
        ``import_runner._import_metadata``），与自动就绪判定读取的是同一份
        Canonical 事实。用相关 ``EXISTS`` 表达，条件留在 SQL 里；没有该事实的
        资产（例如上传类）自然不命中，不会被当成任何机位。
        """
        return exists(
            select(1)
            .select_from(EpisodeRawAssetModel)
            .join(
                RawAssetModel,
                and_(
                    RawAssetModel.project_id == EpisodeRawAssetModel.project_id,
                    RawAssetModel.id == EpisodeRawAssetModel.asset_id,
                ),
            )
            .where(
                EpisodeRawAssetModel.project_id == EpisodeModel.project_id,
                EpisodeRawAssetModel.episode_id == EpisodeModel.id,
                RawAssetModel.raw_metadata["camera"].as_string() == camera,
            )
        ).correlate(EpisodeModel)

    def _captured_at_predicate(
        self,
        captured_from: datetime | None,
        captured_to: datetime | None,
    ) -> ColumnElement[bool]:
        """捕获时间区间条件：取 Episode 关联资产 ``captured_at`` 的最早值判定。

        用相关子查询表达，条件留在 SQL 里；无捕获时间的 Episode 自然不命中
        （``MIN`` 为 NULL，比较结果为 NULL → 不满足），与
        ``SelectionFilters.matches`` 的语义一致。
        """
        earliest = (
            select(func.min(RawAssetModel.captured_at))
            .select_from(EpisodeRawAssetModel)
            .join(
                RawAssetModel,
                and_(
                    RawAssetModel.project_id == EpisodeRawAssetModel.project_id,
                    RawAssetModel.id == EpisodeRawAssetModel.asset_id,
                ),
            )
            .where(
                EpisodeRawAssetModel.project_id == EpisodeModel.project_id,
                EpisodeRawAssetModel.episode_id == EpisodeModel.id,
            )
            .correlate(EpisodeModel)
            .scalar_subquery()
        )
        conditions = []
        if captured_from is not None:
            conditions.append(earliest >= captured_from)
        if captured_to is not None:
            conditions.append(earliest <= captured_to)
        if not conditions:  # 调用方保证至少一条；显式拒绝空条件掩盖缺陷。
            raise DomainValidationError("captured_at 条件至少需要一端")
        predicate = conditions[0]
        for extra in conditions[1:]:
            predicate = and_(predicate, extra)
        return predicate

    def save(
        self,
        scope: ProjectScope,
        episode: Episode,
        *,
        expected_version: int,
    ) -> Episode:
        scope.ensure(episode.project_id)
        if episode.version != expected_version:
            raise VersionConflictError("Episode")
        now = episode.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(EpisodeModel)
            .where(
                EpisodeModel.id == episode.episode_id,
                EpisodeModel.project_id == scope.project_id,
                EpisodeModel.version == expected_version,
            )
            .values(
                task_id=episode.task_id,
                batch_id=episode.batch_id,
                robot_id=episode.robot_id,
                device_ids=[str(item) for item in episode.device_ids],
                sensor_ids=[str(item) for item in episode.sensor_ids],
                status=episode.status.value,
                quality_status=episode.quality_status.value,
                annotation_status=episode.annotation_status.value,
                success=episode.success,
                failure_reason=episode.failure_reason,
                scene_labels=list(episode.scene_labels),
                language_instruction=episode.language_instruction,
                start_time_ns=episode.start_time_ns,
                end_time_ns=episode.end_time_ns,
                duration_ns=episode.duration_ns,
                time_domain="UTC",
                time_base=dict(episode.time_base),
                canonical_schema_version=episode.canonical_schema_version,
                pipeline_id=episode.pipeline_id,
                pipeline_version=episode.pipeline_version,
                segmentation_rule_version=episode.segmentation_rule_version,
                frame_count=episode.frame_count,
                calibration_snapshot=[dict(item) for item in episode.calibration_snapshot],
                manifest_key=episode.manifest_key,
                manifest_sha256=episode.manifest_sha256,
                readiness_blockers=[item.as_dict() for item in episode.readiness_blockers],
                preview_refs=[dict(item) for item in episode.preview_refs],
                supersedes_id=episode.supersedes_id,
                attempt=episode.attempt,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise VersionConflictError("Episode")
        return replace(
            episode,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: EpisodeModel) -> Episode:
        return Episode(
            episode_id=row.id,
            project_id=row.project_id,
            episode_lineage_id=row.episode_lineage_id,
            episode_version=row.episode_version,
            code=row.code,
            task_id=row.task_id,
            batch_id=row.batch_id,
            robot_id=row.robot_id,
            device_ids=tuple(UUID(item) for item in (row.device_ids or ())),
            sensor_ids=tuple(UUID(item) for item in (row.sensor_ids or ())),
            status=EpisodeStatus(row.status),
            quality_status=EpisodeQualityStatus(row.quality_status),
            annotation_status=EpisodeAnnotationStatus(row.annotation_status),
            success=row.success,
            failure_reason=row.failure_reason,
            scene_labels=tuple(row.scene_labels or ()),
            language_instruction=row.language_instruction,
            start_time_ns=row.start_time_ns,
            end_time_ns=row.end_time_ns,
            time_base=dict(row.time_base or {}),
            canonical_schema_version=row.canonical_schema_version,
            pipeline_id=row.pipeline_id,
            pipeline_version=row.pipeline_version,
            segmentation_rule_version=row.segmentation_rule_version,
            frame_count=row.frame_count,
            calibration_snapshot=tuple(row.calibration_snapshot or ()),
            manifest_key=row.manifest_key,
            manifest_sha256=row.manifest_sha256,
            readiness_blockers=tuple(row.readiness_blockers or ()),
            preview_refs=tuple(row.preview_refs or ()),
            supersedes_id=row.supersedes_id,
            attempt=row.attempt,
            max_attempts=row.max_attempts,
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )


class SqlEpisodeRawAssetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(
        self,
        scope: ProjectScope,
        rows: list[EpisodeRawAsset],
    ) -> list[EpisodeRawAsset]:
        if not rows:
            return []
        for item in rows:
            scope.ensure(item.project_id)
        try:
            with self._session.begin_nested():
                self._session.add_all(
                    [
                        EpisodeRawAssetModel(
                            project_id=item.project_id,
                            episode_id=item.episode_id,
                            asset_id=item.asset_id,
                            role=item.role,
                            start_time_ns=item.start_time_ns,
                            end_time_ns=item.end_time_ns,
                            sha256_snapshot=item.sha256_snapshot,
                            size_bytes_snapshot=item.size_bytes_snapshot,
                        )
                        for item in rows
                    ]
                )
                self._session.flush()
        except IntegrityError as exc:
            message = str(exc.orig).lower()
            if any(token in message for token in ("unique", "duplicate", "pk_", "foreign")):
                raise ConflictError("Episode 原始资产关系重复或引用无效") from exc
            raise
        return rows

    def list_for_episode(
        self,
        scope: ProjectScope,
        episode_id: UUID,
    ) -> list[EpisodeRawAsset]:
        rows = self._session.scalars(
            select(EpisodeRawAssetModel)
            .where(
                EpisodeRawAssetModel.project_id == scope.project_id,
                EpisodeRawAssetModel.episode_id == episode_id,
            )
            .order_by(EpisodeRawAssetModel.asset_id, EpisodeRawAssetModel.role)
        )
        return [self._from_row(row) for row in rows]

    def list_for_asset(
        self,
        scope: ProjectScope,
        asset_id: UUID,
    ) -> list[EpisodeRawAsset]:
        rows = self._session.scalars(
            select(EpisodeRawAssetModel)
            .where(
                EpisodeRawAssetModel.project_id == scope.project_id,
                EpisodeRawAssetModel.asset_id == asset_id,
            )
            .order_by(EpisodeRawAssetModel.episode_id, EpisodeRawAssetModel.role)
        )
        return [self._from_row(row) for row in rows]

    def find_overlaps(
        self,
        scope: ProjectScope,
        *,
        asset_id: UUID,
        start_time_ns: int,
        end_time_ns: int,
        exclude_episode_id: UUID | None = None,
    ) -> list[EpisodeRawAsset]:
        statement = (
            select(EpisodeRawAssetModel)
            .join(
                EpisodeModel,
                and_(
                    EpisodeModel.project_id == EpisodeRawAssetModel.project_id,
                    EpisodeModel.id == EpisodeRawAssetModel.episode_id,
                ),
            )
            .where(
                EpisodeRawAssetModel.project_id == scope.project_id,
                EpisodeRawAssetModel.asset_id == asset_id,
                EpisodeRawAssetModel.start_time_ns.is_not(None),
                EpisodeRawAssetModel.end_time_ns.is_not(None),
                EpisodeRawAssetModel.start_time_ns < end_time_ns,
                EpisodeRawAssetModel.end_time_ns > start_time_ns,
                EpisodeModel.status.not_in(_TERMINAL_FOR_OVERLAP),
            )
        )
        if exclude_episode_id is not None:
            statement = statement.where(EpisodeRawAssetModel.episode_id != exclude_episode_id)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def _from_row(self, row: EpisodeRawAssetModel) -> EpisodeRawAsset:
        return EpisodeRawAsset(
            project_id=row.project_id,
            episode_id=row.episode_id,
            asset_id=row.asset_id,
            role=row.role,
            sha256_snapshot=row.sha256_snapshot,
            size_bytes_snapshot=row.size_bytes_snapshot,
            start_time_ns=row.start_time_ns,
            end_time_ns=row.end_time_ns,
        )
