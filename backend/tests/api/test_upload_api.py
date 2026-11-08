"""上传会话/分片/RawAsset API：幂等、断点、校验、隔离与失败重试。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from app.core.config import Settings
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from app.infrastructure.db.models.ingestion import (
    RawAssetLinkModel,
    RawAssetModel,
    UploadPartModel,
    UploadSessionModel,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.fakes_s3 import FakeMultipartStorage, PartMaterial

MCAP_BYTES = b"\x89MCAP0\r\n"
JSON_BYTES = b'{"a":1}'
TEXT_BYTES = b"not-a-mp4"


@dataclass(frozen=True)
class UploadHarness:
    client: TestClient
    fake: FakeMultipartStorage
    factory: sessionmaker[Session]
    ids: ApiIds
    settings: Settings


@pytest.fixture()
def upload_api(api_db: tuple[sessionmaker[Session], ApiIds]) -> UploadHarness:
    factory, ids = api_db
    fake = FakeMultipartStorage()
    settings = Settings(
        env="local",
        upload_part_size_bytes=4,
        upload_max_size_bytes=64,
        upload_session_ttl_hours=1,
        upload_url_ttl_seconds=60,
    )
    client = make_client(
        factory,
        ids.editor_a,
        object_storage=fake,
        settings_override=settings,
    )
    return UploadHarness(client=client, fake=fake, factory=factory, ids=ids, settings=settings)


def _sessions_url(project_id: UUID, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/upload-sessions{suffix}"


def _assets_url(project_id: UUID, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/raw-assets{suffix}"


def _payload(
    *,
    size: int = len(MCAP_BYTES),
    content_type: str = "application/x-mcap",
    format_hint: str = "MCAP",
    filename: str = "sample.mcap",
    expected_sha: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "original_filename": filename,
        "declared_content_type": content_type,
        "declared_size_bytes": size,
        "format_hint": format_hint,
        "expected_sha256": expected_sha,
    }
    payload.update(overrides)
    return payload


def _create(
    client: TestClient,
    project_id: UUID,
    *,
    key: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    headers = {"Idempotency-Key": key} if key else {}
    response = client.post(
        _sessions_url(project_id),
        json=_payload(**overrides),
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _get(client: TestClient, project_id: UUID, session_id: str) -> dict[str, Any]:
    response = client.get(_sessions_url(project_id, f"/{session_id}"))
    assert response.status_code == 200, response.text
    return response.json()


def _upload_part(
    harness: UploadHarness,
    project_id: UUID,
    session: dict[str, Any],
    part_number: int,
    data: bytes,
) -> PartMaterial:
    url_response = harness.client.post(
        _sessions_url(
            project_id,
            f"/{session['upload_session_id']}/parts/{part_number}/url",
        )
    )
    assert url_response.status_code == 200, url_response.text
    assert url_response.json()["method"] == "PUT"
    assert url_response.json()["headers"]["Content-Length"] == str(len(data))
    material = harness.fake.put_part(session["object_key"], part_number, data)
    ack = harness.client.post(
        _sessions_url(
            project_id,
            f"/{session['upload_session_id']}/parts/{part_number}/ack",
        ),
        json={
            "etag": material.etag,
            "size_bytes": material.size_bytes,
            "sha256": material.sha256,
        },
    )
    assert ack.status_code == 200, ack.text
    return material


def _upload_all(
    harness: UploadHarness,
    project_id: UUID,
    session: dict[str, Any],
    content: bytes,
) -> list[PartMaterial]:
    part_size = harness.settings.upload_part_size_bytes
    materials: list[PartMaterial] = []
    for index, start in enumerate(range(0, len(content), part_size), start=1):
        materials.append(
            _upload_part(harness, project_id, session, index, content[start : start + part_size])
        )
    return materials


def _complete_body(
    content: bytes,
    materials: list[PartMaterial],
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "expected_sha256": f"sha256:{hashlib.sha256(content).hexdigest()}",
        "expected_size_bytes": len(content),
        "parts": [
            {
                "part_number": item.part_number,
                "etag": item.etag,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            }
            for item in materials
        ],
    }
    body.update(overrides)
    return body


def _complete(
    harness: UploadHarness,
    project_id: UUID,
    session: dict[str, Any],
    body: dict[str, Any],
    *,
    key: str | None = None,
) -> Any:
    headers = {"If-Match": f'"{session["version"]}"'}
    if key:
        headers["Idempotency-Key"] = key
    return harness.client.post(
        _sessions_url(project_id, f"/{session['upload_session_id']}/complete"),
        json=body,
        headers=headers,
    )


def test_create_session_idempotent_and_rejects_key_reuse(upload_api: UploadHarness) -> None:
    harness = upload_api
    project = harness.ids.project_a
    payload = _payload()

    first = harness.client.post(
        _sessions_url(project),
        json=payload,
        headers={"Idempotency-Key": "upload-key-0001"},
    )
    second = harness.client.post(
        _sessions_url(project),
        json=payload,
        headers={"Idempotency-Key": "upload-key-0001"},
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["upload_session_id"] == second.json()["upload_session_id"]
    assert second.headers["Idempotent-Replayed"] == "true"
    assert first.json()["object_key"].endswith("content.mcap")
    assert str(project) in first.json()["object_key"]
    assert "sample.mcap" not in first.json()["object_key"]

    conflict = harness.client.post(
        _sessions_url(project),
        json=_payload(size=4),
        headers={"Idempotency-Key": "upload-key-0001"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reused"

    with harness.factory() as session:
        count = session.scalar(select(func.count()).select_from(UploadSessionModel))
    assert count == 1


def test_part_url_and_ack_are_idempotent_but_conflict_on_change(
    upload_api: UploadHarness,
) -> None:
    harness = upload_api
    project = harness.ids.project_a
    session = _create(harness.client, project)
    material = _upload_part(harness, project, session, 1, MCAP_BYTES[:4])

    same = harness.client.post(
        _sessions_url(project, f"/{session['upload_session_id']}/parts/1/ack"),
        json={
            "etag": material.etag,
            "size_bytes": material.size_bytes,
            "sha256": material.sha256,
        },
    )
    assert same.status_code == 200
    assert same.json()["uploaded_part_count"] == 1

    other_sha = f"sha256:{hashlib.sha256(b'xxxx').hexdigest()}"
    conflict = harness.client.post(
        _sessions_url(project, f"/{session['upload_session_id']}/parts/1/ack"),
        json={
            "etag": material.etag,
            "size_bytes": material.size_bytes,
            "sha256": other_sha,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "upload_part_conflict"

    with harness.factory() as db:
        count = db.scalar(select(func.count()).select_from(UploadPartModel))
        assert count == 1
        assert db.scalar(select(UploadSessionModel.status)) == "UPLOADING"


def test_resume_missing_part_then_complete_and_replay(upload_api: UploadHarness) -> None:
    harness = upload_api
    project = harness.ids.project_a
    expected_sha = f"sha256:{hashlib.sha256(MCAP_BYTES).hexdigest()}"
    session = _create(harness.client, project, expected_sha=expected_sha)
    first = _upload_part(harness, project, session, 1, MCAP_BYTES[:4])
    detail = _get(harness.client, project, session["upload_session_id"])
    assert detail["missing_part_numbers"] == [2]
    assert len(detail["uploaded_parts"]) == 1
    parts_response = harness.client.get(
        _sessions_url(project, f"/{session['upload_session_id']}/parts")
    )
    assert parts_response.status_code == 200
    assert parts_response.json()["missing_part_numbers"] == [2]
    assert len(parts_response.json()["items"]) == 1

    premature = _complete(
        harness,
        project,
        detail,
        _complete_body(MCAP_BYTES, [first]),
    )
    assert premature.status_code == 409
    assert premature.json()["error"]["code"] == "upload_incomplete"
    assert premature.json()["error"]["details"]["missing_part_numbers"] == [2]

    second = _upload_part(harness, project, session, 2, MCAP_BYTES[4:])
    detail = _get(harness.client, project, session["upload_session_id"])
    materials = [first, second]
    completed = _complete(
        harness,
        project,
        detail,
        _complete_body(MCAP_BYTES, materials),
        key="complete-key-0001",
    )
    assert completed.status_code == 202, completed.text
    body = completed.json()
    assert body["upload_session"]["status"] == "COMPLETED"
    assert body["deduplicated"] is False
    assert body["replayed"] is False
    assert body["raw_asset"]["status"] == "VALIDATING"
    assert body["raw_asset"]["sha256"] == expected_sha
    assert body["raw_asset"]["size_bytes"] == len(MCAP_BYTES)
    assert body["raw_asset"]["object_key"] == body["upload_session"]["object_key"]
    assert body["validation_job_id"]
    assert len(body["upload_session"]["uploaded_parts"]) == len(materials)
    assert body["upload_session"]["missing_part_numbers"] == []

    replay = _complete(
        harness,
        project,
        _get(harness.client, project, session["upload_session_id"]),
        _complete_body(MCAP_BYTES, materials),
        key="complete-key-0001",
    )
    assert replay.status_code == 202
    assert replay.json()["replayed"] is True
    assert replay.json()["validation_job_id"] == body["validation_job_id"]
    assert replay.json()["raw_asset"]["asset_id"] == body["raw_asset"]["asset_id"]

    with harness.factory() as db:
        assert db.scalar(select(func.count()).select_from(UploadSessionModel)) == 1
        assert db.scalar(select(func.count()).select_from(RawAssetModel)) == 1
        assert db.scalar(select(func.count()).select_from(RawAssetLinkModel)) == 1
        event_types = set(db.scalars(select(OutboxEvent.event_type)))
        assert "ingestion.raw_asset.validation_requested" in event_types
        assert db.scalar(select(func.count()).select_from(AuditLog)) >= 3


def test_size_mismatch_is_retryable(upload_api: UploadHarness) -> None:
    harness = upload_api
    project = harness.ids.project_a
    session = _create(harness.client, project)
    materials = _upload_all(harness, project, session, MCAP_BYTES)
    detail = _get(harness.client, project, session["upload_session_id"])

    wrong = _complete(
        harness,
        project,
        detail,
        _complete_body(MCAP_BYTES, materials, expected_size_bytes=len(MCAP_BYTES) - 1),
    )
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "size_mismatch"
    detail = _get(harness.client, project, session["upload_session_id"])
    assert detail["status"] == "UPLOADING"
    assert detail["failure_code"] == "size_mismatch"

    retried = _complete(
        harness,
        project,
        detail,
        _complete_body(MCAP_BYTES, materials),
    )
    assert retried.status_code == 202, retried.text


def test_create_rejects_invalid_type_size_filename_and_foreign_task(
    upload_api: UploadHarness,
) -> None:
    harness = upload_api
    project = harness.ids.project_a

    unsupported = harness.client.post(
        _sessions_url(project),
        json=_payload(content_type="text/plain", format_hint="CSV"),
    )
    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["code"] == "file_type_unsupported"

    too_large = harness.client.post(
        _sessions_url(project),
        json=_payload(size=65),
    )
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "file_too_large"

    bad_name = harness.client.post(
        _sessions_url(project),
        json=_payload(filename="../evil.mcap"),
    )
    assert bad_name.status_code == 422
    assert bad_name.json()["error"]["code"] == "filename_invalid"

    owner_b = make_client(
        harness.factory,
        harness.ids.owner_a,
        object_storage=harness.fake,
        settings_override=harness.settings,
    )
    foreign_task = owner_b.post(
        f"/api/v1/projects/{harness.ids.project_b}/collection-tasks",
        json={
            "code": "b-task-1",
            "name": "B 项目任务",
            "priority": "P1",
            "target_episode_count": 1,
            "target_duration_sec": 10,
        },
    )
    assert foreign_task.status_code == 201, foreign_task.text
    cross_project_ref = harness.client.post(
        _sessions_url(project),
        json=_payload(collection_task_id=foreign_task.json()["task_id"]),
    )
    assert cross_project_ref.status_code == 404
    assert cross_project_ref.json()["error"]["code"] == "resource_not_found"


def test_signature_and_hash_mismatch_fail_session_without_asset(
    upload_api: UploadHarness,
) -> None:
    harness = upload_api
    project = harness.ids.project_a

    mp4_session = _create(
        harness.client,
        project,
        size=len(TEXT_BYTES),
        content_type="video/mp4",
        format_hint="MP4",
        filename="fake.mp4",
    )
    materials = _upload_all(harness, project, mp4_session, TEXT_BYTES)
    detail = _get(harness.client, project, mp4_session["upload_session_id"])
    mismatch = _complete(
        harness,
        project,
        detail,
        _complete_body(TEXT_BYTES, materials),
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "file_signature_mismatch"
    detail = _get(harness.client, project, mp4_session["upload_session_id"])
    assert detail["status"] == "FAILED"

    wrong_hash = f"sha256:{hashlib.sha256(b'other').hexdigest()}"
    hash_session = _create(
        harness.client,
        project,
        expected_sha=f"sha256:{hashlib.sha256(MCAP_BYTES).hexdigest()}",
    )
    materials = _upload_all(harness, project, hash_session, MCAP_BYTES)
    detail = _get(harness.client, project, hash_session["upload_session_id"])
    hash_mismatch = _complete(
        harness,
        project,
        detail,
        {
            "expected_sha256": wrong_hash,
            "expected_size_bytes": len(MCAP_BYTES),
            "parts": [
                {
                    "part_number": item.part_number,
                    "etag": item.etag,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in materials
            ],
        },
    )
    assert hash_mismatch.status_code == 422
    assert hash_mismatch.json()["error"]["code"] == "hash_mismatch"

    with harness.factory() as db:
        assert db.scalar(select(func.count()).select_from(RawAssetModel)) == 0


def test_abort_is_idempotent_and_blocks_further_upload(upload_api: UploadHarness) -> None:
    harness = upload_api
    project = harness.ids.project_a
    session = _create(harness.client, project)

    first = harness.client.post(
        _sessions_url(project, f"/{session['upload_session_id']}/abort"),
        json={"reason": "用户取消"},
        headers={"If-Match": '"1"'},
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "ABORTED"

    second = harness.client.post(
        _sessions_url(project, f"/{session['upload_session_id']}/abort"),
        json={"reason": "重复取消"},
        headers={"If-Match": '"1"'},
    )
    assert second.status_code == 200
    assert second.json()["status"] == "ABORTED"

    url = harness.client.post(
        _sessions_url(project, f"/{session['upload_session_id']}/parts/1/url")
    )
    assert url.status_code == 409
    assert url.json()["error"]["code"] == "upload_session_not_uploading"

    complete = _complete(
        harness,
        project,
        _get(harness.client, project, session["upload_session_id"]),
        {"expected_sha256": f"sha256:{'a' * 64}", "expected_size_bytes": 8, "parts": []},
    )
    assert complete.status_code in {409, 422}


def test_object_storage_failure_is_503_and_retryable(upload_api: UploadHarness) -> None:
    harness = upload_api
    project = harness.ids.project_a
    session = _create(harness.client, project)
    materials = _upload_all(harness, project, session, MCAP_BYTES)
    detail = _get(harness.client, project, session["upload_session_id"])

    harness.fake.fail_next("list")
    failed_get = harness.client.get(_sessions_url(project, f"/{session['upload_session_id']}"))
    assert failed_get.status_code == 503
    assert failed_get.json()["error"]["code"] == "object_storage_unavailable"
    assert _get(harness.client, project, session["upload_session_id"])["status"] == "UPLOADING"

    harness.fake.fail_next("complete")
    failed_complete = _complete(
        harness,
        project,
        detail,
        _complete_body(MCAP_BYTES, materials),
    )
    assert failed_complete.status_code == 503

    detail = _get(harness.client, project, session["upload_session_id"])
    retried = _complete(
        harness,
        project,
        detail,
        _complete_body(MCAP_BYTES, materials),
    )
    assert retried.status_code == 202, retried.text


def test_list_parts_mismatch_returns_conflict(upload_api: UploadHarness) -> None:
    harness = upload_api
    project = harness.ids.project_a
    session = _create(harness.client, project)
    materials = _upload_all(harness, project, session, MCAP_BYTES)
    detail = _get(harness.client, project, session["upload_session_id"])

    harness.fake.put_part(session["object_key"], 1, b"XXXX")
    conflict = _complete(
        harness,
        project,
        detail,
        _complete_body(MCAP_BYTES, materials),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "upload_part_conflict"
    assert _get(harness.client, project, session["upload_session_id"])["status"] == "UPLOADING"


def test_expired_session_returns_410(upload_api: UploadHarness) -> None:
    harness = upload_api
    project = harness.ids.project_a
    session = _create(harness.client, project)

    with harness.factory() as db:
        db.execute(
            update(UploadSessionModel)
            .where(UploadSessionModel.id == UUID(session["upload_session_id"]))
            .values(expires_at=datetime(2000, 1, 1, tzinfo=UTC))
        )
        db.commit()

    expired = harness.client.get(_sessions_url(project, f"/{session['upload_session_id']}"))
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "upload_session_expired"

    with harness.factory() as db:
        assert db.scalar(select(UploadSessionModel.status)) == "EXPIRED"


def test_cross_project_session_and_asset_are_invisible(upload_api: UploadHarness) -> None:
    harness = upload_api
    project_a = harness.ids.project_a
    project_b = harness.ids.project_b
    session = _create(harness.client, project_a)
    materials = _upload_all(harness, project_a, session, MCAP_BYTES)
    detail = _get(harness.client, project_a, session["upload_session_id"])
    completed = _complete(
        harness,
        project_a,
        detail,
        _complete_body(MCAP_BYTES, materials),
    )
    assert completed.status_code == 202
    asset_id = completed.json()["raw_asset"]["asset_id"]

    owner_b = make_client(
        harness.factory,
        harness.ids.owner_a,
        object_storage=harness.fake,
        settings_override=harness.settings,
    )
    assert (
        owner_b.get(_sessions_url(project_b, f"/{session['upload_session_id']}")).status_code == 404
    )
    assert owner_b.get(_assets_url(project_b, f"/{asset_id}")).status_code == 404

    outsider = make_client(
        harness.factory,
        harness.ids.outsider,
        object_storage=harness.fake,
        settings_override=harness.settings,
    )
    assert (
        outsider.get(_sessions_url(project_a, f"/{session['upload_session_id']}")).status_code
        == 403
    )


def test_dedup_same_project_but_not_cross_project(upload_api: UploadHarness) -> None:
    harness = upload_api
    project_a = harness.ids.project_a
    project_b = harness.ids.project_b

    first = _create(harness.client, project_a)
    materials = _upload_all(harness, project_a, first, MCAP_BYTES)
    detail = _get(harness.client, project_a, first["upload_session_id"])
    completed = _complete(harness, project_a, detail, _complete_body(MCAP_BYTES, materials))
    assert completed.status_code == 202
    asset_id = completed.json()["raw_asset"]["asset_id"]

    with harness.factory() as db:
        db.execute(
            update(RawAssetModel).where(RawAssetModel.id == UUID(asset_id)).values(status="VALID")
        )
        db.commit()

    second = _create(harness.client, project_a)
    materials = _upload_all(harness, project_a, second, MCAP_BYTES)
    detail = _get(harness.client, project_a, second["upload_session_id"])
    dedup = _complete(harness, project_a, detail, _complete_body(MCAP_BYTES, materials))
    assert dedup.status_code == 202
    assert dedup.json()["deduplicated"] is True
    assert dedup.json()["raw_asset"]["asset_id"] == asset_id
    assert dedup.json()["upload_session"]["deduplicated_asset_id"] == asset_id

    owner_b_client = make_client(
        harness.factory,
        harness.ids.owner_a,
        object_storage=harness.fake,
        settings_override=harness.settings,
    )
    owner_b = replace(harness, client=owner_b_client)
    cross = _create(owner_b.client, project_b)
    materials = _upload_all(owner_b, project_b, cross, MCAP_BYTES)
    detail = _get(owner_b.client, project_b, cross["upload_session_id"])
    completed_b = _complete(owner_b, project_b, detail, _complete_body(MCAP_BYTES, materials))
    assert completed_b.status_code == 202
    assert completed_b.json()["deduplicated"] is False
    assert completed_b.json()["raw_asset"]["asset_id"] != asset_id

    with harness.factory() as db:
        assert db.scalar(select(func.count()).select_from(RawAssetModel)) == 2


def test_raw_asset_list_and_detail_contract(upload_api: UploadHarness) -> None:
    harness = upload_api
    project = harness.ids.project_a
    session = _create(harness.client, project)
    materials = _upload_all(harness, project, session, MCAP_BYTES)
    detail = _get(harness.client, project, session["upload_session_id"])
    completed = _complete(harness, project, detail, _complete_body(MCAP_BYTES, materials))
    assert completed.status_code == 202

    listed = harness.client.get(_assets_url(project))
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    item = listed.json()["items"][0]
    assert item["format"] == "MCAP"
    assert item["sha256"] == f"sha256:{hashlib.sha256(MCAP_BYTES).hexdigest()}"
    assert item["object_key"].startswith(f"raw/{project}/")
    assert item["upload_session_id"] == session["upload_session_id"]

    detail_response = harness.client.get(_assets_url(project, f"/{item['asset_id']}"))
    assert detail_response.status_code == 200
    assert detail_response.json()["asset_id"] == item["asset_id"]
