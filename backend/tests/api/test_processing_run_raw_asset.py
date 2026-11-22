"""ProcessingRun 接受 ``raw_asset_id`` 并校验 ``VALID``（V31-015）。

当前创建接口只接受裸对象 key（``input_refs``），调用方必须自己拼
``raw/<project>/<date>/<asset>/content.<ext>`` 这种存储路径。这有两个问题：

1. **契约泄漏**：对象存储的 key 布局是实现细节，客户端不该知道；
2. **无法校验**：裸 key 没有任何业务含义，服务端无法判断它指向的资产是否
   已通过校验（``VALID``），只能等到 FILE_VALIDATION 阶段才发现对象不存在。

本文件锁定新契约：``raw_asset_id`` 与 ``input_refs`` 二选一（至少一个），
``raw_asset_id`` 由服务端解析成对象 key，并在创建时校验资产属于当前项目且
状态为 ``VALID`` / ``ARCHIVED``。``input_refs`` 保持兼容（既有调用方不受影响）。

**为什么不直接替换 ``input_refs``**：V31-016 才引入 ``processing_artifacts``
产物目录，在此之前仍需要能引用非 RawAsset 的中间产物（如 canonical 前缀）。
两者并存是过渡期的最小变更面。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.ingestion import RawAssetModel
from sqlalchemy import update

from tests.api.conftest import make_client
from tests.api.dataset_support import url

pytestmark = pytest.mark.usefixtures("api_db")

_NOW = datetime(2026, 11, 22, 0, 0, tzinfo=UTC)
_SHA = "sha256:" + "a" * 64


def _seed_asset(
    factory,
    project_id: UUID,
    actor_id: UUID,
    *,
    status: str = "VALID",
) -> UUID:
    """插入一条 RawAsset；DB 守卫要求从 VALIDATING 起，再用 UPDATE 推进。"""
    asset_id = uuid4()
    with factory() as session:
        session.add(
            RawAssetModel(
                id=asset_id,
                project_id=project_id,
                code=f"RAW-{asset_id.hex[:12]}",
                source_type="UPLOAD",
                original_filename="sample.mcap",
                object_key=f"raw/{project_id}/2026/11/22/{asset_id}/content.mcap",
                size_bytes=1024,
                content_type="application/x-mcap",
                format="MCAP",
                sha256=_SHA,
                status="VALIDATING",
                ingested_at=_NOW,
                storage_class="STANDARD",
                quarantine_codes=[],
                raw_metadata={},
                uploaded_by=actor_id,
                version=1,
                created_at=_NOW,
                updated_at=_NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        if status != "VALIDATING":
            session.execute(
                update(RawAssetModel)
                .where(RawAssetModel.id == asset_id)
                .values(status=status)
                .execution_options(synchronize_session=False)
            )
        session.commit()
    return asset_id


def _object_key(project_id: UUID, asset_id: UUID) -> str:
    return f"raw/{project_id}/2026/11/22/{asset_id}/content.mcap"


def test_create_resolves_raw_asset_id_to_object_key(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    asset_id = _seed_asset(factory, ids.project_a, ids.editor_a)

    response = editor.post(
        url(ids.project_a, "/processing-runs"),
        json={"pipeline_version": "pipeline-v1", "raw_asset_id": str(asset_id)},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    # 服务端解析：客户端只给资产 ID，存储 key 由服务端从资产记录读取。
    assert body["input_refs"] == [_object_key(ids.project_a, asset_id)]


def test_create_rejects_asset_that_is_not_valid(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    asset_id = _seed_asset(factory, ids.project_a, ids.editor_a, status="QUARANTINED")

    response = editor.post(
        url(ids.project_a, "/processing-runs"),
        json={"pipeline_version": "pipeline-v1", "raw_asset_id": str(asset_id)},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"


def test_create_rejects_unknown_asset(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    response = editor.post(
        url(ids.project_a, "/processing-runs"),
        json={"pipeline_version": "pipeline-v1", "raw_asset_id": str(uuid4())},
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "resource_not_found"


def test_create_rejects_asset_from_another_project(api_db) -> None:
    """跨项目资产必须表现为"不存在"，不能泄露它在别的项目里存在。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    foreign_asset = _seed_asset(factory, ids.project_b, ids.editor_a)

    response = editor.post(
        url(ids.project_a, "/processing-runs"),
        json={"pipeline_version": "pipeline-v1", "raw_asset_id": str(foreign_asset)},
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "resource_not_found"


def test_create_requires_exactly_one_of_raw_asset_or_input_refs(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    asset_id = _seed_asset(factory, ids.project_a, ids.editor_a)

    neither = editor.post(
        url(ids.project_a, "/processing-runs"),
        json={"pipeline_version": "pipeline-v1"},
    )
    assert neither.status_code == 422, neither.text

    both = editor.post(
        url(ids.project_a, "/processing-runs"),
        json={
            "pipeline_version": "pipeline-v1",
            "raw_asset_id": str(asset_id),
            "input_refs": [f"raw/{ids.project_a}/1"],
        },
    )
    assert both.status_code == 422, both.text


def test_input_refs_path_still_works(api_db) -> None:
    """既有调用方只用 ``input_refs``，行为必须完全不变。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    ref = f"raw/{ids.project_a}/2026/11/22/legacy/content.mcap"

    response = editor.post(
        url(ids.project_a, "/processing-runs"),
        json={"pipeline_version": "pipeline-v1", "input_refs": [ref]},
    )

    assert response.status_code == 201, response.text
    assert response.json()["input_refs"] == [ref]
