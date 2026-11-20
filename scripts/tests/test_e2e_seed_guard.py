from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).parents[2] / "apps" / "web" / "scripts" / "e2e" / "seed_e2e.py"
)
SPEC = importlib.util.spec_from_file_location("seed_e2e", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
seed_e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed_e2e)


def test_database_url_accepts_temporary_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "sqlite+pysqlite:////tmp/ddp-e2e-test.db"
    monkeypatch.setenv("DDP_DATABASE_URL", url)

    assert seed_e2e._database_url() == url


@pytest.mark.parametrize(
    "url",
    [
        "sqlite+pysqlite:///relative.db",
        "sqlite+pysqlite:////workspace/shared.db",
        "postgresql+psycopg://ddp:ddp@example.com/ddp",
    ],
)
def test_database_url_rejects_non_temporary_or_remote_database(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    monkeypatch.setenv("DDP_DATABASE_URL", url)

    with pytest.raises(SystemExit, match="拒绝使用非本机数据库"):
        seed_e2e._database_url()
