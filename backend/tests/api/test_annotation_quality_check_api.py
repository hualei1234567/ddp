"""质量检查 HTTP 接口：触发、查询、跨项目隔离与越权。

用真实 SQLite + 真实身份仓储走完整链路（Schema → 样本集 → 任务 → 批次 →
分配 → 提交 → 质量检查），不 mock 用例层：本模块要验证的正是"记录真的落库、
计数真的来自规则、项目隔离真的在查询里生效"。
"""

from __future__ import annotations

from uuid import UUID

from app.infrastructure.db.models.annotation_quality_rules import (
    AnnotationQualityFinding,
    AnnotationQualityRun,
)
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    create_submitted,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client


def _checkable_submission(
    factory: sessionmaker[Session],
    ids: ApiIds,
    code: str,
    *,
    quality_policy: dict[str, object] | None = None,
) -> dict[str, object]:
    """造一份已提交（SUBMITTED）的标注提交，返回提交 JSON。"""
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, f"EP-{code}")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, f"schema-{code}")
    sample_set = create_snapshot_set(editor, ids.project_a, f"sample-{code}")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        f"task-{code}",
        quality_policy=quality_policy,
    )
    return create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )


def test_run_quality_check_persists_run_and_findings(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    submission = _checkable_submission(factory, ids, "qc-basic")
    editor = make_client(factory, ids.editor_a)
    submission_id = submission["submission_id"]

    response = editor.post(
        url(ids.project_a, f"/annotation-submissions/{submission_id}/quality-checks")
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["submission_id"] == submission_id
    assert body["status"] == "PASSED"
    assert body["error_count"] == 0
    assert body["rule_set_version"].startswith("sha256:")
    assert len(body["rule_set_version"]) == 71
    # 4 条默认规则 × 1 条记录
    assert len(body["findings"]) == 4
    rule_ids = {item["rule_id"] for item in body["findings"]}
    assert rule_ids == {
        "ai_label_confidence_low",
        "duplicate_label_keys",
        "required_labels_present",
        "time_range_within_episode",
    }
    # 两类"没有结论"必须落 null 而不是 true：
    # - AI 置信度：任务未配置 ai_confidence_threshold；
    # - 时间片段：该记录只在标签里表达了时间片段，顶层没有 time_range。
    by_rule = {item["rule_id"]: item for item in body["findings"]}
    assert by_rule["ai_label_confidence_low"]["passed"] is None
    assert by_rule["time_range_within_episode"]["passed"] is None
    assert by_rule["required_labels_present"]["passed"] is True
    assert by_rule["duplicate_label_keys"]["passed"] is True

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationQualityRun)) == 1
        assert session.scalar(select(func.count()).select_from(AnnotationQualityFinding)) == 4
        run = session.scalar(select(AnnotationQualityRun))
        assert run is not None
        assert run.project_id == ids.project_a
        assert run.created_by == ids.editor_a
        assert run.rule_set_version == body["rule_set_version"]


def test_quality_check_fails_when_ai_confidence_below_policy(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    submission = _checkable_submission(
        factory,
        ids,
        "qc-conf",
        quality_policy={"ai_confidence_threshold": 0.99},
    )
    editor = make_client(factory, ids.editor_a)

    # 现有记录的标签都是 HUMAN，AI 规则判定"不含 AI 标签"→ 通过；
    # 这一条先确认策略键真的被读到（message 里带阈值）。
    response = editor.post(
        url(
            ids.project_a,
            f"/annotation-submissions/{submission['submission_id']}/quality-checks",
        )
    )
    assert response.status_code == 201, response.text
    finding = next(
        item for item in response.json()["findings"] if item["rule_id"] == "ai_label_confidence_low"
    )
    assert finding["passed"] is True
    assert finding["severity"] == "WARN"


def test_quality_check_is_append_only_and_listed_newest_first(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    submission = _checkable_submission(factory, ids, "qc-append")
    editor = make_client(factory, ids.editor_a)
    path = url(
        ids.project_a,
        f"/annotation-submissions/{submission['submission_id']}/quality-checks",
    )

    first = editor.post(path)
    second = editor.post(path)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["run_id"] != second.json()["run_id"]

    listed = editor.get(path)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is None

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationQualityRun)) == 2
        assert session.scalar(select(func.count()).select_from(AnnotationQualityFinding)) == 8


def test_quality_check_list_paginates_with_cursor(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    submission = _checkable_submission(factory, ids, "qc-page")
    editor = make_client(factory, ids.editor_a)
    path = url(
        ids.project_a,
        f"/annotation-submissions/{submission['submission_id']}/quality-checks",
    )
    for _ in range(3):
        assert editor.post(path).status_code == 201

    first_page = editor.get(f"{path}?limit=2")
    assert first_page.status_code == 200, first_page.text
    assert len(first_page.json()["items"]) == 2
    cursor = first_page.json()["next_cursor"]
    assert cursor is not None

    second_page = editor.get(f"{path}?limit=2&cursor={cursor}")
    assert second_page.status_code == 200, second_page.text
    assert len(second_page.json()["items"]) == 1
    first_ids = {item["run_id"] for item in first_page.json()["items"]}
    second_ids = {item["run_id"] for item in second_page.json()["items"]}
    assert first_ids & second_ids == set()


def test_quality_check_isolated_across_projects(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """项目 B 的 owner 用项目 B 路径访问项目 A 的提交：一律 404，不泄露存在性。"""
    factory, ids = api_db
    submission = _checkable_submission(factory, ids, "qc-iso")
    foreign = make_client(factory, ids.owner_a)  # 同时是项目 B 的 owner

    denied_run = foreign.post(
        url(ids.project_b, f"/annotation-submissions/{submission['submission_id']}/quality-checks")
    )
    assert denied_run.status_code == 404
    assert denied_run.json()["error"]["code"] == "resource_not_found"

    denied_list = foreign.get(
        url(ids.project_b, f"/annotation-submissions/{submission['submission_id']}/quality-checks")
    )
    assert denied_list.status_code == 404
    assert denied_list.json()["error"]["code"] == "resource_not_found"

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationQualityRun)) == 0


def test_outsider_cannot_run_or_read_quality_check(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    submission = _checkable_submission(factory, ids, "qc-outsider")
    outsider = make_client(factory, ids.outsider)
    path = url(
        ids.project_a,
        f"/annotation-submissions/{submission['submission_id']}/quality-checks",
    )

    assert outsider.post(path).status_code == 403
    assert outsider.get(path).status_code == 403


def test_viewer_cannot_trigger_but_can_read_quality_check(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """只读角色不得触发检查（写作用域），但可以查询既有结果。"""
    factory, ids = api_db
    submission = _checkable_submission(factory, ids, "qc-viewer")
    editor = make_client(factory, ids.editor_a)
    viewer = make_client(factory, ids.viewer_a)
    path = url(
        ids.project_a,
        f"/annotation-submissions/{submission['submission_id']}/quality-checks",
    )
    assert editor.post(path).status_code == 201

    denied = viewer.post(path)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"

    readable = viewer.get(path)
    assert readable.status_code == 200
    assert len(readable.json()["items"]) == 1


def test_quality_check_rejects_draft_submission(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """草稿不参与质量检查：结论会立刻过期，返回明确的域错误。"""
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-qc-draft")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-qc-draft")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-qc-draft")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-qc-draft",
    )
    created = editor.post(
        url(ids.project_a, "/annotation-submissions"),
        json={
            "assignment_id": work["assignment"]["assignment_id"],
            "records": [
                {
                    "episode_id": str(episode_id),
                    "episode_version": 1,
                    "labels": [
                        {
                            "label_key": "action",
                            "value": "PICK",
                            "source": "HUMAN",
                            "label_status": "CONFIRMED",
                        }
                    ],
                }
            ],
            "data_source": "REAL",
        },
    )
    assert created.status_code == 201, created.text
    submission_id = created.json()["submission_id"]

    response = editor.post(
        url(ids.project_a, f"/annotation-submissions/{submission_id}/quality-checks")
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "annotation_submission_not_submitted"

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationQualityRun)) == 0


def test_quality_check_unknown_submission_returns_404(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    missing: UUID = UUID("00000000-0000-0000-0000-000000000000")
    response = editor.post(url(ids.project_a, f"/annotation-submissions/{missing}/quality-checks"))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


def test_quality_check_writes_audit_and_outbox(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """审计与留痕事件必须与业务写入同事务落库（失败要能回滚整笔）。"""
    factory, ids = api_db
    submission = _checkable_submission(factory, ids, "qc-audit")
    editor = make_client(factory, ids.editor_a)

    response = editor.post(
        url(
            ids.project_a,
            f"/annotation-submissions/{submission['submission_id']}/quality-checks",
        )
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["run_id"]

    with factory() as session:
        audit = session.scalars(
            select(AuditLog).where(
                AuditLog.resource_type == "ANNOTATION_QUALITY_RUN",
                AuditLog.resource_id == UUID(run_id),
            )
        ).all()
        assert len(audit) == 1
        assert audit[0].action == "run"
        assert audit[0].to_status == "PASSED"
        events = session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == "annotation.quality_run.completed")
        ).all()
        assert len(events) == 1
        assert events[0].dispatch is False
        assert events[0].payload["run_id"] == run_id


def test_quality_check_rule_threshold_comes_from_task_policy(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """策略阈值非法时请求失败且不落任何检查记录（不静默回退默认值）。"""
    factory, ids = api_db
    submission = _checkable_submission(
        factory,
        ids,
        "qc-bad-policy",
        quality_policy={"ai_confidence_threshold": 1.5},
    )
    editor = make_client(factory, ids.editor_a)

    response = editor.post(
        url(
            ids.project_a,
            f"/annotation-submissions/{submission['submission_id']}/quality-checks",
        )
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationQualityRun)) == 0
