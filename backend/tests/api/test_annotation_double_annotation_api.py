"""双标任务与一致性门槛 API 用例。

覆盖：两人独立提交、达标/不达标、缺第二人、自动通过、跨项目隔离、
同一标注者不得跨批次重复标注同一 Episode。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    create_submitted,
    seed_arbiter,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client
from tests.api.training_support import add_member

DOUBLE_POLICY = {
    "review_mode": "DOUBLE",
    "agreement_threshold": 0.8,
    "agreement_metric": "PERCENT_AGREEMENT",
}


def _add_assignment(
    client,
    project_id: UUID,
    batch_id: UUID,
    assignee_id: UUID,
    *,
    planned_item_count: int = 1,
) -> dict[str, object]:
    created = client.post(
        url(project_id, "/annotation-assignments"),
        json={
            "batch_id": str(batch_id),
            "assignee_id": str(assignee_id),
            "planned_item_count": planned_item_count,
            "planned_minutes": 30,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _claim_and_start(client, project_id: UUID, assignment: dict[str, object]) -> dict[str, object]:
    claimed = client.post(
        url(
            project_id,
            f"/annotation-assignments/{assignment['assignment_id']}/actions/claim",
        ),
        headers={"If-Match": f'"{assignment["version"]}"'},
        json={},
    )
    assert claimed.status_code == 200, claimed.text
    started = client.post(
        url(
            project_id,
            f"/annotation-assignments/{assignment['assignment_id']}/actions/start",
        ),
        headers={"If-Match": f'"{claimed.json()["version"]}"'},
        json={},
    )
    assert started.status_code == 200, started.text
    return started.json()


def _double_work(factory, ids: ApiIds, code: str, iaa_policy: dict[str, object] | None = None):
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, f"EP-{code}")
    second_annotator_id = seed_arbiter(factory, ids.project_a)
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    second = make_client(factory, second_annotator_id)
    schema = create_published_schema(editor, owner, ids.project_a, f"schema-{code}")
    sample_set = create_snapshot_set(editor, ids.project_a, f"sample-{code}")
    # 自动通过会以 OWNER 身份留痕，只有 OWNER 能开启；任务创建人不能审批
    # 自己的任务，因此自动通过场景由 OWNER 创建、另一个 OWNER 审批。
    auto = bool((iaa_policy or {}).get("auto_approve"))
    approver = (
        make_client(factory, add_member(factory, ids.project_a, role="owner")) if auto else owner
    )
    work = create_ready_work(
        editor,
        approver,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        f"task-{code}",
        iaa_policy=iaa_policy or dict(DOUBLE_POLICY),
        creator=owner if auto else None,
    )
    return episode_id, second_annotator_id, editor, owner, second, work


def test_double_annotation_two_annotators_pass(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id, second_id, editor, _owner, second, work = _double_work(factory, ids, "double-pass")
    second_assignment = _add_assignment(
        editor,
        ids.project_a,
        work["batch"]["batch_id"],
        second_id,
    )
    _claim_and_start(second, ids.project_a, second_assignment)

    first = create_submitted(editor, ids.project_a, work["assignment"]["assignment_id"], episode_id)
    assert first["consistency"]["status"] == "PENDING_SECOND_ANNOTATOR"

    second_submission = create_submitted(
        second, ids.project_a, second_assignment["assignment_id"], episode_id
    )
    consistency = second_submission["consistency"]
    assert consistency["status"] == "PASSED"
    assert consistency["passed"] is True
    assert consistency["value"] == 1.0
    assert consistency["annotator_count"] == 2
    assert consistency["arbitration_ids"] == []
    assert consistency["auto_approved"] is False

    task = editor.get(url(ids.project_a, f"/annotation-tasks/{work['task']['task_id']}")).json()
    assert task["review_mode"] == "DOUBLE"

    fetched = editor.get(
        url(
            ids.project_a,
            f"/annotation-tasks/{work['task']['task_id']}/consistency",
        )
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["status"] == "PASSED"


def test_double_annotation_failure_creates_arbitration_items(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id, second_id, editor, _owner, second, work = _double_work(factory, ids, "double-fail")
    second_assignment = _add_assignment(
        editor,
        ids.project_a,
        work["batch"]["batch_id"],
        second_id,
    )
    _claim_and_start(second, ids.project_a, second_assignment)

    first = create_submitted(editor, ids.project_a, work["assignment"]["assignment_id"], episode_id)
    second_submission = create_submitted(
        second,
        ids.project_a,
        second_assignment["assignment_id"],
        episode_id,
        action="PLACE",
        segment=(1_200, 1_600),
    )
    consistency = second_submission["consistency"]
    assert consistency["status"] == "FAILED"
    assert consistency["passed"] is False
    assert consistency["arbitration_required"] is True
    assert len(consistency["arbitration_ids"]) == 2

    for submission_id in (
        first["submission_id"],
        second_submission["submission_id"],
    ):
        arbitrations = editor.get(
            url(ids.project_a, f"/annotation-submissions/{submission_id}/arbitrations")
        )
        assert arbitrations.status_code == 200, arbitrations.text
        items = arbitrations.json()["items"]
        assert len(items) == 1
        assert items[0]["trigger"] == "IAA_CONFLICT"
        assert items[0]["task_id"] == work["task"]["task_id"]


def test_double_annotation_missing_second_annotator_is_pending(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id, _second_id, editor, _owner, _second, work = _double_work(
        factory, ids, "double-missing"
    )
    submission = create_submitted(
        editor, ids.project_a, work["assignment"]["assignment_id"], episode_id
    )
    consistency = submission["consistency"]
    assert consistency["status"] == "PENDING_SECOND_ANNOTATOR"
    assert consistency["arbitration_ids"] == []

    fetched = editor.get(
        url(
            ids.project_a,
            f"/annotation-tasks/{work['task']['task_id']}/consistency",
        )
    ).json()
    assert fetched["status"] == "PENDING_SECOND_ANNOTATOR"
    assert fetched["double_episode_count"] == 0


def test_double_annotation_auto_approve_when_explicitly_enabled(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    policy = {**DOUBLE_POLICY, "auto_approve": True}
    episode_id, second_id, editor, _owner, second, work = _double_work(
        factory, ids, "double-auto", iaa_policy=policy
    )
    second_assignment = _add_assignment(
        editor,
        ids.project_a,
        work["batch"]["batch_id"],
        second_id,
    )
    _claim_and_start(second, ids.project_a, second_assignment)

    first = create_submitted(editor, ids.project_a, work["assignment"]["assignment_id"], episode_id)
    second_submission = create_submitted(
        second, ids.project_a, second_assignment["assignment_id"], episode_id
    )
    consistency = second_submission["consistency"]
    assert consistency["status"] == "PASSED"
    assert consistency["auto_approve_enabled"] is True
    assert consistency["auto_approved"] is True

    for submission_id in (
        first["submission_id"],
        second_submission["submission_id"],
    ):
        stored = editor.get(url(ids.project_a, f"/annotation-submissions/{submission_id}")).json()
        assert stored["status"] == "APPROVED"


def test_editor_cannot_enable_auto_approve(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """EDITOR 不能开启 auto_approve：自动通过会冒用 OWNER 身份写"人工审批"。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    code = f"auto-forbidden-{UUID(int=0).hex[:6]}"
    seed_episode(factory, ids.project_a, ids.editor_a, f"EP-{code}")
    schema = create_published_schema(editor, owner, ids.project_a, f"schema-{code}")
    sample_set = create_snapshot_set(editor, ids.project_a, f"sample-{code}")
    payload = {
        "code": f"t-{code}",
        "name": "越权自动通过",
        "sample_set_id": sample_set["sample_set_id"],
        "schema_id": schema["schema_id"],
        "schema_version": schema["schema_version"],
        "target_item_count": 1,
        "target_episode_count": 1,
        "iaa_policy": {**DOUBLE_POLICY, "auto_approve": True},
    }
    response = editor.post(url(ids.project_a, "/annotation-tasks"), json=payload)
    assert response.status_code == 403, response.text


def test_double_annotation_consistency_is_project_isolated(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _episode_id, _second_id, _editor, _owner, _second, work = _double_work(
        factory, ids, "double-isolation"
    )
    outsider_view = make_client(factory, ids.owner_a)
    denied = outsider_view.get(
        url(
            ids.project_b,
            f"/annotation-tasks/{work['task']['task_id']}/consistency",
        )
    )
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "resource_not_found"


def test_double_annotation_rejects_same_annotator_on_same_episode_across_batches(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-double-dup")
    second_id = seed_arbiter(factory, ids.project_a)
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-double-dup")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-double-dup")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-double-dup",
        iaa_policy=dict(DOUBLE_POLICY),
        planned_item_count=1,
        target_item_count=2,
    )
    second_batch = editor.post(
        url(
            ids.project_a,
            f"/annotation-tasks/{work['task']['task_id']}/batches",
        ),
        json={
            "planned_item_count": 1,
            "item_refs": [str(episode_id)],
        },
    )
    assert second_batch.status_code == 201, second_batch.text

    duplicate = editor.post(
        url(ids.project_a, "/annotation-assignments"),
        json={
            "batch_id": second_batch.json()["batch_id"],
            "assignee_id": str(ids.editor_a),
            "planned_item_count": 1,
        },
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "annotation_item_already_assigned"

    distinct = _add_assignment(
        editor,
        ids.project_a,
        UUID(second_batch.json()["batch_id"]),
        second_id,
    )
    assert distinct["assignee_id"] == str(second_id)


def test_editor_cannot_enable_auto_approve_via_patch(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """创建闸口之外，PATCH 也不能被用来补开自动通过。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    code = f"auto-patch-{UUID(int=1).hex[:6]}"
    seed_episode(factory, ids.project_a, ids.editor_a, f"EP-{code}")
    schema = create_published_schema(editor, owner, ids.project_a, f"schema-{code}")
    sample_set = create_snapshot_set(editor, ids.project_a, f"sample-{code}")
    created = editor.post(
        url(ids.project_a, "/annotation-tasks"),
        json={
            "code": f"t-{code}",
            "name": "PATCH 越权自动通过",
            "sample_set_id": sample_set["sample_set_id"],
            "schema_id": schema["schema_id"],
            "schema_version": schema["schema_version"],
            "target_item_count": 1,
            "target_episode_count": 1,
            "iaa_policy": dict(DOUBLE_POLICY),
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]

    denied = editor.patch(
        url(ids.project_a, f"/annotation-tasks/{task_id}"),
        headers={"If-Match": '"1"'},
        json={"iaa_policy": {**DOUBLE_POLICY, "auto_approve": True}},
    )
    assert denied.status_code == 403, denied.text

    allowed = owner.patch(
        url(ids.project_a, f"/annotation-tasks/{task_id}"),
        headers={"If-Match": '"1"'},
        json={"iaa_policy": {**DOUBLE_POLICY, "auto_approve": True}},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["iaa_policy"]["auto_approve"] is True
