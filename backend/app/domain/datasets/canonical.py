"""数据集域 Hash 入口；实现只有一份，见 ``app.domain.common.canonical_json``。

保留本模块是因为 17 处调用点用 ``app.domain.datasets.canonical`` 这个路径，
且领域错误类型（``ValueError`` 子类）在此确认。规范化算法本身不在此重复实现
（G-15：此前这里是一份"类似 JCS"的 json.dumps，与 RFC 8785 实际不同）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.common.canonical_json import (
    CanonicalJSONError,
    canonical_json,
    canonical_json_bytes,
    sha256_text,
    sha256_value,
)


def validate_sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是 sha256 字符串")
    if len(value) != 71 or not value.startswith("sha256:"):
        raise ValueError(f"{field_name} 必须形如 sha256:<64 位小写十六进制>")
    digest = value[7:]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError(f"{field_name} 必须形如 sha256:<64 位小写十六进制>")
    return value


def plain(value: Any) -> Any:
    """把 dataclass/枚举/元组递归转换为可 JSON 化的普通值。"""
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [plain(item) for item in value]
    if hasattr(value, "value") and type(value).__module__ != "builtins":
        return value.value
    return value


__all__ = [
    "CanonicalJSONError",
    "canonical_json",
    "canonical_json_bytes",
    "plain",
    "sha256_text",
    "sha256_value",
    "validate_sha256",
]
