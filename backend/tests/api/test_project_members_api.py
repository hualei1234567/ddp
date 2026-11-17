"""项目成员 API 集成测试（人员与团队）。

覆盖：成员列表可见性与分页、添加/去重/用户不存在、改角色、移除成员、
"最后一名 owner"保护（含移除自己）、跨项目越权、平台管理员代管，
以及审计与 outbox 留痕。
"""

from __future__ import annotations

from uuid import uuid4

from app.domain.identity.models import PlatformRole
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.project_support import member_payload, seed_user

PROJECTS = "/api/v1/projects"


def _members_url(project_id: object, suffix: str = "") -> str:
    return f"{PROJECTS}/{project_id}/members{suffix}"


def _members(client: TestClient, project_id: object, **params: object) -> dict[str, object]:
    response = client.get(_members_url(project_id), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _count(factory: sessionmaker[Session], **filters: object) -> int:
    statement = select(func.count()).select_from(AuditLog)
    for column, value in filters.items():
        statement = statement.where(getattr(AuditLog, column) == value)
    with factory() as session:
        return int(session.scalar(statement) or 0)


# --- 列表 -------------------------------------------------------------------
def test_list_members_with_display_names(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.owner_a)

    payload = _members(client, ids.project_a)
    users = {item["user_id"]: item for item in payload["items"]}
    assert set(users) == {str(ids.owner_a), str(ids.editor_a), str(ids.viewer_a)}
    assert users[str(ids.owner_a)]["role"] == "owner"
    assert users[str(ids.owner_a)]["display_name"] == "负责人"
    assert users[str(ids.editor_a)]["display_name"] == "编辑者"
    assert all(item["project_id"] == str(ids.project_a) for item in payload["items"])

    # 只读成员同样可以查看成员列表（READ）。
    assert make_client(factory, ids.viewer_a).get(_members_url(ids.project_a)).status_code == 200


def test_member_list_is_project_isolated(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    # editor_a 只是 project_a 成员；访问 project_b 必须 403。
    denied = make_client(factory, ids.editor_a).get(_members_url(ids.project_b))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"

    project_b = _members(make_client(factory, ids.owner_a), ids.project_b)
    assert [item["user_id"] for item in project_b["items"]] == [str(ids.owner_a)]


def test_member_pagination(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.owner_a)

    first = _members(client, ids.project_a, limit=1)
    assert len(first["items"]) == 1
    cursor = first["next_cursor"]
    assert isinstance(cursor, str)

    second = _members(client, ids.project_a, limit=1, cursor=cursor)
    assert len(second["items"]) == 1
    third = _members(client, ids.project_a, limit=1, cursor=second["next_cursor"])
    assert len(third["items"]) == 1
    assert third["next_cursor"] is None

    seen = {
        first["items"][0]["user_id"],
        second["items"][0]["user_id"],
        third["items"][0]["user_id"],
    }
    assert seen == {str(ids.owner_a), str(ids.editor_a), str(ids.viewer_a)}

    invalid = client.get(_members_url(ids.project_a), params={"cursor": "bad!!!cursor"})
    assert invalid.status_code == 422


# --- 添加 -------------------------------------------------------------------
def test_owner_adds_member_with_audit(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.owner_a)

    created = client.post(
        _members_url(ids.project_a),
        json=member_payload(ids.outsider, "viewer"),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["user_id"] == str(ids.outsider)
    assert body["display_name"] == "项目外用户"
    assert body["role"] == "viewer"

    # 新成员现在能看到项目。
    assert make_client(factory, ids.outsider).get(f"{PROJECTS}/{ids.project_a}").status_code == 200

    assert (
        _count(
            factory,
            project_id=ids.project_a,
            resource_type="PROJECT_MEMBER",
            resource_id=ids.outsider,
            action="add",
        )
        == 1
    )
    with factory() as session:
        event = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "project.member_added",
                OutboxEvent.project_id == ids.project_a,
            )
        ).one()
    assert event.status == "RECORDED"
    assert event.dispatch is False


def test_add_member_conflicts_and_validation(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.owner_a)

    duplicate = client.post(
        _members_url(ids.project_a),
        json=member_payload(ids.editor_a, "viewer"),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"

    unknown = client.post(_members_url(ids.project_a), json=member_payload(uuid4(), "viewer"))
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"

    invalid_role = client.post(
        _members_url(ids.project_a),
        json=member_payload(ids.outsider, "admin"),
    )
    assert invalid_role.status_code == 422

    extra_field = client.post(
        _members_url(ids.project_a),
        json={**member_payload(ids.outsider), "project_id": str(ids.project_b)},
    )
    assert extra_field.status_code == 422


def test_non_owner_cannot_manage_members(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    for user_id in (ids.editor_a, ids.viewer_a, ids.outsider):
        response = make_client(factory, user_id).post(
            _members_url(ids.project_a),
            json=member_payload(ids.outsider),
        )
        assert response.status_code == 403


def test_platform_admin_manages_members_without_membership(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    admin_id = seed_user(factory)
    admin = make_client(factory, admin_id, platform_role=PlatformRole.ADMIN)

    created = admin.post(
        _members_url(ids.project_b),
        json=member_payload(ids.outsider, "editor"),
    )
    assert created.status_code == 201
    assert created.json()["role"] == "editor"


# --- 改角色 -----------------------------------------------------------------
def test_change_member_role(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.owner_a)

    changed = client.patch(
        _members_url(ids.project_a, f"/{ids.editor_a}"),
        json={"role": "viewer"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["role"] == "viewer"
    assert changed.json()["display_name"] == "编辑者"

    # 同值不写库、不新增审计（PATCH 幂等）。
    audits_before = _count(
        factory,
        project_id=ids.project_a,
        resource_type="PROJECT_MEMBER",
        resource_id=ids.editor_a,
        action="update",
    )
    repeated = client.patch(
        _members_url(ids.project_a, f"/{ids.editor_a}"),
        json={"role": "viewer"},
    )
    assert repeated.status_code == 200
    assert (
        _count(
            factory,
            project_id=ids.project_a,
            resource_type="PROJECT_MEMBER",
            resource_id=ids.editor_a,
            action="update",
        )
        == audits_before
    )

    missing = client.patch(
        _members_url(ids.project_a, f"/{ids.outsider}"),
        json={"role": "editor"},
    )
    assert missing.status_code == 404

    invalid = client.patch(
        _members_url(ids.project_a, f"/{ids.editor_a}"),
        json={"role": "owner", "extra": 1},
    )
    assert invalid.status_code == 422


def test_cannot_demote_last_owner(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    # project_b 只有 owner_a 一名 owner。
    client = make_client(factory, ids.owner_a)
    response = client.patch(
        _members_url(ids.project_b, f"/{ids.owner_a}"),
        json={"role": "editor"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"

    # 加第二名 owner 后可以降级。
    added = client.post(
        _members_url(ids.project_b),
        json=member_payload(ids.outsider, "owner"),
    )
    assert added.status_code == 201
    demoted = client.patch(
        _members_url(ids.project_b, f"/{ids.owner_a}"),
        json={"role": "editor"},
    )
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "editor"


# --- 移除 -------------------------------------------------------------------
def test_remove_member(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.owner_a)

    removed = client.delete(_members_url(ids.project_a, f"/{ids.viewer_a}"))
    assert removed.status_code == 204
    assert removed.text == ""

    remaining = {item["user_id"] for item in _members(client, ids.project_a)["items"]}
    assert str(ids.viewer_a) not in remaining
    # 被移除者立即失去访问权。
    assert make_client(factory, ids.viewer_a).get(f"{PROJECTS}/{ids.project_a}").status_code == 403
    assert (
        _count(
            factory,
            project_id=ids.project_a,
            resource_type="PROJECT_MEMBER",
            resource_id=ids.viewer_a,
            action="remove",
        )
        == 1
    )
    with factory() as session:
        event = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "project.member_removed",
                OutboxEvent.project_id == ids.project_a,
            )
        ).one()
    assert event.status == "RECORDED"

    missing = client.delete(_members_url(ids.project_a, f"/{ids.viewer_a}"))
    assert missing.status_code == 404


def test_owner_can_remove_self_only_if_another_owner_remains(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.owner_a)

    # project_a 只有 owner_a 一名 owner：移除自己必须被拒绝。
    denied = client.delete(_members_url(ids.project_a, f"/{ids.owner_a}"))
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "conflict"
    assert client.get(f"{PROJECTS}/{ids.project_a}").status_code == 200

    added = client.post(
        _members_url(ids.project_a),
        json=member_payload(ids.outsider, "owner"),
    )
    assert added.status_code == 201
    removed = client.delete(_members_url(ids.project_a, f"/{ids.owner_a}"))
    assert removed.status_code == 204
    # 移除自己后该主体不再是成员。
    assert client.get(f"{PROJECTS}/{ids.project_a}").status_code == 403


def test_non_owner_cannot_remove_member(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    for user_id in (ids.editor_a, ids.viewer_a, ids.outsider):
        response = make_client(factory, user_id).delete(
            _members_url(ids.project_a, f"/{ids.owner_a}")
        )
        assert response.status_code == 403
