"""采集会话产物复合游标编解码单元测试（N9-008）。

游标是只读检索的唯一状态载体：编码/解码必须严格互逆，非法输入一律抛
``DomainValidationError``（对外 422），不得静默回退到首页。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from app.application.collection.artifact_cursor import (
    MAX_CURSOR_LENGTH,
    decode_artifact_cursor,
    encode_artifact_cursor,
)
from app.domain.collection.artifacts import ArtifactCursor, CaptureSessionArtifactKind
from app.domain.collection.errors import DomainValidationError


def _cursor(**overrides: object) -> ArtifactCursor:
    values: dict[str, object] = {
        "created_at": datetime(2026, 11, 19, 8, 0, tzinfo=UTC),
        "kind": CaptureSessionArtifactKind.EPISODE,
        "artifact_id": uuid4(),
    }
    values.update(overrides)
    return ArtifactCursor(**values)  # type: ignore[arg-type]


def test_roundtrip_preserves_all_fields() -> None:
    original = _cursor()
    decoded = decode_artifact_cursor(encode_artifact_cursor(original))
    assert decoded == original


def test_none_and_empty_decode_to_none() -> None:
    assert decode_artifact_cursor(None) is None
    assert decode_artifact_cursor("") is None


def test_naive_datetime_is_interpreted_as_utc() -> None:
    naive = datetime(2026, 11, 19, 8, 0)
    decoded = decode_artifact_cursor(encode_artifact_cursor(_cursor(created_at=naive)))
    assert decoded is not None
    assert decoded.created_at.tzinfo is not None
    assert decoded.created_at.utcoffset() == timedelta(0)


def test_non_utc_timezone_is_normalized_to_utc() -> None:
    shanghai = timezone(timedelta(hours=8))
    decoded = decode_artifact_cursor(
        encode_artifact_cursor(_cursor(created_at=datetime(2026, 11, 19, 16, 0, tzinfo=shanghai)))
    )
    assert decoded is not None
    assert decoded.created_at == datetime(2026, 11, 19, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [
        "not-base64!!",
        "e30",  # base64("{}")，缺字段
        "W10",  # base64("[]")，结构非法
        "a" * (MAX_CURSOR_LENGTH + 1),
    ],
)
def test_invalid_cursor_raises_domain_validation_error(value: str) -> None:
    with pytest.raises(DomainValidationError):
        decode_artifact_cursor(value)


def test_unknown_kind_is_rejected() -> None:
    import base64
    import json

    payload = json.dumps(
        {"t": "2026-11-19T08:00:00+00:00", "k": "UNKNOWN", "i": str(uuid4())},
        separators=(",", ":"),
    )
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    with pytest.raises(DomainValidationError):
        decode_artifact_cursor(encoded)
