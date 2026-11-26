"""采集任务 SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.domain.collection.enums import CollectionTaskStatus
from app.domain.collection.errors import DomainValidationError
from app.domain.collection.models import CollectionTask
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import (
    CollectionTask as CollectionTaskModel,
)
from app.infrastructure.db.models.collection import (
    CollectionTaskDevice,
    CollectionTaskSensor,
)
from app.infrastructure.repositories._collection_sql import (
    as_utc,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    raise_update_failure,
    scoped_select,
)


class SqlCollectionTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, task: CollectionTask) -> CollectionTask:
        scope.ensure(task.project_id)
        ensure_initial_version(task.version)
        now = datetime.now(UTC)
        row = CollectionTaskModel(
            id=task.task_id,
            project_id=task.project_id,
            code=task.code,
            name=task.name,
            requirement_id=task.requirement_id,
            plan_id=task.plan_id,
            sop_id=task.sop_id,
            sop_version=task.sop_version,
            scenario_id=task.scenario_id,
            action_spec_id=task.action_spec_id,
            action_spec_version=task.action_spec_version,
            status=enum_value(task.status),
            priority=enum_value(task.priority),
            assigned_to=task.assigned_to,
            assigned_robot_id=task.assigned_robot_id,
            location_id=task.location_id,
            scheduled_start=task.scheduled_start,
            scheduled_end=task.scheduled_end,
            actual_start=task.actual_start,
            actual_end=task.actual_end,
            target_episode_count=task.target_episode_count,
            target_duration_sec=task.target_duration_sec,
            submitted_episode_count=task.submitted_episode_count,
            accepted_episode_count=task.accepted_episode_count,
            quality_summary=dict(task.quality_summary),
            status_reason=task.status_reason,
            blocked_reason=task.blocked_reason,
            created_at=task.created_at or now,
            updated_at=task.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session)
        self._replace_assignments(scope, task, now=now)
        return task

    def get(self, scope: ProjectScope, task_id: UUID) -> CollectionTask | None:
        row = self._session.scalar(
            select(CollectionTaskModel).where(
                CollectionTaskModel.id == task_id,
                CollectionTaskModel.project_id == scope.project_id,
            )
        )
        if row is None:
            return None
        return self._from_row(scope, row)

    def get_by_code(self, scope: ProjectScope, code: str) -> CollectionTask | None:
        row = self._session.scalar(
            select(CollectionTaskModel).where(
                CollectionTaskModel.code == code.strip(),
                CollectionTaskModel.project_id == scope.project_id,
            )
        )
        if row is None:
            return None
        return self._from_row(scope, row)

    def list(
        self,
        scope: ProjectScope,
        *,
        status: CollectionTaskStatus | None = None,
        assigned_to: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[CollectionTask]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = scoped_select(CollectionTaskModel, scope)
        if status is not None:
            statement = statement.where(CollectionTaskModel.status == status.value)
        if assigned_to is not None:
            statement = statement.where(CollectionTaskModel.assigned_to == assigned_to)
        if after_id is not None:
            statement = statement.where(CollectionTaskModel.id > after_id)
        statement = statement.order_by(CollectionTaskModel.id).limit(limit)
        rows = list(self._session.scalars(statement))
        # V52-035：批量预取关联 ID 再构造领域对象。原实现每行各查 2 次
        # （device / sensor），列表页大小 N 时是 2N+1 次查询——压测实测
        # collection-tasks limit 50 达 87.7ms（docs/testing/evidence/
        # 2026-11-26-prod-readonly-perf/）。改为固定 3 次查询，与页大小无关。
        if not rows:
            return []
        device_map = self._assignment_ids_bulk(
            CollectionTaskDevice, "device_id", scope, [row.id for row in rows]
        )
        sensor_map = self._assignment_ids_bulk(
            CollectionTaskSensor, "sensor_id", scope, [row.id for row in rows]
        )
        return [
            self._from_row(
                scope,
                row,
                device_ids=device_map.get(row.id, ()),
                sensor_ids=sensor_map.get(row.id, ()),
            )
            for row in rows
        ]

    def list_pending_status(
        self,
        status: CollectionTaskStatus,
        *,
        after_id: UUID | None = None,
        limit: int = 100,
    ) -> list[tuple[UUID, UUID]]:
        """跨项目列出某状态的任务 ``(task_id, project_id)``，供系统任务扫描。

        系统任务（数据检查、租约过期）不属于任何单个项目，无法用
        ``scoped_select``。这里只返回 ID，不返回业务字段——调用方必须自己用
        ``ProjectScope(project_id, ...)`` 重新读取，项目隔离仍由仓储强制。
        """
        statement = select(CollectionTaskModel.id, CollectionTaskModel.project_id).where(
            CollectionTaskModel.status == status.value
        )
        if after_id is not None:
            statement = statement.where(CollectionTaskModel.id > after_id)
        statement = statement.order_by(CollectionTaskModel.id).limit(limit)
        return [(row[0], row[1]) for row in self._session.execute(statement)]

    def save(
        self,
        scope: ProjectScope,
        task: CollectionTask,
        *,
        expected_version: int,
    ) -> CollectionTask:
        scope.ensure(task.project_id)
        ensure_expected_version(task.version, expected_version)
        now = task.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(CollectionTaskModel)
            .where(
                CollectionTaskModel.id == task.task_id,
                CollectionTaskModel.project_id == scope.project_id,
                CollectionTaskModel.version == expected_version,
            )
            .values(
                code=task.code,
                name=task.name,
                requirement_id=task.requirement_id,
                plan_id=task.plan_id,
                sop_id=task.sop_id,
                sop_version=task.sop_version,
                scenario_id=task.scenario_id,
                action_spec_id=task.action_spec_id,
                action_spec_version=task.action_spec_version,
                status=enum_value(task.status),
                priority=enum_value(task.priority),
                assigned_to=task.assigned_to,
                assigned_robot_id=task.assigned_robot_id,
                location_id=task.location_id,
                scheduled_start=task.scheduled_start,
                scheduled_end=task.scheduled_end,
                actual_start=task.actual_start,
                actual_end=task.actual_end,
                target_episode_count=task.target_episode_count,
                target_duration_sec=task.target_duration_sec,
                submitted_episode_count=task.submitted_episode_count,
                accepted_episode_count=task.accepted_episode_count,
                quality_summary=dict(task.quality_summary),
                status_reason=task.status_reason,
                blocked_reason=task.blocked_reason,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                CollectionTaskModel,
                scope,
                task.task_id,
                "采集任务",
            )
        self._replace_assignments(scope, task, now=now)
        return replace(
            task,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _replace_assignments(
        self,
        scope: ProjectScope,
        task: CollectionTask,
        *,
        now: datetime,
    ) -> None:
        self._session.execute(
            delete(CollectionTaskDevice).where(
                CollectionTaskDevice.project_id == scope.project_id,
                CollectionTaskDevice.task_id == task.task_id,
            )
        )
        self._session.execute(
            delete(CollectionTaskSensor).where(
                CollectionTaskSensor.project_id == scope.project_id,
                CollectionTaskSensor.task_id == task.task_id,
            )
        )
        self._session.add_all(
            [
                CollectionTaskDevice(
                    project_id=scope.project_id,
                    task_id=task.task_id,
                    device_id=device_id,
                    assigned_by=scope.actor_id,
                    assigned_at=now,
                )
                for device_id in task.assigned_device_ids
            ]
        )
        self._session.add_all(
            [
                CollectionTaskSensor(
                    project_id=scope.project_id,
                    task_id=task.task_id,
                    sensor_id=sensor_id,
                    assigned_by=scope.actor_id,
                    assigned_at=now,
                )
                for sensor_id in task.assigned_sensor_ids
            ]
        )
        self._session.flush()

    def _from_row(
        self,
        scope: ProjectScope,
        row: CollectionTaskModel,
        *,
        device_ids: tuple[UUID, ...] | None = None,
        sensor_ids: tuple[UUID, ...] | None = None,
    ) -> CollectionTask:
        """行 → 领域对象。

        ``device_ids`` / ``sensor_ids`` 为 ``None`` 时按单行查询（详情路径沿用
        旧行为）；列表路径传入批量预取结果，避免 N+1。
        """
        return CollectionTask(
            task_id=row.id,
            project_id=row.project_id,
            code=row.code,
            name=row.name,
            priority=row.priority,
            status=CollectionTaskStatus(row.status),
            requirement_id=row.requirement_id,
            plan_id=row.plan_id,
            sop_id=row.sop_id,
            sop_version=row.sop_version,
            scenario_id=row.scenario_id,
            action_spec_id=row.action_spec_id,
            action_spec_version=row.action_spec_version,
            assigned_to=row.assigned_to,
            assigned_robot_id=row.assigned_robot_id,
            assigned_device_ids=(
                device_ids
                if device_ids is not None
                else self._assignment_ids(CollectionTaskDevice, "device_id", scope, row.id)
            ),
            assigned_sensor_ids=(
                sensor_ids
                if sensor_ids is not None
                else self._assignment_ids(CollectionTaskSensor, "sensor_id", scope, row.id)
            ),
            location_id=row.location_id,
            scheduled_start=as_utc(row.scheduled_start),
            scheduled_end=as_utc(row.scheduled_end),
            actual_start=as_utc(row.actual_start),
            actual_end=as_utc(row.actual_end),
            target_episode_count=row.target_episode_count,
            target_duration_sec=row.target_duration_sec,
            submitted_episode_count=row.submitted_episode_count,
            accepted_episode_count=row.accepted_episode_count,
            quality_summary=row.quality_summary or {},
            status_reason=row.status_reason,
            blocked_reason=row.blocked_reason,
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )

    def _assignment_ids(
        self,
        model: type,
        column_name: str,
        scope: ProjectScope,
        task_id: UUID,
    ) -> tuple[UUID, ...]:
        column = getattr(model, column_name)
        statement = (
            select(column)
            .where(
                model.project_id == scope.project_id,
                model.task_id == task_id,
            )
            .order_by(column)
        )
        return tuple(self._session.scalars(statement))

    def _assignment_ids_bulk(
        self,
        model: type,
        column_name: str,
        scope: ProjectScope,
        task_ids: list[UUID],
    ) -> dict[UUID, tuple[UUID, ...]]:
        """一次查询取回一批任务的关联 ID，按 ``task_id`` 分组。

        排序与单行版一致（关联 ID 升序），保证领域对象与旧实现逐字段等价。
        """
        if not task_ids:
            return {}
        column = getattr(model, column_name)
        statement = (
            select(model.task_id, column)
            .where(
                model.project_id == scope.project_id,
                model.task_id.in_(task_ids),
            )
            .order_by(model.task_id, column)
        )
        grouped: dict[UUID, list[UUID]] = {}
        for task_id, value in self._session.execute(statement).all():
            grouped.setdefault(task_id, []).append(value)
        return {task_id: tuple(values) for task_id, values in grouped.items()}
