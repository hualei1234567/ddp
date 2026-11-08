"""并发 complete 的 PostgreSQL 真实并发条件用例。

未配置 ``DDP_TEST_POSTGRES_URL`` / ``DDP_DATABASE_URL`` 时跳过并标注 BLOCKED。
覆盖验收：同一会话两个 complete 并发时，结果为 [202, 202(重放)] 或
[202, 409]，绝不出现 503；最终只有一个可用资产。
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.domain.identity.models import ProjectRole
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import identity as _identity  # noqa: F401
from app.infrastructure.db.models import ingestion as _ingestion  # noqa: F401
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.models.ingestion import RawAssetModel, UploadSessionModel
from app.infrastructure.db.session import create_session_factory
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from tests.api.conftest import ApiIds, make_client
from tests.fakes_s3 import FakeMultipartStorage

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get(
    "DDP_DATABASE_URL",
    "",
)
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
MCAP_BYTES = b"\x89MCAP0\r\n"


@dataclass(frozen=True)
class _PgHarness:
    factory: sessionmaker[Session]
    ids: ApiIds
    fake: FakeMultipartStorage
    settings: Settings


@pytest.fixture()
def pg_harness() -> Iterator[_PgHarness]:
    schema = f"ddp_310_it_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    engine: Engine | None = None
    try:
        with admin.connect() as connection:
            connection.execute(CreateSchema(schema))
        engine = create_engine(
            _POSTGRES_URL,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        Base.metadata.create_all(engine)
        factory = create_session_factory(engine)

        project_a, project_b = uuid4(), uuid4()
        editor_a, owner_a, viewer_a, outsider = uuid4(), uuid4(), uuid4(), uuid4()
        ids = ApiIds(project_a, project_b, editor_a, owner_a, viewer_a, outsider)
        with factory() as session:
            session.add_all(
                [
                    Project(id=project_a, code=f"p-a-{project_a.hex[:8]}", name="项目 A"),
                    Project(id=project_b, code=f"p-b-{project_b.hex[:8]}", name="项目 B"),
                    User(id=editor_a, display_name="编辑者"),
                    User(id=owner_a, display_name="负责人"),
                    User(id=viewer_a, display_name="只读用户"),
                    User(id=outsider, display_name="项目外用户"),
                ]
            )
            session.flush()
            session.add_all(
                [
                    ProjectMember(
                        project_id=project_a,
                        user_id=editor_a,
                        role=ProjectRole.EDITOR.value,
                    ),
                    ProjectMember(
                        project_id=project_a,
                        user_id=owner_a,
                        role=ProjectRole.OWNER.value,
                    ),
                    ProjectMember(
                        project_id=project_a,
                        user_id=viewer_a,
                        role=ProjectRole.VIEWER.value,
                    ),
                    ProjectMember(
                        project_id=project_b,
                        user_id=owner_a,
                        role=ProjectRole.OWNER.value,
                    ),
                ]
            )
            session.commit()

        settings = Settings(
            env="local",
            upload_part_size_bytes=4,
            upload_max_size_bytes=64,
            upload_session_ttl_hours=1,
            upload_url_ttl_seconds=60,
        )
        yield _PgHarness(factory, ids, FakeMultipartStorage(), settings)
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


def _create_and_upload(harness: _PgHarness) -> dict[str, Any]:
    client = make_client(
        harness.factory,
        harness.ids.editor_a,
        object_storage=harness.fake,
        settings_override=harness.settings,
    )
    project = harness.ids.project_a
    created = client.post(
        f"/api/v1/projects/{project}/upload-sessions",
        json={
            "original_filename": "sample.mcap",
            "declared_content_type": "application/x-mcap",
            "declared_size_bytes": len(MCAP_BYTES),
            "format_hint": "MCAP",
        },
    )
    assert created.status_code == 201, created.text
    session = created.json()
    materials = []
    part_size = harness.settings.upload_part_size_bytes
    for index, start in enumerate(range(0, len(MCAP_BYTES), part_size), start=1):
        material = harness.fake.put_part(
            session["object_key"], index, MCAP_BYTES[start : start + part_size]
        )
        ack = client.post(
            f"/api/v1/projects/{project}/upload-sessions/"
            f"{session['upload_session_id']}/parts/{index}/ack",
            json={
                "etag": material.etag,
                "size_bytes": material.size_bytes,
                "sha256": material.sha256,
            },
        )
        assert ack.status_code == 200, ack.text
        materials.append(material)
    detail = client.get(
        f"/api/v1/projects/{project}/upload-sessions/{session['upload_session_id']}"
    )
    assert detail.status_code == 200, detail.text
    body = {
        "expected_sha256": f"sha256:{hashlib.sha256(MCAP_BYTES).hexdigest()}",
        "expected_size_bytes": len(MCAP_BYTES),
        "parts": [
            {
                "part_number": item.part_number,
                "etag": item.etag,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            }
            for item in materials
        ],
    }
    return {
        "session_id": session["upload_session_id"],
        "version": detail.json()["version"],
        "body": body,
        "project": project,
    }


def test_postgres_concurrent_complete_is_202_or_409_never_503(pg_harness: _PgHarness) -> None:
    prepared = _create_and_upload(pg_harness)
    barrier = threading.Barrier(2)

    def _send(key: str) -> Any:
        client = make_client(
            pg_harness.factory,
            pg_harness.ids.editor_a,
            object_storage=pg_harness.fake,
            settings_override=pg_harness.settings,
        )
        barrier.wait(timeout=10)
        return client.post(
            f"/api/v1/projects/{prepared['project']}/upload-sessions/"
            f"{prepared['session_id']}/complete",
            json=prepared["body"],
            headers={
                "If-Match": f'"{prepared["version"]}"',
                "Idempotency-Key": key,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_send, "pg-complete-key-0001")
        second = executor.submit(_send, "pg-complete-key-0002")
        responses = [first.result(timeout=30), second.result(timeout=30)]

    statuses = sorted(response.status_code for response in responses)
    assert 503 not in statuses, [response.text for response in responses]
    assert statuses == [202, 202] or statuses == [202, 409], [
        response.text for response in responses
    ]
    assert any(response.status_code == 202 for response in responses)

    with pg_harness.factory() as session:
        status = session.scalar(
            select(UploadSessionModel.status).where(
                UploadSessionModel.id == UUID(prepared["session_id"])
            )
        )
        asset_count = session.scalar(select(func.count()).select_from(RawAssetModel))
    assert status == "COMPLETED"
    assert asset_count == 1
