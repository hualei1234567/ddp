"""领域闭环用例：采集任务数据检查、Episode 计数与租约过期。

这三件事的共同点是"能力存在但没有触发点"，用户视角表现为按钮永远不出现：
- 采集任务停在 SUBMITTED，验收/完成永远不可达；
- complete 的"验收数量"永远为 0，只能靠豁免；
- 标注分配租约过期没人调度，领了活不干也永远占容量。

本文件走真实链路：任务按状态机一路推进到 SUBMITTED，Episode 通过 HTTP
接口真实创建并推进到 ACCEPTED，再由系统用例驱动数据检查——不直接改状态列。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.application.collection.data_check import CollectionDataCheckUseCases
from app.infrastructure.db.models.datasources import (
    DataSourceImportModel,
    DataSourceModel,
    MediaAssetModel,
)
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository as SqlCollectionAuditRepository,
)
from app.infrastructure.repositories.collection_audit import (
    SqlOutboxRepository as SqlCollectionOutboxRepository,
)
from app.infrastructure.repositories.collection_tasks import SqlCollectionTaskRepository
from app.infrastructure.repositories.datasources import (
    SqlDataSourceImportRepository,
    SqlMediaAssetRepository,
)
from app.infrastructure.repositories.projects import SqlProjectMemberRepository
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.ddp310_support import (
    episode_payload,
    manifest_for_episode,
    project_url,
    seed_valid_asset,
)
from tests.fakes_s3 import FakeMultipartStorage

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
SHA = "sha256:" + "b" * 64


def _tasks_url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/collection-tasks{suffix}"


@pytest.fixture()
def loop_clients(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> Iterator[tuple[sessionmaker[Session], ApiIds, Any, Any]]:
    """受管客户端：测试结束显式关闭。

    ``TestClient`` 内部持有 anyio 流；不关闭时会在 GC 期抛
    ``PytestUnraisableExceptionWarning``（被 ``filterwarnings=error`` 判成失败）。
    CI 上已经因此红过一次，这里显式收尾。
    """
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a, object_storage=FakeMultipartStorage())
    owner = make_client(factory, ids.owner_a)
    try:
        yield factory, ids, editor, owner
    finally:
        editor.close()
        owner.close()


@contextmanager
def _data_check_session(
    factory: sessionmaker[Session],
) -> Iterator[CollectionDataCheckUseCases]:
    """在受管会话里运行数据检查。

    直接 ``factory()`` 后手动 ``_uow.close()`` 依赖私有属性，且会话/连接
    泄漏会在 CI 上表现为 ``PytestUnraisableExceptionWarning``（被
    ``filterwarnings=error`` 判成失败）。这里用上下文管理器保证一定关闭。
    """
    with factory() as session:
        yield CollectionDataCheckUseCases(
            SqlCollectionTaskRepository(session),
            SqlDataSourceImportRepository(session),
            SqlMediaAssetRepository(session),
            SqlProjectMemberRepository(session),
            SqlCollectionAuditRepository(session),
            SqlCollectionOutboxRepository(session),
            session,
        )


def _seed_import_with_asset(
    factory: sessionmaker[Session],
    project_id: UUID,
    task_id: UUID,
    actor_id: UUID,
) -> None:
    """造一条"导入已成功并产出媒体资产"的事实。

    数据源必须先 flush 再插导入：两行在同一个 flush 里时，SQLAlchemy 按
    表依赖排序仍会把导入的复合外键(project_id, data_source_id)解析到同一批
    未落库的行上，SQLite 报 FOREIGN KEY constraint failed。
    """
    with factory() as session:
        source_id, import_id = uuid4(), uuid4()
        session.add(
            DataSourceModel(
                id=source_id,
                project_id=project_id,
                code=f"src-{source_id.hex[:8]}",
                name="主链路数据源",
                kind="HUGGINGFACE",
                repo_id="ddp/loop-closure",
                revision="c" * 40,
                license="Apache-2.0",
                access_condition="PUBLIC",
                is_gated=False,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        session.add(
            DataSourceImportModel(
                id=import_id,
                project_id=project_id,
                data_source_id=source_id,
                collection_task_id=task_id,
                status="SUCCEEDED",
                scope="SAMPLE",
                tool_name="huggingface_hub",
                tool_version="0.27.1",
                sample_limit=1,
                imported_row_count=1,
                imported_file_count=1,
                started_at=NOW,
                finished_at=NOW,
                attempt=1,
                max_attempts=3,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.add(
            MediaAssetModel(
                id=uuid4(),
                project_id=project_id,
                data_source_id=source_id,
                import_id=import_id,
                episode_key="demo_001",
                path="videos/demo_001_main.mp4",
                camera="main",
                object_key=f"raw/{project_id}/{import_id}/demo_001_main.mp4",
                size_bytes=2048,
                sha256=SHA,
                content_type="video/mp4",
                task_name="demo",
                created_by=actor_id,
                updated_by=actor_id,
                source_fps=30.0,
                excerpt_start_seconds=0.0,
                excerpt_seconds=5.0,
            )
        )
        session.commit()


def _create_task(client: object, project_id: UUID, *, code: str, target: int) -> dict:
    created = client.post(  # type: ignore[attr-defined]
        _tasks_url(project_id),
        json={
            "code": code,
            "name": code,
            "priority": "P1",
            "target_episode_count": target,
            "target_duration_sec": 60,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _act(
    client: object,
    project_id: UUID,
    task_id: UUID,
    action: str,
    version: int,
    *,
    json: dict | None = None,
    actor: UUID | None = None,
) -> dict:
    kwargs: dict[str, object] = {
        "headers": {"If-Match": f'"{version}"'},
        "json": json or {},
    }
    if actor is not None:
        kwargs["headers"] = {
            "If-Match": f'"{version}"',
            "X-DDP-Actor-Id": str(actor),
        }
    response = client.post(  # type: ignore[attr-defined]
        _tasks_url(project_id, f"/{task_id}/actions/{action}"), **kwargs
    )
    assert response.status_code == 200, (action, response.text)
    return response.json()


def _drive_to_submitted(
    client: object,
    ids: ApiIds,
    owner_client: object,
    task: dict,
    *,
    assignee: UUID,
) -> dict:
    """按状态机真实路径把任务推进到 SUBMITTED。

    DRAFT → submit-review → PENDING_REVIEW → approve(非创建人) → APPROVED
    → ready → READY → assign → ASSIGNED → start → IN_PROGRESS → submit → SUBMITTED
    """
    task_id = UUID(task["task_id"])
    version = int(task["version"])
    for action in ("submit-review", "ready", "start", "submit"):
        payload = None
        if action == "start":
            payload = {"assigned_to": str(assignee)}
        task = _act(client, ids.project_a, task_id, action, version, json=payload)
        version = int(task["version"])
        # approve / assign 必须由非创建人执行：创建人不能审批自己的任务，
        # 分配动作需要项目成员身份且创建人自己分配给自己不构成"分配"。
        if action in ("submit-review", "ready"):
            owner_task = _act(
                owner_client,
                ids.project_a,
                task_id,
                "approve" if action == "submit-review" else "assign",
                version,
                json={"assigned_to": str(assignee)} if action == "ready" else None,
            )
            version = int(owner_task["version"])
            task = owner_task
    return task


def test_collection_task_data_check_advances_to_data_checking(
    loop_clients: tuple[sessionmaker[Session], ApiIds, Any, Any],
) -> None:
    factory, ids, client, owner_client = loop_clients
    task = _create_task(client, ids.project_a, code="loop-task", target=1)
    task_id = UUID(task["task_id"])
    _seed_import_with_asset(factory, ids.project_a, task_id, ids.editor_a)

    submitted = _drive_to_submitted(client, ids, owner_client, task, assignee=ids.editor_a)
    assert submitted["status"] == "SUBMITTED"

    with _data_check_session(factory) as use_cases:
        outcomes = use_cases.run_once(limit=10, now=NOW)

    assert [item.task_id for item in outcomes] == [task_id]
    assert outcomes[0].passed, outcomes[0].failed_rules

    after = client.get(_tasks_url(ids.project_a, f"/{task_id}")).json()
    assert after["status"] == "DATA_CHECKING"
    summary = after["quality_summary"]
    assert summary["imports"] == 1
    assert summary["assets"] == 1
    assert summary["episodes"] == 1
    assert summary["passed"] is True

    # 数据检查是幂等的：状态已变，再扫一次不再处理。
    with _data_check_session(factory) as again_use_cases:
        again = again_use_cases.run_once(limit=10, now=NOW)
    assert again == []


def test_collection_task_complete_uses_live_episode_counts(
    loop_clients: tuple[sessionmaker[Session], ApiIds, Any, Any],
) -> None:
    """验收数量从 Episode 事实读取，不依赖没人写过的计数器。

    Episode 走真实 HTTP 链路创建并推进到 ACCEPTED；随后任务的 complete
    判定必须看到 1 条已验收片段，无需 waiver。
    """
    factory, ids, client, owner_client = loop_clients
    task = _create_task(client, ids.project_a, code="loop-count", target=1)
    task_id = UUID(task["task_id"])

    asset_id = seed_valid_asset(
        factory,
        ids.project_a,
        ids.editor_a,
        label="loop",
        metadata={"camera": "front", "source_fps": 30.0},
    )
    episode = client.post(
        project_url(ids.project_a, "/episodes"),
        json=episode_payload(
            asset_id,
            code="EP-LOOP-001",
            start_time_ns=1_000_000_000,
            end_time_ns=2_000_000_000,
            task_id=str(task_id),
        ),
        headers={"Idempotency-Key": "loop-episode-0001"},
    )
    assert episode.status_code == 201, episode.text
    episode_body = episode.json()
    base = project_url(ids.project_a, f"/episodes/{episode_body['episode_id']}")
    for action, payload, version in (
        ("ready", {"manifest": manifest_for_episode(episode_body), "frame_count": 30}, "1"),
        ("quality-check", None, "3"),
        ("quality-result", {"quality_status": "PASS"}, "4"),
        ("accept", None, "5"),
    ):
        response = client.post(
            f"{base}/actions/{action}",
            json=payload,
            headers={
                "If-Match": f'"{version}"',
                "Idempotency-Key": f"loop-episode-{action}-0001",
            },
        )
        assert response.status_code == 200, (action, response.text)
    assert response.json()["status"] == "ACCEPTED"

    submitted = _drive_to_submitted(client, ids, owner_client, task, assignee=ids.editor_a)
    assert submitted["status"] == "SUBMITTED"

    with _data_check_session(factory) as use_cases:
        outcomes = use_cases.run_once(limit=10, now=NOW)
    assert [item.task_id for item in outcomes] == [task_id]

    current = client.get(_tasks_url(ids.project_a, f"/{task_id}")).json()
    assert current["status"] == "DATA_CHECKING"
    assert current["submitted_episode_count"] == 1
    assert current["accepted_episode_count"] == 1

    accepted = _act(client, ids.project_a, task_id, "accept", int(current["version"]))
    assert accepted["status"] == "ACCEPTED"
    completed = _act(client, ids.project_a, task_id, "complete", int(accepted["version"]))
    assert completed["status"] == "COMPLETED"
    assert completed["accepted_episode_count"] == 1
    assert completed["submitted_episode_count"] == 1
