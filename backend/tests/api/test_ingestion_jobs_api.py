"""IngestionJob HTTP API：创建/列表/详情/取消/重试、权限、隔离与幂等。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.jobs import IngestionJobModel
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.ddp310_support import job_payload, project_url, seed_valid_asset
from tests.fakes_s3 import FakeMultipartStorage


@pytest.fixture()
def job_api(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> tuple[Any, ApiIds, UUID, sessionmaker[Session]]:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a, object_storage=FakeMultipartStorage())
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="job")
    return client, ids, asset_id, factory


def _create(
    client: Any,
    project_id: UUID,
    asset_id: UUID,
    *,
    key: str,
    job_type: str = "PARSE_METADATA",
) -> dict[str, object]:
    response = client.post(
        project_url(project_id, "/ingestion-jobs"),
        json=job_payload(asset_id, job_type=job_type),
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_idempotent_list_detail_and_pagination(
    job_api: tuple[Any, ApiIds, UUID, sessionmaker[Session]],
) -> None:
    client, ids, asset_id, _factory = job_api
    project = ids.project_a
    body = job_payload(asset_id)
    headers = {"Idempotency-Key": "job-create-0001"}

    created = client.post(project_url(project, "/ingestion-jobs"), json=body, headers=headers)
    assert created.status_code == 201, created.text
    assert created.headers["Idempotent-Replayed"] == "false"
    assert created.headers["ETag"] == '"1"'
    job = created.json()
    assert job["status"] == "QUEUED"
    assert job["job_type"] == "PARSE_METADATA"
    assert job["raw_asset_ids"] == [str(asset_id)]

    replay = client.post(project_url(project, "/ingestion-jobs"), json=body, headers=headers)
    assert replay.status_code == 201
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["job_id"] == job["job_id"]

    conflict = client.post(
        project_url(project, "/ingestion-jobs"),
        json=job_payload(asset_id, job_type="VALIDATE"),
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reused"

    detail = client.get(project_url(project, f"/ingestion-jobs/{job['job_id']}"))
    assert detail.status_code == 200
    assert detail.headers["ETag"] == '"1"'

    for index in range(2, 5):
        _create(client, project, asset_id, key=f"job-create-000{index}")

    first = client.get(project_url(project, "/ingestion-jobs?limit=2")).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    second = client.get(
        project_url(project, f"/ingestion-jobs?limit=2&cursor={first['next_cursor']}")
    ).json()
    assert len(second["items"]) == 2
    assert second["next_cursor"] is None
    assert {item["job_id"] for item in first["items"]} & {
        item["job_id"] for item in second["items"]
    } == set()

    assert client.get(project_url(project, "/ingestion-jobs?cursor=bad")).status_code == 422
    assert client.get(project_url(project, "/ingestion-jobs?status=UNKNOWN")).status_code == 422


def _set_job_failed(
    session: Session,
    job_id: UUID,
    *,
    attempt: int | None = None,
    max_attempts: int | None = None,
) -> None:
    """按合法迁移 QUEUED→RUNNING→FAILED 种入失败态。"""
    session.execute(
        update(IngestionJobModel)
        .where(IngestionJobModel.id == job_id)
        .values(
            status="RUNNING",
            started_at=datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        )
    )
    values: dict[str, object] = {
        "status": "FAILED",
        "finished_at": datetime(2026, 11, 8, 12, 0, tzinfo=UTC),
        "error_code": "parse_failed",
        "error_summary": "解析失败",
    }
    if attempt is not None:
        values["attempt"] = attempt
    if max_attempts is not None:
        values["max_attempts"] = max_attempts
    session.execute(
        update(IngestionJobModel).where(IngestionJobModel.id == job_id).values(**values)
    )
    session.commit()


def test_cancel_retry_idempotency_and_state_conflict(
    job_api: tuple[Any, ApiIds, UUID, sessionmaker[Session]],
) -> None:
    client, ids, asset_id, factory = job_api
    project = ids.project_a
    job = _create(client, project, asset_id, key="job-action-0001")
    base = project_url(project, f"/ingestion-jobs/{job['job_id']}")

    headers = {"If-Match": '"1"', "Idempotency-Key": "job-cancel-0001"}
    cancelled = client.post(f"{base}/actions/cancel", json={"reason": "用户取消"}, headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLING"
    assert cancelled.headers["ETag"] == '"2"'

    replay = client.post(f"{base}/actions/cancel", json={"reason": "用户取消"}, headers=headers)
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["version"] == 2

    again = client.post(
        f"{base}/actions/cancel",
        json={"reason": "重复取消"},
        headers={"If-Match": '"2"', "Idempotency-Key": "job-cancel-0002"},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "state_transition_invalid"

    retry_job = _create(
        client,
        project,
        asset_id,
        key="job-action-0002",
        job_type="VALIDATE",
    )
    retry_base = project_url(project, f"/ingestion-jobs/{retry_job['job_id']}")
    with factory() as session:
        _set_job_failed(session, UUID(retry_job["job_id"]))

    retried = client.post(
        f"{retry_base}/actions/retry",
        headers={"If-Match": '"1"', "Idempotency-Key": "job-retry-0001"},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "QUEUED"
    assert retried.json()["attempt"] == 2

    maxed = _create(client, project, asset_id, key="job-action-0003", job_type="VALIDATE")
    maxed_base = project_url(project, f"/ingestion-jobs/{maxed['job_id']}")
    with factory() as session:
        _set_job_failed(session, UUID(maxed["job_id"]), attempt=3, max_attempts=3)
    not_retryable = client.post(
        f"{maxed_base}/actions/retry",
        headers={"If-Match": '"1"', "Idempotency-Key": "job-retry-0002"},
    )
    assert not_retryable.status_code == 409
    assert not_retryable.json()["error"]["code"] == "job_not_retryable"


def test_permissions_cross_project_and_validation_errors(
    job_api: tuple[Any, ApiIds, UUID, sessionmaker[Session]],
) -> None:
    client, ids, asset_id, factory = job_api
    project_a = ids.project_a
    job = _create(client, project_a, asset_id, key="job-perm-0001")

    missing_key = client.post(
        project_url(project_a, "/ingestion-jobs"),
        json=job_payload(asset_id),
    )
    assert missing_key.status_code == 428
    assert missing_key.json()["error"]["code"] == "precondition_required"

    missing_match = client.post(
        project_url(project_a, f"/ingestion-jobs/{job['job_id']}/actions/cancel"),
        json={"reason": "取消"},
        headers={"Idempotency-Key": "job-perm-cancel-0001"},
    )
    assert missing_match.status_code == 428

    unknown_action = client.post(
        project_url(project_a, f"/ingestion-jobs/{job['job_id']}/actions/worker-start"),
        headers={"If-Match": '"1"', "Idempotency-Key": "job-perm-action-0001"},
    )
    assert unknown_action.status_code == 422

    extra_field = client.post(
        project_url(project_a, "/ingestion-jobs"),
        json={**job_payload(asset_id), "status": "SUCCEEDED"},
        headers={"Idempotency-Key": "job-perm-extra-0001"},
    )
    assert extra_field.status_code == 422

    bad_type = client.post(
        project_url(project_a, "/ingestion-jobs"),
        json=job_payload(asset_id, job_type="UNKNOWN"),
        headers={"Idempotency-Key": "job-perm-type-0001"},
    )
    assert bad_type.status_code == 422

    missing_asset = client.post(
        project_url(project_a, "/ingestion-jobs"),
        json=job_payload(uuid4()),
        headers={"Idempotency-Key": "job-perm-missing-0001"},
    )
    assert missing_asset.status_code == 404
    assert missing_asset.json()["error"]["code"] == "resource_not_found"

    viewer = make_client(factory, ids.viewer_a, object_storage=FakeMultipartStorage())
    viewer_write = viewer.post(
        project_url(project_a, "/ingestion-jobs"),
        json=job_payload(asset_id),
        headers={"Idempotency-Key": "job-viewer-0001"},
    )
    assert viewer_write.status_code == 403

    outsider = make_client(factory, ids.outsider, object_storage=FakeMultipartStorage())
    assert outsider.get(project_url(project_a, "/ingestion-jobs")).status_code == 403

    owner_b = make_client(factory, ids.owner_a, object_storage=FakeMultipartStorage())
    cross = owner_b.get(project_url(ids.project_b, f"/ingestion-jobs/{job['job_id']}"))
    assert cross.status_code == 404
    cross_list = owner_b.get(project_url(ids.project_b, "/ingestion-jobs"))
    assert cross_list.status_code == 200
    assert cross_list.json()["items"] == []
