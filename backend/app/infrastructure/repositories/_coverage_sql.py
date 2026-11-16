"""缺口覆盖统计共用的 SQL 辅助：统计口径条件与跨方言 JSON 展开。

这些表达式被「开放轴」与「封闭轴」两个仓储模块共用，放在这里是为了保证
**覆盖范围口径只有一份实现**（``EPISODE_COVERAGE_STATUSES`` + 服务端 project
作用域），避免两处各写一份后漂移。

JSON 跨方言取舍（与既有数组轴一致）：

- SQLite 3.38+ 用表值函数 ``json_each(x)``，对数组元素逐行展开；对对象元素
  额外暴露 ``type`` 列——**对非对象元素调用 ``json_extract`` 会直接报
  "malformed JSON"**，必须先判类型。
- PostgreSQL 用 ``jsonb_array_elements[_text](x)``，``jsonb_typeof`` 对任意
  jsonb 都安全。

UUID 文本比较的方言差异：PostgreSQL 的 ``uuid::varchar`` 是带横线的小写，
SQLite 的 ``Uuid`` 列存 32 位无横线小写十六进制。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, case, func

from app.domain.episodes.state_machines import EPISODE_COVERAGE_STATUSES
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.episodes import EpisodeModel

_COVERAGE_STATUSES = tuple(sorted(item.value for item in EPISODE_COVERAGE_STATUSES))


def coverage_scope_conditions(scope: ProjectScope) -> tuple[Any, ...]:
    """缺口统计的 ``WHERE`` 条件：项目隔离 + 数据已落定的 Episode 状态。"""
    return (
        EpisodeModel.project_id == scope.project_id,
        EpisodeModel.status.in_(_COVERAGE_STATUSES),
    )


def expand_text_array(column: Any, *, dialect: str) -> Any:
    """把 JSON 字符串数组展开成单列 ``value``（每元素一行）。"""
    if dialect == "postgresql":
        return (
            func.jsonb_array_elements_text(column)
            .table_valued("value", joins_implicitly=True)
            .alias("coverage_value")
        )
    return (
        func.json_each(column).table_valued("value", joins_implicitly=True).alias("coverage_value")
    )


def expand_object_array(column: Any, *, dialect: str) -> Any:
    """把 JSON 对象数组展开成单列 ``value``（每元素一行）。

    SQLite 额外暴露 ``type`` 列，供 :func:`element_is_object` 判类型。
    """
    if dialect == "postgresql":
        return (
            func.jsonb_array_elements(column)
            .table_valued("value", joins_implicitly=True)
            .alias("coverage_element")
        )
    return (
        func.json_each(column)
        .table_valued("value", "type", joins_implicitly=True)
        .alias("coverage_element")
    )


def element_is_object(element: Any, *, dialect: str) -> Any:
    if dialect == "postgresql":
        return func.jsonb_typeof(element.c.value) == "object"
    return element.c.type == "object"


def json_string_field(value: Any, key: str, *, dialect: str) -> Any:
    """严格取 JSON 对象里的字符串字段；缺失、非字符串或空白一律 NULL。

    调用前必须已用 :func:`element_is_object` 过滤，否则 SQLite 会对非对象元素
    的 ``json_type`` / ``json_extract`` 报 "malformed JSON"。

    ``trim`` + ``nullif``：空串/纯空白不是合法标识，必须按"取不出标识"处理，
    否则 ``{"code": ""}`` 会被当成一个名为空字符串的维度值。
    """
    if dialect == "postgresql":
        extracted = case(
            (func.jsonb_typeof(value.op("->")(key)) == "string", value.op("->>")(key)),
            else_=None,
        )
    else:
        extracted = case(
            (
                func.json_type(value, f"$.{key}") == "text",
                func.json_extract(value, f"$.{key}"),
            ),
            else_=None,
        )
    return func.nullif(func.trim(extracted), "")


def json_array_length(column: Any, *, dialect: str) -> Any:
    if dialect == "postgresql":
        return func.jsonb_array_length(column)
    return func.json_array_length(column)


def uuid_text_from_json(value: Any, *, dialect: str) -> Any:
    """JSON 里的 UUID 文本归一成与 ``Uuid`` 列 cast 后一致的形式。"""
    if dialect == "postgresql":
        return func.lower(value)
    return func.lower(func.replace(value, "-", ""))


def uuid_text_from_column(column: Any) -> Any:
    return func.lower(func.cast(column, String))


__all__ = [
    "coverage_scope_conditions",
    "element_is_object",
    "expand_object_array",
    "expand_text_array",
    "json_array_length",
    "json_string_field",
    "uuid_text_from_column",
    "uuid_text_from_json",
]
