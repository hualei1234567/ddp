"""PostgreSQL 训练域守卫回归：请求/授权状态机与禁删。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.training import DatasetGrantModel, TrainingRequestModel
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
_EXPECTED = frozenset(
    {
        "trg_training_requests_initial_status",
        "trg_training_requests_transition",
        "trg_training_requests_immutable",
        "trg_training_requests_terminal_immutable",
        "trg_training_requests_no_delete",
        "trg_dataset_grants_initial_status",
        "trg_dataset_grants_transition",
        "trg_dataset_grants_immutable",
        "trg_dataset_grants_terminal_immutable",
        "trg_dataset_grants_no_delete",
    }
)


@pytest.fixture()
def postgres_engine() -> Iterator[Engine]:
    schema = f"ddp_training_it_{uuid4().hex}"
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


def _seed(engine: Engine) -> tuple[UUID, UUID]:
    request_id, grant_id = uuid4(), uuid4()
    project_id, dataset_id, grantee_project_id, user_id = uuid4(), uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(text("SET session_replication_role = replica"))
        connection.execute(
            text(
                "INSERT INTO training_requests ("
                "id, project_id, code, title, status, priority, owner_id, version, "
                "created_at, updated_at, created_by, updated_by"
                ") VALUES (:id, :project_id, :code, '训练需求', 'DRAFT', 'P1', :user_id, 1, "
                ":now, :now, :user_id, :user_id)"
            ),
            {
                "id": request_id,
                "project_id": project_id,
                "code": f"tr-{request_id.hex[:8]}",
                "user_id": user_id,
                "now": NOW,
            },
        )
        connection.execute(
            text(
                "INSERT INTO dataset_grants ("
                "id, project_id, dataset_id, grantee_project_id, scope, status, reason, "
                "expires_at, version, created_at, updated_at, created_by, updated_by"
                ") VALUES (:id, :project_id, :dataset_id, :grantee_project_id, "
                "'[\"VIEW\"]', 'PENDING_APPROVAL', '联合训练', :expires_at, 1, :now, :now, "
                ":user_id, :user_id)"
            ),
            {
                "id": grant_id,
                "project_id": project_id,
                "dataset_id": dataset_id,
                "grantee_project_id": grantee_project_id,
                "expires_at": NOW,
                "now": NOW,
                "user_id": user_id,
            },
        )
        connection.execute(text("SET session_replication_role = origin"))
    return request_id, grant_id


def test_postgres_training_guards(postgres_engine: Engine) -> None:
    request_id, grant_id = _seed(postgres_engine)
    with postgres_engine.connect() as connection:
        names = {
            str(row[0])
            for row in connection.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            )
        }
    assert not (_EXPECTED - names)

    with postgres_engine.begin() as connection:
        with pytest.raises(IntegrityError, match="training_request_state_transition_invalid"):
            connection.execute(
                update(TrainingRequestModel)
                .where(TrainingRequestModel.id == request_id)
                .values(status="APPROVED")
            )

    with postgres_engine.begin() as connection:
        with pytest.raises(IntegrityError, match="dataset_grant_state_transition_invalid"):
            connection.execute(
                update(DatasetGrantModel)
                .where(DatasetGrantModel.id == grant_id)
                .values(status="REVOKED")
            )

    with postgres_engine.begin() as connection:
        with pytest.raises(IntegrityError, match="training_requests_delete_forbidden"):
            connection.execute(delete(TrainingRequestModel))
    with postgres_engine.begin() as connection:
        with pytest.raises(IntegrityError, match="dataset_grants_delete_forbidden"):
            connection.execute(delete(DatasetGrantModel))
