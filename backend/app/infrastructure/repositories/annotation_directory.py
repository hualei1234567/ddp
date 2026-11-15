"""标注域只读目录适配：Episode 候选与项目成员。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.application.annotation.ports import EpisodeCandidate
from app.domain.annotation.errors import DomainValidationError
from app.domain.identity.models import ProjectRole, ProjectScope
from app.infrastructure.db.models.episodes import DataLineageEdgeModel, EpisodeModel
from app.infrastructure.db.models.identity import ProjectMember
from app.infrastructure.repositories._annotation_sql import as_utc


class SqlEpisodeDirectory:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_candidates(
        self,
        scope: ProjectScope,
        *,
        statuses: tuple[str, ...],
        quality_statuses: tuple[str, ...],
        include_ids: tuple[UUID, ...] = (),
        exclude_ids: tuple[UUID, ...] = (),
        captured_from: datetime | None = None,
        captured_to: datetime | None = None,
        after_id: UUID | None = None,
        limit: int = 500,
    ) -> list[EpisodeCandidate]:
        if limit < 1 or limit > 1001:
            raise DomainValidationError("limit 必须在 1 到 1001 之间")
        statement = select(EpisodeModel).where(
            EpisodeModel.project_id == scope.project_id,
            EpisodeModel.status.in_(statuses),
            EpisodeModel.quality_status.in_(quality_statuses),
        )
        if include_ids:
            statement = statement.where(EpisodeModel.id.in_(include_ids))
        if exclude_ids:
            statement = statement.where(EpisodeModel.id.not_in(exclude_ids))
        if captured_from is not None:
            statement = statement.where(EpisodeModel.created_at >= captured_from)
        if captured_to is not None:
            statement = statement.where(EpisodeModel.created_at <= captured_to)
        if after_id is not None:
            statement = statement.where(EpisodeModel.id > after_id)
        statement = statement.order_by(EpisodeModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def exclusion_summary(
        self,
        scope: ProjectScope,
        *,
        statuses: tuple[str, ...],
        quality_statuses: tuple[str, ...],
        include_ids: tuple[UUID, ...] = (),
        exclude_ids: tuple[UUID, ...] = (),
        captured_from: datetime | None = None,
        captured_to: datetime | None = None,
    ) -> Mapping[str, int]:
        statement = (
            select(EpisodeModel.status, EpisodeModel.quality_status, func.count())
            .where(
                EpisodeModel.project_id == scope.project_id,
                or_(
                    EpisodeModel.status.not_in(statuses),
                    EpisodeModel.quality_status.not_in(quality_statuses),
                ),
            )
            .group_by(EpisodeModel.status, EpisodeModel.quality_status)
        )
        if include_ids:
            statement = statement.where(EpisodeModel.id.in_(include_ids))
        if exclude_ids:
            statement = statement.where(EpisodeModel.id.not_in(exclude_ids))
        if captured_from is not None:
            statement = statement.where(EpisodeModel.created_at >= captured_from)
        if captured_to is not None:
            statement = statement.where(EpisodeModel.created_at <= captured_to)
        summary: dict[str, int] = {}
        for status, quality_status, count in self._session.execute(statement):
            # 状态不合格优先归因；否则归因到质量门禁，避免同一 Episode 重复计数。
            if status not in statuses:
                reason = f"episode_status:{status}"
            else:
                reason = f"quality_status:{quality_status}"
            summary[reason] = summary.get(reason, 0) + int(count)
        return summary

    def episode_bounds(
        self,
        scope: ProjectScope,
        *,
        episode_ids: tuple[UUID, ...],
    ) -> Mapping[UUID, tuple[int, int]]:
        if not episode_ids:
            return {}
        rows = self._session.execute(
            select(EpisodeModel.id, EpisodeModel.start_time_ns, EpisodeModel.end_time_ns).where(
                EpisodeModel.project_id == scope.project_id,
                EpisodeModel.id.in_(episode_ids),
            )
        )
        result: dict[UUID, tuple[int, int]] = {}
        for episode_id, start, end in rows:
            if start is None or end is None:
                continue
            result[episode_id] = (int(start), int(end))
        return result

    def list_by_ids(
        self,
        scope: ProjectScope,
        *,
        episode_ids: tuple[UUID, ...],
    ) -> list[EpisodeCandidate]:
        if not episode_ids:
            return []
        rows = self._session.scalars(
            select(EpisodeModel)
            .where(
                EpisodeModel.project_id == scope.project_id,
                EpisodeModel.id.in_(episode_ids),
            )
            .order_by(EpisodeModel.id)
        )
        return [self._from_row(row) for row in rows]

    def _from_row(self, row: EpisodeModel) -> EpisodeCandidate:
        has_lineage = bool(
            self._session.scalar(
                select(
                    exists().where(
                        DataLineageEdgeModel.project_id == row.project_id,
                        or_(
                            (
                                (DataLineageEdgeModel.from_node_type == "EPISODE")
                                & (DataLineageEdgeModel.from_node_id == row.id)
                            ),
                            (
                                (DataLineageEdgeModel.to_node_type == "EPISODE")
                                & (DataLineageEdgeModel.to_node_id == row.id)
                            ),
                        ),
                    )
                )
            )
        )
        return EpisodeCandidate(
            episode_id=row.id,
            episode_lineage_id=row.episode_lineage_id,
            episode_version=row.episode_version,
            status=row.status,
            quality_status=row.quality_status,
            manifest_sha256=row.manifest_sha256,
            scene_labels=tuple(row.scene_labels or ()),
            robot_id=row.robot_id,
            device_ids=tuple(UUID(item) for item in (row.device_ids or ())),
            sensor_ids=tuple(UUID(item) for item in (row.sensor_ids or ())),
            captured_at=as_utc(row.created_at),
            has_lineage=has_lineage,
            calibration_count=len(row.calibration_snapshot or ()),
        )


class SqlMemberDirectory:
    def __init__(self, session: Session) -> None:
        self._session = session

    def user_is_member(self, scope: ProjectScope, user_id: UUID) -> bool:
        return bool(
            self._session.scalar(
                select(
                    exists().where(
                        ProjectMember.project_id == scope.project_id,
                        ProjectMember.user_id == user_id,
                    )
                )
            )
        )

    def user_role(self, scope: ProjectScope, user_id: UUID) -> str | None:
        return self._session.scalar(
            select(ProjectMember.role).where(
                ProjectMember.project_id == scope.project_id,
                ProjectMember.user_id == user_id,
            )
        )


class SqlGoldSetAnswerAccessPolicy:
    """黄金集答案访问控制：仅项目 OWNER 可读，其余角色一律拒绝。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def can_view_answers(self, scope: ProjectScope, gold_set_id: UUID) -> bool:
        role = self._session.scalar(
            select(ProjectMember.role).where(
                ProjectMember.project_id == scope.project_id,
                ProjectMember.user_id == scope.actor_id,
            )
        )
        return role == ProjectRole.OWNER.value
