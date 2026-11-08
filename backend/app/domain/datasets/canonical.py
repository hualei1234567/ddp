"""确定性 JSON 规范化与 SHA256。

Manifest/报告 Hash 由服务端对规范化 JSON 计算，客户端提交的 Hash 一律忽略。
本实现使用 RFC 8785 的核心要求：对象键按 UTF-16 码点排序、无多余空白、
禁止 NaN/Infinity、UTF-8 编码；项目数据仅包含字符串/整数/布尔/数组/对象。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("值无法规范化为 JSON") from exc


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_value(value: Any) -> str:
    return sha256_text(canonical_json(value))


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
