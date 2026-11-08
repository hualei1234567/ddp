"""血缘查询与 RawAsset 下载地址 API：方向、隔离、短时 URL 与审计脱敏。"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.infrastructure.db.models.collection import AuditLog
from app.infrastructure.db.models.ingestion import RawAssetModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.ddp310_support import episode_payload, project_url, seed_valid_asset
from tests.fakes_s3 import FakeMultipartStorage


@pytest.fixture()
def lineage_api(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> tuple[Any, ApiIds, UUID, FakeMultipartStorage, sessionmaker[Session]]:
    factory, ids = api_db
    fake = FakeMultipartStorage()
    settings = Settings(
        env="local",
        download_url_ttl_seconds=60,
    )
    client = make_client(
        factory,
        ids.editor_a,
        object_storage=fake,
        settings_override=settings,
    )
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="lineage")
    return client, ids, asset_id, fake, factory


def _create_episode(
    client: Any,
    project_id: UUID,
    asset_id: UUID,
    *,
    code: str = "EP-LINEAGE-001",
) -> dict[str, object]:
    response = client.post(
        project_url(project_id, "/episodes"),
        json=episode_payload(asset_id, code=code),
        headers={"Idempotency-Key": f"lineage-create-{code}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_episode_and_raw_asset_lineage_directions(
    lineage_api: tuple[Any, ApiIds, UUID, FakeMultipartStorage, sessionmaker[Session]],
) -> None:
    client, ids, asset_id, _fake, _factory = lineage_api
    project = ids.project_a
    episode = _create_episode(client, project, asset_id)
    episode_id = episode["episode_id"]

    forward = client.get(
        f"/api/v1/episodes/{episode_id}/lineage?project_id={project}&direction=forward"
    )
    assert forward.status_code == 200, forward.text
    assert forward.json()["direction"] == "forward"

    reverse = client.get(
        f"/api/v1/episodes/{episode_id}/lineage?project_id={project}&direction=reverse"
    )
    assert reverse.status_code == 200
    reverse_items = reverse.json()["items"]
    assert any(
        item["relation"] == "SEGMENTED_INTO"
        and item["from_node_id"] == str(asset_id)
        and item["to_node_id"] == str(episode_id)
        for item in reverse_items
    )

    asset_forward = client.get(
        f"/api/v1/raw-assets/{asset_id}/lineage?project_id={project}&direction=forward"
    )
    assert asset_forward.status_code == 200
    assert any(
        item["relation"] == "SEGMENTED_INTO" and item["to_node_id"] == str(episode_id)
        for item in asset_forward.json()["items"]
    )

    invalid_direction = client.get(
        f"/api/v1/episodes/{episode_id}/lineage?project_id={project}&direction=sideways"
    )
    assert invalid_direction.status_code == 422

    missing_query = client.get(f"/api/v1/episodes/{episode_id}/lineage")
    assert missing_query.status_code == 422

    missing_episode = client.get(f"/api/v1/episodes/{uuid4()}/lineage?project_id={project}")
    assert missing_episode.status_code == 404
    assert missing_episode.json()["error"]["code"] == "resource_not_found"

    owner_b = make_client(
        lineage_api[4],
        ids.owner_a,
        object_storage=FakeMultipartStorage(),
    )
    cross = owner_b.get(f"/api/v1/episodes/{episode_id}/lineage?project_id={ids.project_b}")
    assert cross.status_code == 404

    outsider = make_client(
        lineage_api[4],
        ids.outsider,
        object_storage=FakeMultipartStorage(),
    )
    forbidden = outsider.get(f"/api/v1/episodes/{episode_id}/lineage?project_id={project}")
    assert forbidden.status_code == 403


def test_download_url_short_lived_audited_without_plaintext(
    lineage_api: tuple[Any, ApiIds, UUID, FakeMultipartStorage, sessionmaker[Session]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, ids, asset_id, fake, factory = lineage_api
    project = ids.project_a
    with factory() as session:
        object_key = session.scalar(
            select(RawAssetModel.object_key).where(RawAssetModel.id == asset_id)
        )
    assert object_key is not None
    fake.put_object(object_key, b"\x89MCAP0\r\nsynthetic")

    caplog.set_level(logging.DEBUG)
    response = client.get(f"/api/v1/raw-assets/{asset_id}/download-url?project_id={project}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"].startswith("https://fake-s3.test/")
    assert body["expires_at"]
    assert fake.download_presigned_calls == [{"object_key": object_key, "expires_in_seconds": 60}]
    # 只生成 URL，不读取对象内容；大文件不经过 HTTP 线程。
    assert fake.stream_calls == []

    with factory() as session:
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.project_id == project,
                    AuditLog.resource_id == asset_id,
                )
            )
        )
    download_audits = [row for row in audits if row.action == "download_requested"]
    assert len(download_audits) == 1
    assert download_audits[0].request_id is not None
    serialized = json.dumps(download_audits[0].details, ensure_ascii=False)
    assert body["url"] not in serialized
    assert "X-Amz" not in serialized
    assert "http" not in serialized

    assert body["url"] not in caplog.text
    assert "X-Amz-Signature" not in caplog.text

    viewer = make_client(
        factory,
        ids.viewer_a,
        object_storage=fake,
        settings_override=Settings(env="local", download_url_ttl_seconds=60),
    )
    assert (
        viewer.get(f"/api/v1/raw-assets/{asset_id}/download-url?project_id={project}").status_code
        == 200
    )

    owner_b = make_client(
        factory,
        ids.owner_a,
        object_storage=fake,
        settings_override=Settings(env="local", download_url_ttl_seconds=60),
    )
    assert (
        owner_b.get(
            f"/api/v1/raw-assets/{asset_id}/download-url?project_id={ids.project_b}"
        ).status_code
        == 404
    )

    outsider = make_client(
        factory,
        ids.outsider,
        object_storage=fake,
        settings_override=Settings(env="local", download_url_ttl_seconds=60),
    )
    assert (
        outsider.get(f"/api/v1/raw-assets/{asset_id}/download-url?project_id={project}").status_code
        == 403
    )
    assert (
        client.get(f"/api/v1/raw-assets/{uuid4()}/download-url?project_id={project}").status_code
        == 404
    )
    assert client.get(f"/api/v1/raw-assets/{asset_id}/download-url").status_code == 422


def test_download_url_rejects_unvalidated_asset(
    lineage_api: tuple[Any, ApiIds, UUID, FakeMultipartStorage, sessionmaker[Session]],
) -> None:
    client, ids, _asset_id, _fake, factory = lineage_api
    project = ids.project_a
    pending_id = seed_valid_asset(factory, project, ids.owner_a, status="VALIDATING")
    response = client.get(f"/api/v1/raw-assets/{pending_id}/download-url?project_id={project}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "asset_not_usable"
