"""上传/RawAsset 数据库兜底触发器测试（SQLite；PostgreSQL 由迁移 CI 覆盖）。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.models.ingestion import (
    RawAssetLinkModel,
    RawAssetModel,
    UploadPartModel,
    UploadSessionModel,
)
from app.infrastructure.db.session import create_db_engine, create_session_factory
from sqlalchemy import delete, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SHA_A = f"sha256:{'a' * 64}"


@pytest.fixture()
def guard_db() -> Iterator[tuple[sessionmaker[Session], UUID, UUID]]:
    engine: Engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    project_id, user_id = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_id, code="guard-project", name="兜底项目"),
                User(id=user_id, display_name="兜底用户"),
            ]
        )
        session.commit()
    yield factory, project_id, user_id


def _session_row(project_id: UUID, user_id: UUID, status: str = "CREATED") -> UploadSessionModel:
    return UploadSessionModel(
        id=uuid4(),
        project_id=project_id,
        status=status,
        verification_status="PENDING",
        original_filename="robot.mcap",
        declared_content_type="application/x-mcap",
        declared_size_bytes=8,
        part_size_bytes=4,
        max_size_bytes=64,
        object_key=f"raw/{project_id}/2026/11/08/{uuid4()}/content.mcap",
        multipart_upload_id="upload-guard",
        expires_at=NOW,
        created_by=user_id,
        updated_by=user_id,
    )


def _asset_row(project_id: UUID, user_id: UUID, status: str = "VALIDATING") -> RawAssetModel:
    asset_id = uuid4()
    return RawAssetModel(
        id=asset_id,
        project_id=project_id,
        code=f"RAW-{asset_id.hex[:20]}",
        source_type="UPLOAD",
        original_filename="robot.mcap",
        object_key=f"raw/{project_id}/2026/11/08/{asset_id}/content.mcap",
        size_bytes=8,
        content_type="application/x-mcap",
        format="MCAP",
        sha256=SHA_A,
        status=status,
        ingested_at=NOW,
        storage_class="STANDARD",
        uploaded_by=user_id,
        created_by=user_id,
        updated_by=user_id,
    )


def test_upload_session_initial_and_transition_guard(
    guard_db: tuple[sessionmaker[Session], UUID, UUID],
) -> None:
    factory, project_id, user_id = guard_db
    with factory() as db:
        db.add(_session_row(project_id, user_id, status="COMPLETED"))
        with pytest.raises(IntegrityError, match="upload_session_initial_status_invalid"):
            db.flush()
        db.rollback()

        session = _session_row(project_id, user_id)
        db.add(session)
        db.commit()

        with pytest.raises(IntegrityError, match="upload_session_state_transition_invalid"):
            db.execute(
                update(UploadSessionModel)
                .where(UploadSessionModel.id == session.id)
                .values(status="COMPLETED")
            )
            db.flush()
        db.rollback()

        with pytest.raises(IntegrityError, match="upload_session_delete_forbidden"):
            db.execute(delete(UploadSessionModel).where(UploadSessionModel.id == session.id))
            db.flush()


def test_upload_part_is_append_only(
    guard_db: tuple[sessionmaker[Session], UUID, UUID],
) -> None:
    factory, project_id, user_id = guard_db
    with factory() as db:
        session = _session_row(project_id, user_id)
        db.add(session)
        db.flush()
        db.add(
            UploadPartModel(
                project_id=project_id,
                upload_session_id=session.id,
                part_number=1,
                size_bytes=4,
                etag="etag-1",
                sha256=SHA_A,
                recorded_at=NOW,
            )
        )
        db.commit()

        with pytest.raises(IntegrityError, match="upload_part_immutable"):
            db.execute(
                update(UploadPartModel)
                .where(UploadPartModel.upload_session_id == session.id)
                .values(etag="etag-2")
            )
            db.flush()
        db.rollback()

        with pytest.raises(IntegrityError, match="upload_part_delete_forbidden"):
            db.execute(
                delete(UploadPartModel).where(UploadPartModel.upload_session_id == session.id)
            )
            db.flush()


def test_raw_asset_guards(
    guard_db: tuple[sessionmaker[Session], UUID, UUID],
) -> None:
    factory, project_id, user_id = guard_db
    with factory() as db:
        db.add(_asset_row(project_id, user_id, status="VALID"))
        with pytest.raises(IntegrityError, match="raw_asset_initial_status_invalid"):
            db.flush()
        db.rollback()

        asset = _asset_row(project_id, user_id)
        db.add(asset)
        db.commit()

        with pytest.raises(IntegrityError, match="raw_asset_content_immutable"):
            db.execute(
                update(RawAssetModel)
                .where(RawAssetModel.id == asset.id)
                .values(object_key=asset.object_key + ".tampered")
            )
            db.flush()
        db.rollback()

        db.execute(update(RawAssetModel).where(RawAssetModel.id == asset.id).values(status="VALID"))
        db.commit()

        with pytest.raises(IntegrityError, match="raw_asset_state_transition_invalid"):
            db.execute(
                update(RawAssetModel)
                .where(RawAssetModel.id == asset.id)
                .values(status="VALIDATING")
            )
            db.flush()
        db.rollback()

        with pytest.raises(IntegrityError, match="raw_asset_delete_forbidden"):
            db.execute(delete(RawAssetModel).where(RawAssetModel.id == asset.id))
            db.flush()


def test_raw_asset_link_guards(
    guard_db: tuple[sessionmaker[Session], UUID, UUID],
) -> None:
    factory, project_id, user_id = guard_db
    with factory() as db:
        session = _session_row(project_id, user_id)
        asset = _asset_row(project_id, user_id)
        db.add_all([session, asset])
        db.flush()
        link = RawAssetLinkModel(
            id=uuid4(),
            project_id=project_id,
            asset_id=asset.id,
            upload_session_id=session.id,
            link_type="UPLOAD_SESSION",
            link_status="PENDING_VERIFICATION",
            created_by=user_id,
            created_at=NOW,
        )
        db.add(link)
        db.commit()

        db.execute(
            update(RawAssetLinkModel)
            .where(RawAssetLinkModel.id == link.id)
            .values(link_status="ACTIVE")
        )
        db.commit()

        with pytest.raises(IntegrityError, match="raw_asset_link_state_transition_invalid"):
            db.execute(
                update(RawAssetLinkModel)
                .where(RawAssetLinkModel.id == link.id)
                .values(link_status="PENDING_VERIFICATION")
            )
            db.flush()
        db.rollback()

        with pytest.raises(IntegrityError, match="raw_asset_link_delete_forbidden"):
            db.execute(delete(RawAssetLinkModel).where(RawAssetLinkModel.id == link.id))
            db.flush()
