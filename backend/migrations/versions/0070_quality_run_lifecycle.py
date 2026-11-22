"""V31-030：质量运行生命周期 ``lifecycle``

Revision ID: 0070
Revises: 0069
Create Date: 2026-11-22

为什么要把生命周期单独拆出来
---------------------------
``quality_runs.status`` 原本只有一个枚举：``PASSED`` / ``FAILED`` /
``INCOMPLETE``。这三者都是**结论**——"已经算完了，结果是什么"。异步质检还需要
表达"排队中 / 执行中 / 执行失败待重投"，如果把这些值塞进同一个枚举：

- 读取方必须靠字符串白名单猜"这一行算完了没有"，任何遗漏都会把"没算完"
  读成"通过"；
- ``started_at`` / ``finished_at`` 是 NOT NULL，排队中的运行没有真实时间可写，
  只能填假时间，等于伪造执行证据；
- ``FAILED`` 会同时表示"规则判定失败"和"执行崩了"，两者需要完全不同的处置
  （前者转返工，后者重投）。

因此本迁移新增与结论**正交**的 ``lifecycle`` 列，并放开三个列的可空性：

- ``lifecycle``：``QUEUED`` / ``RUNNING`` / ``SUCCEEDED`` / ``FAILED`` /
  ``RETRYING`` / ``CANCELLED``，默认 ``SUCCEEDED``——0070 之前落库的每一行
  都是"已经算完的结论"，回填即旧语义；
- ``status`` / ``started_at`` / ``finished_at`` 改为可空：未完成的运行没有结论，
  也没有可信时间；
- ``attempt`` / ``max_attempts``：重投次数与其上限，``attempt <= max_attempts``；
- ``error_code`` / ``error_summary``：**执行**失败的原因，成对出现；只有
  ``FAILED`` 必须填写，重投期间保留作为"为什么又跑了一遍"的依据。

约束
----
``lifecycle_fields_consistent`` 把领域不变量落到数据库：``QUEUED`` 不得有结论
与时间，``RUNNING`` / ``RETRYING`` 不得有结论，``SUCCEEDED`` 必须有结论与起止
时间且不得携带执行错误，``FAILED`` 必须有执行错误，``CANCELLED`` 必须有结束时
间。任何绕过领域层的写入都写不出"没跑却通过"的行。

``finished_after_started`` 改为空值容忍：未完成的行允许只有一个时间（或都没有）。

SQLite 与 PostgreSQL
--------------------
SQLite 不支持 ``ALTER COLUMN``，本迁移用 ``batch_alter_table`` 重建表；新增列
带 ``server_default``，存量行在重建时即完成回填。

约束全名与 ``op.f()``
----------------------
``Base.metadata`` 的命名约定会对传入的约束名再套一次 ``ck_<table>_`` 前缀。
因此这里传**数据库中的全名**并包进 ``op.f()``（标记"命名约定已应用"），
否则删除时会去找 ``ck_quality_runs_ck_quality_runs_...`` 而失败，创建时也会
生成重复前缀的名字。

回滚
----
回滚会恢复 ``status`` / ``started_at`` / ``finished_at`` 的 NOT NULL 与旧 CHECK。
若存在未完成（``lifecycle <> 'SUCCEEDED'``）的行，回滚无法无损进行：这些行
没有可用结论，强行回填就是伪造质量证据。因此 ``downgrade`` 在检测到此类行时
直接报错并要求先处理，绝不静默丢数据。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None

_TABLE = "quality_runs"
_LIFECYCLES = "'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'RETRYING', 'CANCELLED'"
_RUN_STATUSES = "'PASSED', 'FAILED', 'INCOMPLETE'"

# 数据库中的真实约束名（命名约定 ck_%(table_name)s_%(constraint_name)s）。
_CK_STATUS = "ck_quality_runs_status_allowed"
_CK_FINISHED = "ck_quality_runs_finished_after_started"
_CK_LIFECYCLE_ALLOWED = "ck_quality_runs_lifecycle_allowed"
_CK_ATTEMPT_POSITIVE = "ck_quality_runs_attempt_positive"
_CK_MAX_ATTEMPTS_POSITIVE = "ck_quality_runs_max_attempts_positive"
_CK_ATTEMPT_WITHIN_MAX = "ck_quality_runs_attempt_within_max"
_CK_ERROR_PAIRED = "ck_quality_runs_error_fields_paired"
_CK_LIFECYCLE_FIELDS = "ck_quality_runs_lifecycle_fields_consistent"

_STATUS_CHECK = f"status IS NULL OR status IN ({_RUN_STATUSES})"
_FINISHED_CHECK = "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at"
_ERROR_PAIR_CHECK = (
    "(error_code IS NULL AND error_summary IS NULL)"
    " OR (error_code IS NOT NULL AND error_summary IS NOT NULL)"
)
_LIFECYCLE_FIELDS_CHECK = (
    "(lifecycle = 'QUEUED'"
    " AND status IS NULL AND started_at IS NULL"
    " AND finished_at IS NULL AND error_code IS NULL)"
    " OR (lifecycle IN ('RUNNING', 'RETRYING')"
    " AND status IS NULL AND started_at IS NOT NULL AND finished_at IS NULL)"
    " OR (lifecycle = 'SUCCEEDED'"
    " AND status IS NOT NULL AND started_at IS NOT NULL AND finished_at IS NOT NULL"
    " AND error_code IS NULL)"
    " OR (lifecycle = 'FAILED'"
    " AND status IS NULL AND started_at IS NOT NULL AND finished_at IS NOT NULL"
    " AND error_code IS NOT NULL)"
    # 取消可能发生在重投之后：保留上一次失败原因（成对约束仍由
    # error_fields_paired 兜底），不强制清空。
    " OR (lifecycle = 'CANCELLED'"
    " AND status IS NULL AND finished_at IS NOT NULL)"
)


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def _check_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {str(item.get("name") or "") for item in inspector.get_check_constraints(_TABLE)}


def upgrade() -> None:
    columns = _columns()
    checks = _check_names()
    add_lifecycle = "lifecycle" not in columns
    add_execution_columns = "attempt" not in columns or "max_attempts" not in columns
    add_error_columns = "error_code" not in columns or "error_summary" not in columns
    replace_constraints = _CK_LIFECYCLE_FIELDS not in checks
    if not any((add_lifecycle, add_execution_columns, add_error_columns, replace_constraints)):
        return

    with op.batch_alter_table(_TABLE) as batch:
        if add_lifecycle:
            batch.add_column(
                sa.Column(
                    "lifecycle",
                    sa.String(length=16),
                    nullable=False,
                    server_default=sa.text("'SUCCEEDED'"),
                )
            )
        if add_execution_columns:
            batch.add_column(
                sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1"))
            )
            batch.add_column(
                sa.Column(
                    "max_attempts",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("1"),
                )
            )
        if add_error_columns:
            batch.add_column(sa.Column("error_code", sa.String(length=64), nullable=True))
            batch.add_column(sa.Column("error_summary", sa.String(length=1000), nullable=True))
        batch.alter_column("status", existing_type=sa.String(length=16), nullable=True)
        batch.alter_column("started_at", existing_type=sa.DateTime(timezone=True), nullable=True)
        batch.alter_column("finished_at", existing_type=sa.DateTime(timezone=True), nullable=True)
        if replace_constraints:
            if _CK_STATUS in checks:
                batch.drop_constraint(op.f(_CK_STATUS), type_="check")
            batch.create_check_constraint(op.f(_CK_STATUS), _STATUS_CHECK)
            batch.create_check_constraint(
                op.f(_CK_LIFECYCLE_ALLOWED), f"lifecycle IN ({_LIFECYCLES})"
            )
            if _CK_FINISHED in checks:
                batch.drop_constraint(op.f(_CK_FINISHED), type_="check")
            batch.create_check_constraint(op.f(_CK_FINISHED), _FINISHED_CHECK)
            batch.create_check_constraint(op.f(_CK_ATTEMPT_POSITIVE), "attempt >= 1")
            batch.create_check_constraint(op.f(_CK_MAX_ATTEMPTS_POSITIVE), "max_attempts >= 1")
            batch.create_check_constraint(op.f(_CK_ATTEMPT_WITHIN_MAX), "attempt <= max_attempts")
            batch.create_check_constraint(op.f(_CK_ERROR_PAIRED), _ERROR_PAIR_CHECK)
            batch.create_check_constraint(op.f(_CK_LIFECYCLE_FIELDS), _LIFECYCLE_FIELDS_CHECK)


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns()
    if "lifecycle" not in columns:
        return
    unfinished = bind.execute(
        sa.text(f"SELECT COUNT(*) FROM {_TABLE} WHERE lifecycle <> 'SUCCEEDED'")  # noqa: S608
    ).scalar_one()
    if unfinished:
        raise RuntimeError(
            f"存在 {unfinished} 条未完成的质量运行（lifecycle <> 'SUCCEEDED'），"
            "0070 无法无损回滚：这些行没有可用结论，回填 status/时间等于伪造质量"
            "证据。请先清理或导出这些运行后再降级。"
        )

    checks = _check_names()
    with op.batch_alter_table(_TABLE) as batch:
        for name in (
            _CK_LIFECYCLE_FIELDS,
            _CK_ERROR_PAIRED,
            _CK_ATTEMPT_WITHIN_MAX,
            _CK_MAX_ATTEMPTS_POSITIVE,
            _CK_ATTEMPT_POSITIVE,
            _CK_LIFECYCLE_ALLOWED,
        ):
            if name in checks:
                batch.drop_constraint(op.f(name), type_="check")
        if _CK_STATUS in checks:
            batch.drop_constraint(op.f(_CK_STATUS), type_="check")
        batch.create_check_constraint(op.f(_CK_STATUS), f"status IN ({_RUN_STATUSES})")
        if _CK_FINISHED in checks:
            batch.drop_constraint(op.f(_CK_FINISHED), type_="check")
        batch.create_check_constraint(op.f(_CK_FINISHED), "finished_at >= started_at")
        batch.alter_column("status", existing_type=sa.String(length=16), nullable=False)
        batch.alter_column("started_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.alter_column("finished_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        for name in ("error_summary", "error_code", "max_attempts", "attempt", "lifecycle"):
            if name in columns:
                batch.drop_column(name)
