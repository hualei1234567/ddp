"""Manifest 字段校验与“禁止内容”检查。

禁止：任何 token/凭证类键、agent 自报 verified 类键、疑似凭证的值、浮点数
（canonical JSON 不允许浮点，避免跨语言数值表示差异）。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from edge_agent.errors import ManifestError

ALLOWED_FORMATS = frozenset(
    {"MCAP", "ROSBAG2", "HDF5", "MP4", "PARQUET", "CSV", "JSON", "ZIP", "UNKNOWN"}
)
ALLOWED_CLOCK_UNITS = frozenset({"ns", "us", "ms", "s"})

_FORBIDDEN_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
    "bearer",
    "cookie",
    "signature",
    "presigned",
)
_FORBIDDEN_KEY_PREFIXES = ("verified", "verification")
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."),  # JWT
    re.compile(r"(?i)\bbearer\s+\S{8,}"),
    re.compile(r"(?i)x-amz-signature="),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_MIME = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def assert_no_forbidden_content(node: Any, where: str = "$") -> None:
    """递归检查键名/值/浮点；发现即抛 ManifestError。"""
    if isinstance(node, Mapping):
        for key, value in node.items():
            if not isinstance(key, str):
                raise ManifestError(f"{where} 含非字符串键：{key!r}")
            lowered = key.lower()
            if any(part in lowered for part in _FORBIDDEN_KEY_PARTS) or lowered.startswith(
                _FORBIDDEN_KEY_PREFIXES
            ):
                raise ManifestError(f"Manifest 禁止字段（凭证或自报校验）：{where}.{key}")
            assert_no_forbidden_content(value, f"{where}.{key}")
    elif isinstance(node, str):
        for pattern in _FORBIDDEN_VALUE_PATTERNS:
            if pattern.search(node):
                raise ManifestError(f"Manifest 值疑似凭证：{where}")
    elif isinstance(node, float):
        raise ManifestError(f"Manifest 不允许浮点数（请用整数毫秒/微秒）：{where}")
    elif isinstance(node, Sequence) and not isinstance(node, bytes | bytearray):
        for index, value in enumerate(node):
            assert_no_forbidden_content(value, f"{where}[{index}]")


def normalize_uuid(value: str, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise ManifestError(f"{name} 不是合法 UUID：{value!r}") from exc


def normalize_optional_uuid(value: str | None, name: str) -> str | None:
    return None if value is None else normalize_uuid(value, name)


def normalize_time(value: datetime | None, name: str) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ManifestError(f"{name} 必须带时区")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def normalize_sha256(value: str, name: str) -> str:
    text = value.removeprefix("sha256:").lower()
    if not _SHA256_HEX.fullmatch(text):
        raise ManifestError(f"{name} 不是合法 SHA256：{value!r}")
    return f"sha256:{text}"


def validate_mime(value: str, name: str) -> str:
    if not _MIME.fullmatch(value.lower()):
        raise ManifestError(f"{name} 不是合法 MIME：{value!r}")
    return value.lower()


def validate_relative_path(value: str, name: str) -> str:
    if not value or value.startswith("/") or "\\" in value or "\x00" in value:
        raise ManifestError(f"{name} 必须是 POSIX 相对路径：{value!r}")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ManifestError(f"{name} 含非法路径段：{value!r}")
    return value
