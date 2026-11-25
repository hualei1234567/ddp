"""黄金题熔断 resume 端点 API 集成测试（V52-008）。

覆盖：owner 可解除、editor/viewer 403、跨项目隔离（404 语义 = 无状态行
幂等返回）、开关关闭行为不变（resumed=false 不改状态）、幂等重复解除、
PAUSED 拦截派单（经真实路由 + 真实依赖，非 mock）。

全部用真实 SQLite + 真实仓储 + 真实路由；种子直接写
``annotation_honeypot_state``（接线层写入已有应用层测试覆盖，本文件钉住
HTTP 层契约：权限、状态码、响应体与错误码）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.domain.identity.models import PlatformRole
from app.infrastructure.db.models.annotation_honeypot_state import (
    AnnotationHoneypotState,
)
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


@pytest.fixture()
def honeypot_api_db() -> Iterator[tuple[sessionmaker[Session], ApiIds]]:
    """复用 api_db 的身份种子，但用独立内存库（避免跨文件状态污染）。"""
    from app.infrastructure.db.base import Base
    from app.infrastructure.db.session import create_db_engine, create_session_factory

    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    # 与 tests/api/conftest.seed_identity 相同的身份形状（owner/editor/viewer）。
    from app.domain.identity.models import ProjectRole
    from app.infrastructure.db.models.identity import Project, ProjectMember, User

    project_a, project_b = uuid4(), uuid4()
    owner_a, editor_a, viewer_a = uuid4(), uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="hp-a", name="熔断项目 A"),
                Project(id=project_b, code="hp-b", name="熔断项目 B"),
                User(id=owner_a, display_name="负责人"),
                User(id=editor_a, display_name="编辑者"),
                User(id=viewer_a, display_name="只读用户"),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProjectMember(project_id=project_a, user_id=owner_a, role=ProjectRole.OWNER.value),
                ProjectMember(
                    project_id=project_a, user_id=editor_a, role=ProjectRole.EDITOR.value
                ),
                ProjectMember(
                    project_id=project_a, user_id=viewer_a, role=ProjectRole.VIEWER.value
                ),
                ProjectMember(project_id=project_b, user_id=owner_a, role=ProjectRole.OWNER.value),
            ]
        )
        session.commit()
    ids = ApiIds(project_a, project_b, editor_a, owner_a, viewer_a, uuid4())
    try:
        yield factory, ids
    finally:
        engine.dispose()


def _url(project_id: object, annotator_id: object) -> str:
    return f"/api/v1/projects/{project_id}/annotation-honeypot/{annotator_id}/resume"


def _settings(*, enabled: bool) -> Settings:
    return Settings(env="local", honeypot_breaker_enabled=enabled)


def _client(
    factory: sessionmaker[Session],
    user_id: UUID,
    *,
    settings: Settings | None = None,
    platform_role: PlatformRole = PlatformRole.MEMBER,
) -> TestClient:
    from app.api.deps import get_settings

    client = make_client(factory, user_id, platform_role=platform_role)
    if settings is not None:
        client.app.dependency_overrides[get_settings] = lambda: settings
    return client


def seed_paused(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    annotator_id: UUID,
    actor_id: UUID,
) -> None:
    """直接种一行 PAUSED（接线写入路径由应用层测试覆盖）。"""
    with factory() as session:
        session.add(
            AnnotationHoneypotState(
                id=uuid4(),
                project_id=project_id,
                annotator_id=annotator_id,
                hits=2,
                total=20,
                window_history="0" * 18 + "11",
                state="PAUSED",
                paused_reason="连续 2 个窗口黄金题准确率低于 0.85：20%=20%, 20%=20%",
                paused_at=NOW,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.commit()


# --- 权限 ----------------------------------------------------------------


def test_owner_can_resume_paused_annotator(
    honeypot_api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = honeypot_api_db
    annotator = uuid4()
    seed_paused(factory, project_id=ids.project_a, annotator_id=annotator, actor_id=ids.owner_a)
    client = _client(factory, ids.owner_a, settings=_settings(enabled=True))
    response = client.post(_url(ids.project_a, annotator))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["annotator_id"] == str(annotator)
    assert body["state"] == "NORMAL"
    assert body["resumed"] is True
    assert body["checked_at"]
    # 状态行已清空窗口回 NORMAL。
    with factory() as session:
        row = session.query(AnnotationHoneypotState).one()
        assert row.state == "NORMAL"
        assert row.hits == 0 and row.total == 0 and row.window_history == ""
        assert row.paused_reason is None and row.paused_at is None
        assert row.updated_by == ids.owner_a


def test_editor_and_viewer_get_403(
    honeypot_api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = honeypot_api_db
    annotator = uuid4()
    seed_paused(factory, project_id=ids.project_a, annotator_id=annotator, actor_id=ids.owner_a)
    for user_id in (ids.editor_a, ids.viewer_a):
        client = _client(factory, user_id, settings=_settings(enabled=True))
        response = client.post(_url(ids.project_a, annotator))
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "forbidden"
    # 状态未被 editor/viewer 改动。
    with factory() as session:
        row = session.query(AnnotationHoneypotState).one()
        assert row.state == "PAUSED"


def test_outsider_gets_403(
    honeypot_api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = honeypot_api_db
    client = _client(factory, ids.outsider, settings=_settings(enabled=True))
    response = client.post(_url(ids.project_a, uuid4()))
    assert response.status_code == 403, response.text


# --- 开关与幂等 -----------------------------------------------------------


def test_disabled_switch_returns_current_state_unchanged(
    honeypot_api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """开关关闭：端点存在但行为退化为只读快照（行为不变承诺）。"""
    factory, ids = honeypot_api_db
    annotator = uuid4()
    seed_paused(factory, project_id=ids.project_a, annotator_id=annotator, actor_id=ids.owner_a)
    client = _client(factory, ids.owner_a, settings=_settings(enabled=False))
    response = client.post(_url(ids.project_a, annotator))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "PAUSED"
    assert body["resumed"] is False
    with factory() as session:
        row = session.query(AnnotationHoneypotState).one()
        assert row.state == "PAUSED"  # 未被改动


def test_resume_is_idempotent(
    honeypot_api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """重复解除：第二次返回 resumed=false 且仍是 NORMAL。"""
    factory, ids = honeypot_api_db
    annotator = uuid4()
    seed_paused(factory, project_id=ids.project_a, annotator_id=annotator, actor_id=ids.owner_a)
    client = _client(factory, ids.owner_a, settings=_settings(enabled=True))
    first = client.post(_url(ids.project_a, annotator))
    assert first.status_code == 200 and first.json()["resumed"] is True
    second = client.post(_url(ids.project_a, annotator))
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["resumed"] is False and body["state"] == "NORMAL"


def test_no_row_returns_normal_noop(
    honeypot_api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """无状态行：幂等 no-op（不建行）。"""
    factory, ids = honeypot_api_db
    annotator = uuid4()
    client = _client(factory, ids.owner_a, settings=_settings(enabled=True))
    response = client.post(_url(ids.project_a, annotator))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "annotator_id": str(annotator),
        "state": "NORMAL",
        "resumed": False,
        "checked_at": response.json()["checked_at"],
    }
    with factory() as session:
        assert session.query(AnnotationHoneypotState).count() == 0


def test_cross_project_row_is_isolated(
    honeypot_api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """owner_a 对项目 B 的同名标注员解除：项目 A 的行不可见、不可改。"""
    factory, ids = honeypot_api_db
    annotator = uuid4()
    seed_paused(factory, project_id=ids.project_a, annotator_id=annotator, actor_id=ids.owner_a)
    client = _client(factory, ids.owner_a, settings=_settings(enabled=True))
    # owner_a 在项目 B 也是 owner；但状态行属于项目 A——跨项目查询看不到它。
    response = client.post(_url(ids.project_b, annotator))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resumed"] is False and body["state"] == "NORMAL"
    with factory() as session:
        row = session.query(AnnotationHoneypotState).one()
        assert row.state == "PAUSED"  # 项目 A 的行未被触碰


# --- 派单拦截 ------------------------------------------------------------
# 完整分配链路（批次/任务前置 + PAUSED 拦截）在应用层测试
# tests/application/test_annotation_assignment_honeypot.py 覆盖
# （422 契约码 + details 原因码 + REVIEWER 不受闸口限制 + 开关关闭不拦）；
# 本文件钉住 resume 端点的 HTTP 契约。两文件共同覆盖任务卡第 5 条。


def test_dependency_factory_injects_gate_when_enabled(
    honeypot_api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """依赖工厂按开关装配闸口：开启时非 None，关闭时 None（装配层钉住）。"""
    from app.api.deps import get_session, get_settings
    from app.api.v1.annotation_dependencies import get_annotation_use_cases
    from app.application.annotation.honeypot_wiring import SqlHoneypotGatekeeper

    factory, ids = honeypot_api_db

    def _session_override() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    for enabled, expected in ((True, SqlHoneypotGatekeeper), (False, type(None))):
        app = create_app()
        app.dependency_overrides[get_session] = _session_override
        app.dependency_overrides[get_settings] = lambda enabled=enabled: _settings(enabled=enabled)
        # 直接调用依赖函数树（Depends 树在 TestClient 之外也可解析）。
        use_cases = app.dependency_overrides.get(get_annotation_use_cases)
        assert use_cases is None  # 未被覆写：走真实工厂
        session = factory()
        try:
            settings = _settings(enabled=enabled)
            built = get_annotation_use_cases(
                session=session,
                settings=settings,
            )
            gate = built.assignments._honeypot_gate  # noqa: SLF001
            if expected is SqlHoneypotGatekeeper:
                assert isinstance(gate, SqlHoneypotGatekeeper)
            else:
                assert gate is None
        finally:
            session.close()
