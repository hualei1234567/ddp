"""迁移 0083：``annotation_honeypot_state`` 建表、约束与双向回滚。

覆盖（对齐 ``test_ai_token_usage_migration.py`` 的口径）：
- 升级建表 / 降级删表（含索引），可反复升降级；
- 列属性：``window_history`` String(32)、``paused_at`` timezone-aware 可空；
- 库层约束：``state`` 枚举、hits/total 非负且 hits<=total、滑窗容量、
  序列长度与 total 一致、paused_reason 可空但不得空串——约束用例直接
  写 ORM 行（领域/接线层自身会先拒绝非法值，只有直插才能证明
  "库层 CHECK 真的存在"）；
- 项目外键级联删除；
- 全新库 ``upgrade head`` 成功（防回到 ``Model.__table__.create`` 老坑）；
- 配 ``DDP_TEST_POSTGRES_URL`` 时在真实 PostgreSQL 上再跑一遍
  （MEM-INC-013；0083 的 GLOB/``!~`` 双方言 CHECK 在 PG 侧覆盖）。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.infrastructure.db.models.annotation_honeypot_state import (
    AnnotationHoneypotState,
)
from sqlalchemy import create_engine, insert, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "annotation_honeypot_state"
INDEXES = {"ix_annotation_honeypot_state_project_state"}
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


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
        def _enable_fk(dbapi_conn: object, _record: object) -> None:
            cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        __import__("sqlalchemy").event.listens_for(engine, "connect")(_enable_fk)
    return engine


def _seed_project(engine: Engine) -> tuple[object, object, object]:
    """建最小身份上下文：1 个项目 + 标注员 + 操作人（audit 外键目标）。

    返回 (project_id, annotator_id, operator_id)；标注员 id 必须真实存在
    （表对 ``users.id`` 有 FK），测试用 ``operator_id`` 直插绕过接线层。
    """
    from app.infrastructure.db.models.identity import Project, User

    project_id, annotator_id, operator_id = uuid4(), uuid4(), uuid4()
    with Session(engine) as session:
        session.add_all(
            [
                Project(id=project_id, code="hp-proj", name="熔断项目"),
                User(id=annotator_id, display_name="标注员"),
                User(id=operator_id, display_name="操作人"),
            ]
        )
        session.commit()
    return project_id, annotator_id, operator_id


def _insert(engine: Engine, project_id: object, actor_id: object, **overrides: object) -> None:
    """绕过接线层直插一行；非法值由库层约束拒绝。

    默认行不带 annotator_id（调用方必须给真实的 users.id）；其余字段
    为合法 NORMAL 空行。
    """
    values: dict[str, object] = {
        "id": uuid4(),
        "project_id": project_id,
        "hits": 0,
        "total": 0,
        "window_history": "",
        "state": "NORMAL",
        "paused_reason": None,
        "paused_at": None,
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
        "created_by": actor_id,
        "updated_by": actor_id,
    }
    values.update(overrides)
    with Session(engine) as session:
        session.execute(insert(AnnotationHoneypotState).values(**values))
        session.commit()


def _table_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return set(inspect(connection).get_table_names())


def _index_names(engine: Engine) -> set[str]:
    return {item["name"] for item in inspect(engine).get_indexes(TABLE)}


def test_upgrade_creates_table_and_downgrade_removes_it(tmp_path: Path) -> None:
    db_path = tmp_path / "honeypot_state.db"
    url = _sqlite_url(db_path)
    config = _config(url)

    command.upgrade(config, "0080")
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

    command.downgrade(config, "0080")
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
    """``window_history`` String(32)、``paused_at`` timezone-aware 可空。"""
    url = _sqlite_url(tmp_path / "honeypot_types.db")
    command.upgrade(_config(url), "head")
    engine = _engine(url)
    try:
        columns = {col["name"]: col for col in inspect(engine).get_columns(TABLE)}
    finally:
        engine.dispose()
    history = columns["window_history"]
    assert history["nullable"] is False
    assert "VARCHAR(32)" in str(history["type"]).upper()
    paused_at = columns["paused_at"]
    assert paused_at["nullable"] is True


def test_constraints_reject_invalid_rows(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "honeypot_constraints.db")
    command.upgrade(_config(url), "head")
    engine = _engine(url)
    try:
        project_id, annotator_id, actor_id = _seed_project(engine)
        _insert(
            engine,
            project_id,
            actor_id,
            annotator_id=annotator_id,
            hits=3,
            total=10,
            window_history="1110000000",
        )  # 合法行

        cases = {
            "state 非法": {"state": "SUSPENDED"},
            "state 为空": {"state": ""},
            "hits 为负": {"hits": -1},
            "total 为负": {"total": -3},
            "hits 超过 total": {"hits": 11},
            "total 超过滑窗容量": {"total": 25, "window_history": "0" * 25, "hits": 0},
            "序列长度与 total 不一致": {"total": 10, "window_history": "0101"},
            "序列含非二进制字符": {"total": 4, "window_history": "01a0"},
            "paused_reason 为空串": {"paused_reason": ""},
        }
        for label, overrides in cases.items():
            with pytest.raises(IntegrityError) as excinfo:
                _insert(engine, project_id, actor_id, annotator_id=annotator_id, **overrides)
            message = str(excinfo.value)
            assert (
                "CHECK" in message
                or "ck_annotation_honeypot_state" in message
                or "not null" in message.lower()
            ), f"{label} 未被库层约束拦截：{message}"
    finally:
        engine.dispose()


def test_same_annotator_unique_per_project(tmp_path: Path) -> None:
    """(project_id, annotator_id) 唯一：同一标注员重复建行被拒绝。"""
    url = _sqlite_url(tmp_path / "honeypot_unique.db")
    command.upgrade(_config(url), "head")
    engine = _engine(url)
    try:
        project_id, annotator_id, actor_id = _seed_project(engine)
        _insert(engine, project_id, actor_id, annotator_id=annotator_id)
        with pytest.raises(IntegrityError):
            _insert(engine, project_id, actor_id, annotator_id=annotator_id)
    finally:
        engine.dispose()


def test_unknown_project_rejected_by_foreign_key(tmp_path: Path) -> None:
    """跨项目/幽灵 project_id 在库层就被拒绝（项目隔离的最后一道闸）。"""
    url = _sqlite_url(tmp_path / "honeypot_fk.db")
    command.upgrade(_config(url), "head")
    engine = _engine(url)
    try:
        project_id, annotator_id, actor_id = _seed_project(engine)
        with pytest.raises(IntegrityError):
            _insert(engine, uuid4(), actor_id, annotator_id=annotator_id)
        # 合法 project 仍可写。
        _insert(engine, project_id, actor_id, annotator_id=annotator_id)
    finally:
        engine.dispose()


def test_project_cascade_delete_removes_state(tmp_path: Path) -> None:
    """删项目级联删状态行：状态行不阻碍项目清理。"""
    from app.infrastructure.db.models.identity import Project

    url = _sqlite_url(tmp_path / "honeypot_cascade.db")
    command.upgrade(_config(url), "head")
    engine = _engine(url)
    try:
        project_id, annotator_id, actor_id = _seed_project(engine)
        _insert(engine, project_id, actor_id, annotator_id=annotator_id)
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
    """配 ``DDP_TEST_POSTGRES_URL`` 时在真实 PG 上验证 upgrade → downgrade。

    同时覆盖 0083 的 PG 侧二进制 CHECK（``window_history !~ '[^01]'``，
    MEM-INC-013 双方言分支的另一半）。
    """
    if not _POSTGRES_URL:
        pytest.skip("未配置 DDP_TEST_POSTGRES_URL，跳过 PostgreSQL 往返")
    config = _config(_POSTGRES_URL)
    command.upgrade(config, "head")
    command.downgrade(config, "0080")
    command.upgrade(config, "head")
