"""审计日志 API DTO。

响应字段与 ``AuditLogEntry`` 一一对应，不做"友好化"重命名：
追溯场景里调用方需要能按库里的值反查（例如 ``request_id`` 回查日志）。
本模块只有响应模型——审计日志没有创建/修改/删除的请求模型，平台不提供
任何写入口，这是「不可篡改」在契约层的体现。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.application.audit import AuditLogEntry


class AuditLogOut(BaseModel):
    audit_id: UUID
    # 项目维度是服务端强制的隔离边界，回显它是为了让客户端能校验
    # 「这一页确实属于请求路径上的项目」，不做任何权限判断依据。
    project_id: UUID
    actor_user_id: UUID
    actor_role: str | None
    resource_type: str
    resource_id: UUID
    action: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    request_id: str | None
    result: str
    correlation_id: str | None
    details: dict[str, object]
    occurred_at: datetime

    @classmethod
    def from_entry(cls, entry: AuditLogEntry) -> AuditLogOut:
        return cls(
            audit_id=entry.audit_id,
            project_id=entry.project_id,
            actor_user_id=entry.actor_user_id,
            actor_role=entry.actor_role,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            action=entry.action,
            from_status=entry.from_status,
            to_status=entry.to_status,
            reason=entry.reason,
            request_id=entry.request_id,
            result=entry.result,
            correlation_id=entry.correlation_id,
            details=dict(entry.details),
            occurred_at=entry.occurred_at,
        )


__all__ = ["AuditLogOut"]
