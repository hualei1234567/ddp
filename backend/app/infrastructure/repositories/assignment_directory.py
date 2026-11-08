"""采集任务分配目录适配器：校验成员、机器人、设备与传感器。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.devices import Device, Robot, Sensor
from app.infrastructure.db.models.identity import ProjectMember


class SqlAssignmentDirectory:
    def __init__(self, session: Session) -> None:
        self._session = session

    def user_is_member(self, scope: ProjectScope, user_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(ProjectMember.id).where(
                    ProjectMember.project_id == scope.project_id,
                    ProjectMember.user_id == user_id,
                )
            )
            is not None
        )

    def robot_status(self, scope: ProjectScope, robot_id: UUID) -> str | None:
        return self._session.scalar(
            select(Robot.status).where(
                Robot.id == robot_id,
                Robot.project_id == scope.project_id,
            )
        )

    def device_status(self, scope: ProjectScope, device_id: UUID) -> str | None:
        return self._session.scalar(
            select(Device.status).where(
                Device.id == device_id,
                Device.project_id == scope.project_id,
            )
        )

    def sensor_status(self, scope: ProjectScope, sensor_id: UUID) -> str | None:
        return self._session.scalar(
            select(Sensor.status).where(
                Sensor.id == sensor_id,
                Sensor.project_id == scope.project_id,
            )
        )
