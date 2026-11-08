"""SQLAlchemy 引擎与会话。

- 引擎延迟创建：导入本模块不会连接数据库，保护 /health 等无 DB 依赖的接口；
- 连接池参数全部来自 ``Settings``，不在代码里写死；
- 会话依赖只负责回滚与关闭，事务提交由 application 用例显式完成。
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings


def _enable_sqlite_foreign_keys(
    dbapi_connection: Any,
    _connection_record: Any,
) -> None:
    """SQLite 默认关闭外键；每条新连接都必须显式开启。

    使用 DB-API 游标而非 ORM，确保连接池中复用的连接同样生效。
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_db_engine(
    url: str,
    *,
    pool_size: int,
    max_overflow: int,
    pool_timeout_seconds: int,
    connect_timeout_seconds: int,
    echo: bool = False,
) -> Engine:
    """按 URL 创建同步引擎；SQLite 仅用于测试与本地轻量验证。"""
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        if ":memory:" in url:
            # 内存库必须在多连接间共享同一实例。
            engine = create_engine(
                url,
                connect_args=connect_args,
                poolclass=StaticPool,
                echo=echo,
            )
        else:
            engine = create_engine(url, connect_args=connect_args, echo=echo)
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        return engine

    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout_seconds,
        connect_args={"connect_timeout": connect_timeout_seconds},
        echo=echo,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_db_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout_seconds=settings.database_pool_timeout_seconds,
        connect_timeout_seconds=settings.database_connect_timeout_seconds,
        echo=settings.database_echo,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return create_session_factory(get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI 会话依赖。

    成功返回时不会自动提交；需要写事务的 application 用例必须显式
    ``commit()``，否则会话关闭时数据会被回滚。
    """
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
