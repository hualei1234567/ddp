"""采集会话产物复合游标编解码（N9-008）。

格式与 ``application/catalog/cursor.py`` 一致（base64url(JSON)），但只承载
``(created_at, kind, artifact_id)`` 三个短键。解析失败一律抛领域校验错误
（对外 422），不静默回退到首页——否则客户端拿错游标会重复/跳过数据而不自知。
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from uuid import UUID

from app.domain.collection.artifacts import ArtifactCursor, CaptureSessionArtifactKind
from app.domain.collection.errors import DomainValidationError

MAX_CURSOR_LENGTH = 512


def as_utc(value: datetime) -> datetime:
    """SQLite 读回的时间可能没有 tzinfo；统一按 UTC 解释，禁止依赖本机时区。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def encode_artifact_cursor(cursor: ArtifactCursor) -> str:
    payload = json.dumps(
        {
            "t": as_utc(cursor.created_at).isoformat(),
            "k": cursor.kind.value,
            "i": str(cursor.artifact_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_artifact_cursor(value: str | None) -> ArtifactCursor | None:
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
        artifact_id = payload["i"]
        if (
            not isinstance(timestamp, str)
            or not isinstance(kind, str)
            or not isinstance(artifact_id, str)
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
        parsed_kind = CaptureSessionArtifactKind(kind)
        parsed_id = UUID(artifact_id)
    except ValueError as exc:
        raise DomainValidationError("cursor 非法") from exc
    return ArtifactCursor(
        created_at=as_utc(parsed),
        kind=parsed_kind,
        artifact_id=parsed_id,
    )


__all__ = [
    "MAX_CURSOR_LENGTH",
    "as_utc",
    "decode_artifact_cursor",
    "encode_artifact_cursor",
]
