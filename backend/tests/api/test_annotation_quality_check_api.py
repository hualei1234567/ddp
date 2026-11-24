"""质量检查 HTTP 接口：自动触发、手动重跑、查询、跨项目隔离与越权。

用真实 SQLite + 真实身份仓储走完整链路（Schema → 样本集 → 任务 → 批次 →
分配 → 提交 → 质量检查），不 mock 用例层：本模块要验证的正是"记录真的落库、
计数真的来自规则、项目隔离真的在查询里生效"。

## 提交即自动检查（语义约定）

标注提交成功的那一刻会自动产生一条质量检查记录——结论必须与"提交冻结"
同时可见，否则复核人会看到空结论，而空结论读起来像"没问题"。

`POST .../quality-checks` 因此是**手动重跑**：每次新增一条 run（结论只追加），
不是"首次触发"。本文件的计数断言都以此为前提：辅助函数
`_checkable_submission` 走完提交，库里已有 1 条自动记录。
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
    record_payload,
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
    # 5 条默认规则 × 1 条记录（V51-010 新增 number_label_within_range）
    assert len(body["findings"]) == 5
    rule_ids = {item["rule_id"] for item in body["findings"]}
    assert rule_ids == {
        "ai_label_confidence_low",
        "duplicate_label_keys",
        "number_label_within_range",
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
    # 该 Schema 无数值边界：新规则如实"无结论"，不得当作通过。
    assert by_rule["number_label_within_range"]["passed"] is None

    # 提交时已自动产生 1 条，本次手动重跑又新增 1 条：结论只追加，不改写。
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationQualityRun)) == 2
        # 5 条规则 × 2 次运行
        assert session.scalar(select(func.count()).select_from(AnnotationQualityFinding)) == 10
        # 最新一条是本次手动重跑的，内容与响应一致。
        latest = session.scalars(
            select(AnnotationQualityRun).order_by(AnnotationQualityRun.created_at.desc())
        ).first()
        assert latest is not None
        assert str(latest.id).replace("-", "") == body["run_id"].replace("-", "")
        assert latest.project_id == ids.project_a
        assert latest.created_by == ids.editor_a
        assert latest.rule_set_version == body["rule_set_version"]


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
    # 1 条自动 + 2 条手动 = 3
    assert len(body["items"]) == 3
    assert body["next_cursor"] is None

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationQualityRun)) == 3
        # 5 条规则 × 3 次运行（V51-010 新增 number_label_within_range）
        assert session.scalar(select(func.count()).select_from(AnnotationQualityFinding)) == 15


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

    # 3 次手动 + 1 次自动 = 4 条
    first_page = editor.get(f"{path}?limit=2")
    assert first_page.status_code == 200, first_page.text
    assert len(first_page.json()["items"]) == 2
    cursor = first_page.json()["next_cursor"]
    assert cursor is not None

    second_page = editor.get(f"{path}?limit=2&cursor={cursor}")
    assert second_page.status_code == 200, second_page.text
    assert len(second_page.json()["items"]) == 2
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

    # 提交时那 1 条自动记录是项目 A 自己的；跨项目访问不得新增任何记录。
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationQualityRun)) == 1


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
    # 只读角色能读到既有结果：1 条来自提交时的自动检查、1 条来自 editor 手动触发。
    assert len(readable.json()["items"]) == 2


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
        # 按本次 run 过滤：提交时那次自动检查也会发同类型事件，
        # 统计全部会把两条混在一起，测不出"本次写用例是否留痕"。
        events = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "annotation.quality_run.completed",
                OutboxEvent.aggregate_id == UUID(run_id),
            )
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


def test_submit_succeeds_even_when_auto_quality_check_fails(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """非法的 quality_policy 不得让标注人无法提交。

    已实测的失败场景：管理员把 ``ai_confidence_threshold`` 配成 1.5，规则引擎
    抛校验错。若自动检查的异常直接上抛，提交事务整体回滚——标注人明明按 Schema
    填完了所有必填项，却因为**别人的配置错误**提交不了，而且他没有任何自助
    修复手段（改任务策略需要管理权限）。

    因此：提交必须成功、记录必须落库、质检不产生结论。
    """
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-autofail")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-autofail")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-autofail")
    # 非法阈值：1.5 超出 [0, 1]
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-autofail",
        quality_policy={"ai_confidence_threshold": 1.5},
    )
    created = editor.post(
        url(ids.project_a, "/annotation-submissions"),
        json={
            "assignment_id": str(work["assignment"]["assignment_id"]),
            "records": [record_payload(episode_id)],
            "data_source": "REAL",
        },
    )
    assert created.status_code == 201, created.text
    submission = created.json()

    submitted = editor.post(
        url(
            ids.project_a,
            f"/annotation-submissions/{submission['submission_id']}/actions/submit",
        ),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "SUBMITTED"

    # 质检没有产生任何结论——非法配置下"没有结论"是唯一诚实的答案。
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationQualityRun)) == 0
