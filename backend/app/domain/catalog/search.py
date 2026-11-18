"""检索词校验与前缀上界。

为什么用"前缀区间 + LIKE 复核"而不是 ``%关键词%``
--------------------------------------------------
跨资源目录不能建宽表、也不引入搜索引擎；唯一可用的加速手段是既有 B-tree
索引上的前缀范围。裸 ``LIKE '%q%'`` 无法命中索引，会退化为项目内全表扫描；
本模块把用户输入规范化为 ``[prefix, upper_bound)`` 的半开区间，仓储再用
``startswith`` 做精确复核（转义通配符），既走索引又不放宽语义。

``upper_bound`` 的计算方式：把前缀最后一个可递增的码位加一，截断其后字符。
例如 ``ep-1`` -> ``ep-2``，``ep-￿`` -> 无上界。前缀长度上限 64，且禁止
NUL，避免把控制字符送进数据库。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.catalog.errors import DomainValidationError

MAX_QUERY_LENGTH = 64
_MAX_CODEPOINT = 0x10FFFF


def _prefix_upper_bound(value: str) -> str | None:
    chars = list(value)
    for index in range(len(chars) - 1, -1, -1):
        codepoint = ord(chars[index])
        if codepoint < _MAX_CODEPOINT:
            return "".join(chars[:index]) + chr(codepoint + 1)
    return None


@dataclass(frozen=True, slots=True)
class SearchPrefix:
    """规范化后的检索前缀；``upper_bound`` 为 ``None`` 表示无可用上界。"""

    value: str
    upper_bound: str | None

    @classmethod
    def parse(cls, raw: str) -> SearchPrefix:
        if not isinstance(raw, str):
            raise DomainValidationError("q 必须是字符串")
        value = raw.strip()
        if not value:
            raise DomainValidationError("q 不能为空")
        if len(value) > MAX_QUERY_LENGTH:
            raise DomainValidationError(f"q 不能超过 {MAX_QUERY_LENGTH} 个字符")
        if "\x00" in value:
            raise DomainValidationError("q 不能包含 NUL 字符")
        return cls(value=value, upper_bound=_prefix_upper_bound(value))


__all__ = ["MAX_QUERY_LENGTH", "SearchPrefix"]
