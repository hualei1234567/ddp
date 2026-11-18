"""资产目录复合游标编解码。

既有各域的 ``encode_cursor/decode_cursor`` 只承载单个 UUID，无法表达
"跨表按 ``(updated_at, kind, id)`` 键集分页"。这里新增一个**只读检索专用**
的复合游标，格式为 base64url(JSON)，不引入通用分页框架，也不改动既有
UUID 游标语义。

JSON 只含三个短键：``t``（UTC ISO 时间）、``k``（资源类型）、``i``（UUID）。
解析失败一律抛领域校验错误（对外 422 ``validation_failed``），不静默回退到
首页——否则客户端拿错游标会重复/跳过数据而不自知。
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from uuid import UUID

from app.application.catalog.models import CatalogCursor, CatalogEntry
from app.domain.catalog.enums import AssetKind
from app.domain.catalog.errors import DomainValidationError

MAX_CURSOR_LENGTH = 512


def as_utc(value: datetime) -> datetime:
    """SQLite 读回的时间可能没有 tzinfo；统一按 UTC 解释，禁止依赖本机时区。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def cursor_for(entry: CatalogEntry) -> CatalogCursor:
    return CatalogCursor(
        updated_at=as_utc(entry.updated_at),
        kind=entry.kind,
        asset_id=entry.asset_id,
    )


def encode_cursor(cursor: CatalogCursor) -> str:
    payload = json.dumps(
        {
            "t": as_utc(cursor.updated_at).isoformat(),
            "k": cursor.kind.value,
            "i": str(cursor.asset_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(value: str | None) -> CatalogCursor | None:
    if value is None or value == "":
        return None
    if len(value) > MAX_CURSOR_LENGTH:
        raise DomainValidationError("cursor 非法")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("游标结构非法")
        timestamp = payload["t"]
        kind = payload["k"]
        asset_id = payload["i"]
        if (
            not isinstance(timestamp, str)
            or not isinstance(kind, str)
            or not isinstance(asset_id, str)
        ):
            raise ValueError("游标字段类型非法")
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (
        binascii.Error,
        ValueError,
        KeyError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise DomainValidationError("cursor 非法") from exc
    try:
        parsed_kind = AssetKind(kind)
        parsed_id = UUID(asset_id)
    except ValueError as exc:
        raise DomainValidationError("cursor 非法") from exc
    return CatalogCursor(updated_at=as_utc(parsed), kind=parsed_kind, asset_id=parsed_id)


__all__ = [
    "MAX_CURSOR_LENGTH",
    "as_utc",
    "cursor_for",
    "decode_cursor",
    "encode_cursor",
]
