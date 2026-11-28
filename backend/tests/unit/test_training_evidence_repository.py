"""``SqlTrainingEvidenceRepository`` 的单元测试（V61-T3-03）。

覆盖写入与读取两侧的持久化事实（不是内存对象）：

- ``add`` 登记一条证据：不提交（由调用方 UoW 提交），提交后字段完整落库；
- ``list_for_project`` 分页 + 最新在前，且**项目隔离**（A 项目的证据不出现在
  B 项目的列表里）；
- ``find_by_reference`` 按引用反查：一个引用多条证据（smoke 重跑 / smoke+real）
  全部返回，且不串到别的引用；
- ``metrics_summary`` / ``checkpoint_*`` 为 NULL 时读回仍是 NULL（NULL 表示
  "没有"，不是"零"）。

种子说明：``external_training_references`` 的交付/修订/版本外键不在本表范围，
种子引用行时在共享连接上临时关闭 SQLite 外键检查（StaticPool 内存库复用同一
连接），外键完整性由迁移测试覆盖。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.models.training_evidence import TrainingRunEvidenceModel
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.training_evidence import (
    SqlTrainingEvidenceRepository,
)
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
LATER = NOW + datetime.resolution * 1000
_SHA = "sha256:" + "a" * 64


def _insert_reference(engine: Engine, *, project_id: UUID, user_id: UUID) -> UUID:
    """插入一条外部训练引用（引用行即 run 记录，ADR-0036）。

    引用的交付/修订/版本外键不在本表范围：种子连接上临时关闭 FK 检查，
    避免为种子造整条训练链。
    """
    reference_id = uuid4()
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(
            text(
                "INSERT INTO external_training_references ("
                "id, project_id, adapter_id, adapter_version, training_delivery_id,"
                " training_request_revision_id, dataset_version_id, external_run_id,"
                " external_status, submitted_at, last_synced_at,"
                " correlation_id, idempotency_key, version, created_at, updated_at,"
                " created_by, updated_by"
                ") VALUES ("
                ":id, :project_id, 'lerobot', '1.0.0', :delivery_id,"
                " :revision_id, :version_id, :run_id,"
                " 'SUBMITTED', :now, :now,"
                "'corr-seed', :idempotency_key, 1, :now, :now,"
                " :actor_id, :actor_id"
                ")"
            ),
            {
                # SQLite 驱动不支持绑定 UUID：用 32 位 hex（同 Uuid 存储，0063 先例）。
                "id": reference_id.hex,
                "project_id": project_id.hex,
                "delivery_id": uuid4().hex,
                "revision_id": uuid4().hex,
                "version_id": uuid4().hex,
                "run_id": f"run-{reference_id.hex[:8]}",
                "now": NOW,
                "idempotency_key": f"idem-{reference_id.hex[:8]}",
                "actor_id": user_id.hex,
            },
        )
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
    return reference_id


@pytest.fixture()
def engine() -> Iterator[Engine]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture()
def context(
    engine: Engine, session_factory: sessionmaker[Session]
) -> Iterator[dict[str, object]]:
    """1 项目 + 1 用户 + 2 引用（同项目），返回种子上下文。"""
    project_a, project_b, user_id = uuid4(), uuid4(), uuid4()
    with session_factory() as session:
        session.add(User(id=user_id, display_name="训练证据仓储测试用户"))
        session.add_all(
            [
                Project(
                    id=project_a, code=f"pa-{project_a.hex[:8]}", name="项目A", status="ACTIVE"
                ),
                Project(
                    id=project_b, code=f"pb-{project_b.hex[:8]}", name="项目B", status="ACTIVE"
                ),
            ]
        )
        session.commit()
    reference_a = _insert_reference(engine, project_id=project_a, user_id=user_id)
    reference_b = _insert_reference(engine, project_id=project_b, user_id=user_id)
    yield {
        "project_a": project_a,
        "project_b": project_b,
        "user_id": user_id,
        "reference_a": reference_a,
        "reference_b": reference_b,
    }


def _add(
    repository: SqlTrainingEvidenceRepository,
    *,
    project_id: UUID,
    reference_id: UUID,
    user_id: UUID,
    run_kind: str = "SMOKE",
    exit_code: int = 0,
    created_at: datetime = NOW,
    metrics_summary: dict[str, object] | None = None,
    checkpoint_sha256: str | None = _SHA,
) -> object:
    return repository.add(
        project_id=project_id,
        external_training_reference_id=reference_id,
        run_kind=run_kind,
        device="cpu",
        runtime_versions={"torch": "2.5.0", "lerobot": "0.3.3"},
        config_sha256=_SHA,
        seed=42,
        batch_size=2,
        steps=2,
        started_at=NOW,
        created_by=user_id,
        now=created_at,
        finished_at=NOW,
        metrics_summary=metrics_summary,
        checkpoint_sha256=checkpoint_sha256,
        exit_code=exit_code,
    )


def test_add_persists_full_row(
    context: dict[str, object], session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        repository = SqlTrainingEvidenceRepository(session)
        evidence = _add(
            repository,
            project_id=context["project_a"],  # type: ignore[arg-type]
            reference_id=context["reference_a"],  # type: ignore[arg-type]
            user_id=context["user_id"],  # type: ignore[arg-type]
        )
        session.commit()
        row = session.get(TrainingRunEvidenceModel, evidence.id)
        assert row is not None
        assert row.project_id == context["project_a"]
        assert row.external_training_reference_id == context["reference_a"]
        assert row.run_kind == "SMOKE"
        assert row.device == "cpu"
        assert row.runtime_versions == {"torch": "2.5.0", "lerobot": "0.3.3"}
        assert row.config_sha256 == _SHA
        assert row.seed == 42
        assert row.batch_size == 2
        assert row.steps == 2
        assert row.exit_code == 0
        assert row.finished_at is not None
        assert row.created_by == context["user_id"]


def test_add_does_not_commit(
    context: dict[str, object], session_factory: sessionmaker[Session]
) -> None:
    """``add`` 只登记不提交：回滚后库里没有该行。"""
    with session_factory() as session:
        repository = SqlTrainingEvidenceRepository(session)
        _add(
            repository,
            project_id=context["project_a"],  # type: ignore[arg-type]
            reference_id=context["reference_a"],  # type: ignore[arg-type]
            user_id=context["user_id"],  # type: ignore[arg-type]
        )
        session.rollback()
        count = session.scalar(
            text("SELECT count(*) FROM training_run_evidence")  # noqa: S608 - 常量表名
        )
        assert count == 0


def test_list_for_project_paginates_newest_first(
    context: dict[str, object], session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        repository = SqlTrainingEvidenceRepository(session)
        first = _add(
            repository,
            project_id=context["project_a"],  # type: ignore[arg-type]
            reference_id=context["reference_a"],  # type: ignore[arg-type]
            user_id=context["user_id"],  # type: ignore[arg-type]
            created_at=NOW,
        )
        second = _add(
            repository,
            project_id=context["project_a"],  # type: ignore[arg-type]
            reference_id=context["reference_a"],  # type: ignore[arg-type]
            user_id=context["user_id"],  # type: ignore[arg-type]
            created_at=LATER,
        )
        session.commit()

        page1 = repository.list_for_project(context["project_a"], limit=1, offset=0)  # type: ignore[arg-type]
        assert [item.id for item in page1] == [second.id]
        page2 = repository.list_for_project(context["project_a"], limit=1, offset=1)  # type: ignore[arg-type]
        assert [item.id for item in page2] == [first.id]


def test_list_for_project_is_isolated_between_projects(
    context: dict[str, object], session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        repository = SqlTrainingEvidenceRepository(session)
        _add(
            repository,
            project_id=context["project_a"],  # type: ignore[arg-type]
            reference_id=context["reference_a"],  # type: ignore[arg-type]
            user_id=context["user_id"],  # type: ignore[arg-type]
        )
        session.commit()

        listed = repository.list_for_project(context["project_b"])  # type: ignore[arg-type]
        assert listed == []


def test_find_by_reference_returns_all_rows_for_reference(
    context: dict[str, object], session_factory: sessionmaker[Session]
) -> None:
    """一个引用可有多条证据（smoke 重跑 / smoke + real），全部返回。"""
    with session_factory() as session:
        repository = SqlTrainingEvidenceRepository(session)
        smoke = _add(
            repository,
            project_id=context["project_a"],  # type: ignore[arg-type]
            reference_id=context["reference_a"],  # type: ignore[arg-type]
            user_id=context["user_id"],  # type: ignore[arg-type]
            run_kind="SMOKE",
            created_at=NOW,
        )
        real = _add(
            repository,
            project_id=context["project_a"],  # type: ignore[arg-type]
            reference_id=context["reference_a"],  # type: ignore[arg-type]
            user_id=context["user_id"],  # type: ignore[arg-type]
            run_kind="REAL",
            created_at=LATER,
        )
        other = _add(
            repository,
            project_id=context["project_b"],  # type: ignore[arg-type]
            reference_id=context["reference_b"],  # type: ignore[arg-type]
            user_id=context["user_id"],  # type: ignore[arg-type]
            created_at=LATER,
        )
        session.commit()

        found = repository.find_by_reference(context["reference_a"])  # type: ignore[arg-type]
        assert {item.id for item in found} == {smoke.id, real.id}
        assert {item.run_kind for item in found} == {"SMOKE", "REAL"}
        # 不串到别的引用：reference_b 只看到自己的行
        found_b = repository.find_by_reference(context["reference_b"])  # type: ignore[arg-type]
        assert {item.id for item in found_b} == {other.id}


def test_nullable_fields_read_back_as_none(
    context: dict[str, object], session_factory: sessionmaker[Session]
) -> None:
    """失败 run 没有指标/checkpoint：NULL 读回仍是 None，不伪装成零值。"""
    with session_factory() as session:
        repository = SqlTrainingEvidenceRepository(session)
        failed = _add(
            repository,
            project_id=context["project_a"],  # type: ignore[arg-type]
            reference_id=context["reference_a"],  # type: ignore[arg-type]
            user_id=context["user_id"],  # type: ignore[arg-type]
            exit_code=1,
            metrics_summary=None,
            checkpoint_sha256=None,
        )
        session.commit()

        [item] = repository.list_for_project(context["project_a"])  # type: ignore[arg-type]
        assert item.id == failed.id
        assert item.exit_code == 1
        assert item.metrics_summary is None
        assert item.checkpoint_sha256 is None
