"""标注完整链路：提交/复核/仲裁职责分离、质检抽检、黄金集与 IAA。"""

from __future__ import annotations

from uuid import uuid4

from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from sqlalchemy import select
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
    planned = owner.post(
        url(ids.project_a, "/quality-samples/plan"),
        json={
            "task_id": work["task"]["task_id"],
            "submission_ids": [submission["submission_id"]],
            "sampling_policy_version": "quality/1",
            "sample_type": "RANDOM",
            "sample_size": 1,
            "sample_reason": "例行抽检",
        },
    )
    assert planned.status_code == 201, planned.text
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
