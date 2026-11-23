"""Schema 预设目录 API（N6-001）：只读、权限、模板复用与非法输入。

覆盖任务验收项：预设目录读取、创建时引用预设、覆盖字段、非法模板拒绝、
以及目录不随项目变化（静态只读，不泄漏项目数据）。
"""

from __future__ import annotations

import copy

from tests.api.annotation_support import url
from tests.api.conftest import make_client

EXPECTED_KEYS = {
    "action",
    "segment",
    "object",
    "attributes",
    "quality",
    "scene",
    "semantic-hierarchy",
    "robot-state",
}


def _create_payload(preset: dict[str, object], code: str) -> dict[str, object]:
    return {
        "code": code,
        "name": str(preset["name"]),
        "annotation_levels": preset["annotation_levels"],
        "labels": preset["labels"],
    }


def test_list_and_detail_return_eight_readonly_templates(api_db) -> None:
    factory, ids = api_db
    viewer = make_client(factory, ids.viewer_a)

    listed = viewer.get(url(ids.project_a, "/annotation-schema-presets"))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert set(body) == {"catalog_version", "items"}
    assert body["catalog_version"].startswith("sha256:")
    assert len(body["items"]) == 8
    assert {item["preset_key"] for item in body["items"]} == EXPECTED_KEYS

    for item in body["items"]:
        assert item["version"] == "1.0.0"
        assert item["annotation_levels"]
        assert item["labels"]
        levels = set(item["annotation_levels"])
        assert all(isinstance(label["required"], bool) for label in item["labels"])
        assert all(label["level"] in levels for label in item["labels"])

    detail = viewer.get(url(ids.project_a, "/annotation-schema-presets/action"))
    assert detail.status_code == 200, detail.text
    assert detail.json() == next(item for item in body["items"] if item["preset_key"] == "action")

    # 只读：目录端点不提供任何写方法。
    assert viewer.post(url(ids.project_a, "/annotation-schema-presets"), json={}).status_code == 405


def test_catalog_is_static_across_projects(api_db) -> None:
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)

    project_a = owner.get(url(ids.project_a, "/annotation-schema-presets"))
    project_b = owner.get(url(ids.project_b, "/annotation-schema-presets"))
    assert project_a.status_code == 200, project_a.text
    assert project_b.status_code == 200, project_b.text
    # 目录是代码资产，不携带项目数据；两个项目看到同一份目录。
    assert project_a.json() == project_b.json()


def test_outsider_is_forbidden(api_db) -> None:
    factory, ids = api_db
    outsider = make_client(factory, ids.outsider)

    denied = outsider.get(url(ids.project_a, "/annotation-schema-presets"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"


def test_unknown_preset_returns_not_found(api_db) -> None:
    factory, ids = api_db
    viewer = make_client(factory, ids.viewer_a)

    for key in ("not-a-preset", "act"):
        response = viewer.get(url(ids.project_a, f"/annotation-schema-presets/{key}"))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "resource_not_found"


def test_create_schema_from_preset_then_publish(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)

    preset = editor.get(url(ids.project_a, "/annotation-schema-presets/segment"))
    assert preset.status_code == 200, preset.text
    payload = _create_payload(preset.json(), "preset-segment")

    created = editor.post(url(ids.project_a, "/annotation-schemas"), json=payload)
    assert created.status_code == 201, created.text
    schema = created.json()
    assert schema["status"] == "DRAFT"
    assert schema["annotation_levels"] == payload["annotation_levels"]
    assert schema["labels"] == payload["labels"]

    submitted = editor.post(
        url(ids.project_a, f"/annotation-schemas/{schema['schema_id']}/actions/submit-review"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    published = owner.post(
        url(ids.project_a, f"/annotation-schemas/{schema['schema_id']}/actions/publish"),
        headers={"If-Match": '"2"'},
        json={},
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "PUBLISHED"


def test_project_can_override_preset_fields_without_mutating_catalog(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    preset = editor.get(url(ids.project_a, "/annotation-schema-presets/action")).json()
    labels = copy.deepcopy(preset["labels"])
    labels[0]["required"] = False
    labels[0]["description"] = "项目覆盖后的动作类别说明"
    labels.append(
        {
            "label_key": "project_note",
            "label_type": "FREE_TEXT",
            "required": False,
            "level": "EPISODE",
            "visibility": "ANNOTATOR",
            "value_spec": {},
            "quality_rules": [],
        }
    )
    payload = {
        "code": "preset-action-overridden",
        "name": "项目自定义动作标注",
        "annotation_levels": preset["annotation_levels"],
        "labels": labels,
    }

    created = editor.post(url(ids.project_a, "/annotation-schemas"), json=payload)
    assert created.status_code == 201, created.text
    created_labels = created.json()["labels"]
    by_key = {label["label_key"]: label for label in created_labels}
    assert by_key["action_type"]["required"] is False
    assert by_key["action_type"]["description"] == "项目覆盖后的动作类别说明"
    assert "project_note" in by_key

    # 项目覆盖不能回写静态目录。
    fresh = editor.get(url(ids.project_a, "/annotation-schema-presets/action")).json()
    assert fresh == preset


def test_invalid_preset_derived_payload_is_rejected(api_db) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)

    preset = editor.get(url(ids.project_a, "/annotation-schema-presets/action")).json()
    labels = copy.deepcopy(preset["labels"])
    labels[0]["value_spec"]["enum_values"] = []

    response = editor.post(
        url(ids.project_a, "/annotation-schemas"),
        json={
            "code": "preset-action-invalid",
            "name": "非法预设派生",
            "annotation_levels": preset["annotation_levels"],
            "labels": labels,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
