"""N7-007：MOCK / DRY_RUN 独立执行器所需的状态与演练计划列

Revision ID: 0067
Revises: 0066
Create Date: 2026-11-19

为什么加一个状态值和一个可空列
------------------------------
此前 ``MOCK`` / ``DRY_RUN`` 只是 ``execution_mode`` 上的**标记**，用例仍会调用
外部 Adapter，无法诚实表达"这次没有真正训练"：

- DRY_RUN 只做校验与计划，既不提交外部平台，也不产生训练产物。沿用
  ``SUBMITTED``（意味着已提交给外部平台）或 ``SUCCEEDED``（意味着训练成功）都
  是撒谎，因此新增演练专用终态 ``DRY_RUN``；
- 计划（校验结论 + 将执行的动作清单）需要独立落点，不能塞进 ``artifacts``
  （那是训练产物元数据），因此新增可空 JSON 列 ``dry_run_plan``，并用
  ``dry_run_plan IS NULL OR execution_mode = 'DRY_RUN'`` 约束它只能挂在演练引用上。

**MOCK 不新增状态**：它的外部 Run 状态可以是 ``SUCCEEDED``，但
``execution_mode=MOCK`` 且 ``artifacts.mock=true`` 由领域层强制；下游（模型版本
登记）读取执行方式并拒绝。这样不破坏既有前端"SUCCEEDED 的 MOCK 仍是模拟"的展示。

幂等与建库路径
--------------
建表迁移 0019 直接用运行时 ORM 元数据建表。全新库上，0019 已按当前模型建出
``dry_run_plan`` 列、新 ``external_status`` 取值与新 CHECK；此时本迁移必须逐项
检测并跳过，不能 ``duplicate column`` / 重复建约束。与 0051/0054/0063 同一处理。

**触发器必须重装**：SQLite 的 ``CREATE TRIGGER IF NOT EXISTS`` 不会替换旧定义；
``external_training_references`` 的状态迁移触发器必须包含 ``SUBMITTED -> DRY_RUN``。
因此升级/回滚前后都显式 ``drop_training_guards`` / ``install_training_guards``。

回滚
----
``downgrade`` 恢复不含 ``DRY_RUN`` 的 ``external_status`` 约束并删除
``dry_run_plan`` 列。若库里已有 ``DRY_RUN`` 引用，约束无法容纳，迁移**拒绝
静默改写事实**并报错，要求先导出/清理演练记录。演练记录本身不是训练产物，
清理不丢失真实训练数据。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.training_guards import (
    drop_training_guards,
    install_training_guards,
)
from sqlalchemy.dialects import postgresql

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None

_TABLE = "external_training_references"
_COLUMN = "dry_run_plan"
# inspector.get_check_constraints 返回含命名约定前缀的完整名；而
# batch.create/drop_constraint 传入的是**逻辑名**，batch 会再套一层
# ``ck_<table>_<name>``。两套名字必须分开，否则会得到双重前缀。
_STATUS_CONSTRAINT = "external_status_allowed"
_DRY_RUN_CONSTRAINT = "dry_run_plan_is_dry_run"
_STATUS_CONSTRAINT_FULL = f"ck_{_TABLE}_external_status_allowed"
_DRY_RUN_CONSTRAINT_FULL = f"ck_{_TABLE}_dry_run_plan_is_dry_run"
_JSON_TYPE = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True), "postgresql"
)

# 迁移是历史快照：取值冻结在 0067 时点，禁止引用运行时 Enum。
_STATUS_VALUES = (
    "'SUBMITTED', 'RUNNING', 'SUCCEEDED', 'FAILED', "
    "'CANCELLED', 'UNKNOWN', 'UNAVAILABLE', 'DRY_RUN'"
)
_STATUS_VALUES_WITHOUT_DRY_RUN = (
    "'SUBMITTED', 'RUNNING', 'SUCCEEDED', 'FAILED', " "'CANCELLED', 'UNKNOWN', 'UNAVAILABLE'"
)


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def _checks() -> dict[str, str]:
    return {
        str(item.get("name")): str(item.get("sqltext") or "").upper()
        for item in sa.inspect(op.get_bind()).get_check_constraints(_TABLE)
        if item.get("name")
    }


def _has_dry_run_rows() -> bool:
    # 表名为迁移常量，无用户输入。
    count = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT COUNT(*) FROM {_TABLE} WHERE external_status = 'DRY_RUN'"  # noqa: S608
            )
        )
        .scalar_one()
    )
    return bool(count)


def upgrade() -> None:
    bind = op.get_bind()
    exists = _columns()
    checks = _checks()
    status_sql = checks.get(_STATUS_CONSTRAINT_FULL)
    status_needs_rebuild = status_sql is not None and "DRY_RUN" not in status_sql
    status_needs_create = status_sql is None
    dry_check_missing = _DRY_RUN_CONSTRAINT_FULL not in checks
    column_missing = _COLUMN not in exists

    if column_missing or status_needs_rebuild or status_needs_create or dry_check_missing:
        # 先卸载触发器：SQLite 的 batch 重建会丢掉它们，且状态迁移触发器必须
        # 在迁移后按新规则重装。
        drop_training_guards(bind)
        with op.batch_alter_table(_TABLE) as batch:
            if column_missing:
                batch.add_column(sa.Column(_COLUMN, _JSON_TYPE, nullable=True))
            if status_needs_rebuild:
                batch.drop_constraint(_STATUS_CONSTRAINT, type_="check")
            if status_needs_rebuild or status_needs_create:
                batch.create_check_constraint(
                    _STATUS_CONSTRAINT,
                    f"external_status IN ({_STATUS_VALUES})",
                )
            if dry_check_missing:
                batch.create_check_constraint(
                    _DRY_RUN_CONSTRAINT,
                    f"{_COLUMN} IS NULL OR execution_mode = 'DRY_RUN'",
                )
        install_training_guards(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_dry_run_rows():
        raise RuntimeError(
            "存在 DRY_RUN 外部训练引用，0067 无法无损回滚：请先导出或清理这些"
            "演练记录后再降级（演练记录不是训练产物，不涉及真实数据丢失）"
        )
    exists = _columns()
    checks = _checks()
    status_sql = checks.get(_STATUS_CONSTRAINT_FULL)
    status_needs_restore = status_sql is not None and "DRY_RUN" in status_sql
    drop_dry_check = _DRY_RUN_CONSTRAINT_FULL in checks
    column_present = _COLUMN in exists

    if status_needs_restore or drop_dry_check or column_present:
        drop_training_guards(bind)
        with op.batch_alter_table(_TABLE) as batch:
            if drop_dry_check:
                batch.drop_constraint(_DRY_RUN_CONSTRAINT, type_="check")
            if status_needs_restore:
                batch.drop_constraint(_STATUS_CONSTRAINT, type_="check")
            if column_present:
                batch.drop_column(_COLUMN)
            if status_needs_restore:
                batch.create_check_constraint(
                    _STATUS_CONSTRAINT,
                    f"external_status IN ({_STATUS_VALUES_WITHOUT_DRY_RUN})",
                )
        install_training_guards(bind)
