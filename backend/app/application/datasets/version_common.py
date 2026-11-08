"""DatasetVersion 公共辅助。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.domain.datasets.errors import ResourceNotFoundError


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid(value: str | None) -> UUID:
    if not value:
        raise ResourceNotFoundError("数据集版本", value)
    try:
        return UUID(value)
    except ValueError as exc:
        raise ResourceNotFoundError("数据集版本", value) from exc


def _enum_text(value: object) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)
