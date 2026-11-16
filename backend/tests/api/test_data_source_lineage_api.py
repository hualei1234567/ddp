"""数据源 / 导入任务血缘 API 集成测试（真实 HTTP + 真实 SQLite 仓储）。

覆盖验收要求的三件事：
1. Episode / RawAsset 反向能查到「哪个数据源的哪次导入」；
2. 数据源正向能查到它产出了哪些资产；
3. 手工上传的资产显式表达为「无外部数据源」，跨项目数据源不可见。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.datasources import (
    DataSourceImportModel,
    DataSourceModel,
    MediaAssetModel,
)
from app.infrastructure.db.models.ingestion import RawAssetModel
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.ddp310_support import episode_payload, project_url, seed_valid_asset

_SHA = "0123456789abcdef0123456789abcdef01234567"
_NOW = datetime(2026, 11, 8, 13, 0, tzinfo=UTC)


@pytest.fixture()
def lineage_api(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> tuple[Any, sessionmaker[Session], ApiIds]:
    factory, ids = api_db
    return make_client(factory, ids.editor_a), factory, ids


def _create_data_source(client: Any, project_id: UUID) -> dict[str, Any]:
    return client.post(
        project_url(project_id, "/data-sources"),
        json={
            "code": f"hf-{uuid4().hex[:8]}",
            "name": "示例 HF 数据集",
            "repo_id": "lerobot/aloha_sim_insertion_human",
            "revision": _SHA,
            "license": "Apache-2.0",
            "access_condition": "PUBLIC",
            "is_gated": False,
        },
    ).json()


def _create_import(client: Any, project_id: UUID, source_id: str) -> dict[str, Any]:
    response = client.post(
        project_url(project_id, f"/data-sources/{source_id}/imports"),
        json={
            "scope": "SAMPLE",
            "tool_name": "huggingface_hub",
            "tool_version": "0.27.1",
            "sample_limit": 3,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _seed_import_asset(
    factory: sessionmaker[Session],
    ids: ApiIds,
    *,
    source_id: str,
    import_id: str,
) -> UUID:
    """写入一条 IMPORT 来源的 RawAsset + 对应媒体资产行（导入 Worker 的产出形状）。"""
    asset_id = uuid4()
    with factory() as session:
        session.add(
            RawAssetModel(
                id=asset_id,
                project_id=ids.project_a,
                code=f"RAW-import-{asset_id.hex[:12]}",
                source_type="IMPORT",
                original_filename="demo_001_main.mp4",
                object_key=f"raw/{ids.project_a}/2026/11/08/{asset_id}/content.mp4",
                size_bytes=4096,
                content_type="video/mp4",
                format="MP4",
                sha256=f"sha256:{asset_id.hex}{uuid4().hex}"[:71],
                status="VALIDATING",
                ingested_at=_NOW,
                storage_class="STANDARD",
                quarantine_codes=[],
                raw_metadata={
                    "origin": "data_source_import",
                    "data_source_id": source_id,
                    "import_id": import_id,
                },
                uploaded_by=ids.editor_a,
                version=1,
                created_at=_NOW,
                updated_at=_NOW,
                created_by=ids.editor_a,
                updated_by=ids.editor_a,
            )
        )
        session.flush()
        # 与生产写入一致：先 VALIDATING 再转 VALID（初始状态受触发器约束）。
        session.execute(
            update(RawAssetModel)
            .where(RawAssetModel.id == asset_id)
            .values(status="VALID")
            .execution_options(synchronize_session=False)
        )
        session.add(
            MediaAssetModel(
                id=uuid4(),
                project_id=ids.project_a,
                data_source_id=UUID(source_id),
                import_id=UUID(import_id),
                raw_asset_id=asset_id,
                episode_key="demo_001",
                path="videos/demo_001_main.mp4",
                camera="main",
                object_key=f"raw/{ids.project_a}/2026/11/08/{asset_id}/content.mp4",
                size_bytes=4096,
                sha256=f"sha256:{'d' * 64}",
                content_type="video/mp4",
                task_name="Sew lace fabric",
                source_fps=30.0,
                created_by=ids.editor_a,
                updated_by=ids.editor_a,
                created_at=_NOW,
                updated_at=_NOW,
                version=1,
            )
        )
        session.commit()
    return asset_id


def _seed_foreign_source(factory: sessionmaker[Session], ids: ApiIds) -> tuple[UUID, UUID]:
    """在 B 项目写一套数据源 + 导入任务，用于跨项目隔离断言。"""
    source_id, import_id = uuid4(), uuid4()
    with factory() as session:
        session.add(
            DataSourceModel(
                id=source_id,
                project_id=ids.project_b,
                code="hf-foreign",
                name="B 项目数据源",
                kind="HUGGINGFACE",
                repo_id="nvidia/PhysicalAI-Human-1M-100K",
                revision=_SHA,
                license="CC-BY-4.0",
                access_condition="PUBLIC",
                is_gated=False,
                metadata_json={},
                created_by=ids.owner_a,
                updated_by=ids.owner_a,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        session.flush()
        session.add(
            DataSourceImportModel(
                id=import_id,
                project_id=ids.project_b,
                data_source_id=source_id,
                status="SUCCEEDED",
                scope="SAMPLE",
                tool_name="huggingface_hub",
                tool_version="0.27.1",
                sample_limit=1,
                created_by=ids.owner_a,
                updated_by=ids.owner_a,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        session.commit()
    return source_id, import_id


def test_episode_lineage_reverse_reaches_data_source(
    lineage_api: tuple[Any, sessionmaker[Session], ApiIds],
) -> None:
    client, factory, ids = lineage_api
    source = _create_data_source(client, ids.project_a)
    job = _create_import(client, ids.project_a, source["data_source_id"])
    asset_id = _seed_import_asset(
        factory,
        ids,
        source_id=source["data_source_id"],
        import_id=job["import_id"],
    )
    episode = client.post(
        project_url(ids.project_a, "/episodes"),
        json=episode_payload(asset_id, code="EP-SRC-001"),
        headers={"Idempotency-Key": "lineage-source-001"},
    )
    assert episode.status_code == 201, episode.text
    episode_id = episode.json()["episode_id"]

    response = client.get(
        f"/api/v1/episodes/{episode_id}/lineage?project_id={ids.project_a}"
        f"&direction=reverse&depth=3&limit=50"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    triples = {
        (item["from_node_type"], item["to_node_type"], item["relation"]) for item in body["items"]
    }
    # 既有血缘原样保留。
    assert ("RAW_ASSET", "EPISODE", "SEGMENTED_INTO") in triples
    # 新增的数据源链：导入任务 → 资产 → 数据源 → 导入任务。
    assert ("DATA_SOURCE_IMPORT", "RAW_ASSET", "PRODUCED") in triples
    assert ("DATA_SOURCE", "DATA_SOURCE_IMPORT", "PRODUCED") in triples

    derived = [
        item
        for item in body["items"]
        if item["from_node_type"] == "DATA_SOURCE_IMPORT" and item["to_node_type"] == "RAW_ASSET"
    ]
    assert len(derived) == 1
    assert derived[0]["from_node_id"] == job["import_id"]
    assert derived[0]["to_node_id"] == str(asset_id)
    assert derived[0]["metadata"]["derived"] is True
    assert derived[0]["metadata"]["episode_key"] == "demo_001"

    sources = {item["raw_asset_id"]: item for item in body["raw_asset_sources"]}
    origin = sources[str(asset_id)]
    assert origin["status"] == "RESOLVED"
    assert origin["data_sources"][0]["data_source_id"] == source["data_source_id"]
    assert origin["data_sources"][0]["repo_id"] == "lerobot/aloha_sim_insertion_human"
    assert origin["imports"][0]["import_id"] == job["import_id"]
    assert origin["imports"][0]["status"] == "PENDING"


def test_raw_asset_lineage_reverse_reaches_data_source(
    lineage_api: tuple[Any, sessionmaker[Session], ApiIds],
) -> None:
    client, factory, ids = lineage_api
    source = _create_data_source(client, ids.project_a)
    job = _create_import(client, ids.project_a, source["data_source_id"])
    asset_id = _seed_import_asset(
        factory, ids, source_id=source["data_source_id"], import_id=job["import_id"]
    )

    response = client.get(
        f"/api/v1/raw-assets/{asset_id}/lineage?project_id={ids.project_a}"
        f"&direction=reverse&depth=2"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["direction"] == "reverse"
    assert {(item["from_node_type"], item["to_node_type"]) for item in body["items"]} == {
        ("DATA_SOURCE_IMPORT", "RAW_ASSET"),
        ("DATA_SOURCE", "DATA_SOURCE_IMPORT"),
    }
    assert body["raw_asset_sources"][0]["status"] == "RESOLVED"


def test_data_source_lineage_forward_lists_produced_assets(
    lineage_api: tuple[Any, sessionmaker[Session], ApiIds],
) -> None:
    client, factory, ids = lineage_api
    source = _create_data_source(client, ids.project_a)
    job = _create_import(client, ids.project_a, source["data_source_id"])
    asset_id = _seed_import_asset(
        factory, ids, source_id=source["data_source_id"], import_id=job["import_id"]
    )

    response = client.get(
        project_url(
            ids.project_a,
            f"/data-sources/{source['data_source_id']}/lineage?direction=forward&depth=2",
        )
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [(item["from_node_type"], item["to_node_type"]) for item in body["items"]] == [
        ("DATA_SOURCE", "DATA_SOURCE_IMPORT"),
        ("DATA_SOURCE_IMPORT", "RAW_ASSET"),
    ]
    assert body["items"][1]["to_node_id"] == str(asset_id)
    assert {item["raw_asset_id"] for item in body["raw_asset_sources"]} == {str(asset_id)}


def test_upload_asset_reports_no_external_source(
    lineage_api: tuple[Any, sessionmaker[Session], ApiIds],
) -> None:
    client, factory, ids = lineage_api
    uploaded_asset = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="upload")

    response = client.get(
        f"/api/v1/raw-assets/{uploaded_asset}/lineage?project_id={ids.project_a}"
        f"&direction=reverse&depth=2"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["raw_asset_sources"] == [
        {
            "raw_asset_id": str(uploaded_asset),
            "status": "NO_EXTERNAL_SOURCE",
            "data_sources": [],
            "imports": [],
        }
    ]


def test_cross_project_data_source_lineage_is_404(
    lineage_api: tuple[Any, sessionmaker[Session], ApiIds],
) -> None:
    client, factory, ids = lineage_api
    foreign_source, _foreign_import = _seed_foreign_source(factory, ids)

    response = client.get(
        project_url(ids.project_a, f"/data-sources/{foreign_source}/lineage?direction=forward")
    )

    assert response.status_code == 404, response.text
    assert str(foreign_source) not in response.text


def test_viewer_cannot_read_data_source_lineage_without_membership(
    lineage_api: tuple[Any, sessionmaker[Session], ApiIds],
) -> None:
    """项目外用户看不到血缘，也不能靠错误码判断数据源是否存在。"""
    _client, factory, ids = lineage_api
    source = _create_data_source(_client, ids.project_a)
    outsider = make_client(factory, ids.outsider)

    response = outsider.get(
        project_url(ids.project_a, f"/data-sources/{source['data_source_id']}/lineage")
    )

    assert response.status_code in {403, 404}, response.text
