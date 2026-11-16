"""迁移 0044：``episodes.readiness_blockers`` 升级/回滚。

**为什么单独测这条迁移**

自动就绪被拒的原因是这个能力的唯一可查询载体：列没建出来、默认值不对或回滚
删不干净，都会让"为什么它还没就绪"重新变成黑盒。这里显式验证：

- 升级后列存在、NOT NULL、默认 ``[]``；
- 存量行（升级前已存在）在升级后拿到 ``[]``，不是 NULL；
- 读数能正确往返（写入后读回同一结构）；
- 回滚把列删干净，且可反复升降级。
"""

from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _insert_episode(connection, episode_id: str, project_id: str) -> None:
    """插入一条最小可用的 Episode 行（升级前/后都用同一形状）。"""
    connection.execute(
        text(
            "INSERT INTO episodes ("
            "id, project_id, episode_lineage_id, episode_version, code, status,"
            " quality_status, annotation_status, scene_labels, start_time_ns, end_time_ns,"
            " duration_ns, time_domain, time_base, canonical_schema_version, pipeline_id,"
            " pipeline_version, segmentation_rule_version, frame_count, calibration_snapshot,"
            " preview_refs, attempt, max_attempts, version, created_at, updated_at,"
            " created_by, updated_by"
            ") VALUES ("
            ":id, :project_id, :id, 1, 'EP-mig-0044', 'PENDING', 'NOT_CHECKED',"
            " 'NOT_STARTED', '[]', 0, 1000, 1000, 'UTC', '{}', 'ddp.canonical/1.0.0-draft',"
            " 'data_source_import', '1.0.0', 'episode-boundary/1.0.0', 0, '[]', '[]',"
            " 1, 3, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :actor_id, :actor_id"
            ")"
        ),
        {"id": episode_id, "project_id": project_id, "actor_id": project_id},
    )


def _seed_project_and_user(connection) -> None:
    connection.execute(
        text("INSERT INTO projects (id, code, name) VALUES ('p-0044', 'p-0044', '迁移 0044 项目')")
    )
    connection.execute(
        text("INSERT INTO users (id, display_name) VALUES ('p-0044', '迁移 0044 用户')")
    )


def test_upgrade_adds_column_with_empty_default(tmp_path: Path) -> None:
    db_path = tmp_path / "readiness.db"
    config = _config(db_path)
    # 先升到 0043（本迁移之前），插入一条历史行，再升到 head。
    command.upgrade(config, "0043")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            _seed_project_and_user(conn)
            _insert_episode(conn, "e-0044", "p-0044")
        command.upgrade(config, "head")
        columns = {col["name"]: col for col in inspect(engine).get_columns("episodes")}
        assert "readiness_blockers" in columns
        assert columns["readiness_blockers"]["nullable"] is False
        with engine.connect() as conn:
            value = conn.execute(
                text("SELECT readiness_blockers FROM episodes WHERE id = 'e-0044'")
            ).scalar_one()
            # 存量行必须是 []（"尚未尝试"），而不是 NULL。
            assert json.loads(value) == []
            conn.execute(
                text("UPDATE episodes SET readiness_blockers = :v WHERE id = 'e-0044'"),
                {
                    "v": json.dumps(
                        [
                            {
                                "code": "CALIBRATION_MISSING",
                                "message": "多机位 Episode 缺少标定快照",
                                "detail": {"stream_count": 2},
                            }
                        ],
                        ensure_ascii=False,
                    )
                },
            )
            stored = conn.execute(
                text("SELECT readiness_blockers FROM episodes WHERE id = 'e-0044'")
            ).scalar_one()
        assert json.loads(stored)[0]["code"] == "CALIBRATION_MISSING"
    finally:
        engine.dispose()


def test_downgrade_removes_column(tmp_path: Path) -> None:
    db_path = tmp_path / "readiness_down.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert "readiness_blockers" in {
            col["name"] for col in inspect(engine).get_columns("episodes")
        }
    finally:
        engine.dispose()

    command.downgrade(config, "0043")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert "readiness_blockers" not in {
            col["name"] for col in inspect(engine).get_columns("episodes")
        }
    finally:
        engine.dispose()

    # 可反复升降级：回滚后再升级必须重新建出该列。
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert "readiness_blockers" in {
            col["name"] for col in inspect(engine).get_columns("episodes")
        }
    finally:
        engine.dispose()
