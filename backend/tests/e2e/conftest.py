"""端到端链路夹具：默认真实 SQLite；配置 PostgreSQL 时在随机 schema 复跑。

与 ``tests/api/conftest.py`` 的区别只有作用域（module 级，链路只搭一次）和
可选 PostgreSQL。身份基线、客户端装配全部复用后者，避免两套夹具分叉。

``create_all`` 不是迁移验收：迁移的升级/回滚由 ``tests/migrations`` 负责；
这里验证的是业务链路，所以两边用同一套 ORM 元数据即可。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import uuid4

import pytest
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import create_db_engine, create_session_factory
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from tests.api.conftest import ApiIds, seed_identity

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get(
    "DDP_E2E_POSTGRES_URL", ""
)


@dataclass(frozen=True)
class E2EEnv:
    factory: sessionmaker[Session]
    ids: ApiIds
    backend: str


def _build_sqlite() -> tuple[Engine, None, None]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    return engine, None, None


def _build_postgres(url: str) -> tuple[Engine, Engine, str]:
    schema = f"ddp_e2e_{uuid4().hex}"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    return engine, admin, schema


@pytest.fixture(scope="module")
def e2e_env() -> Iterator[E2EEnv]:
    if _POSTGRES_URL.startswith("postgresql"):
        engine, admin, schema = _build_postgres(_POSTGRES_URL)
        backend = "postgresql"
    else:
        engine, admin, schema = _build_sqlite()
        backend = "sqlite"
    print(f"[e2e] backend={backend}")
    factory = create_session_factory(engine)
    ids = seed_identity(factory)
    try:
        yield E2EEnv(factory=factory, ids=ids, backend=backend)
    finally:
        engine.dispose()
        if admin is not None and schema is not None:
            with admin.connect() as connection:
                connection.execute(DropSchema(schema, cascade=True, if_exists=True))
            admin.dispose()
