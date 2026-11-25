"""回流案例表迁移测试（0082，V52-030）。

覆盖：upgrade → 约束行为（唯一键/CHECK 枚举/优先级）→ downgrade → 再 upgrade
往返。双方言同构，SQLite 即可验证（MEM-INC-013：本迁移无 PG 专有语句）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[sa.Engine]:
    url = f"sqlite+pysqlite:///{tmp_path / 'backflow.db'}"
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(config, "head")
    eng = sa.create_engine(url)
    try:
        yield eng
    finally:
        eng.dispose()


def _insert(conn: sa.Connection, **overrides: object) -> None:
    values = {
        "id": uuid4().hex,
        "project_id": uuid4().hex,
        "source": "eval-platform",
        "case_ref": "case-1",
        "capability": "tool_use",
        "failure_count": 1,
        "suggested_priority": "P1",
        "payload": "{}",
        "status": "RECEIVED",
        "received_at": datetime.now(UTC),
    }
    values.update(overrides)
    conn.execute(
        sa.text(
            "INSERT INTO annotation_backflow_cases "
            "(id, project_id, source, case_ref, capability, failure_count, suggested_priority, "
            " payload, status, received_at) VALUES "
            "(:id, :project_id, :source, :case_ref, :capability, :failure_count, "
            " :suggested_priority, :payload, :status, :received_at)"
        ),
        values,
    )


def test_roundtrip_and_constraints(engine: sa.Engine, tmp_path: Path) -> None:
    """升级后表存在、唯一键与 CHECK 生效；降级后表消失；再升级恢复。"""
    inspector = sa.inspect(engine)
    assert "annotation_backflow_cases" in inspector.get_table_names()

    with engine.begin() as conn:
        _insert(conn)
        # 唯一键 (source, case_ref) 重复被拒
        with pytest.raises(sa.exc.IntegrityError):
            _insert(conn, id=uuid4().hex)
        # 状态枚举 CHECK
        with pytest.raises(sa.exc.IntegrityError):
            _insert(conn, id=uuid4().hex, case_ref="case-2", status="UNKNOWN")
        # 优先级枚举 CHECK
        with pytest.raises(sa.exc.IntegrityError):
            _insert(conn, id=uuid4().hex, case_ref="case-3", suggested_priority="P9")
        # failure_count >= 1
        with pytest.raises(sa.exc.IntegrityError):
            _insert(conn, id=uuid4().hex, case_ref="case-4", failure_count=0)

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{tmp_path / 'backflow.db'}")
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    command.downgrade(config, "0080")
    assert "annotation_backflow_cases" not in sa.inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert "annotation_backflow_cases" in sa.inspect(engine).get_table_names()
