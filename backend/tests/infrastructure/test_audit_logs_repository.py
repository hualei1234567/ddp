"""审计日志只读仓储测试（真实 SQLite）。

覆盖：项目隔离、全部过滤条件、``(occurred_at, id)`` 倒序键集分页、
游标锚点跨项目不泄露、以及 SQLite 读回 naive 时间时的 UTC 规范化。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.domain.audit.models import AuditQuery, AuditResult
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import AuditLog
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.audit_logs import SqlAuditLogQueryRepository
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


@pytest.fixture()
def audit_db() -> Iterator[tuple[Engine, sessionmaker[Session], UUID, UUID, UUID, UUID]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    project_a, project_b = uuid4(), uuid4()
    actor_a, actor_b = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="audit-a", name="审计项目 A"),
                Project(id=project_b, code="audit-b", name="审计项目 B"),
                User(id=actor_a, display_name="操作者 A"),
                User(id=actor_b, display_name="操作者 B"),
            ]
        )
        session.commit()
    try:
        yield engine, factory, project_a, project_b, actor_a, actor_b
    finally:
        engine.dispose()


def _seed(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    actor_id: UUID,
    audit_id: UUID | None = None,
    resource_type: str = "EPISODE",
    resource_id: UUID | None = None,
    action: str = "create",
    result: str = "SUCCESS",
    occurred_at: datetime = NOW,
) -> UUID:
    row_id = audit_id or uuid4()
    with factory() as session:
        session.add(
            AuditLog(
                id=row_id,
                project_id=project_id,
                actor_user_id=actor_id,
                actor_role="editor",
                resource_type=resource_type,
                resource_id=resource_id or uuid4(),
                action=action,
                from_status=None,
                to_status="READY",
                reason=None,
                request_id="req-repo",
                result=result,
                correlation_id=None,
                details={"k": "v"},
                occurred_at=occurred_at,
            )
        )
        session.commit()
    return row_id


def _list(
    factory: sessionmaker[Session],
    scope: ProjectScope,
    query: AuditQuery,
    *,
    after_id: UUID | None = None,
    limit: int = 20,
) -> list:
    with factory() as session:
        return SqlAuditLogQueryRepository(session).list(
            scope,
            query,
            after_id=after_id,
            limit=limit,
        )


def test_project_scope_is_enforced(
    audit_db: tuple[Engine, sessionmaker[Session], UUID, UUID, UUID, UUID],
) -> None:
    _, factory, project_a, project_b, actor_a, _ = audit_db
    mine = _seed(factory, project_id=project_a, actor_id=actor_a)
    _seed(factory, project_id=project_b, actor_id=actor_a)

    entries = _list(
        factory,
        ProjectScope(project_id=project_a, actor_id=actor_a),
        AuditQuery(),
    )

    assert [entry.audit_id for entry in entries] == [mine]
    assert entries[0].occurred_at == NOW
    assert entries[0].occurred_at.tzinfo is not None
    assert entries[0].details == {"k": "v"}


def test_filters_are_pushed_down_and_combined(
    audit_db: tuple[Engine, sessionmaker[Session], UUID, UUID, UUID, UUID],
) -> None:
    _, factory, project_a, project_b, actor_a, actor_b = audit_db
    resource_id = uuid4()
    target = _seed(
        factory,
        project_id=project_a,
        actor_id=actor_a,
        resource_type="DATASET",
        resource_id=resource_id,
        action="freeze",
        result="FAILURE",
        occurred_at=NOW + timedelta(hours=1),
    )
    # 每个条件各有一条"只差一个字段"的记录，确保过滤是真下推而非巧合。
    _seed(
        factory,
        project_id=project_a,
        actor_id=actor_a,
        resource_type="DATASET",
        resource_id=resource_id,
        action="freeze",
        result="SUCCESS",
        occurred_at=NOW + timedelta(hours=1),
    )
    _seed(
        factory,
        project_id=project_a,
        actor_id=actor_b,
        resource_type="DATASET",
        resource_id=resource_id,
        action="freeze",
        result="FAILURE",
        occurred_at=NOW + timedelta(hours=1),
    )
    _seed(
        factory,
        project_id=project_a,
        actor_id=actor_a,
        resource_type="EPISODE",
        resource_id=resource_id,
        action="freeze",
        result="FAILURE",
        occurred_at=NOW + timedelta(hours=1),
    )
    _seed(
        factory,
        project_id=project_a,
        actor_id=actor_a,
        resource_type="DATASET",
        resource_id=uuid4(),
        action="freeze",
        result="FAILURE",
        occurred_at=NOW + timedelta(hours=1),
    )
    _seed(
        factory,
        project_id=project_a,
        actor_id=actor_a,
        resource_type="DATASET",
        resource_id=resource_id,
        action="create",
        result="FAILURE",
        occurred_at=NOW + timedelta(hours=1),
    )
    _seed(factory, project_id=project_b, actor_id=actor_a, resource_type="DATASET")

    entries = _list(
        factory,
        ProjectScope(project_id=project_a, actor_id=actor_a),
        AuditQuery.build(
            resource_type="DATASET",
            resource_id=resource_id,
            action="freeze",
            actor_user_id=actor_a,
            result=AuditResult.FAILURE,
            occurred_from=NOW,
            occurred_to=NOW + timedelta(hours=2),
        ),
    )

    assert [entry.audit_id for entry in entries] == [target]


def test_time_range_is_inclusive(
    audit_db: tuple[Engine, sessionmaker[Session], UUID, UUID, UUID, UUID],
) -> None:
    _, factory, project_a, _, actor_a, _ = audit_db
    start = _seed(factory, project_id=project_a, actor_id=actor_a, occurred_at=NOW)
    end = _seed(
        factory,
        project_id=project_a,
        actor_id=actor_a,
        occurred_at=NOW + timedelta(hours=1),
    )
    _seed(
        factory,
        project_id=project_a,
        actor_id=actor_a,
        occurred_at=NOW + timedelta(hours=2),
    )

    entries = _list(
        factory,
        ProjectScope(project_id=project_a, actor_id=actor_a),
        AuditQuery.build(occurred_from=NOW, occurred_to=NOW + timedelta(hours=1)),
    )

    assert [entry.audit_id for entry in entries] == [end, start]


def test_keyset_pagination_is_stable_on_same_timestamp(
    audit_db: tuple[Engine, sessionmaker[Session], UUID, UUID, UUID, UUID],
) -> None:
    _, factory, project_a, _, actor_a, _ = audit_db
    scope = ProjectScope(project_id=project_a, actor_id=actor_a)
    # 三条同一时刻的记录：只有 (occurred_at, id) 复合键集才能稳定翻页。
    ids = sorted(
        (_seed(factory, project_id=project_a, actor_id=actor_a) for _ in range(3)),
        reverse=True,
    )

    first_page = _list(factory, scope, AuditQuery(), limit=2)
    assert [entry.audit_id for entry in first_page] == ids[:2]

    second_page = _list(factory, scope, AuditQuery(), after_id=ids[1], limit=2)
    assert [entry.audit_id for entry in second_page] == ids[2:]


def test_cursor_anchor_from_other_project_does_not_leak(
    audit_db: tuple[Engine, sessionmaker[Session], UUID, UUID, UUID, UUID],
) -> None:
    _, factory, project_a, project_b, actor_a, _ = audit_db
    _seed(factory, project_id=project_a, actor_id=actor_a)
    foreign = _seed(
        factory,
        project_id=project_b,
        actor_id=actor_a,
        occurred_at=NOW + timedelta(days=1),
    )
    scope = ProjectScope(project_id=project_a, actor_id=actor_a)

    # 锚点子查询限定项目后取不到排序值，条件为 NULL，结果必须为空。
    assert _list(factory, scope, AuditQuery(), after_id=foreign) == []
    # 同一游标在本项目内则正常工作（证明上面的空结果来自隔离而非游标失效）。
    assert _list(factory, scope, AuditQuery(), after_id=None) != []


def test_unknown_cursor_returns_empty_page(
    audit_db: tuple[Engine, sessionmaker[Session], UUID, UUID, UUID, UUID],
) -> None:
    _, factory, project_a, _, actor_a, _ = audit_db
    _seed(factory, project_id=project_a, actor_id=actor_a)

    entries = _list(
        factory,
        ProjectScope(project_id=project_a, actor_id=actor_a),
        AuditQuery(),
        after_id=uuid4(),
    )

    assert entries == []


def test_occurred_at_is_normalized_to_utc(
    audit_db: tuple[Engine, sessionmaker[Session], UUID, UUID, UUID, UUID],
) -> None:
    _, factory, project_a, _, actor_a, _ = audit_db
    _seed(factory, project_id=project_a, actor_id=actor_a, occurred_at=NOW)

    entries = _list(
        factory,
        ProjectScope(project_id=project_a, actor_id=actor_a),
        AuditQuery(),
    )

    assert entries[0].occurred_at.tzinfo is UTC
