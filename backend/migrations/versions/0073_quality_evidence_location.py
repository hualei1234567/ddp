"""V51-010：质量结论的证据定位列（``evidence_location``）

Revision ID: 0073
Revises: 0072
Create Date: 2026-11-24

为什么单独加一列，而不是继续放 ``quality_findings.evidence``
------------------------------------------------------------
``evidence`` 是自由 JSON，规则想写什么就写什么；前端无法从中**可靠地**取出
"跳转到哪个时间、高亮哪一路流"。如果按约定去读 ``evidence.time_range``，
任何一条历史规则改了键名、或某条规则的证据结构不同，界面就会静默定位失败
——"点了没反应"比"没有这个功能"更难发现。

因此把定位提升为**结构化的三列**：

- ``evidence_start_time_ns`` / ``evidence_end_time_ns``：证据覆盖的绝对时间区间。
  用 ``BIGINT`` 而不是 ``NUMERIC``/``DOUBLE``：纳秒时间戳超过 IEEE-754 双精度
  可精确表示范围（2^53），浮点存储会静默改值，定位到错误的时间点。
- ``evidence_stream_id``：证据属于哪一路媒体流（机位/轨道）。
- ``evidence_field_path``：证据指向被检查对象的哪个字段。

三列**全部可空**且没有默认值：不是每条结论都有时间/流/字段维度（"缺必填标签"
只有字段、多路同步超差只有流的集合）。用空字符串当"没有"会让
``evidence_stream_id = ''`` 与"对象级结论"混为一谈，无法区分。

不变式（CHECK）
--------------
``evidence_time_range_consistent``：起止必须同时存在且起点早于终点。只有一端
的区间是无意义数据，宁可写入失败也不要让前端渲染半个区间。

``evidence_location_any`` 不加约束：三列全空的 finding 是合法的——它表示
"这条结论没有可定位的证据"，接口以 ``evidence_location: null`` 表达，而不是
返回三个 null 的"空定位对象"。

回滚
----
``downgrade`` 删除这三列。列都是本迁移新增、且只由本迁移写入，删除不丢失
迁移前的任何数据。已在列里写入的定位信息会随列一起消失——回滚后前端退回
"无定位"行为，不会出现读到半截数据的情况。

SQLite 与 PostgreSQL
--------------------
SQLite 不支持 ``ALTER COLUMN`` 之外的加列限制，但支持 ``ADD COLUMN``；
``batch_alter_table`` 在两端都能用，且加列不需要重建表（无 CHECK 依赖旧列）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None

_TABLE = "quality_findings"

_COLUMNS = (
    ("evidence_start_time_ns", sa.BigInteger()),
    ("evidence_end_time_ns", sa.BigInteger()),
    ("evidence_stream_id", sa.String(length=128)),
    ("evidence_field_path", sa.String(length=256)),
)

#: 数据库中的真实约束名（命名约定 ck_%(table_name)s_%(constraint_name)s）。
_CK_TIME_RANGE = "ck_quality_findings_evidence_time_range_consistent"
_TIME_RANGE_CHECK = (
    "(evidence_start_time_ns IS NULL AND evidence_end_time_ns IS NULL)"
    " OR (evidence_start_time_ns IS NOT NULL"
    " AND evidence_end_time_ns IS NOT NULL"
    " AND evidence_start_time_ns < evidence_end_time_ns)"
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
    missing = [(name, type_) for name, type_ in _COLUMNS if name not in columns]
    add_check = _CK_TIME_RANGE not in checks
    if not missing and not add_check:
        return

    with op.batch_alter_table(_TABLE) as batch:
        for name, type_ in missing:
            batch.add_column(sa.Column(name, type_, nullable=True))
        if add_check:
            batch.create_check_constraint(op.f(_CK_TIME_RANGE), _TIME_RANGE_CHECK)


def downgrade() -> None:
    columns = _columns()
    checks = _check_names()
    with op.batch_alter_table(_TABLE) as batch:
        if _CK_TIME_RANGE in checks:
            batch.drop_constraint(op.f(_CK_TIME_RANGE), type_="check")
        for name, _type in reversed(_COLUMNS):
            if name in columns:
                batch.drop_column(name)
