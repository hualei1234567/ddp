"""stage1 device and calibration

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08

建立设备域最小表：标定配置、机器人、设备、标定记录、传感器。
不包含采集任务、RawAsset、Episode 等采集业务表。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_LIFECYCLE_VALUES = "'PROVISIONED', 'ACTIVE', 'IN_MAINTENANCE', 'FAULT', 'SUSPENDED', 'RETIRED'"
_SCOPE_VALUES = "'ROBOT', 'DEVICE', 'SENSOR', 'SYSTEM'"
_DEVICE_TYPE_VALUES = "'COMPUTE', 'CONTROLLER', 'CAMERA_RIG', 'TELEOP', 'GRIPPER', 'OTHER'"
_SENSOR_TYPE_VALUES = (
    "'RGB', 'RGB_D', 'DEPTH', 'IMU', 'JOINT_STATE', "
    "'END_EFFECTOR', 'FORCE_TORQUE', 'MOCAP', 'AUDIO'"
)
_SYNC_MODE_VALUES = "'PTP', 'NTP', 'HARDWARE_TRIGGER', 'SOFTWARE_ESTIMATE', 'MANUAL', 'NONE'"
_CALIBRATION_TYPE_VALUES = (
    "'INTRINSICS', 'EXTRINSICS', 'HAND_EYE', 'IMU', "
    "'JOINT_OFFSET', 'TIME_OFFSET', 'FORCE_TORQUE', 'DEPTH_ALIGNMENT'"
)
_CALIBRATION_STATUS_VALUES = (
    "'DRAFT', 'SUBMITTED', 'VALIDATED', 'ACTIVE', "
    "'REJECTED', 'SUPERSEDED', 'REVOKED', 'EXPIRED'"
)
_VALIDATED_STATUS_VALUES = "'VALIDATED', 'ACTIVE', 'SUPERSEDED', 'REVOKED', 'EXPIRED'"


def _project_scoped_columns() -> list[sa.Column]:
    return [
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
    ]


def _project_scoped_constraints(table: str) -> list[sa.schema.Constraint]:
    return [
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=f"fk_{table}_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=f"fk_{table}_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=f"fk_{table}_updated_by_users",
        ),
        sa.CheckConstraint("version >= 1", name=op.f(f"ck_{table}_version_positive")),
    ]


def upgrade() -> None:
    op.create_table(
        "calibration_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("required_types", sa.JSON(), nullable=False),
        sa.Column("selection_rule", sa.JSON(), nullable=False),
        *_project_scoped_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_calibration_profiles"),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_calibration_profiles_project_id",
        ),
        sa.CheckConstraint(
            f"scope IN ({_SCOPE_VALUES})",
            name=op.f("ck_calibration_profiles_scope_allowed"),
        ),
        sa.CheckConstraint(
            f"target_type IN ({_SCOPE_VALUES})",
            name=op.f("ck_calibration_profiles_target_type_allowed"),
        ),
        *_project_scoped_constraints("calibration_profiles"),
    )
    op.create_index(
        "ix_calibration_profiles_project_scope",
        "calibration_profiles",
        ["project_id", "scope"],
        unique=False,
    )

    op.create_table(
        "robots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("robot_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("vendor", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("serial_number", sa.String(length=120), nullable=True),
        sa.Column("firmware_version", sa.String(length=64), nullable=True),
        sa.Column("hardware_version", sa.String(length=64), nullable=True),
        sa.Column("calibration_profile_id", sa.Uuid(), nullable=True),
        sa.Column("capability_profile", sa.JSON(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        *_project_scoped_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_robots"),
        sa.UniqueConstraint("project_id", "code", name="uq_robots_project_id"),
        sa.UniqueConstraint(
            "project_id",
            "serial_number",
            name="uq_robots_project_serial_number",
        ),
        sa.UniqueConstraint("project_id", "id", name="uq_robots_project_id_id"),
        sa.ForeignKeyConstraint(
            ["project_id", "calibration_profile_id"],
            ["calibration_profiles.project_id", "calibration_profiles.id"],
            name="fk_robots_project_calibration_profile",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_robots_owner_user_id_users",
        ),
        sa.CheckConstraint(
            f"status IN ({_LIFECYCLE_VALUES})",
            name=op.f("ck_robots_status_allowed"),
        ),
        *_project_scoped_constraints("robots"),
    )
    op.create_index(
        "ix_robots_project_status_created_at",
        "robots",
        ["project_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("device_type", sa.String(length=32), nullable=False),
        sa.Column("robot_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("vendor", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("serial_number", sa.String(length=120), nullable=True),
        sa.Column("firmware_version", sa.String(length=64), nullable=True),
        sa.Column("hardware_version", sa.String(length=64), nullable=True),
        sa.Column("calibration_profile_id", sa.Uuid(), nullable=True),
        sa.Column("capability_profile", sa.JSON(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        *_project_scoped_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_devices"),
        sa.UniqueConstraint("project_id", "code", name="uq_devices_project_id"),
        sa.UniqueConstraint(
            "project_id",
            "serial_number",
            name="uq_devices_project_serial_number",
        ),
        sa.UniqueConstraint("project_id", "id", name="uq_devices_project_id_id"),
        sa.ForeignKeyConstraint(
            ["project_id", "robot_id"],
            ["robots.project_id", "robots.id"],
            name="fk_devices_project_robot",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "calibration_profile_id"],
            ["calibration_profiles.project_id", "calibration_profiles.id"],
            name="fk_devices_project_calibration_profile",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_devices_owner_user_id_users",
        ),
        sa.CheckConstraint(
            f"device_type IN ({_DEVICE_TYPE_VALUES})",
            name=op.f("ck_devices_device_type_allowed"),
        ),
        sa.CheckConstraint(
            f"status IN ({_LIFECYCLE_VALUES})",
            name=op.f("ck_devices_status_allowed"),
        ),
        *_project_scoped_constraints("devices"),
    )
    op.create_index(
        "ix_devices_project_status_created_at",
        "devices",
        ["project_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_devices_project_robot_id",
        "devices",
        ["project_id", "robot_id"],
        unique=False,
    )

    op.create_table(
        "calibration_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("calibration_type", sa.String(length=32), nullable=False),
        sa.Column("method", sa.String(length=200), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("parent_frame", sa.String(length=200), nullable=True),
        sa.Column("child_frame", sa.String(length=200), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("evidence_asset_ids", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=True),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("uncertainty", sa.JSON(), nullable=False),
        sa.Column("performed_by", sa.Uuid(), nullable=False),
        sa.Column("validated_by", sa.Uuid(), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        *_project_scoped_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_calibration_records"),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_calibration_records_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "profile_id"],
            ["calibration_profiles.project_id", "calibration_profiles.id"],
            name="fk_calibration_records_project_profile",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "supersedes_id"],
            ["calibration_records.project_id", "calibration_records.id"],
            name="fk_calibration_records_project_supersedes",
        ),
        sa.ForeignKeyConstraint(
            ["performed_by"],
            ["users.id"],
            name="fk_calibration_records_performed_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["validated_by"],
            ["users.id"],
            name="fk_calibration_records_validated_by_users",
        ),
        sa.CheckConstraint(
            f"scope IN ({_SCOPE_VALUES})",
            name=op.f("ck_calibration_records_scope_allowed"),
        ),
        sa.CheckConstraint(
            f"target_type IN ({_SCOPE_VALUES})",
            name=op.f("ck_calibration_records_target_type_allowed"),
        ),
        sa.CheckConstraint(
            f"calibration_type IN ({_CALIBRATION_TYPE_VALUES})",
            name=op.f("ck_calibration_records_calibration_type_allowed"),
        ),
        sa.CheckConstraint(
            f"status IN ({_CALIBRATION_STATUS_VALUES})",
            name=op.f("ck_calibration_records_status_allowed"),
        ),
        sa.CheckConstraint(
            "validated_by IS NULL OR validated_by <> performed_by",
            name=op.f("ck_calibration_records_validator_separated"),
        ),
        sa.CheckConstraint(
            "status NOT IN ("
            + _VALIDATED_STATUS_VALUES
            + ") OR (validated_by IS NOT NULL AND validated_at IS NOT NULL)",
            name=op.f("ck_calibration_records_validated_fields_required"),
        ),
        *_project_scoped_constraints("calibration_records"),
    )
    op.create_index(
        "ix_calibration_records_project_target",
        "calibration_records",
        ["project_id", "target_type", "target_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_calibration_records_active_target",
        "calibration_records",
        ["project_id", "scope", "target_type", "target_id", "calibration_type"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        "sensors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("sensor_type", sa.String(length=32), nullable=False),
        sa.Column("robot_id", sa.Uuid(), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("vendor", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("serial_number", sa.String(length=120), nullable=True),
        sa.Column("sample_rate_hz", sa.Float(), nullable=False),
        sa.Column("resolution_w", sa.Integer(), nullable=True),
        sa.Column("resolution_h", sa.Integer(), nullable=True),
        sa.Column("bit_depth", sa.Integer(), nullable=True),
        sa.Column("channels", sa.Integer(), nullable=True),
        sa.Column("intrinsic_calibration_id", sa.Uuid(), nullable=True),
        sa.Column("extrinsic_calibration_id", sa.Uuid(), nullable=True),
        sa.Column("coordinate_frame", sa.String(length=200), nullable=False),
        sa.Column("sync_mode", sa.String(length=32), nullable=False),
        sa.Column("sync_group", sa.String(length=64), nullable=True),
        sa.Column("firmware_version", sa.String(length=64), nullable=True),
        sa.Column("calibration_profile_id", sa.Uuid(), nullable=True),
        sa.Column("mount_position", sa.String(length=200), nullable=True),
        *_project_scoped_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_sensors"),
        sa.UniqueConstraint("project_id", "code", name="uq_sensors_project_id"),
        sa.UniqueConstraint(
            "project_id",
            "serial_number",
            name="uq_sensors_project_serial_number",
        ),
        sa.UniqueConstraint("project_id", "id", name="uq_sensors_project_id_id"),
        sa.ForeignKeyConstraint(
            ["project_id", "robot_id"],
            ["robots.project_id", "robots.id"],
            name="fk_sensors_project_robot",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "device_id"],
            ["devices.project_id", "devices.id"],
            name="fk_sensors_project_device",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "intrinsic_calibration_id"],
            ["calibration_records.project_id", "calibration_records.id"],
            name="fk_sensors_project_intrinsic_calibration",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "extrinsic_calibration_id"],
            ["calibration_records.project_id", "calibration_records.id"],
            name="fk_sensors_project_extrinsic_calibration",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "calibration_profile_id"],
            ["calibration_profiles.project_id", "calibration_profiles.id"],
            name="fk_sensors_project_calibration_profile",
        ),
        sa.CheckConstraint(
            "robot_id IS NOT NULL OR device_id IS NOT NULL",
            name=op.f("ck_sensors_mount_required"),
        ),
        sa.CheckConstraint(
            f"sensor_type IN ({_SENSOR_TYPE_VALUES})",
            name=op.f("ck_sensors_sensor_type_allowed"),
        ),
        sa.CheckConstraint(
            f"status IN ({_LIFECYCLE_VALUES})",
            name=op.f("ck_sensors_status_allowed"),
        ),
        sa.CheckConstraint(
            f"sync_mode IN ({_SYNC_MODE_VALUES})",
            name=op.f("ck_sensors_sync_mode_allowed"),
        ),
        sa.CheckConstraint(
            "sample_rate_hz > 0",
            name=op.f("ck_sensors_sample_rate_positive"),
        ),
        sa.CheckConstraint(
            "(resolution_w IS NULL AND resolution_h IS NULL) "
            "OR (resolution_w > 0 AND resolution_h > 0)",
            name=op.f("ck_sensors_resolution_positive"),
        ),
        *_project_scoped_constraints("sensors"),
    )
    op.create_index(
        "ix_sensors_project_status_created_at",
        "sensors",
        ["project_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_sensors_project_robot_id",
        "sensors",
        ["project_id", "robot_id"],
        unique=False,
    )
    op.create_index(
        "ix_sensors_project_device_id",
        "sensors",
        ["project_id", "device_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sensors_project_device_id", table_name="sensors")
    op.drop_index("ix_sensors_project_robot_id", table_name="sensors")
    op.drop_index("ix_sensors_project_status_created_at", table_name="sensors")
    op.drop_table("sensors")

    op.drop_index(
        "uq_calibration_records_active_target",
        table_name="calibration_records",
    )
    op.drop_index(
        "ix_calibration_records_project_target",
        table_name="calibration_records",
    )
    op.drop_table("calibration_records")

    op.drop_index("ix_devices_project_robot_id", table_name="devices")
    op.drop_index("ix_devices_project_status_created_at", table_name="devices")
    op.drop_table("devices")

    op.drop_index("ix_robots_project_status_created_at", table_name="robots")
    op.drop_table("robots")

    op.drop_index(
        "ix_calibration_profiles_project_scope",
        table_name="calibration_profiles",
    )
    op.drop_table("calibration_profiles")
