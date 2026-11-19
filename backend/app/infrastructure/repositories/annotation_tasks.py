"""标注任务 SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.task import SLA_CLOSED_STATUSES, AnnotationTask
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_work import (
    AnnotationTask as AnnotationTaskModel,
)
from app.infrastructure.repositories._annotation_sql import (
    as_utc,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    raise_update_failure,
    scoped_select,
)

# 终态任务不再计入逾期；SQL 下推与领域 ``is_overdue`` 共用同一份状态集合。
_CLOSED_STATUS_VALUES = tuple(status.value for status in SLA_CLOSED_STATUSES)


def _overdue_condition(now: datetime):
    """与 ``domain.annotation.task.is_overdue`` 同口径的 SQL 条件。"""
    return and_(
        AnnotationTaskModel.due_at.is_not(None),
        AnnotationTaskModel.due_at < now,
        AnnotationTaskModel.status.not_in(_CLOSED_STATUS_VALUES),
    )


class SqlAnnotationTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, task: AnnotationTask) -> AnnotationTask:
        scope.ensure(task.project_id)
        ensure_initial_version(task.version)
        now = datetime.now(UTC)
        row = AnnotationTaskModel(
            id=task.task_id,
            project_id=task.project_id,
            code=task.code,
            name=task.name,
            description=task.description,
            status=enum_value(task.status),
            sample_set_id=task.sample_set_id,
            schema_id=task.schema_id,
            schema_version=task.schema_version,
            schema_sha256=task.schema_sha256,
            gold_set_id=task.gold_set_id,
            iaa_policy=dict(task.iaa_policy),
            quality_policy=dict(task.quality_policy),
            assignment_mode=task.assignment_mode.value,
            target_item_count=task.target_item_count,
            target_episode_count=task.target_episode_count,
            due_at=task.due_at,
            priority=task.priority.value,
            progress=dict(task.progress),
            quality_summary=dict(task.quality_summary),
            iaa_report_id=task.iaa_report_id,
            accepted_at=task.accepted_at,
            completed_at=task.completed_at,
            status_reason=task.status_reason,
            blocked_reason=task.blocked_reason,
            version=task.version,
            created_at=task.created_at or now,
            updated_at=task.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session)
        return task

    def get(self, scope: ProjectScope, task_id: UUID) -> AnnotationTask | None:
        row = self._session.scalar(
            select(AnnotationTaskModel).where(
                AnnotationTaskModel.id == task_id,
                AnnotationTaskModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_code(self, scope: ProjectScope, code: str) -> AnnotationTask | None:
        row = self._session.scalar(
            select(AnnotationTaskModel).where(
                AnnotationTaskModel.project_id == scope.project_id,
                AnnotationTaskModel.code == code.strip(),
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        overdue: bool | None = None,
        now: datetime | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationTask]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = scoped_select(AnnotationTaskModel, scope)
        if status is not None:
            statement = statement.where(AnnotationTaskModel.status == status)
        if overdue is not None:
            moment = now or datetime.now(UTC)
            if overdue:
                statement = statement.where(_overdue_condition(moment))
            else:
                # false 的语义是"当前不逾期"：无截止、尚未到期、或已进入终态。
                statement = statement.where(
                    or_(
                        AnnotationTaskModel.due_at.is_(None),
                        AnnotationTaskModel.due_at >= moment,
                        AnnotationTaskModel.status.in_(_CLOSED_STATUS_VALUES),
                    )
                )
        if after_id is not None:
            statement = statement.where(AnnotationTaskModel.id > after_id)
        statement = statement.order_by(AnnotationTaskModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def update_progress(
        self,
        scope: ProjectScope,
        task_id: UUID,
        progress: dict[str, int],
    ) -> None:
        """只写派生的 ``progress`` 列，**不推进乐观锁版本**。

        进度是从提交事实重算出来的投影，不属于"用户对任务的修改"。若它推进
        version，客户端每次提交后都会因为持有旧 version 而在下一个动作上 409
        ——把统计变成一次隐式的并发冲突源。这里不加 version 条件：并发下最后
        一次重算覆盖前一次，而重算依据的是同一批提交事实，收敛结果一致。
        """
        self._session.execute(
            update(AnnotationTaskModel)
            .where(
                AnnotationTaskModel.id == task_id,
                AnnotationTaskModel.project_id == scope.project_id,
            )
            .values(progress=dict(progress))
            .execution_options(synchronize_session=False)
        )

    def save(
        self,
        scope: ProjectScope,
        task: AnnotationTask,
        *,
        expected_version: int,
    ) -> AnnotationTask:
        scope.ensure(task.project_id)
        ensure_expected_version(task.version, expected_version)
        now = task.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(AnnotationTaskModel)
            .where(
                AnnotationTaskModel.id == task.task_id,
                AnnotationTaskModel.project_id == scope.project_id,
                AnnotationTaskModel.version == expected_version,
            )
            .values(
                name=task.name,
                description=task.description,
                status=enum_value(task.status),
                iaa_policy=dict(task.iaa_policy),
                quality_policy=dict(task.quality_policy),
                assignment_mode=task.assignment_mode.value,
                target_item_count=task.target_item_count,
                target_episode_count=task.target_episode_count,
                due_at=task.due_at,
                priority=task.priority.value,
                progress=dict(task.progress),
                quality_summary=dict(task.quality_summary),
                gold_set_id=task.gold_set_id,
                iaa_report_id=task.iaa_report_id,
                accepted_at=task.accepted_at,
                completed_at=task.completed_at,
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
                AnnotationTaskModel,
                scope,
                task.task_id,
                "标注任务",
            )
        return replace(
            task,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def list_for_sample_set(
        self,
        scope: ProjectScope,
        *,
        sample_set_id: UUID,
        active_only: bool = True,
    ) -> list[AnnotationTask]:
        statement = scoped_select(AnnotationTaskModel, scope).where(
            AnnotationTaskModel.sample_set_id == sample_set_id
        )
        if active_only:
            statement = statement.where(AnnotationTaskModel.status != "CANCELLED")
        statement = statement.order_by(AnnotationTaskModel.id)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def count_active_for_sample_set(
        self,
        scope: ProjectScope,
        *,
        sample_set_id: UUID,
    ) -> int:
        total = self._session.scalar(
            select(func.count())
            .select_from(AnnotationTaskModel)
            .where(
                AnnotationTaskModel.project_id == scope.project_id,
                AnnotationTaskModel.sample_set_id == sample_set_id,
                AnnotationTaskModel.status != "CANCELLED",
            )
        )
        return int(total or 0)

    def count_overdue(self, now: datetime) -> int:
        """跨项目统计逾期任务数；只读，供巡检 worker 观测。

        逾期是派生事实：这里不写库、不推进版本、不自动改派或关闭任务。
        """
        total = self._session.scalar(
            select(func.count()).select_from(AnnotationTaskModel).where(_overdue_condition(now))
        )
        return int(total or 0)

    def list_overdue_candidates(
        self,
        now: datetime,
        *,
        after_id: UUID | None = None,
        limit: int = 100,
    ) -> list[AnnotationTask]:
        """**跨项目**列出逾期任务（按 id 键集游标）；只读。

        与 ``count_overdue`` 同口径：不做项目过滤，因为提醒巡检需要一次看到
        所有项目。写提醒前调用方必须用任务自身的 ``project_id`` 构造
        ``ProjectScope``，由仓储与审计共同兜底隔离。
        """
        if limit < 1 or limit > 201:
            raise DomainValidationError("limit 必须在 1 到 201 之间")
        statement = select(AnnotationTaskModel).where(_overdue_condition(now))
        if after_id is not None:
            statement = statement.where(AnnotationTaskModel.id > after_id)
        statement = statement.order_by(AnnotationTaskModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def _from_row(self, row: AnnotationTaskModel) -> AnnotationTask:
        return AnnotationTask(
            task_id=row.id,
            project_id=row.project_id,
            code=row.code,
            name=row.name,
            description=row.description,
            status=row.status,
            sample_set_id=row.sample_set_id,
            schema_id=row.schema_id,
            schema_version=row.schema_version,
            schema_sha256=row.schema_sha256,
            gold_set_id=row.gold_set_id,
            iaa_policy=row.iaa_policy or {},
            quality_policy=row.quality_policy or {},
            assignment_mode=row.assignment_mode,
            target_item_count=row.target_item_count,
            target_episode_count=row.target_episode_count,
            due_at=as_utc(row.due_at),
            priority=row.priority,
            progress=row.progress or {},
            quality_summary=row.quality_summary or {},
            iaa_report_id=row.iaa_report_id,
            accepted_at=as_utc(row.accepted_at),
            completed_at=as_utc(row.completed_at),
            status_reason=row.status_reason,
            blocked_reason=row.blocked_reason,
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )
