"""迁移 0038：``training_deliveries.requirement_match`` 的升级/回滚。

检查项：
1. 升级后列存在且可空（NULL = 未核对，不得折叠成"通过"）；
2. 回滚后列消失；
3. 存量行升级后该列为 NULL，而不是被回填成某种"通过"标记。

关于"0032 时该列是否已存在"
---------------------------
**不能**断言 0032 时列不存在。迁移 ``0019_stage4_training.py`` 用
``TrainingDeliveryModel.__table__.create()`` 按**活 ORM 元数据**建表，而该模型
现在已包含 ``requirement_match``。因此在全新库上顺序 upgrade 到 0032 时，该列
已经随 0019 建出来了（这是 ``Model.__table__.create()`` 的固有缺陷，已作为
技术债务登记），0038 走"列为空操作"分支。

两条路径都合法，0038 用 inspector 实际状态判断，两向幂等。故本测试只断言迁移
对使用者的承诺：**升级后列存在且可空、回滚后列消失、存量行为 NULL**，不断言
中间态的列集合。
"""

from __future__ import annotations

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


def _columns(db_path: Path) -> dict[str, bool]:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        return {
            column["name"]: bool(column["nullable"])
            for column in inspect(engine).get_columns("training_deliveries")
        }
    finally:
        engine.dispose()


def test_upgrade_adds_nullable_column_and_downgrade_removes_it(tmp_path: Path) -> None:
    db_path = tmp_path / "training_match.db"
    config = _config(db_path)

    command.upgrade(config, "0032")

    command.upgrade(config, "0038")
    columns = _columns(db_path)
    assert "requirement_match" in columns
    assert columns["requirement_match"] is True, "该列必须可空：NULL 表示未核对"

    # 回滚到 0032 后，无论 0038 走的是"新增列"还是"列为空操作"分支，
    # 该列都必须消失——降级承诺的是"本迁移引入的东西被撤销"。
    command.downgrade(config, "0032")
    assert "requirement_match" not in _columns(db_path)


def test_upgrade_is_repeatable_after_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "training_match_roundtrip.db"
    config = _config(db_path)

    command.upgrade(config, "0038")
    command.downgrade(config, "0032")
    command.upgrade(config, "0038")
    assert "requirement_match" in _columns(db_path)


def test_existing_rows_have_null_requirement_match(tmp_path: Path) -> None:
    """存量交付升级后该列为 NULL——读取方不得把 NULL 当成 PASSED。"""
    db_path = tmp_path / "training_match_backfill.db"
    config = _config(db_path)
    command.upgrade(config, "0032")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    project_id = "11111111-1111-1111-1111-111111111111"
    delivery_id = "22222222-2222-2222-2222-222222222222"
    user_id = "33333333-3333-3333-3333-333333333333"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, code, name, created_at, updated_at) "
                    "VALUES (:id, 'p-match', '项目', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": project_id},
            )
            connection.execute(
                text(
                    "INSERT INTO users (id, display_name, created_at, updated_at) "
                    "VALUES (:id, '用户', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO training_deliveries ("
                    "id, project_id, training_request_id, training_request_revision_id, "
                    "dataset_version_id, export_job_id, delivery_note_no, recipient_id, "
                    "item_count, split_counts, format, format_version, status, "
                    "version, created_at, updated_at, created_by, updated_by"
                    ") VALUES (:id, :project, :req, :rev, :ver, :job, 'DN-1', :user, "
                    '0, \'{"train": 0, "validation": 0, "test": 0}\', '
                    "'LE_ROBOT', 'ddp.lerobot/1.0.0', 'DRAFT', 1, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :user, :user)"
                ),
                {
                    "id": delivery_id,
                    "project": project_id,
                    "req": "44444444-4444-4444-4444-444444444444",
                    "rev": "55555555-5555-5555-5555-555555555555",
                    "ver": "66666666-6666-6666-6666-666666666666",
                    "job": "77777777-7777-7777-7777-777777777777",
                    "user": user_id,
                },
            )
    finally:
        engine.dispose()

    command.upgrade(config, "0038")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            value = connection.execute(
                text("SELECT requirement_match FROM training_deliveries WHERE id = :id"),
                {"id": delivery_id},
            ).scalar_one()
    finally:
        engine.dispose()

    assert value is None, "存量交付不得被回填成任何'通过'标记"
