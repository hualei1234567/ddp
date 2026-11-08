"""标定目标投影行的仓储级同步。

``calibration_target_refs`` 是 ``calibration_records.target_id`` 的数据库权威
防线。应用层在校验目标存在后调用这里的 upsert，保证：

- 机器人/设备/传感器写入时同事务维护投影行；
- 生命周期状态变化时同步 ``lifecycle_status`` 与 ``is_retired``；
- ``SYSTEM`` 目标按 ``target_id == project_id`` 维护；
- 重复同步幂等，不因并发重复插入而失败。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.domain.devices.enums import CalibrationTargetType, LifecycleStatus
from app.infrastructure.db.models.devices import CalibrationTargetRef
from app.infrastructure.repositories._sql_support import enum_value

_TARGET_KEY = ("project_id", "target_type", "target_id")


def _is_retired(lifecycle_status: str) -> bool:
    return lifecycle_status == LifecycleStatus.RETIRED.value


def upsert_target_ref(
    session: Session,
    *,
    project_id: UUID,
    target_type: CalibrationTargetType | str,
    target_id: UUID,
    lifecycle_status: LifecycleStatus | str,
) -> None:
    """幂等写入/更新一行目标投影。"""
    status_value = enum_value(lifecycle_status)
    values = {
        "project_id": project_id,
        "target_type": enum_value(target_type),
        "target_id": target_id,
        "lifecycle_status": status_value,
        "is_retired": _is_retired(status_value),
        "updated_at": func.now(),
    }
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(CalibrationTargetRef).values(**values)
    elif dialect == "sqlite":
        statement = sqlite_insert(CalibrationTargetRef).values(**values)
    else:
        raise RuntimeError(f"未支持的数据库方言：{dialect}")
    session.execute(
        statement.on_conflict_do_update(
            index_elements=list(_TARGET_KEY),
            set_={
                "lifecycle_status": values["lifecycle_status"],
                "is_retired": values["is_retired"],
                "updated_at": func.now(),
            },
        )
    )


def ensure_system_target_ref(session: Session, project_id: UUID) -> None:
    """项目级 SYSTEM 目标：``target_id`` 必须等于项目 ID。"""
    upsert_target_ref(
        session,
        project_id=project_id,
        target_type=CalibrationTargetType.SYSTEM,
        target_id=project_id,
        lifecycle_status=LifecycleStatus.ACTIVE,
    )
