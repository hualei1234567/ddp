"""语义近似去重仓储测试（SQLite 兜底路径，真实 SQL + 真实约束）。

覆盖：后端能力探测（SQLite 恒 TRIGRAM 可用 / VECTOR 不可用）、
``prepare_candidates`` 的有界拉取（禁止全表）、空指令过滤、项目隔离、
空指纹按需现算（读路径不写库），以及非法窗口的显式失败。
PostgreSQL 的 ``similarity()`` 索引路径由迁移测试的 PG 分支与领域测试覆盖。
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
import sqlalchemy as sa
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.semantic_dedup import (
    MAX_CANDIDATE_LIMIT,
    text_digest,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.semantic_dedup import SqlSemanticDedupRepository
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import seed_episode
from tests.api.conftest import ApiIds, seed_identity

_BASE = "pick up the red cup from the kitchen table"


@pytest.fixture()
def dedup_db() -> Iterator[tuple[sessionmaker[Session], ApiIds]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    try:
        yield factory, seed_identity(factory)
    finally:
        engine.dispose()


def _seed(
    factory: sessionmaker[Session],
    project_id: UUID,
    actor_id: UUID,
    code: str,
    instruction: str | None,
) -> UUID:
    """插入时就带指令：Episode READY 后 ``language_instruction`` 冻结。"""
    return seed_episode(factory, project_id, actor_id, code, language_instruction=instruction)


def _scope(ids: ApiIds) -> ProjectScope:
    return ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)


def test_backend_flags_on_sqlite(
    dedup_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = dedup_db
    with factory() as session:
        flags = SqlSemanticDedupRepository(session).backend_flags()
    assert flags.engine == "sqlite"
    assert flags.has_pg_trgm is True  # Python 兜底恒可用
    assert flags.has_vector_extension is False


def test_prepare_candidates_is_bounded(
    dedup_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """有界拉取：limit 就是硬上限，不允许"拉全表再截断"。"""
    factory, ids = dedup_db
    for index in range(5):
        _seed(factory, ids.project_a, ids.editor_a, f"EP-{index}", f"{_BASE} #{index}")
    with factory() as session:
        candidates = SqlSemanticDedupRepository(session).prepare_candidates(_scope(ids), limit=2)
    assert len(candidates) == 2


def test_prepare_candidates_skips_missing_and_blank_instruction(
    dedup_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = dedup_db
    _seed(factory, ids.project_a, ids.editor_a, "EP-NULL", None)
    _seed(factory, ids.project_a, ids.editor_a, "EP-BLANK", "   ")
    _seed(factory, ids.project_a, ids.editor_a, "EP-OK", _BASE)
    with factory() as session:
        candidates = SqlSemanticDedupRepository(session).prepare_candidates(_scope(ids))
    assert [item.text for item in candidates] == [_BASE]


def test_prepare_candidates_is_project_isolated(
    dedup_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = dedup_db
    _seed(factory, ids.project_b, ids.owner_a, "EP-B", _BASE)
    with factory() as session:
        candidates = SqlSemanticDedupRepository(session).prepare_candidates(_scope(ids))
    assert candidates == ()


def test_prepare_candidates_computes_missing_digest_without_writing(
    dedup_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """存量行 text_digest 为 NULL 时读路径现算，且不回写（只读能力）。"""
    factory, ids = dedup_db
    episode_id = _seed(factory, ids.project_a, ids.editor_a, "EP-NULL-DIGEST", _BASE)
    with factory() as session:
        candidates = SqlSemanticDedupRepository(session).prepare_candidates(_scope(ids))
    assert candidates[0].text_digest == text_digest(_BASE)
    assert candidates[0].episode_id == episode_id
    with factory() as session:
        stored = session.execute(
            sa.select(EpisodeModel.text_digest).where(EpisodeModel.id == episode_id)
        ).scalar_one()
    assert stored is None


def test_prepare_candidates_prefers_stored_digest(
    dedup_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = dedup_db
    episode_id = _seed(factory, ids.project_a, ids.editor_a, "EP-STORED", _BASE)
    stored = text_digest(_BASE)
    with factory() as session:
        session.execute(
            sa.update(EpisodeModel).where(EpisodeModel.id == episode_id).values(text_digest=stored)
        )
        session.commit()
    with factory() as session:
        candidates = SqlSemanticDedupRepository(session).prepare_candidates(_scope(ids))
    assert candidates[0].text_digest == stored


def test_search_sqlite_returns_bounded_candidates_and_scanned(
    dedup_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = dedup_db
    for index in range(3):
        _seed(factory, ids.project_a, ids.editor_a, f"EP-S-{index}", f"{_BASE} {index}")
    with factory() as session:
        candidates, scanned = SqlSemanticDedupRepository(session).search(
            _scope(ids), _BASE, threshold=0.85, limit=2
        )
    assert len(candidates) == 2
    assert scanned == 2


@pytest.mark.parametrize("limit", [0, -1, MAX_CANDIDATE_LIMIT + 1])
def test_prepare_candidates_rejects_invalid_limit(
    dedup_db: tuple[sessionmaker[Session], ApiIds],
    limit: int,
) -> None:
    factory, ids = dedup_db
    with factory() as session:
        with pytest.raises(DomainValidationError):
            SqlSemanticDedupRepository(session).prepare_candidates(_scope(ids), limit=limit)
