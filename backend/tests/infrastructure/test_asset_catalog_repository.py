"""统一资产目录仓储：跨类型、前缀下推、键集分页与项目隔离。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.application.catalog.cursor import decode_cursor, encode_cursor
from app.application.catalog.models import CatalogCursor
from app.domain.catalog.enums import AssetKind
from app.domain.catalog.errors import DomainValidationError
from app.domain.catalog.search import SearchPrefix
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories import asset_catalog as asset_catalog_module
from app.infrastructure.repositories.asset_catalog import SqlAssetCatalogRepository
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import Session, sessionmaker

from tests.asset_catalog_support import seed_asset_catalog, seed_identity


@pytest.fixture()
def catalog_db() -> Iterator[tuple[sessionmaker[Session], dict[str, UUID], UUID, UUID]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    project_a, project_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    seed_identity(
        factory,
        project_a=project_a,
        project_b=project_b,
        users=(user_a, user_b),
    )
    seed_asset_catalog(factory, project_a, user_a, handle="a")
    seed_asset_catalog(factory, project_b, user_b, handle="b")
    try:
        yield factory, {"a": project_a, "b": project_b}, user_a, user_b
    finally:
        engine.dispose()


def _scope(project_id: UUID, actor_id: UUID) -> ProjectScope:
    return ProjectScope(project_id, actor_id=actor_id)


def _search(
    factory: sessionmaker[Session],
    scope: ProjectScope,
    q: str,
    *,
    kinds: frozenset[AssetKind] | None = None,
    after: CatalogCursor | None = None,
    limit: int = 20,
):
    with factory() as session:
        return list(
            SqlAssetCatalogRepository(session).search(
                scope,
                prefix=SearchPrefix.parse(q),
                kinds=kinds,
                after=after,
                limit=limit,
            )
        )


def test_prefix_search_spans_kinds_and_orders_by_updated_at_then_kind(catalog_db) -> None:
    factory, projects, user_a, _ = catalog_db
    scope = _scope(projects["a"], user_a)

    raw = _search(factory, scope, "raw-")
    assert [entry.kind for entry in raw] == [AssetKind.RAW_ASSET, AssetKind.RAW_ASSET]
    # updated_at DESC：raw-beta 比 raw-alpha 新；展示名取原始文件名。
    assert [entry.display_name for entry in raw] == ["beta.mcap", "alpha.mcap"]

    episodes = _search(factory, scope, "ep-")
    # 同一时间戳按 kind ASC：EPISODE 在 MEDIA_ASSET 前。
    assert [entry.kind for entry in episodes] == [
        AssetKind.EPISODE,
        AssetKind.MEDIA_ASSET,
        AssetKind.EPISODE,
        AssetKind.MEDIA_ASSET,
    ]
    assert [entry.display_name for entry in episodes] == [
        "ep-beta",
        "media/a/ep-beta/cam0.mp4",
        "ep-alpha",
        "media/a/ep-alpha/cam0.mp4",
    ]

    datasets = _search(factory, scope, "ds-alpha")
    assert [entry.kind for entry in datasets] == [
        AssetKind.DATASET_VERSION,
        AssetKind.DATASET,
    ]
    assert [entry.display_name for entry in datasets] == ["ds-alpha v1", "数据集 Alpha"]

    assert _search(factory, scope, "no-such-prefix") == []


def test_prefix_matching_is_anchored_and_escapes_wildcards(catalog_db) -> None:
    factory, projects, user_a, _ = catalog_db
    scope = _scope(projects["a"], user_a)

    # 只做前缀匹配：片段的中间/末尾命中不算；LIKE 通配符按字面量处理。
    assert _search(factory, scope, "alpha") == []
    assert _search(factory, scope, "raw%") == []
    assert _search(factory, scope, "raw_") == []
    assert len(_search(factory, scope, "raw-")) == 2


def test_project_isolation_never_leaks_other_project(catalog_db) -> None:
    factory, projects, user_a, user_b = catalog_db
    scope_a = _scope(projects["a"], user_a)
    scope_b = _scope(projects["b"], user_b)

    a_ids = {entry.asset_id for entry in _search(factory, scope_a, "ep-")}
    b_ids = {entry.asset_id for entry in _search(factory, scope_b, "ep-")}
    assert len(a_ids) == 4
    assert len(b_ids) == 4
    assert a_ids.isdisjoint(b_ids)

    # 两个项目都写了同名 code；结果里不得出现对方项目的主键。
    assert {entry.display_name for entry in _search(factory, scope_a, "raw-alpha")} == {
        "alpha.mcap"
    }


def test_kind_filter_is_applied_in_sql(catalog_db) -> None:
    factory, projects, user_a, _ = catalog_db
    scope = _scope(projects["a"], user_a)

    only_episodes = _search(factory, scope, "ep-", kinds=frozenset({AssetKind.EPISODE}))
    assert {entry.kind for entry in only_episodes} == {AssetKind.EPISODE}
    assert len(only_episodes) == 2

    only_media = _search(factory, scope, "ep-", kinds=frozenset({AssetKind.MEDIA_ASSET}))
    assert {entry.kind for entry in only_media} == {AssetKind.MEDIA_ASSET}
    assert len(only_media) == 2

    assert _search(factory, scope, "ep-", kinds=frozenset()) == []


def test_keyset_pagination_walks_all_rows_without_duplicates(catalog_db) -> None:
    factory, projects, user_a, _ = catalog_db
    scope = _scope(projects["a"], user_a)

    collected = []
    after = None
    for _ in range(10):
        page = _search(factory, scope, "ep-", after=after, limit=1)
        if not page:
            break
        collected.extend(page)
        after = CatalogCursor(
            updated_at=page[-1].updated_at,
            kind=page[-1].kind,
            asset_id=page[-1].asset_id,
        )

    assert [entry.display_name for entry in collected] == [
        "ep-beta",
        "media/a/ep-beta/cam0.mp4",
        "ep-alpha",
        "media/a/ep-alpha/cam0.mp4",
    ]
    assert len({entry.asset_id for entry in collected}) == 4


def test_cursor_codec_roundtrip_and_rejects_garbage() -> None:
    cursor = CatalogCursor(
        updated_at=datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
        kind=AssetKind.EPISODE,
        asset_id=uuid4(),
    )
    assert decode_cursor(encode_cursor(cursor)) == cursor

    for invalid in ("not-base64!!", "e30", "eyJ0Ijoibm90LWEtZGF0ZSJ9"):
        with pytest.raises(DomainValidationError):
            decode_cursor(invalid)


def test_prefix_and_project_conditions_are_pushed_into_sql() -> None:
    project_id = uuid4()
    statement = asset_catalog_module._episode_branch(
        project_id,
        SearchPrefix.parse("ep-"),
        None,
        11,
    )
    compiled = str(
        statement.compile(
            dialect=sqlite.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    # 项目隔离、前缀区间与锚定 LIKE 必须同时出现在 SQL 里，而不是内存过滤。
    assert f"episodes.project_id = '{project_id.hex}'" in compiled
    assert "episodes.code >= 'ep-'" in compiled
    assert "episodes.code < 'ep.'" in compiled
    assert "LIKE 'ep-' || '%'" in compiled
    assert "LIMIT 11" in compiled


def test_limit_out_of_bounds_is_rejected(catalog_db) -> None:
    factory, projects, user_a, _ = catalog_db
    with pytest.raises(DomainValidationError):
        _search(factory, _scope(projects["a"], user_a), "ep-", limit=0)
    with pytest.raises(DomainValidationError):
        _search(factory, _scope(projects["a"], user_a), "ep-", limit=102)
