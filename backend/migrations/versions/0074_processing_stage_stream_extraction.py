"""V51-001：``processing_*`` 三张表的 ``stage`` 允许值补入 ``STREAM_EXTRACTION``

Revision ID: 0074
Revises: 0073
Create Date: 2026-11-25

背景（真实缺陷，代价极高）
--------------------------
迁移 0056 / 0057 / 0068 用 CHECK 约束把 ``stage`` 允许值锁成当时的 8 个阶段。
V51-001 新增了第 9 个阶段 ``STREAM_EXTRACTION``（枚举改了、编排器接入了），
但**没有任何人同步这三张表的数据库约束**。

后果：Pipeline 推进到 ``STREAM_EXTRACTION`` 时写库撞约束——

    psycopg.errors.CheckViolation:
    new row violates check constraint "ck_processing_runs_stage_allowed"

运行被判定可重试（``RETRYING``），但**重试永远不可能成功**。

**为什么必须一次修三张表**
``processing_runs`` / ``processing_run_steps`` / ``processing_artifacts``
各自有一份独立的 stage CHECK 约束。只修第一张时，运行能过 ``runs`` 的约束，
却在写 ``steps`` 时挂掉——表现为"修了一半"的假象。这是实测踩到的：
只改 runs 后错误从 runs 转到了 steps。

**调试过程中发现的更深的坑**
``pg_constraint`` 里同名约束可能分属**不同 schema**（测试库 ``ddp_v51xxx``、
调试库 ``ddp_dbg_xxx`` 各有一张 ``processing_runs``，共用同一个约束名）。
用 ``conrelid = 'processing_runs'::regclass`` 查会命中 search_path 里的那一张，
而 ``ALTER TABLE processing_runs DROP CONSTRAINT`` 也只作用于默认 schema——
若默认 schema 不是 ``public``，就会"删了别的库的约束、public 的没动"。
因此本迁移**所有语句显式限定 ``public.``**。

**PostgreSQL 的一个陷阱**
同一张表上允许存在多个**同名** CHECK 约束（约束名不是唯一键，OID 才是）。
一条 ``DROP CONSTRAINT`` 只删一个。历史重复应用会让库里堆积多条同名约束，
旧的那条仍拦住新值 → **迁移显示成功但功能无效**。
因此 ``_drop_all_stage_constraints`` 循环删到干净为止。

防回归
------
``backend/tests/migrations/test_processing_stage_constraints.py``：
**``ProcessingStage`` 枚举的每个值都必须在三张表的约束允许集合内**。
下次新增阶段忘改约束，CI 直接红，而不是等运行时撞车。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None

#: (表名, 约束名)。三张表各有一份 stage 约束，必须同时改。
_STAGE_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    ("processing_runs", "ck_processing_runs_stage_allowed"),
    ("processing_run_steps", "ck_processing_run_steps_stage_allowed"),
    ("processing_artifacts", "ck_processing_artifacts_stage_allowed"),
)

#: 0074 之后的允许集合。顺序与 ``ProcessingStage`` 枚举一致
#: （``STREAM_EXTRACTION`` 在 ``EPISODE_SEGMENTATION`` 前），便于人工比对。
_STAGE_VALUES_AFTER_0074: tuple[str, ...] = (
    "FILE_VALIDATION",
    "METADATA_EXTRACTION",
    "FORMAT_PARSING",
    "TIME_ALIGNMENT",
    "CALIBRATION_VALIDATION",
    "STREAM_EXTRACTION",
    "EPISODE_SEGMENTATION",
    "DATA_CLEANING",
    "QUALITY_PRE_CHECK",
)

_STAGE_VALUES_BEFORE_0074: tuple[str, ...] = tuple(
    value for value in _STAGE_VALUES_AFTER_0074 if value != "STREAM_EXTRACTION"
)

#: ``processing_artifacts.kind`` 的约束。
#: **这是第二个被遗漏的约束**：新增 ``ProcessingArtifactKind.STREAM_EXTRACTION``
#: 时同样没人同步它，导致产物插入被拒。实测踩到——修好 stage 后错误从
#: "stage_allowed" 变成 "kind_allowed"，说明**逐个修是不够的**，
#: 必须把所有受影响的枚举约束一次改完。
_KIND_CONSTRAINT: tuple[str, str] = ("processing_artifacts", "ck_processing_artifacts_kind_allowed")

_KIND_VALUES_AFTER_0074: tuple[str, ...] = (
    "METADATA",
    "CANONICAL",
    "TIME_ALIGNMENT_REPORT",
    "STREAM_EXTRACTION",
)

_KIND_VALUES_BEFORE_0074: tuple[str, ...] = tuple(
    value for value in _KIND_VALUES_AFTER_0074 if value != "STREAM_EXTRACTION"
)


def _stage_list(values: tuple[str, ...]) -> str:
    """渲染成 SQL 的 IN 列表（值是代码常量，非用户输入）。"""
    return ", ".join(f"'{value}'" for value in values)


def _supports_constraint_ddl() -> bool:
    """本迁移的 CHECK 变更是否可在当前方言上执行。

    **只在 PostgreSQL 上做**。SQLite 不支持 ``ALTER TABLE ... DROP CONSTRAINT``；
    用 ``batch_alter_table`` 重建表虽然理论上可行，但 SQLAlchemy 会把旧表上的
    CHECK 一并复制到新表，再 ``create_check_constraint`` 同名就会报
    "constraint ... already exists"（实测踩到，且没有干净的绕法）。

    取舍：SQLite 只用于 ``tests/migrations/`` 的迁移链测试——它验证的是
    "迁移链能跑通"，不是"约束语义正确"。约束语义由真实 PostgreSQL 保证
    （本迁移在 PG 上做过 upgrade→downgrade→upgrade 往返验证）。
    **生产与集成环境都是 PostgreSQL**，因此跳过 SQLite 的约束变更不影响正确性。

    这条取舍必须显式写在代码里——沉默地"SQLite 上什么也没做"会让后来者
    以为约束在 SQLite 上也生效了。
    """
    return _dialect() == "postgresql"


def _dialect() -> str:
    """当前连接的数据库方言名（``postgresql`` / ``sqlite``）。"""
    return op.get_bind().dialect.name


def _relation(table: str) -> str:
    """表引用。

    **PostgreSQL 必须限定 ``public.``**（模块文档「调试过程中发现的更深的坑」：
    同名约束可能分属不同 schema，不限定会作用于 search_path 里的那一张）。

    **SQLite 不能加 schema 前缀**：它没有 schema 概念，``public.processing_runs``
    会被当成表名而报 ``no such table``。迁移测试跑在 SQLite 上（``tests/migrations``
    的既有约定），因此必须按方言分支。这是实测踩到的——加了 public. 后
    4 个迁移测试同时红。
    """
    if _dialect() == "postgresql":
        return f"public.{table}"
    return table


def _drop_all(connection: sa.Connection, table: str, constraint: str) -> None:
    """删掉 ``public.<table>`` 上**所有**同名 CHECK 约束。

    PostgreSQL 允许同表多个同名约束，一条 DROP 只删一个。循环到干净为止，
    否则旧约束会继续拦住新值（迁移"成功"但功能无效）。
    """
    # SQLite 无法枚举/删除 CHECK 约束（也没有 pg_constraint 表）：
    # batch 模式重建表时旧 CHECK 自然消失，因此这里直接返回。
    # **这个分支必须在任何 pg_constraint 查询之前**——否则 SQLite 会报
    # "unrecognized token: :"（它不认 ::regclass 语法）。
    if _dialect() != "postgresql":
        return

    relation = _relation(table)
    for _ in range(64):  # 上界防御：正常库最多 1~2 条
        # 注意：SQLAlchemy 的 text() 会把 ``:word`` 当绑定参数。这里不能用
        # ``:relation::regclass``——冒号会被解析成参数名的一部分而报语法错误。
        # 关系名来自本模块常量（非用户输入），直接内联即可。
        remaining = connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM pg_constraint "  # noqa: S608 - 关系名来自模块常量
                f"WHERE conname = :name AND conrelid = '{relation}'::regclass"
            ),
            {"name": constraint},
        ).scalar()
        if not remaining:
            return
        op.execute(sa.text(f"ALTER TABLE {relation} DROP CONSTRAINT {constraint}"))  # noqa: S608 - 表名/约束名/值均为模块常量，无外部输入
    raise RuntimeError(
        f"无法清空 {relation}.{constraint}：疑似异常多的重复约束，请人工检查 pg_constraint"
    )


def _replace(
    connection: sa.Connection, table: str, constraint: str, values: tuple[str, ...]
) -> None:
    """替换一张表的 stage 约束（仅 PostgreSQL）。"""
    if not _supports_constraint_ddl():
        return
    _drop_all(connection, table, constraint)
    op.execute(
        sa.text(
            f"ALTER TABLE {_relation(table)} ADD CONSTRAINT {constraint} "  # noqa: S608
            f"CHECK (stage IN ({_stage_list(values)}))"
        )
    )


def _replace_kind(connection: sa.Connection, values: tuple[str, ...]) -> None:
    """替换 ``processing_artifacts.kind`` 的允许值约束（仅 PostgreSQL）。"""
    if not _supports_constraint_ddl():
        return
    table, constraint = _KIND_CONSTRAINT
    _drop_all(connection, table, constraint)
    op.execute(
        sa.text(
            f"ALTER TABLE {_relation(table)} ADD CONSTRAINT {constraint} "  # noqa: S608
            f"CHECK (kind IN ({_stage_list(values)}))"
        )
    )


def upgrade() -> None:
    connection = op.get_bind()

    # 先确认存量行没有未知 stage：理论上不存在（旧约束拦着），
    # 但若有环境手工改过库，给出可定位的错误而不是让 ADD CONSTRAINT 抛含糊异常。
    for table, _constraint in _STAGE_CONSTRAINTS:
        unknown = connection.execute(
            sa.text(
                f"SELECT DISTINCT stage FROM {_relation(table)} "  # noqa: S608 - 表名/约束名/值均为模块常量，无外部输入
                f"WHERE stage IS NOT NULL AND stage NOT IN ({_stage_list(_STAGE_VALUES_AFTER_0074)})"
            )
        ).fetchall()
        if unknown:
            names = ", ".join(str(row[0]) for row in unknown)
            raise RuntimeError(
                f"{table} 中存在未知 stage 值：{names}；请先人工确认这些行，再重新执行本迁移"
            )

    for table, constraint in _STAGE_CONSTRAINTS:
        _replace(connection, table, constraint, _STAGE_VALUES_AFTER_0074)
    _replace_kind(connection, _KIND_VALUES_AFTER_0074)


def downgrade() -> None:
    """回滚前必须确认没有行使用 ``STREAM_EXTRACTION``。

    强行回滚会让这些行在重建约束时失败（或数据被删）。因此显式拒绝并说明
    处置方式——数据修复类迁移不应悄悄回退。
    """
    connection = op.get_bind()
    in_use: list[str] = []
    for table, _constraint in _STAGE_CONSTRAINTS:
        count = connection.execute(
            sa.text(f"SELECT COUNT(*) FROM {_relation(table)} WHERE stage = 'STREAM_EXTRACTION'")  # noqa: S608 - 表名/约束名/值均为模块常量，无外部输入
        ).scalar()
        if count:
            in_use.append(f"{table}（{count} 行）")
    if in_use:
        raise RuntimeError(
            "仍有运行记录使用 STREAM_EXTRACTION 阶段，无法回滚："
            + "、".join(in_use)
            + "；请先清理或迁移这些记录"
        )

    kind_in_use = connection.execute(
        sa.text(
            f"SELECT COUNT(*) FROM {_relation('processing_artifacts')} "  # noqa: S608 - 表名/约束名/值均为模块常量，无外部输入
            "WHERE kind = 'STREAM_EXTRACTION'"
        )
    ).scalar()
    if kind_in_use:
        raise RuntimeError(
            f"仍有 {kind_in_use} 条 processing_artifacts 使用 STREAM_EXTRACTION kind，无法回滚"
        )

    for table, constraint in _STAGE_CONSTRAINTS:
        _replace(connection, table, constraint, _STAGE_VALUES_BEFORE_0074)
    _replace_kind(connection, _KIND_VALUES_BEFORE_0074)
