"""采集素材 API 集成测试（COL-002）。

覆盖四类素材的 CRUD、跨项目隔离与越权，以及**本次修复的核心**：
任务引用素材时的同项目校验（跨项目 → 404、幽灵 → 404、版本不一致 → 422）。

这些断言都用真实 SQLite + 真实仓储 + 真实路由，没有 mock。
"""

from __future__ import annotations

from uuid import UUID

import pytest
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
    path: str,
    payload: dict[str, object],
) -> dict[str, object]:
    response = client.post(_url(project_id, path), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def plan_payload(code: str = "plan-1", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"code": code, "name": f"方案 {code}", "plan_version": 1}
    payload.update(overrides)
    return payload


def sop_payload(code: str = "sop-1", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": code,
        "name": f"SOP {code}",
        "sop_version": "v1",
        "steps": [{"index": 1, "action": "开机检查"}],
    }
    payload.update(overrides)
    return payload


def scenario_payload(code: str = "scene-1", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": code,
        "name": f"场景 {code}",
        "scene_labels": ["家庭", "桌面"],
    }
    payload.update(overrides)
    return payload


def action_spec_payload(code: str = "act-1", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": code,
        "name": f"动作库 {code}",
        "spec_version": "v1",
        "actions": [{"code": "pick", "name": "抓取"}],
    }
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
def test_plan_crud_and_audit_outbox(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    created = _create(client, ids.project_a, "collection-plans", plan_payload())
    assert created["project_id"] == str(ids.project_a)
    assert created["status"] == "DRAFT"
    assert created["plan_version"] == 1
    assert created["version"] == 1

    listed = client.get(_url(ids.project_a, "collection-plans"))
    assert listed.status_code == 200
    assert [item["plan_id"] for item in listed.json()["items"]] == [created["plan_id"]]

    detail = client.get(_url(ids.project_a, "collection-plans", f"/{created['plan_id']}"))
    assert detail.status_code == 200
    assert detail.json()["code"] == "plan-1"

    # 审计与 outbox 必须与业务同事务落库：留痕类事件（dispatch=False）。
    with factory() as session:
        audits = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.project_id == ids.project_a,
                AuditLog.resource_type == "COLLECTION_PLAN",
            )
        )
        assert audits == 1
        events = session.scalars(
            select(OutboxEvent).where(OutboxEvent.project_id == ids.project_a)
        ).all()
        assert [event.event_type for event in events] == ["collection.plan.created"]
        assert events[0].dispatch is False
        assert events[0].status == "RECORDED"


def test_sop_scenario_action_spec_crud(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    sop = _create(client, ids.project_a, "collection-sops", sop_payload())
    assert sop["sop_version"] == "v1"
    assert sop["steps"] == [{"index": 1, "action": "开机检查"}]

    scenario = _create(client, ids.project_a, "scenarios", scenario_payload())
    assert scenario["scene_labels"] == ["家庭", "桌面"]

    spec = _create(client, ids.project_a, "action-specs", action_spec_payload())
    assert spec["spec_version"] == "v1"
    assert spec["actions"] == [{"code": "pick", "name": "抓取"}]

    for path, identifier, key in (
        ("collection-sops", sop["sop_id"], "sop_id"),
        ("scenarios", scenario["scenario_id"], "scenario_id"),
        ("action-specs", spec["action_spec_id"], "action_spec_id"),
    ):
        detail = client.get(_url(ids.project_a, path, f"/{identifier}"))
        assert detail.status_code == 200, detail.text
        assert detail.json()[key] == identifier
        assert client.get(_url(ids.project_a, path)).json()["items"]


def test_duplicate_code_version_is_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    _create(client, ids.project_a, "collection-plans", plan_payload())

    duplicate = client.post(
        _url(ids.project_a, "collection-plans"),
        json=plan_payload(),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_resource"

    # 同 code 的新版本必须允许（方案是版本化的）。
    next_version = client.post(
        _url(ids.project_a, "collection-plans"),
        json=plan_payload(plan_version=2),
    )
    assert next_version.status_code == 201, next_version.text
    assert next_version.json()["plan_version"] == 2


def test_invalid_status_filter_is_validation_error(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    response = client.get(_url(ids.project_a, "collection-plans", "?status=UNKNOWN"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# --- 权限与项目隔离 ---------------------------------------------------------
def test_viewer_reads_but_cannot_write_and_outsider_denied(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    plan = _create(editor, ids.project_a, "collection-plans", plan_payload())

    viewer = make_client(factory, ids.viewer_a)
    assert viewer.get(_url(ids.project_a, "collection-plans")).status_code == 200
    assert (
        viewer.get(_url(ids.project_a, "collection-plans", f"/{plan['plan_id']}")).status_code
        == 200
    )
    denied = viewer.post(_url(ids.project_a, "collection-plans"), json=plan_payload("plan-2"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"

    outsider = make_client(factory, ids.outsider)
    assert outsider.get(_url(ids.project_a, "collection-plans")).status_code == 403
    assert (
        outsider.get(_url(ids.project_a, "collection-plans", f"/{plan['plan_id']}")).status_code
        == 403
    )


def test_cross_project_asset_ids_are_not_found(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """B 项目的素材 ID 在 A 项目下必须 404，不能因为"ID 存在"而泄漏。"""
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    foreign = _create(owner, ids.project_b, "collection-plans", plan_payload("b-plan"))

    editor_a = make_client(factory, ids.editor_a)
    response = editor_a.get(_url(ids.project_a, "collection-plans", f"/{foreign['plan_id']}"))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"

    listed = editor_a.get(_url(ids.project_a, "collection-plans"))
    assert listed.json()["items"] == []


# --- 任务引用素材：本次修复的核心 -------------------------------------------
def test_task_can_reference_same_project_assets(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    plan = _create(client, ids.project_a, "collection-plans", plan_payload())
    sop = _create(client, ids.project_a, "collection-sops", sop_payload())
    scenario = _create(client, ids.project_a, "scenarios", scenario_payload())
    spec = _create(client, ids.project_a, "action-specs", action_spec_payload())

    task = _create(
        client,
        ids.project_a,
        "collection-tasks",
        task_payload(
            plan_id=plan["plan_id"],
            sop_id=sop["sop_id"],
            sop_version="v1",
            scenario_id=scenario["scenario_id"],
            action_spec_id=spec["action_spec_id"],
            action_spec_version="v1",
        ),
    )
    assert task["plan_id"] == plan["plan_id"]
    assert task["sop_id"] == sop["sop_id"]
    assert task["sop_version"] == "v1"
    assert task["scenario_id"] == scenario["scenario_id"]
    assert task["action_spec_id"] == spec["action_spec_id"]
    assert task["action_spec_version"] == "v1"


def test_task_cannot_reference_cross_project_plan(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """核心负向用例：A 项目任务引用 B 项目方案必须 404，且任务不落库。"""
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    foreign_plan = _create(owner, ids.project_b, "collection-plans", plan_payload("b-plan"))

    editor_a = make_client(factory, ids.editor_a)
    response = editor_a.post(
        _url(ids.project_a, "collection-tasks"),
        json=task_payload(plan_id=foreign_plan["plan_id"]),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"

    # 关键断言：拒绝意味着没有任何任务被写入（不能出现"先写再报错"的残留）。
    listed = editor_a.get(_url(ids.project_a, "collection-tasks"))
    assert listed.json()["items"] == []


def test_task_cannot_reference_ghost_uuid(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """不存在的 UUID（幽灵引用）必须可读地拒绝，而不是数据库 500。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    ghost = "00000000-0000-0000-0000-000000000001"
    for field in ("plan_id", "scenario_id", "action_spec_id", "sop_id"):
        response = client.post(
            _url(ids.project_a, "collection-tasks"),
            json=task_payload(f"task-{field}", **{field: ghost}),
        )
        assert response.status_code == 404, (field, response.text)
        assert response.json()["error"]["code"] == "resource_not_found"


def test_task_sop_version_must_match_referenced_sop(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """版本快照必须与实际行一致：v1 的 SOP 不能写成 v9。

    外键只校验 ``(project_id, sop_id)`` 存在，**不校验版本号**，
    所以这条必须由应用层拒绝，否则任务会带着不存在的版本快照进入执行。
    """
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    sop = _create(client, ids.project_a, "collection-sops", sop_payload())

    mismatched = client.post(
        _url(ids.project_a, "collection-tasks"),
        json=task_payload("task-bad-version", sop_id=sop["sop_id"], sop_version="v9"),
    )
    assert mismatched.status_code == 422
    assert mismatched.json()["error"]["code"] == "validation_failed"

    missing_version = client.post(
        _url(ids.project_a, "collection-tasks"),
        json=task_payload("task-no-version", sop_id=sop["sop_id"]),
    )
    assert missing_version.status_code == 422
    assert missing_version.json()["error"]["code"] == "validation_failed"

    orphan_version = client.post(
        _url(ids.project_a, "collection-tasks"),
        json=task_payload("task-orphan-version", sop_version="v1"),
    )
    assert orphan_version.status_code == 422
    assert orphan_version.json()["error"]["code"] == "validation_failed"


def test_task_action_spec_version_must_match(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    spec = _create(client, ids.project_a, "action-specs", action_spec_payload())

    mismatched = client.post(
        _url(ids.project_a, "collection-tasks"),
        json=task_payload(
            "task-bad-spec",
            action_spec_id=spec["action_spec_id"],
            action_spec_version="v2",
        ),
    )
    assert mismatched.status_code == 422
    assert mismatched.json()["error"]["code"] == "validation_failed"


def test_cross_project_scenario_and_action_spec_are_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    foreign_scenario = _create(owner, ids.project_b, "scenarios", scenario_payload("b-scene"))
    foreign_spec = _create(owner, ids.project_b, "action-specs", action_spec_payload("b-act"))

    editor_a = make_client(factory, ids.editor_a)
    scenario_response = editor_a.post(
        _url(ids.project_a, "collection-tasks"),
        json=task_payload("task-x-scene", scenario_id=foreign_scenario["scenario_id"]),
    )
    assert scenario_response.status_code == 404
    spec_response = editor_a.post(
        _url(ids.project_a, "collection-tasks"),
        json=task_payload(
            "task-x-spec",
            action_spec_id=foreign_spec["action_spec_id"],
            action_spec_version="v1",
        ),
    )
    assert spec_response.status_code == 404


def test_task_patch_cannot_bypass_sop_version_check(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """回归用例：PATCH 是可改 ``sop_version`` 的入口，不能成为版本校验的后门。

    创建时校验了"版本必须与所引用 SOP 那一行一致"，但 ``sop_version`` 同时在
    PATCH 可改字段里。若 PATCH 不复用同一份校验，任务可以先把引用建好，
    再把版本号改成任意值（如不存在的 ``v9``），最终落库一个自相矛盾的版本快照。
    """
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    sop = _create(client, ids.project_a, "collection-sops", sop_payload())
    task = _create(
        client,
        ids.project_a,
        "collection-tasks",
        task_payload(sop_id=sop["sop_id"], sop_version="v1"),
    )

    rejected = client.patch(
        _url(ids.project_a, "collection-tasks", f"/{task['task_id']}"),
        json={"sop_version": "v9"},
        headers={"If-Match": f'"{task["version"]}"'},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "validation_failed"

    # 未被改写的证据：详情里的 sop_version 仍是 v1。
    reloaded = client.get(_url(ids.project_a, "collection-tasks", f"/{task['task_id']}"))
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()["sop_version"] == "v1"


def test_task_patch_allows_consistent_sop_version(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """反向断言：写成与所引用行一致的版本仍应成功，证明上面的拒绝不是"一律拒绝"。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    sop = _create(client, ids.project_a, "collection-sops", sop_payload())
    task = _create(
        client,
        ids.project_a,
        "collection-tasks",
        task_payload(sop_id=sop["sop_id"], sop_version="v1"),
    )

    # 顺带固定一个协议细节：领域层 ``optional_text`` 会裁剪首尾空白，
    # 因此 " v1 " 落库后读回是 "v1"；校验用的是裁剪后的值，不会误判为不一致。
    accepted = client.patch(
        _url(ids.project_a, "collection-tasks", f"/{task['task_id']}"),
        json={"sop_version": " v1 "},
        headers={"If-Match": f'"{task["version"]}"'},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["sop_version"] == "v1"


def test_asset_create_supports_idempotency_key(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    headers = {"Idempotency-Key": "asset-idem-0001"}

    first = client.post(
        _url(ids.project_a, "collection-plans"),
        json=plan_payload(),
        headers=headers,
    )
    second = client.post(
        _url(ids.project_a, "collection-plans"),
        json=plan_payload(),
        headers=headers,
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["plan_id"] == second.json()["plan_id"]
    assert len(client.get(_url(ids.project_a, "collection-plans")).json()["items"]) == 1

    reused = client.post(
        _url(ids.project_a, "collection-plans"),
        json=plan_payload("plan-2"),
        headers=headers,
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "idempotency_key_reused"


def test_asset_response_project_id_is_server_authoritative(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """客户端不能通过请求体注入 project_id（``extra="forbid"`` 直接 422）。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    response = client.post(
        _url(ids.project_a, "collection-plans"),
        json=plan_payload(project_id=str(ids.project_b)),
    )
    assert response.status_code == 422
    assert UUID(str(ids.project_a)) != UUID(str(ids.project_b))


# --- N8 写路径：状态动作 / PATCH / DELETE -----------------------------------
def _act(
    client: TestClient,
    project_id: object,
    path: str,
    asset_id: object,
    action: str,
    *,
    version: int,
    key: str | None = None,
    json: dict[str, object] | None = None,
) -> object:
    headers = {"If-Match": f'"{version}"'}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(
        _url(project_id, path, f"/{asset_id}/actions/{action}"),
        headers=headers,
        json=json,
    )


ASSET_KINDS = (
    ("collection-plans", "plan_id", plan_payload),
    ("collection-sops", "sop_id", sop_payload),
    ("scenarios", "scenario_id", scenario_payload),
    ("action-specs", "action_spec_id", action_spec_payload),
)


@pytest.mark.parametrize(("path", "id_field", "factory"), ASSET_KINDS)
def test_full_lifecycle_reaches_archived_and_stops_there(
    api_db: tuple[sessionmaker[Session], ApiIds],
    path: str,
    id_field: str,
    factory: object,
) -> None:
    """四类素材完整链路：DRAFT → ACTIVE → ARCHIVED，之后不可再迁出。

    N8 的核心价值就是这个断言：在此之前 ``ACTIVE`` / ``ARCHIVED`` 两个枚举值
    永远不可到达，``version`` 也不可演进。
    """
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    created = _create(client, ids.project_a, path, factory())  # type: ignore[operator]
    asset_id = created[id_field]
    assert created["status"] == "DRAFT"
    assert created["version"] == 1

    activated = _act(client, ids.project_a, path, asset_id, "activate", version=1)
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "ACTIVE"
    assert activated.json()["version"] == 2

    archived = _act(client, ids.project_a, path, asset_id, "archive", version=2)
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "ARCHIVED"
    assert archived.json()["version"] == 3

    # 终态：任何迁出都必须被拒绝，且不能"未知状态默认成功"。
    for action in ("activate", "archive"):
        denied = _act(client, ids.project_a, path, asset_id, action, version=3)
        assert denied.status_code == 409, denied.text
        assert denied.json()["error"]["code"] == "state_transition_invalid"


@pytest.mark.parametrize(("path", "id_field", "factory"), ASSET_KINDS)
def test_allowed_actions_follow_state_machine(
    api_db: tuple[sessionmaker[Session], ApiIds],
    path: str,
    id_field: str,
    factory: object,
) -> None:
    """``allowed_actions`` 必须与状态机同源，终态返回空数组。

    为什么要单独守着：前端**只按这个字段渲染按钮**（CLAUDE.md「动作按钮只按
    服务端 allowed_actions 渲染」）。若它退化成常量或遗漏终态，界面会给出一个
    点了必然 409 的按钮——比不显示更糟，因为用户会以为是自己操作错了。
    """
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    created = _create(client, ids.project_a, path, factory())  # type: ignore[operator]
    asset_id = created[id_field]

    # 创建响应即带该字段；DRAFT 只允许 activate
    assert created["allowed_actions"] == ["activate"]

    # 列表与详情同源
    listed = client.get(_url(ids.project_a, path))
    assert listed.status_code == 200
    assert listed.json()["items"][0]["allowed_actions"] == ["activate"]
    detail = client.get(_url(ids.project_a, path, f"/{asset_id}"))
    assert detail.json()["allowed_actions"] == ["activate"]

    activated = _act(client, ids.project_a, path, asset_id, "activate", version=1)
    assert activated.json()["allowed_actions"] == ["archive"]

    archived = _act(client, ids.project_a, path, asset_id, "archive", version=2)
    assert archived.json()["allowed_actions"] == []
    # 终态再查详情仍是空数组，不会因为"刚从 ACTIVE 过来"而残留
    assert client.get(_url(ids.project_a, path, f"/{asset_id}")).json()["allowed_actions"] == []


@pytest.mark.parametrize(("path", "id_field", "factory"), ASSET_KINDS)
def test_illegal_migration_is_conflict(
    api_db: tuple[sessionmaker[Session], ApiIds],
    path: str,
    id_field: str,
    factory: object,
) -> None:
    """跳过中间状态必须失败：DRAFT 不能直接 archive。"""
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    created = _create(client, ids.project_a, path, factory())  # type: ignore[operator]

    denied = _act(client, ids.project_a, path, created[id_field], "archive", version=1)
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "state_transition_invalid"


def test_unknown_action_is_validation_error(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    created = _create(client, ids.project_a, "collection-plans", plan_payload())

    denied = _act(
        client, ids.project_a, "collection-plans", created["plan_id"], "publish", version=1
    )
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "validation_failed"


def test_action_requires_if_match_and_rejects_stale_version(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    created = _create(client, ids.project_a, "collection-plans", plan_payload())
    url = _url(ids.project_a, "collection-plans", f"/{created['plan_id']}/actions/activate")

    missing = client.post(url)
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "precondition_required"

    stale = client.post(url, headers={"If-Match": '"7"'})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"

    invalid = client.post(url, headers={"If-Match": "not-a-version"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_failed"


def test_action_idempotency_replay_returns_same_state(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """同键同体重放必须返回同一结果；同键不同体必须 409。

    重放拿到的版本号是**当前**版本（3），不是当时响应里的 2——因为重放读的是
    资源本身。这仍然满足幂等语义：客户端看到的状态与首次一致。
    """
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    created = _create(client, ids.project_a, "collection-plans", plan_payload())
    plan_id = created["plan_id"]

    key = "asset-action-idem-1"
    headers = {"If-Match": '"1"', "Idempotency-Key": key}
    url = _url(ids.project_a, "collection-plans", f"/{plan_id}/actions/activate")
    first = client.post(url, headers=headers)
    replay = client.post(url, headers=headers)
    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "ACTIVE"
    # 重放读的是资源本身，因此拿到的是当前版本而不是首次响应里的版本。
    assert replay.json()["version"] == 2
    # 只有一次迁移：重放没有把状态再推进一步。
    assert len(client.get(_url(ids.project_a, "collection-plans")).json()["items"]) == 1

    # 同键不同体：必须报 idempotency_key_reused，不能静默当成重放。
    reused = client.post(
        _url(ids.project_a, "collection-plans", f"/{plan_id}/actions/archive"),
        headers={"If-Match": '"2"', "Idempotency-Key": key},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "idempotency_key_reused"


def test_action_writes_audit_and_outbox(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    created = _create(client, ids.project_a, "collection-plans", plan_payload())
    _act(client, ids.project_a, "collection-plans", created["plan_id"], "activate", version=1)

    with db_factory() as session:
        actions = session.scalars(
            select(AuditLog.action).where(
                AuditLog.project_id == ids.project_a,
                AuditLog.resource_type == "COLLECTION_PLAN",
            )
        ).all()
        assert sorted(actions) == ["activate", "create"]
        events = session.scalars(
            select(OutboxEvent.event_type).where(OutboxEvent.project_id == ids.project_a)
        ).all()
        assert "collection.plan.status_changed" in events
        # 留痕类事件：写入即 RECORDED，不进重试队列。
        statuses = session.scalars(
            select(OutboxEvent.status).where(OutboxEvent.project_id == ids.project_a)
        ).all()
        assert set(statuses) == {"RECORDED"}


@pytest.mark.parametrize(("path", "id_field", "factory"), ASSET_KINDS)
def test_patch_updates_descriptive_fields_on_draft_and_active(
    api_db: tuple[sessionmaker[Session], ApiIds],
    path: str,
    id_field: str,
    factory: object,
) -> None:
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    created = _create(client, ids.project_a, path, factory())  # type: ignore[operator]
    asset_id = created[id_field]

    drafted = client.patch(
        _url(ids.project_a, path, f"/{asset_id}"),
        headers={"If-Match": '"1"'},
        json={"name": "改过名", "description": "说明"},
    )
    assert drafted.status_code == 200, drafted.text
    assert drafted.json()["name"] == "改过名"
    assert drafted.json()["version"] == 2
    # ``code`` 不会被 PATCH 改动。
    assert drafted.json()["code"] == created["code"]

    assert _act(client, ids.project_a, path, asset_id, "activate", version=2).status_code == 200
    active = client.patch(
        _url(ids.project_a, path, f"/{asset_id}"),
        headers={"If-Match": '"3"'},
        json={"description": "ACTIVE 下仍可改描述"},
    )
    assert active.status_code == 200, active.text
    assert active.json()["description"] == "ACTIVE 下仍可改描述"
    assert active.json()["version"] == 4


def test_patch_cannot_change_code_or_execution_content(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """``code`` 是业务标识；``steps`` / ``actions`` 是执行内容。

    两者都不在 PATCH 模型里，因此客户端提交即 422（``extra="forbid"``），
    不会出现"提交了但被静默忽略"的情况。
    """
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    sop = _create(client, ids.project_a, "collection-sops", sop_payload())

    for payload in ({"code": "NEW-CODE"}, {"steps": []}, {"sop_version": "v9"}):
        denied = client.patch(
            _url(ids.project_a, "collection-sops", f"/{sop['sop_id']}"),
            headers={"If-Match": '"1"'},
            json=payload,
        )
        assert denied.status_code == 422, (payload, denied.text)
        assert denied.json()["error"]["code"] == "validation_failed"


def test_patch_archived_is_immutable(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    created = _create(client, ids.project_a, "collection-plans", plan_payload())
    plan_id = created["plan_id"]
    assert (
        _act(client, ids.project_a, "collection-plans", plan_id, "activate", version=1).status_code
        == 200
    )
    assert (
        _act(client, ids.project_a, "collection-plans", plan_id, "archive", version=2).status_code
        == 200
    )

    denied = client.patch(
        _url(ids.project_a, "collection-plans", f"/{plan_id}"),
        headers={"If-Match": '"3"'},
        json={"description": "归档后还想改"},
    )
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "resource_immutable"


def test_patch_requires_if_match(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    created = _create(client, ids.project_a, "collection-plans", plan_payload())

    missing = client.patch(
        _url(ids.project_a, "collection-plans", f"/{created['plan_id']}"),
        json={"name": "改名"},
    )
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "precondition_required"


def test_patch_empty_body_is_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    created = _create(client, ids.project_a, "collection-plans", plan_payload())

    denied = client.patch(
        _url(ids.project_a, "collection-plans", f"/{created['plan_id']}"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "validation_failed"


def test_scenario_scene_labels_patch(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    created = _create(client, ids.project_a, "scenarios", scenario_payload())

    updated = client.patch(
        _url(ids.project_a, "scenarios", f"/{created['scenario_id']}"),
        headers={"If-Match": '"1"'},
        json={"scene_labels": ["仓储", "仓储", "桌面"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["scene_labels"] == ["仓储", "桌面"]


# --- DELETE -----------------------------------------------------------------
@pytest.mark.parametrize(("path", "id_field", "factory"), ASSET_KINDS)
def test_owner_deletes_draft_asset(
    api_db: tuple[sessionmaker[Session], ApiIds],
    path: str,
    id_field: str,
    factory: object,
) -> None:
    db_factory, ids = api_db
    editor = make_client(db_factory, ids.editor_a)
    owner = make_client(db_factory, ids.owner_a)
    created = _create(editor, ids.project_a, path, factory())  # type: ignore[operator]
    asset_id = created[id_field]

    # 编辑者无 DELETE 权限（删除是不可逆动作，沿用既有权限矩阵）。
    denied = editor.delete(_url(ids.project_a, path, f"/{asset_id}"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"

    response = owner.delete(_url(ids.project_a, path, f"/{asset_id}"))
    assert response.status_code == 204, response.text
    assert owner.get(_url(ids.project_a, path, f"/{asset_id}")).status_code == 404


@pytest.mark.parametrize("action", ["activate"])
def test_cannot_delete_non_draft(
    api_db: tuple[sessionmaker[Session], ApiIds],
    action: str,
) -> None:
    db_factory, ids = api_db
    owner = make_client(db_factory, ids.owner_a)
    created = _create(owner, ids.project_a, "collection-plans", plan_payload())
    plan_id = created["plan_id"]
    assert (
        _act(owner, ids.project_a, "collection-plans", plan_id, action, version=1).status_code
        == 200
    )

    denied = owner.delete(_url(ids.project_a, "collection-plans", f"/{plan_id}"))
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "resource_immutable"


def test_cannot_delete_asset_referenced_by_task(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """被任务引用的 DRAFT 素材不能删，且错误必须可读（不是裸 integrity 冲突）。"""
    db_factory, ids = api_db
    owner = make_client(db_factory, ids.owner_a)
    plan = _create(owner, ids.project_a, "collection-plans", plan_payload())
    task = _create(
        owner,
        ids.project_a,
        "collection-tasks",
        task_payload(plan_id=plan["plan_id"]),
    )
    assert task["plan_id"] == plan["plan_id"]

    denied = owner.delete(_url(ids.project_a, "collection-plans", f"/{plan['plan_id']}"))
    assert denied.status_code == 409, denied.text
    assert denied.json()["error"]["code"] == "resource_immutable"
    # 响应体不得泄漏数据库原文（表名 / FOREIGN KEY / SQL 片段）。
    body = denied.text.lower()
    for leak in ("foreign key", "collection_tasks", "sqlite", "integrityerror"):
        assert leak not in body


def test_delete_cross_project_is_not_found(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """B 项目的素材 ID 在 A 项目删除必须 404，不能因为"ID 存在"而泄漏。"""
    db_factory, ids = api_db
    owner = make_client(db_factory, ids.owner_a)
    foreign = _create(owner, ids.project_b, "collection-plans", plan_payload("b-plan"))

    denied = owner.delete(_url(ids.project_a, "collection-plans", f"/{foreign['plan_id']}"))
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "resource_not_found"
    # 原资源不受影响。
    assert (
        owner.get(_url(ids.project_b, "collection-plans", f"/{foreign['plan_id']}")).status_code
        == 200
    )


def test_delete_writes_audit(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """删除后审计仍可追溯"删过什么"——这是选择硬删的前提。"""
    db_factory, ids = api_db
    owner = make_client(db_factory, ids.owner_a)
    created = _create(owner, ids.project_a, "collection-plans", plan_payload())
    assert (
        owner.delete(_url(ids.project_a, "collection-plans", f"/{created['plan_id']}")).status_code
        == 204
    )

    with db_factory() as session:
        record = session.scalar(
            select(AuditLog).where(
                AuditLog.project_id == ids.project_a,
                AuditLog.resource_type == "COLLECTION_PLAN",
                AuditLog.action == "delete",
            )
        )
        assert record is not None
        assert str(record.resource_id) == created["plan_id"]
        assert record.from_status == "DRAFT"
        assert record.to_status is None


# --- 权限分层 ---------------------------------------------------------------
def test_viewer_cannot_patch_or_act_or_delete(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    db_factory, ids = api_db
    client = make_client(db_factory, ids.editor_a)
    created = _create(client, ids.project_a, "collection-plans", plan_payload())
    viewer = make_client(db_factory, ids.viewer_a)
    asset_id = created["plan_id"]

    patch = viewer.patch(
        _url(ids.project_a, "collection-plans", f"/{asset_id}"),
        headers={"If-Match": '"1"'},
        json={"name": "只读用户改名"},
    )
    assert patch.status_code == 403
    assert patch.json()["error"]["code"] == "forbidden"

    acted = _act(viewer, ids.project_a, "collection-plans", asset_id, "activate", version=1)
    assert acted.status_code == 403
    assert acted.json()["error"]["code"] == "forbidden"

    deleted = viewer.delete(_url(ids.project_a, "collection-plans", f"/{asset_id}"))
    assert deleted.status_code == 403
    assert deleted.json()["error"]["code"] == "forbidden"


def test_cross_project_asset_cannot_be_activated(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    db_factory, ids = api_db
    owner = make_client(db_factory, ids.owner_a)
    foreign = _create(owner, ids.project_b, "collection-sops", sop_payload("b-sop"))

    denied = _act(owner, ids.project_a, "collection-sops", foreign["sop_id"], "activate", version=1)
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "resource_not_found"
