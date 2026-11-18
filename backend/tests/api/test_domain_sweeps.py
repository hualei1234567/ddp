"""领域巡检 Worker：标注租约过期与采集任务数据检查的调度闭环。

worker 本身只是"调度器"，这里验证的是**调度之后状态真的变了**：
- 领了活不干、租约已过的分配被推进到 EXPIRED；
- 提交了的采集任务被推进到 DATA_CHECKING。

只测 worker 返回计数是不够的——那正是"能力存在但没有触发点"的原缺陷形态。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.infrastructure.db.models.annotation_work import (
    AnnotationAssignment as AnnotationAssignmentModel,
)
from app.workers.domain_sweeps import DomainSweepWorker
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _expire_lease(
    factory: sessionmaker[Session],
    assignment_id: UUID,
    *,
    expires_at: datetime,
) -> None:
    with factory() as session:
        session.execute(
            update(AnnotationAssignmentModel)
            .where(AnnotationAssignmentModel.id == assignment_id)
            .values(lease_expires_at=expires_at)
            .execution_options(synchronize_session=False)
        )
        session.commit()


def test_expire_assignments_pushes_claimed_lease_to_expired(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-SWEEP-001")
    schema = create_published_schema(editor, owner, ids.project_a, "schema-sweep")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-sweep")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-sweep",
        claim_only=True,
    )
    assignment = work["assignment"]
    assert assignment["status"] == "CLAIMED"
    _expire_lease(factory, UUID(assignment["assignment_id"]), expires_at=NOW - timedelta(hours=1))

    worker = DomainSweepWorker(factory)
    assert worker.expire_assignments(now=NOW) == 1
    # 幂等：同一批已过期的记录再扫一次不会重复推进。
    assert worker.expire_assignments(now=NOW) == 0

    detail = editor.get(
        f"/api/v1/projects/{ids.project_a}/annotation-assignments/{assignment['assignment_id']}"
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "EXPIRED"

    # 未到期的租约不能被误推进。
    other_episode = seed_episode(factory, ids.project_a, ids.editor_a, "EP-SWEEP-002")
    # 样本集是一次性资源：快照被一个任务消费后不能再给第二个任务用。
    other_sample_set = create_snapshot_set(editor, ids.project_a, "sample-sweep-1b")
    other_work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        other_sample_set,
        other_episode,
        ids.editor_a,
        "task-sweep-2",
        claim_only=True,
        episode_ids=[other_episode],
    )
    assert worker.expire_assignments(now=NOW) == 0
    other = editor.get(
        f"/api/v1/projects/{ids.project_a}/annotation-assignments/"
        f"{other_work['assignment']['assignment_id']}"
    )
    assert other.json()["status"] == "CLAIMED"


def test_sweep_once_runs_both_sweeps(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """两类巡检互相独立：一次 sweep_once 同时推进租约与采集任务。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    # 1) 一个已过租约的标注分配。
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-SWEEP-003")
    schema = create_published_schema(editor, owner, ids.project_a, "schema-sweep-2")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-sweep-2")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-sweep-3",
        claim_only=True,
    )
    _expire_lease(
        factory, UUID(work["assignment"]["assignment_id"]), expires_at=NOW - timedelta(hours=1)
    )

    # 2) 一个已提交、等待数据检查的采集任务（无导入：检查结论如实为未通过，
    #    但仍推进到 DATA_CHECKING，由人决定 accept / reject）。
    task = editor.post(
        f"/api/v1/projects/{ids.project_a}/collection-tasks",
        json={
            "code": "sweep-task",
            "name": "巡检采集任务",
            "priority": "P1",
            "target_episode_count": 1,
            "target_duration_sec": 60,
        },
    ).json()
    version = int(task["version"])
    for action, actor in (
        ("submit-review", editor),
        ("approve", owner),
        ("ready", owner),
        ("assign", owner),
        ("start", owner),
        ("submit", editor),
    ):
        response = actor.post(
            f"/api/v1/projects/{ids.project_a}/collection-tasks/{task['task_id']}/actions/{action}",
            headers={"If-Match": f'"{version}"'},
            json={"assigned_to": str(ids.editor_a)} if action == "assign" else {},
        )
        assert response.status_code == 200, (action, response.text)
        version = int(response.json()["version"])
    assert response.json()["status"] == "SUBMITTED"

    # 3) 一个已过截止时间的标注任务：逾期扫描只读计数，不改派、不关闭。
    overdue_sample_set = create_snapshot_set(editor, ids.project_a, "sample-sweep-overdue")
    overdue_created = editor.post(
        url(ids.project_a, "/annotation-tasks"),
        json={
            "code": "sweep-overdue",
            "name": "逾期巡检任务",
            "sample_set_id": overdue_sample_set["sample_set_id"],
            "schema_id": schema["schema_id"],
            "target_item_count": 1,
            "target_episode_count": 1,
            "due_at": (NOW - timedelta(days=1)).isoformat(),
        },
    )
    assert overdue_created.status_code == 201, overdue_created.text
    overdue_task_id = overdue_created.json()["task_id"]
    assert overdue_created.json()["is_overdue"] is True

    result = DomainSweepWorker(factory).sweep_once(now=NOW)
    assert result == {
        "expired_assignments": 1,
        "overdue_annotation_tasks": 1,
        "checked_collection_tasks": 1,
        "stale_dataset_versions": 0,
    }
    # 逾期只是可见事实：任务状态与截止时间都不被巡检改写。
    overdue_after = editor.get(url(ids.project_a, f"/annotation-tasks/{overdue_task_id}"))
    assert overdue_after.status_code == 200, overdue_after.text
    assert overdue_after.json()["status"] == "DRAFT"
    assert overdue_after.json()["is_overdue"] is True

    assert (
        editor.get(
            f"/api/v1/projects/{ids.project_a}/annotation-assignments/"
            f"{work['assignment']['assignment_id']}"
        ).json()["status"]
        == "EXPIRED"
    )
    checked = editor.get(
        f"/api/v1/projects/{ids.project_a}/collection-tasks/{task['task_id']}"
    ).json()
    assert checked["status"] == "DATA_CHECKING"
    # 没有导入 → 检查不通过，但不得伪造通过。
    assert checked["quality_summary"]["passed"] is False
    assert "imports_present" in checked["quality_summary"]["failed_rules"]
