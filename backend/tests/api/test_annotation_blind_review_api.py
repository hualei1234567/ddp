"""普通复核盲审选项 API 用例。

覆盖：开启后非标注人读取提交/记录只拿到标签键；标注人与"复核人自己的
记录"始终可见；未开启时行为不变；越权与跨项目隔离。
"""

from __future__ import annotations

from uuid import UUID

from app.infrastructure.db.models.annotation_results import AnnotationRecord
from sqlalchemy import select
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

BLIND_POLICY = {"blind_review": True}


def _setup(
    factory: sessionmaker[Session],
    ids: ApiIds,
    code: str,
    *,
    iaa_policy: dict[str, object] | None = None,
):
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, f"EP-{code}")
    reviewer_id = seed_arbiter(factory, ids.project_a)
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    viewer = make_client(factory, ids.viewer_a)
    reviewer = make_client(factory, reviewer_id)
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
        iaa_policy=iaa_policy,
    )
    submission = create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )
    return {
        "episode_id": episode_id,
        "reviewer_id": reviewer_id,
        "editor": editor,
        "owner": owner,
        "viewer": viewer,
        "reviewer": reviewer,
        "work": work,
        "submission": submission,
    }


def _record_labels(payload: dict[str, object]) -> list[dict[str, object]]:
    records = payload["records"]
    assert isinstance(records, list) and records
    labels = records[0]["labels"]
    assert isinstance(labels, list)
    return labels


def _assert_redacted(labels: list[dict[str, object]]) -> None:
    assert labels
    assert all(
        item == {"label_key": item["label_key"], "value": None, "redacted": True} for item in labels
    )
    assert {item["label_key"] for item in labels} == {"action", "segment"}


def _assert_values_visible(labels: list[dict[str, object]]) -> None:
    by_key = {str(item["label_key"]): item for item in labels}
    assert by_key["action"]["value"] == "PICK"
    assert by_key["segment"]["value"] == {
        "start_time_ns": 1_100,
        "end_time_ns": 1_500,
        "time_domain": "UTC",
    }
    assert all("redacted" not in item for item in labels)


def test_blind_task_hides_values_from_non_annotators(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids, "blind-hide", iaa_policy=dict(BLIND_POLICY))
    submission_id = ctx["submission"]["submission_id"]

    for client in (ctx["reviewer"], ctx["owner"], ctx["viewer"]):
        fetched = client.get(url(ids.project_a, f"/annotation-submissions/{submission_id}"))
        assert fetched.status_code == 200, fetched.text
        body = fetched.json()
        _assert_redacted(_record_labels(body))
        # 覆盖标签内容的 Hash 不能在脱敏视图中形成校验预言机。
        assert body["records_digest"] == ""
        assert body["records"][0]["record_sha256"] == ""

        records = client.get(url(ids.project_a, f"/annotation-submissions/{submission_id}/records"))
        assert records.status_code == 200, records.text
        _assert_redacted(records.json()["items"][0]["labels"])

    # 列表响应不含记录内容，但 records_digest 仍是标签内容的摘要。
    listed = ctx["reviewer"].get(
        url(
            ids.project_a,
            f"/annotation-submissions?task_id={ctx['work']['task']['task_id']}",
        )
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["records_digest"] == ""


def test_annotator_sees_own_records_in_blind_task(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids, "blind-own", iaa_policy=dict(BLIND_POLICY))
    submission_id = ctx["submission"]["submission_id"]

    own = ctx["editor"].get(url(ids.project_a, f"/annotation-submissions/{submission_id}"))
    assert own.status_code == 200, own.text
    _assert_values_visible(_record_labels(own.json()))
    assert own.json()["records_digest"]
    assert own.json()["records"][0]["record_sha256"]
    listed = ctx["editor"].get(
        url(
            ids.project_a,
            f"/annotation-submissions?task_id={ctx['work']['task']['task_id']}",
        )
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["records_digest"]


def test_reviewer_own_submission_stays_visible(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids, "blind-reviewer-own", iaa_policy=dict(BLIND_POLICY))
    batch_id = ctx["work"]["batch"]["batch_id"]

    created = ctx["editor"].post(
        url(ids.project_a, "/annotation-assignments"),
        json={
            "batch_id": str(batch_id),
            "assignee_id": str(ctx["reviewer_id"]),
            "planned_item_count": 1,
            "planned_minutes": 30,
        },
    )
    assert created.status_code == 201, created.text
    assignment = created.json()
    for action, version in (("claim", 1), ("start", 2)):
        moved = ctx["reviewer"].post(
            url(
                ids.project_a,
                f"/annotation-assignments/{assignment['assignment_id']}/actions/{action}",
            ),
            headers={"If-Match": f'"{version}"'},
            json={},
        )
        assert moved.status_code == 200, moved.text

    reviewer_submission = create_submitted(
        ctx["reviewer"],
        ids.project_a,
        assignment["assignment_id"],
        ctx["episode_id"],
    )
    own = ctx["reviewer"].get(
        url(
            ids.project_a,
            f"/annotation-submissions/{reviewer_submission['submission_id']}",
        )
    )
    assert own.status_code == 200, own.text
    _assert_values_visible(_record_labels(own.json()))

    foreign = ctx["editor"].get(
        url(
            ids.project_a,
            f"/annotation-submissions/{reviewer_submission['submission_id']}",
        )
    )
    assert foreign.status_code == 200, foreign.text
    _assert_redacted(_record_labels(foreign.json()))


def test_blind_off_keeps_existing_behavior(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids, "blind-off")
    submission_id = ctx["submission"]["submission_id"]

    fetched = ctx["reviewer"].get(url(ids.project_a, f"/annotation-submissions/{submission_id}"))
    assert fetched.status_code == 200, fetched.text
    _assert_values_visible(_record_labels(fetched.json()))


def test_blind_task_enforced_across_project_and_membership(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids, "blind-scope", iaa_policy=dict(BLIND_POLICY))
    submission_id = ctx["submission"]["submission_id"]
    outsider = make_client(factory, ids.outsider)

    # 非项目成员：路径项目本身就不允许访问。
    denied = outsider.get(url(ids.project_a, f"/annotation-submissions/{submission_id}"))
    assert denied.status_code == 403, denied.text

    # 跨项目：owner_a 是项目 B 成员，但提交属于项目 A，按项目过滤后 404。
    cross = ctx["owner"].get(url(ids.project_b, f"/annotation-submissions/{submission_id}"))
    assert cross.status_code == 404, cross.text


def test_require_rework_response_does_not_leak_labels(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids, "blind-rework", iaa_policy=dict(BLIND_POLICY))
    submission_id = UUID(str(ctx["submission"]["submission_id"]))

    review = ctx["owner"].post(
        url(ids.project_a, f"/annotation-submissions/{submission_id}/reviews"),
        json={"reviewer_id": str(ctx["reviewer_id"])},
    )
    assert review.status_code == 201, review.text
    review_id = review.json()["review_id"]
    claimed = ctx["reviewer"].post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert claimed.status_code == 200, claimed.text
    rejected = ctx["reviewer"].post(
        url(ids.project_a, f"/annotation-reviews/{review_id}/actions/reject"),
        headers={"If-Match": '"2"'},
        json={
            "reason": "标签错误",
            "issues": [{"code": "LABEL_ERROR", "severity": "MAJOR", "message": "错误"}],
        },
    )
    assert rejected.status_code == 200, rejected.text

    current = ctx["editor"].get(url(ids.project_a, f"/annotation-submissions/{submission_id}"))
    assert current.status_code == 200, current.text
    rework = ctx["owner"].post(
        url(ids.project_a, f"/annotation-submissions/{submission_id}/actions/require-rework"),
        headers={"If-Match": f'"{current.json()["version"]}"'},
        json={"reason": "请返工"},
    )
    assert rework.status_code == 200, rework.text
    _assert_redacted(_record_labels(rework.json()))


def test_ai_suggestion_human_value_hidden_from_non_annotator(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """AI 建议的人工最终值也是标注结果，盲审下同样不得下发。"""
    factory, ids = api_db
    ctx = _setup(factory, ids, "blind-ai", iaa_policy=dict(BLIND_POLICY))
    submission_id = ctx["submission"]["submission_id"]
    with factory() as session:
        annotation_id = session.scalar(
            select(AnnotationRecord.id).where(
                AnnotationRecord.submission_id == UUID(str(submission_id))
            )
        )
    assert annotation_id is not None
    suggestions_url = url(
        ids.project_a,
        f"/annotation-submissions/{submission_id}/ai-suggestions",
    )
    recorded = ctx["editor"].post(
        suggestions_url,
        json={
            "suggestions": [
                {
                    "annotation_id": str(annotation_id),
                    "label_key": "action",
                    "suggested_value": "PLACE",
                    "model_id": "ddp-action-vlm",
                    "model_version": "1.2.0",
                    "prompt_version": "prompt-7",
                    "confidence": 0.87,
                }
            ]
        },
    )
    assert recorded.status_code == 201, recorded.text
    suggestion = recorded.json()["items"][0]
    confirmed = ctx["editor"].post(
        url(ids.project_a, f"/annotation-ai-suggestions/{suggestion['suggestion_id']}/confirm"),
        headers={"If-Match": f'"{suggestion["version"]}"'},
        json={"human_value": "PICK"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["human_value"] == "PICK"

    own = ctx["editor"].get(suggestions_url)
    assert own.status_code == 200, own.text
    assert own.json()["items"][0]["human_value"] == "PICK"

    foreign = ctx["reviewer"].get(suggestions_url)
    assert foreign.status_code == 200, foreign.text
    item = foreign.json()["items"][0]
    assert item["human_confirmed"] is True
    assert item["human_value"] is None
    assert item["suggested_value"] == "PLACE"
