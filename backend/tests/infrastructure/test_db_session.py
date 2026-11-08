"""基础设施测试：SQLite 上的引擎、会话生命周期与仓储。"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.domain.identity.models import ProjectRole
from app.infrastructure.db import session as session_module
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import (
    create_db_engine,
    create_session_factory,
    get_session,
)
from app.infrastructure.repositories.identity import (
    SqlProjectMembershipRepository,
    SqlProjectRepository,
)
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _memory_engine() -> Engine:
    return create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )


def _factory() -> tuple[Engine, sessionmaker[Session]]:
    engine = _memory_engine()
    Base.metadata.create_all(engine)
    return engine, create_session_factory(engine)


def test_memory_engine_uses_static_pool() -> None:
    engine = _memory_engine()
    assert isinstance(engine.pool, StaticPool)
    engine.dispose()


def test_sqlite_connections_enable_foreign_keys() -> None:
    engine = _memory_engine()
    try:
        with engine.connect() as connection:
            enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
        assert enabled == 1
    finally:
        engine.dispose()


def test_sqlite_rejects_foreign_key_violation() -> None:
    from sqlalchemy.exc import IntegrityError

    engine = _memory_engine()
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            session.add(
                ProjectMember(
                    project_id=uuid4(),
                    user_id=uuid4(),
                    role=ProjectRole.OWNER.value,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
    finally:
        engine.dispose()


def test_repositories_read_within_project_scope() -> None:
    _, factory = _factory()
    project_id = uuid4()
    other_project_id = uuid4()
    user_id = uuid4()

    with factory() as session:
        session.add_all(
            [
                Project(id=project_id, code="p1", name="项目一"),
                Project(id=other_project_id, code="p2", name="项目二"),
                User(id=user_id, display_name="张三"),
            ]
        )
        session.flush()
        session.add(
            ProjectMember(
                project_id=project_id,
                user_id=user_id,
                role=ProjectRole.OWNER.value,
            )
        )
        session.commit()

    with factory() as session:
        projects = SqlProjectRepository(session)
        memberships = SqlProjectMembershipRepository(session)

        assert projects.exists(project_id)
        assert not projects.exists(uuid4())
        assert memberships.role_for(project_id, user_id) is ProjectRole.OWNER
        # 同一用户在其他项目没有成员资格。
        assert memberships.role_for(other_project_id, user_id) is None
        assert memberships.role_for(project_id, uuid4()) is None


def test_unknown_role_from_database_fails_loudly() -> None:
    class _StubSession:
        def scalar(self, *args: object, **kwargs: object) -> str:
            return "root"

    repository = SqlProjectMembershipRepository(_StubSession())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="项目角色非法"):
        repository.role_for(uuid4(), uuid4())


def test_get_session_rolls_back_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _, factory = _factory()
    monkeypatch.setattr(session_module, "get_session_factory", lambda: factory)

    generator = get_session()
    session = next(generator)
    session.add(Project(code="p-rollback", name="回滚测试"))
    session.flush()

    with pytest.raises(RuntimeError):
        generator.throw(RuntimeError("模拟用例失败"))

    with factory() as verify:
        count = verify.scalar(select(func.count()).select_from(Project))
        assert count == 0


def test_get_session_does_not_auto_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """会话依赖成功返回时也不提交；写用例必须显式 commit。"""
    _, factory = _factory()
    monkeypatch.setattr(session_module, "get_session_factory", lambda: factory)

    generator = get_session()
    session = next(generator)
    session.add(Project(code="p-uncommitted", name="未提交"))
    session.flush()
    generator.close()

    with factory() as verify:
        count = verify.scalar(select(func.count()).select_from(Project))
        assert count == 0


def test_project_code_is_unique() -> None:
    from sqlalchemy.exc import IntegrityError

    _, factory = _factory()
    with factory() as session:
        session.add(Project(code="dup", name="一"))
        session.commit()
        session.add(Project(code="dup", name="二"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_project_scope_roundtrip_uses_uuid() -> None:
    project_id: UUID = uuid4()
    _, factory = _factory()
    with factory() as session:
        session.add(Project(id=project_id, code="scope", name="作用域"))
        session.commit()
        assert session.get(Project, project_id) is not None
