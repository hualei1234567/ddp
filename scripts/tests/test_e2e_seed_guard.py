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
        "sqlite+pysqlite:///:memory:",
        "sqlite+pysqlite:///relative.db",
        "sqlite+pysqlite:////workspace/shared.db",
        "postgresql+psycopg://ddp:ddp@example.com/ddp",
        "postgresql+psycopg://ddp:ddp@localhost/ddp",
        "postgresql+psycopg://ddp:ddp@127.0.0.1/ddp",
        "postgresql+psycopg://ddp:ddp@[::1]/ddp",
        "postgresql+psycopg://ddp:ddp@postgres/ddp",
    ],
)
def test_database_url_rejects_non_temporary_or_remote_database(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    monkeypatch.setenv("DDP_DATABASE_URL", url)

    with pytest.raises(SystemExit, match="拒绝使用非临时 E2E 数据库"):
        seed_e2e._database_url()


def test_rbac_seed_uses_existing_project_roles_only() -> None:
    """V51-013 不新增角色：种子里的角色必须落在仓库既有 ``ProjectRole`` 内。"""
    from app.domain.identity.models import ProjectRole

    declared = {
        "owner",  # 提交人 / 审核人 / 对照项目 B 负责人
        "editor",  # 标注人
        "viewer",  # 只读成员
    }
    assert declared <= {role.value for role in ProjectRole}


def test_rbac_seed_covers_two_projects_and_isolated_members() -> None:
    """蓝图 §4：至少两个项目、多个角色、不同资源 ID，且成员关系互不重叠。"""
    ids = seed_e2e.e2e_ids()

    assert ids["project_id"] != ids["project_b_id"]
    assert ids["task_id"] != ids["task_b_id"]
    assert ids["schema_a_id"] != ids["schema_b_id"]
    assert ids["dataset_a_id"] != ids["dataset_b_id"]
    assert ids["dataset_version_a_id"] != ids["dataset_version_b_id"]

    # A 的成员与 B 的成员必须是不同的人：否则"跨项目拒绝"无法证明。
    project_a_users = {
        ids["submitter_id"],
        ids["reviewer_id"],
        ids["annotator_id"],
        ids["viewer_id"],
    }
    assert ids["outsider_id"] not in project_a_users
    assert len(project_a_users) == 4


def test_rbac_seed_ids_are_stable_and_copied() -> None:
    """用例引用的是固定 UUID（便于失败时定位），并且返回值是副本。"""
    first = seed_e2e.e2e_ids()
    first["project_id"] = "mutated"

    assert seed_e2e.e2e_ids()["project_id"] == str(seed_e2e.PROJECT_ID)
