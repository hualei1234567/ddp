"""机器人、设备、传感器与标定 ORM 模型。

约束设计对应 DDP-101 §2、§6：
- 所有业务表强制 ``project_id``，子表使用 ``(project_id, parent_id)`` 复合外键；
- ``(project_id, code)`` 与 ``(project_id, serial_number)`` 唯一；
- 状态字段使用 ``varchar + CHECK``，枚举值来自 Domain，禁止未知值；
- 同一目标/类型只允许一条 ACTIVE 标定记录（部分唯一索引）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.devices.enums import (
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
    DeviceType,
    LifecycleStatus,
    SensorSyncMode,
    SensorType,
)
from app.infrastructure.db.base import Base

_VALIDATED_STATUSES = (
    CalibrationStatus.VALIDATED,
    CalibrationStatus.ACTIVE,
    CalibrationStatus.SUPERSEDED,
    CalibrationStatus.REVOKED,
    CalibrationStatus.EXPIRED,
)


def _enum_check(column: str, enum_type: type, name: str) -> CheckConstraint:
    values = ", ".join(repr(item.value) for item in enum_type)
    return CheckConstraint(f"{column} IN ({values})", name=name)


class ProjectScopedMixin:
    """项目隔离与审计公共字段；所有设备域表共用。"""

    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )
    updated_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )


class CalibrationTargetRef(Base):
    """标定目标的多态投影表（契约冻结 v1 §10）。

    该表是 ``calibration_records.target_id`` 完整性的数据库权威防线：
    ROBOT/DEVICE/SENSOR 由仓储在写入目标实体时同步维护；SYSTEM 行在首次
    使用时按 ``target_id == project_id`` 维护。``calibration_records`` 通过
    ``(project_id, target_type, target_id)`` 复合外键引用本表，从而拒绝
    不存在、跨项目或类型错配的目标。

    SQLite 无法给已存在的表追加外键，迁移在 SQLite 上以等价触发器实现
    同一语义；PostgreSQL 使用真实复合外键。
    """

    __tablename__ = "calibration_target_refs"

    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    target_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    target_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False)
    is_retired: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        _enum_check(
            "target_type",
            CalibrationTargetType,
            "target_type_allowed",
        ),
        _enum_check(
            "lifecycle_status",
            LifecycleStatus,
            "lifecycle_status_allowed",
        ),
        CheckConstraint(
            "is_retired = (lifecycle_status = 'RETIRED')",
            name="retired_flag_consistent",
        ),
        CheckConstraint(
            "target_type <> 'SYSTEM' OR target_id = project_id",
            name="system_target_matches_project",
        ),
    )


class CalibrationProfile(Base, ProjectScopedMixin):
    """标定配置版本；``version`` 同时是配置版本号。"""

    __tablename__ = "calibration_profiles"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    required_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    selection_rule: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    # ADR-0020（G-06）：绑定到具体目标；SYSTEM 作用域必须为空。
    target_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_calibration_profiles_project_id",
        ),
        # 同一逻辑 Profile 的版本号不可重复：否则"第 2 版"可能有两行。
        UniqueConstraint(
            "project_id",
            "name",
            "target_id",
            "version",
            name="uq_calibration_profiles_name_target_version",
        ),
        _enum_check("scope", CalibrationScope, "scope_allowed"),
        _enum_check("target_type", CalibrationTargetType, "target_type_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_calibration_profiles_project_scope", "project_id", "scope"),
    )


class Robot(Base, ProjectScopedMixin):
    __tablename__ = "robots"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    robot_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hardware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calibration_profile_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    capability_profile: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    location_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    owner_user_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=True,
    )
    # 状态语义（G-09）：与 devices/sensors 对齐。不加外键——actor 溯源列
    # 不做强引用，用户离开项目后历史仍需可读。
    status_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fault_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fault_detail: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    fault_occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_changed_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_robots_project_id"),
        UniqueConstraint(
            "project_id",
            "serial_number",
            name="uq_robots_project_serial_number",
        ),
        UniqueConstraint("project_id", "id", name="uq_robots_project_id_id"),
        ForeignKeyConstraint(
            ["project_id", "calibration_profile_id"],
            ["calibration_profiles.project_id", "calibration_profiles.id"],
            name="fk_robots_project_calibration_profile",
        ),
        _enum_check("status", LifecycleStatus, "status_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_robots_project_status_created_at", "project_id", "status", "created_at"),
    )


class Device(Base, ProjectScopedMixin):
    __tablename__ = "devices"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    device_type: Mapped[str] = mapped_column(String(32), nullable=False)
    robot_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hardware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calibration_profile_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    capability_profile: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    location_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    owner_user_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=True,
    )
    # 状态语义：契约要求能表达"为什么进入该状态"，而不只是状态本身。
    # G-09/G-10：此前动作请求体（reason/fault_code/occurred_at）被路由层丢弃。
    status_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    fault_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fault_detail: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    fault_occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 不加外键：SQLite 无法 ALTER 追加约束，且 actor 溯源列不需要强引用
    # （用户离开项目后历史仍需可读）。取值由应用层从 ProjectScope 写入。
    status_changed_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_devices_project_id"),
        UniqueConstraint(
            "project_id",
            "serial_number",
            name="uq_devices_project_serial_number",
        ),
        UniqueConstraint("project_id", "id", name="uq_devices_project_id_id"),
        ForeignKeyConstraint(
            ["project_id", "robot_id"],
            ["robots.project_id", "robots.id"],
            name="fk_devices_project_robot",
        ),
        ForeignKeyConstraint(
            ["project_id", "calibration_profile_id"],
            ["calibration_profiles.project_id", "calibration_profiles.id"],
            name="fk_devices_project_calibration_profile",
        ),
        _enum_check("device_type", DeviceType, "device_type_allowed"),
        _enum_check("status", LifecycleStatus, "status_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_devices_project_status_created_at", "project_id", "status", "created_at"),
        Index("ix_devices_project_robot_id", "project_id", "robot_id"),
    )


class CalibrationRecord(Base, ProjectScopedMixin):
    __tablename__ = "calibration_records"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    calibration_type: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(200), nullable=False)
    parameters: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    parent_frame: Mapped[str | None] = mapped_column(String(200), nullable=True)
    child_frame: Mapped[str | None] = mapped_column(String(200), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_asset_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    uncertainty: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    performed_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )
    validated_by: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=True,
    )
    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    # ADR-0020（G-04）：业务修订号；version 继续作为乐观锁。
    calibration_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    # ADR-0020（G-05）：创建时的 Profile 快照，保证事后可复现。
    profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    profile_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_calibration_records_project_id",
        ),
        ForeignKeyConstraint(
            ["project_id", "profile_id"],
            ["calibration_profiles.project_id", "calibration_profiles.id"],
            name="fk_calibration_records_project_profile",
        ),
        ForeignKeyConstraint(
            ["project_id", "target_type", "target_id"],
            [
                "calibration_target_refs.project_id",
                "calibration_target_refs.target_type",
                "calibration_target_refs.target_id",
            ],
            name="fk_calibration_records_project_target_ref",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "supersedes_id"],
            ["calibration_records.project_id", "calibration_records.id"],
            name="fk_calibration_records_project_supersedes",
        ),
        _enum_check("scope", CalibrationScope, "scope_allowed"),
        _enum_check("target_type", CalibrationTargetType, "target_type_allowed"),
        _enum_check("calibration_type", CalibrationType, "calibration_type_allowed"),
        _enum_check("status", CalibrationStatus, "status_allowed"),
        CheckConstraint(
            "validated_by IS NULL OR validated_by <> performed_by",
            name="validator_separated",
        ),
        CheckConstraint(
            "status NOT IN ("
            + ", ".join(repr(item.value) for item in _VALIDATED_STATUSES)
            + ") OR (validated_by IS NOT NULL AND validated_at IS NOT NULL)",
            name="validated_fields_required",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_calibration_records_project_target",
            "project_id",
            "target_type",
            "target_id",
            "status",
        ),
    )


class Sensor(Base, ProjectScopedMixin):
    __tablename__ = "sensors"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    sensor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    robot_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    device_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sample_rate_hz: Mapped[float] = mapped_column(nullable=False)
    resolution_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bit_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    intrinsic_calibration_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    extrinsic_calibration_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    coordinate_frame: Mapped[str] = mapped_column(String(200), nullable=False)
    # ADR-0019 阶段 1：frame_id 为新字段（回填自 coordinate_frame），
    # frame_convention 显式表达约定；coordinate_frame 标记 DEPRECATED。
    # 可空：迁移 0027 回填后由应用层保证非空。直接写 ORM 的测试夹具
    # 与外部工具不必知道这个派生规则；应用层 create/update 一定写入。
    frame_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    frame_convention: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'UNSPECIFIED'")
    )
    sync_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    sync_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calibration_profile_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    mount_position: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # 状态语义：契约要求能表达"为什么进入该状态"，而不只是状态本身。
    # G-09/G-10：此前动作请求体（reason/fault_code/occurred_at）被路由层丢弃。
    status_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    fault_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fault_detail: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    fault_occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 不加外键：SQLite 无法 ALTER 追加约束，且 actor 溯源列不需要强引用
    # （用户离开项目后历史仍需可读）。取值由应用层从 ProjectScope 写入。
    status_changed_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_sensors_project_id"),
        UniqueConstraint(
            "project_id",
            "serial_number",
            name="uq_sensors_project_serial_number",
        ),
        UniqueConstraint("project_id", "id", name="uq_sensors_project_id_id"),
        ForeignKeyConstraint(
            ["project_id", "robot_id"],
            ["robots.project_id", "robots.id"],
            name="fk_sensors_project_robot",
        ),
        ForeignKeyConstraint(
            ["project_id", "device_id"],
            ["devices.project_id", "devices.id"],
            name="fk_sensors_project_device",
        ),
        ForeignKeyConstraint(
            ["project_id", "intrinsic_calibration_id"],
            ["calibration_records.project_id", "calibration_records.id"],
            name="fk_sensors_project_intrinsic_calibration",
        ),
        ForeignKeyConstraint(
            ["project_id", "extrinsic_calibration_id"],
            ["calibration_records.project_id", "calibration_records.id"],
            name="fk_sensors_project_extrinsic_calibration",
        ),
        ForeignKeyConstraint(
            ["project_id", "calibration_profile_id"],
            ["calibration_profiles.project_id", "calibration_profiles.id"],
            name="fk_sensors_project_calibration_profile",
        ),
        CheckConstraint(
            "robot_id IS NOT NULL OR device_id IS NOT NULL",
            name="mount_required",
        ),
        _enum_check("sensor_type", SensorType, "sensor_type_allowed"),
        _enum_check("status", LifecycleStatus, "status_allowed"),
        _enum_check("sync_mode", SensorSyncMode, "sync_mode_allowed"),
        CheckConstraint("sample_rate_hz > 0", name="sample_rate_positive"),
        CheckConstraint(
            "(resolution_w IS NULL AND resolution_h IS NULL) "
            "OR (resolution_w > 0 AND resolution_h > 0)",
            name="resolution_positive",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_sensors_project_status_created_at", "project_id", "status", "created_at"),
        Index("ix_sensors_project_robot_id", "project_id", "robot_id"),
        Index("ix_sensors_project_device_id", "project_id", "device_id"),
    )


# 同一项目、同一目标、同一标定类型只允许一条 ACTIVE 记录。
# 部分唯一索引在 PostgreSQL 与 SQLite 均支持；PostgreSQL 迁移时无需锁表回填。
Index(
    "uq_calibration_records_active_target",
    CalibrationRecord.project_id,
    CalibrationRecord.scope,
    CalibrationRecord.target_type,
    CalibrationRecord.target_id,
    CalibrationRecord.calibration_type,
    unique=True,
    postgresql_where=text("status = 'ACTIVE'"),
    sqlite_where=text("status = 'ACTIVE'"),
)
