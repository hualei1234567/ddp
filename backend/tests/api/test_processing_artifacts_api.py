"""处理运行产物 API 测试（V31-016）。

覆盖：产物列表按运行返回、跨项目 404、运行不存在 404、只读语义（不提供
写端点——产物由 Worker 登记，客户端不能伪造）、按阶段过滤。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import app.infrastructure.db.models  # noqa: F401 - 注册全部 ORM 模型
import pytest
from app.domain.identity.models import ProjectScope
from app.domain.processing.artifacts import ProcessingArtifact, ProcessingArtifactKind
from app.domain.processing.enums import ProcessingStage
from app.infrastructure.db.models.processing import ProcessingRunModel
from app.infrastructure.repositories.processing_artifacts import (
    SqlProcessingArtifactRepository,
)
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client

pytestmark = pytest.mark.usefixtures("api_db")

NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


def _seed_run(
    session: Session,
    *,
    project_id: UUID,
    actor_id: UUID,
) -> UUID:
    run_id = uuid4()
    session.add(
        ProcessingRunModel(
            id=run_id,
            project_id=project_id,
            pipeline_version="pipeline-v1",
            input_refs=[f"raw/{project_id}/asset"],
            output_refs=[],
            status="CREATED",
            attempt=0,
            retry_count=0,
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    session.flush()
    return run_id


def _seed_artifact(
    session: Session,
    *,
    project_id: UUID,
    run_id: UUID,
    actor_id: UUID,
    object_key: str = "processing-run/p/r/metadata/0000-mp4.json",
) -> ProcessingArtifact:
    artifact = ProcessingArtifact(
        artifact_id=uuid4(),
        project_id=project_id,
        run_id=run_id,
        stage=ProcessingStage.METADATA_EXTRACTION.value,
        attempt=1,
        kind=ProcessingArtifactKind.METADATA.value,
        object_key=object_key,
        schema_version="ddp.processing.metadata/1.0.0",
        content_type="application/json",
        size_bytes=512,
        checksum_sha256="c" * 64,
        source_ref=f"raw/{project_id}/asset",
        created_at=NOW,
        created_by=actor_id,
    )
    SqlProcessingArtifactRepository(session).add(
        ProjectScope(project_id=project_id, actor_id=actor_id),
        artifact,
    )
    session.flush()
    return artifact


def _artifacts_path(ids: ApiIds, run_id: UUID) -> str:
    return f"/api/v1/projects/{ids.project_a}/processing-runs/{run_id}/artifacts"


def test_list_artifacts_returns_registered_rows(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    with factory() as session:
        run_id = _seed_run(session, project_id=ids.project_a, actor_id=ids.editor_a)
        _seed_artifact(session, project_id=ids.project_a, run_id=run_id, actor_id=ids.editor_a)
        session.commit()

    client = make_client(factory, ids.viewer_a)
    response = client.get(_artifacts_path(ids, run_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["kind"] == ProcessingArtifactKind.METADATA.value
    assert item["stage"] == ProcessingStage.METADATA_EXTRACTION.value
    assert item["attempt"] == 1
    assert item["object_key"] == "processing-run/p/r/metadata/0000-mp4.json"
    assert item["schema_version"] == "ddp.processing.metadata/1.0.0"
    assert item["content_type"] == "application/json"
    assert item["size_bytes"] == 512
    assert item["checksum_sha256"] == "c" * 64
    assert item["source_ref"] == f"raw/{ids.project_a}/asset"
    assert body["next_cursor"] is None


def test_list_artifacts_unknown_run_is_404(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client = make_client(factory, ids.viewer_a)
    response = client.get(_artifacts_path(ids, uuid4()))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


def test_list_artifacts_cross_project_run_is_404(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """别的项目的运行：统一 404，不泄露存在性。"""
    factory, ids = api_db
    with factory() as session:
        foreign_run = _seed_run(session, project_id=ids.project_b, actor_id=ids.outsider)
        _seed_artifact(
            session,
            project_id=ids.project_b,
            run_id=foreign_run,
            actor_id=ids.outsider,
        )
        session.commit()

    client = make_client(factory, ids.viewer_a)
    response = client.get(_artifacts_path(ids, foreign_run))

    assert response.status_code == 404


def test_artifacts_endpoint_is_read_only(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """客户端不能登记产物：POST 必须 405。"""
    factory, ids = api_db
    with factory() as session:
        run_id = _seed_run(session, project_id=ids.project_a, actor_id=ids.editor_a)
        session.commit()

    client = make_client(factory, ids.editor_a)
    response = client.post(_artifacts_path(ids, run_id), json={})

    assert response.status_code == 405


def test_list_artifacts_filters_by_stage(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    with factory() as session:
        run_id = _seed_run(session, project_id=ids.project_a, actor_id=ids.editor_a)
        _seed_artifact(session, project_id=ids.project_a, run_id=run_id, actor_id=ids.editor_a)
        session.commit()

    client = make_client(factory, ids.viewer_a)
    filtered = client.get(
        _artifacts_path(ids, run_id),
        params={"stage": ProcessingStage.FORMAT_PARSING.value},
    )
    matching = client.get(
        _artifacts_path(ids, run_id),
        params={"stage": ProcessingStage.METADATA_EXTRACTION.value},
    )

    assert filtered.status_code == 200
    assert filtered.json()["items"] == []
    assert len(matching.json()["items"]) == 1
