"""技能标签写入 API 集成测试（V52-033）。

全部用真实 SQLite + 真实身份/成员仓储 + 真实路由（非 mock），钉住 HTTP 层契约：

- **权限矩阵**：owner 改任何人 / 本人改自己 / 他人 403 / viewer（非可写角色）403；
- **成员约束**：非项目成员不得持标签 → 422（应用层先判；DB 复合外键兜底）；
- **规范化**：小写 + 去首尾空白，重复（规范化后）显式 422 不静默去重；
- **幂等**：整体替换同一集合不产生新行（行 id / created_at 不变）、追加已存在
  标签不重复插入；
- **weight**：``(0, 1]`` 校验（复用任务侧 SkillTag 规则），且不落库；
- **单条移除**：返回剩余集合；移除不存在标签返回 404 而非静默成功。
"""

from __future__ import annotations

from uuid import UUID

import pytest
from app.infrastructure.db.models.annotation_work import AnnotatorSkillTag
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client


@pytest.fixture()
def skill_api(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> tuple[sessionmaker[Session], ApiIds]:
    """复用 api_db 身份种子（owner/editor/viewer + 项目外用户）。"""
    return api_db


def _base(project_id: object, annotator_id: object) -> str:
    return f"/api/v1/projects/{project_id}/annotation-annotators/{annotator_id}/skill-tags"


def _client(factory: sessionmaker[Session], user_id: UUID) -> TestClient:
    return make_client(factory, user_id)


def _rows(
    factory: sessionmaker[Session], project_id: UUID, annotator_id: UUID
) -> list[AnnotatorSkillTag]:
    with factory() as session:
        return list(
            session.query(AnnotatorSkillTag)
            .filter(
                AnnotatorSkillTag.project_id == project_id,
                AnnotatorSkillTag.annotator_id == annotator_id,
            )
            .order_by(AnnotatorSkillTag.tag_name)
            .all()
        )


# --- 权限矩阵 --------------------------------------------------------------


def test_owner_can_replace_any_members_tags(
    skill_api: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """owner 可改他人：200 + 规范化落库 + created_by = 操作者。"""
    factory, ids = skill_api
    client = _client(factory, ids.owner_a)
    response = client.put(
        _base(ids.project_a, ids.editor_a),
        json={"tags": [{"tag_name": "  Kitchen ", "category": " vision "}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["annotator_id"] == str(ids.editor_a)
    assert body["tags"] == [{"tag_name": "kitchen", "category": "vision"}]
    rows = _rows(factory, ids.project_a, ids.editor_a)
    assert [row.tag_name for row in rows] == ["kitchen"]
    assert rows[0].created_by == ids.owner_a


def test_member_can_replace_own_tags(
    skill_api: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """本人（editor）可改自己的标签。"""
    factory, ids = skill_api
    client = _client(factory, ids.editor_a)
    response = client.put(
        _base(ids.project_a, ids.editor_a),
        json={"tags": [{"tag_name": "tool-use"}]},
    )
    assert response.status_code == 200, response.text
    assert [tag["tag_name"] for tag in response.json()["tags"]] == ["tool-use"]
    assert [row.tag_name for row in _rows(factory, ids.project_a, ids.editor_a)] == ["tool-use"]


def test_other_member_gets_403_and_rows_unchanged(
    skill_api: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """非 owner 的他人（editor 改 editor/owner）→ 403，且不落任何行。"""
    factory, ids = skill_api
    client = _client(factory, ids.editor_a)
    for target in (ids.owner_a, ids.viewer_a):
        response = client.put(
            _base(ids.project_a, target),
            json={"tags": [{"tag_name": "kitchen"}]},
        )
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "forbidden"
    assert _rows(factory, ids.project_a, ids.owner_a) == []


def test_viewer_self_edit_gets_403(
    skill_api: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """viewer 不是可写角色：即使在写自己的标签，也在 WriteScope 被拒。"""
    factory, ids = skill_api
    client = _client(factory, ids.viewer_a)
    response = client.put(
        _base(ids.project_a, ids.viewer_a),
        json={"tags": [{"tag_name": "kitchen"}]},
    )
    assert response.status_code == 403, response.text
    assert _rows(factory, ids.project_a, ids.viewer_a) == []


def test_non_member_target_gets_422(
    skill_api: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """非项目成员不得持标签：应用层先判 422（不是 403/500）。"""
    factory, ids = skill_api
    client = _client(factory, ids.owner_a)
    response = client.put(
        _base(ids.project_a, ids.outsider),
        json={"tags": [{"tag_name": "kitchen"}]},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"
    assert _rows(factory, ids.project_a, ids.outsider) == []


def test_outsider_actor_cannot_write_foreign_project(
    skill_api: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """项目外用户直接访问 → 403（项目成员资格在依赖层拦截）。"""
    factory, ids = skill_api
    client = _client(factory, ids.outsider)
    response = client.put(
        _base(ids.project_a, ids.editor_a),
        json={"tags": [{"tag_name": "kitchen"}]},
    )
    assert response.status_code == 403, response.text


# --- 校验与规范化 ----------------------------------------------------------


def test_duplicate_after_normalization_returns_422(
    skill_api: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """规范化后重复的标签显式 422，不静默去重（掩盖调用方 bug）。"""
    factory, ids = skill_api
    client = _client(factory, ids.owner_a)
    response = client.put(
        _base(ids.project_a, ids.editor_a),
        json={"tags": [{"tag_name": "Kitchen"}, {"tag_name": " kitchen "}]},
    )
    assert response.status_code == 422, response.text
    assert _rows(factory, ids.project_a, ids.editor_a) == []


@pytest.mark.parametrize("weight", [0, 1.5, -0.2])
def test_weight_out_of_range_returns_422(
    skill_api: tuple[sessionmaker[Session], ApiIds], weight: float
) -> None:
    """weight ∈ (0, 1]：越界请求体校验拒绝（与任务侧 SkillTag 同规则）。"""
    factory, ids = skill_api
    client = _client(factory, ids.owner_a)
    response = client.post(
        _base(ids.project_a, ids.editor_a),
        json={"tag_name": "kitchen", "weight": weight},
    )
    assert response.status_code == 422, response.text
    assert _rows(factory, ids.project_a, ids.editor_a) == []


def test_weight_is_validated_but_not_persisted(
    skill_api: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """合法 weight 通过校验，但事实表无 weight 列：响应与行都不含它。"""
    factory, ids = skill_api
    client = _client(factory, ids.owner_a)
    response = client.post(
        _base(ids.project_a, ids.editor_a),
        json={"tag_name": "kitchen", "weight": 0.5},
    )
    assert response.status_code == 200, response.text
    tag = response.json()["tags"][0]
    assert set(tag) == {"tag_name", "category"}
    assert not hasattr(_rows(factory, ids.project_a, ids.editor_a)[0], "weight")


# --- 幂等 ------------------------------------------------------------------


def test_replace_is_idempotent(
    skill_api: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """同一集合重复 PUT：响应一致，且行 id / created_at 不被重写。"""
    factory, ids = skill_api
    client = _client(factory, ids.owner_a)
    payload = {"tags": [{"tag_name": "kitchen"}, {"tag_name": "vlm"}]}
    first = client.put(_base(ids.project_a, ids.editor_a), json=payload)
    assert first.status_code == 200, first.text
    rows_first = _rows(factory, ids.project_a, ids.editor_a)
    second = client.put(_base(ids.project_a, ids.editor_a), json=payload)
    assert second.status_code == 200, second.text
    rows_second = _rows(factory, ids.project_a, ids.editor_a)
    assert second.json() == first.json()
    assert [(row.id, row.created_at) for row in rows_second] == [
        (row.id, row.created_at) for row in rows_first
    ]


def test_replace_with_empty_list_clears_tags(
    skill_api: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """空数组 = 清空（整体替换的语义闭环，不是"未提供"）。"""
    factory, ids = skill_api
    client = _client(factory, ids.owner_a)
    client.put(
        _base(ids.project_a, ids.editor_a),
        json={"tags": [{"tag_name": "kitchen"}]},
    )
    response = client.put(_base(ids.project_a, ids.editor_a), json={"tags": []})
    assert response.status_code == 200, response.text
    assert response.json()["tags"] == []
    assert _rows(factory, ids.project_a, ids.editor_a) == []


def test_add_single_tag_is_idempotent(
    skill_api: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """同一标签 POST 两次：只有一行，且第二次不重写 created_at。"""
    factory, ids = skill_api
    client = _client(factory, ids.owner_a)
    url = _base(ids.project_a, ids.editor_a)
    first = client.post(url, json={"tag_name": "kitchen"})
    assert first.status_code == 200, first.text
    created_at = _rows(factory, ids.project_a, ids.editor_a)[0].created_at
    second = client.post(url, json={"tag_name": "kitchen"})
    assert second.status_code == 200, second.text
    rows = _rows(factory, ids.project_a, ids.editor_a)
    assert len(rows) == 1
    assert rows[0].created_at == created_at
    assert second.json()["tags"] == [{"tag_name": "kitchen", "category": None}]


def test_remove_single_tag_returns_remaining_set(
    skill_api: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """DELETE 单条：返回剩余集合；再删同一条返回 404（不静默成功）。"""
    factory, ids = skill_api
    client = _client(factory, ids.owner_a)
    client.put(
        _base(ids.project_a, ids.editor_a),
        json={"tags": [{"tag_name": "kitchen"}, {"tag_name": "vlm"}]},
    )
    response = client.delete(f"{_base(ids.project_a, ids.editor_a)}/kitchen")
    assert response.status_code == 200, response.text
    assert [tag["tag_name"] for tag in response.json()["tags"]] == ["vlm"]
    missing = client.delete(f"{_base(ids.project_a, ids.editor_a)}/kitchen")
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "resource_not_found"
