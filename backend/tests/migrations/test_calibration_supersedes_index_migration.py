"""迁移 0053：标定记录修订链部分唯一索引与存量冲突检测。

两条底线：

1. 索引在 SQLite / PostgreSQL 都是**部分**唯一索引——``supersedes_id IS NULL``
   的行（草稿、首版）不参与唯一性；
2. 存量数据若已分叉或自引用，升级必须**逐行打印冲突 ID 后失败退出**，
   不得静默删改数据。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
INDEX_NAME = "uq_calibration_records_project_supersedes"
TABLE = "calibration_records"


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _index_names(db_path: Path) -> set[str]:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        return {index["name"] for index in inspect(engine).get_indexes(TABLE)}
    finally:
        engine.dispose()


_INSERT_RECORD = text(
    """
    INSERT INTO calibration_records (
        id, project_id, profile_id, scope, target_type, target_id,
        calibration_type, method, parameters, valid_from, status,
        evidence_asset_ids, checksum, uncertainty, performed_by,
        supersedes_id, version, created_by, updated_by,
        calibration_version, profile_snapshot
    ) VALUES (
        :id, :project_id, :profile_id, 'SENSOR', 'SENSOR', :target_id,
        'INTRINSICS', 'migration-test', '{}', '2026-01-01 00:00:00', 'DRAFT',
        '[]', :checksum, '{}', :performed_by,
        :supersedes_id, 1, :performed_by, :performed_by,
        1, '{}'
    )
    """
)


def _seed_record(
    db_path: Path,
    *,
    project_id: str,
    profile_id: str,
    target_id: str,
    performed_by: str,
    record_id: str,
    supersedes_id: str | None,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                _INSERT_RECORD,
                {
                    "id": record_id,
                    "project_id": project_id,
                    "profile_id": profile_id,
                    "target_id": target_id,
                    "checksum": "sha256:" + "a" * 64,
                    "performed_by": performed_by,
                    "supersedes_id": supersedes_id,
                },
            )
    finally:
        engine.dispose()


def _seed_target_ref(db_path: Path, *, project_id: str, target_id: str) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO calibration_target_refs "
                    "(project_id, target_type, target_id, lifecycle_status, "
                    " is_retired, updated_at) "
                    "VALUES (:project_id, 'SENSOR', :target_id, 'PROVISIONED', "
                    " 0, '2026-01-01 00:00:00')"
                ),
                {"project_id": project_id, "target_id": target_id},
            )
    finally:
        engine.dispose()


def test_upgrade_creates_partial_unique_index_and_downgrade_removes_it(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "supersedes_index.db"
    config = _config(db_path)

    command.upgrade(config, "0052")
    assert INDEX_NAME not in _index_names(db_path)

    command.upgrade(config, "0053")
    assert INDEX_NAME in _index_names(db_path)

    command.downgrade(config, "0052")
    assert INDEX_NAME not in _index_names(db_path)

    command.upgrade(config, "head")
    assert INDEX_NAME in _index_names(db_path)


def test_unique_index_rejects_second_successor(tmp_path: Path) -> None:
    db_path = tmp_path / "supersedes_unique.db"
    config = _config(db_path)
    command.upgrade(config, "head")

    project_id = uuid4().hex
    profile_id = uuid4().hex
    target_id = uuid4().hex
    performer_id = uuid4().hex
    _seed_target_ref(db_path, project_id=project_id, target_id=target_id)
    predecessor = uuid4().hex
    _seed_record(
        db_path,
        project_id=project_id,
        profile_id=profile_id,
        target_id=target_id,
        performed_by=performer_id,
        record_id=predecessor,
        supersedes_id=None,
    )
    _seed_record(
        db_path,
        project_id=project_id,
        profile_id=profile_id,
        target_id=target_id,
        performed_by=performer_id,
        record_id=uuid4().hex,
        supersedes_id=predecessor,
    )

    with pytest.raises(IntegrityError):
        _seed_record(
            db_path,
            project_id=project_id,
            profile_id=profile_id,
            target_id=target_id,
            performed_by=performer_id,
            record_id=uuid4().hex,
            supersedes_id=predecessor,
        )


def test_forked_rows_abort_upgrade_with_ids(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "supersedes_fork.db"
    config = _config(db_path)
    command.upgrade(config, "0052")

    project_id = uuid4().hex
    profile_id = uuid4().hex
    target_id = uuid4().hex
    performer_id = uuid4().hex
    _seed_target_ref(db_path, project_id=project_id, target_id=target_id)
    predecessor = uuid4().hex
    _seed_record(
        db_path,
        project_id=project_id,
        profile_id=profile_id,
        target_id=target_id,
        performed_by=performer_id,
        record_id=predecessor,
        supersedes_id=None,
    )
    fork_a, fork_b = uuid4().hex, uuid4().hex
    for record_id in (fork_a, fork_b):
        _seed_record(
            db_path,
            project_id=project_id,
            profile_id=profile_id,
            target_id=target_id,
            performed_by=performer_id,
            record_id=record_id,
            supersedes_id=predecessor,
        )

    with pytest.raises(RuntimeError, match="修订链"):
        command.upgrade(config, "0053")

    stderr = capsys.readouterr().err
    assert "修订链分叉" in stderr
    assert predecessor in stderr
    assert fork_a in stderr
    assert fork_b in stderr
    # 失败退出不得留下半成品索引。
    assert INDEX_NAME not in _index_names(db_path)


def test_self_reference_aborts_upgrade_with_ids(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "supersedes_self.db"
    config = _config(db_path)
    command.upgrade(config, "0052")

    project_id = uuid4().hex
    profile_id = uuid4().hex
    target_id = uuid4().hex
    performer_id = uuid4().hex
    _seed_target_ref(db_path, project_id=project_id, target_id=target_id)
    self_ref = uuid4().hex
    _seed_record(
        db_path,
        project_id=project_id,
        profile_id=profile_id,
        target_id=target_id,
        performed_by=performer_id,
        record_id=self_ref,
        supersedes_id=self_ref,
    )

    with pytest.raises(RuntimeError, match="自引用"):
        command.upgrade(config, "0053")

    stderr = capsys.readouterr().err
    assert "supersedes_id 自引用" in stderr
    assert self_ref in stderr
    assert INDEX_NAME not in _index_names(db_path)


def test_valid_existing_chain_survives_round_trip(tmp_path: Path) -> None:
    """存量线性链不算冲突：upgrade 成功，downgrade → upgrade 依旧可重复。"""
    db_path = tmp_path / "supersedes_valid_chain.db"
    config = _config(db_path)
    command.upgrade(config, "0052")

    project_id = uuid4().hex
    profile_id = uuid4().hex
    target_id = uuid4().hex
    performer_id = uuid4().hex
    _seed_target_ref(db_path, project_id=project_id, target_id=target_id)
    predecessor = uuid4().hex
    successor = uuid4().hex
    _seed_record(
        db_path,
        project_id=project_id,
        profile_id=profile_id,
        target_id=target_id,
        performed_by=performer_id,
        record_id=predecessor,
        supersedes_id=None,
    )
    _seed_record(
        db_path,
        project_id=project_id,
        profile_id=profile_id,
        target_id=target_id,
        performed_by=performer_id,
        record_id=successor,
        supersedes_id=predecessor,
    )

    command.upgrade(config, "0053")
    assert INDEX_NAME in _index_names(db_path)

    command.downgrade(config, "0052")
    assert INDEX_NAME not in _index_names(db_path)

    command.upgrade(config, "head")
    assert INDEX_NAME in _index_names(db_path)
