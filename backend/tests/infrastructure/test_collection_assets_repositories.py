"""采集素材仓储与目录适配器测试（SQLite 真实约束）。

重点不是 CRUD 本身（API 测试已覆盖），而是三件只有靠近数据库才看得出来事实：

1. 按 ID 取单条与列表**都**带 ``project_id`` 过滤，跨项目返回 ``None`` / 空列表；
2. ``sop_version_exists`` / ``action_spec_version_exists`` 的语义是
   "同一 code 下的对应版本行是否存在"，不是"该行的版本列等于给定值"——
   两者在素材升版后会产生不同结果，必须钉死；
3. ``(project_id, code, version)`` 唯一约束由数据库拒绝重复，仓储把
   ``IntegrityError`` 翻译成 ``DuplicateResourceError``。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.collection.assets import (
    ActionSpec,
    CollectionPlan,
    CollectionSop,
    Scenario,
)
from app.domain.collection.enums import CollectionAssetStatus
from app.domain.collection.errors import (
    DuplicateResourceError,
    ResourceImmutableError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.identity.errors import CrossProjectAccessError
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import CollectionTask as CollectionTaskModel
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.collection_asset_directory import (
    SqlCollectionAssetDirectory,
)
from app.infrastructure.repositories.collection_assets import (
    SqlActionSpecRepository,
    SqlCollectionPlanRepository,
    SqlCollectionSopRepository,
    SqlScenarioRepository,
)
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 16, tzinfo=UTC)


@dataclass(frozen=True)
class AssetIds:
    project_a: UUID
    project_b: UUID
    user: UUID


@pytest.fixture()
def asset_db() -> tuple[sessionmaker[Session], AssetIds]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    project_a, project_b, user = uuid4(), uuid4(), uuid4()
    ids = AssetIds(project_a, project_b, user)
    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="project-a", name="项目 A"),
                Project(id=project_b, code="project-b", name="项目 B"),
                User(id=user, display_name="用户"),
            ]
        )
        session.commit()
    try:
        yield factory, ids
    finally:
        engine.dispose()


def _scope(ids: AssetIds, project: UUID) -> ProjectScope:
    return ProjectScope(project_id=project, actor_id=ids.user)


def _plan(
    ids: AssetIds, project: UUID, *, code: str = "PLAN-1", version: int = 1
) -> CollectionPlan:
    return CollectionPlan(
        plan_id=uuid4(),
        project_id=project,
        code=code,
        name="方案",
        plan_version=version,
        created_at=NOW,
        updated_at=NOW,
    )


def _sop(
    ids: AssetIds, project: UUID, *, code: str = "SOP-1", version: str = "v1"
) -> CollectionSop:
    return CollectionSop(
        sop_id=uuid4(),
        project_id=project,
        code=code,
        name="SOP",
        sop_version=version,
        steps=({"index": 1},),
        created_at=NOW,
        updated_at=NOW,
    )


def _scenario(ids: AssetIds, project: UUID, *, code: str = "SC-1") -> Scenario:
    return Scenario(
        scenario_id=uuid4(),
        project_id=project,
        code=code,
        name="场景",
        scene_labels=("家庭",),
        created_at=NOW,
        updated_at=NOW,
    )


def _spec(ids: AssetIds, project: UUID, *, code: str = "ACT-1", version: str = "v1") -> ActionSpec:
    return ActionSpec(
        action_spec_id=uuid4(),
        project_id=project,
        code=code,
        name="动作",
        spec_version=version,
        actions=({"code": "pick"},),
        created_at=NOW,
        updated_at=NOW,
    )


def test_plan_repository_round_trip_and_isolation(
    asset_db: tuple[sessionmaker[Session], AssetIds],
) -> None:
    factory, ids = asset_db
    with factory() as session:
        repo = SqlCollectionPlanRepository(session)
        plan = _plan(ids, ids.project_a)
        repo.add(_scope(ids, ids.project_a), plan)
        session.commit()

        scope_a = _scope(ids, ids.project_a)
        scope_b = _scope(ids, ids.project_b)
        assert repo.get(scope_a, plan.plan_id) is not None
        assert repo.get(scope_b, plan.plan_id) is None
        assert [item.plan_id for item in repo.list(scope_a)] == [plan.plan_id]
        assert repo.list(scope_b) == []
        assert repo.get_by_code_version(scope_a, code="PLAN-1", plan_version=1) is not None
        assert repo.get_by_code_version(scope_b, code="PLAN-1", plan_version=1) is None


def test_sop_repository_scope_guard(
    asset_db: tuple[sessionmaker[Session], AssetIds],
) -> None:
    factory, ids = asset_db
    with factory() as session:
        repo = SqlCollectionSopRepository(session)
        sop = _sop(ids, ids.project_a)
        repo.add(_scope(ids, ids.project_a), sop)
        session.commit()

        # 用 B 项目的 scope 写入 A 项目的实体：必须被 ProjectScope.ensure 拒绝。
        with pytest.raises(Exception, match="不属于当前项目"):
            repo.add(_scope(ids, ids.project_b), _sop(ids, ids.project_a, code="SOP-X"))


def test_duplicate_code_version_is_translated(
    asset_db: tuple[sessionmaker[Session], AssetIds],
) -> None:
    factory, ids = asset_db
    with factory() as session:
        repo = SqlCollectionSopRepository(session)
        scope = _scope(ids, ids.project_a)
        repo.add(scope, _sop(ids, ids.project_a))
        with pytest.raises(DuplicateResourceError):
            repo.add(scope, _sop(ids, ids.project_a))
        session.rollback()


def test_version_lookup_compares_the_referenced_row_only(
    asset_db: tuple[sessionmaker[Session], AssetIds],
) -> None:
    """版本快照必须与**被引用那一行**一致，不能按同一 code 的版本谱系放行。

    回归用例（曾是真实缺陷）：早期实现先取被引用行的 ``code``，再按
    ``(code, sop_version)`` 查任意版本行。于是 ``sop_id=<v1 行> + sop_version=v2``
    被判为"一致"——因为同 code 下确实存在 v2 行，而 v2 其实是**另一行**。
    任务会带着"指向 v1 行、冻结 v2 版本"的自相矛盾引用进入执行。
    """
    factory, ids = asset_db
    scope = _scope(ids, ids.project_a)
    with factory() as session:
        repo = SqlCollectionSopRepository(session)
        v1 = _sop(ids, ids.project_a, version="v1")
        repo.add(scope, v1)
        session.commit()

        lookup = SqlCollectionAssetDirectory(session)
        assert lookup.sop_exists(scope, v1.sop_id) is True
        assert lookup.sop_version_exists(scope, sop_id=v1.sop_id, sop_version="v1") is True
        assert lookup.sop_version_exists(scope, sop_id=v1.sop_id, sop_version="v9") is False

        v2 = _sop(ids, ids.project_a, version="v2")
        repo.add(scope, v2)
        session.commit()

        # v1 行只能配 v1；v2 行只能配 v2。交叉组合必须为 False。
        assert lookup.sop_version_exists(scope, sop_id=v1.sop_id, sop_version="v1") is True
        assert lookup.sop_version_exists(scope, sop_id=v2.sop_id, sop_version="v2") is True
        assert lookup.sop_version_exists(scope, sop_id=v1.sop_id, sop_version="v2") is False
        assert lookup.sop_version_exists(scope, sop_id=v2.sop_id, sop_version="v1") is False


def test_action_spec_version_lookup_compares_the_referenced_row_only(
    asset_db: tuple[sessionmaker[Session], AssetIds],
) -> None:
    """动作库同上：跨版本行必须判为不一致。"""
    factory, ids = asset_db
    scope = _scope(ids, ids.project_a)
    with factory() as session:
        repo = SqlActionSpecRepository(session)
        v1 = _spec(ids, ids.project_a, version="v1")
        repo.add(scope, v1)
        v2 = _spec(ids, ids.project_a, version="v2")
        repo.add(scope, v2)
        session.commit()

        lookup = SqlCollectionAssetDirectory(session)
        assert lookup.action_spec_version_exists(
            scope, spec_id=v1.action_spec_id, spec_version="v1"
        )
        assert lookup.action_spec_version_exists(
            scope, spec_id=v2.action_spec_id, spec_version="v2"
        )
        assert not lookup.action_spec_version_exists(
            scope, spec_id=v1.action_spec_id, spec_version="v2"
        )
        assert not lookup.action_spec_version_exists(
            scope, spec_id=v2.action_spec_id, spec_version="v1"
        )


def test_version_lookup_tolerates_surrounding_whitespace(
    asset_db: tuple[sessionmaker[Session], AssetIds],
) -> None:
    """协议层不裁剪版本号，仓储比对前统一 strip，避免"看着一样却判不一致"。"""
    factory, ids = asset_db
    scope = _scope(ids, ids.project_a)
    with factory() as session:
        repo = SqlCollectionSopRepository(session)
        sop = _sop(ids, ids.project_a, version="v1")
        repo.add(scope, sop)
        session.commit()

        lookup = SqlCollectionAssetDirectory(session)
        assert lookup.sop_version_exists(scope, sop_id=sop.sop_id, sop_version=" v1 ")


def test_directory_rejects_other_project_assets(
    asset_db: tuple[sessionmaker[Session], AssetIds],
) -> None:
    factory, ids = asset_db
    scope_a = _scope(ids, ids.project_a)
    with factory() as session:
        SqlCollectionPlanRepository(session).add(scope_a, _plan(ids, ids.project_a))
        SqlCollectionSopRepository(session).add(scope_a, _sop(ids, ids.project_a))
        SqlScenarioRepository(session).add(scope_a, _scenario(ids, ids.project_a))
        SqlActionSpecRepository(session).add(scope_a, _spec(ids, ids.project_a))
        session.commit()

        directory = SqlCollectionAssetDirectory(session)
        plan = SqlCollectionPlanRepository(session).list(scope_a)[0]
        sop = SqlCollectionSopRepository(session).list(scope_a)[0]
        scenario = SqlScenarioRepository(session).list(scope_a)[0]
        spec = SqlActionSpecRepository(session).list(scope_a)[0]

        scope_b = _scope(ids, ids.project_b)
        assert directory.plan_exists(scope_b, plan.plan_id) is False
        assert directory.sop_exists(scope_b, sop.sop_id) is False
        assert directory.scenario_exists(scope_b, scenario.scenario_id) is False
        assert directory.action_spec_exists(scope_b, spec.action_spec_id) is False
        # 跨项目连"版本存在"也必须为 False，不能因为 code/version 相同就放行。
        assert directory.sop_version_exists(scope_b, sop_id=sop.sop_id, sop_version="v1") is False
        assert (
            directory.action_spec_version_exists(
                scope_b, spec_id=spec.action_spec_id, spec_version="v1"
            )
            is False
        )


# --- N8 写路径：乐观锁与删除 ------------------------------------------------
def test_save_update_bumps_version_and_rejects_stale_version(
    asset_db: tuple[sessionmaker[Session], AssetIds],
) -> None:
    """乐观锁：第一次成功推进版本；用同一个旧版本再写必须冲突。

    这是"同一版本被两次修改"的真实形态：并发的两个客户端都读到 version=1，
    先提交者成功，后提交者必须拿到 409 而不是静默覆盖。
    """
    factory, ids = asset_db
    with factory() as session:
        repo = SqlCollectionPlanRepository(session)
        scope = _scope(ids, ids.project_a)
        plan = _plan(ids, ids.project_a)
        repo.add(scope, plan)
        session.commit()

        updated = repo.save_update(scope, replace(plan, name="新名"), expected_version=1)
        assert updated.version == 2
        session.commit()
        assert repo.get(scope, plan.plan_id).name == "新名"  # type: ignore[union-attr]

        with pytest.raises(VersionConflictError):
            repo.save_update(scope, replace(plan, name="覆盖"), expected_version=1)


def test_save_update_on_missing_row_is_not_found(
    asset_db: tuple[sessionmaker[Session], AssetIds],
) -> None:
    """行不存在必须是 404 而不是 409：两者对客户端的含义完全不同。"""
    factory, ids = asset_db
    with factory() as session:
        repo = SqlCollectionSopRepository(session)
        scope = _scope(ids, ids.project_a)
        ghost = _sop(ids, ids.project_a, code="SOP-GHOST")
        with pytest.raises(ResourceNotFoundError):
            repo.save_update(scope, ghost, expected_version=1)


def test_save_update_is_project_scoped(
    asset_db: tuple[sessionmaker[Session], AssetIds],
) -> None:
    """跨项目写必须被拦下——不能靠"ID 存在"就允许更新。

    拦截发生在 ``ProjectScope.ensure``，与 ``add`` / 任务的 ``save`` 同一约定：
    实体自带的 ``project_id`` 与 scope 不符即拒绝。应用层的 ``get`` 会先返回 404，
    这里是仓储层的第二道校验（防止用例层漏掉那次读）。
    """
    factory, ids = asset_db
    with factory() as session:
        repo = SqlScenarioRepository(session)
        scope_a = _scope(ids, ids.project_a)
        scenario = _scenario(ids, ids.project_a)
        repo.add(scope_a, scenario)
        session.commit()

        with pytest.raises(CrossProjectAccessError):
            repo.save_update(
                _scope(ids, ids.project_b),
                replace(scenario, name="越权改名"),
                expected_version=1,
            )
        # 未改动：越权写不能留下任何痕迹。
        assert repo.get(scope_a, scenario.scenario_id).name == "场景"


def test_delete_draft_removes_row(
    asset_db: tuple[sessionmaker[Session], AssetIds],
) -> None:
    factory, ids = asset_db
    with factory() as session:
        repo = SqlActionSpecRepository(session)
        scope = _scope(ids, ids.project_a)
        spec = _spec(ids, ids.project_a)
        repo.add(scope, spec)
        session.commit()

        repo.delete_draft(scope, spec.action_spec_id)
        session.commit()
        assert repo.get(scope, spec.action_spec_id) is None


def _advance_status(session: Session, plan_id: UUID, status: CollectionAssetStatus) -> None:
    """把方案推进到目标状态。

    不能直接插入非 DRAFT 行：``trg_collection_plans_initial_status`` 会拒绝
    （这正是迁移 0041 要保证的事实）。因此逐级迁移，走真实触发器路径。
    """
    steps = {
        CollectionAssetStatus.DRAFT: (),
        CollectionAssetStatus.ACTIVE: ("ACTIVE",),
        CollectionAssetStatus.ARCHIVED: ("ACTIVE", "ARCHIVED"),
    }[status]
    for target in steps:
        session.execute(
            text("UPDATE collection_plans SET status = :status WHERE id = :id"),
            {"status": target, "id": plan_id.hex},
        )
    session.commit()


@pytest.mark.parametrize("status", [CollectionAssetStatus.ACTIVE, CollectionAssetStatus.ARCHIVED])
def test_delete_rejects_non_draft(
    asset_db: tuple[sessionmaker[Session], AssetIds],
    status: CollectionAssetStatus,
) -> None:
    factory, ids = asset_db
    with factory() as session:
        repo = SqlCollectionPlanRepository(session)
        scope = _scope(ids, ids.project_a)
        plan = _plan(ids, ids.project_a)
        repo.add(scope, plan)
        session.commit()
        _advance_status(session, plan.plan_id, status)

        with pytest.raises(ResourceImmutableError):
            repo.delete_draft(scope, plan.plan_id)
        assert repo.get(scope, plan.plan_id) is not None


def test_delete_rejects_referenced_draft_with_readable_error(
    asset_db: tuple[sessionmaker[Session], AssetIds],
) -> None:
    """被任务引用的 DRAFT 也不能删；错误必须是"被引用"而不是裸 IntegrityError。

    数据库的 ``ON DELETE RESTRICT`` 复合外键会兜底，但它抛的是方言相关的
    ``IntegrityError``，客户端只能看到笼统的 409。仓储必须先用可读语义拒绝。
    """
    factory, ids = asset_db
    with factory() as session:
        plan_repo = SqlCollectionPlanRepository(session)
        scope = _scope(ids, ids.project_a)
        plan = _plan(ids, ids.project_a)
        plan_repo.add(scope, plan)
        session.add(
            CollectionTaskModel(
                id=uuid4(),
                project_id=ids.project_a,
                code="T-1",
                name="任务",
                status="DRAFT",
                priority="P1",
                plan_id=plan.plan_id,
                created_by=ids.user,
                updated_by=ids.user,
            )
        )
        session.commit()

        with pytest.raises(ResourceImmutableError, match="已被采集任务引用"):
            plan_repo.delete_draft(scope, plan.plan_id)
        assert plan_repo.get(scope, plan.plan_id) is not None


def test_delete_cross_project_row_is_not_found(
    asset_db: tuple[sessionmaker[Session], AssetIds],
) -> None:
    factory, ids = asset_db
    with factory() as session:
        repo = SqlScenarioRepository(session)
        scenario = _scenario(ids, ids.project_a)
        repo.add(_scope(ids, ids.project_a), scenario)
        session.commit()

        with pytest.raises(ResourceNotFoundError):
            repo.delete_draft(_scope(ids, ids.project_b), scenario.scenario_id)
        assert repo.get(_scope(ids, ids.project_a), scenario.scenario_id) is not None


def test_create_all_installs_same_asset_guards_as_migration() -> None:
    """``create_all`` 与迁移 0041 必须装同一份素材守卫。

    两者不一致会让"测试库全绿、真实库不拦"只在生产暴露——这正是
    ``collection_guards.py`` 把 DDL 抽成一份的原因，这条测试守住它。
    """
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    try:
        with engine.connect() as connection:
            names = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                ).fetchall()
            }
    finally:
        engine.dispose()

    for table in ("collection_plans", "collection_sops", "scenarios", "action_specs"):
        assert f"trg_{table}_initial_status" in names
        assert f"trg_{table}_transition" in names
