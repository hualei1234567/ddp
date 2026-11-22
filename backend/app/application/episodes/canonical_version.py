"""Episode Canonical 版本白名单（ADR-0038 §2.6 / V31-017）。

历史行上的 ``ddp.canonical/1.0.0-draft`` 是溯源事实，**读取时不拒绝**；
但新写入必须落在 ``SUPPORTED_SCHEMA_VERSIONS`` 内——否则共享 JSON Schema
一接上就会立刻拒绝这条数据，而那时它已经落库、被下游引用。

白名单的唯一来源是 ``ddp_data.canonical.v1.SUPPORTED_SCHEMA_VERSIONS``，
不在 backend 里写第二份字符串。
"""

from __future__ import annotations

from ddp_data.canonical.v1 import SUPPORTED_SCHEMA_VERSIONS

from app.core.errors import ValidationFailedError


def ensure_writable_canonical_schema_version(value: str) -> str:
    """校验写入入口的 Canonical 版本；不支持时抛 ``ValidationFailedError``。"""
    if value not in SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_SCHEMA_VERSIONS))
        raise ValidationFailedError(
            f"不支持的 Canonical Schema 版本：{value!r}；写入只接受 {supported}"
        )
    return value


__all__ = ["ensure_writable_canonical_schema_version"]
