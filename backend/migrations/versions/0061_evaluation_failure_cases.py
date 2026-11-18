"""N7-003：evaluations.failure_cases —— 结构化失败用例

Revision ID: 0061
Revises: 0059
Create Date: 2026-11-18

为什么是 JSON 列而不是独立表
----------------------------
失败用例的读取方式永远是"随一条评测整体读取"：评测详情、评测列表、以及
后续 Data Gap 聚合（N7-004）都按 ``evaluation_id`` 取全量事实。没有按
``category``/``episode_id`` 跨评测检索的既定需求，独立表只会带来一次 JOIN、
一批生命周期约束（评测不可变时子行也必须不可变）和孤儿行风险，收益不足。
列是 JSON 而不是多列拆开，是因为单条用例内部字段（evidence/observed 等）
形态可变，拆列会把契约固化在数据库层，后续扩展需要再迁移。

代价是"单行可无限膨胀"，因此写入侧有硬上限：最多 200 条、序列化后
64 KiB（见 ``domain/modelops/failure_cases.py``），与回调请求体上限一致地
受控。读取随评测返回，等价于固定上限分页。

存量取值：空数组，不伪造
------------------------
迁移前的评测没有任何结构化失败事实。新列为 ``NOT NULL DEFAULT '[]'``，
存量行因此得到空数组——它诚实地表示"没有报告任何失败用例"，而不是
"零失败"或"未测量"。不写示例数据、不按 metrics 反推、不从 report_ref
猜测：那都会把推断伪装成事实。

两条建库路径（必须幂等）
------------------------
``0021_stage5_modelops.py`` 用**活的 ORM 表**建 ``evaluations``。给 ORM
新增本列后：

1. **全新库**：升级到 0021 时本列与默认值已随 ORM 元数据建出，0061 必须是
   空操作，否则 ``duplicate column`` 让全新库升级失败（与 0043 同一路径分叉）；
2. **存量库**：0021 早已执行完，表里没有本列，必须 ``add_column`` 并按
   ``'[]'`` 回填（``NOT NULL DEFAULT`` 在 SQLite/PG 上都会填充存量行）。

守卫触发器
----------
本列登记进 ``modelops_guards._IMMUTABLE_WHEN['evaluations']``：评测成功后再改
失败明细等于事后篡改 Data Gap 的证据。存量库上已安装的触发器正文不含本列，
因此加列后必须 ``drop_modelops_guards`` + ``install_modelops_guards`` 重装一轮
（SQLite 的 ``CREATE TRIGGER IF NOT EXISTS`` 不会更新已有触发器，只能拆装）。
全新库路径无需重装：0021 安装守卫时从当前源码生成，已包含本列。

回滚
----
``downgrade`` 先拆守卫再删列：SQLite 在 ``ALTER TABLE ... DROP COLUMN`` 时会
解析触发器正文，触发器若仍引用本列会直接报
``no such column`` 让回滚失败（0043 已踩过同款坑）。删列后按实际结构重装守卫。
回滚**丢弃**已写入的失败明细；它们是回滚前的评测事实，删除后不可恢复——
这是缩容操作的固有代价，升级前的数据（metrics/verdict 等）不受影响。

编号说明
--------
任务预留编号为 0061（0060 未被使用：N6-003 复用既有 JSON 列、无迁移）。
集成时本迁移串在 0059 之后。``down_revision`` 指向 0059，
0058–0060 由并行任务占用，合并时按实际链头线性化，避免在本分支引用尚未存在
的迁移文件导致 ``alembic upgrade head`` 无法解析。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.modelops_guards import (
    drop_modelops_guards,
    install_modelops_guards,
)
from sqlalchemy.dialects.postgresql import JSONB

revision = "0061"
down_revision = "0059"
branch_labels = None
depends_on = None

_TABLE = "evaluations"
_COLUMN = "failure_cases"
_JSON_TYPE = sa.JSON().with_variant(JSONB, "postgresql")
_EMPTY_ARRAY = sa.text("'[]'")


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    connection = op.get_bind()
    if _COLUMN in _columns():
        # 全新库：0021 已按当前 ORM 建出本列与默认值。
        return
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, _JSON_TYPE, nullable=False, server_default=_EMPTY_ARRAY),
    )
    # 存量触发器正文不含本列；拆装一轮让"成功后不可改失败明细"真正生效。
    drop_modelops_guards(connection)
    install_modelops_guards(connection)


def downgrade() -> None:
    connection = op.get_bind()
    if _COLUMN not in _columns():
        return
    # 必须先拆守卫：SQLite 删列时会校验触发器正文，引用已删列直接失败。
    drop_modelops_guards(connection)
    op.drop_column(_TABLE, _COLUMN)
    install_modelops_guards(connection)
