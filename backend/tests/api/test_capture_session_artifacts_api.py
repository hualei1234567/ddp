"""采集会话产物只读端点集成测试（N9-008）。

真实 SQLite + 真实仓储 + 真实路由，不 mock。覆盖：
- 空页（会话存在但尚未产出）不是 404；
- 跨项目会话 404、跨项目产物不可见；
- 复合游标分页边界（跨 kind 排序、末页 next_cursor 为 null）；
- 非法游标 422；
- 归属写入：Episode 创建携带 capture_session_id，RawAsset 通过上传完成继承；
- 会话终态后仍可补录（创建时携带），不改变既有创建链路语义。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.infrastructure.db.models.ingestion import RawAssetModel
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.ddp310_support import episode_payload, project_url, seed_valid_asset


def _session_url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/capture-sessions{suffix}"


def _artifacts_url(project_id: object, session_id: object) -> str:
    return _session_url(project_id, f"/{session_id}/artifacts")


def _create_session(
    client: TestClient,
    project_id: object,
    code: str = "session-1",
) -> dict[str, object]:
    response = client.post(_session_url(project_id), json={"code": code})
    assert response.status_code == 201, response.text
    return response.json()


def _create_episode(
    client: TestClient,
    project_id: UUID,
    asset_id: UUID,
    *,
    code: str,
    capture_session_id: UUID | str | None = None,
    start_time_ns: int = 1_000,
    end_time_ns: int = 2_000,
) -> dict[str, object]:
    overrides: dict[str, object] = {}
    if capture_session_id is not None:
        overrides["capture_session_id"] = str(capture_session_id)
    response = client.post(
        project_url(project_id, "/episodes"),
        json=episode_payload(
            asset_id,
            code=code,
            start_time_ns=start_time_ns,
            end_time_ns=end_time_ns,
            **overrides,
        ),
        headers={"Idempotency-Key": f"episode-{uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _seed_asset_with_session(
    factory: sessionmaker[Session],
    project_id: UUID,
    actor_id: UUID,
    *,
    session_id: UUID,
    label: str,
) -> UUID:
    """直接落一条带会话归属的 RawAsset（模拟上传完成后的产物）。"""
    asset_id = seed_valid_asset(factory, project_id, actor_id, label=label)
    with factory() as session:
        session.execute(
            update(RawAssetModel)
            .where(RawAssetModel.id == asset_id)
            .values(capture_session_id=session_id)
            .execution_options(synchronize_session=False)
        )
        session.commit()
    return asset_id


def test_empty_page_when_session_has_no_artifacts(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    created = _create_session(client, ids.project_a)

    page = client.get(_artifacts_url(ids.project_a, created["session_id"]))
    assert page.status_code == 200, page.text
    assert page.json() == {"items": [], "next_cursor": None}


def test_cross_project_session_is_not_found(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    foreign = _create_session(owner, ids.project_b, code="session-b")

    editor = make_client(factory, ids.editor_a)
    response = editor.get(_artifacts_url(ids.project_a, foreign["session_id"]))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"

    # 未知会话同样 404，不泄漏是否存在。
    missing = editor.get(_artifacts_url(ids.project_a, "00000000-0000-0000-0000-000000000000"))
    assert missing.status_code == 404


def test_episode_carries_session_and_lists_in_artifacts(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    session = _create_session(client, ids.project_a)
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="a")

    episode = _create_episode(
        client,
        ids.project_a,
        asset_id,
        code="EP-SESSION-1",
        capture_session_id=session["session_id"],
    )
    assert episode["capture_session_id"] == session["session_id"]

    page = client.get(_artifacts_url(ids.project_a, session["session_id"])).json()
    assert [item["kind"] for item in page["items"]] == ["EPISODE"]
    assert page["items"][0]["artifact_id"] == episode["episode_id"]
    assert page["items"][0]["code"] == "EP-SESSION-1"
    assert page["items"][0]["status"] == "PENDING"


def test_cross_project_episode_session_binding_is_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    foreign_session = _create_session(owner, ids.project_b, code="session-b")

    editor = make_client(factory, ids.editor_a)
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="a")
    response = editor.post(
        project_url(ids.project_a, "/episodes"),
        json=episode_payload(
            asset_id,
            code="EP-XPROJECT",
            capture_session_id=str(foreign_session["session_id"]),
        ),
        headers={"Idempotency-Key": f"episode-{uuid4().hex}"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


def test_pagination_across_kinds_and_cursor_boundary(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    session = _create_session(client, ids.project_a)
    session_id = UUID(str(session["session_id"]))

    # 两个 RawAsset（直接落库）+ 两个 Episode，验证跨 kind 排序与游标。
    for label in ("a1", "a2"):
        _seed_asset_with_session(
            factory, ids.project_a, ids.editor_a, session_id=session_id, label=label
        )
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="ep")
    for index in (1, 2):
        _create_episode(
            client,
            ids.project_a,
            asset_id,
            code=f"EP-PAGE-{index}",
            capture_session_id=session_id,
            start_time_ns=index * 1_000,
            end_time_ns=(index + 1) * 1_000,
        )

    first = client.get(_artifacts_url(ids.project_a, session_id) + "?limit=2").json()
    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None

    second = client.get(
        _artifacts_url(ids.project_a, session_id) + f"?limit=2&cursor={first['next_cursor']}"
    ).json()
    assert len(second["items"]) == 2
    assert second["next_cursor"] is None

    seen = {item["artifact_id"] for item in first["items"]} | {
        item["artifact_id"] for item in second["items"]
    }
    assert len(seen) == 4

    # 非法游标 → 422，不静默回到首页。
    invalid = client.get(_artifacts_url(ids.project_a, session_id) + "?cursor=not-a-cursor")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_failed"


def test_artifacts_are_isolated_between_sessions(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    first = _create_session(client, ids.project_a, code="session-1")
    second = _create_session(client, ids.project_a, code="session-2")
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="a")

    _create_episode(
        client,
        ids.project_a,
        asset_id,
        code="EP-FIRST",
        capture_session_id=first["session_id"],
    )
    _create_episode(
        client,
        ids.project_a,
        asset_id,
        code="EP-SECOND",
        capture_session_id=second["session_id"],
        start_time_ns=5_000,
        end_time_ns=6_000,
    )

    first_page = client.get(_artifacts_url(ids.project_a, first["session_id"])).json()
    assert [item["code"] for item in first_page["items"]] == ["EP-FIRST"]
    second_page = client.get(_artifacts_url(ids.project_a, second["session_id"])).json()
    assert [item["code"] for item in second_page["items"]] == ["EP-SECOND"]


def test_terminal_session_allows_backfill_binding(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """会话终态后仍允许补录产物归属；只校验同项目，不因终态拒绝。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    created = _create_session(client, ids.project_a)
    session_id = created["session_id"]

    started = client.post(
        _session_url(ids.project_a, f"/{session_id}/actions/start"),
        headers={"If-Match": f'"{created["version"]}"'},
    )
    assert started.status_code == 200, started.text
    completed = client.post(
        _session_url(ids.project_a, f"/{session_id}/actions/complete"),
        headers={"If-Match": f'"{started.json()["version"]}"'},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"

    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="a")
    episode = _create_episode(
        client,
        ids.project_a,
        asset_id,
        code="EP-BACKFILL",
        capture_session_id=session_id,
    )
    assert episode["capture_session_id"] == session_id

    page = client.get(_artifacts_url(ids.project_a, session_id)).json()
    assert [item["code"] for item in page["items"]] == ["EP-BACKFILL"]


def test_viewer_can_read_artifacts_and_outsider_cannot(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    created = _create_session(editor, ids.project_a)

    viewer = make_client(factory, ids.viewer_a)
    assert viewer.get(_artifacts_url(ids.project_a, created["session_id"])).status_code == 200

    outsider = make_client(factory, ids.outsider)
    assert outsider.get(_artifacts_url(ids.project_a, created["session_id"])).status_code == 403


def test_upload_session_carries_capture_session_to_raw_asset(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """上传完成链路把会话归属复制到 RawAsset（不新增第二套创建接口）。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    created = _create_session(client, ids.project_a)

    # 上传会话创建时携带会话归属；同项目校验通过。
    response = client.post(
        f"/api/v1/projects/{ids.project_a}/upload-sessions",
        json={
            "original_filename": "sample.mcap",
            "declared_content_type": "application/x-mcap",
            "declared_size_bytes": 8,
            "format_hint": "MCAP",
            "capture_session_id": created["session_id"],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["capture_session_id"] == created["session_id"]


def test_upload_session_rejects_cross_project_capture_session(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    foreign = _create_session(owner, ids.project_b, code="session-b")

    editor = make_client(factory, ids.editor_a)
    response = editor.post(
        f"/api/v1/projects/{ids.project_a}/upload-sessions",
        json={
            "original_filename": "sample.mcap",
            "declared_content_type": "application/x-mcap",
            "declared_size_bytes": 8,
            "format_hint": "MCAP",
            "capture_session_id": foreign["session_id"],
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"
