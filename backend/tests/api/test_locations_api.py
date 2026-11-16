"""场地 API 集成测试（N9 多基地）。

覆盖：创建/列表/详情/修改、项目隔离、越权、幂等重放、``If-Match`` 乐观锁，
以及**本轮修复的核心**——设备/机器人/采集任务写入时的场地引用校验
（跨项目 → 404、幽灵 → 404、NULL 放行）。

全部用真实 SQLite + 真实仓储 + 真实路由，没有 mock。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client


def _url(project_id: object, path: str, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/{path}{suffix}"


def _create(
    client: TestClient,
    project_id: object,
    payload: dict[str, object],
) -> dict[str, object]:
    response = client.post(_url(project_id, "locations"), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def location_payload(code: str = "BJ-1", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": code,
        "name": f"基地 {code}",
        "kind": "SITE",
        "address": "北京亦庄",
    }
    payload.update(overrides)
    return payload


def robot_payload(code: str = "robot-1", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"code": code, "name": f"机器人 {code}", "robot_type": "HUMANOID"}
    payload.update(overrides)
    return payload


def task_payload(code: str = "task-1", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": code,
        "name": f"采集任务 {code}",
        "priority": "P1",
        "target_episode_count": 1,
        "target_duration_sec": 60,
    }
    payload.update(overrides)
    return payload


# --- CRUD 与响应形状 --------------------------------------------------------
def test_location_crud_and_audit_outbox(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    created = _create(client, ids.project_a, location_payload())
    assert created["project_id"] == str(ids.project_a)
    assert created["kind"] == "SITE"
    # 本阶段刻意没有 status 列（见 domain/locations/enums.py 的理由）。
    assert "status" not in created
    assert created["version"] == 1
    assert created["code"] == "BJ-1"

    listed = client.get(_url(ids.project_a, "locations"))
    assert listed.status_code == 200
    assert [item["location_id"] for item in listed.json()["items"]] == [created["location_id"]]

    detail = client.get(_url(ids.project_a, "locations", f"/{created['location_id']}"))
    assert detail.status_code == 200
    assert detail.json()["name"] == "基地 BJ-1"

    # 审计与 outbox 必须与业务同事务落库（留痕类事件，dispatch=False）。
    with factory() as session:
        audits = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.project_id == ids.project_a,
                AuditLog.resource_type == "LOCATION",
            )
        )
        assert audits == 1
        event = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.project_id == ids.project_a,
                OutboxEvent.event_type == "location.created",
            )
        ).one()
        assert event.status == "RECORDED"


def test_location_kind_filter_and_pagination(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    _create(client, ids.project_a, location_payload("BJ-1", kind="SITE"))
    _create(client, ids.project_a, location_payload("BJ-1-LAB", kind="LAB"))

    sites = client.get(_url(ids.project_a, "locations"), params={"kind": "SITE"})
    assert [item["code"] for item in sites.json()["items"]] == ["BJ-1"]

    labs = client.get(_url(ids.project_a, "locations"), params={"kind": "LAB"})
    assert [item["code"] for item in labs.json()["items"]] == ["BJ-1-LAB"]

    invalid = client.get(_url(ids.project_a, "locations"), params={"kind": "WAREHOUSE"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_failed"


def test_duplicate_code_in_project_is_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    _create(client, ids.project_a, location_payload("BJ-1"))

    response = client.post(_url(ids.project_a, "locations"), json=location_payload("BJ-1"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_resource"

    # 同名 code 在另一个项目里合法：唯一性是项目内，不是全局。
    # 用 owner_a（project_b 的 owner；editor_a 只是 project_a 的成员）。
    other_client = make_client(factory, ids.owner_a)
    other = other_client.post(_url(ids.project_b, "locations"), json=location_payload("BJ-1"))
    assert other.status_code == 201


def test_create_rejects_unknown_fields_and_bad_kind(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    for payload in (
        location_payload(project_id=str(uuid4())),
        location_payload(version=7),
        location_payload(kind="WAREHOUSE"),
    ):
        response = client.post(_url(ids.project_a, "locations"), json=payload)
        assert response.status_code == 422, payload


def test_idempotent_create_replays_the_same_location(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    headers = {"Idempotency-Key": "locations-create-1"}

    first = client.post(_url(ids.project_a, "locations"), json=location_payload(), headers=headers)
    second = client.post(_url(ids.project_a, "locations"), json=location_payload(), headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["location_id"] == second.json()["location_id"]
    # 同键不同体必须 409，而不是静默返回上一次结果。
    conflict = client.post(
        _url(ids.project_a, "locations"),
        json=location_payload(name="另一个名字"),
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reused"


def test_patch_requires_if_match_and_uses_optimistic_lock(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    created = _create(client, ids.project_a, location_payload())
    path = _url(ids.project_a, "locations", f"/{created['location_id']}")

    missing = client.patch(path, json={"name": "新名字"})
    assert missing.status_code == 428

    updated = client.patch(path, json={"name": "新名字"}, headers={"If-Match": '"1"'})
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "新名字"
    assert updated.json()["version"] == 2

    # 用过期版本再改必须 409。
    stale = client.patch(path, json={"name": "再次改名"}, headers={"If-Match": '"1"'})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"


def test_patch_clears_nullable_fields_with_explicit_null(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    created = _create(client, ids.project_a, location_payload(address="旧地址", notes="旧备注"))
    path = _url(ids.project_a, "locations", f"/{created['location_id']}")

    cleared = client.patch(path, json={"address": None}, headers={"If-Match": '"1"'})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["address"] is None
    # 未提供的字段不得被隐式清空。
    assert cleared.json()["notes"] == "旧备注"


def test_patch_rejects_null_kind_and_null_name(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """``kind`` / ``name`` 不可为空：显式 ``null`` 必须 422，不能静默清空。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    created = _create(client, ids.project_a, location_payload())
    path = _url(ids.project_a, "locations", f"/{created['location_id']}")

    for payload in ({"kind": None}, {"name": None}):
        response = client.patch(path, json=payload, headers={"If-Match": '"1"'})
        assert response.status_code == 422, payload
        assert response.json()["error"]["code"] == "validation_failed"


def test_patch_rejects_code_change(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    created = _create(client, ids.project_a, location_payload())
    path = _url(ids.project_a, "locations", f"/{created['location_id']}")

    response = client.patch(path, json={"code": "BJ-2"}, headers={"If-Match": '"1"'})
    assert response.status_code == 422


# --- 项目隔离与越权 ---------------------------------------------------------
def test_cross_project_access_is_denied(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    created = _create(editor, ids.project_a, location_payload())

    # outsider 不是任何项目成员：读取详情与列表都必须被拒。
    outsider = make_client(factory, ids.outsider)
    detail = outsider.get(_url(ids.project_a, "locations", f"/{created['location_id']}"))
    assert detail.status_code == 403
    listed = outsider.get(_url(ids.project_a, "locations"))
    assert listed.status_code == 403

    # 同一用户在有权限的另一个项目里看不到 A 项目的场地（服务端强制 scope）。
    other_owner = make_client(factory, ids.owner_a)
    listed_b = other_owner.get(_url(ids.project_b, "locations"))
    assert listed_b.status_code == 200
    assert listed_b.json()["items"] == []
    missing = other_owner.get(_url(ids.project_b, "locations", f"/{created['location_id']}"))
    assert missing.status_code == 404


def test_viewer_cannot_write(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    viewer = make_client(factory, ids.viewer_a)

    assert viewer.get(_url(ids.project_a, "locations")).status_code == 200
    created = viewer.post(_url(ids.project_a, "locations"), json=location_payload())
    assert created.status_code == 403


# --- 三处写入路径的场地引用校验（本次修复的核心） --------------------
def test_robot_rejects_ghost_and_cross_project_location(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    ghost = editor.post(
        _url(ids.project_a, "robots"),
        json=robot_payload(location_id=str(uuid4())),
    )
    assert ghost.status_code == 404
    assert ghost.json()["error"]["code"] == "resource_not_found"

    # B 项目的场地在 A 项目下不可见：同样 404，不泄漏跨项目存在性。
    # 用 owner_a 在 project_b 建场地（editor_a 不是 project_b 成员）。
    foreign = _create(make_client(factory, ids.owner_a), ids.project_b, location_payload("SH-1"))
    cross = editor.post(
        _url(ids.project_a, "robots"),
        json=robot_payload(location_id=foreign["location_id"]),
    )
    assert cross.status_code == 404

    own = _create(editor, ids.project_a, location_payload("BJ-1"))
    ok = editor.post(
        _url(ids.project_a, "robots"),
        json=robot_payload(location_id=own["location_id"]),
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["location_id"] == own["location_id"]

    # NULL（尚未指定基地）必须放行。
    null_case = editor.post(_url(ids.project_a, "robots"), json=robot_payload("robot-2"))
    assert null_case.status_code == 201
    assert null_case.json()["location_id"] is None


def test_device_rejects_ghost_location_on_create_and_patch(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    ghost = editor.post(
        _url(ids.project_a, "devices"),
        json={
            "code": "dev-1",
            "name": "设备",
            "device_type": "COMPUTE",
            "location_id": str(uuid4()),
        },
    )
    assert ghost.status_code == 404

    own = _create(editor, ids.project_a, location_payload("BJ-1"))
    created = editor.post(
        _url(ids.project_a, "devices"),
        json={"code": "dev-1", "name": "设备", "device_type": "COMPUTE"},
    )
    assert created.status_code == 201, created.text

    path = _url(ids.project_a, "devices", f"/{created.json()['device_id']}")
    bad_patch = editor.patch(
        path,
        json={"location_id": str(uuid4())},
        headers={"If-Match": '"1"'},
    )
    assert bad_patch.status_code == 404

    good_patch = editor.patch(
        path,
        json={"location_id": own["location_id"]},
        headers={"If-Match": '"1"'},
    )
    assert good_patch.status_code == 200, good_patch.text
    assert good_patch.json()["location_id"] == own["location_id"]


def test_collection_task_rejects_ghost_and_cross_project_location(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    ghost = editor.post(
        _url(ids.project_a, "collection-tasks"),
        json=task_payload(location_id=str(uuid4())),
    )
    assert ghost.status_code == 404
    assert ghost.json()["error"]["code"] == "resource_not_found"

    foreign = _create(make_client(factory, ids.owner_a), ids.project_b, location_payload("SH-1"))
    cross = editor.post(
        _url(ids.project_a, "collection-tasks"),
        json=task_payload(location_id=foreign["location_id"]),
    )
    assert cross.status_code == 404

    own = _create(editor, ids.project_a, location_payload("BJ-1"))
    ok = editor.post(
        _url(ids.project_a, "collection-tasks"),
        json=task_payload(location_id=own["location_id"]),
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["location_id"] == own["location_id"]

    # PATCH 也不能成为绕过场地校验的后门。
    task_path = _url(ids.project_a, "collection-tasks", f"/{ok.json()['task_id']}")
    bad_patch = editor.patch(
        task_path,
        json={"location_id": str(uuid4())},
        headers={"If-Match": '"1"'},
    )
    assert bad_patch.status_code == 404
    cleared = editor.patch(task_path, json={"location_id": None}, headers={"If-Match": '"1"'})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["location_id"] is None


def test_collection_task_assign_action_also_validates_location(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """``assign`` 动作也接受 ``location_id``：必须与创建/PATCH 走同一份校验。

    否则动作路由就是绕过场地校验的后门——PATCH 拦住了、assign 放过去，
    等于没修。
    """
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    created = editor.post(_url(ids.project_a, "collection-tasks"), json=task_payload())
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]

    # 走到可分配的 DRAFT → PENDING_REVIEW → APPROVED → READY。
    # approve 必须换人：职责分离规则禁止创建人审批自己的任务。
    owner = make_client(factory, ids.owner_a)
    version = 1
    for action, client in (
        ("submit-review", editor),
        ("approve", owner),
        ("ready", owner),
    ):
        response = client.post(
            _url(ids.project_a, "collection-tasks", f"/{task_id}/actions/{action}"),
            json={},
            headers={"If-Match": f'"{version}"'},
        )
        assert response.status_code == 200, (action, response.text)
        version = response.json()["version"]

    ghost = editor.post(
        _url(ids.project_a, "collection-tasks", f"/{task_id}/actions/assign"),
        json={
            "assigned_to": str(ids.editor_a),
            "location_id": str(uuid4()),
        },
        headers={"If-Match": f'"{version}"'},
    )
    assert ghost.status_code == 404
    assert ghost.json()["error"]["code"] == "resource_not_found"


def test_location_id_in_response_is_project_scoped(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """场地响应不得回显任何其它项目的数据；``project_id`` 必须等于路径项目。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    created = _create(editor, ids.project_a, location_payload())

    detail = editor.get(_url(ids.project_a, "locations", f"/{created['location_id']}"))
    body = detail.json()
    assert UUID(body["project_id"]) == ids.project_a
    assert set(body) == {
        "location_id",
        "project_id",
        "code",
        "name",
        "kind",
        "address",
        "notes",
        "version",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
    }
