"""上传会话、分片、RawAsset 与使用关系仓储测试。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import (
    RawAssetFormat,
    RawAssetLinkStatus,
    RawAssetLinkType,
    RawAssetStatus,
)
from app.domain.ingestion.errors import (
    ConflictError,
    UploadPartConflictError,
)
from app.domain.ingestion.models import RawAsset, RawAssetLink, UploadPart, UploadSession
from app.domain.ingestion.validation import build_raw_object_key
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.raw_assets import (
    SqlRawAssetLinkRepository,
    SqlRawAssetRepository,
)
from app.infrastructure.repositories.upload_sessions import (
    SqlUploadPartRepository,
    SqlUploadSessionRepository,
)
from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SHA_A = f"sha256:{'a' * 64}"


@pytest.fixture()
def upload_db() -> Iterator[tuple[sessionmaker[Session], UUID, UUID, UUID, UUID]]:
    engine: Engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    project_a, project_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="project-a", name="项目 A"),
                Project(id=project_b, code="project-b", name="项目 B"),
                User(id=user_a, display_name="甲"),
                User(id=user_b, display_name="乙"),
            ]
        )
        session.commit()
    try:
        yield factory, project_a, project_b, user_a, user_b
    finally:
        engine.dispose()


def _scope(project_id: UUID, actor_id: UUID) -> ProjectScope:
    return ProjectScope(project_id=project_id, actor_id=actor_id)


def _session(project_id: UUID, actor_id: UUID) -> UploadSession:
    asset_id = uuid4()
    return UploadSession(
        upload_session_id=uuid4(),
        project_id=project_id,
        original_filename="robot.mcap",
        declared_content_type="application/x-mcap",
        declared_size_bytes=8,
        part_size_bytes=4,
        max_size_bytes=64,
        object_key=build_raw_object_key(project_id, asset_id, NOW, RawAssetFormat.MCAP),
        multipart_upload_id="upload-1",
        expires_at=NOW + timedelta(hours=1),
        allowed_content_types=("application/x-mcap",),
        format_hint=RawAssetFormat.MCAP,
        asset_id=asset_id,
        created_at=NOW,
        updated_at=NOW,
        created_by=actor_id,
        updated_by=actor_id,
    )


def _asset(
    project_id: UUID,
    actor_id: UUID,
    *,
    sha256: str = SHA_A,
    status: RawAssetStatus = RawAssetStatus.VALIDATING,
) -> RawAsset:
    asset_id = uuid4()
    return RawAsset(
        asset_id=asset_id,
        project_id=project_id,
        code=f"RAW-{asset_id.hex[:20]}",
        source_type="UPLOAD",
        original_filename="robot.mcap",
        object_key=build_raw_object_key(project_id, asset_id, NOW, RawAssetFormat.MCAP),
        size_bytes=8,
        content_type="application/x-mcap",
        format=RawAssetFormat.MCAP,
        sha256=sha256,
        status=status,
        uploaded_by=actor_id,
        ingested_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        created_by=actor_id,
        updated_by=actor_id,
    )


def _seed_asset(
    db: Session,
    assets: SqlRawAssetRepository,
    scope: ProjectScope,
    asset: RawAsset,
) -> None:
    """经合法状态路径种入资产；直接插入终态会触发 DB 兜底。"""
    target = asset.status
    assets.add(scope, replace(asset, status=RawAssetStatus.VALIDATING))
    if target is RawAssetStatus.VALIDATING:
        return
    stages = (
        [RawAssetStatus.VALID, RawAssetStatus.ARCHIVED]
        if target is RawAssetStatus.ARCHIVED
        else [target]
    )
    for state in stages:
        db.execute(
            update(RawAssetModel)
            .where(RawAssetModel.id == asset.asset_id)
            .values(status=state.value)
            .execution_options(synchronize_session=False)
        )
    db.flush()


def test_session_and_part_repository_are_project_scoped(
    upload_db: tuple[sessionmaker[Session], UUID, UUID, UUID, UUID],
) -> None:
    factory, project_a, project_b, user_a, _ = upload_db
    session = _session(project_a, user_a)
    with factory() as db:
        sessions = SqlUploadSessionRepository(db)
        parts = SqlUploadPartRepository(db)
        scope_a = _scope(project_a, user_a)
        scope_b = _scope(project_b, user_a)
        sessions.add(scope_a, session)
        parts.add(
            scope_a,
            UploadPart(
                project_id=project_a,
                upload_session_id=session.upload_session_id,
                part_number=1,
                size_bytes=4,
                etag="etag-1",
                sha256=SHA_A,
                recorded_at=NOW,
            ),
        )
        db.commit()

        assert sessions.get(scope_a, session.upload_session_id) is not None
        assert sessions.get(scope_b, session.upload_session_id) is None
        assert parts.get(scope_b, session.upload_session_id, 1) is None
        updated = sessions.set_progress(
            scope_a,
            session.upload_session_id,
            uploaded_bytes=4,
            uploaded_part_count=1,
            status="UPLOADING",
            now=NOW,
        )
        assert updated.uploaded_bytes == 4
        assert updated.status.value == "UPLOADING"


def test_part_duplicate_returns_conflict(
    upload_db: tuple[sessionmaker[Session], UUID, UUID, UUID, UUID],
) -> None:
    factory, project_a, _, user_a, _ = upload_db
    session = _session(project_a, user_a)
    scope = _scope(project_a, user_a)
    with factory() as db:
        SqlUploadSessionRepository(db).add(scope, session)
        parts = SqlUploadPartRepository(db)
        part = UploadPart(
            project_id=project_a,
            upload_session_id=session.upload_session_id,
            part_number=1,
            size_bytes=4,
            etag="etag-1",
            sha256=SHA_A,
            recorded_at=NOW,
        )
        parts.add(scope, part)
        with pytest.raises(UploadPartConflictError):
            parts.add(scope, part)


def test_active_content_index_is_partial(
    upload_db: tuple[sessionmaker[Session], UUID, UUID, UUID, UUID],
) -> None:
    factory, project_a, project_b, user_a, _ = upload_db
    with factory() as db:
        assets = SqlRawAssetRepository(db)
        scope_a = _scope(project_a, user_a)
        scope_b = _scope(project_b, user_a)

        first = _asset(project_a, user_a, status=RawAssetStatus.VALID)
        _seed_asset(db, assets, scope_a, first)
        db.commit()

        # 同一项目同内容迁移到可用态时触发部分唯一索引。
        with pytest.raises(IntegrityError):
            _seed_asset(
                db,
                assets,
                scope_a,
                _asset(project_a, user_a, status=RawAssetStatus.ARCHIVED),
            )
        db.rollback()

        # REJECTED 不占用内容唯一键，可保留多个失败记录。
        _seed_asset(db, assets, scope_a, _asset(project_a, user_a, status=RawAssetStatus.REJECTED))
        _seed_asset(db, assets, scope_a, _asset(project_a, user_a, status=RawAssetStatus.REJECTED))

        # 跨项目同内容不去重。
        cross = _asset(project_b, user_a, status=RawAssetStatus.VALID)
        _seed_asset(db, assets, scope_b, cross)
        assert assets.find_active_by_content(scope_a, sha256=SHA_A, size_bytes=8) is not None
        assert assets.get(scope_b, cross.asset_id) is not None
        db.commit()

        # SQLite 下 Repository 的 SAVEPOINT 会先提交暂存行（PG 行为正确），
        # 因此冲突尝试会留下一条 VALIDATING 记录；可用资产仍只有一个。
        assert db.query(RawAssetModel).count() == 5
        assert (
            db.query(RawAssetModel)
            .filter(RawAssetModel.project_id == project_a, RawAssetModel.status == "VALID")
            .count()
            == 1
        )


def test_raw_asset_link_session_uniqueness(
    upload_db: tuple[sessionmaker[Session], UUID, UUID, UUID, UUID],
) -> None:
    factory, project_a, _, user_a, _ = upload_db
    session = _session(project_a, user_a)
    asset = _asset(project_a, user_a, status=RawAssetStatus.VALID)
    scope = _scope(project_a, user_a)
    with factory() as db:
        SqlUploadSessionRepository(db).add(scope, session)
        _seed_asset(db, SqlRawAssetRepository(db), scope, asset)
        links = SqlRawAssetLinkRepository(db)
        link = RawAssetLink(
            link_id=uuid4(),
            project_id=project_a,
            asset_id=asset.asset_id,
            collection_task_id=None,
            batch_id=None,
            upload_session_id=session.upload_session_id,
            link_type=RawAssetLinkType.UPLOAD_SESSION,
            link_status=RawAssetLinkStatus.PENDING_VERIFICATION,
            created_by=user_a,
            created_at=NOW,
        )
        links.add(scope, link)
        assert links.exists_for_session(
            scope,
            asset_id=asset.asset_id,
            upload_session_id=session.upload_session_id,
            link_type=RawAssetLinkType.UPLOAD_SESSION.value,
        )
        with pytest.raises(ConflictError):
            links.add(
                scope,
                RawAssetLink(
                    link_id=uuid4(),
                    project_id=project_a,
                    asset_id=asset.asset_id,
                    upload_session_id=session.upload_session_id,
                    link_type=RawAssetLinkType.UPLOAD_SESSION,
                    link_status=RawAssetLinkStatus.PENDING_VERIFICATION,
                    created_by=user_a,
                    created_at=NOW,
                ),
            )
