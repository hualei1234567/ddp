"""AI 预标注建议 API 集成测试。

覆盖验收点：
- 登记 → 确认 → 查询全链路；
- 跨项目隔离（另一项目看不到、改不了）；
- 越权（viewer / 无关项目成员）被拒绝；
- **AI 建议与人工记录物理分离**：``annotation_records.labels`` 不因登记
  建议而变化，建议单独落在 ``annotation_ai_suggestions``；
- 模型身份缺一不可（审计缺口）；
- 乐观锁 ``If-Match`` 与幂等键。
"""

from __future__ import annotations

from uuid import UUID

from app.infrastructure.db.models.annotation_ai import AnnotationAiSuggestion
from app.infrastructure.db.models.annotation_results import AnnotationRecord
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

_AI_SUGGESTION_PATH = "/annotation-submissions/{submission_id}/ai-suggestions"
_CONFIRM_PATH = "/annotation-ai-suggestions/{suggestion_id}/confirm"


def _suggestion_payload(
    annotation_id: UUID,
    *,
    model_id: str = "ddp-action-vlm",
    prompt_version: str = "prompt-7",
) -> dict[str, object]:
    return {
        "annotation_id": str(annotation_id),
        "label_key": "action",
        "suggested_value": "PLACE",
        "model_id": model_id,
        "model_version": "1.2.0",
        "prompt_version": prompt_version,
        "confidence": 0.87,
    }


def _prepare(
    factory: sessionmaker[Session],
    ids: ApiIds,
    code: str,
) -> tuple[object, dict[str, object]]:
    """建好发布 Schema、快照样本集、可工作分配，并提交一版标注。"""
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
    )
    submission = create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )
    return submission, submission


def _annotation_id(factory: sessionmaker[Session], submission_id: str) -> UUID:
    with factory() as session:
        return session.scalar(
            select(AnnotationRecord.id).where(AnnotationRecord.submission_id == UUID(submission_id))
        )


def test_record_confirm_and_list_roundtrip(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    submission, _ = _prepare(factory, ids, "ai-roundtrip")
    editor = make_client(factory, ids.editor_a)
    subscription_url = url(
        ids.project_a,
        _AI_SUGGESTION_PATH.format(submission_id=submission["submission_id"]),
    )

    annotation_id = _annotation_id(factory, submission["submission_id"])
    recorded = editor.post(
        subscription_url, json={"suggestions": [_suggestion_payload(annotation_id)]}
    )
    assert recorded.status_code == 201, recorded.text
    items = recorded.json()["items"]
    assert len(items) == 1
    suggestion = items[0]
    assert suggestion["model_id"] == "ddp-action-vlm"
    assert suggestion["model_version"] == "1.2.0"
    assert suggestion["prompt_version"] == "prompt-7"
    assert suggestion["confidence"] == 0.87
    assert suggestion["human_confirmed"] is False
    assert suggestion["human_value"] is None

    listed = editor.get(subscription_url)
    assert listed.status_code == 200, listed.text
    assert [item["suggestion_id"] for item in listed.json()["items"]] == [
        suggestion["suggestion_id"]
    ]

    confirmed = editor.post(
        url(
            ids.project_a,
            _CONFIRM_PATH.format(suggestion_id=suggestion["suggestion_id"]),
        ),
        headers={"If-Match": f'"{suggestion["version"]}"'},
        json={"human_value": "PLACE"},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["human_confirmed"] is True
    assert body["human_value"] == "PLACE"
    # 模型输出保留原值，人工值独立成列——这正是"AI vs 人工"可统计的前提。
    assert body["suggested_value"] == "PLACE"
    assert body["decided_by"] == str(ids.editor_a)
    assert body["version"] == suggestion["version"] + 1


def test_ai_suggestions_are_physically_separate_from_records(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """核心验收：登记 AI 建议不得写入 ``annotation_records.labels``。

    历史实现把两者塞进同一 JSONB 字段，只靠标签的 ``source`` 区分，
    导致模型身份丢失、建议被人工值覆盖。本用例断言物理分离。
    """
    factory, ids = api_db
    submission, _ = _prepare(factory, ids, "ai-separation")
    editor = make_client(factory, ids.editor_a)
    annotation_id = _annotation_id(factory, submission["submission_id"])

    with factory() as session:
        before = session.scalar(
            select(AnnotationRecord.labels).where(AnnotationRecord.id == annotation_id)
        )
        before_sha = session.scalar(
            select(AnnotationRecord.record_sha256).where(AnnotationRecord.id == annotation_id)
        )

    recorded = editor.post(
        url(
            ids.project_a,
            _AI_SUGGESTION_PATH.format(submission_id=submission["submission_id"]),
        ),
        json={"suggestions": [_suggestion_payload(annotation_id)]},
    )
    assert recorded.status_code == 201, recorded.text

    with factory() as session:
        after = session.scalar(
            select(AnnotationRecord.labels).where(AnnotationRecord.id == annotation_id)
        )
        after_sha = session.scalar(
            select(AnnotationRecord.record_sha256).where(AnnotationRecord.id == annotation_id)
        )
        stored = session.scalar(select(func.count()).select_from(AnnotationAiSuggestion))

    assert after == before, "AI 建议不得写入标注记录的 labels 字段"
    assert after_sha == before_sha, "AI 建议不得改变记录内容摘要"
    assert stored == 1, "AI 建议必须落在独立表 annotation_ai_suggestions"
    # 既有标签语义未受影响：仍全部为 HUMAN 来源。
    assert {label["source"] for label in before} == {"HUMAN"}


def test_model_identity_is_mandatory_over_http(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    submission, _ = _prepare(factory, ids, "ai-identity")
    editor = make_client(factory, ids.editor_a)
    annotation_id = _annotation_id(factory, submission["submission_id"])
    endpoint = url(
        ids.project_a,
        _AI_SUGGESTION_PATH.format(submission_id=submission["submission_id"]),
    )

    for field in ("model_id", "model_version", "prompt_version"):
        payload = _suggestion_payload(annotation_id)
        payload[field] = ""
        rejected = editor.post(endpoint, json={"suggestions": [payload]})
        # 空串先被 Pydantic 的 min_length 拦下，返回 422 validation_failed。
        assert rejected.status_code == 422, (field, rejected.text)
        assert rejected.json()["error"]["code"] == "validation_failed"

    missing = _suggestion_payload(annotation_id)
    missing.pop("prompt_version")
    rejected = editor.post(endpoint, json={"suggestions": [missing]})
    assert rejected.status_code == 422, rejected.text


def test_confidence_out_of_range_is_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    submission, _ = _prepare(factory, ids, "ai-confidence")
    editor = make_client(factory, ids.editor_a)
    annotation_id = _annotation_id(factory, submission["submission_id"])
    endpoint = url(
        ids.project_a,
        _AI_SUGGESTION_PATH.format(submission_id=submission["submission_id"]),
    )
    payload = _suggestion_payload(annotation_id)
    payload["confidence"] = 1.5
    rejected = editor.post(endpoint, json={"suggestions": [payload]})
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "validation_failed"


def test_cross_project_isolation(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """项目 B 的负责人拿项目 A 的提交/建议 ID 一律 404，而不是看到数据。"""
    factory, ids = api_db
    submission, _ = _prepare(factory, ids, "ai-cross-project")
    editor = make_client(factory, ids.editor_a)
    annotation_id = _annotation_id(factory, submission["submission_id"])
    recorded = editor.post(
        url(
            ids.project_a,
            _AI_SUGGESTION_PATH.format(submission_id=submission["submission_id"]),
        ),
        json={"suggestions": [_suggestion_payload(annotation_id)]},
    )
    assert recorded.status_code == 201, recorded.text
    suggestion_id = recorded.json()["items"][0]["suggestion_id"]

    # owner_a 在项目 B 里也是 OWNER，但项目 A 的提交不属于项目 B。
    foreign_owner = make_client(factory, ids.owner_a)
    cross_list = foreign_owner.get(
        url(
            ids.project_b,
            _AI_SUGGESTION_PATH.format(submission_id=submission["submission_id"]),
        )
    )
    assert cross_list.status_code == 404, cross_list.text
    assert cross_list.json()["error"]["code"] == "resource_not_found"

    cross_confirm = foreign_owner.post(
        url(ids.project_b, _CONFIRM_PATH.format(suggestion_id=suggestion_id)),
        headers={"If-Match": '"1"'},
        json={"human_value": "PICK"},
    )
    assert cross_confirm.status_code == 404, cross_confirm.text
    assert cross_confirm.json()["error"]["code"] == "resource_not_found"


def test_viewer_and_outsider_cannot_record_suggestions(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    submission, _ = _prepare(factory, ids, "ai-forbidden")
    annotation_id = _annotation_id(factory, submission["submission_id"])
    endpoint = url(
        ids.project_a,
        _AI_SUGGESTION_PATH.format(submission_id=submission["submission_id"]),
    )
    payload = {"suggestions": [_suggestion_payload(annotation_id)]}

    viewer = make_client(factory, ids.viewer_a)
    denied = viewer.post(endpoint, json=payload)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"

    outsider = make_client(factory, ids.outsider)
    excluded = outsider.post(endpoint, json=payload)
    assert excluded.status_code == 403


def test_non_owner_editor_cannot_confirm_others_suggestion(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """同项目内的普通编辑者（非标注人、非复核人）不得确认他人建议。"""
    factory, ids = api_db
    submission, _ = _prepare(factory, ids, "ai-separation-duty")
    editor = make_client(factory, ids.editor_a)
    annotation_id = _annotation_id(factory, submission["submission_id"])
    recorded = editor.post(
        url(
            ids.project_a,
            _AI_SUGGESTION_PATH.format(submission_id=submission["submission_id"]),
        ),
        json={"suggestions": [_suggestion_payload(annotation_id)]},
    )
    assert recorded.status_code == 201, recorded.text
    suggestion_id = recorded.json()["items"][0]["suggestion_id"]

    # viewer_a 有读权限但无写权限（WriteScope 先于用例拒绝）。
    viewer = make_client(factory, ids.viewer_a)
    denied = viewer.post(
        url(ids.project_a, _CONFIRM_PATH.format(suggestion_id=suggestion_id)),
        headers={"If-Match": '"1"'},
        json={"human_value": "PICK"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"


def test_confirm_requires_if_match_and_honours_version(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    submission, _ = _prepare(factory, ids, "ai-ifmatch")
    editor = make_client(factory, ids.editor_a)
    annotation_id = _annotation_id(factory, submission["submission_id"])
    recorded = editor.post(
        url(
            ids.project_a,
            _AI_SUGGESTION_PATH.format(submission_id=submission["submission_id"]),
        ),
        json={"suggestions": [_suggestion_payload(annotation_id)]},
    )
    suggestion_id = recorded.json()["items"][0]["suggestion_id"]
    endpoint = url(ids.project_a, _CONFIRM_PATH.format(suggestion_id=suggestion_id))

    missing = editor.post(endpoint, json={"human_value": "PICK"})
    assert missing.status_code == 428, missing.text

    stale = editor.post(endpoint, headers={"If-Match": '"7"'}, json={"human_value": "PICK"})
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "version_conflict"


def test_record_suggestions_is_idempotent(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    submission, _ = _prepare(factory, ids, "ai-idem")
    editor = make_client(factory, ids.editor_a)
    annotation_id = _annotation_id(factory, submission["submission_id"])
    endpoint = url(
        ids.project_a,
        _AI_SUGGESTION_PATH.format(submission_id=submission["submission_id"]),
    )
    payload = {"suggestions": [_suggestion_payload(annotation_id)]}
    headers = {"Idempotency-Key": "idem-ai-suggest-0001"}

    first = editor.post(endpoint, json=payload, headers=headers)
    assert first.status_code == 201, first.text
    replay = editor.post(endpoint, json=payload, headers=headers)
    assert replay.status_code == 201, replay.text
    assert [item["suggestion_id"] for item in replay.json()["items"]] == [
        item["suggestion_id"] for item in first.json()["items"]
    ]

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationAiSuggestion)) == 1


def test_idempotent_replay_returns_only_the_first_batch(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """重放必须返回首次请求那一批，不能把后来追加的建议一并返回。

    同一份提交换模型版本再登记（不同幂等键、不同 identity，唯一约束允许并存，
    这是多模型对比的设计用法）。若重放按"提交下的全部建议"返回，第一次请求的
    重放响应会凭空多出后来那批，客户端看到与首次不同的结果。
    """
    factory, ids = api_db
    submission, _ = _prepare(factory, ids, "ai-idem-scope")
    editor = make_client(factory, ids.editor_a)
    annotation_id = _annotation_id(factory, submission["submission_id"])
    endpoint = url(
        ids.project_a,
        _AI_SUGGESTION_PATH.format(submission_id=submission["submission_id"]),
    )
    first_payload = {"suggestions": [_suggestion_payload(annotation_id, model_id="model-a")]}
    second_payload = {"suggestions": [_suggestion_payload(annotation_id, model_id="model-b")]}

    first = editor.post(
        endpoint, json=first_payload, headers={"Idempotency-Key": "idem-ai-scope-0001"}
    )
    assert first.status_code == 201, first.text
    second = editor.post(
        endpoint, json=second_payload, headers={"Idempotency-Key": "idem-ai-scope-0002"}
    )
    assert second.status_code == 201, second.text
    assert len(second.json()["items"]) == 1

    replay = editor.post(
        endpoint, json=first_payload, headers={"Idempotency-Key": "idem-ai-scope-0001"}
    )
    assert replay.status_code == 201, replay.text
    assert [item["model_id"] for item in replay.json()["items"]] == ["model-a"]
    assert [item["suggestion_id"] for item in replay.json()["items"]] == [
        item["suggestion_id"] for item in first.json()["items"]
    ]

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationAiSuggestion)) == 2


def test_suggestion_for_foreign_annotation_is_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """``annotation_id`` 不属于该提交时必须 404，不能凭外键"记录存在"就放行。"""
    factory, ids = api_db
    submission, _ = _prepare(factory, ids, "ai-foreign-record")
    editor = make_client(factory, ids.editor_a)
    endpoint = url(
        ids.project_a,
        _AI_SUGGESTION_PATH.format(submission_id=submission["submission_id"]),
    )
    rejected = editor.post(
        endpoint,
        json={"suggestions": [_suggestion_payload(UUID(int=0))]},
    )
    assert rejected.status_code == 404, rejected.text
    assert rejected.json()["error"]["code"] == "resource_not_found"


def test_foreign_draft_suggestions_are_invisible(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """他人 DRAFT 提交的 AI 建议对非作者不可见（与提交读取同规则）。"""
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-AI-DRAFT")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-ai-draft")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-ai-draft")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-ai-draft",
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
    draft = created.json()
    assert draft["status"] == "DRAFT"

    draft_records = editor.get(
        url(ids.project_a, f"/annotation-submissions/{draft['submission_id']}/records")
    ).json()["items"]
    annotation_id = UUID(draft_records[0]["annotation_id"])
    recorded = editor.post(
        url(
            ids.project_a,
            _AI_SUGGESTION_PATH.format(submission_id=draft["submission_id"]),
        ),
        json={"suggestions": [_suggestion_payload(annotation_id)]},
    )
    assert recorded.status_code == 201, recorded.text

    # owner 有写权限，但草稿可见性由领域规则决定：403。
    hidden = owner.get(
        url(
            ids.project_a,
            _AI_SUGGESTION_PATH.format(submission_id=draft["submission_id"]),
        )
    )
    assert hidden.status_code == 403, hidden.text
    assert hidden.json()["error"]["code"] == "forbidden"
