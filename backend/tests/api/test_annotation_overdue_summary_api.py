"""接口集成：项目级逾期汇总（N6-002 收尾，只读）。

验证汇总端点真的按服务端口径统计，而不是信任客户端：

- 空项目：计数全 0、``earliest_due_at`` 为 null、封闭集合零值占位；
- 优先级 / 状态分布与 SLA 分桶（<24h / 24–72h / ≥72h）与任务事实一致；
- 终态任务（完成 / 取消 / 归档）不计入逾期；
- 服务端强制项目隔离：项目 B 的逾期任务不出现在项目 A 的汇总里；
- 只读：调用后任务状态与版本不变；非成员 403，只读成员可读。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.identity.models import ProjectRole
from app.infrastructure.db.models.annotation_work import (
    AnnotationTask as AnnotationTaskModel,
)
from app.infrastructure.db.models.identity import ProjectMember
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_snapshot_set,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client

SUMMARY_PATH = "/annotation-overdue-summary"


def _create_task(
    client: TestClient,
    project_id: UUID,
    schema: dict[str, object],
    sample_set: dict[str, object],
    code: str,
    *,
    due_at: datetime | None,
    priority: str = "P1",
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
            "due_at": due_at.isoformat() if due_at is not None else None,
            "priority": priority,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# 状态机守卫触发器拒绝跳跃迁移；测试按合法路径逐跳推进。
_STATUS_PATH: dict[str, tuple[str, ...]] = {
    "BLOCKED": ("PENDING_REVIEW", "APPROVED", "READY", "ASSIGNED", "IN_PROGRESS", "BLOCKED"),
    "CANCELLED": ("CANCELLED",),
}


def _set_status(
    factory: sessionmaker[Session],
    task_id: object,
    status: str,
) -> None:
    with factory() as session:
        for step in _STATUS_PATH[status]:
            session.execute(
                update(AnnotationTaskModel)
                .where(AnnotationTaskModel.id == UUID(str(task_id)))
                .values(status=step)
                .execution_options(synchronize_session=False)
            )
        session.commit()


def _summary(client: TestClient, project_id: UUID) -> dict[str, object]:
    response = client.get(url(project_id, SUMMARY_PATH))
    assert response.status_code == 200, response.text
    return response.json()


def _counts(entries: list[dict[str, object]], key: str) -> dict[str, int]:
    return {str(item[key]): int(item["count"]) for item in entries}  # type: ignore[arg-type]


def test_empty_project_summary_is_zeroed(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    viewer = make_client(factory, ids.viewer_a)

    summary = _summary(viewer, ids.project_a)

    assert summary["project_id"] == str(ids.project_a)
    assert summary["total_overdue"] == 0
    assert summary["earliest_due_at"] is None
    # 封闭集合零值占位，顺序稳定。
    assert [item["priority"] for item in summary["by_priority"]] == ["P0", "P1", "P2", "P3"]
    assert all(item["count"] == 0 for item in summary["by_priority"])
    assert summary["by_status"] == []
    assert [item["bucket"] for item in summary["buckets"]] == [
        "LT_24H",
        "D1_TO_D3",
        "GT_3D",
    ]
    assert all(item["count"] == 0 for item in summary["buckets"])
    assert [
        (item["lower_bound_hours"], item["upper_bound_hours"]) for item in summary["buckets"]
    ] == [
        (0, 24),
        (24, 72),
        (72, None),
    ]


def test_summary_reports_distribution_and_buckets(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    seed_episode(factory, ids.project_a, ids.editor_a, "EP-SUMMARY-001")
    schema = create_published_schema(editor, owner, ids.project_a, "schema-summary")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-summary")
    now = datetime.now(UTC)

    # 短时逾期（<24h），P0。
    short = _create_task(
        editor,
        ids.project_a,
        schema,
        sample_set,
        "task-summary-short",
        due_at=now - timedelta(hours=1),
        priority="P0",
    )
    # 中期逾期（24–72h），P1，BLOCKED。
    middle = _create_task(
        editor,
        ids.project_a,
        schema,
        sample_set,
        "task-summary-middle",
        due_at=now - timedelta(hours=30),
        priority="P1",
    )
    _set_status(factory, middle["task_id"], "BLOCKED")
    # 长期逾期（≥72h），P2。
    long_overdue = _create_task(
        editor,
        ids.project_a,
        schema,
        sample_set,
        "task-summary-long",
        due_at=now - timedelta(hours=100),
        priority="P2",
    )
    # 未到期任务不计入。
    _create_task(
        editor,
        ids.project_a,
        schema,
        sample_set,
        "task-summary-future",
        due_at=now + timedelta(days=1),
        priority="P3",
    )
    # 终态任务即使截止时间已过也不计入。
    closed = _create_task(
        editor,
        ids.project_a,
        schema,
        sample_set,
        "task-summary-cancelled",
        due_at=now - timedelta(days=10),
        priority="P3",
    )
    _set_status(factory, closed["task_id"], "CANCELLED")

    summary = _summary(editor, ids.project_a)

    assert summary["total_overdue"] == 3
    assert _counts(summary["by_priority"], "priority") == {
        "P0": 1,
        "P1": 1,
        "P2": 1,
        "P3": 0,
    }
    assert _counts(summary["by_status"], "status") == {"DRAFT": 2, "BLOCKED": 1}
    assert _counts(summary["buckets"], "bucket") == {
        "LT_24H": 1,
        "D1_TO_D3": 1,
        "GT_3D": 1,
    }
    # 最早期逾期时间来自逾期最久的任务（P2），与未来/终态任务无关。
    earliest = datetime.fromisoformat(str(summary["earliest_due_at"]))
    assert earliest.tzinfo is not None
    assert abs((earliest - (now - timedelta(hours=100))).total_seconds()) < 5
    # 只读：任务状态与版本未被汇总端点改写。
    assert (
        editor.get(url(ids.project_a, f"/annotation-tasks/{short['task_id']}")).json()["status"]
        == "DRAFT"
    )
    assert (
        editor.get(url(ids.project_a, f"/annotation-tasks/{long_overdue['task_id']}")).json()[
            "version"
        ]
        == 1
    )


def test_summary_is_project_scoped(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    seed_episode(factory, ids.project_a, ids.editor_a, "EP-SUMMARY-002A")
    schema_a = create_published_schema(editor, owner, ids.project_a, "schema-summary-2a")
    sample_a = create_snapshot_set(editor, ids.project_a, "sample-summary-2a")
    now = datetime.now(UTC)
    _create_task(
        editor,
        ids.project_a,
        schema_a,
        sample_a,
        "task-summary-2a",
        due_at=now - timedelta(hours=2),
    )

    # 项目 B：editor_a 需先成为项目 B 的编辑者才能建任务。
    seed_episode(factory, ids.project_b, ids.owner_a, "EP-SUMMARY-002B")
    with factory() as session:
        session.add(
            ProjectMember(
                project_id=ids.project_b,
                user_id=ids.editor_a,
                role=ProjectRole.EDITOR.value,
            )
        )
        session.commit()
    schema_b = create_published_schema(editor, owner, ids.project_b, "schema-summary-2b")
    sample_b = create_snapshot_set(editor, ids.project_b, "sample-summary-2b")
    _create_task(
        editor,
        ids.project_b,
        schema_b,
        sample_b,
        "task-summary-2b",
        due_at=now - timedelta(days=4),
    )

    summary_a = _summary(editor, ids.project_a)
    summary_b = _summary(editor, ids.project_b)

    assert summary_a["total_overdue"] == 1
    assert summary_b["total_overdue"] == 1
    assert summary_a["project_id"] == str(ids.project_a)
    assert summary_b["project_id"] == str(ids.project_b)
    # 项目 A 的汇总里不会出现项目 B 的长期逾期桶。
    assert _counts(summary_a["buckets"], "bucket")["GT_3D"] == 0
    assert _counts(summary_b["buckets"], "bucket")["GT_3D"] == 1

    # 项目外用户无法读取任何项目的汇总。
    outsider = make_client(factory, ids.outsider)
    assert outsider.get(url(ids.project_a, SUMMARY_PATH)).status_code == 403
    assert outsider.get(url(ids.project_b, SUMMARY_PATH)).status_code == 403
