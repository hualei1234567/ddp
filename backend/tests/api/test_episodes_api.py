"""Episode HTTP API：创建/列表/详情/动作/修订、权限、隔离、幂等与错误码。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.ddp310_support import (
    episode_payload,
    manifest_for_episode,
    project_url,
    seed_valid_asset,
)
from tests.fakes_s3 import FakeMultipartStorage


@pytest.fixture()
def episode_api(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> tuple[Any, ApiIds, UUID, sessionmaker[Session]]:
    factory, ids = api_db
    client = make_client(
        factory,
        ids.editor_a,
        object_storage=FakeMultipartStorage(),
    )
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="a")
    return client, ids, asset_id, factory


def _create(
    client: Any,
    project_id: UUID,
    asset_id: UUID,
    *,
    code: str,
    key: str,
    start_time_ns: int = 1_000,
    end_time_ns: int = 2_000,
) -> dict[str, object]:
    response = client.post(
        project_url(project_id, "/episodes"),
        json=episode_payload(
            asset_id,
            code=code,
            start_time_ns=start_time_ns,
            end_time_ns=end_time_ns,
        ),
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_list_detail_idempotent_and_pagination(
    episode_api: tuple[Any, ApiIds, UUID],
) -> None:
    client, ids, asset_id, factory = episode_api
    project = ids.project_a
    body = episode_payload(asset_id, code="EP-API-001")
    headers = {"Idempotency-Key": "episode-create-0001"}

    created = client.post(project_url(project, "/episodes"), json=body, headers=headers)
    assert created.status_code == 201, created.text
    assert created.headers["ETag"] == '"1"'
    assert created.headers["Idempotent-Replayed"] == "false"
    episode = created.json()
    assert episode["status"] == "PENDING"
    assert episode["episode_version"] == 1
    assert episode["project_id"] == str(project)

    replay = client.post(project_url(project, "/episodes"), json=body, headers=headers)
    assert replay.status_code == 201
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["episode_id"] == episode["episode_id"]

    conflict = client.post(
        project_url(project, "/episodes"),
        json=episode_payload(asset_id, code="EP-API-OTHER"),
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reused"

    detail = client.get(project_url(project, f"/episodes/{episode['episode_id']}"))
    assert detail.status_code == 200
    assert detail.headers["ETag"] == '"1"'
    assert detail.json()["code"] == "EP-API-001"

    for index in range(2, 6):
        _create(
            client,
            project,
            asset_id,
            code=f"EP-API-00{index}",
            key=f"episode-create-000{index}",
            start_time_ns=index * 1_000,
            end_time_ns=(index + 1) * 1_000,
        )

    first = client.get(project_url(project, "/episodes?limit=2")).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    second = client.get(
        project_url(project, f"/episodes?limit=2&cursor={first['next_cursor']}")
    ).json()
    assert len(second["items"]) == 2
    third = client.get(
        project_url(project, f"/episodes?limit=2&cursor={second['next_cursor']}")
    ).json()
    assert len(third["items"]) == 1
    assert third["next_cursor"] is None

    ids_seen = {item["episode_id"] for item in first["items"] + second["items"] + third["items"]}
    assert len(ids_seen) == 5

    bad_cursor = client.get(project_url(project, "/episodes?cursor=not-base64"))
    assert bad_cursor.status_code == 422
    assert bad_cursor.json()["error"]["code"] == "validation_failed"

    bad_status = client.get(project_url(project, "/episodes?status=UNKNOWN"))
    assert bad_status.status_code == 422
    assert bad_status.json()["error"]["code"] == "validation_failed"


def test_permissions_preconditions_and_cross_project_isolation(
    episode_api: tuple[Any, ApiIds, UUID],
) -> None:
    client, ids, asset_id, factory = episode_api
    project_a = ids.project_a
    episode = _create(client, project_a, asset_id, code="EP-API-PERM", key="episode-perm-0001")

    missing_match = client.post(
        project_url(project_a, f"/episodes/{episode['episode_id']}/actions/quality-check"),
        headers={"Idempotency-Key": "episode-perm-action-0001"},
    )
    assert missing_match.status_code == 428
    assert missing_match.json()["error"]["code"] == "precondition_required"

    missing_key = client.post(
        project_url(project_a, f"/episodes/{episode['episode_id']}/actions/quality-check"),
        headers={"If-Match": '"1"'},
    )
    assert missing_key.status_code == 428
    assert missing_key.json()["error"]["code"] == "precondition_required"

    stale = client.post(
        project_url(project_a, f"/episodes/{episode['episode_id']}/actions/accept"),
        headers={"If-Match": '"99"', "Idempotency-Key": "episode-perm-action-0002"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"

    invalid_transition = client.post(
        project_url(project_a, f"/episodes/{episode['episode_id']}/actions/accept"),
        headers={"If-Match": '"1"', "Idempotency-Key": "episode-perm-action-0003"},
    )
    assert invalid_transition.status_code == 409
    assert invalid_transition.json()["error"]["code"] == "state_transition_invalid"

    unknown_action = client.post(
        project_url(project_a, f"/episodes/{episode['episode_id']}/actions/system-fail"),
        headers={"If-Match": '"1"', "Idempotency-Key": "episode-perm-action-0004"},
    )
    assert unknown_action.status_code == 422

    extra_field = client.post(
        project_url(project_a, "/episodes"),
        json={
            **episode_payload(asset_id, code="EP-API-EXTRA"),
            "project_id": str(project_a),
        },
        headers={"Idempotency-Key": "episode-perm-extra-0001"},
    )
    assert extra_field.status_code == 422

    viewer = make_client(factory, ids.viewer_a, object_storage=FakeMultipartStorage())
    viewer_create = viewer.post(
        project_url(project_a, "/episodes"),
        json=episode_payload(asset_id, code="EP-API-VIEWER"),
        headers={"Idempotency-Key": "episode-viewer-0001"},
    )
    assert viewer_create.status_code == 403
    assert viewer_create.json()["error"]["code"] == "forbidden"

    outsider = make_client(factory, ids.outsider, object_storage=FakeMultipartStorage())
    outsider_read = outsider.get(project_url(project_a, "/episodes"))
    assert outsider_read.status_code == 403

    owner_b = make_client(factory, ids.owner_a, object_storage=FakeMultipartStorage())
    cross_project = owner_b.get(project_url(ids.project_b, f"/episodes/{episode['episode_id']}"))
    assert cross_project.status_code == 404
    assert cross_project.json()["error"]["code"] == "resource_not_found"
    cross_list = owner_b.get(project_url(ids.project_b, "/episodes"))
    assert cross_list.status_code == 200
    assert cross_list.json()["items"] == []


def test_actions_ready_quality_accept_complete_and_revise(
    episode_api: tuple[Any, ApiIds, UUID],
) -> None:
    client, ids, asset_id, factory = episode_api
    project = ids.project_a
    episode = _create(client, project, asset_id, code="EP-API-FLOW", key="episode-flow-0001")
    episode_id = episode["episode_id"]
    base = project_url(project, f"/episodes/{episode_id}")

    ready = client.post(
        f"{base}/actions/ready",
        json={
            "manifest": manifest_for_episode(episode),
            "frame_count": 30,
        },
        headers={"If-Match": '"1"', "Idempotency-Key": "episode-flow-ready-0001"},
    )
    assert ready.status_code == 200, ready.text
    ready_body = ready.json()
    assert ready_body["status"] == "READY"
    assert ready_body["manifest_key"].endswith("/1/manifest.json")
    assert ready_body["manifest_sha256"].startswith("sha256:")
    assert ready.headers["ETag"] == '"3"'

    ready_replay = client.post(
        f"{base}/actions/ready",
        json={
            "manifest": manifest_for_episode(episode),
            "frame_count": 30,
        },
        headers={"If-Match": '"1"', "Idempotency-Key": "episode-flow-ready-0001"},
    )
    assert ready_replay.status_code == 200
    assert ready_replay.headers["Idempotent-Replayed"] == "true"
    assert ready_replay.json()["episode_id"] == episode_id

    checking = client.post(
        f"{base}/actions/quality-check",
        headers={"If-Match": '"3"', "Idempotency-Key": "episode-flow-qc-0001"},
    )
    assert checking.status_code == 200
    assert checking.json()["status"] == "QUALITY_CHECKING"

    quality = client.post(
        f"{base}/actions/quality-result",
        json={"quality_status": "PASS"},
        headers={"If-Match": '"4"', "Idempotency-Key": "episode-flow-qr-0001"},
    )
    assert quality.status_code == 200
    assert quality.json()["quality_status"] == "PASS"

    accepted = client.post(
        f"{base}/actions/accept",
        headers={"If-Match": '"5"', "Idempotency-Key": "episode-flow-accept-0001"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "ACCEPTED"

    completed = client.post(
        f"{base}/actions/complete",
        headers={"If-Match": '"6"', "Idempotency-Key": "episode-flow-complete-0001"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"

    revision_body = {"code": "EP-API-FLOW-V2"}
    revision_headers = {
        "If-Match": '"7"',
        "Idempotency-Key": "episode-flow-revise-0001",
    }
    revised = client.post(f"{base}/revisions", json=revision_body, headers=revision_headers)
    assert revised.status_code == 201, revised.text
    revised_body = revised.json()
    assert revised_body["episode_id"] != episode_id
    assert revised_body["episode_version"] == 2
    assert revised_body["supersedes_id"] == episode_id
    assert revised_body["status"] == "PENDING"

    revision_replay = client.post(f"{base}/revisions", json=revision_body, headers=revision_headers)
    assert revision_replay.status_code == 201
    assert revision_replay.headers["Idempotent-Replayed"] == "true"
    assert revision_replay.json()["episode_id"] == revised_body["episode_id"]


def test_reject_and_invalidate_require_reason(
    episode_api: tuple[Any, ApiIds, UUID],
) -> None:
    client, ids, asset_id, factory = episode_api
    project = ids.project_a

    episode = _create(client, project, asset_id, code="EP-API-REJECT", key="episode-reject-0001")
    base = project_url(project, f"/episodes/{episode['episode_id']}")
    ready = client.post(
        f"{base}/actions/ready",
        json={"manifest": manifest_for_episode(episode), "frame_count": 1},
        headers={"If-Match": '"1"', "Idempotency-Key": "episode-reject-ready-0001"},
    )
    assert ready.status_code == 200
    checking = client.post(
        f"{base}/actions/quality-check",
        headers={"If-Match": '"3"', "Idempotency-Key": "episode-reject-qc-0001"},
    )
    assert checking.status_code == 200

    no_reason = client.post(
        f"{base}/actions/reject",
        json={},
        headers={"If-Match": '"4"', "Idempotency-Key": "episode-reject-and-0001"},
    )
    assert no_reason.status_code == 422
    assert no_reason.json()["error"]["code"] == "validation_failed"

    rejected = client.post(
        f"{base}/actions/reject",
        json={"reason": "关键帧缺失"},
        headers={"If-Match": '"4"', "Idempotency-Key": "episode-reject-and-0002"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"

    other = _create(
        client,
        project,
        asset_id,
        code="EP-API-INVALIDATE",
        key="episode-invalidate-0001",
        start_time_ns=10_000,
        end_time_ns=11_000,
    )
    invalidate = client.post(
        project_url(project, f"/episodes/{other['episode_id']}/actions/invalidate"),
        json={"reason": "来源资产被隔离"},
        headers={"If-Match": '"1"', "Idempotency-Key": "episode-invalidate-act-0001"},
    )
    assert invalidate.status_code == 200
    assert invalidate.json()["status"] == "INVALIDATED"


def test_episode_exposes_allowed_actions(
    episode_api: tuple[Any, ApiIds, UUID, sessionmaker[Session]],
) -> None:
    """Episode 响应带 ``allowed_actions``，且只含对外暴露的动作。

    `system-*` / `retry` / `archive` 不在公开集合里，必须被过滤掉——
    前端拿到它们会渲染出按了必然 422 的按钮。
    """
    client, ids, asset_id, _factory = episode_api
    created = _create(client, ids.project_a, asset_id, code="ep-actions", key="ep-actions-key")

    actions = created["allowed_actions"]
    # 新建 Episode 处于 PENDING，公开动作只有「作废」；关键是不为空、
    # 且只含对外暴露的动作。
    assert actions == ["invalidate"], (created["status"], actions)
    assert set(actions) <= {
        "ready",
        "quality-check",
        "quality-result",
        "accept",
        "reject",
        "complete",
        "invalidate",
    }
