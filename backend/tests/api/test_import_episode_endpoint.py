"""POST /data-source-imports/{id}/episodes 的 HTTP 契约测试。

覆盖三类必须成立的行为：
1. 成功导入可以生成 Episode，响应结构与契约一致；
2. **未成功的导入被拒绝**——对失败/进行中的导入生成 Episode 等于把半截
   数据当样本；
3. 重复调用幂等（skipped_episode_keys 非空、不新增）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.datasources.media import MediaAsset
from app.domain.datasources.models import DataSource, DataSourceImport
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import RawAssetFormat, RawAssetSourceType, RawAssetStatus
from app.domain.ingestion.models import RawAsset
from app.infrastructure.repositories.datasources import (
    SqlDataSourceImportRepository,
    SqlDataSourceRepository,
    SqlMediaAssetRepository,
)
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import make_client

NOW = datetime(2026, 11, 16, tzinfo=UTC)
_SHA = "a" * 40


def _url(project_id: object, import_id: object) -> str:
    return f"/api/v1/projects/{project_id}/data-source-imports/{import_id}/episodes"


def _seed(
    factory: sessionmaker[Session],
    scope: ProjectScope,
    *,
    status: str = "SUCCEEDED",
    episode_keys: tuple[str, ...] = ("demo_001",),
) -> UUID:
    with factory() as session:
        source = SqlDataSourceRepository(session).add(
            scope,
            DataSource(
                data_source_id=uuid4(),
                project_id=scope.project_id,
                code=f"hf-{uuid4().hex[:8]}",
                name="示例源",
                kind="HUGGINGFACE",
                repo_id="lerobot/aloha_sim_insertion_human",
                revision=_SHA,
                license="Apache-2.0",
                access_condition="PUBLIC",
                is_gated=False,
            ),
        )
        job = SqlDataSourceImportRepository(session).add(
            scope,
            DataSourceImport(
                import_id=uuid4(),
                project_id=scope.project_id,
                data_source_id=source.data_source_id,
                status=status,
                scope="SAMPLE",
                tool_name="huggingface_hub",
                tool_version="0.27.1",
                sample_limit=10,
                finished_at=NOW if status == "SUCCEEDED" else None,
            ),
        )
        raw_repo = SqlRawAssetRepository(session)
        media_repo = SqlMediaAssetRepository(session)
        for index, key in enumerate(episode_keys):
            raw_id = uuid4()
            asset = raw_repo.add(
                scope,
                RawAsset(
                    asset_id=raw_id,
                    project_id=scope.project_id,
                    code=f"RAW-{raw_id.hex[:20]}",
                    source_type=RawAssetSourceType.IMPORT,
                    original_filename=f"{key}.mp4",
                    object_key=(f"raw/{scope.project_id}/2026/11/16/{raw_id}/content.mp4"),
                    size_bytes=1024 + index,
                    content_type="video/mp4",
                    format=RawAssetFormat.MP4,
                    sha256="sha256:" + f"{index:064d}",
                    status=RawAssetStatus.VALIDATING,
                    uploaded_by=scope.actor_id,
                    ingested_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                    created_by=scope.actor_id,
                    updated_by=scope.actor_id,
                ),
            )
            raw_repo.save(scope, asset.mark_valid(now=NOW), expected_version=asset.version)
            media_repo.add(
                scope,
                MediaAsset(
                    asset_id=uuid4(),
                    project_id=scope.project_id,
                    data_source_id=source.data_source_id,
                    import_id=job.import_id,
                    episode_key=key,
                    path=f"videos/{key}_main.mp4",
                    camera="main",
                    object_key=asset.object_key,
                    size_bytes=asset.size_bytes,
                    sha256=asset.sha256,
                    content_type="video/mp4",
                    task_name="Sew lace fabric",
                    raw_asset_id=asset.asset_id,
                    excerpt_start_seconds=0.0,
                    excerpt_seconds=12.0,
                    created_at=NOW,
                ),
            )
        session.commit()
        return job.import_id


def test_succeeded_import_creates_episodes(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    scope = ProjectScope(ids.project_a, actor_id=ids.editor_a)
    import_id = _seed(factory, scope, episode_keys=("demo_001", "demo_002"))

    response = client.post(_url(ids.project_a, import_id), json={"scene_labels": ["industrial"]})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["import_id"] == str(import_id)
    assert body["episode_count"] == 2
    assert {item["episode_key"] for item in body["episodes"]} == {"demo_001", "demo_002"}
    assert body["skipped_episode_keys"] == []
    for item in body["episodes"]:
        assert len(item["raw_asset_ids"]) == 1


def test_repeat_call_is_idempotent(api_db) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    scope = ProjectScope(ids.project_a, actor_id=ids.editor_a)
    import_id = _seed(factory, scope)

    first = client.post(_url(ids.project_a, import_id), json={})
    assert first.status_code == 201
    assert first.json()["episode_count"] == 1

    second = client.post(_url(ids.project_a, import_id), json={})
    assert second.status_code == 201
    assert second.json()["episode_count"] == 0
    assert second.json()["skipped_episode_keys"] == ["demo_001"]


def test_failed_import_is_rejected(api_db) -> None:
    """失败导入不得生成 Episode：那是把半截数据当样本。"""
    factory, ids = api_db
    client = make_client(factory, ids.editor_a)
    scope = ProjectScope(ids.project_a, actor_id=ids.editor_a)
    import_id = _seed(factory, scope, status="FAILED")

    response = client.post(_url(ids.project_a, import_id), json={})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


def test_cross_project_import_is_not_found(api_db) -> None:
    factory, ids = api_db
    scope = ProjectScope(ids.project_a, actor_id=ids.editor_a)
    import_id = _seed(factory, scope)
    client = make_client(factory, ids.owner_a)

    response = client.post(_url(ids.project_b, import_id), json={})

    assert response.status_code in {403, 404}


def test_viewer_cannot_materialize(api_db) -> None:
    factory, ids = api_db
    scope = ProjectScope(ids.project_a, actor_id=ids.editor_a)
    import_id = _seed(factory, scope)
    client = make_client(factory, ids.viewer_a)

    response = client.post(_url(ids.project_a, import_id), json={})

    assert response.status_code == 403
