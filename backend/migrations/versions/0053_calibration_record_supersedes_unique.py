"""标定记录修订链唯一性守卫（G-16）

Revision ID: 0053
Revises: 0052
Create Date: 2026-11-18

``calibration_records.supersedes_id`` 此前只有 ``(project_id, supersedes_id)``
复合外键：能保证"指向同项目的真实记录"，却**拦不住两条记录同时替代同一条
前驱**（修订链分叉）。分叉之后无法判断某条记录属于哪一支，版本链失去意义。

本迁移加部分唯一索引 ``uq_calibration_records_project_supersedes``：

- ``supersedes_id IS NULL`` 的行（草稿、首版）不参与唯一性，因此用部分索引；
- PostgreSQL 与 SQLite 都支持部分唯一索引。

**存量数据可能已经分叉**：直接建索引只会在数据库层抛一个缺少上下文的
duplicate key error，运维不知道是哪几条记录。所以升级前先扫描两类问题：

- 同一 ``(project_id, supersedes_id)`` 出现多条（分叉）；
- ``supersedes_id = id``（自引用）。

命中任意一类就逐行打印 ``project_id`` / ``supersedes_id`` / 记录 id 后
**失败退出**，绝不静默删除或改写数据——链怎么合并属于业务判断，
迁移无权替用户决定。
"""

from __future__ import annotations

import sys

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_calibration_records_project_supersedes"

_FORK_ROWS = sa.text(
    """
    SELECT c.project_id, c.supersedes_id, c.id
    FROM calibration_records AS c
    JOIN (
        SELECT project_id, supersedes_id
        FROM calibration_records
        WHERE supersedes_id IS NOT NULL
        GROUP BY project_id, supersedes_id
        HAVING COUNT(*) > 1
    ) AS dup
      ON dup.project_id = c.project_id
     AND dup.supersedes_id = c.supersedes_id
    ORDER BY c.project_id, c.supersedes_id, c.id
    """
)

_SELF_REFERENCE_ROWS = sa.text(
    """
    SELECT project_id, supersedes_id, id
    FROM calibration_records
    WHERE supersedes_id IS NOT NULL
      AND supersedes_id = id
    ORDER BY project_id, id
    """
)


def _index_exists() -> bool:
    inspector = sa.inspect(op.get_bind())
    return INDEX_NAME in {index["name"] for index in inspector.get_indexes("calibration_records")}


def _print_conflicts(label: str, rows: list[sa.engine.Row]) -> None:
    if not rows:
        return
    print(f"[0053] {label}（共 {len(rows)} 行）：", file=sys.stderr)
    for project_id, supersedes_id, record_id in rows:
        print(
            f"[0053] {label} project_id={project_id} "
            f"supersedes_id={supersedes_id} record_id={record_id}",
            file=sys.stderr,
        )


def _assert_no_supersedes_conflicts() -> None:
    """建索引前先证明存量数据满足唯一性；不满足则逐行打印并失败退出。"""
    bind = op.get_bind()
    forks = list(bind.execute(_FORK_ROWS))
    self_references = list(bind.execute(_SELF_REFERENCE_ROWS))
    if not forks and not self_references:
        return
    _print_conflicts("修订链分叉", forks)
    _print_conflicts("supersedes_id 自引用", self_references)
    raise RuntimeError(
        "存在不满足唯一性的标定记录修订链（分叉 "
        f"{len(forks)} 行、自引用 {len(self_references)} 行）。"
        "本迁移不做数据改写：请先人工确认并修复上述记录后重新执行 upgrade。"
    )


def upgrade() -> None:
    _assert_no_supersedes_conflicts()
    if _index_exists():
        return
    op.create_index(
        INDEX_NAME,
        "calibration_records",
        ["project_id", "supersedes_id"],
        unique=True,
        postgresql_where=sa.text("supersedes_id IS NOT NULL"),
        sqlite_where=sa.text("supersedes_id IS NOT NULL"),
    )


def downgrade() -> None:
    if _index_exists():
        op.drop_index(INDEX_NAME, table_name="calibration_records")
