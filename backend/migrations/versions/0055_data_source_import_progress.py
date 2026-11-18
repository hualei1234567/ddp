"""导入任务可观测性：阶段、进度与步骤日志

Revision ID: 0055
Revises: 0054
Create Date: 2026-11-18

为什么加这三列
--------------
此前导入任务只有 ``status`` + 结果计数（``imported_row_count`` /
``imported_file_count``），执行期对外是黑盒：看不到卡在下载还是解析，
也看不到已处理多少。运维只能等终态，失败时只有一句 ``error_message``。

新列全部**可空**，语义是"未测量"：

- ``stage``：``DOWNLOAD`` / ``VALIDATE`` / ``PARSE`` / ``INGEST`` / ``COMPLETE``；
- ``processed_items`` / ``total_items``：单位为**媒体文件**；
  ``total_items`` 在元数据解析完成后才可知（之前为 NULL）。

**存量一律保持 NULL，不回填 0**：迁移前的导入没有测量过进度，把"没记录"
写成 0 会被前端渲染成"已完成 0%"，正是本任务要杜绝的假数据。前端对 NULL
显示"未测量"。

新增日志表
----------
``data_source_import_logs`` 只追加、只读，随导入任务级联删除。为什么不复用
``audit_logs``：审计记录业务变更（谁改了什么），阶段进度是运行期遥测；
写进审计会污染变更时间线，且无法表达 ``stage`` / ``level`` / ``attempt``。
消息列在应用层写入前已脱敏（URL / 预签名参数 / 绝对路径）。

为什么判列/表是否存在：与 0051/0054 同一处理。全新库上某些建表迁移若已
带出同名列（历史漂移），重复 ADD COLUMN 会直接让 ``upgrade head`` 失败。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

_IMPORT_TABLE = "data_source_imports"
_LOG_TABLE = "data_source_import_logs"

_STAGE_VALUES = "'DOWNLOAD', 'VALIDATE', 'PARSE', 'INGEST', 'COMPLETE'"
_LEVEL_VALUES = "'INFO', 'WARNING', 'ERROR'"

_ADDED_COLUMNS = (
    sa.Column("stage", sa.String(length=16), nullable=True),
    sa.Column("processed_items", sa.Integer(), nullable=True),
    sa.Column("total_items", sa.Integer(), nullable=True),
)

_IMPORT_CHECKS = (
    sa.CheckConstraint(
        f"stage IS NULL OR stage IN ({_STAGE_VALUES})",
        name="import_stage_allowed",
    ),
    sa.CheckConstraint(
        "processed_items IS NULL OR processed_items >= 0",
        name="import_processed_items_non_negative",
    ),
    sa.CheckConstraint(
        "total_items IS NULL OR total_items >= 0",
        name="import_total_items_non_negative",
    ),
    sa.CheckConstraint(
        "processed_items IS NULL OR total_items IS NULL OR processed_items <= total_items",
        name="import_progress_within_total",
    ),
)


def _column_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def _has_table(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "stage" not in _column_names(_IMPORT_TABLE):
        with op.batch_alter_table(_IMPORT_TABLE, recreate="auto") as batch:
            for column in _ADDED_COLUMNS:
                batch.add_column(column)
            for constraint in _IMPORT_CHECKS:
                batch.create_check_constraint(constraint.name, constraint.sqltext)

    if not _has_table(_LOG_TABLE):
        op.create_table(
            _LOG_TABLE,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("project_id", sa.Uuid(), nullable=False),
            sa.Column("import_id", sa.Uuid(), nullable=False),
            sa.Column("stage", sa.String(length=16), nullable=False),
            sa.Column("level", sa.String(length=8), nullable=False),
            sa.Column("message", sa.String(length=1000), nullable=False),
            sa.Column(
                "attempt",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            ),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("sequence_no", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["import_id"], ["data_source_imports.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint(
                f"stage IN ({_STAGE_VALUES})",
                name="import_log_stage_allowed",
            ),
            sa.CheckConstraint(
                f"level IN ({_LEVEL_VALUES})",
                name="import_log_level_allowed",
            ),
            sa.CheckConstraint("length(message) > 0", name="import_log_message_not_empty"),
            sa.CheckConstraint("attempt >= 1", name="import_log_attempt_positive"),
            sa.CheckConstraint("sequence_no >= 1", name="import_log_sequence_positive"),
        )
        op.create_index(
            "ix_data_source_import_logs_import",
            _LOG_TABLE,
            ["project_id", "import_id", "occurred_at", "id"],
        )


def downgrade() -> None:
    if _has_table(_LOG_TABLE):
        op.drop_index(
            "ix_data_source_import_logs_import",
            table_name=_LOG_TABLE,
        )
        op.drop_table(_LOG_TABLE)

    if "stage" in _column_names(_IMPORT_TABLE):
        with op.batch_alter_table(_IMPORT_TABLE, recreate="auto") as batch:
            for constraint in reversed(_IMPORT_CHECKS):
                batch.drop_constraint(constraint.name, type_="check")
            batch.drop_column("total_items")
            batch.drop_column("processed_items")
            batch.drop_column("stage")
