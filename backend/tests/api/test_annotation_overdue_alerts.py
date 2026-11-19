"""巡检集成：标注任务逾期提醒事件（N6-005）。

验证巡检真的把"进入逾期"变成留痕事件，且重复扫描不重复发：
- 首次逾期发一次 ``annotation.task.overdue``，payload 含任务/项目/到期时间/逾期秒数；
- 同一窗口重复巡检不再发；
- 延期恢复后再到期 → 新窗口 → 再发一次；
- 已关闭任务不发；每个事件严格落在任务自己的项目下。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.annotation.overdue_alerts import OVERDUE_ALERT_EVENT
from app.domain.identity.models import ProjectRole
from app.infrastructure.db.models.annotation_work import (
    AnnotationTask as AnnotationTaskModel,
)
from app.infrastructure.db.models.collection import (
    AuditLog,
)
from app.infrastructure.db.models.collection import (
    OutboxEvent as OutboxEventModel,
)
from app.infrastructure.db.models.identity import ProjectMember
from app.workers.domain_sweeps import DomainSweepWorker
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_snapshot_set,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _create_task(
    client: TestClient,
    project_id: UUID,
    schema: dict[str, object],
    sample_set: dict[str, object],
    code: str,
    *,
    due_at: datetime,
) -> dict[str, object]:
    response = client.post(
        url(project_id, "/annotation-tasks"),
        json={
            "code": code,
            "name": f"任务 {code}",
            "sample_set_id": sample_set["sample_set_id"],
            "schema_id": schema["schema_id"],
            "target_item_count": 1,
            "target_episode_count": 1,
            "due_at": due_at.isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _overdue_events(factory: sessionmaker[Session]) -> list[OutboxEventModel]:
    with factory() as session:
        return list(
            session.scalars(
                select(OutboxEventModel)
                .where(OutboxEventModel.event_type == OVERDUE_ALERT_EVENT)
                .order_by(OutboxEventModel.created_at, OutboxEventModel.id)
            )
        )


def test_sweep_emits_overdue_alert_once_per_window(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    seed_episode(factory, ids.project_a, ids.editor_a, "EP-ALERT-001")
    schema = create_published_schema(editor, owner, ids.project_a, "schema-alert")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-alert")
    due_at = NOW - timedelta(days=1)
    task = _create_task(editor, ids.project_a, schema, sample_set, "task-alert", due_at=due_at)
    assert task["is_overdue"] is True

    worker = DomainSweepWorker(factory)
    worker.alert_overdue_tasks(now=NOW)

    events = _overdue_events(factory)
    assert len(events) == 1
    event = events[0]
    assert event.project_id == ids.project_a
    assert event.aggregate_type == "ANNOTATION_TASK"
    assert event.aggregate_id == UUID(str(task["task_id"]))
    # 留痕类：写入即 RECORDED，不进投递重试队列。
    assert event.status == "RECORDED"
    assert event.dispatch is False
    assert event.payload["task_id"] == task["task_id"]
    assert event.payload["task_code"] == "task-alert"
    assert event.payload["project_id"] == str(ids.project_a)
    assert event.payload["due_at"] == due_at.isoformat()
    assert event.payload["overdue_seconds"] == 86400
    assert event.payload["overdue_window"] == f"{task['task_id']}:{due_at.isoformat()}"

    # 同一条审计留痕：动作是 overdue，结果 SUCCESS，执行人是项目负责人。
    with factory() as session:
        audits = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "overdue",
                    AuditLog.resource_id == UUID(str(task["task_id"])),
                )
            )
        )
    assert len(audits) == 1
    assert audits[0].actor_user_id == ids.owner_a
    assert audits[0].result == "SUCCESS"
    assert audits[0].details["overdue_seconds"] == 86400

    # 重复巡检：同一窗口不再发；任务状态仍不被改写。
    assert worker.alert_overdue_tasks(now=NOW) == 0
    assert len(_overdue_events(factory)) == 1
    assert (
        editor.get(url(ids.project_a, f"/annotation-tasks/{task['task_id']}")).json()["status"]
        == "DRAFT"
    )


def test_rescheduled_task_alerts_again_only_after_new_window(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    seed_episode(factory, ids.project_a, ids.editor_a, "EP-ALERT-002")
    schema = create_published_schema(editor, owner, ids.project_a, "schema-alert-2")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-alert-2")
    task = _create_task(
        editor,
        ids.project_a,
        schema,
        sample_set,
        "task-alert-2",
        due_at=NOW - timedelta(hours=1),
    )
    worker = DomainSweepWorker(factory)
    worker.alert_overdue_tasks(now=NOW)
    assert len(_overdue_events(factory)) == 1

    # 延期：离开逾期状态，不产生"恢复"事件，也不重复发旧窗口。
    rescheduled_due = NOW + timedelta(hours=1)
    updated = editor.patch(
        url(ids.project_a, f"/annotation-tasks/{task['task_id']}"),
        headers={"If-Match": '"1"'},
        json={"due_at": rescheduled_due.isoformat()},
    )
    assert updated.status_code == 200, updated.text
    worker.alert_overdue_tasks(now=NOW)
    assert len(_overdue_events(factory)) == 1

    # 新截止时间再次到期：新窗口，再发一次。
    later = rescheduled_due + timedelta(hours=1)
    assert worker.alert_overdue_tasks(now=later) == 1
    events = _overdue_events(factory)
    assert len(events) == 2
    assert events[1].payload["due_at"] == rescheduled_due.isoformat()
    assert events[1].payload["overdue_seconds"] == 3600
    # 旧窗口仍然是终态记录，不会被覆盖或删除。
    assert events[0].payload["due_at"] != events[1].payload["due_at"]


def test_closed_task_does_not_alert(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    seed_episode(factory, ids.project_a, ids.editor_a, "EP-ALERT-003")
    schema = create_published_schema(editor, owner, ids.project_a, "schema-alert-3")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-alert-3")
    task = _create_task(
        editor,
        ids.project_a,
        schema,
        sample_set,
        "task-alert-3",
        due_at=NOW - timedelta(days=2),
    )
    with factory() as session:
        session.execute(
            update(AnnotationTaskModel)
            .where(AnnotationTaskModel.id == UUID(str(task["task_id"])))
            .values(status="CANCELLED")
            .execution_options(synchronize_session=False)
        )
        session.commit()

    assert DomainSweepWorker(factory).alert_overdue_tasks(now=NOW) == 0
    assert _overdue_events(factory) == []


def test_alerts_are_project_scoped_and_bounded(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    # 项目 A 任务
    seed_episode(factory, ids.project_a, ids.editor_a, "EP-ALERT-004A")
    schema_a = create_published_schema(editor, owner, ids.project_a, "schema-alert-4a")
    sample_a = create_snapshot_set(editor, ids.project_a, "sample-alert-4a")
    task_a = _create_task(
        editor,
        ids.project_a,
        schema_a,
        sample_a,
        "task-alert-4a",
        due_at=NOW - timedelta(hours=3),
    )
    # 项目 B 任务：Schema 创建人与批准人必须不同，给 editor_a 补一条编辑者成员关系。
    seed_episode(factory, ids.project_b, ids.owner_a, "EP-ALERT-004B")
    with factory() as session:
        session.add(
            ProjectMember(
                project_id=ids.project_b,
                user_id=ids.editor_a,
                role=ProjectRole.EDITOR.value,
            )
        )
        session.commit()
    schema_b = create_published_schema(editor, owner, ids.project_b, "schema-alert-4b")
    sample_b = create_snapshot_set(editor, ids.project_b, "sample-alert-4b")
    task_b = _create_task(
        editor,
        ids.project_b,
        schema_b,
        sample_b,
        "task-alert-4b",
        due_at=NOW - timedelta(hours=1),
    )

    # 批量上限 1：两轮各推进一个任务，事件数量受控而不是一次全发。
    worker = DomainSweepWorker(factory, batch_limit=1)
    assert worker.alert_overdue_tasks(now=NOW) == 1
    assert worker.alert_overdue_tasks(now=NOW) == 1
    assert worker.alert_overdue_tasks(now=NOW) == 0

    events = _overdue_events(factory)
    assert len(events) == 2
    by_task = {str(event.aggregate_id): event for event in events}
    assert set(by_task) == {task_a["task_id"], task_b["task_id"]}
    # 项目隔离：事件的项目字段 = 任务自己所属项目。
    assert by_task[task_a["task_id"]].project_id == ids.project_a
    assert by_task[task_b["task_id"]].project_id == ids.project_b
    assert by_task[task_a["task_id"]].payload["project_id"] == str(ids.project_a)
    assert by_task[task_b["task_id"]].payload["project_id"] == str(ids.project_b)
