"""V51-017 并发竞态：真实 PostgreSQL + 真实行锁 + 真实线程。

## 为什么要单独一个文件

并发语义只在**真实 PostgreSQL** 下成立：SQLite 忽略 ``FOR UPDATE``（靠单写
事务串行），因此 SQLite 上的"并发测试"永远通过，证明不了任何东西。本文件在
未配置 PostgreSQL 时 **skip 并标注 BLOCKED**，而不是用 mock 假装并发跑过了。

## 怎么真实复现竞态（不用 mock、不用 sleep 猜测时序）

竞态窗口是「查引用 → 写状态」之间的那段。要证明它被锁闭合，必须让两个事务
**真的同时**在这段里：

1. 线程 A 开启事务，调用与生产完全相同的用例方法
   （``assert_freeze_allowed`` / ``assert_destroy_allowed``），拿到行锁后
   **停在事务中不提交**，用 ``threading.Event`` 通知主线程。
2. 线程 B 随后调用另一侧的用例方法。它会在 ``SELECT ... FOR UPDATE`` 上
   **真实阻塞**——这不是 mock，是 PostgreSQL 的行锁等待。
3. 主线程断言"B 还没完成"（证明它确实被挡住，而不是碰巧先跑完），
   然后让 A 提交。
4. B 解除阻塞、重新读取引用集合，得到**已提交后的最新事实**，据此拒绝。

每一步都是真实的数据库行为。``Event`` 只用于决定"何时提交"，不用于伪造
阻塞——阻塞本身由锁造成，并由"B 未完成"的断言证明。

## 覆盖的两条验收

- 冻结进行中时删除被拒（删除方等到冻结提交，读到新引用 → 拒绝）；
- 删除进行中时冻冻结被拒（冻结方等到销毁提交，读到 TOMBSTONED → 拒绝）。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.application.retention.use_cases import ReferenceImpactUseCases
from app.domain.datasets.enums import DatasetVersionStatus
from app.domain.identity.models import ProjectRole, ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import devices as _devices  # noqa: F401
from app.infrastructure.db.models import ingestion as _ingestion  # noqa: F401
from app.infrastructure.db.models.datasets import DatasetVersionModel
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.db.session import create_session_factory
from app.infrastructure.repositories.retention import SqlReferenceImpactRepository
from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import (
    create_dataset,
    create_policies,
    seed_dataset_episodes,
    version_payload,
)
from tests.api.dataset_support import (
    url as dataset_url,
)
from tests.api.export_support import FakeExportStorage

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get(
    "DDP_DATABASE_URL",
    "",
)
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)

#: 行锁等待的上限：够了才说明"真的被挡住"，太短会把调度延迟误判成阻塞。
_BLOCK_PROOF_SECONDS = 1.5
_THREAD_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class _Harness:
    factory: sessionmaker[Session]
    ids: ApiIds
    #: 一个 READY（尚未冻结）版本 + 它引用的资产
    version_id: UUID
    asset_ids: tuple[UUID, ...]
    scope: ProjectScope
    storage: FakeExportStorage


@pytest.fixture()
def harness() -> Iterator[_Harness]:
    schema = f"ddp_v51017_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    engine: Engine | None = None
    try:
        with admin.connect() as connection:
            connection.execute(CreateSchema(schema))
        engine = create_engine(
            _POSTGRES_URL,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        Base.metadata.create_all(engine)
        factory = create_session_factory(engine)

        project_a, project_b = uuid4(), uuid4()
        editor_a, owner_a = uuid4(), uuid4()
        ids = ApiIds(project_a, project_b, editor_a, owner_a, uuid4(), uuid4())
        with factory() as session:
            session.add_all(
                [
                    Project(id=project_a, code=f"p-{project_a.hex[:8]}", name="项目 A"),
                    Project(id=project_b, code=f"p-{project_b.hex[:8]}", name="项目 B"),
                    User(id=editor_a, display_name="编辑者"),
                    User(id=owner_a, display_name="负责人"),
                ]
            )
            session.flush()
            session.add_all(
                [
                    ProjectMember(
                        project_id=project_a,
                        user_id=editor_a,
                        role=ProjectRole.EDITOR.value,
                    ),
                    ProjectMember(
                        project_id=project_a,
                        user_id=owner_a,
                        role=ProjectRole.OWNER.value,
                    ),
                ]
            )
            session.commit()

        # 复用数据集域的既有种子与真实 build → validate，得到 READY 版本
        # （此时 dataset_items 已存在，但版本尚未冻结 → frozen_at 为 NULL）。
        seed_dataset_episodes(factory, project_a, editor_a, count=10)
        storage = FakeExportStorage()
        editor = make_client(factory, editor_a, object_storage=storage)
        dataset = create_dataset(editor, project_a, owner_a, f"ds-{uuid4().hex[:8]}")
        policies = create_policies(editor, project_a, dataset["dataset_id"])
        created = editor.post(
            dataset_url(project_a, f"/datasets/{dataset['dataset_id']}/versions"),
            json=version_payload(policies),
        )
        assert created.status_code == 201, created.text
        version_id = UUID(created.json()["dataset_version_id"])
        built = editor.post(
            dataset_url(
                project_a,
                f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/build",
            ),
            headers={"If-Match": '"1"'},
            json={"reason": None},
        )
        assert built.status_code == 200, built.text
        validated = editor.post(
            dataset_url(
                project_a,
                f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/validate",
            ),
            headers={"If-Match": f'"{built.json()["version"]}"'},
            json={},
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["status"] == "READY"

        with factory() as session:
            asset_ids = tuple(
                session.scalars(
                    select(RawAssetModel.id).where(RawAssetModel.project_id == project_a)
                ).all()
            )
        assert asset_ids
        yield _Harness(
            factory=factory,
            ids=ids,
            version_id=version_id,
            asset_ids=asset_ids,
            scope=ProjectScope(project_id=project_a, actor_id=editor_a),
            storage=storage,
        )
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


@dataclass
class _Unit:
    """一个独立的数据库会话 + 与生产装配一致的保留策略用例。

    两个线程各自持有**独立会话**（独立事务、独立连接），这是复现行锁竞态的
    前提：同一会话内的加锁不会自我阻塞。
    """

    session: Session
    use_cases: ReferenceImpactUseCases

    def close(self) -> None:
        try:
            self.session.rollback()
        finally:
            self.session.close()


def _unit(harness: _Harness) -> _Unit:
    session = harness.factory()
    return _Unit(
        session=session,
        use_cases=ReferenceImpactUseCases(SqlReferenceImpactRepository(session)),
    )


def _write_frozen(harness: _Harness, session: Session) -> None:
    """在**调用方事务内**把版本置为 FROZEN（写 frozen_at 与状态）。

    为什么不走 HTTP：HTTP 用例自己 commit，事务无法停留在"已加锁、未提交"
    的窗口里，竞态就复现不出来。这里只跳过 API 外壳，写的仍是数据集域触发器
    允许的合法迁移 ``READY --freeze--> FROZEN`` 涉及的同一组列。
    """
    # 冻结标记必须成套写入，否则会被库层 CHECK
    # ``frozen_requires_approval_and_leakage`` 拒绝——这不是可以省略的细节，
    # 而是"冻结必须带审批与泄漏结论"这条不变量本身。
    session.execute(
        update(DatasetVersionModel)
        .where(DatasetVersionModel.id == harness.version_id)
        .values(
            status=DatasetVersionStatus.FROZEN.value,
            frozen_at=datetime.now(UTC),
            frozen_by=harness.scope.actor_id,
            freeze_approval_id=uuid4(),
        )
        .execution_options(synchronize_session=False)
    )


# --------------------------------------------------------------------------- #
# 竞态 1：冻结进行中 → 删除被拒
# --------------------------------------------------------------------------- #


def test_delete_blocked_while_freeze_in_progress(harness: _Harness) -> None:
    """冻结事务持锁未提交时，另一个线程的删除必须**阻塞**，提交后被拒。

    这正是原始缺陷的场景：删除方若在冻结提交前读到"无冻结引用"，就会
    放行墓碑，而冻结完成后该版本立刻指向已销毁内容。
    """
    asset_id = harness.asset_ids[0]
    freeze_locked = threading.Event()
    #: 由主线程序在"确认删除方已被锁挡住"之后 set，用来放行冻结方提交。
    #: 绝不能由删除方自己 set——那会让冻结方在删除方真正尝试加锁之前就提交，
    #: 窗口根本不存在，测试就变成永远通过的空壳。
    release_freezer = threading.Event()
    delete_finished = threading.Event()
    outcomes: dict[str, Any] = {}

    def _freezer() -> None:
        unit = _unit(harness)
        try:
            # 与生产冻结路径相同的准入：锁住本版本引用的资产行。
            unit.use_cases.assert_freeze_allowed(harness.scope, harness.version_id)
            _write_frozen(harness, unit.session)
            freeze_locked.set()
            # 停在事务中不提交，让删除方真实地撞上行锁。
            release_freezer.wait(timeout=_THREAD_TIMEOUT_SECONDS)
            unit.session.commit()
        except Exception as exc:  # pragma: no cover - 失败时给出可诊断信息
            outcomes["freezer_error"] = repr(exc)
        finally:
            unit.close()

    def _deleter() -> None:
        unit = _unit(harness)
        try:
            # 直接尝试销毁：这里必须在 FOR UPDATE 上真实阻塞，
            # 阻塞本身就证明了锁生效，不需要任何额外的同步信号。
            unit.use_cases.assert_destroy_allowed(harness.scope, asset_id, action="tombstone")
            outcomes["deleter_allowed"] = True
        except Exception as exc:
            outcomes["deleter_error"] = exc
        finally:
            delete_finished.set()
            unit.close()

    freezer_thread = threading.Thread(target=_freezer, daemon=True)
    deleter_thread = threading.Thread(target=_deleter, daemon=True)
    freezer_thread.start()
    assert freeze_locked.wait(timeout=_THREAD_TIMEOUT_SECONDS), "冻结方未能取得行锁"
    deleter_thread.start()

    # 关键断言：删除方**确实被行锁挡住**，而不是碰巧先跑完了。
    assert not delete_finished.wait(
        timeout=_BLOCK_PROOF_SECONDS
    ), "删除未被阻塞：说明行锁没有生效，并发保护存在窗口"
    # 删除方确实被锁挡住后，才放行冻结方提交。
    release_freezer.set()
    assert delete_finished.wait(timeout=_THREAD_TIMEOUT_SECONDS), "删除方未在提交后返回"
    freezer_thread.join(timeout=_THREAD_TIMEOUT_SECONDS)
    deleter_thread.join(timeout=_THREAD_TIMEOUT_SECONDS)

    assert "freezer_error" not in outcomes, outcomes.get("freezer_error")
    assert outcomes.get("deleter_allowed") is not True, "冻结已提交，删除仍被放行"
    error = outcomes.get("deleter_error")
    assert error is not None
    assert getattr(error, "code", None) == "raw_asset_referenced_by_frozen_dataset"
    details = getattr(error, "details", {}) or {}
    assert details.get("dataset_version_count", 0) >= 1
    assert any(
        item["dataset_version_id"] == str(harness.version_id)
        for item in details.get("frozen_versions", [])
    )

    with harness.factory() as session:
        status = session.execute(
            select(RawAssetModel.status).where(RawAssetModel.id == asset_id)
        ).scalar_one()
    assert status != "TOMBSTONED"


# --------------------------------------------------------------------------- #
# 竞态 2：删除进行中 → 冻结被拒
# --------------------------------------------------------------------------- #


def test_freeze_blocked_while_delete_in_progress(harness: _Harness) -> None:
    """销毁事务持锁未提交时，另一个线程的冻结必须阻塞，提交后因内容已丢弃而失败。

    反向窗口：冻结若在销毁提交前读到 VALID，就会成功冻结一个即将失去内容的
    版本。
    """
    asset_id = harness.asset_ids[0]
    delete_locked = threading.Event()
    #: 同竞态 1：只能由主线程在证明冻结方被挡住后 set。
    release_deleter = threading.Event()
    freeze_finished = threading.Event()
    outcomes: dict[str, Any] = {}

    def _deleter() -> None:
        unit = _unit(harness)
        try:
            unit.use_cases.assert_destroy_allowed(harness.scope, asset_id, action="tombstone")
            unit.session.execute(
                update(RawAssetModel)
                .where(RawAssetModel.id == asset_id)
                .values(status="TOMBSTONED")
                .execution_options(synchronize_session=False)
            )
            delete_locked.set()
            release_deleter.wait(timeout=_THREAD_TIMEOUT_SECONDS)
            unit.session.commit()
        except Exception as exc:  # pragma: no cover
            outcomes["deleter_error"] = repr(exc)
        finally:
            unit.close()

    def _freezer() -> None:
        unit = _unit(harness)
        try:
            # 必须在批量 FOR UPDATE 上真实阻塞。
            unit.use_cases.assert_freeze_allowed(harness.scope, harness.version_id)
            outcomes["freeze_allowed"] = True
        except Exception as exc:
            outcomes["freeze_error"] = exc
        finally:
            freeze_finished.set()
            unit.close()

    deleter_thread = threading.Thread(target=_deleter, daemon=True)
    freezer_thread = threading.Thread(target=_freezer, daemon=True)
    deleter_thread.start()
    assert delete_locked.wait(timeout=_THREAD_TIMEOUT_SECONDS), "销毁方未能取得行锁"
    freezer_thread.start()

    assert not freeze_finished.wait(
        timeout=_BLOCK_PROOF_SECONDS
    ), "冻结未被阻塞：说明行锁没有生效，反向窗口仍然存在"
    release_deleter.set()
    assert freeze_finished.wait(timeout=_THREAD_TIMEOUT_SECONDS), "冻结方未在提交后返回"
    deleter_thread.join(timeout=_THREAD_TIMEOUT_SECONDS)
    freezer_thread.join(timeout=_THREAD_TIMEOUT_SECONDS)

    assert "deleter_error" not in outcomes, outcomes.get("deleter_error")
    assert outcomes.get("freeze_allowed") is not True, "内容已销毁，冻结仍被放行"
    error = outcomes.get("freeze_error")
    assert error is not None
    details = getattr(error, "details", {}) or {}
    assert details.get("reason") == "raw_asset_content_discarded"
    assert str(asset_id) in details.get("discarded_asset_ids", [])


# --------------------------------------------------------------------------- #
# 对照：串行（无竞争）时两侧都正常
# --------------------------------------------------------------------------- #


def test_sequential_freeze_then_delete_blocks(harness: _Harness) -> None:
    """无并发时：先冻结、后删除 → 删除被拒（证明保护不依赖线程时序）。"""
    asset_id = harness.asset_ids[0]

    unit = _unit(harness)
    try:
        unit.use_cases.assert_freeze_allowed(harness.scope, harness.version_id)
        _write_frozen(harness, unit.session)
        unit.session.commit()
    finally:
        unit.close()

    with harness.factory() as session:
        impact = SqlReferenceImpactRepository(session).raw_asset_impact(harness.scope, asset_id)
        assert impact.protected is True
        assert any(item.dataset_version_id == harness.version_id for item in impact.references)
        with pytest.raises(Exception) as excinfo:
            ReferenceImpactUseCases(SqlReferenceImpactRepository(session)).assert_destroy_allowed(
                harness.scope, asset_id, action="tombstone"
            )
    assert getattr(excinfo.value, "code", None) == "raw_asset_referenced_by_frozen_dataset"
