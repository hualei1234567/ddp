"""项目 API 集成测试（阶段 8）。

覆盖：列表可见性（成员 vs 平台管理员）、分页、创建权限与审计/outbox、
详情越权、PATCH 乐观锁与字段校验、归档语义。

全部使用真实 SQLite + 真实仓储 + 真实路由，没有 mock。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.domain.identity.models import PlatformRole
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.project_support import project_payload, seed_user

PROJECTS = "/api/v1/projects"


def _list(client: TestClient, **params: object) -> dict[str, object]:
    response = client.get(PROJECTS, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _codes(payload: dict[str, object]) -> list[str]:
    return [str(item["code"]) for item in payload["items"]]  # type: ignore[union-attr]


def _count(factory: sessionmaker[Session], model: object, **filters: object) -> int:
    statement = select(func.count()).select_from(model)  # type: ignore[arg-type]
    for column, value in filters.items():
        statement = statement.where(getattr(model, column) == value)
    with factory() as session:
        return int(session.scalar(statement) or 0)


# --- 列表与可见性 -----------------------------------------------------------
def test_member_lists_only_own_projects(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db

    owner_payload = _list(make_client(factory, ids.owner_a))
    assert _codes(owner_payload) == ["project-a", "project-b"]
    first = owner_payload["items"][0]
    assert first["membership_role"] == "owner"
    assert first["allowed_actions"] == [
        "read",
        "write",
        "delete",
        "validate",
        "manage_members",
    ]

    editor_payload = _list(make_client(factory, ids.editor_a))
    assert _codes(editor_payload) == ["project-a"]
    assert editor_payload["items"][0]["allowed_actions"] == [
        "read",
        "write",
        "validate",
    ]

    viewer_payload = _list(make_client(factory, ids.viewer_a))
    assert viewer_payload["items"][0]["allowed_actions"] == ["read"]

    outsider_payload = _list(make_client(factory, ids.outsider))
    assert outsider_payload["items"] == []
    assert outsider_payload["next_cursor"] is None


def test_platform_admin_lists_all_projects(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, _ids = api_db
    admin_id = seed_user(factory)
    client = make_client(factory, admin_id, platform_role=PlatformRole.ADMIN)

    payload = _list(client)
    assert _codes(payload) == ["project-a", "project-b"]
    assert payload["items"][0]["membership_role"] is None
    assert payload["items"][0]["allowed_actions"] == [
        "read",
        "write",
        "delete",
        "validate",
        "manage_members",
    ]


def test_cursor_pagination(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, _ids = api_db
    admin_id = seed_user(factory)
    client = make_client(factory, admin_id, platform_role=PlatformRole.ADMIN)

    first_page = _list(client, limit=1)
    assert _codes(first_page) == ["project-a"]
    cursor = first_page["next_cursor"]
    assert isinstance(cursor, str)

    second_page = _list(client, limit=1, cursor=cursor)
    assert _codes(second_page) == ["project-b"]
    assert second_page["next_cursor"] is None

    invalid = client.get(PROJECTS, params={"cursor": "!!!not-base64!!!"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_failed"

    bad_status = client.get(PROJECTS, params={"status": "DELETED"})
    assert bad_status.status_code == 422


# --- 创建 -------------------------------------------------------------------
def test_only_platform_admin_can_create_project(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner_client = make_client(factory, ids.owner_a)
    denied = owner_client.post(PROJECTS, json=project_payload())
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"

    admin_id = seed_user(factory)
    admin_client = make_client(factory, admin_id, platform_role=PlatformRole.ADMIN)
    created = admin_client.post(PROJECTS, json=project_payload())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "ACTIVE"
    assert body["version"] == 1
    assert body["code"] == "prj-new"
    assert body["membership_role"] == "owner"
    assert body["project_id"]

    # 创建者自动成为 owner：普通审计与 outbox 与业务同事务落库。
    project_id = UUID(str(body["project_id"]))
    assert _count(factory, AuditLog, project_id=project_id, resource_type="PROJECT") == 1
    with factory() as session:
        event = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "project.created",
                OutboxEvent.project_id == project_id,
            )
        ).one()
    assert event.status == "RECORDED"
    assert event.dispatch is False

    # 非成员看不到新项目；管理员能看到。
    outsider_view = make_client(factory, ids.owner_a).get(f"{PROJECTS}/{body['project_id']}")
    assert outsider_view.status_code == 403
    assert admin_client.get(f"{PROJECTS}/{body['project_id']}").status_code == 200


def test_create_project_duplicate_code_conflicts(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, _ids = api_db
    admin_id = seed_user(factory)
    client = make_client(factory, admin_id, platform_role=PlatformRole.ADMIN)

    duplicate = client.post(PROJECTS, json=project_payload(code="project-a"))
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"

    # 大小写/空格差异会先被规范化，再判定重复。
    padded = client.post(PROJECTS, json=project_payload(code="  project-a  "))
    assert padded.status_code == 409


def test_create_project_validates_body(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, _ids = api_db
    admin_id = seed_user(factory)
    client = make_client(factory, admin_id, platform_role=PlatformRole.ADMIN)

    assert client.post(PROJECTS, json={"code": "p1"}).status_code == 422
    injected_id = client.post(
        PROJECTS,
        json={"code": "p1", "name": "x", "project_id": str(uuid4())},
    )
    assert injected_id.status_code == 422
    blank = client.post(PROJECTS, json={"code": "   ", "name": "x"})
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "validation_failed"
    assert client.post(PROJECTS, json={"code": "p1", "name": ""}).status_code == 422


def test_admin_principal_must_exist_in_user_directory(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, _ids = api_db
    # 未登记到 users 的认证主体：project_members 有外键，直接拒绝比留下孤儿项目好。
    client = make_client(factory, uuid4(), platform_role=PlatformRole.ADMIN)
    response = client.post(PROJECTS, json=project_payload())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


# --- 详情 -------------------------------------------------------------------
def test_get_project_enforces_membership(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db

    owner = make_client(factory, ids.owner_a).get(f"{PROJECTS}/{ids.project_a}")
    assert owner.status_code == 200
    assert owner.json()["description"] is None
    assert owner.json()["membership_role"] == "owner"

    assert make_client(factory, ids.outsider).get(f"{PROJECTS}/{ids.project_a}").status_code == 403
    # 成员访问不存在的项目同样是 403：不暴露项目是否存在。
    assert make_client(factory, ids.owner_a).get(f"{PROJECTS}/{uuid4()}").status_code == 403

    admin_id = seed_user(factory)
    admin = make_client(factory, admin_id, platform_role=PlatformRole.ADMIN)
    assert admin.get(f"{PROJECTS}/{uuid4()}").status_code == 404


# --- 修改 -------------------------------------------------------------------
def test_patch_project_with_optimistic_lock(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    url = f"{PROJECTS}/{ids.project_a}"

    missing = owner.patch(url, json={"name": "新名字"})
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "precondition_required"

    patched = owner.patch(
        url,
        json={"name": "  新名字  ", "description": "新说明"},
        headers={"If-Match": '"1"'},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["name"] == "新名字"
    assert body["description"] == "新说明"
    assert body["version"] == 2

    stale = owner.patch(url, json={"name": "再次"}, headers={"If-Match": '"1"'})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conflict"

    cleared = owner.patch(url, json={"description": None}, headers={"If-Match": '"2"'})
    assert cleared.status_code == 200
    assert cleared.json()["description"] is None
    assert cleared.json()["version"] == 3

    # 两次成功 PATCH → 两条审计、两条 outbox 事件（409 那次不落库）。
    assert _count(factory, AuditLog, project_id=ids.project_a, resource_type="PROJECT") == 2
    with factory() as session:
        events = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "project.updated",
                OutboxEvent.project_id == ids.project_a,
            )
        ).all()
    assert len(events) == 2
    assert {event.status for event in events} == {"RECORDED"}


def test_patch_project_requires_owner_and_valid_fields(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    url = f"{PROJECTS}/{ids.project_a}"

    for user_id in (ids.editor_a, ids.viewer_a, ids.outsider):
        denied = make_client(factory, user_id).patch(
            url, json={"name": "x"}, headers={"If-Match": '"1"'}
        )
        assert denied.status_code == 403

    owner = make_client(factory, ids.owner_a)
    blank_name = owner.patch(url, json={"name": "   "}, headers={"If-Match": '"1"'})
    assert blank_name.status_code == 422
    null_name = owner.patch(url, json={"name": None}, headers={"If-Match": '"1"'})
    assert null_name.status_code == 422
    unknown_status = owner.patch(url, json={"status": "DELETED"}, headers={"If-Match": '"1"'})
    assert unknown_status.status_code == 422
    immutable_code = owner.patch(url, json={"code": "other"}, headers={"If-Match": '"1"'})
    assert immutable_code.status_code == 422
    empty = owner.patch(url, json={}, headers={"If-Match": '"1"'})
    assert empty.status_code == 422
    bad_if_match = owner.patch(url, json={"name": "x"}, headers={"If-Match": 'W/"abc"'})
    assert bad_if_match.status_code == 422


def test_archive_hides_project_from_default_list(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)

    archived = owner.patch(
        f"{PROJECTS}/{ids.project_a}",
        json={"status": "ARCHIVED"},
        headers={"If-Match": '"1"'},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"

    assert _codes(_list(owner)) == ["project-b"]
    assert _codes(_list(owner, status="ARCHIVED")) == ["project-a"]

    # 归档不是权限开关：治理操作仍可用，否则项目无法被恢复。
    assert owner.get(f"{PROJECTS}/{ids.project_a}/members").status_code == 200
    restored = owner.patch(
        f"{PROJECTS}/{ids.project_a}",
        json={"status": "ACTIVE"},
        headers={"If-Match": '"2"'},
    )
    assert restored.status_code == 200
    assert _codes(_list(owner)) == ["project-a", "project-b"]


def test_patch_unknown_project_as_admin_is_404(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, _ids = api_db
    admin_id = seed_user(factory)
    admin = make_client(factory, admin_id, platform_role=PlatformRole.ADMIN)
    response = admin.patch(
        f"{PROJECTS}/{uuid4()}",
        json={"name": "x"},
        headers={"If-Match": '"1"'},
    )
    assert response.status_code == 404
