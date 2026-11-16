"""JSON 数组列在 SQL 层的"任一元素命中"谓词。

## 为什么需要抽象

``episodes.scene_labels`` / ``episodes.device_ids`` / ``datasets.tags`` 都是
JSON/JSONB 数组列。要按"数组任一元素命中给定集合"过滤，必须把它展开成行集合
再判定，否则只能把整表取回内存筛选——那正是
``SqlEpisodeCandidateDirectory.list_candidates`` 的已知问题，检索端点不能复制。

PostgreSQL 与 SQLite 的展开函数不同（``jsonb_array_elements_text`` /
``json_each``），因此按方言分派。谓词函数显式接收方言名：调用方从
``Session.get_bind().dialect.name`` 取，测试可对两方言分别编译核对。

只有 PostgreSQL 与 SQLite 两个目标（生产 PostgreSQL、测试 SQLite）；
其他方言直接抛错，避免静默退化成"不过滤"。
"""

from __future__ import annotations

from sqlalchemy import Exists, exists, func, select
from sqlalchemy.orm import Session

SUPPORTED_DIALECTS = ("postgresql", "sqlite")


def session_dialect(session: Session) -> str:
    """取会话绑定方言；不在受支持列表内直接失败（不做静默降级）。"""
    name = session.get_bind().dialect.name
    if name not in SUPPORTED_DIALECTS:
        raise RuntimeError(f"未支持的数据库方言：{name}；JSON 数组检索缺少实现")
    return name


def json_array_any_of(
    column: object,
    values: tuple[str, ...] | list[str],
    *,
    dialect: str,
) -> Exists:
    """数组列中**任一**元素等于给定值之一。

    生成 ``EXISTS (SELECT 1 FROM <展开(column)> WHERE value IN (...))``，
    由数据库完成展开与比较。
    """
    items = tuple(dict.fromkeys(values))
    if not items:
        raise ValueError("json_array_any_of 至少需要一个候选值")
    if dialect == "postgresql":
        table = func.jsonb_array_elements_text(column).table_valued("value").alias("elem")
    elif dialect == "sqlite":
        table = func.json_each(column).table_valued("value").alias("elem")
    else:
        raise RuntimeError(f"未支持的数据库方言：{dialect}；JSON 数组检索缺少实现")
    elements = select(table.c.value).select_from(table).subquery("elem")
    return exists(select(1).select_from(elements).where(elements.c.value.in_(items)))
