"""迁移 0088：``training_run_evidence`` 训练证据表的建表、约束与双向回滚。

覆盖（对齐 ``test_webhook_outbox_migration.py`` 的口径）：
- 升级建表 / 降级删表（含两个索引），可反复升降级；
- 列属性：``runtime_versions``/``metrics_summary`` JSON、``run_kind`` String(16)、
  ``exit_code`` Integer、``started_at``/``finished_at``/``created_at``
  timezone-aware；
- 库层约束：``run_kind`` 枚举（SMOKE/REAL）、``steps >= 1``、``seed >= 0``、
  ``config_sha256``/``checkpoint_sha256`` 的 ``sha256:`` 前缀格式；
- 可空面：``checkpoint_sha256``/``metrics_summary``/``finished_at`` 为 NULL 不强制；
- ``exit_code`` 开放域：0 与非 0（含负数，信号杀死）都接受，不设枚举 CHECK；
- 项目外键级联删除；引用外键 RESTRICT（有证据行时拒绝删引用，防静默丢证据）；
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
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "training_run_evidence"
INDEXES = {
    "ix_training_run_evidence_project_created_at",
    "ix_training_run_evidence_reference_id",
}
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")
NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
_SHA = "sha256:" + "a" * 64

#: 引用行即 run 记录（ADR-0036）：证据挂在引用之下。引用的 delivery/revision/
#: version 外键不在本迁移范围，种子行用占位 id（FK 在种子连接上临时关闭）。
_REF_INSERT = text(
    "INSERT INTO external_training_references ("
    "id, project_id, adapter_id, adapter_version, training_delivery_id,"
    " training_request_revision_id, dataset_version_id, external_run_id,"
    " external_status, submitted_at, last_synced_at,"
    " correlation_id, idempotency_key, version, created_at, updated_at,"
    " created_by, updated_by"
    ") VALUES ("
    ":id, :project_id, 'lerobot', '1.0.0', :delivery_id,"
    " :revision_id, :version_id, :run_id,"
    " 'SUBMITTED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,"
    "'corr-0088', :idempotency_key, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,"
    " :actor_id, :actor_id"
    ")"
)


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
        # SQLite 默认关闭外键；不开 PRAGMA，级联删除与 RESTRICT 用例会假绿。
        def _enable_fk(dbapi_conn: object, _record: object) -> None:
            cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        __import__("sqlalchemy").event.listens_for(engine, "connect")(_enable_fk)
    return engine


def _seed_context(engine: Engine) -> tuple[object, object, object]:
    """建最小上下文：1 个项目 + 1 个用户 + 1 条外部训练引用。

    返回 (project_id, user_id, reference_id)。
    """
    from app.infrastructure.db.models.identity import Project, User

    project_id, user_id, reference_id = uuid4(), uuid4(), uuid4()
    with Session(engine) as session:
        session.add(User(id=user_id, display_name="训练证据测试用户"))
        session.add(
            Project(
                id=project_id,
                code=f"tre-{project_id.hex[:8]}",
                name="训练证据测试项目",
                status="ACTIVE",
                version=1,
            )
        )
        session.commit()
    _insert_reference(engine, project_id=project_id, user_id=user_id, reference_id=reference_id)
    return project_id, user_id, reference_id


def _insert_reference(
    engine: Engine, *, project_id: object, user_id: object, reference_id: object
) -> None:
    """插入一条外部训练引用。

    引用行对 delivery/revision/version 的外键不在本迁移范围：种子连接上
    临时关闭 FK 检查（SQLite 用 AUTOCOMMIT 连接执行 PRAGMA，PG 用
    ``session_replication_role``），避免为种子造整条训练链。
    """
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection:
            connection.execute(text("SET session_replication_role = origin"))
            connection.execute(
                _REF_INSERT,
                {
                    # SQLite 驱动不支持绑定 UUID：用 32 位 hex（同 Uuid 存储，0063 先例）。
                    "id": reference_id.hex,
                    "project_id": project_id.hex,
                    "delivery_id": uuid4().hex,
                    "revision_id": uuid4().hex,
                    "version_id": uuid4().hex,
                    "run_id": f"run-{reference_id.hex[:8]}",
                    "idempotency_key": f"idem-{reference_id.hex[:8]}",
                    "actor_id": user_id.hex,
                },
            )
            connection.execute(text("SET session_replication_role = replica"))
        return
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(
            _REF_INSERT,
            {
                # SQLite 驱动不支持绑定 UUID：用 32 位 hex（同 Uuid 存储，0063 先例）。
                "id": reference_id.hex,
                "project_id": project_id.hex,
                "delivery_id": uuid4().hex,
                "revision_id": uuid4().hex,
                "version_id": uuid4().hex,
                "run_id": f"run-{reference_id.hex[:8]}",
                "idempotency_key": f"idem-{reference_id.hex[:8]}",
                "actor_id": user_id.hex,
            },
        )
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def _insert_evidence(
    engine: Engine,
    *,
    project_id: object,
    reference_id: object,
    user_id: object,
    run_kind: str = "SMOKE",
    steps: int = 2,
    seed: int = 42,
    exit_code: int = 0,
    config_sha256: str = _SHA,
    checkpoint_sha256: str | None = _SHA,
    finished_at: datetime | None = NOW,
    metrics_summary: dict[str, object] | None = None,
) -> None:
    from app.infrastructure.db.models.training_evidence import TrainingRunEvidenceModel

    with Session(engine) as session:
        session.add(
            TrainingRunEvidenceModel(
                id=uuid4(),
                project_id=project_id,
                external_training_reference_id=reference_id,
                run_kind=run_kind,
                device="cpu",
                runtime_versions={"torch": "2.5.0", "lerobot": "0.3.3"},
                config_sha256=config_sha256,
                seed=seed,
                batch_size=2,
                steps=steps,
                started_at=NOW,
                finished_at=finished_at,
                metrics_summary=metrics_summary,
                checkpoint_ref="checkpoints/000002/pretrained_model",
                checkpoint_sha256=checkpoint_sha256,
                exit_code=exit_code,
                stdout_tail=None,
                created_at=NOW,
                created_by=user_id,
            )
        )
        session.commit()


@pytest.fixture(params=["sqlite"])
def migrated_engine(request: pytest.FixtureRequest, tmp_path: Path) -> Engine:
    """建一个升到 head 的 SQLite 库（方言无关的那部分约束在这里验）。"""
    url = _sqlite_url(tmp_path / "training_run_evidence_test.db")
    config = _config(url)
    command.upgrade(config, "head")
    engine = _engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_upgrade_creates_table_with_indexes(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    assert TABLE in inspector.get_table_names()
    indexes = {item["name"] for item in inspector.get_indexes(TABLE)}
    assert INDEXES.issubset(indexes)
    columns = {item["name"] for item in inspector.get_columns(TABLE)}
    assert {
        "id",
        "project_id",
        "external_training_reference_id",
        "dataset_version_id",
        "run_kind",
        "device",
        "runtime_versions",
        "config_sha256",
        "seed",
        "batch_size",
        "steps",
        "started_at",
        "finished_at",
        "metrics_summary",
        "checkpoint_ref",
        "checkpoint_sha256",
        "exit_code",
        "stdout_tail",
        "created_at",
        "created_by",
    }.issubset(columns)


def test_run_kind_constraint_rejects_unknown_value(migrated_engine: Engine) -> None:
    project_id, user_id, reference_id = _seed_context(migrated_engine)
    with pytest.raises(IntegrityError):
        _insert_evidence(
            migrated_engine,
            project_id=project_id,
            reference_id=reference_id,
            user_id=user_id,
            run_kind="NOT_A_KIND",
        )


def test_run_kind_accepts_smoke_and_real(migrated_engine: Engine) -> None:
    project_id, user_id, reference_id = _seed_context(migrated_engine)
    _insert_evidence(
        migrated_engine, project_id=project_id, reference_id=reference_id, user_id=user_id
    )
    _insert_evidence(
        migrated_engine,
        project_id=project_id,
        reference_id=reference_id,
        user_id=user_id,
        run_kind="REAL",
    )


def test_steps_constraint_rejects_zero(migrated_engine: Engine) -> None:
    project_id, user_id, reference_id = _seed_context(migrated_engine)
    with pytest.raises(IntegrityError):
        _insert_evidence(
            migrated_engine,
            project_id=project_id,
            reference_id=reference_id,
            user_id=user_id,
            steps=0,
        )


def test_seed_constraint_rejects_negative(migrated_engine: Engine) -> None:
    project_id, user_id, reference_id = _seed_context(migrated_engine)
    with pytest.raises(IntegrityError):
        _insert_evidence(
            migrated_engine,
            project_id=project_id,
            reference_id=reference_id,
            user_id=user_id,
            seed=-1,
        )


def test_config_sha256_format_rejected(migrated_engine: Engine) -> None:
    project_id, user_id, reference_id = _seed_context(migrated_engine)
    with pytest.raises(IntegrityError):
        _insert_evidence(
            migrated_engine,
            project_id=project_id,
            reference_id=reference_id,
            user_id=user_id,
            config_sha256="deadbeef",
        )


def test_checkpoint_sha256_optional_but_formatted(migrated_engine: Engine) -> None:
    """checkpoint 列可空（smoke 可能在存 checkpoint 前失败）；有值必须带前缀。"""
    project_id, user_id, reference_id = _seed_context(migrated_engine)
    _insert_evidence(
        migrated_engine,
        project_id=project_id,
        reference_id=reference_id,
        user_id=user_id,
        checkpoint_sha256=None,
    )
    with pytest.raises(IntegrityError):
        _insert_evidence(
            migrated_engine,
            project_id=project_id,
            reference_id=reference_id,
            user_id=user_id,
            checkpoint_sha256="deadbeef",
        )


def test_exit_code_accepts_zero_and_nonzero(migrated_engine: Engine) -> None:
    """退出码域开放：0 成功、非 0（含负数，信号杀死）失败，不设枚举 CHECK。"""
    project_id, user_id, reference_id = _seed_context(migrated_engine)
    _insert_evidence(
        migrated_engine, project_id=project_id, reference_id=reference_id, user_id=user_id
    )
    _insert_evidence(
        migrated_engine,
        project_id=project_id,
        reference_id=reference_id,
        user_id=user_id,
        exit_code=1,
    )
    _insert_evidence(
        migrated_engine,
        project_id=project_id,
        reference_id=reference_id,
        user_id=user_id,
        exit_code=-9,
        finished_at=None,
    )


def test_nullable_metric_and_checkpoint_fields_accept_null(migrated_engine: Engine) -> None:
    """失败 run 没有指标/checkpoint：NULL 表示"没有"，不强制非空。"""
    project_id, user_id, reference_id = _seed_context(migrated_engine)
    _insert_evidence(
        migrated_engine,
        project_id=project_id,
        reference_id=reference_id,
        user_id=user_id,
        finished_at=None,
        metrics_summary=None,
        checkpoint_sha256=None,
    )


def test_rows_deletable_directly(migrated_engine: Engine) -> None:
    """证据行本身可删（表生命周期独立于引用行）。"""
    project_id, user_id, reference_id = _seed_context(migrated_engine)
    _insert_evidence(
        migrated_engine, project_id=project_id, reference_id=reference_id, user_id=user_id
    )
    with Session(migrated_engine) as session:
        from uuid import UUID as _UUID

        from app.infrastructure.db.models.training_evidence import (
            TrainingRunEvidenceModel,
        )

        raw_id = session.scalars(text(f"SELECT id FROM {TABLE}")).first()  # noqa: S608
        # SQLite 读回的 id 是 str，ORM 主键是 Uuid——统一转成 UUID 再 get。
        row = session.get(
            TrainingRunEvidenceModel,
            raw_id if isinstance(raw_id, _UUID) else _UUID(str(raw_id)),
        )
        assert row is not None
        session.delete(row)
        session.commit()
    with Session(migrated_engine) as session:
        count = session.execute(
            text(f"SELECT count(*) FROM {TABLE}")  # noqa: S608
        ).scalar_one()
    assert count == 0


def test_project_delete_blocked_by_training_guard_keeps_rows(migrated_engine: Engine) -> None:
    """删项目被**既有**训练守卫阻止时，证据行保留。

    **既有语义冲突（阶段 4 遗留，非本表引入）**：
    ``external_training_references.project_id`` 是 CASCADE，但训练守卫触发器
    （``external_training_reference_delete_forbidden``）禁止删除该表行——
    两者互斥，导致「删有训练引用的项目」在生产上本就不可达。
    本表不改变该语义（修它需要单独 ADR）；此处只验证：项目删除失败时
    证据行不被部分删除（事务原子性）。
    """
    import pytest as _pytest
    from sqlalchemy.exc import IntegrityError as _IntegrityError

    project_id, user_id, reference_id = _seed_context(migrated_engine)
    _insert_evidence(
        migrated_engine, project_id=project_id, reference_id=reference_id, user_id=user_id
    )
    with Session(migrated_engine) as session:
        from app.infrastructure.db.models.identity import Project

        row = session.get(Project, project_id)
        assert row is not None
        with _pytest.raises(_IntegrityError):
            session.delete(row)
            session.commit()
        session.rollback()
    with Session(migrated_engine) as session:
        count = session.execute(
            text(f"SELECT count(*) FROM {TABLE}")  # noqa: S608
        ).scalar_one()
    assert count == 1, "项目删除失败后证据行必须完整保留（事务原子性）"


def test_reference_restrict_blocks_delete(migrated_engine: Engine) -> None:
    """有证据行时拒绝删引用（RESTRICT）：防静默丢失训练证据。"""
    project_id, user_id, reference_id = _seed_context(migrated_engine)
    _insert_evidence(
        migrated_engine, project_id=project_id, reference_id=reference_id, user_id=user_id
    )
    with Session(migrated_engine) as session:
        from app.infrastructure.db.models.training import ExternalTrainingReferenceModel

        row = session.get(ExternalTrainingReferenceModel, reference_id)
        assert row is not None
        with pytest.raises(IntegrityError):
            session.delete(row)
            session.commit()
        session.rollback()


def test_downgrade_drops_table_and_upgrade_recreates(tmp_path: Path) -> None:
    """降级删表、再升级重建，往返后表仍在且结构完整。"""
    url = _sqlite_url(tmp_path / "roundtrip.db")
    config = _config(url)
    command.upgrade(config, "head")
    engine = _engine(url)
    try:
        assert TABLE in inspect(engine).get_table_names()
        command.downgrade(config, "0087")
        assert TABLE not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        inspector = inspect(engine)
        assert TABLE in inspector.get_table_names()
        indexes = {item["name"] for item in inspector.get_indexes(TABLE)}
        assert INDEXES.issubset(indexes)
    finally:
        engine.dispose()


@pytest.mark.skipif(not _POSTGRES_URL, reason="未配置 DDP_TEST_POSTGRES_URL")
def test_postgres_roundtrip() -> None:
    """真实 PostgreSQL 上再跑一遍建表/约束/回滚（MEM-INC-013 双方言）。"""
    url = _POSTGRES_URL
    config = _config(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = _engine(url)
    try:
        inspector = inspect(engine)
        assert TABLE in inspector.get_table_names()
        indexes = {item["name"] for item in inspector.get_indexes(TABLE)}
        assert INDEXES.issubset(indexes)
        project_id, user_id, reference_id = _seed_context(engine)
        _insert_evidence(engine, project_id=project_id, reference_id=reference_id, user_id=user_id)
        with pytest.raises(IntegrityError):
            _insert_evidence(
                engine,
                project_id=project_id,
                reference_id=reference_id,
                user_id=user_id,
                run_kind="NOT_A_KIND",
            )
    finally:
        engine.dispose()
        command.downgrade(config, "base")
