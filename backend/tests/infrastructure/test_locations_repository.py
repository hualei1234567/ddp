"""场地仓储与目录适配器测试（N9 多基地）。

用真实 SQLite + 真实仓储（``Base.metadata.create_all`` 建库，与 API/集成测试
同一路径）。重点覆盖三类真实故障：

1. 跨项目读写必须返回 ``None`` / ``False``（防 IDOR 枚举）；
2. 乐观锁：版本不符 409、目标不存在 404，两者不得混淆；
3. 项目内 ``code`` 重复抛 ``DuplicateResourceError``，而不是裸 ``IntegrityError``。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.application.locations.pagination import decode_cursor, encode_cursor
from app.domain.identity.errors import CrossProjectAccessError
from app.domain.identity.models import ProjectScope
from app.domain.locations import (
    DomainValidationError,
    DuplicateResourceError,
    Location,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.locations import SqlLocationDirectory, SqlLocationRepository
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class Ids:
    project_a: UUID
    project_b: UUID
    user_a: UUID


@pytest.fixture()
def location_db() -> Iterator[tuple[sessionmaker[Session], Ids]]:
    engine: Engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    ids = Ids(project_a=uuid4(), project_b=uuid4(), user_a=uuid4())
    with factory() as session:
        session.add_all(
            [
                Project(id=ids.project_a, code="project-a", name="项目 A"),
                Project(id=ids.project_b, code="project-b", name="项目 B"),
                User(id=ids.user_a, display_name="用户一"),
            ]
        )
        session.commit()
    try:
        yield factory, ids
    finally:
        engine.dispose()


def _scope(ids: Ids, project_id: UUID | None = None) -> ProjectScope:
    return ProjectScope(project_id or ids.project_a, actor_id=ids.user_a)


def _location(ids: Ids, *, code: str = "BJ-1", **overrides: object) -> Location:
    payload: dict[str, object] = {
        "location_id": uuid4(),
        "project_id": ids.project_a,
        "code": code,
        "name": f"基地 {code}",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    payload.update(overrides)
    return Location(**payload)  # type: ignore[arg-type]


def test_add_get_and_list_roundtrip(location_db: tuple[sessionmaker[Session], Ids]) -> None:
    factory, ids = location_db
    with factory() as session:
        repo = SqlLocationRepository(session)
        created = _location(ids)
        repo.add(_scope(ids), created)
        session.commit()

        fetched = repo.get(_scope(ids), created.location_id)
        assert fetched is not None
        assert fetched.code == "BJ-1"
        assert fetched.kind.value == "SITE"
        assert [item.location_id for item in repo.list(_scope(ids))] == [created.location_id]


def test_cross_project_reads_return_none(location_db: tuple[sessionmaker[Session], Ids]) -> None:
    """跨项目按 ID 取单条必须 ``None``，不泄漏"该 ID 存在于别的项目"。"""
    factory, ids = location_db
    with factory() as session:
        repo = SqlLocationRepository(session)
        created = _location(ids)
        repo.add(_scope(ids), created)
        session.commit()

        assert repo.get(_scope(ids, ids.project_b), created.location_id) is None
        assert repo.get_by_code(_scope(ids, ids.project_b), "BJ-1") is None
        assert repo.list(_scope(ids, ids.project_b)) == []
        # 目录适配器同样必须拒绝跨项目存在性。
        assert SqlLocationDirectory(session).location_exists(_scope(ids), created.location_id)
        assert not SqlLocationDirectory(session).location_exists(
            _scope(ids, ids.project_b), created.location_id
        )


def test_duplicate_code_raises_domain_error(
    location_db: tuple[sessionmaker[Session], Ids],
) -> None:
    factory, ids = location_db
    with factory() as session:
        repo = SqlLocationRepository(session)
        repo.add(_scope(ids), _location(ids, code="BJ-1"))
        session.commit()

        with pytest.raises(DuplicateResourceError):
            repo.add(_scope(ids), _location(ids, code="BJ-1"))


def test_same_code_in_other_project_is_allowed(
    location_db: tuple[sessionmaker[Session], Ids],
) -> None:
    factory, ids = location_db
    with factory() as session:
        repo = SqlLocationRepository(session)
        repo.add(_scope(ids), _location(ids, code="BJ-1"))
        repo.add(
            _scope(ids, ids.project_b),
            _location(ids, code="BJ-1", project_id=ids.project_b),
        )
        session.commit()
        assert len(repo.list(_scope(ids))) == 1
        assert len(repo.list(_scope(ids, ids.project_b))) == 1


def test_list_filters_kind_and_applies_keyset_cursor(
    location_db: tuple[sessionmaker[Session], Ids],
) -> None:
    factory, ids = location_db
    with factory() as session:
        repo = SqlLocationRepository(session)
        first = _location(ids, code="A", kind="SITE")
        second = _location(ids, code="B", kind="LAB")
        repo.add(_scope(ids), first)
        repo.add(_scope(ids), second)
        session.commit()

        ordered = sorted([first, second], key=lambda item: item.location_id)
        assert [item.code for item in repo.list(_scope(ids), kind="LAB")] == ["B"]
        # 游标编解码的往返在应用层有单测；这里验证它真的被当成 keyset 条件使用。
        cursor = encode_cursor(ordered[0].location_id)
        after_first = repo.list(_scope(ids), after_id=decode_cursor(cursor))
        assert [item.code for item in after_first] == [ordered[1].code]


def test_list_rejects_out_of_range_limit(
    location_db: tuple[sessionmaker[Session], Ids],
) -> None:
    factory, ids = location_db
    with factory() as session:
        repo = SqlLocationRepository(session)
        with pytest.raises(DomainValidationError):
            repo.list(_scope(ids), limit=0)
        with pytest.raises(DomainValidationError):
            repo.list(_scope(ids), limit=102)


def test_save_bumps_version_and_persists_fields(
    location_db: tuple[sessionmaker[Session], Ids],
) -> None:
    factory, ids = location_db
    with factory() as session:
        repo = SqlLocationRepository(session)
        created = repo.add(_scope(ids), _location(ids))
        session.commit()

        updated = created.apply_patch(
            {"name": "上海基地", "kind": "LAB", "address": "张江"},
            now=datetime.now(UTC),
            actor_id=ids.user_a,
        )
        saved = repo.save(_scope(ids), updated, expected_version=1)
        session.commit()
        assert saved.version == 2

        reloaded = repo.get(_scope(ids), created.location_id)
        assert reloaded is not None
        assert reloaded.name == "上海基地"
        assert reloaded.kind.value == "LAB"
        assert reloaded.address == "张江"
        assert reloaded.version == 2


def test_save_rejects_stale_version_and_missing_row(
    location_db: tuple[sessionmaker[Session], Ids],
) -> None:
    factory, ids = location_db
    with factory() as session:
        repo = SqlLocationRepository(session)
        created = repo.add(_scope(ids), _location(ids))
        session.commit()

        with pytest.raises(VersionConflictError):
            repo.save(_scope(ids), created, expected_version=5)
        session.rollback()

        missing = _location(ids, code="MISSING")
        with pytest.raises(ResourceNotFoundError):
            repo.save(_scope(ids), missing, expected_version=1)


def test_save_across_projects_is_rejected(
    location_db: tuple[sessionmaker[Session], Ids],
) -> None:
    """用 B 项目的 scope 改 A 项目的场地：必须被拒，不能静默成功。

    实际拒绝点是 ``scope.ensure()``（``CrossProjectAccessError`` → 403）——
    比"回查不到行再报 404"更早、也更准确：调用方连跨项目读写的机会都没有。
    """
    factory, ids = location_db
    with factory() as session:
        repo = SqlLocationRepository(session)
        created = repo.add(_scope(ids), _location(ids))
        session.commit()

        with pytest.raises(CrossProjectAccessError):
            repo.save(_scope(ids, ids.project_b), created, expected_version=1)


def test_directory_only_answers_exists(location_db: tuple[sessionmaker[Session], Ids]) -> None:
    factory, ids = location_db
    with factory() as session:
        directory = SqlLocationDirectory(session)
        assert not directory.location_exists(_scope(ids), uuid4())
        created = SqlLocationRepository(session).add(_scope(ids), _location(ids))
        session.commit()
        assert directory.location_exists(_scope(ids), created.location_id)
