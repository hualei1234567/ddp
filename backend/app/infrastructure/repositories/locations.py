"""场地 SQLAlchemy 仓储与目录适配器（N9 多基地）。

所有查询强制 ``project_id`` 过滤；按 ID 取单条也带项目条件——跨项目资源返回
``None``，由用例统一映射为 404，不泄漏"该 ID 在别的项目存在"。

``SqlLocationRepository.save`` 用带 ``version = expected_version`` 条件的
``UPDATE`` 实现乐观锁：命中 0 行时再回查该行是否存在，以区分"不存在/跨项目"
（404）与"版本已变化"（409）。与采集域/设备域的既有做法一致。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.domain.locations.enums import LocationKind
from app.domain.locations.errors import (
    DomainValidationError,
    DuplicateResourceError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.locations.models import Location
from app.infrastructure.db.models.locations import LocationModel
from app.infrastructure.repositories._optimistic_lock import (
    ensure_expected_version as _ensure_expected_version,
)

# 列表用例取 limit+1 判定下一页；上限与采集/设备域一致。
_FETCH_LIMIT_MAX = 101


def _check_limit(limit: int) -> None:
    if limit < 1 or limit > _FETCH_LIMIT_MAX:
        raise DomainValidationError(f"limit 必须在 1 到 {_FETCH_LIMIT_MAX} 之间")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


class SqlLocationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, location: Location) -> Location:
        scope.ensure(location.project_id)
        now = datetime.now(UTC)
        self._session.add(
            LocationModel(
                id=location.location_id,
                project_id=location.project_id,
                code=location.code,
                name=location.name,
                kind=location.kind.value,
                address=location.address,
                notes=location.notes,
                version=location.version,
                created_at=location.created_at or now,
                updated_at=location.updated_at or now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            message = str(exc.orig).lower()
            if any(token in message for token in ("unique", "duplicate", "uq_")):
                raise DuplicateResourceError("项目内场地 code 重复") from exc
            raise
        return location

    def get(self, scope: ProjectScope, location_id: UUID) -> Location | None:
        row = self._session.scalar(
            select(LocationModel).where(
                LocationModel.id == location_id,
                LocationModel.project_id == scope.project_id,
            )
        )
        return None if row is None else _location(row)

    def get_by_code(self, scope: ProjectScope, code: str) -> Location | None:
        row = self._session.scalar(
            select(LocationModel).where(
                LocationModel.project_id == scope.project_id,
                LocationModel.code == code.strip(),
            )
        )
        return None if row is None else _location(row)

    def list(
        self,
        scope: ProjectScope,
        *,
        kind: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Location]:
        _check_limit(limit)
        statement = select(LocationModel).where(LocationModel.project_id == scope.project_id)
        if kind is not None:
            statement = statement.where(LocationModel.kind == kind)
        if after_id is not None:
            statement = statement.where(LocationModel.id > after_id)
        statement = statement.order_by(LocationModel.id).limit(limit)
        return [_location(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        location: Location,
        *,
        expected_version: int,
    ) -> Location:
        scope.ensure(location.project_id)
        _ensure_expected_version(
            location.version,
            expected_version,
            invalid=DomainValidationError,
            conflict=lambda: VersionConflictError("采集场地"),
        )
        now = location.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(LocationModel)
            .where(
                LocationModel.id == location.location_id,
                LocationModel.project_id == scope.project_id,
                LocationModel.version == expected_version,
            )
            .values(
                name=location.name,
                kind=location.kind.value,
                address=location.address,
                notes=location.notes,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            _raise_update_failure(self._session, scope, location.location_id)
        return replace(location, version=expected_version + 1)


class SqlLocationDirectory:
    """场地存在性校验；供设备域与采集域在写入前给出可读 404。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def location_exists(self, scope: ProjectScope, location_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(LocationModel.id).where(
                    LocationModel.id == location_id,
                    LocationModel.project_id == scope.project_id,
                )
            )
            is not None
        )


def _raise_update_failure(session: Session, scope: ProjectScope, location_id: UUID) -> None:
    """UPDATE 未命中：先判"不存在/跨项目"，否则是并发版本冲突。"""
    exists = session.scalar(
        select(LocationModel.id).where(
            LocationModel.id == location_id,
            LocationModel.project_id == scope.project_id,
        )
    )
    if exists is None:
        raise ResourceNotFoundError("采集场地", location_id)
    raise VersionConflictError("采集场地")


def _location(row: LocationModel) -> Location:
    return Location(
        location_id=row.id,
        project_id=row.project_id,
        code=row.code,
        name=row.name,
        kind=LocationKind(row.kind),
        address=row.address,
        notes=row.notes,
        version=row.version,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


__all__ = ["SqlLocationDirectory", "SqlLocationRepository"]
