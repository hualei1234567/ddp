"""Episode HTTP API：创建/列表/详情/动作/修订、权限、隔离、幂等与错误码。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from app.infrastructure.db.models.ingestion import RawAssetModel
from sqlalchemy import update
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
    asset_id = seed_valid_asset(
        factory,
        ids.project_a,
        ids.editor_a,
        label="a",
        metadata={"camera": "front", "source_fps": 30.0},
    )
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
    # 1 秒区间 + 声明 30fps：就绪门禁会按事实推导帧数，微秒级区间必然被判
    # "无法确定帧数"（那是对数据的正确判断，不是测试该绕过的限制）。
    episode = _create(
        client,
        project,
        asset_id,
        code="EP-API-FLOW",
        key="episode-flow-0001",
        start_time_ns=1_000_000_000,
        end_time_ns=2_000_000_000,
    )
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

    episode = _create(
        client,
        project,
        asset_id,
        code="EP-API-REJECT",
        key="episode-reject-0001",
        start_time_ns=1_000_000_000,
        end_time_ns=2_000_000_000,
    )
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


def test_ready_is_gated_by_readiness_facts(
    episode_api: tuple[Any, ApiIds, UUID, sessionmaker[Session]],
) -> None:
    """HTTP ready 不能绕过就绪门禁：事实不足必须 409 并给出可读原因。

    与自动就绪用同一套判定（``EpisodeReadinessService.evaluate``）。此前
    ready 只校验 Manifest 结构，调用方提交一份"格式正确"的 Manifest 就能
    把缺帧率/缺标定的 Episode 推进 READY。
    """
    client, ids, asset_id, factory = episode_api
    project = ids.project_a
    episode = _create(
        client,
        project,
        asset_id,
        code="EP-API-GATED",
        key="episode-gated-0001",
        start_time_ns=1_000_000_000,
        end_time_ns=2_000_000_000,
    )
    # 把资产元数据清空，模拟"机位与帧率都没声明"的真实脏数据。
    with factory() as session:
        session.execute(
            update(RawAssetModel)
            .where(RawAssetModel.id == asset_id)
            .values(raw_metadata={})
            .execution_options(synchronize_session=False)
        )
        session.commit()

    response = client.post(
        project_url(project, f"/episodes/{episode['episode_id']}") + "/actions/ready",
        json={"manifest": manifest_for_episode(episode), "frame_count": 30},
        headers={"If-Match": '"1"', "Idempotency-Key": "episode-gated-ready-0001"},
    )
    assert response.status_code == 409, response.text
    body = response.json()["error"]
    assert body["code"] == "episode_not_ready"
    blockers = body["details"]["blockers"]
    # 机位和帧率都没声明时，流本身就不完整（STREAM_INCOMPLETE）；判定不会
    # 再对一个"没有有效流"的 Episode 补报帧率缺失——一条事实只报一次。
    assert {item["code"] for item in blockers} >= {"STREAM_INCOMPLETE"}


def test_archive_is_reachable_from_completed(
    episode_api: tuple[Any, ApiIds, UUID],
) -> None:
    """COMPLETED → ARCHIVED 必须能通过网络接口走通。

    归档此前只在领域状态机里有边，HTTP 动作映射与契约枚举都没暴露它，
    结果是"数据生命周期终点"在界面上不可达——Episode 永远停在 COMPLETED。
    """
    client, ids, asset_id, _factory = episode_api
    project = ids.project_a
    episode = _create(
        client,
        project,
        asset_id,
        code="EP-API-ARCHIVE",
        key="episode-archive-0001",
        start_time_ns=1_000_000_000,
        end_time_ns=2_000_000_000,
    )
    base = project_url(project, f"/episodes/{episode['episode_id']}")
    for action, payload, version in (
        ("ready", {"manifest": manifest_for_episode(episode), "frame_count": 30}, "1"),
        ("quality-check", None, "3"),
        ("quality-result", {"quality_status": "PASS"}, "4"),
        ("accept", None, "5"),
        ("complete", None, "6"),
    ):
        response = client.post(
            f"{base}/actions/{action}",
            json=payload,
            headers={"If-Match": f'"{version}"', "Idempotency-Key": f"ep-archive-{action}-0001"},
        )
        assert response.status_code == 200, (action, response.text)

    completed = response.json()
    assert completed["status"] == "COMPLETED"
    # 归档是公开动作：出现在 allowed_actions 里才谈得上界面上点得到。
    assert "archive" in completed["allowed_actions"]

    archived = client.post(
        f"{base}/actions/archive",
        headers={"If-Match": '"7"', "Idempotency-Key": "ep-archive-archive-0001"},
    )
    assert archived.status_code == 200, archived.text
    body = archived.json()
    assert body["status"] == "ARCHIVED"
    # ARCHIVED 是终态：不再下发任何动作按钮。
    assert body["allowed_actions"] == []


def test_create_rejects_unsupported_canonical_schema_version(
    episode_api: tuple[Any, ApiIds, UUID],
) -> None:
    """写入入口只接受正式 Canonical 版本（ADR-0038 §2.6）。

    历史行上的 ``1.0.0-draft`` 是溯源事实、读取时不拒绝；但**新写入**必须落在
    ``SUPPORTED_SCHEMA_VERSIONS`` 内，否则共享 JSON Schema 一接上就会拒绝这条数据。
    """
    client, ids, asset_id, _factory = episode_api
    response = client.post(
        project_url(ids.project_a, "/episodes"),
        json=episode_payload(
            asset_id,
            code="EP-API-BAD-VERSION",
            canonical_schema_version="ddp.canonical/1.0.0-draft",
        ),
        headers={"Idempotency-Key": "episode-bad-version-0001"},
    )

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "ddp.canonical/1.0.0-draft" in error["message"]


def test_create_accepts_every_supported_canonical_schema_version(
    episode_api: tuple[Any, ApiIds, UUID],
) -> None:
    """白名单不是"只认一个字符串"，而是"逐个接受 ``SUPPORTED_SCHEMA_VERSIONS``"。"""
    from ddp_data.canonical.v1 import SUPPORTED_SCHEMA_VERSIONS

    client, ids, asset_id, _factory = episode_api
    for index, version in enumerate(sorted(SUPPORTED_SCHEMA_VERSIONS)):
        response = client.post(
            project_url(ids.project_a, "/episodes"),
            json=episode_payload(
                asset_id,
                code=f"EP-API-VERSION-{index}",
                start_time_ns=index * 10_000,
                end_time_ns=(index + 1) * 10_000,
                canonical_schema_version=version,
            ),
            headers={"Idempotency-Key": f"episode-version-{index:04d}"},
        )
        assert response.status_code == 201, (version, response.text)
        assert response.json()["canonical_schema_version"] == version
