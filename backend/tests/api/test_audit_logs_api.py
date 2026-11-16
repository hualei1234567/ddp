"""审计日志查询 API 集成测试。

覆盖：owner 可读、editor/viewer 403、跨项目隔离（含游标与资源时间线不泄露
存在性）、过滤条件组合、时间区间语义、cursor 分页、只读约束。

全部用真实 SQLite + 真实仓储 + 真实路由与依赖（含真实身份仓储），没有 mock。
种子直接写 ``audit_logs`` 表：本任务不改写入路径，读侧的契约就是"表里有什么
就读到什么"。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.identity.models import PlatformRole
from app.infrastructure.db.models.collection import AuditLog
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


def _url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/audit-logs{suffix}"


def seed_audit(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    actor_id: UUID,
    resource_type: str = "INGESTION_JOB",
    resource_id: UUID | None = None,
    action: str = "create",
    result: str = "SUCCESS",
    occurred_at: datetime = NOW,
    actor_role: str | None = "editor",
    reason: str | None = None,
    request_id: str | None = "req-1",
    from_status: str | None = None,
    to_status: str | None = None,
    details: dict[str, object] | None = None,
) -> UUID:
    audit_id = uuid4()
    with factory() as session:
        session.add(
            AuditLog(
                id=audit_id,
                project_id=project_id,
                actor_user_id=actor_id,
                actor_role=actor_role,
                resource_type=resource_type,
                resource_id=resource_id or uuid4(),
                action=action,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
                request_id=request_id,
                result=result,
                correlation_id=None,
                details=details if details is not None else {"seed": True},
                occurred_at=occurred_at,
            )
        )
        session.commit()
    return audit_id


def _audit_ids(response: object) -> list[str]:
    return [item["audit_id"] for item in response.json()["items"]]  # type: ignore[attr-defined]


# --- 读权限 ----------------------------------------------------------------
def test_owner_can_list_audit_logs_newest_first(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    resource_id = uuid4()
    oldest = seed_audit(
        factory,
        project_id=ids.project_a,
        actor_id=ids.editor_a,
        resource_type="INGESTION_JOB",
        resource_id=resource_id,
        action="create",
        occurred_at=NOW,
        reason="首次接入",
        to_status="UPLOADED",
    )
    middle = seed_audit(
        factory,
        project_id=ids.project_a,
        actor_id=ids.owner_a,
        resource_type="INGESTION_JOB",
        resource_id=resource_id,
        action="validate",
        occurred_at=NOW + timedelta(minutes=5),
        result="FAILURE",
        reason="校验失败",
    )
    newest = seed_audit(
        factory,
        project_id=ids.project_a,
        actor_id=ids.owner_a,
        resource_type="RAW_ASSET",
        resource_id=uuid4(),
        action="download",
        occurred_at=NOW + timedelta(minutes=10),
    )
    client = make_client(factory, ids.owner_a)

    response = client.get(_url(ids.project_a))

    assert response.status_code == 200, response.text
    assert _audit_ids(response) == [str(newest), str(middle), str(oldest)]
    first = response.json()["items"][0]
    assert first["project_id"] == str(ids.project_a)
    assert first["actor_user_id"] == str(ids.owner_a)
    assert first["actor_role"] == "editor"
    assert first["resource_type"] == "RAW_ASSET"
    assert first["action"] == "download"
    assert first["result"] == "SUCCESS"
    assert first["details"] == {"seed": True}
    assert first["occurred_at"].startswith("2026-11-17T10:10:00")


def test_editor_and_viewer_cannot_read_audit_logs(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    seed_audit(factory, project_id=ids.project_a, actor_id=ids.owner_a)

    for user_id in (ids.editor_a, ids.viewer_a):
        response = make_client(factory, user_id).get(_url(ids.project_a))
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "forbidden"


def test_platform_admin_can_read_audit_logs_of_any_existing_project(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    seeded = seed_audit(factory, project_id=ids.project_a, actor_id=ids.owner_a)
    client = make_client(factory, ids.outsider, platform_role=PlatformRole.ADMIN)

    response = client.get(_url(ids.project_a))

    assert response.status_code == 200, response.text
    assert _audit_ids(response) == [str(seeded)]


# --- 项目隔离 --------------------------------------------------------------
def test_other_project_records_never_appear(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    mine = seed_audit(factory, project_id=ids.project_a, actor_id=ids.owner_a)
    theirs = seed_audit(factory, project_id=ids.project_b, actor_id=ids.owner_a)
    client = make_client(factory, ids.owner_a)

    assert _audit_ids(client.get(_url(ids.project_a))) == [str(mine)]
    assert _audit_ids(client.get(_url(ids.project_b))) == [str(theirs)]


def test_resource_timeline_and_cross_project_hidden(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    shared_resource = uuid4()
    first = seed_audit(
        factory,
        project_id=ids.project_a,
        actor_id=ids.owner_a,
        resource_type="COLLECTION_TASK",
        resource_id=shared_resource,
        action="create",
        occurred_at=NOW,
    )
    second = seed_audit(
        factory,
        project_id=ids.project_a,
        actor_id=ids.owner_a,
        resource_type="COLLECTION_TASK",
        resource_id=shared_resource,
        action="start",
        occurred_at=NOW + timedelta(minutes=1),
    )
    # 同类型的其它资源，不应混入时间线。
    seed_audit(
        factory,
        project_id=ids.project_a,
        actor_id=ids.owner_a,
        resource_type="COLLECTION_TASK",
        resource_id=uuid4(),
        action="create",
        occurred_at=NOW + timedelta(minutes=2),
    )
    # 其它项目里恰好复用了同一个 resource_id（UUID 碰撞之外的场景：
    # 资源 ID 由各项目各自生成，这里刻意构造以验证隔离边界）。
    foreign = seed_audit(
        factory,
        project_id=ids.project_b,
        actor_id=ids.owner_a,
        resource_type="COLLECTION_TASK",
        resource_id=shared_resource,
        action="delete",
        occurred_at=NOW + timedelta(minutes=3),
    )
    client = make_client(factory, ids.owner_a)

    response = client.get(_url(ids.project_a, f"/COLLECTION_TASK/{shared_resource}"))

    assert response.status_code == 200, response.text
    assert _audit_ids(response) == [str(second), str(first)]

    # 用别家项目独有资源的 ID 查询：200 + 空列表，与"本项目无记录"不可区分。
    foreign_only = uuid4()
    seed_audit(
        factory,
        project_id=ids.project_b,
        actor_id=ids.owner_a,
        resource_type="COLLECTION_TASK",
        resource_id=foreign_only,
        action="create",
    )
    hidden = client.get(_url(ids.project_a, f"/COLLECTION_TASK/{foreign_only}"))
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["items"] == []
    assert str(foreign) not in hidden.text


# --- 过滤条件 --------------------------------------------------------------
def test_filters_are_combined(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    resource_id = uuid4()
    target = seed_audit(
        factory,
        project_id=ids.project_a,
        actor_id=ids.owner_a,
        resource_type="DATASET",
        resource_id=resource_id,
        action="submit_review",
        result="FAILURE",
        occurred_at=NOW + timedelta(hours=2),
        actor_role="owner",
    )
    seed_audit(
        factory,
        project_id=ids.project_a,
        actor_id=ids.owner_a,
        resource_type="DATASET",
        resource_id=resource_id,
        action="submit_review",
        result="SUCCESS",
        occurred_at=NOW + timedelta(hours=2),
    )
    seed_audit(
        factory,
        project_id=ids.project_a,
        actor_id=ids.editor_a,
        resource_type="DATASET",
        resource_id=resource_id,
        action="submit_review",
        result="FAILURE",
        occurred_at=NOW + timedelta(hours=2),
    )
    seed_audit(
        factory,
        project_id=ids.project_a,
        actor_id=ids.owner_a,
        resource_type="DATASET",
        resource_id=resource_id,
        action="freeze",
        result="FAILURE",
        occurred_at=NOW + timedelta(hours=2),
    )
    # 区间外（区间为闭区间，用超出 1 秒的记录验证）。
    seed_audit(
        factory,
        project_id=ids.project_a,
        actor_id=ids.owner_a,
        resource_type="DATASET",
        resource_id=resource_id,
        action="submit_review",
        result="FAILURE",
        occurred_at=NOW + timedelta(hours=5),
    )
    client = make_client(factory, ids.owner_a)

    response = client.get(
        _url(ids.project_a),
        params={
            "resource_type": "DATASET",
            "resource_id": str(resource_id),
            "action": "submit_review",
            "actor_user_id": str(ids.owner_a),
            "result": "FAILURE",
            "occurred_from": (NOW + timedelta(hours=1)).isoformat(),
            "occurred_to": (NOW + timedelta(hours=3)).isoformat(),
        },
    )

    assert response.status_code == 200, response.text
    assert _audit_ids(response) == [str(target)]


def test_time_filters_require_timezone_and_ordered_range(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.owner_a)

    naive = client.get(_url(ids.project_a), params={"occurred_from": "2026-11-17T10:00:00"})
    assert naive.status_code == 422, naive.text
    assert "时区" in naive.json()["error"]["message"]

    reversed_range = client.get(
        _url(ids.project_a),
        params={
            "occurred_from": "2026-11-18T00:00:00Z",
            "occurred_to": "2026-11-17T00:00:00Z",
        },
    )
    assert reversed_range.status_code == 422, reversed_range.text


def test_invalid_result_and_limit_are_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.owner_a)

    assert client.get(_url(ids.project_a), params={"result": "UNKNOWN"}).status_code == 422
    assert client.get(_url(ids.project_a), params={"limit": 0}).status_code == 422
    assert client.get(_url(ids.project_a), params={"limit": 101}).status_code == 422


# --- 分页 ------------------------------------------------------------------
def test_cursor_pagination_has_no_gaps_or_duplicates(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    expected: list[str] = []
    for index in range(5):
        expected.append(
            str(
                seed_audit(
                    factory,
                    project_id=ids.project_a,
                    actor_id=ids.owner_a,
                    action=f"step_{index}",
                    occurred_at=NOW + timedelta(minutes=index),
                )
            )
        )
    expected.reverse()
    client = make_client(factory, ids.owner_a)

    collected: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, object] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get(_url(ids.project_a), params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        collected.extend(item["audit_id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert cursor is None
    assert collected == expected


def test_cursor_from_another_project_yields_empty_page(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    seed_audit(factory, project_id=ids.project_a, actor_id=ids.owner_a)
    foreign = seed_audit(factory, project_id=ids.project_b, actor_id=ids.owner_a)
    client = make_client(factory, ids.owner_a)

    response = client.get(
        _url(ids.project_a),
        params={"cursor": _encode_cursor(foreign)},
    )

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


def test_invalid_cursor_is_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.owner_a)

    response = client.get(_url(ids.project_a), params={"cursor": "not-a-cursor"})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


# --- 只读 ------------------------------------------------------------------
def test_audit_logs_have_no_write_endpoints(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    audit_id = seed_audit(factory, project_id=ids.project_a, actor_id=ids.owner_a)
    client: TestClient = make_client(factory, ids.owner_a)

    assert client.post(_url(ids.project_a), json={}).status_code == 405
    assert client.delete(_url(ids.project_a)).status_code == 405
    assert client.delete(_url(ids.project_a, f"/INGESTION_JOB/{audit_id}")).status_code == 405


def _encode_cursor(value: UUID) -> str:
    """与 ``app.api.v1.dataset_pagination.encode_cursor`` 同语义（服务端游标）。"""
    import base64

    return base64.urlsafe_b64encode(value.bytes).decode("ascii").rstrip("=")
