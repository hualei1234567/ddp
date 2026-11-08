"""PostgreSQL 条件用例：outbox 校验事件使用 SKIP LOCKED 并发抢占。

本地未配置 PostgreSQL 时自动跳过并标注 BLOCKED；CI 真实执行。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.application.ingestion.raw_asset_validation import VALIDATION_EVENT_TYPE
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import OutboxEvent
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.session import create_session_factory
from app.infrastructure.repositories.outbox_validation import SqlOutboxValidationQueue
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateSchema, DropSchema

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get(
    "DDP_DATABASE_URL",
    "",
)

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.fixture()
def postgres_engine() -> Iterator[Engine]:
    schema = f"ddp_outbox_it_{uuid4().hex}"
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


def _seed_event(engine: Engine) -> UUID:
    factory = create_session_factory(engine)
    project_id, user_id, event_id = uuid4(), uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_id, code=f"outbox-{project_id.hex[:8]}", name="outbox"),
                User(id=user_id, display_name="Worker"),
            ]
        )
        session.flush()
        session.add(
            OutboxEvent(
                id=event_id,
                project_id=project_id,
                aggregate_type="RAW_ASSET",
                aggregate_id=uuid4(),
                event_type=VALIDATION_EVENT_TYPE,
                payload={"asset_id": str(uuid4()), "actor_user_id": str(user_id)},
                status="PENDING",
                attempts=0,
                available_at=NOW,
                created_at=NOW,
            )
        )
        session.commit()
    return event_id


def test_postgres_claim_skips_rows_locked_by_another_worker(
    postgres_engine: Engine,
) -> None:
    factory = create_session_factory(postgres_engine)
    event_id = _seed_event(postgres_engine)
    lookup_at = NOW + timedelta(minutes=1)

    with factory() as first_session:
        first = SqlOutboxValidationQueue(first_session).claim_next(
            now=lookup_at,
            event_type=VALIDATION_EVENT_TYPE,
        )
        assert first is not None
        assert first.event_id == event_id

        with factory() as second_session:
            second = SqlOutboxValidationQueue(second_session).claim_next(
                now=lookup_at,
                event_type=VALIDATION_EVENT_TYPE,
            )
            assert second is None

        first_session.rollback()

    with factory() as third_session:
        third = SqlOutboxValidationQueue(third_session).claim_next(
            now=lookup_at,
            event_type=VALIDATION_EVENT_TYPE,
        )
        assert third is not None
        assert third.event_id == event_id
        third_session.rollback()
