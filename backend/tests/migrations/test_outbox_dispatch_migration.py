"""迁移 0030：outbox 的 dispatch 语义与状态约束（R-13）。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _columns(db_path: Path) -> set[str]:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        return {column["name"] for column in inspect(engine).get_columns("outbox_events")}
    finally:
        engine.dispose()


def test_upgrade_adds_dispatch_and_downgrade_removes_it(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox_dispatch.db"
    config = _config(db_path)

    command.upgrade(config, "0029")
    assert "dispatch" not in _columns(db_path)

    command.upgrade(config, "0030")
    assert "dispatch" in _columns(db_path)

    command.downgrade(config, "0029")
    assert "dispatch" not in _columns(db_path)


def test_existing_pending_rows_become_dispatch_on_upgrade(tmp_path: Path) -> None:
    """存量 PENDING 行必须回填 dispatch=true——它们本来就在等消费者。"""
    db_path = tmp_path / "outbox_backfill.db"
    config = _config(db_path)
    command.upgrade(config, "0029")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    project_id = "11111111-1111-1111-1111-111111111111"
    event_id = "22222222-2222-2222-2222-222222222222"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, code, name, created_at, updated_at) "
                    "VALUES (:id, 'p1', '项目', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": project_id},
            )
            connection.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(id, project_id, aggregate_type, aggregate_id, event_type, payload, "
                    " status, attempts, available_at, created_at) "
                    "VALUES (:id, :project, 'RAW_ASSET', :agg, "
                    "'ingestion.raw_asset.validation_requested', "
                    "'{}', 'PENDING', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": event_id, "project": project_id, "agg": event_id},
            )
    finally:
        engine.dispose()

    command.upgrade(config, "0030")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            dispatch = connection.execute(
                text("SELECT dispatch FROM outbox_events WHERE id = :id"), {"id": event_id}
            ).scalar_one()
            # RECORDED 在旧约束下非法；升级后应允许写入并被一致性约束接受。
            connection.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(id, project_id, aggregate_type, aggregate_id, event_type, payload, "
                    " status, dispatch, attempts, available_at, created_at) "
                    "VALUES ('33333333-3333-3333-3333-333333333333', :project, 'DEVICE', "
                    " '33333333-3333-3333-3333-333333333333', 'device.created', '{}', "
                    " 'RECORDED', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"project": project_id},
            )
            # 自相矛盾的行必须被拒。
            try:
                connection.execute(
                    text(
                        "INSERT INTO outbox_events "
                        "(id, project_id, aggregate_type, aggregate_id, event_type, payload, "
                        " status, dispatch, attempts, available_at, created_at) "
                        "VALUES ('44444444-4444-4444-4444-444444444444', :project, 'DEVICE', "
                        " '44444444-4444-4444-4444-444444444444', 'device.created', '{}', "
                        " 'PENDING', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"project": project_id},
                )
            except IntegrityError:
                rejected = True
            else:
                rejected = False
    finally:
        engine.dispose()

    assert dispatch in (1, True)
    assert rejected, "留痕类（dispatch=0）不允许写成 PENDING"
