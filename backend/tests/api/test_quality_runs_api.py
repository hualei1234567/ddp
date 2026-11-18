"""统一 Quality Engine HTTP API 测试。

覆盖导入/Episode 两类对象、六类聚合、规则集版本、未知类别拒绝、结论分页与
筛选、项目隔离、角色权限以及审计/outbox 同事务留痕。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from app.infrastructure.db.models.datasources import (
    DataSourceImportModel,
    DataSourceModel,
    MediaAssetModel,
)

from tests.api.annotation_support import seed_episode
from tests.api.conftest import make_client
from tests.api.dataset_support import seed_dataset_episodes, url

pytestmark = pytest.mark.usefixtures("api_db")

_SHA = "sha256:" + "a" * 64
_REVISION = "a" * 40


def _seed_import(
    factory,
    ids,
    *,
    project_id: UUID | None = None,
    user_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    project = project_id or ids.project_a
    actor = user_id or ids.editor_a
    source_id, import_id = uuid4(), uuid4()
    with factory() as session:
        session.add(
            DataSourceModel(
                id=source_id,
                project_id=project,
                code=f"hf-{source_id.hex[:8]}",
                name="源",
                kind="HUGGINGFACE",
                repo_id="nvidia/PhysicalAI-Human-1M-100K",
                revision=_REVISION,
                license="CC-BY-4.0",
                access_condition="PUBLIC",
                is_gated=False,
                metadata_json={},
                created_by=actor,
                updated_by=actor,
            )
        )
        session.flush()
        session.add(
            DataSourceImportModel(
                id=import_id,
                project_id=project,
                data_source_id=source_id,
                status="SUCCEEDED",
                scope="SAMPLE",
                tool_name="huggingface_hub",
                tool_version="0.27.1",
                sample_limit=15,
                created_by=actor,
                updated_by=actor,
            )
        )
        session.commit()
    return source_id, import_id


def _seed_asset(
    factory,
    ids,
    source_id: UUID,
    import_id: UUID,
    *,
    project_id: UUID | None = None,
    user_id: UUID | None = None,
    episode_key: str = "ep-1",
    camera: str = "main",
    sha256: str = _SHA,
) -> UUID:
    project = project_id or ids.project_a
    actor = user_id or ids.editor_a
    asset_id = uuid4()
    with factory() as session:
        session.add(
            MediaAssetModel(
                id=asset_id,
                project_id=project,
                data_source_id=source_id,
                import_id=import_id,
                episode_key=episode_key,
                path=f"videos/{episode_key}-{camera}.mp4",
                camera=camera,
                object_key=f"data-sources/{project}/{asset_id}/v.mp4",
                size_bytes=1024,
                sha256=sha256,
                content_type="video/mp4",
                task_name="demo",
                excerpt_start_seconds=0.0,
                excerpt_seconds=10.0,
                source_fps=30.0,
                created_by=actor,
                updated_by=actor,
            )
        )
        session.commit()
    return asset_id


def _create(client, project_id: object, subject_type: str, subject_id: object, **overrides):
    payload: dict[str, object] = {
        "subject_type": subject_type,
        "subject_id": str(subject_id),
    }
    payload.update(overrides)
    return client.post(url(project_id, "/quality-runs"), json=payload)


def _by_category(body: dict[str, object]) -> dict[str, dict[str, object]]:
    return {item["category"]: item for item in body["categories"]}  # type: ignore[index]


def test_import_run_has_six_categories_and_is_retrievable(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    viewer = make_client(factory, ids.viewer_a)
    source_id, import_id = _seed_import(factory, ids)
    _seed_asset(factory, ids, source_id, import_id)

    created = _create(editor, ids.project_a, "DATASOURCE_IMPORT", import_id)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["subject_type"] == "DATASOURCE_IMPORT"
    assert body["subject_id"] == str(import_id)
    assert body["project_id"] == str(ids.project_a)
    assert body["status"] == "PASSED"
    assert body["finding_count"] == 7
    assert body["error_count"] == 0
    assert body["warn_count"] == 0
    assert body["score"] == 1.0
    assert body["rule_set_version"].startswith("sha256:")
    assert [item["category"] for item in body["categories"]] == [
        "INTEGRITY",
        "TEMPORAL",
        "VISUAL",
        "METADATA",
        "ANNOTATION",
        "OVERALL",
    ]
    categories = _by_category(body)
    assert categories["INTEGRITY"]["status"] == "PASS"
    assert categories["ANNOTATION"]["status"] == "NOT_APPLICABLE"
    assert categories["METADATA"]["status"] == "NOT_APPLICABLE"
    assert categories["OVERALL"]["status"] == "PASS"

    detail = viewer.get(url(ids.project_a, f"/quality-runs/{body['run_id']}"))
    assert detail.status_code == 200, detail.text
    assert detail.json()["rule_set_version"] == body["rule_set_version"]

    findings = editor.get(url(ids.project_a, f"/quality-runs/{body['run_id']}/findings"))
    assert findings.status_code == 200, findings.text
    payload = findings.json()
    assert len(payload["items"]) == 7
    assert payload["next_cursor"] is None
    assert all(item["status"] == "PASS" for item in payload["items"])
    assert all(item["rule_version"] == "1.1.0" for item in payload["items"])


def test_import_without_assets_fails_integrity(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    _, import_id = _seed_import(factory, ids)

    response = _create(editor, ids.project_a, "DATASOURCE_IMPORT", import_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error_count"] == 4
    categories = _by_category(body)
    assert categories["INTEGRITY"]["status"] == "FAIL"
    assert categories["VISUAL"]["status"] == "FAIL"
    assert categories["TEMPORAL"]["status"] == "FAIL"


def test_episode_run_uses_annotation_rules_and_reports_all_categories(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    episode_id = seed_dataset_episodes(
        factory,
        ids.project_a,
        ids.editor_a,
        count=1,
        record_labels=[
            {
                "label_key": "action",
                "value": "PICK",
                "source": "HUMAN",
                "label_status": "CONFIRMED",
            }
        ],
    )[0]

    response = _create(editor, ids.project_a, "EPISODE", episode_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["subject_type"] == "EPISODE"
    assert body["status"] == "PASSED"
    categories = _by_category(body)
    # 无导入媒体资产 → 导入规则不适用；不标成通过。
    assert categories["INTEGRITY"]["finding_count"] == 4
    assert categories["INTEGRITY"]["status"] == "NOT_APPLICABLE"
    # 标注规则按 Episode 汇总；必填标签与重复键两条通过，另两条缺上下文。
    annotation = categories["ANNOTATION"]
    assert annotation["finding_count"] == 4
    assert annotation["pass_count"] == 2
    assert annotation["not_applicable_count"] == 2
    assert annotation["status"] == "PASS"
    assert categories["METADATA"]["status"] == "NOT_APPLICABLE"
    assert categories["OVERALL"]["status"] == "PASS"


def test_findings_pagination_and_filters(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    episode_id = seed_dataset_episodes(
        factory,
        ids.project_a,
        ids.editor_a,
        count=1,
        record_labels=[
            {
                "label_key": "action",
                "value": "PICK",
                "source": "HUMAN",
                "label_status": "CONFIRMED",
            }
        ],
    )[0]
    run = _create(editor, ids.project_a, "EPISODE", episode_id).json()
    base = url(ids.project_a, f"/quality-runs/{run['run_id']}/findings")

    first = editor.get(base, params={"limit": 2})
    assert first.status_code == 200, first.text
    assert len(first.json()["items"]) == 2
    assert first.json()["next_cursor"]

    second = editor.get(base, params={"limit": 2, "cursor": first.json()["next_cursor"]})
    assert second.status_code == 200, second.text
    assert len(second.json()["items"]) == 2

    annotation = editor.get(base, params={"category": "ANNOTATION"})
    assert annotation.status_code == 200
    assert len(annotation.json()["items"]) == 4
    assert all(item["category"] == "ANNOTATION" for item in annotation.json()["items"])

    not_applicable = editor.get(base, params={"status": "NOT_APPLICABLE"})
    assert len(not_applicable.json()["items"]) == 9

    # OVERALL 是派生类别，不落 finding。
    overall = editor.get(base, params={"category": "OVERALL"})
    assert overall.status_code == 200
    assert overall.json()["items"] == []

    unknown = editor.get(base, params={"category": "BOGUS"})
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "validation_failed"


def test_unknown_subject_type_and_category_are_rejected_with_error_code(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    _, import_id = _seed_import(factory, ids)

    bad_subject = _create(editor, ids.project_a, "DATASET_VERSION", import_id)
    assert bad_subject.status_code == 422
    assert bad_subject.json()["error"]["code"] == "validation_failed"

    bad_category = _create(
        editor,
        ids.project_a,
        "DATASOURCE_IMPORT",
        import_id,
        categories=["NOT_A_CATEGORY"],
    )
    assert bad_category.status_code == 422
    assert bad_category.json()["error"]["code"] == "validation_failed"

    empty_categories = _create(
        editor,
        ids.project_a,
        "DATASOURCE_IMPORT",
        import_id,
        categories=[],
    )
    assert empty_categories.status_code == 422
    assert empty_categories.json()["error"]["code"] == "validation_failed"

    overall_only = _create(
        editor,
        ids.project_a,
        "DATASOURCE_IMPORT",
        import_id,
        categories=["OVERALL"],
    )
    assert overall_only.status_code == 422
    assert overall_only.json()["error"]["code"] == "validation_failed"


def test_project_isolation_and_role_permissions(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    viewer = make_client(factory, ids.viewer_a)
    outsider = make_client(factory, ids.outsider)
    owner = make_client(factory, ids.owner_a)
    _, import_id = _seed_import(factory, ids)
    run = _create(editor, ids.project_a, "DATASOURCE_IMPORT", import_id).json()

    forbidden = _create(viewer, ids.project_a, "DATASOURCE_IMPORT", import_id)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"

    hidden = _create(outsider, ids.project_a, "DATASOURCE_IMPORT", import_id)
    assert hidden.status_code == 403

    # owner 同时在项目 A/B；用 B 的路径检查 A 的导入必须 404，不能泄漏存在性。
    cross = _create(owner, ids.project_b, "DATASOURCE_IMPORT", import_id)
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "resource_not_found"

    cross_read = owner.get(url(ids.project_b, f"/quality-runs/{run['run_id']}"))
    assert cross_read.status_code == 404
    assert cross_read.json()["error"]["code"] == "resource_not_found"

    hidden_read = outsider.get(url(ids.project_a, f"/quality-runs/{run['run_id']}"))
    assert hidden_read.status_code == 403


def test_audit_and_outbox_are_written_in_same_transaction(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    _, import_id = _seed_import(factory, ids)
    run = _create(editor, ids.project_a, "DATASOURCE_IMPORT", import_id).json()

    with factory() as session:
        audit = session.query(AuditLog).filter(AuditLog.resource_id == UUID(run["run_id"])).one()
        event = (
            session.query(OutboxEvent).filter(OutboxEvent.aggregate_id == UUID(run["run_id"])).one()
        )
    assert audit.resource_type == "QUALITY_RUN"
    assert audit.action == "run"
    assert audit.details["rule_set_version"] == run["rule_set_version"]
    assert event.event_type == "quality.run.completed"
    assert event.payload["status"] == run["status"]
    assert event.dispatch is False


def test_unknown_subject_id_returns_resource_not_found(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    response = _create(editor, ids.project_a, "EPISODE", uuid4())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


def test_episode_without_any_evaluable_fact_is_incomplete_not_passed(api_db) -> None:
    """没有任何可判定事实时必须是 INCOMPLETE：把"没检查"说成"通过"会放行脏数据。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    # 用既有标注夹具直接种一个 ACCEPTED Episode：没有媒体资产，也没有标注提交。
    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "EP-quality-empty")

    response = _create(editor, ids.project_a, "EPISODE", episode_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "INCOMPLETE"
    assert body["score"] is None
    assert body["finding_count"] > 0
    assert body["error_count"] == 0
    categories = _by_category(body)
    assert categories["INTEGRITY"]["status"] == "NOT_APPLICABLE"
    assert categories["ANNOTATION"]["status"] == "NOT_APPLICABLE"
    assert categories["OVERALL"]["status"] == "NOT_APPLICABLE"
    assert categories["OVERALL"]["score"] is None
