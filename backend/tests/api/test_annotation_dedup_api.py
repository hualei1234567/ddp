"""语义近似去重 API 测试（V52-031，蓝图 G6 / N1 训练集防污染）。

覆盖：能力状态响应（TRIGRAM 可用 / VECTOR 不可用且有原因）、只读权限
（viewer 可读可查）、越权与项目隔离、近似命中与阈值边界、非法入参 422、
"查询不产生任何写入"。
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from app.infrastructure.db.models.episodes import EpisodeModel
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import seed_episode
from tests.api.conftest import ApiIds, make_client

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_BASE = "pick up the red cup from the kitchen table"
#: 与 _BASE 的 trigram 相似度约 0.86：默认阈值 0.85 命中、阈值 0.9 不命中。
_NEAR = "pick up the red cup from the kitchen table please"
_FAR = "navigate to the charging station and dock"


def _url(project_id: UUID) -> str:
    return f"/api/v1/projects/{project_id}/annotation-dedup"


def _seed_text(
    factory: sessionmaker[Session],
    project_id: UUID,
    actor_id: UUID,
    code: str,
    instruction: str | None,
) -> UUID:
    """复用标注测试夹具的 Episode 造数，并在插入时带上语言指令。

    **不能后置 UPDATE**：``language_instruction`` 在 Episode 进入 READY 后属于
    冻结字段（``EPISODE_DATA_FROZEN_FIELDS``，触发器 ``episode_data_immutable``）。
    """
    return seed_episode(factory, project_id, actor_id, code, language_instruction=instruction)


def _client(factory: sessionmaker[Session], user_id: UUID) -> TestClient:
    return make_client(factory, user_id)


def test_status_reports_trigram_available_and_vector_unavailable(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    response = _client(factory, ids.editor_a).get(_url(ids.project_a))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["engine"] == "sqlite"
    assert body["backend"] == "TRIGRAM"
    assert body["threshold"] == pytest.approx(0.85)
    assert body["candidate_limit"] == 200

    backends = {item["backend"]: item for item in body["backends"]}
    assert backends["TRIGRAM"]["available"] is True
    assert backends["TRIGRAM"]["reason"] is None
    assert backends["VECTOR"]["available"] is False
    # 不假装向量能力：原因必须点名 pgvector 缺失（SQLite 无该扩展）。
    assert "pgvector" in backends["VECTOR"]["reason"]
    assert "扩展" in backends["VECTOR"]["reason"]


def test_outsider_and_unknown_project_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    outsider = _client(factory, ids.outsider)
    assert outsider.get(_url(ids.project_a)).status_code in {403, 404}
    assert outsider.post(_url(ids.project_a), json={"text": _BASE}).status_code in {403, 404}
    # 不存在的项目与"无权访问"对外不可区分（403/404 均可），不得泄露项目存在性。
    assert _client(factory, ids.editor_a).get(_url(uuid4())).status_code in {403, 404}


def test_viewer_can_read_status_and_query(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """只读能力：viewer 有 READ 权限即可查，不需要 WRITE。"""
    factory, ids = api_db
    viewer = _client(factory, ids.viewer_a)
    assert viewer.get(_url(ids.project_a)).status_code == 200
    response = viewer.post(_url(ids.project_a), json={"text": _BASE})
    assert response.status_code == 200, response.text


def test_query_identical_text_is_full_duplicate(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episode_id = _seed_text(factory, ids.project_a, ids.editor_a, "EP-D1", _BASE)
    response = _client(factory, ids.editor_a).post(_url(ids.project_a), json={"text": _BASE})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["backend"] == "TRIGRAM"
    assert body["candidate_count"] == 1
    assert body["scanned"] == 1
    digest = body["text_digest"]
    assert _DIGEST_RE.match(digest)
    candidate = body["candidates"][0]
    assert candidate["score"] == 1.0
    assert candidate["text"] == _BASE
    assert candidate["text_digest"] == digest
    assert candidate["episode_id"] == str(episode_id)


def test_query_filters_below_threshold(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """默认阈值 0.85：近似句命中、无关句不返回。"""
    factory, ids = api_db
    _seed_text(factory, ids.project_a, ids.editor_a, "EP-NEAR", _NEAR)
    _seed_text(factory, ids.project_a, ids.editor_a, "EP-FAR", _FAR)
    body = _client(factory, ids.editor_a).post(_url(ids.project_a), json={"text": _BASE}).json()
    assert body["candidate_count"] == 1
    assert body["candidates"][0]["text"] == _NEAR
    assert 0.85 <= body["candidates"][0]["score"] < 1.0


def test_query_threshold_override_excludes_near_duplicate(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _seed_text(factory, ids.project_a, ids.editor_a, "EP-NEAR", _NEAR)
    _seed_text(factory, ids.project_a, ids.editor_a, "EP-EXACT", _BASE)
    body = (
        _client(factory, ids.editor_a)
        .post(_url(ids.project_a), json={"text": _BASE, "threshold": 0.9})
        .json()
    )
    assert body["threshold"] == pytest.approx(0.9)
    assert [item["text"] for item in body["candidates"]] == [_BASE]


def test_query_respects_project_isolation(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """项目 B 的样本文本不得出现在项目 A 的查询结果里。"""
    factory, ids = api_db
    _seed_text(factory, ids.project_b, ids.owner_a, "EP-B", _BASE)
    response = _client(factory, ids.editor_a).post(_url(ids.project_a), json={"text": _BASE})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scanned"] == 0
    assert body["candidates"] == []


def test_query_empty_corpus_returns_no_candidates(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    body = _client(factory, ids.editor_a).post(_url(ids.project_a), json={"text": _BASE}).json()
    assert (body["scanned"], body["candidate_count"], body["candidates"]) == (0, 0, [])


def test_query_is_read_only(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """查询不写库：Episode 行数与去重候选快照前后一致。"""
    factory, ids = api_db
    _seed_text(factory, ids.project_a, ids.editor_a, "EP-RO", _BASE)
    with factory() as session:
        before = session.execute(sa.select(sa.func.count()).select_from(EpisodeModel)).scalar_one()
    response = _client(factory, ids.editor_a).post(_url(ids.project_a), json={"text": _BASE})
    assert response.status_code == 200, response.text
    with factory() as session:
        after = session.execute(sa.select(sa.func.count()).select_from(EpisodeModel)).scalar_one()
        digests = (
            session.execute(
                sa.select(EpisodeModel.text_digest).where(EpisodeModel.project_id == ids.project_a)
            )
            .scalars()
            .all()
        )
    assert after == before
    # 只读能力不回填 text_digest（写路径属后续接线，不在本端点职责内）。
    assert digests == [None]


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"text": ""}, "空文本"),
        ({"text": "   "}, "纯空白文本"),
        ({"text": "x" * 2001}, "超长文本"),
        ({"text": _BASE, "threshold": 1.5}, "阈值越界"),
        ({"text": _BASE, "threshold": -0.1}, "阈值负数"),
        ({"text": _BASE, "limit": 0}, "窗口为 0"),
        ({"text": _BASE, "limit": 501}, "窗口超上限"),
        ({"threshold": 0.9}, "缺 text"),
    ],
)
def test_query_rejects_invalid_payload(
    api_db: tuple[sessionmaker[Session], ApiIds],
    payload: dict[str, object],
    reason: str,
) -> None:
    factory, ids = api_db
    response = _client(factory, ids.editor_a).post(_url(ids.project_a), json=payload)
    assert response.status_code == 422, f"{reason}: {response.text}"
