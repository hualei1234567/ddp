"""标注完整链路：提交/复核/仲裁职责分离、质检抽检、黄金集与 IAA。"""

from __future__ import annotations

from uuid import uuid4

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
