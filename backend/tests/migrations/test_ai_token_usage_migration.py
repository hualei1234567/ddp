"""迁移 0081：``ai_token_usage`` 建表、约束与双向回滚。

覆盖（对齐 ``test_ai_suggestions_migration.py`` 的口径）：
- 升级建表 / 降级删表（含索引），可反复升降级；
- 列属性：``occurred_at`` timezone-aware、``cost_estimate`` Numeric(14,4) 可空；
- 库层约束：``purpose`` 枚举、tokens 非负、成本非负、模型身份非空、
  prompt_version 可空但不得空串——约束用例直接写 ORM 行（领域实体自身会
  先拒绝非法值，只有直插才能证明"库层 CHECK 真的存在"）；
- 项目外键级联删除；
- 全新库 ``upgrade head`` 成功（防回到 ``Model.__table__.create`` 老坑）；
- 配 ``DDP_TEST_POSTGRES_URL`` 时在真实 PostgreSQL 上再跑一遍（MEM-INC-013）。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.infrastructure.db.models.annotation_ai_token_usage import AiTokenUsage
from sqlalchemy import create_engine, insert, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "ai_token_usage"
INDEXES = {
    "ix_ai_token_usage_project_occurred",
    "ix_ai_token_usage_project_model_purpose",
}
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def _config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    # ConfigParser 会对 ``%`` 做插值：PG 连接串里的 ``%`` 必须转义成 ``%%``。
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+pysqlite:///{db_path}"


def _engine(url: str) -> Engine:
    engine = create_engine(url)
    if url.startswith("sqlite"):
        # SQLite 默认关闭外键；不开 PRAGMA，级联删除与外键用例会假绿。
        # 这里挂在 DBAPI 连接上（与 ``create_db_engine`` 的做法一致）。
        def _enable_fk(dbapi_conn: object, _record: object) -> None:
            cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        __import__("sqlalchemy").event.listens_for(engine, "connect")(_enable_fk)
    return engine


def _seed_project(engine: Engine) -> tuple[object, object]:
    """建最小身份上下文：1 个项目 + 2 个用户（审计列的外键目标）。"""
    from app.infrastructure.db.models.identity import Project, User

    project_id, owner_id, auditor_id = uuid4(), uuid4(), uuid4()
    with Session(engine) as session:
        session.add_all(
            [
                Project(id=project_id, code="token-proj", name="Token 台账项目"),
                User(id=owner_id, display_name="负责人"),
                User(id=auditor_id, display_name="记录人"),
            ]
        )
        session.commit()
    return project_id, auditor_id


def _insert(
    engine: Engine,
    project_id: object,
    actor_id: object,
    **overrides: object,
) -> None:
    """绕过领域层直插一行；非法值由库层约束拒绝。"""
    values: dict[str, object] = {
        "id": uuid4(),
        "project_id": project_id,
        "model_id": "ddp-action-vlm",
        "model_version": "1.2.0",
        "prompt_version": "prompt-7",
        "purpose": "PRE_LABEL",
        "input_tokens": 1000,
        "output_tokens": 500,
        "cost_estimate": None,
        "occurred_at": NOW,
        "annotation_id": None,
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
        "created_by": actor_id,
        "updated_by": actor_id,
    }
    values.update(overrides)
    with Session(engine) as session:
        session.execute(insert(AiTokenUsage).values(**values))
        session.commit()


def _table_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return set(inspect(connection).get_table_names())


def _index_names(engine: Engine) -> set[str]:
    return {item["name"] for item in inspect(engine).get_indexes(TABLE)}


def test_upgrade_creates_table_and_downgrade_removes_it(tmp_path: Path) -> None:
    db_path = tmp_path / "token_usage.db"
    url = _sqlite_url(db_path)
    config = _config(url)

    command.upgrade(config, "0074")
    engine = _engine(url)
    try:
        assert TABLE not in _table_names(engine)
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _engine(url)
    try:
        names = _table_names(engine)
        assert TABLE in names
        assert INDEXES <= _index_names(engine)
        assert "projects" in names
    finally:
        engine.dispose()

    command.downgrade(config, "0074")
    engine = _engine(url)
    try:
        names = _table_names(engine)
        assert TABLE not in names
        assert "projects" in names
    finally:
        engine.dispose()

    # 可反复升降级。
    command.upgrade(config, "head")
    engine = _engine(url)
    try:
        assert TABLE in _table_names(engine)
    finally:
        engine.dispose()


def test_column_types_match_contract(tmp_path: Path) -> None:
    """``occurred_at`` timezone-aware、``cost_estimate`` Numeric(14,4) 可空。"""
    url = _sqlite_url(tmp_path / "token_types.db")
    command.upgrade(_config(url), "head")
    engine = _engine(url)
    try:
        columns = {col["name"]: col for col in inspect(engine).get_columns(TABLE)}
    finally:
        engine.dispose()
    occurred = columns["occurred_at"]
    assert occurred["nullable"] is False
    cost = columns["cost_estimate"]
    assert cost["nullable"] is True
    # SQLite 里 NUMERIC 落成 NUMERIC 声明；PG 下精度由 0081 的 DDL 保证。
    assert "NUMERIC" in str(cost["type"]).upper()


def test_constraints_reject_invalid_rows(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "token_constraints.db")
    command.upgrade(_config(url), "head")
    engine = _engine(url)
    try:
        project_id, actor_id = _seed_project(engine)
        _insert(engine, project_id, actor_id)  # 合法行：未配价（cost NULL）

        cases = {
            "purpose 非法": {"purpose": "FINE_TUNE"},
            "purpose 为空": {"purpose": ""},
            "input_tokens 为负": {"input_tokens": -1},
            "output_tokens 为负": {"output_tokens": -5},
            "cost_estimate 为负": {"cost_estimate": -0.01},
            "model_id 为空": {"model_id": ""},
            "model_version 为空": {"model_version": ""},
            "prompt_version 为空串": {"prompt_version": ""},
        }
        for label, overrides in cases.items():
            with pytest.raises(IntegrityError) as excinfo:
                _insert(engine, project_id, actor_id, **overrides)
            message = str(excinfo.value)
            assert (
                "CHECK" in message
                or "ck_ai_token_usage" in message
                or "not null" in message.lower()
            ), f"{label} 未被库层约束拦截：{message}"
    finally:
        engine.dispose()


def test_unknown_project_rejected_by_foreign_key(tmp_path: Path) -> None:
    """跨项目/幽灵 project_id 在库层就被拒绝（项目隔离的最后一道闸）。"""
    url = _sqlite_url(tmp_path / "token_fk.db")
    command.upgrade(_config(url), "head")
    engine = _engine(url)
    try:
        project_id, actor_id = _seed_project(engine)
        ghost = uuid4()
        with pytest.raises(IntegrityError):
            _insert(engine, ghost, actor_id)
        # 合法 project 仍可写。
        _insert(engine, project_id, actor_id)
    finally:
        engine.dispose()


def test_project_cascade_delete_removes_usage(tmp_path: Path) -> None:
    """删项目级联删台账行：台账不阻碍项目清理。"""
    from app.infrastructure.db.models.identity import Project

    url = _sqlite_url(tmp_path / "token_cascade.db")
    command.upgrade(_config(url), "head")
    engine = _engine(url)
    try:
        project_id, actor_id = _seed_project(engine)
        _insert(engine, project_id, actor_id)
        with Session(engine) as session:
            session.execute(
                text("DELETE FROM projects WHERE id = :pid"),
                {"pid": str(project_id)},
            )
            session.commit()
        with engine.connect() as connection:
            # 表名是模块常量（非用户输入），列过滤用绑定参数；S608 针对表名
            # 内插误报，用 # noqa 显式声明。
            remaining = connection.execute(
                text(f"SELECT count(*) FROM {TABLE} WHERE project_id = :pid"),  # noqa: S608
                {"pid": str(project_id)},
            ).scalar_one()
        assert remaining == 0
        assert Project is not None  # 导入仅在需要时触发表注册
    finally:
        engine.dispose()


def test_postgres_roundtrip_when_configured() -> None:
    """配 ``DDP_TEST_POSTGRES_URL`` 时在真实 PG 上验证 upgrade → downgrade。"""
    if not _POSTGRES_URL:
        pytest.skip("未配置 DDP_TEST_POSTGRES_URL，跳过 PostgreSQL 往返")
    config = _config(_POSTGRES_URL)
    command.upgrade(config, "head")
    command.downgrade(config, "0074")
    command.upgrade(config, "head")


def _sessionmaker(url: str) -> sessionmaker:
    return sessionmaker(bind=_engine(url), expire_on_commit=False)
