"""采集素材 API 集成测试（COL-002）。

覆盖四类素材的 CRUD、跨项目隔离与越权，以及**本次修复的核心**：
任务引用素材时的同项目校验（跨项目 → 404、幽灵 → 404、版本不一致 → 422）。

这些断言都用真实 SQLite + 真实仓储 + 真实路由，没有 mock。
"""

from __future__ import annotations

from uuid import UUID

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
