"""审计日志只读查询仓储。

``audit_logs`` 由多个业务域共用写入（数据集、采集、标注、模型运营、供应商、
数据源、上传与导入），因此查询实现不挂在任一写仓储里，单独成模块。

边界：
- 只读：本模块只有 ``list``，没有 add/update/delete。审计可追溯的前提是
  记录不可篡改，平台不提供修改或删除审计日志的接口。
- 项目隔离：``scope.project_id`` 是唯一边界，过滤条件全部下推为 SQL，
  不取回后内存过滤。
- 索引：``(project_id, occurred_at)`` 覆盖通用时间倒序列表，
  ``(project_id, resource_type, resource_id, occurred_at)`` 覆盖资源时间线。
  ``action`` / ``actor_user_id`` / ``result`` 没有独立索引，作为索引扫描后的
  残余过滤条件执行；当前数据量（千级）下无需为它们新增迁移。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit import AuditLogEntry
from app.domain.audit.models import AuditQuery
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import AuditLog
from app.infrastructure.repositories._sql_support import as_utc, keyset_after_desc


class SqlAuditLogQueryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(
        self,
        scope: ProjectScope,
        query: AuditQuery,
        *,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AuditLogEntry]:
        statement = select(AuditLog).where(AuditLog.project_id == scope.project_id)

        if query.resource_type is not None:
            statement = statement.where(AuditLog.resource_type == query.resource_type)
        if query.resource_id is not None:
            statement = statement.where(AuditLog.resource_id == query.resource_id)
        if query.action is not None:
            statement = statement.where(AuditLog.action == query.action)
        if query.actor_user_id is not None:
            statement = statement.where(AuditLog.actor_user_id == query.actor_user_id)
        if query.result is not None:
            statement = statement.where(AuditLog.result == query.result.value)
        if query.occurred_from is not None:
            statement = statement.where(AuditLog.occurred_at >= query.occurred_from)
        if query.occurred_to is not None:
            statement = statement.where(AuditLog.occurred_at <= query.occurred_to)

        after_clause = keyset_after_desc(
            AuditLog,
            AuditLog.occurred_at,
            AuditLog.id,
            after_id,
            project_id=scope.project_id,
        )
        if after_clause is not None:
            statement = statement.where(after_clause)

        rows = self._session.scalars(
            statement.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).limit(limit)
        )
        return [self._to_entry(row) for row in rows]

    @staticmethod
    def _to_entry(row: AuditLog) -> AuditLogEntry:
        occurred_at = as_utc(row.occurred_at)
        # occurred_at 是 NOT NULL 列；这里显式兜底而不是断言静默通过，
        # 避免未来 schema 变更时把 None 带进领域对象。
        if occurred_at is None:
            raise ValueError("audit_logs.occurred_at 不允许为空")
        return AuditLogEntry(
            audit_id=row.id,
            project_id=row.project_id,
            actor_user_id=row.actor_user_id,
            actor_role=row.actor_role,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            action=row.action,
            from_status=row.from_status,
            to_status=row.to_status,
            reason=row.reason,
            request_id=row.request_id,
            result=row.result,
            correlation_id=row.correlation_id,
            details=dict(row.details),
            occurred_at=occurred_at,
        )
