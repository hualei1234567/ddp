"""采集会话 API DTO。

请求模型 ``extra="forbid"``；客户端不能提交 ``status`` / ``project_id`` /
``version`` / 时间戳 / 审计字段。响应里的 ``allowed_actions`` 由服务端状态机
计算，前端只渲染这里列出的动作。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.collection.enums import (
    PUBLIC_CAPTURE_SESSION_ACTIONS,
    CaptureSessionStatus,
)
from app.domain.collection.models import CaptureSession
from app.domain.collection.state_machines import allowed_session_actions


class CaptureSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    task_id: UUID | None = None
    batch_id: UUID | None = None
    operator_id: UUID | None = None
    robot_id: UUID | None = None
    location_id: UUID | None = None


class CaptureSessionActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class CaptureSessionOut(BaseModel):
    session_id: UUID
    project_id: UUID
    code: str
    task_id: UUID | None
    batch_id: UUID | None
    operator_id: UUID | None
    robot_id: UUID | None
    location_id: UUID | None
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    status_reason: str | None
    allowed_actions: list[str]
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, session: CaptureSession) -> CaptureSessionOut:
        status = CaptureSessionStatus(_value(session.status))
        return cls(
            session_id=session.session_id,
            project_id=session.project_id,
            code=session.code,
            task_id=session.task_id,
            batch_id=session.batch_id,
            operator_id=session.operator_id,
            robot_id=session.robot_id,
            location_id=session.location_id,
            status=status.value,
            started_at=session.started_at,
            ended_at=session.ended_at,
            status_reason=session.status_reason,
            allowed_actions=sorted(
                allowed_session_actions(status) & PUBLIC_CAPTURE_SESSION_ACTIONS
            ),
            version=session.version,
            created_at=session.created_at,
            updated_at=session.updated_at,
            created_by=session.created_by,
            updated_by=session.updated_by,
        )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)
