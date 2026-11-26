"""G6 语义近似去重：``episodes.text_digest`` 指纹列 + pg_trgm 索引（V52-031）

Revision ID: 0084
Revises: 0082
Create Date: 2026-11-26

背景
----
蓝图 G6 要求"语义近似去重"以防止训练集污染（N1）。平台此前只有数据集域的
**元数据**近重复检测（``domain/datasets/near_duplicate.py``：时间区间/场景
标签/时长），没有文本级去重载体。本迁移给样本载体 ``episodes`` 增加：

- ``text_digest``：``language_instruction`` 的 ``sha256:`` 规范化指纹，
  支撑"完全重复"的 B-tree 精确命中（近似判定仍由 pg_trgm/领域算法完成）；
- ``ix_episodes_project_text_digest``：``(project_id, text_digest)``，
  精确重复查询永远带项目隔离条件，索引必须包含 ``project_id``；
- PostgreSQL 侧 ``pg_trgm`` 扩展 + ``language_instruction`` 的 GIN 三元组
  索引，支撑 ``similarity()`` / ``%`` 的**索引扫描**（禁止全表扫）。

为什么选 ``episodes`` 而不是 ``annotation_sample_sets``
------------------------------------------------------
任务的建议载体是 ``annotation_sample_sets``，但实测该表**没有文本列**
（只有 ``code`` / ``name`` 元数据与 ``snapshot_sha256`` 选择快照哈希）。
把 ``text_digest`` 加在那里只能得到一个永远无法回填的死列。平台真正的
"样本载体"是 ``episodes``：``annotation_sample_episodes`` 引用它，
``language_instruction`` 是唯一的样本文本列（``String(2000)``，由
``application/datasources/episode_materialization.py`` 真实写入）。

生产事实与选型
--------------
生产 PostgreSQL 16.16 alpine **未安装 pgvector**（``pg_available_extensions``
无 ``vector``），``pg_trgm`` 1.6 **可用**。因此本迁移只安装 pg_trgm；
向量能力在领域层是**接口预留**（``DedupBackend.VECTOR`` 显式报"未实现"），
不在迁移里假装创建 vector 索引。

历史数据
--------
升级时对存量 ``language_instruction`` 做一次性指纹回填（规范化 = 去首尾空白
+ 连续空白压单空格，与 ``domain/annotation/semantic_dedup.py`` 的
``normalize_text`` 同口径）。**这里刻意不 import 领域函数**：迁移是历史快照，
应用代码后续演进不能改变已执行迁移的行为，重复一份 8 行规范化逻辑是正确代价。
新行的指纹由后续写路径接线填充；读路径在 NULL 时按需现算，不依赖回填完整性。

双方言（MEM-INC-013）
--------------------
- ``OP_ADD_COLUMN``：SQLite/PG 都支持，不触发 SQLite 的 ``batch_alter_table``
  重建（``episodes`` 上有触发器，重建表有 MEM-INC-020 的踩坑风险）；
- ``pg_trgm`` 扩展与 GIN 索引**只在 PG 分支**执行（SQLite 无此类对象，
  硬写会在 ``no such extension`` 处失败）；
- 索引名是模块常量（非用户输入），用 ``IF NOT EXISTS`` 保证可重入。

回滚
----
``downgrade`` 只删本迁移创建的索引与列，**不删 pg_trgm 扩展**：扩展是数据库级
共享能力（可能被其他迁移/手工运维使用），删掉它属于超出本迁移范围的破坏性操作。
因此 downgrade 是 upgrade 的"数据对象逆操作"，不是扩展级的完全还原。
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from alembic import op

revision = "0084"
down_revision = "0082"  # main 实测 head：0074→0078→0081→0080→0083→0082
branch_labels = None
depends_on = None

_TABLE = "episodes"
_COLUMN = "text_digest"
_DIGEST_PREFIX = "sha256:"
#: 指纹列长度：``sha256:`` + 64 位十六进制（与库内既有摘要列同口径）。
_DIGEST_LENGTH = 71
_BTREE_INDEX = "ix_episodes_project_text_digest"
_TRGM_INDEX = "ix_episodes_language_instruction_trgm"


def _dialect() -> str:
    """读取当前连接方言；DB 探测（进程内）不是 client 输入，可安全分支。"""
    return op.get_bind().dialect.name


def _normalize_instruction(value: str) -> str:
    """迁移内快照版规范化：去首尾空白 + 连续空白压单空格。"""
    return " ".join(value.split())


def _backfill_text_digest() -> None:
    """为存量 ``language_instruction`` 回填指纹（分批 executemany，单次迁移）。

    分页用主键游标而不是一次性 ``fetchall``：大表上把全部行读进内存会让迁移
    在低配机器上 OOM。每批 500 行是经验值，足够小又不会让往返次数爆炸。
    """
    bind = op.get_bind()
    batch_size = 500
    last_id: object | None = None
    while True:
        sql = (
            f"SELECT id, language_instruction FROM {_TABLE} "  # noqa: S608 - 表名是常量
            f"WHERE language_instruction IS NOT NULL AND {_COLUMN} IS NULL"
        )
        params: dict[str, object] = {}
        if last_id is not None:
            sql += " AND id > :last_id"
            params["last_id"] = last_id
        sql += " ORDER BY id LIMIT :batch_size"
        params["batch_size"] = batch_size
        rows = bind.execute(sa.text(sql), params).all()
        if not rows:
            return
        updates: list[dict[str, object]] = []
        for row in rows:
            normalized = _normalize_instruction(str(row.language_instruction))
            if not normalized:
                continue
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            updates.append({"episode_id": row.id, "digest": f"{_DIGEST_PREFIX}{digest}"})
        if updates:
            bind.execute(
                sa.text(
                    f"UPDATE {_TABLE} SET {_COLUMN} = :digest WHERE id = :episode_id"  # noqa: S608
                ),
                updates,
            )
        last_id = rows[-1].id


def _create_trigram_indexes() -> None:
    """PostgreSQL 专有：安装 pg_trgm 扩展并建 GIN 三元组索引。"""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {_TRGM_INDEX} ON {_TABLE} USING gin (language_instruction gin_trgm_ops)"
    )


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=_DIGEST_LENGTH), nullable=True))
    _backfill_text_digest()
    op.create_index(_BTREE_INDEX, _TABLE, ["project_id", _COLUMN])
    if _dialect() == "postgresql":
        _create_trigram_indexes()


def downgrade() -> None:
    if _dialect() == "postgresql":
        # 只删本迁移建的索引；扩展是数据库级共享能力，不在此处删除。
        op.execute(f"DROP INDEX IF EXISTS {_TRGM_INDEX}")
    op.drop_index(_BTREE_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, _COLUMN)
