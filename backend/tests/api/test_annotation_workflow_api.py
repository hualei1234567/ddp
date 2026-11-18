"""标注完整链路：提交/复核/仲裁职责分离、质检抽检、黄金集与 IAA。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.infrastructure.db.models.annotation_work import (
    AnnotationAssignmentEvent,
)
from app.infrastructure.db.models.annotation_work import (
    AnnotationTask as AnnotationTaskModel,
)
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    create_submitted,
    record_payload,
    seed_arbiter,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client


def test_full_annotation_flow_duty_separation_and_quality(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-A-1")
    arbiter_id = seed_arbiter(factory, ids.project_a)
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    arbiter = make_client(factory, arbiter_id)

    schema = create_published_schema(editor, owner, ids.project_a, "schema-flow")
    first = editor.get(url(ids.project_a, f"/annotation-schemas/{schema['schema_id']}"))
    assert first.status_code == 200
    assert first.json()["status"] == "PUBLISHED"

    sample_set = create_snapshot_set(editor, ids.project_a, "sample-flow")
    assert sample_set["status"] == "SNAPSHOTTED"
    assert sample_set["selected_count"] == 1

    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-flow",
    )
    assert work["task"]["status"] == "IN_PROGRESS"
    assert work["assignment"]["status"] == "IN_PROGRESS"

    submission = create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )
    assert submission["status"] == "SUBMITTED"
    submission_list = editor.get(
        url(ids.project_a, f"/annotation-submissions?task_id={work['task']['task_id']}")
    )
    assert submission_list.status_code == 200
    assert len(submission_list.json()["items"]) == 1

    self_review = editor.post(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}/reviews"),
        json={"reviewer_id": str(ids.editor_a)},
    )
    assert self_review.status_code == 403
    assert self_review.json()["error"]["code"] == "annotation_review_self_forbidden"

    review = owner.post(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}/reviews"),
        json={"reviewer_id": str(ids.owner_a)},
    )
    assert review.status_code == 201, review.text
    review_id = review.json()["review_id"]
    review_list = owner.get(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}/reviews")
    )
    assert review_list.status_code == 200
    assert len(review_list.json()["items"]) == 1

    not_reviewer = editor.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert not_reviewer.status_code == 403
    assert not_reviewer.json()["error"]["code"] == "forbidden"

    claimed = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert claimed.status_code == 200, claimed.text

    escalated = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/escalate"),
        headers={"If-Match": '"2"'},
        json={"reason": "复核人与标注人意见不一致"},
    )
    assert escalated.status_code == 200, escalated.text
    assert escalated.json()["status"] == "ESCALATED"

    arbitration = editor.post(
        url(
            ids.project_a,
            f"/annotation-submissions/{submission['submission_id']}/arbitrations",
        ),
        json={
            "review_id": review_id,
            "arbiter_ids": [str(arbiter_id)],
            "quorum": 1,
            "trigger": "REVIEWER_DISAGREEMENT",
        },
    )
    assert arbitration.status_code == 201, arbitration.text
    arbitration_id = arbitration.json()["arbitration_id"]
    arbitration_list = editor.get(
        url(
            ids.project_a,
            f"/annotation-submissions/{submission['submission_id']}/arbitrations",
        )
    )
    assert arbitration_list.status_code == 200
    assert len(arbitration_list.json()["items"]) == 1

    owner_cannot_arbitrate = owner.post(
        url(ids.project_a, f"/annotation-arbitrations/{arbitration_id}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert owner_cannot_arbitrate.status_code == 403
    assert owner_cannot_arbitrate.json()["error"]["code"] == "annotation_arbiter_conflict"

    arbiter_claim = arbiter.post(
        url(ids.project_a, f"/annotation-arbitrations/{arbitration_id}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert arbiter_claim.status_code == 200, arbiter_claim.text

    vote = arbiter.post(
        url(ids.project_a, f"/annotation-arbitrations/{arbitration_id}/votes"),
        headers={"If-Match": '"2"'},
        json={"decision": "APPROVE", "reason": "样例一致"},
    )
    assert vote.status_code == 200, vote.text
    assert vote.json()["effective_vote_count"] == 1

    resolved = arbiter.post(
        url(
            ids.project_a,
            f"/annotation-arbitrations/{arbitration_id}/actions/resolve-approved",
        ),
        headers={"If-Match": '"3"'},
        json={"reason": "仲裁通过"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "RESOLVED_APPROVED"
    final_submission = editor.get(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}")
    )
    assert final_submission.json()["status"] == "RESOLVED_APPROVED"

    # 质检抽检：标注人不得自选；其他写者创建后由复核人完成闭环。
    self_sample = editor.post(
        url(ids.project_a, "/quality-samples"),
        json={
            "task_id": work["task"]["task_id"],
            "submission_id": submission["submission_id"],
            "annotator_id": str(ids.editor_a),
            "sampling_policy_version": "quality/1",
            "sample_type": "RANDOM",
            "sample_reason": "例行抽检",
        },
    )
    assert self_sample.status_code == 403
    assert self_sample.json()["error"]["code"] == "quality_sample_self_review_forbidden"

    sample = owner.post(
        url(ids.project_a, "/quality-samples"),
        json={
            "task_id": work["task"]["task_id"],
            "submission_id": submission["submission_id"],
            "annotator_id": str(ids.editor_a),
            "sampling_policy_version": "quality/1",
            "sample_type": "RANDOM",
            "sample_reason": "例行抽检",
        },
    )
    assert sample.status_code == 201, sample.text
    sample_id = sample.json()["quality_sample_id"]
    sample_url = url(ids.project_a, f"/quality-samples/{sample_id}/actions")
    for action, version, body in (
        ("sample", 1, {}),
        ("assign", 2, {"reviewer_id": str(ids.owner_a)}),
        ("start", 3, {}),
        ("pass", 4, {}),
    ):
        response = owner.post(
            f"{sample_url}/{action}",
            headers={"If-Match": f'"{version}"'},
            json=body,
        )
        assert response.status_code == 200, response.text
    assert response.json()["status"] == "PASSED"
    listed_samples = owner.get(
        url(ids.project_a, f"/quality-samples?task_id={work['task']['task_id']}")
    )
    assert listed_samples.status_code == 200
    assert len(listed_samples.json()["items"]) == 1

    # IAA 在只有一名标注者时返回 INSUFFICIENT_DATA，而不是 0。
    iaa = editor.post(
        url(ids.project_a, "/iaa-reports"),
        json={
            "task_id": work["task"]["task_id"],
            "metric_definitions_version": "ddp.stage3.iaa/1",
        },
    )
    assert iaa.status_code == 201, iaa.text
    assert iaa.json()["status"] == "INSUFFICIENT_DATA"
    iaa_id = iaa.json()["report_id"]
    assert editor.get(url(ids.project_a, f"/iaa-reports/{iaa_id}")).status_code == 200
    iaa_list = editor.get(url(ids.project_a, f"/iaa-reports?task_id={work['task']['task_id']}"))
    assert iaa_list.status_code == 200


def test_gold_set_answer_protection_and_evaluation(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-GOLD-1")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    viewer = make_client(factory, ids.viewer_a)

    schema = create_published_schema(editor, owner, ids.project_a, "schema-gold")
    gold = editor.post(
        url(ids.project_a, "/gold-sets"),
        json={
            "code": "gold-1",
            "schema_id": schema["schema_id"],
            "items": [
                {
                    "gold_item_id": str(uuid4()),
                    "episode_id": str(episode_id),
                    "expected_labels": [{"label_key": "action", "value": "PICK"}],
                }
            ],
            "exposure_policy": "HIDDEN",
            "max_exposure_per_annotator": 1,
            "rotation_policy": "NONE",
        },
    )
    assert gold.status_code == 201, gold.text
    gold_set_id = gold.json()["gold_set_id"]

    submitted = editor.post(
        url(ids.project_a, f"/gold-sets/{gold_set_id}/actions/submit-review"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    activated = owner.post(
        url(ids.project_a, f"/gold-sets/{gold_set_id}/actions/activate"),
        headers={"If-Match": '"2"'},
        json={},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "ACTIVE"
    assert "expected_labels" not in activated.json()["items"][0]

    redacted = viewer.get(url(ids.project_a, f"/gold-sets/{gold_set_id}"))
    assert redacted.status_code == 200
    assert redacted.json()["items"][0]["answers_redacted"] is True
    assert "expected_labels" not in redacted.json()["items"][0]

    denied = viewer.get(url(ids.project_a, f"/gold-sets/{gold_set_id}/answers"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "gold_set_access_denied"

    # AN/AE（editor）也不得查看期望答案，拒绝必须留痕。
    editor_denied = editor.get(url(ids.project_a, f"/gold-sets/{gold_set_id}/answers"))
    assert editor_denied.status_code == 403
    assert editor_denied.json()["error"]["code"] == "gold_set_access_denied"
    with factory() as session:
        denied_audits = session.scalars(
            select(AuditLog).where(
                AuditLog.action == "view-answers",
                AuditLog.result == "FAILURE",
            )
        ).all()
        denied_actors = {row.actor_user_id for row in denied_audits}
        assert {ids.viewer_a, ids.editor_a} <= denied_actors
        denied_events = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "annotation.gold_set.answers_denied"
            )
        ).all()
        assert len(denied_events) >= 2

    answers = owner.get(url(ids.project_a, f"/gold-sets/{gold_set_id}/answers"))
    assert answers.status_code == 200, answers.text
    assert answers.json()["items"][0]["expected_labels"][0]["value"] == "PICK"

    # 构造一条提交后按黄金集评估，确认只有授权端点能看到答案。
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-gold")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-gold",
    )
    submission = create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )
    evaluation = owner.post(
        url(ids.project_a, f"/gold-sets/{gold_set_id}/evaluations"),
        json={
            "task_id": work["task"]["task_id"],
            "submission_id": submission["submission_id"],
        },
    )
    assert evaluation.status_code == 201, evaluation.text
    assert evaluation.json()["accuracy"] == 1.0


def _reject_and_require_rework(
    owner,
    editor,
    project_id,
    submission_id,
    reviewer_id,
    *,
    reason: str,
) -> dict[str, object]:
    """复核人驳回，再由复核人发出返工要求，返回最新提交快照。"""
    review = owner.post(
        url(project_id, f"/annotation-submissions/{submission_id}/reviews"),
        json={"reviewer_id": str(reviewer_id)},
    )
    assert review.status_code == 201, review.text
    review_id = review.json()["review_id"]
    claimed = owner.post(
        url(project_id, f"/annotation-reviews/{review_id}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert claimed.status_code == 200, claimed.text
    rejected = owner.post(
        url(project_id, f"/annotation-reviews/{review_id}/actions/reject"),
        headers={"If-Match": '"2"'},
        json={
            "reason": reason,
            "issues": [
                {
                    "code": "LABEL_ERROR",
                    "severity": "MAJOR",
                    "message": "动作标签与视频不一致",
                    "label_key": "action",
                }
            ],
        },
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"

    current = editor.get(url(project_id, f"/annotation-submissions/{submission_id}"))
    assert current.status_code == 200, current.text
    assert current.json()["status"] == "REJECTED"
    require = owner.post(
        url(project_id, f"/annotation-submissions/{submission_id}/actions/require-rework"),
        headers={"If-Match": f'"{current.json()["version"]}"'},
        json={"reason": reason},
    )
    return require


def test_rework_cycle_versions_and_limit(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """驳回→返工→新版本→复检→旧版本保留→返工次数递增/超限（AC-ANN-15）。"""
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-REWORK-1")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    schema = create_published_schema(editor, owner, ids.project_a, "schema-rework")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-rework")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-rework",
        quality_policy={"max_rework_rounds": 2},
    )
    assignment_id = work["assignment"]["assignment_id"]
    batch_id = work["batch"]["batch_id"]

    version_one = create_submitted(editor, ids.project_a, assignment_id, episode_id)
    assert version_one["submission_version"] == 1

    first = _reject_and_require_rework(
        owner,
        editor,
        ids.project_a,
        version_one["submission_id"],
        ids.owner_a,
        reason="第一轮返工",
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "REWORK_REQUIRED"
    batch = editor.get(url(ids.project_a, f"/annotation-batches/{batch_id}"))
    assert batch.status_code == 200, batch.text
    assert batch.json()["rework_count"] == 1
    with factory() as session:
        rework_events = session.scalars(
            select(AnnotationAssignmentEvent).where(
                AnnotationAssignmentEvent.assignment_id == UUID(str(assignment_id)),
                AnnotationAssignmentEvent.action == "rework",
            )
        ).all()
        assert len(rework_events) == 1

    # 新版本提交同一条分配，旧版本保留为 SUPERSEDED，不覆盖。
    draft_two = editor.post(
        url(ids.project_a, "/annotation-submissions"),
        json={
            "assignment_id": str(assignment_id),
            "records": [
                {
                    "episode_id": str(episode_id),
                    "episode_version": 1,
                    "labels": [
                        {
                            "label_key": "action",
                            "value": "PLACE",
                            "source": "HUMAN",
                            "label_status": "CONFIRMED",
                        }
                    ],
                }
            ],
            "data_source": "REAL",
        },
    )
    assert draft_two.status_code == 201, draft_two.text
    second = draft_two.json()
    assert second["submission_version"] == 2
    assert second["supersedes_id"] == version_one["submission_id"]

    old = editor.get(url(ids.project_a, f"/annotation-submissions/{version_one['submission_id']}"))
    assert old.status_code == 200, old.text
    assert old.json()["status"] == "SUPERSEDED"
    old_records = editor.get(
        url(
            ids.project_a,
            f"/annotation-submissions/{version_one['submission_id']}/records",
        )
    )
    assert old_records.status_code == 200, old_records.text
    assert len(old_records.json()["items"]) == 1

    submitted_two = editor.post(
        url(ids.project_a, f"/annotation-submissions/{second['submission_id']}/actions/submit"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted_two.status_code == 200, submitted_two.text

    second_round = _reject_and_require_rework(
        owner,
        editor,
        ids.project_a,
        second["submission_id"],
        ids.owner_a,
        reason="第二轮返工",
    )
    assert second_round.status_code == 200, second_round.text
    batch = editor.get(url(ids.project_a, f"/annotation-batches/{batch_id}"))
    assert batch.json()["rework_count"] == 2

    draft_three = editor.post(
        url(ids.project_a, "/annotation-submissions"),
        json={
            "assignment_id": str(assignment_id),
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
    assert draft_three.status_code == 201, draft_three.text
    third = draft_three.json()
    assert third["submission_version"] == 3

    submitted_three = editor.post(
        url(ids.project_a, f"/annotation-submissions/{third['submission_id']}/actions/submit"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted_three.status_code == 200, submitted_three.text

    exceeded = _reject_and_require_rework(
        owner,
        editor,
        ids.project_a,
        third["submission_id"],
        ids.owner_a,
        reason="第三轮返工",
    )
    assert exceeded.status_code == 409
    assert exceeded.json()["error"]["code"] == "annotation_task_rework_limit_exceeded"


def test_assignment_capacity_is_enforced_over_http(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """HTTP 分配端点必须应用服务端容量策略（D4）。"""
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-CAP-1")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    schema = create_published_schema(editor, owner, ids.project_a, "schema-cap")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-cap")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-cap",
    )
    over = editor.post(
        url(ids.project_a, "/annotation-assignments"),
        json={
            "batch_id": work["batch"]["batch_id"],
            "assignee_id": str(ids.viewer_a),
            "assignment_role": "ANNOTATOR",
            "planned_item_count": 51,
            "planned_minutes": 30,
        },
    )
    assert over.status_code == 409, over.text
    assert over.json()["error"]["code"] == "annotation_assignment_capacity_exceeded"


def test_quality_sampling_plan_is_exposed_over_http(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """抽检计划端点必须返回策略版本、抽样类型、理由与真实被选样本（D6）。"""
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-PLAN-1")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    schema = create_published_schema(editor, owner, ids.project_a, "schema-plan")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-plan")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-plan",
    )
    submission = create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )
    plan_payload = {
        "task_id": work["task"]["task_id"],
        "candidates": [{"submission_id": submission["submission_id"]}],
        "sampling_policy_version": "quality/1",
        "sample_type": "RANDOM",
        "sample_size": 1,
        "sample_reason": "例行抽检",
    }
    planned = owner.post(
        url(ids.project_a, "/quality-samples/plan"),
        headers={"Idempotency-Key": "plan-idem-0001"},
        json=plan_payload,
    )
    assert planned.status_code == 201, planned.text
    replay = owner.post(
        url(ids.project_a, "/quality-samples/plan"),
        headers={"Idempotency-Key": "plan-idem-0001"},
        json=plan_payload,
    )
    assert replay.status_code == 201, replay.text
    assert [item["quality_sample_id"] for item in replay.json()] == [
        item["quality_sample_id"] for item in planned.json()
    ]
    duplicated = owner.post(
        url(ids.project_a, "/quality-samples/plan"),
        json={
            **plan_payload,
            "candidates": [
                {"submission_id": submission["submission_id"]},
                {"submission_id": submission["submission_id"]},
            ],
        },
    )
    assert duplicated.status_code == 422
    samples = planned.json()
    assert len(samples) == 1
    sample = samples[0]
    assert sample["sampling_policy_version"] == "quality/1"
    assert sample["sample_type"] == "RANDOM"
    assert sample["sample_reason"] == "例行抽检"
    assert sample["submission_id"] == submission["submission_id"]
    assert sample["status"] == "PLANNED"
    assert sample["item_refs"]


def test_withdrawn_submission_can_be_superseded(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """撤回后的提交必须允许新版本替代，而不是永久卡死（D2 补充）。"""
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-WITHDRAW-1")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-withdraw")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-withdraw")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-withdraw",
    )
    assignment_id = work["assignment"]["assignment_id"]

    created = editor.post(
        url(ids.project_a, "/annotation-submissions"),
        json={
            "assignment_id": str(assignment_id),
            "records": [record_payload(episode_id)],
            "data_source": "REAL",
        },
    )
    assert created.status_code == 201, created.text
    submission_id = created.json()["submission_id"]
    withdrawn = editor.post(
        url(ids.project_a, f"/annotation-submissions/{submission_id}/actions/withdraw"),
        headers={"If-Match": '"1"'},
        json={"reason": "误提交，撤回修改"},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "WITHDRAWN"

    replacement = editor.post(
        url(ids.project_a, "/annotation-submissions"),
        json={
            "assignment_id": str(assignment_id),
            "records": [record_payload(episode_id)],
            "data_source": "REAL",
        },
    )
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["submission_version"] == 2
    assert replacement.json()["supersedes_id"] == submission_id
    old = editor.get(url(ids.project_a, f"/annotation-submissions/{submission_id}"))
    assert old.json()["status"] == "SUPERSEDED"


def test_rework_returns_task_and_batch_from_review(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """任务/批次已进入 REVIEWING 时，返工必须把工作范围退回 REWORK（C1）。"""
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-REVIEW-REWORK")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-review")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-review")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-review",
    )
    task_id = work["task"]["task_id"]
    batch_id = work["batch"]["batch_id"]
    submission = create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )

    batch_submitted = editor.post(
        url(ids.project_a, f"/annotation-batches/{batch_id}/actions/submit"),
        headers={"If-Match": f'"{work["batch"]["version"]}"'},
        json={},
    )
    assert batch_submitted.status_code == 200, batch_submitted.text
    task_submitted = editor.post(
        url(ids.project_a, f"/annotation-tasks/{task_id}/actions/submit"),
        headers={"If-Match": f'"{work["task"]["version"]}"'},
        json={},
    )
    assert task_submitted.status_code == 200, task_submitted.text
    task_reviewing = editor.post(
        url(ids.project_a, f"/annotation-tasks/{task_id}/actions/system-review"),
        headers={"If-Match": f'"{task_submitted.json()["version"]}"'},
        json={},
    )
    assert task_reviewing.status_code == 200, task_reviewing.text
    batch_reviewing = editor.post(
        url(ids.project_a, f"/annotation-batches/{batch_id}/actions/system-review"),
        headers={"If-Match": f'"{batch_submitted.json()["version"]}"'},
        json={},
    )
    assert batch_reviewing.status_code == 200, batch_reviewing.text

    required = _reject_and_require_rework(
        owner,
        editor,
        ids.project_a,
        submission["submission_id"],
        ids.owner_a,
        reason="复核退回",
    )
    assert required.status_code == 200, required.text

    task_after = editor.get(url(ids.project_a, f"/annotation-tasks/{task_id}"))
    batch_after = editor.get(url(ids.project_a, f"/annotation-batches/{batch_id}"))
    assert task_after.json()["status"] == "REWORK"
    assert batch_after.json()["status"] == "REWORK"

    replacement = editor.post(
        url(ids.project_a, "/annotation-submissions"),
        json={
            "assignment_id": str(work["assignment"]["assignment_id"]),
            "records": [record_payload(episode_id)],
            "data_source": "REAL",
        },
    )
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["submission_version"] == 2


def test_require_rework_requires_reviewer_or_owner(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """非复核人/非项目负责人不得发起返工要求（R1）。"""
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-REWORK-ACL")
    other_editor_id = seed_arbiter(factory, ids.project_a)
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    other = make_client(factory, other_editor_id)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-rework-acl")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-rework-acl")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-rework-acl",
    )
    submission = create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )
    review = owner.post(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}/reviews"),
        json={"reviewer_id": str(ids.owner_a)},
    )
    review_id = review.json()["review_id"]
    owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    rejected = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/reject"),
        headers={"If-Match": '"2"'},
        json={
            "reason": "标签错误",
            "issues": [{"code": "E1", "severity": "MAJOR", "message": "错误"}],
        },
    )
    assert rejected.status_code == 200, rejected.text
    current = editor.get(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}")
    )
    denied = other.post(
        url(
            ids.project_a,
            f"/annotation-submissions/{submission['submission_id']}/actions/require-rework",
        ),
        headers={"If-Match": f'"{current.json()["version"]}"'},
        json={"reason": "越权返工"},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "forbidden"


def test_second_task_batch_does_not_collide(api_db) -> None:
    """同一项目里两个任务建批次，code 不得撞车。

    这是一个真实存在过的缺陷：批次 ``code`` 由 ``f"B-{sequence_no:04d}"``
    生成，而 unique 键是 ``(project_id, code)``、`sequence_no` 只在**单个
    任务内**递增。于是同项目第二个任务的第一个批次必然撞
    ``uq_annotation_batches_project_code``——「多任务并行标注」这条常规路径
    直接不可用。既有测试只建过一个任务的批次，所以一直没暴露。
    """
    factory, ids = api_db
    ep1 = seed_episode(factory, ids.project_a, ids.editor_a, "EP-MULTI-1")
    ep2 = seed_episode(factory, ids.project_a, ids.editor_a, "EP-MULTI-2")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    codes: list[str] = []
    for index, episode_id in enumerate((ep1, ep2), start=1):
        schema = create_published_schema(editor, owner, ids.project_a, f"schema-multi-{index}")
        sample_set = create_snapshot_set(editor, ids.project_a, f"sample-multi-{index}")
        work = create_ready_work(
            editor,
            owner,
            ids.project_a,
            schema,
            sample_set,
            episode_id,
            ids.editor_a,
            f"task-multi-{index}",
        )
        codes.append(work["batch"]["code"] if "batch" in work else "")

    # create_ready_work 内部建批次；两次都成功即证明未撞车。
    assert len(codes) == 2


def test_task_progress_and_batch_counts_follow_submissions(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """任务 ``approved_items`` 与批次计数必须由提交事实写入。

    此前两处计数永远为 0：任务验收只能靠豁免说明，批次进度条与实际提交无关。
    """
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-PROGRESS")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    schema = create_published_schema(editor, owner, ids.project_a, "schema-progress")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-progress")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-progress",
    )
    task_id = work["task"]["task_id"]
    batch_id = work["batch"]["batch_id"]

    submission = create_submitted(
        editor, ids.project_a, work["assignment"]["assignment_id"], episode_id
    )
    task = editor.get(url(ids.project_a, f"/annotation-tasks/{task_id}")).json()
    assert task["progress"]["submitted_items"] == 1, task["progress"]
    assert task["progress"]["approved_items"] == 0, task["progress"]
    batch = editor.get(url(ids.project_a, f"/annotation-batches/{batch_id}")).json()
    assert batch["submitted_item_count"] == 1, batch
    assert batch["approved_item_count"] == 0, batch

    review = owner.post(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}/reviews"),
        json={"reviewer_id": str(ids.owner_a)},
    )
    assert review.status_code == 201, review.text
    review_id = review.json()["review_id"]
    claimed = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert claimed.status_code == 200, claimed.text
    approved = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/approve"),
        headers={"If-Match": '"2"'},
        json={"checklist": [{"key": "labels_complete", "passed": True}]},
    )
    assert approved.status_code == 200, approved.text

    task = editor.get(url(ids.project_a, f"/annotation-tasks/{task_id}")).json()
    assert task["progress"]["approved_items"] == 1, task["progress"]
    assert task["progress"]["submitted_items"] == 1, task["progress"]
    batch = editor.get(url(ids.project_a, f"/annotation-batches/{batch_id}")).json()
    assert batch["approved_item_count"] == 1, batch
    assert batch["rejected_item_count"] == 0, batch


def test_submit_task_with_cancelled_batch_is_allowed(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """已取消的批次不该把任务提交永久锁死（DB 守卫与应用层口径必须一致）。

    守卫 SQL 的 SUBMITTED 分支此前把 ``CANCELLED`` 当成"未提交批次"，于是
    只要历史上取消过一个批次，任务就再也提交不了——而且报错是泛化的状态冲突，
    看不出原因。应用层 ``list_non_terminal`` 本来就不返回取消批次，两边口径
    不一致。
    """
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-CANCELLED-BATCH")
    owner = make_client(factory, ids.owner_a)
    editor = make_client(factory, ids.editor_a)

    schema = create_published_schema(editor, owner, ids.project_a, "schema-cancel-batch")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-cancel-batch")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-cancel-batch",
    )
    task_id = work["task"]["task_id"]

    # 第二个批次：建好立刻取消，制造"任务下存在 CANCELLED 批次"。
    # 计划量 0：任务目标只有 1 件，第二个批次不占计划量，只为制造
    # "任务下存在已取消批次"这个前置状态。
    extra = editor.post(
        url(ids.project_a, f"/annotation-tasks/{task_id}/batches"),
        json={"planned_item_count": 0, "item_refs": [], "sequence_no": 2},
    )
    assert extra.status_code == 201, extra.text
    cancelled = editor.post(
        url(ids.project_a, f"/annotation-batches/{extra.json()['batch_id']}/actions/cancel"),
        headers={"If-Match": '"1"'},
        json={"reason": "合并到首批次执行"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"

    create_submitted(editor, ids.project_a, work["assignment"]["assignment_id"], episode_id)
    batch_submitted = editor.post(
        url(ids.project_a, f"/annotation-batches/{work['batch']['batch_id']}/actions/submit"),
        headers={"If-Match": '"4"'},
        json={},
    )
    assert batch_submitted.status_code == 200, batch_submitted.text

    task = editor.get(url(ids.project_a, f"/annotation-tasks/{task_id}")).json()
    submitted = editor.post(
        url(ids.project_a, f"/annotation-tasks/{task_id}/actions/submit"),
        headers={"If-Match": f'"{task["version"]}"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "SUBMITTED"


def test_second_review_can_claim_same_submission(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """双人复核：第二条复核 claim 不得被提交状态机拒绝。

    提交只该被推进一次（SUBMITTED → IN_REVIEW）；此前每次 claim 都无条件推进，
    第二条复核永远领不了，双人复核形同虚设。
    """
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-DOUBLE-REVIEW")
    owner = make_client(factory, ids.owner_a)
    editor = make_client(factory, ids.editor_a)
    second_reviewer_id = seed_arbiter(factory, ids.project_a)
    second = make_client(factory, second_reviewer_id)

    schema = create_published_schema(editor, owner, ids.project_a, "schema-double-review")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-double-review")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-double-review",
    )
    submission = create_submitted(
        editor, ids.project_a, work["assignment"]["assignment_id"], episode_id
    )

    first = owner.post(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}/reviews"),
        json={"reviewer_id": str(ids.owner_a)},
    )
    assert first.status_code == 201, first.text
    second_review = second.post(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}/reviews"),
        json={"reviewer_id": str(second_reviewer_id)},
    )
    assert second_review.status_code == 201, second_review.text

    claimed = owner.post(
        url(ids.project_a, f"/annotation-reviews/{first.json()['review_id']}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert claimed.status_code == 200, claimed.text
    claimed_second = second.post(
        url(
            ids.project_a,
            f"/annotation-reviews/{second_review.json()['review_id']}/actions/claim",
        ),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert claimed_second.status_code == 200, claimed_second.text
    assert claimed_second.json()["status"] == "IN_REVIEW"


def test_arbitration_can_be_reclaimed_after_recuse(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """仲裁人回避后，同一仲裁必须能被重新受理。

    ``recuse`` 会把仲裁退回 PENDING，但提交在第一次受理时已进入 ARBITRATING；
    此前再次 ``claim`` 会重复推进提交状态而被状态机拒绝，结果"退出了就接不回来"。
    """
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-ARB-RECUSE")
    arbiter_id = seed_arbiter(factory, ids.project_a)
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    arbiter = make_client(factory, arbiter_id)

    schema = create_published_schema(editor, owner, ids.project_a, "schema-arb-recuse")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-arb-recuse")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-arb-recuse",
    )
    submission = create_submitted(
        editor, ids.project_a, work["assignment"]["assignment_id"], episode_id
    )
    review = owner.post(
        url(ids.project_a, f"/annotation-submissions/{submission['submission_id']}/reviews"),
        json={"reviewer_id": str(ids.owner_a)},
    )
    assert review.status_code == 201, review.text
    review_id = review.json()["review_id"]
    owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    escalated = owner.post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/escalate"),
        headers={"If-Match": '"2"'},
        json={"reason": "复核意见不一致"},
    )
    assert escalated.status_code == 200, escalated.text

    arbitration = editor.post(
        url(
            ids.project_a,
            f"/annotation-submissions/{submission['submission_id']}/arbitrations",
        ),
        json={
            "review_id": review_id,
            "arbiter_ids": [str(arbiter_id)],
            "quorum": 1,
            "trigger": "REVIEWER_DISAGREEMENT",
        },
    )
    assert arbitration.status_code == 201, arbitration.text
    arbitration_id = arbitration.json()["arbitration_id"]

    first = arbiter.post(
        url(ids.project_a, f"/annotation-arbitrations/{arbitration_id}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert first.status_code == 200, first.text
    recused = arbiter.post(
        url(ids.project_a, f"/annotation-arbitrations/{arbitration_id}/actions/recuse"),
        headers={"If-Match": f'"{first.json()["version"]}"'},
        json={"reason": "与标注人存在利益关系"},
    )
    assert recused.status_code == 200, recused.text
    assert recused.json()["status"] == "PENDING"

    reclaimed = arbiter.post(
        url(ids.project_a, f"/annotation-arbitrations/{arbitration_id}/actions/claim"),
        headers={"If-Match": f'"{recused.json()["version"]}"'},
        json={},
    )
    assert reclaimed.status_code == 200, reclaimed.text
    assert reclaimed.json()["status"] == "IN_ARBITRATION"


def test_task_list_overdue_filter_and_computed_flag(api_db) -> None:
    """SLA 逾期：列表过滤下推到 SQL，响应字段是服务端计算事实。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    seed_episode(factory, ids.project_a, ids.editor_a, "EP-SLA-001")
    schema = create_published_schema(editor, owner, ids.project_a, "schema-sla")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-sla")

    now = datetime.now(UTC)

    def create(code: str, due_at: datetime | None) -> dict[str, object]:
        body: dict[str, object] = {
            "code": code,
            "name": f"任务 {code}",
            "sample_set_id": sample_set["sample_set_id"],
            "schema_id": schema["schema_id"],
            "target_item_count": 1,
            "target_episode_count": 1,
        }
        if due_at is not None:
            body["due_at"] = due_at.isoformat()
        response = editor.post(url(ids.project_a, "/annotation-tasks"), json=body)
        assert response.status_code == 201, response.text
        return response.json()

    overdue = create("task-sla-overdue", now - timedelta(hours=2))
    cancelled = create("task-sla-cancelled", now - timedelta(hours=1))
    create("task-sla-future", now + timedelta(hours=2))
    create("task-sla-no-due", None)

    # 终态任务即使截止时间已过也不算逾期（直接落终态，避免跑完整链路）。
    with factory() as session:
        session.execute(
            update(AnnotationTaskModel)
            .where(AnnotationTaskModel.id == UUID(str(cancelled["task_id"])))
            .values(status="CANCELLED")
            .execution_options(synchronize_session=False)
        )
        session.commit()

    assert overdue["is_overdue"] is True

    only_overdue = editor.get(url(ids.project_a, "/annotation-tasks?overdue=true"))
    assert only_overdue.status_code == 200, only_overdue.text
    overdue_items = only_overdue.json()["items"]
    assert [item["task_id"] for item in overdue_items] == [overdue["task_id"]]
    assert all(item["is_overdue"] is True for item in overdue_items)

    not_overdue = editor.get(url(ids.project_a, "/annotation-tasks?overdue=false"))
    assert not_overdue.status_code == 200, not_overdue.text
    not_overdue_items = not_overdue.json()["items"]
    not_overdue_ids = {item["task_id"] for item in not_overdue_items}
    assert str(cancelled["task_id"]) in not_overdue_ids
    assert len(not_overdue_items) == 3
    assert all(item["is_overdue"] is False for item in not_overdue_items)

    # 终态任务详情同样如实返回 false。
    cancelled_detail = editor.get(url(ids.project_a, f"/annotation-tasks/{cancelled['task_id']}"))
    assert cancelled_detail.status_code == 200, cancelled_detail.text
    assert cancelled_detail.json()["is_overdue"] is False
