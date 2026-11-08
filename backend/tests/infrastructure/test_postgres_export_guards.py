"""PostgreSQL ExportJob 守卫回归：状态机、输入/产物不可变与禁删。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.export_jobs import ExportJobModel
from sqlalchemy import create_engine, delete, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get(
    "DDP_DATABASE_URL",
    "",
)
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64

_EXPECTED_TRIGGERS = frozenset(
    {
        "trg_export_jobs_initial_status",
        "trg_export_jobs_transition",
        "trg_export_jobs_input_immutable",
        "trg_export_jobs_artifacts_immutable",
        "trg_export_jobs_no_delete",
    }
)


@pytest.fixture()
def postgres_engine() -> Iterator[Engine]:
    schema = f"ddp_export_it_{uuid4().hex}"
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
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


def _seed(engine: Engine) -> UUID:
    job_id = uuid4()
    with engine.begin() as connection:
        # 仅用于构造隔离夹具：临时关闭触发器/FK，随后立即恢复并验证真实守卫。
        connection.execute(text("SET session_replication_role = replica"))
        connection.execute(
            text(
                "INSERT INTO export_jobs ("
                "id, project_id, dataset_version_id, status, format, format_version, "
                "exporter_id, exporter_version, mapping_version, config, config_sha256, "
                "idempotency_key, requested_by, output_prefix, attempt, max_attempts, "
                "timeout_seconds, verification_status, version, created_at, updated_at, "
                "created_by, updated_by"
                ") VALUES ("
                ":id, :project_id, :version_id, 'QUEUED', 'LE_ROBOT', 'ddp.lerobot/1.0.0', "
                "'lerobot', '1.0.0', 'mapping/1', '{}', :sha, 'idem-export-0001', "
                ":user_id, 'exports/out', 0, 3, 3600, 'UNVERIFIED', 1, :now, :now, "
                ":user_id, :user_id)"
            ),
            {
                "id": job_id,
                "project_id": uuid4(),
                "version_id": uuid4(),
                "sha": SHA,
                "user_id": uuid4(),
                "now": NOW,
            },
        )
        connection.execute(text("SET session_replication_role = origin"))
    return job_id


def test_postgres_export_job_guards(postgres_engine: Engine) -> None:
    job_id = _seed(postgres_engine)
    with postgres_engine.connect() as connection:
        names = {
            str(row[0])
            for row in connection.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            )
        }
    assert not (_EXPECTED_TRIGGERS - names)

    with postgres_engine.begin() as connection:
        with pytest.raises(IntegrityError, match="export_job_state_transition_invalid"):
            connection.execute(
                update(ExportJobModel)
                .where(ExportJobModel.id == job_id)
                .values(status="SUCCEEDED")
            )

    with postgres_engine.begin() as connection:
        connection.execute(text("SET session_replication_role = replica"))
        connection.execute(
            update(ExportJobModel)
            .where(ExportJobModel.id == job_id)
            .values(status="RUNNING", started_at=NOW, attempt=1)
        )
        connection.execute(text("SET session_replication_role = origin"))
        with pytest.raises(IntegrityError, match="export_job_input_immutable"):
            connection.execute(
                update(ExportJobModel)
                .where(ExportJobModel.id == job_id)
                .values(format_version="ddp.lerobot/9.9.9")
            )

    with postgres_engine.begin() as connection:
        with pytest.raises(IntegrityError, match="export_job_delete_forbidden"):
            connection.execute(delete(ExportJobModel))
