"""经营治理 API 集成测试：配额配置、报表三态、越权与跨项目隔离、写操作超限拒绝。

全部使用真实 SQLite + 真实仓储/路由，没有 mock 业务数据。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core.config import Settings
from app.infrastructure.db.models.collection import AuditLog
from app.infrastructure.db.models.governance import UsageSnapshotModel
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.fakes_s3 import FakeMultipartStorage

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def _quotas_url(project_id: object) -> str:
    return f"/api/v1/projects/{project_id}/quotas"


def _quota_url(project_id: object, kind: str) -> str:
    return f"/api/v1/projects/{project_id}/quotas/{kind}"


def _report_url(project_id: object) -> str:
    return f"/api/v1/projects/{project_id}/usage-report"


def _put_quota(
    client: TestClient,
    project_id: object,
    kind: str,
    limit: int,
    *,
    if_match: str | None = None,
) -> dict[str, object]:
    headers = {"If-Match": if_match} if if_match is not None else {}
    response = client.put(_quota_url(project_id, kind), json={"limit": limit}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# --- 配额配置 --------------------------------------------------------------
def test_quota_create_list_and_update_with_if_match(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)

    created = _put_quota(client, ids.project_a, "EPISODES", 100)
    assert created["limit"] == 100
    assert created["version"] == 1
    assert created["resource_kind"] == "EPISODES"
    assert created["project_id"] == str(ids.project_a)

    listed = client.get(_quotas_url(ids.project_a))
    assert listed.status_code == 200
    assert [item["limit"] for item in listed.json()["items"]] == [100]

    updated = _put_quota(client, ids.project_a, "EPISODES", 150, if_match='"1"')
    assert updated["limit"] == 150
    assert updated["version"] == 2

    with factory() as session:
        rows = session.scalars(
            select(AuditLog).where(AuditLog.resource_type == "RESOURCE_QUOTA")
        ).all()
        actions = [row.action for row in rows]
        assert actions == ["create", "update"]
        assert rows[-1].details["previous_limit"] == 100


def test_quota_update_requires_if_match_and_rejects_stale_version(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    _put_quota(client, ids.project_a, "EPISODES", 100)

    missing = client.put(_quota_url(ids.project_a, "EPISODES"), json={"limit": 200})
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "precondition_required"

    stale = client.put(
        _quota_url(ids.project_a, "EPISODES"),
        json={"limit": 200},
        headers={"If-Match": '"9"'},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"


def test_quota_requires_write_permission_and_project_membership(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db

    viewer = make_client(factory, ids.viewer_a)
    forbidden = viewer.put(_quota_url(ids.project_a, "EPISODES"), json={"limit": 10})
    assert forbidden.status_code == 403

    outsider = make_client(factory, ids.outsider)
    denied = outsider.get(_quotas_url(ids.project_a))
    assert denied.status_code == 403


def test_quota_is_isolated_between_projects(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    # owner_a 同时是 A/B 项目 owner，正好用来证明隔离靠 project_id 而不是身份。
    client = make_client(factory, ids.owner_a)
    _put_quota(client, ids.project_a, "EPISODES", 10)
    _put_quota(client, ids.project_b, "EPISODES", 999)

    listed_a = client.get(_quotas_url(ids.project_a)).json()["items"]
    listed_b = client.get(_quotas_url(ids.project_b)).json()["items"]
    assert [item["limit"] for item in listed_a] == [10]
    assert [item["limit"] for item in listed_b] == [999]


# --- 报表 ------------------------------------------------------------------
def test_usage_report_without_snapshot_is_unmeasured_not_zero(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.viewer_a)

    response = client.get(_report_url(ids.project_a))
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == str(ids.project_a)
    assert len(body["entries"]) == 5
    for entry in body["entries"]:
        assert entry["used"] is None
        assert entry["status"] == "UNMEASURED"
        assert entry["measured_at"] is None
        assert entry["remaining"] is None


def test_usage_report_compares_snapshot_with_quota_and_isolates_projects(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.owner_a)
    _put_quota(client, ids.project_a, "EPISODES", 10)
    _put_quota(client, ids.project_a, "OBJECT_STORAGE_BYTES", 1000)

    with factory() as session:
        session.add_all(
            [
                UsageSnapshotModel(
                    project_id=ids.project_a,
                    resource_kind="EPISODES",
                    used_value=12,
                    measurement_source="episodes.count",
                    measured_at=NOW,
                ),
                UsageSnapshotModel(
                    project_id=ids.project_a,
                    resource_kind="OBJECT_STORAGE_BYTES",
                    used_value=0,
                    measurement_source="raw_assets.size_bytes",
                    measured_at=NOW,
                ),
                UsageSnapshotModel(
                    project_id=ids.project_b,
                    resource_kind="EPISODES",
                    used_value=999,
                    measurement_source="episodes.count",
                    measured_at=NOW,
                ),
            ]
        )
        session.commit()

    body = client.get(_report_url(ids.project_a)).json()
    entries = {entry["resource_kind"]: entry for entry in body["entries"]}

    assert entries["EPISODES"]["used"] == 12
    assert entries["EPISODES"]["limit"] == 10
    assert entries["EPISODES"]["remaining"] == 0
    assert entries["EPISODES"]["exceeded"] is True
    assert entries["EPISODES"]["status"] == "OVER_LIMIT"
    assert entries["EPISODES"]["measured_at"] is not None
    # 真实 0 不等于未测量。
    assert entries["OBJECT_STORAGE_BYTES"]["used"] == 0
    assert entries["OBJECT_STORAGE_BYTES"]["status"] == "OK"
    # 项目 B 的快照绝不能出现在 A 的报表里。
    assert entries["EPISODES"]["used"] != 999


# --- 写操作超限拒绝 --------------------------------------------------------
def _upload_client(
    factory: sessionmaker[Session],
    user_id: UUID,
) -> tuple[TestClient, FakeMultipartStorage]:
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
        user_id,
        object_storage=fake,
        settings_override=settings,
    )
    return client, fake


def _upload_payload(size: int) -> dict[str, object]:
    return {
        "original_filename": "sample.mcap",
        "declared_content_type": "application/x-mcap",
        "declared_size_bytes": size,
        "format_hint": "MCAP",
    }


def test_object_storage_quota_rejects_upload_before_transfer_and_audits(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client, _fake = _upload_client(factory, ids.editor_a)
    _put_quota(client, ids.project_a, "OBJECT_STORAGE_BYTES", 10)

    response = client.post(
        f"/api/v1/projects/{ids.project_a}/upload-sessions",
        json=_upload_payload(11),
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "quota_exceeded"
    assert error["details"]["resource_kind"] == "OBJECT_STORAGE_BYTES"
    assert error["details"]["limit"] == 10
    assert error["details"]["used"] == 0
    assert error["details"]["requested"] == 11

    with factory() as session:
        rows = session.scalars(
            select(AuditLog).where(
                AuditLog.resource_type == "RESOURCE_QUOTA",
                AuditLog.action == "quota-exceeded",
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].result == "FAILURE"
        assert rows[0].details["requested"] == 11


def test_object_storage_quota_allows_upload_within_limit_and_other_project(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client, _fake = _upload_client(factory, ids.owner_a)
    _put_quota(client, ids.project_a, "OBJECT_STORAGE_BYTES", 10)

    at_limit = client.post(
        f"/api/v1/projects/{ids.project_a}/upload-sessions",
        json=_upload_payload(10),
    )
    assert at_limit.status_code == 201, at_limit.text

    # 项目 B 没有配额：同一用户在同一客户端上不受 A 的限制。
    other_project = client.post(
        f"/api/v1/projects/{ids.project_b}/upload-sessions",
        json=_upload_payload(10),
    )
    assert other_project.status_code == 201, other_project.text


def test_idempotent_replay_is_not_rejected_by_new_quota_limit(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """已成功的请求重放时不应被新上限拒绝：重放不新建资源。"""
    factory, ids = api_db
    client, _fake = _upload_client(factory, ids.editor_a)
    _put_quota(client, ids.project_a, "OBJECT_STORAGE_BYTES", 10)
    url = f"/api/v1/projects/{ids.project_a}/upload-sessions"
    headers = {"Idempotency-Key": "idem-replay-0001"}

    first = client.post(url, json=_upload_payload(10), headers=headers)
    assert first.status_code == 201, first.text

    # 随后把上限调低到明显不允许新上传的水平。
    _put_quota(client, ids.project_a, "OBJECT_STORAGE_BYTES", 5, if_match='"1"')

    replay = client.post(url, json=_upload_payload(10), headers=headers)
    assert replay.status_code == 201, replay.text
    assert replay.headers.get("Idempotent-Replayed") == "true"

    with factory() as session:
        denied = session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "quota-exceeded")
        )
        assert denied == 0


def test_quota_rejection_does_not_create_business_rows(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """拒绝必须发生在写业务行之前：不会留下半个上传会话。"""
    from app.infrastructure.db.models.ingestion import UploadSessionModel

    factory, ids = api_db
    client, _fake = _upload_client(factory, ids.editor_a)
    _put_quota(client, ids.project_a, "OBJECT_STORAGE_BYTES", 5)

    response = client.post(
        f"/api/v1/projects/{ids.project_a}/upload-sessions",
        json=_upload_payload(6),
    )
    assert response.status_code == 409

    with factory() as session:
        count = session.scalar(
            select(func.count())
            .select_from(UploadSessionModel)
            .where(UploadSessionModel.project_id == ids.project_a)
        )
        assert count == 0


def test_unknown_resource_kind_is_rejected(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    response = client.put(_quota_url(ids.project_a, "CPU_SECONDS"), json={"limit": 1})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_quota_negative_limit_is_rejected(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    response = client.put(_quota_url(ids.project_a, "EPISODES"), json={"limit": -1})
    assert response.status_code == 422
