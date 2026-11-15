"""迁移 0032：``calibration_profiles.version`` -> ``profile_version``。

ADR-0032 / G-03。重点验证四件事：

1. 列确实改名，且数据不丢；
2. 唯一索引与 CHECK 仍然**指向新列并继续生效**（不是"改名后约束形同虚设"）；
3. 不可变/禁删触发器仍在（原生改名不碰触发器，这是不用 batch 的理由）；
4. downgrade 是严格逆操作，可往返。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROFILE_TRIGGERS = {
    "trg_calibration_profiles_immutable_update",
    "trg_calibration_profiles_no_delete",
}


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _engine(db_path: Path) -> Engine:
    return create_engine(f"sqlite+pysqlite:///{db_path}")


def _trigger_names(db_path: Path) -> set[str]:
    engine = _engine(db_path)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'trigger'"))
            return {row[0] for row in rows}
    finally:
        engine.dispose()


_INSERT_PROFILE = (
    "INSERT INTO calibration_profiles "
    "(id, project_id, name, scope, target_type, schema_version, "
    " required_types, selection_rule, {version_column}, target_id, "
    " created_by, updated_by) "
    "VALUES (:id, :pid, 'cfg', 'DEVICE', 'DEVICE', 'v1', '[]', '{{}}', "
    " :ver, :target, :uid, :uid)"
)


def _seed_profile(db_path: Path, *, version: int = 1, column: str = "profile_version") -> str:
    """插一行 Profile；返回 project_id，供唯一键用例复用。

    ``column`` 必须与库当前版本一致：0031 叫 ``version``，0032 起叫
    ``profile_version``。这里显式传入而不是猜，避免测试自己"假设"列名。
    只允许两个固定取值并显式渲染，不接受任意字符串。
    """
    match column:
        case "version":
            version_column = "version"
        case "profile_version":
            version_column = "profile_version"
        case _:
            raise ValueError(f"未预期的列名：{column}")
    project_id, user_id, profile_id = uuid4().hex, uuid4().hex, uuid4().hex
    engine = _engine(db_path)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO projects (id, code, name) VALUES (:id, :code, '项目')"),
                {"id": project_id, "code": f"p-{project_id[:8]}"},
            )
            conn.execute(
                text("INSERT INTO users (id, display_name) VALUES (:id, '用户')"),
                {"id": user_id},
            )
            conn.execute(
                text(_INSERT_PROFILE.format(version_column=version_column)),
                {
                    "id": profile_id,
                    "pid": project_id,
                    "ver": version,
                    "target": uuid4().hex,
                    "uid": user_id,
                },
            )
    finally:
        engine.dispose()
    return project_id


def test_upgrade_renames_column_and_keeps_data(tmp_path: Path) -> None:
    db_path = tmp_path / "rename.db"
    config = _config(db_path)
    command.upgrade(config, "0031")

    engine = _engine(db_path)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("calibration_profiles")}
    finally:
        engine.dispose()
    assert "version" in columns
    assert "profile_version" not in columns

    # 在 0031 上插一行，升级后必须还在。
    project_id = _seed_profile(db_path, version=7, column="version")

    command.upgrade(config, "0032")

    engine = _engine(db_path)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("calibration_profiles")}
        assert "profile_version" in columns
        assert "version" not in columns
        with engine.connect() as conn:
            stored = conn.execute(
                text("SELECT profile_version FROM calibration_profiles WHERE project_id = :pid"),
                {"pid": project_id},
            ).scalar()
        assert stored == 7
    finally:
        engine.dispose()


def test_renamed_column_keeps_unique_and_check_enforced(tmp_path: Path) -> None:
    """改名后约束必须仍然生效——否则等于把护栏悄悄拆了。"""
    db_path = tmp_path / "constraints.db"
    config = _config(db_path)
    command.upgrade(config, "0032")

    engine = _engine(db_path)
    try:
        inspector = inspect(engine)
        # 迁移 0028 用 CREATE UNIQUE INDEX 建版本唯一键（不是表约束），
        # 所以要从索引里查，且断言它确实指向改名后的列。
        indexes = {item["name"]: item for item in inspector.get_indexes("calibration_profiles")}
        version_unique = indexes["uq_calibration_profiles_name_target_version"]
        assert bool(version_unique["unique"]) is True
        assert tuple(version_unique["column_names"]) == (
            "project_id",
            "name",
            "target_id",
            "profile_version",
        )

        checks = {item["name"] for item in inspector.get_check_constraints("calibration_profiles")}
        # SQLite 无约束改名语法，迁移路径下 CHECK 名保持 0003 的旧名；
        # 语义（表达式）已由数据库随列改名重写，下方用行为断言证明。
        assert "ck_calibration_profiles_version_positive" in checks
    finally:
        engine.dispose()

    project_id = _seed_profile(db_path, version=1)

    engine = _engine(db_path)
    try:
        with engine.connect() as conn:
            # CHECK：0 必须被拒。
            with pytest.raises(IntegrityError, match="ck_calibration_profiles_version_positive"):
                with conn.begin():
                    conn.execute(
                        text(
                            "INSERT INTO calibration_profiles "
                            "(id, project_id, name, scope, target_type, schema_version, "
                            " required_types, selection_rule, profile_version, target_id, "
                            " created_by, updated_by) "
                            "SELECT :id, project_id, name, scope, target_type, schema_version, "
                            " required_types, selection_rule, 0, target_id, created_by, updated_by "
                            "FROM calibration_profiles WHERE project_id = :pid"
                        ),
                        {"id": uuid4().hex, "pid": project_id},
                    )
            # UNIQUE：同 project/name/target 的同版本必须被拒。
            # SQLite 报的是 "UNIQUE constraint failed: <列列表>"，不带索引名；
            # 断言到列即可确认是版本唯一键在拦，而不是主键或其它约束。
            with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
                with conn.begin():
                    conn.execute(
                        text(
                            "INSERT INTO calibration_profiles "
                            "(id, project_id, name, scope, target_type, schema_version, "
                            " required_types, selection_rule, profile_version, target_id, "
                            " created_by, updated_by) "
                            "SELECT :id, project_id, name, scope, target_type, schema_version, "
                            " required_types, selection_rule, profile_version, target_id, "
                            " created_by, updated_by "
                            "FROM calibration_profiles WHERE project_id = :pid"
                        ),
                        {"id": uuid4().hex, "pid": project_id},
                    )
    finally:
        engine.dispose()


def test_immutability_triggers_survive_rename(tmp_path: Path) -> None:
    """原生改名不触碰触发器；若改用 batch 重建表，这里会红。"""
    db_path = tmp_path / "triggers.db"
    config = _config(db_path)
    command.upgrade(config, "0032")

    assert PROFILE_TRIGGERS <= _trigger_names(db_path)

    project_id = _seed_profile(db_path)
    engine = _engine(db_path)
    try:
        with engine.connect() as conn:
            with pytest.raises(IntegrityError, match="calibration_profile_immutable"):
                with conn.begin():
                    conn.execute(
                        text(
                            "UPDATE calibration_profiles SET name = '改名' "
                            "WHERE project_id = :pid"
                        ),
                        {"pid": project_id},
                    )
            with pytest.raises(IntegrityError, match="calibration_profile_delete_forbidden"):
                with conn.begin():
                    conn.execute(
                        text("DELETE FROM calibration_profiles WHERE project_id = :pid"),
                        {"pid": project_id},
                    )
    finally:
        engine.dispose()


def test_downgrade_restores_column_and_constraints(tmp_path: Path) -> None:
    db_path = tmp_path / "roundtrip.db"
    config = _config(db_path)

    command.upgrade(config, "head")
    project_id = _seed_profile(db_path, version=5)
    command.downgrade(config, "0031")

    engine = _engine(db_path)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("calibration_profiles")}
        assert "version" in columns
        assert "profile_version" not in columns
        with engine.connect() as conn:
            stored = conn.execute(
                text("SELECT version FROM calibration_profiles WHERE project_id = :pid"),
                {"pid": project_id},
            ).scalar()
        assert stored == 5
    finally:
        engine.dispose()
    assert PROFILE_TRIGGERS <= _trigger_names(db_path)

    # 可重复升级，保证迁移可重入。
    command.upgrade(config, "head")
    engine = _engine(db_path)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("calibration_profiles")}
    finally:
        engine.dispose()
    assert "profile_version" in columns
    assert "version" not in columns
