"""标注分配与分配事件 SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, exists, func, select, update
from sqlalchemy.orm import Session

from app.application.annotation.ports import WorkloadAggregate
from app.domain.annotation.assignment import AnnotationAssignment
from app.domain.annotation.errors import DomainValidationError
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_work import (
    AnnotationAssignment as AnnotationAssignmentModel,
)
from app.infrastructure.db.models.annotation_work import AnnotationAssignmentEvent
from app.infrastructure.repositories._annotation_sql import (
    as_utc,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    raise_update_failure,
    scoped_select,
)

_TERMINAL_STATUSES = ("COMPLETED", "REVOKED", "EXPIRED")
_OPEN_STATUSES = ("ASSIGNED", "CLAIMED", "IN_PROGRESS", "SUBMITTED")


class SqlAnnotationAssignmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        scope: ProjectScope,
        assignment: AnnotationAssignment,
    ) -> AnnotationAssignment:
        scope.ensure(assignment.project_id)
        ensure_initial_version(assignment.version)
        now = datetime.now(UTC)
        row = AnnotationAssignmentModel(
            id=assignment.assignment_id,
            project_id=assignment.project_id,
            task_id=assignment.task_id,
            batch_id=assignment.batch_id,
            assignee_id=assignment.assignee_id,
            assignment_role=assignment.assignment_role.value,
            status=enum_value(assignment.status),
            planned_item_count=assignment.planned_item_count,
            claimed_item_count=assignment.claimed_item_count,
            submitted_item_count=assignment.submitted_item_count,
            approved_item_count=assignment.approved_item_count,
            planned_minutes=assignment.planned_minutes,
            actual_minutes=assignment.actual_minutes,
            lease_expires_at=assignment.lease_expires_at,
            claimed_at=assignment.claimed_at,
            submitted_at=assignment.submitted_at,
            completed_at=assignment.completed_at,
            revoked_reason=assignment.revoked_reason,
            status_reason=assignment.status_reason,
            version=assignment.version,
            created_at=assignment.created_at or now,
            updated_at=assignment.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session)
        return assignment

    def get(
        self,
        scope: ProjectScope,
        assignment_id: UUID,
    ) -> AnnotationAssignment | None:
        row = self._session.scalar(
            select(AnnotationAssignmentModel).where(
                AnnotationAssignmentModel.id == assignment_id,
                AnnotationAssignmentModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID | None = None,
        batch_id: UUID | None = None,
        assignee_id: UUID | None = None,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 100,
    ) -> list[AnnotationAssignment]:
        if limit < 1 or limit > 501:
            raise DomainValidationError("limit 必须在 1 到 501 之间")
        statement = scoped_select(AnnotationAssignmentModel, scope)
        if task_id is not None:
            statement = statement.where(AnnotationAssignmentModel.task_id == task_id)
        if batch_id is not None:
            statement = statement.where(AnnotationAssignmentModel.batch_id == batch_id)
        if assignee_id is not None:
            statement = statement.where(AnnotationAssignmentModel.assignee_id == assignee_id)
        if status is not None:
            statement = statement.where(AnnotationAssignmentModel.status == status)
        if after_id is not None:
            statement = statement.where(AnnotationAssignmentModel.id > after_id)
        statement = statement.order_by(AnnotationAssignmentModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        assignment: AnnotationAssignment,
        *,
        expected_version: int,
    ) -> AnnotationAssignment:
        scope.ensure(assignment.project_id)
        ensure_expected_version(assignment.version, expected_version)
        now = assignment.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(AnnotationAssignmentModel)
            .where(
                AnnotationAssignmentModel.id == assignment.assignment_id,
                AnnotationAssignmentModel.project_id == scope.project_id,
                AnnotationAssignmentModel.version == expected_version,
            )
            .values(
                status=enum_value(assignment.status),
                planned_item_count=assignment.planned_item_count,
                claimed_item_count=assignment.claimed_item_count,
                submitted_item_count=assignment.submitted_item_count,
                approved_item_count=assignment.approved_item_count,
                planned_minutes=assignment.planned_minutes,
                actual_minutes=assignment.actual_minutes,
                lease_expires_at=assignment.lease_expires_at,
                claimed_at=assignment.claimed_at,
                submitted_at=assignment.submitted_at,
                completed_at=assignment.completed_at,
                revoked_reason=assignment.revoked_reason,
                status_reason=assignment.status_reason,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                AnnotationAssignmentModel,
                scope,
                assignment.assignment_id,
                "标注分配",
            )
        return replace(
            assignment,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def list_expired_leases(
        self,
        now: datetime,
        *,
        statuses: tuple[str, ...],
        after_id: UUID | None = None,
        limit: int = 200,
    ) -> list[tuple[UUID, UUID]]:
        """跨项目列出已到租约的分配 ``(assignment_id, project_id)``，供系统任务扫描。

        只返回 ID；调用方必须用 ``ProjectScope(project_id, ...)`` 重新读取与写入，
        项目隔离仍由仓储强制。
        """
        statement = select(
            AnnotationAssignmentModel.id, AnnotationAssignmentModel.project_id
        ).where(
            AnnotationAssignmentModel.status.in_(statuses),
            AnnotationAssignmentModel.lease_expires_at.is_not(None),
            AnnotationAssignmentModel.lease_expires_at <= now,
        )
        if after_id is not None:
            statement = statement.where(AnnotationAssignmentModel.id > after_id)
        statement = statement.order_by(AnnotationAssignmentModel.id).limit(limit)
        return [(row[0], row[1]) for row in self._session.execute(statement)]

    def list_for_batch(
        self,
        scope: ProjectScope,
        *,
        batch_id: UUID,
    ) -> list[AnnotationAssignment]:
        rows = self._session.scalars(
            scoped_select(AnnotationAssignmentModel, scope)
            .where(AnnotationAssignmentModel.batch_id == batch_id)
            .order_by(AnnotationAssignmentModel.id)
        )
        return [self._from_row(row) for row in rows]

    def list_active_for_task(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
    ) -> list[AnnotationAssignment]:
        rows = self._session.scalars(
            scoped_select(AnnotationAssignmentModel, scope)
            .where(
                AnnotationAssignmentModel.task_id == task_id,
                AnnotationAssignmentModel.status.not_in(_TERMINAL_STATUSES),
            )
            .order_by(AnnotationAssignmentModel.id)
        )
        return [self._from_row(row) for row in rows]

    def has_conflicting_role(
        self,
        scope: ProjectScope,
        *,
        batch_id: UUID,
        assignee_id: UUID,
        assignment_role: str,
    ) -> bool:
        return bool(
            self._session.scalar(
                select(
                    exists().where(
                        AnnotationAssignmentModel.project_id == scope.project_id,
                        AnnotationAssignmentModel.batch_id == batch_id,
                        AnnotationAssignmentModel.assignee_id == assignee_id,
                        AnnotationAssignmentModel.assignment_role != assignment_role,
                    )
                )
            )
        )

    def sum_open_planned_minutes(
        self,
        scope: ProjectScope,
        *,
        assignee_id: UUID,
        exclude_assignment_id: UUID | None = None,
    ) -> int:
        statement = select(
            func.coalesce(func.sum(AnnotationAssignmentModel.planned_minutes), 0)
        ).where(
            AnnotationAssignmentModel.project_id == scope.project_id,
            AnnotationAssignmentModel.assignee_id == assignee_id,
            AnnotationAssignmentModel.status.in_(_OPEN_STATUSES),
        )
        if exclude_assignment_id is not None:
            statement = statement.where(AnnotationAssignmentModel.id != exclude_assignment_id)
        return int(self._session.scalar(statement) or 0)

    def sum_open_planned_items(
        self,
        scope: ProjectScope,
        *,
        assignee_id: UUID,
        exclude_assignment_id: UUID | None = None,
    ) -> int:
        statement = select(
            func.coalesce(func.sum(AnnotationAssignmentModel.planned_item_count), 0)
        ).where(
            AnnotationAssignmentModel.project_id == scope.project_id,
            AnnotationAssignmentModel.assignee_id == assignee_id,
            AnnotationAssignmentModel.status.in_(_OPEN_STATUSES),
        )
        if exclude_assignment_id is not None:
            statement = statement.where(AnnotationAssignmentModel.id != exclude_assignment_id)
        return int(self._session.scalar(statement) or 0)

    def add_event(
        self,
        scope: ProjectScope,
        *,
        event_id: UUID,
        assignment: AnnotationAssignment,
        action: str,
        from_status: str | None,
        actor_id: UUID,
        reason: str | None,
        minutes: int,
        occurred_at: datetime,
    ) -> None:
        scope.ensure(assignment.project_id)
        self._session.add(
            AnnotationAssignmentEvent(
                id=event_id,
                project_id=assignment.project_id,
                assignment_id=assignment.assignment_id,
                task_id=assignment.task_id,
                batch_id=assignment.batch_id,
                assignee_id=assignment.assignee_id,
                action=action,
                from_status=from_status,
                to_status=assignment.status.value,
                actor_id=actor_id,
                reason=reason,
                planned_item_count=assignment.planned_item_count,
                submitted_item_count=assignment.submitted_item_count,
                approved_item_count=assignment.approved_item_count,
                minutes=minutes,
                occurred_at=occurred_at,
            )
        )
        self._session.flush()

    def aggregate_workload(
        self,
        scope: ProjectScope,
        *,
        assignee_id: UUID,
        period_start: datetime,
        period_end: datetime,
    ) -> WorkloadAggregate:
        statement = select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            AnnotationAssignmentEvent.action == "assign",
                            AnnotationAssignmentEvent.planned_item_count,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            AnnotationAssignmentEvent.action == "claim",
                            AnnotationAssignmentEvent.planned_item_count,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            AnnotationAssignmentEvent.action == "submit",
                            AnnotationAssignmentEvent.submitted_item_count,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            AnnotationAssignmentEvent.action == "complete",
                            AnnotationAssignmentEvent.approved_item_count,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            AnnotationAssignmentEvent.action == "rework",
                            AnnotationAssignmentEvent.planned_item_count,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(func.sum(AnnotationAssignmentEvent.minutes), 0),
        ).where(
            AnnotationAssignmentEvent.project_id == scope.project_id,
            AnnotationAssignmentEvent.assignee_id == assignee_id,
            AnnotationAssignmentEvent.occurred_at >= period_start,
            AnnotationAssignmentEvent.occurred_at <= period_end,
        )
        row = self._session.execute(statement).one()
        return WorkloadAggregate(
            assigned_item_count=int(row[0] or 0),
            claimed_item_count=int(row[1] or 0),
            submitted_item_count=int(row[2] or 0),
            approved_item_count=int(row[3] or 0),
            rework_item_count=int(row[4] or 0),
            total_minutes=int(row[5] or 0),
        )

    def _from_row(self, row: AnnotationAssignmentModel) -> AnnotationAssignment:
        return AnnotationAssignment(
            assignment_id=row.id,
            project_id=row.project_id,
            task_id=row.task_id,
            batch_id=row.batch_id,
            assignee_id=row.assignee_id,
            assignment_role=row.assignment_role,
            status=row.status,
            planned_item_count=row.planned_item_count,
            claimed_item_count=row.claimed_item_count,
            submitted_item_count=row.submitted_item_count,
            approved_item_count=row.approved_item_count,
            planned_minutes=row.planned_minutes,
            actual_minutes=row.actual_minutes,
            lease_expires_at=as_utc(row.lease_expires_at),
            claimed_at=as_utc(row.claimed_at),
            submitted_at=as_utc(row.submitted_at),
            completed_at=as_utc(row.completed_at),
            revoked_reason=row.revoked_reason,
            status_reason=row.status_reason,
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )
