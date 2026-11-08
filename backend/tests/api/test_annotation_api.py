"""标注 HTTP API 基础契约：权限、校验、幂等、分页与错误码映射。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.infrastructure.db.models.episodes import EpisodeModel
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    record_payload,
    schema_payload,
    seed_episode,
    url,
)
from tests.api.conftest import ApiIds, make_client


def test_schema_permissions_validation_and_idempotency(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    viewer = make_client(factory, ids.viewer_a)
    outsider = make_client(factory, ids.outsider)

    denied = viewer.post(
        url(ids.project_a, "/annotation-schemas"),
        json=schema_payload("viewer-denied"),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"

    unknown_field = editor.post(
        url(ids.project_a, "/annotation-schemas"),
        json={**schema_payload("extra"), "status": "PUBLISHED"},
    )
    assert unknown_field.status_code == 422
    assert unknown_field.json()["error"]["code"] == "validation_failed"

    created = editor.post(
        url(ids.project_a, "/annotation-schemas"),
        json=schema_payload("schema-idem"),
        headers={"Idempotency-Key": "idem-schema-0001"},
    )
    assert created.status_code == 201, created.text
    first = created.json()
    replay = editor.post(
        url(ids.project_a, "/annotation-schemas"),
        json=schema_payload("schema-idem"),
        headers={"Idempotency-Key": "idem-schema-0001"},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["schema_id"] == first["schema_id"]
    reused = editor.post(
        url(ids.project_a, "/annotation-schemas"),
        json=schema_payload("schema-idem-other"),
        headers={"Idempotency-Key": "idem-schema-0001"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "idempotency_key_reused"

    duplicate_code = editor.post(
        url(ids.project_a, "/annotation-schemas"),
        json=schema_payload("schema-idem"),
    )
    assert duplicate_code.status_code == 409
    assert duplicate_code.json()["error"]["code"] == "duplicate_resource"

    missing = editor.patch(
        url(ids.project_a, f"/annotation-schemas/{first['schema_id']}"),
        json={"name": "无前置条件"},
    )
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "precondition_required"

    patched = editor.patch(
        url(ids.project_a, f"/annotation-schemas/{first['schema_id']}"),
        headers={"If-Match": '"1"'},
        json={"name": "新名称"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["version"] == 2

    stale = editor.patch(
        url(ids.project_a, f"/annotation-schemas/{first['schema_id']}"),
        headers={"If-Match": '"1"'},
        json={"name": "陈旧"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"

    invalid_action = editor.post(
        url(ids.project_a, f"/annotation-schemas/{first['schema_id']}/actions/nope"),
        headers={"If-Match": '"2"'},
        json={},
    )
    assert invalid_action.status_code == 422
    assert invalid_action.json()["error"]["code"] == "validation_failed"

    assert outsider.get(url(ids.project_a, "/annotation-schemas")).status_code == 403


def test_pagination_and_cross_project_isolation(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    for code in ("page-1", "page-2", "page-3"):
        response = editor.post(
            url(ids.project_a, "/annotation-schemas"),
            json=schema_payload(code),
        )
        assert response.status_code == 201, response.text

    first = editor.get(url(ids.project_a, "/annotation-schemas?limit=2"))
    assert first.status_code == 200
    page = first.json()
    assert len(page["items"]) == 2
    assert page["next_cursor"]
    second = editor.get(
        url(ids.project_a, f"/annotation-schemas?limit=2&cursor={page['next_cursor']}")
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 1
    assert second.json()["next_cursor"] is None

    bad_cursor = editor.get(url(ids.project_a, "/annotation-schemas?cursor=not-base64"))
    assert bad_cursor.status_code == 422

    foreign = owner.post(
        url(ids.project_b, "/annotation-schemas"),
        json=schema_payload("foreign-schema"),
    )
    assert foreign.status_code == 201, foreign.text
    foreign_id = foreign.json()["schema_id"]
    cross = editor.get(url(ids.project_a, f"/annotation-schemas/{foreign_id}"))
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "resource_not_found"

    foreign_set = owner.post(
        url(ids.project_b, "/annotation-sample-sets"),
        json={"code": "foreign-set", "filters": {}},
    )
    assert foreign_set.status_code == 201, foreign_set.text
    cross_set = editor.get(
        url(ids.project_a, f"/annotation-sample-sets/{foreign_set.json()['sample_set_id']}")
    )
    assert cross_set.status_code == 404
    assert cross_set.json()["error"]["code"] == "resource_not_found"


def test_assignment_and_submission_error_mapping(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-ERR-1")
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    viewer = make_client(factory, ids.viewer_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-err")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-err")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-err",
    )

    # 非本人不能提交分配（对象级权限 403）。
    foreign_claim = owner.post(
        url(
            ids.project_a,
            f"/annotation-assignments/{work['assignment']['assignment_id']}/actions/submit",
        ),
        headers={"If-Match": '"3"'},
        json={},
    )
    assert foreign_claim.status_code == 403
    assert foreign_claim.json()["error"]["code"] == "annotation_assignment_not_owned"

    # 缺 If-Match 返回 428，非法状态迁移返回 409。
    missing = editor.post(
        url(
            ids.project_a,
            f"/annotation-assignments/{work['assignment']['assignment_id']}/actions/complete",
        ),
        json={},
    )
    assert missing.status_code == 428
    illegal = editor.post(
        url(
            ids.project_a,
            f"/annotation-assignments/{work['assignment']['assignment_id']}/actions/complete",
        ),
        headers={"If-Match": '"3"'},
        json={},
    )
    assert illegal.status_code == 409
    assert illegal.json()["error"]["code"] == "state_transition_invalid"

    draft_created = editor.post(
        url(ids.project_a, "/annotation-submissions"),
        json={
            "assignment_id": work["assignment"]["assignment_id"],
            "records": [record_payload(episode_id)],
            "data_source": "REAL",
        },
    )
    assert draft_created.status_code == 201, draft_created.text
    draft = draft_created.json()
    assert draft["status"] == "DRAFT"

    foreign_draft = viewer.get(
        url(ids.project_a, f"/annotation-submissions/{draft['submission_id']}")
    )
    assert foreign_draft.status_code == 403
    assert foreign_draft.json()["error"]["code"] == "forbidden"

    submitted = editor.post(
        url(ids.project_a, f"/annotation-submissions/{draft['submission_id']}/actions/submit"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    submission = submitted.json()

    # 提交资源不允许直接执行 approve，必须走复核端点。
    bypass = editor.post(
        url(
            ids.project_a,
            f"/annotation-submissions/{submission['submission_id']}/actions/approve",
        ),
        headers={"If-Match": '"2"'},
        json={},
    )
    assert bypass.status_code == 422
    assert bypass.json()["error"]["code"] == "validation_failed"


def _seed_rejected_episode(
    factory: sessionmaker[Session],
    project_id,
    actor_id,
    code: str,
) -> None:
    episode_id = uuid4()
    with factory() as session:
        session.add(
            EpisodeModel(
                id=episode_id,
                project_id=project_id,
                episode_lineage_id=uuid4(),
                episode_version=1,
                code=code,
                status="PENDING",
                quality_status="NOT_CHECKED",
                annotation_status="NOT_STARTED",
                scene_labels=["kitchen"],
                start_time_ns=1_000,
                end_time_ns=2_000,
                duration_ns=1_000,
                time_domain="UTC",
                time_base={},
                canonical_schema_version="ddp.canonical/1.0.0-draft",
                pipeline_id="parser",
                pipeline_version="1.0.0",
                segmentation_rule_version="seg-v1",
                calibration_snapshot=[],
                manifest_key=f"canonical/{project_id}/{episode_id}/1/manifest.json",
                manifest_sha256="sha256:" + "b" * 64,
                frame_count=10,
                attempt=1,
                max_attempts=3,
                created_at=datetime(2026, 11, 9, 12, 0, tzinfo=UTC),
                updated_at=datetime(2026, 11, 9, 12, 0, tzinfo=UTC),
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        for status, quality, failure in (
            ("PROCESSING", "NOT_CHECKED", None),
            ("READY", "NOT_CHECKED", None),
            ("QUALITY_CHECKING", "FAIL", None),
            ("REJECTED", "FAIL", "质量门禁未通过"),
        ):
            values = {"status": status, "quality_status": quality}
            if failure is not None:
                values["failure_reason"] = failure
            session.execute(
                update(EpisodeModel).where(EpisodeModel.id == episode_id).values(**values)
            )
        session.commit()


def test_sample_set_preview_reports_gate_exclusion_reasons(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """门禁排除的 Episode 必须出现在 exclusion_summary（D5）。"""
    factory, ids = api_db
    seed_episode(factory, ids.project_a, ids.editor_a, "EP-EXCL-OK")
    _seed_rejected_episode(factory, ids.project_a, ids.editor_a, "EP-EXCL-REJECTED")
    editor = make_client(factory, ids.editor_a)

    created = editor.post(
        url(ids.project_a, "/annotation-sample-sets"),
        json={"code": "sample-exclusion", "filters": {"scene_labels": ["kitchen"]}},
    )
    assert created.status_code == 201, created.text
    sample_set_id = created.json()["sample_set_id"]
    previewed = editor.post(
        url(ids.project_a, f"/annotation-sample-sets/{sample_set_id}/actions/preview"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert previewed.status_code == 200, previewed.text
    body = previewed.json()
    assert body["exclusion_summary"].get("episode_status:REJECTED") == 1
    assert body["excluded_count"] >= 1


def test_published_schema_patch_returns_contract_error_code(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """发布后原地修改定义必须返回契约错误码 annotation_schema_immutable（D7）。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    schema = create_published_schema(editor, owner, ids.project_a, "schema-immutable")
    patched = editor.patch(
        url(ids.project_a, f"/annotation-schemas/{schema['schema_id']}"),
        headers={"If-Match": f'"{schema["version"]}"'},
        json={"annotation_levels": ["EPISODE", "FRAME"]},
    )
    assert patched.status_code == 409, patched.text
    assert patched.json()["error"]["code"] == "annotation_schema_immutable"
