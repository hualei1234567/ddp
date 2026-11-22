"""``ProcessingArtifact`` 仓储测试（V31-016）：真实 SQLite，不 Mock。

覆盖：按运行列出（含阶段过滤）、唯一键冲突、跨项目隔离、随运行级联删除、
摘要与元数据往返一致。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import app.infrastructure.db.models  # noqa: F401 - 注册全部 ORM 模型
import pytest
from app.application.processing.commands import NewProcessingRun
from app.application.processing.use_cases import ProcessingRunUseCases
from app.domain.identity.errors import CrossProjectAccessError
from app.domain.identity.models import ProjectScope
from app.domain.processing.artifacts import ProcessingArtifact, ProcessingArtifactKind
from app.domain.processing.enums import ProcessingStage
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.processing import SqlProcessingRunRepository
from app.infrastructure.repositories.processing_artifacts import (
    SqlProcessingArtifactRepository,
)
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity

NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    try:
        yield session_factory
    finally:
        engine.dispose()


def _run(
    session: Session,
    *,
    project_id: UUID,
    actor_id: UUID,
) -> UUID:
    use_cases = ProcessingRunUseCases(
        SqlProcessingRunRepository(session),
        None,  # type: ignore[arg-type] - 本测试不写步骤
        session,
        audit=SqlAuditRepository(session),
        outbox=SqlOutboxRepository(session),
        raw_assets=SqlRawAssetRepository(session),
    )
    run = use_cases.create(
        ProjectScope(project_id=project_id, actor_id=actor_id),
        NewProcessingRun(
            pipeline_version="pipeline-v1",
            input_refs=(f"raw/{project_id}/asset",),
        ),
    )
    return run.run_id


def _artifact(
    *,
    project_id: UUID,
    run_id: UUID,
    actor_id: UUID,
    stage: str = ProcessingStage.METADATA_EXTRACTION.value,
    attempt: int = 1,
    kind: str = ProcessingArtifactKind.METADATA.value,
    object_key: str = "processing-run/p/r/metadata/0000-mp4.json",
) -> ProcessingArtifact:
    return ProcessingArtifact(
        artifact_id=uuid4(),
        project_id=project_id,
        run_id=run_id,
        stage=stage,
        attempt=attempt,
        kind=kind,
        object_key=object_key,
        schema_version="ddp.processing.metadata/1.0.0",
        content_type="application/json",
        size_bytes=512,
        checksum_sha256="a" * 64,
        source_ref=f"raw/{project_id}/asset",
        created_at=NOW,
        created_by=actor_id,
    )


def test_add_and_list_round_trip(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    with factory() as session:
        run_id = _run(session, project_id=ids.project_a, actor_id=ids.editor_a)
        repo = SqlProcessingArtifactRepository(session)
        artifact = _artifact(project_id=ids.project_a, run_id=run_id, actor_id=ids.editor_a)
        repo.add(scope, artifact)
        session.commit()

        listed = repo.list(scope, run_id=run_id)

    assert len(listed) == 1
    stored = listed[0]
    assert stored.artifact_id == artifact.artifact_id
    assert stored.stage == ProcessingStage.METADATA_EXTRACTION.value
    assert stored.attempt == 1
    assert stored.kind == ProcessingArtifactKind.METADATA.value
    assert stored.object_key == artifact.object_key
    assert stored.schema_version == "ddp.processing.metadata/1.0.0"
    assert stored.content_type == "application/json"
    assert stored.size_bytes == 512
    assert stored.checksum_sha256 == "a" * 64
    assert stored.source_ref == f"raw/{ids.project_a}/asset"
    assert stored.created_at is not None
    assert stored.created_at.tzinfo is not None


def test_list_filters_by_stage(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    with factory() as session:
        run_id = _run(session, project_id=ids.project_a, actor_id=ids.editor_a)
        repo = SqlProcessingArtifactRepository(session)
        repo.add(
            scope,
            _artifact(
                project_id=ids.project_a,
                run_id=run_id,
                actor_id=ids.editor_a,
                stage=ProcessingStage.METADATA_EXTRACTION.value,
                object_key="processing-run/p/r/metadata/0000-mp4.json",
            ),
        )
        repo.add(
            scope,
            _artifact(
                project_id=ids.project_a,
                run_id=run_id,
                actor_id=ids.editor_a,
                stage=ProcessingStage.FORMAT_PARSING.value,
                kind=ProcessingArtifactKind.CANONICAL.value,
                object_key="processing-run/p/r/canonical/0000-video-stream.json",
            ),
        )
        session.commit()

        metadata_only = repo.list(
            scope,
            run_id=run_id,
            stage=ProcessingStage.METADATA_EXTRACTION.value,
        )
        all_items = repo.list(scope, run_id=run_id)

    assert [item.kind for item in metadata_only] == [ProcessingArtifactKind.METADATA.value]
    assert len(all_items) == 2


def test_duplicate_key_is_rejected(factory: sessionmaker[Session]) -> None:
    """``(run_id, stage, attempt, kind, object_key)`` 唯一：重复登记是错误。"""
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    with factory() as session:
        run_id = _run(session, project_id=ids.project_a, actor_id=ids.editor_a)
        repo = SqlProcessingArtifactRepository(session)
        repo.add(scope, _artifact(project_id=ids.project_a, run_id=run_id, actor_id=ids.editor_a))
        session.commit()

        with pytest.raises(Exception) as caught:
            repo.add(
                scope,
                _artifact(project_id=ids.project_a, run_id=run_id, actor_id=ids.editor_a),
            )
            session.commit()
        assert "UNIQUE" in str(caught.value).upper() or "unique" in str(caught.value).lower()
        session.rollback()


def test_cross_project_read_returns_empty(factory: sessionmaker[Session]) -> None:
    """别的项目的 scope 读不到本项目的产物；不泄露存在性。"""
    ids = seed_identity(factory)
    owner_scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    other_scope = ProjectScope(project_id=ids.project_b, actor_id=ids.outsider)
    with factory() as session:
        run_id = _run(session, project_id=ids.project_a, actor_id=ids.editor_a)
        repo = SqlProcessingArtifactRepository(session)
        repo.add(
            owner_scope,
            _artifact(project_id=ids.project_a, run_id=run_id, actor_id=ids.editor_a),
        )
        session.commit()

        assert repo.list(other_scope, run_id=run_id) == []


def test_add_rejects_cross_project_scope(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    with factory() as session:
        run_id = _run(session, project_id=ids.project_a, actor_id=ids.editor_a)
        repo = SqlProcessingArtifactRepository(session)
        forged = _artifact(project_id=ids.project_a, run_id=run_id, actor_id=ids.editor_a)
        with pytest.raises(CrossProjectAccessError):
            repo.add(
                ProjectScope(project_id=ids.project_b, actor_id=ids.outsider),
                forged,
            )


def test_artifacts_are_deleted_with_run(factory: sessionmaker[Session]) -> None:
    """产物随运行级联删除：运行是产物的生命周期边界。"""
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    with factory() as session:
        run_id = _run(session, project_id=ids.project_a, actor_id=ids.editor_a)
        repo = SqlProcessingArtifactRepository(session)
        repo.add(scope, _artifact(project_id=ids.project_a, run_id=run_id, actor_id=ids.editor_a))
        session.commit()

        from app.infrastructure.db.models.processing import ProcessingRunModel

        session.delete(session.get(ProcessingRunModel, run_id))
        session.commit()

        assert repo.list(scope, run_id=run_id) == []


def test_list_is_ordered_by_created_at_then_id(factory: sessionmaker[Session]) -> None:
    """同一运行内按创建时间稳定排序；同毫秒用主键兜底。"""
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    with factory() as session:
        run_id = _run(session, project_id=ids.project_a, actor_id=ids.editor_a)
        repo = SqlProcessingArtifactRepository(session)
        # 倒序写入、正序读取：验证排序来自数据库而非插入顺序。
        for index in reversed(range(3)):
            artifact = _artifact(
                project_id=ids.project_a,
                run_id=run_id,
                actor_id=ids.editor_a,
                object_key=f"processing-run/p/r/metadata/{index:04d}-mp4.json",
            )
            repo.add(
                scope,
                ProcessingArtifact(
                    artifact_id=artifact.artifact_id,
                    project_id=artifact.project_id,
                    run_id=artifact.run_id,
                    stage=artifact.stage,
                    attempt=artifact.attempt,
                    kind=artifact.kind,
                    object_key=artifact.object_key,
                    schema_version=artifact.schema_version,
                    content_type=artifact.content_type,
                    size_bytes=artifact.size_bytes,
                    checksum_sha256=artifact.checksum_sha256,
                    source_ref=artifact.source_ref,
                    created_at=NOW.replace(microsecond=index),
                    created_by=artifact.created_by,
                ),
            )
        session.commit()

        listed = repo.list(scope, run_id=run_id)

    assert [item.object_key for item in listed] == [
        "processing-run/p/r/metadata/0000-mp4.json",
        "processing-run/p/r/metadata/0001-mp4.json",
        "processing-run/p/r/metadata/0002-mp4.json",
    ]
