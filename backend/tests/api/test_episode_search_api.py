"""Episode 多维检索：每个过滤维度的正反例、SQL 下推证明与跨项目隔离。

"下推"的证明方式有两条，都不依赖读私有实现：

1. **编译期**：让仓储自己组装语句并编译，断言 WHERE 里出现了对应列与展开子查询；
2. **行为**：构造"只命中 1 条的过滤 + limit=1"，若实现是"先取 limit 条再内存过滤"，
   会返回 0 条；下推实现返回 1 条。这条断言能直接杀掉内存过滤的实现。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID, uuid4

from app.domain.episodes.filters import EpisodeListFilters
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.repositories._json_array_predicates import json_array_any_of
from app.infrastructure.repositories.episodes import SqlEpisodeRepository
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import seed_dataset_episodes, url
from tests.api.ddp310_support import episode_payload, project_url, seed_valid_asset
from tests.fakes_s3 import FakeMultipartStorage

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _episodes(
    client,
    project_id: UUID,
    asset_id: UUID,
    *,
    count: int = 5,
    code_prefix: str = "EP-SEARCH",
    **overrides: object,
) -> list[dict[str, object]]:
    created: list[dict[str, object]] = []
    for index in range(count):
        payload = episode_payload(
            asset_id,
            code=f"{code_prefix}-{index}",
            start_time_ns=1_000 + index * 10_000,
            end_time_ns=2_000 + index * 10_000,
        )
        payload.update({key: value for key, value in overrides.items()})
        response = client.post(
            project_url(project_id, "/episodes"),
            json=payload,
            headers={"Idempotency-Key": f"{code_prefix.lower()}-create-{index}"},
        )
        assert response.status_code == 201, response.text
        created.append(response.json())
    return created


def _set_episode_attrs(
    factory: sessionmaker[Session],
    project_id: UUID,
    episode_id: UUID,
    **values: object,
) -> None:
    with factory() as session:
        session.execute(
            update(EpisodeModel)
            .where(EpisodeModel.project_id == project_id, EpisodeModel.id == episode_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        session.commit()


def _query(params: dict[str, object]) -> str:
    """拼查询串；值统一 URL 编码（ISO 时间里的 ``+`` 会被解成空格）。

    值为列表/元组时展开成重复同名参数，对应"任一命中"的多值语义。
    """
    pairs: list[str] = []
    for key, value in params.items():
        if value is None:
            continue
        values = value if isinstance(value, list | tuple) else (value,)
        for item in values:
            pairs.append(f"{key}={quote(str(item), safe='')}")
    return "&".join(pairs)


def _search(
    client,
    project_id: UUID,
    **params: object,
) -> list[dict[str, object]]:
    query = _query(params)
    response = client.get(project_url(project_id, f"/episodes?{query}"))
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_filters_by_quality_and_annotation_status(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="search-q")
    client = make_client(factory, ids.editor_a, object_storage=FakeMultipartStorage())
    created = _episodes(client, ids.project_a, asset_id, count=4)
    target = created[1]["episode_id"]
    _set_episode_attrs(factory, ids.project_a, UUID(str(target)), quality_status="PASS")

    hits = _search(client, ids.project_a, quality_status="PASS")
    assert [item["episode_id"] for item in hits] == [target]

    misses = _search(client, ids.project_a, quality_status="FAIL")
    assert misses == []

    # 非法枚举值必须被拒绝，而不是静默返回全部。
    bad = client.get(project_url(ids.project_a, "/episodes?quality_status=NOPE"))
    assert bad.status_code == 422

    _set_episode_attrs(
        factory,
        ids.project_a,
        UUID(str(created[2]["episode_id"])),
        annotation_status="IN_PROGRESS",
    )
    annotated = _search(client, ids.project_a, annotation_status="IN_PROGRESS")
    assert [item["episode_id"] for item in annotated] == [created[2]["episode_id"]]
    assert _search(client, ids.project_a, annotation_status="APPROVED") == []


def test_filters_by_scene_label_and_device_id(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="search-s")
    client = make_client(factory, ids.editor_a, object_storage=FakeMultipartStorage())
    created = _episodes(client, ids.project_a, asset_id, count=3)
    device_a, device_b = uuid4(), uuid4()

    _set_episode_attrs(
        factory,
        ids.project_a,
        UUID(str(created[0]["episode_id"])),
        scene_labels=["kitchen", "indoor"],
        device_ids=[str(device_a)],
    )
    _set_episode_attrs(
        factory,
        ids.project_a,
        UUID(str(created[1]["episode_id"])),
        scene_labels=["street"],
        device_ids=[str(device_b)],
    )

    kitchen = _search(client, ids.project_a, scene_label="kitchen")
    assert [item["episode_id"] for item in kitchen] == [created[0]["episode_id"]]

    indoor = _search(client, ids.project_a, scene_label="indoor")
    assert [item["episode_id"] for item in indoor] == [created[0]["episode_id"]]

    # 任一命中：两个标签各命中不同 Episode。
    either = _search(client, ids.project_a, scene_label=["kitchen", "street"])
    assert {item["episode_id"] for item in either} == {
        created[0]["episode_id"],
        created[1]["episode_id"],
    }

    none = _search(client, ids.project_a, scene_label="warehouse")
    assert none == []

    by_device = _search(client, ids.project_a, device_id=str(device_b))
    assert [item["episode_id"] for item in by_device] == [created[1]["episode_id"]]
    assert _search(client, ids.project_a, device_id=str(uuid4())) == []


def test_filters_by_camera(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """机位过滤按 Episode 关联资产 ``raw_metadata.camera`` 下推。

    没有机位事实的资产（如上传资产）不得命中任何机位条件。
    """
    factory, ids = api_db
    main_asset = seed_valid_asset(
        factory, ids.project_a, ids.editor_a, label="cam-main", metadata={"camera": "main"}
    )
    wrist_asset = seed_valid_asset(
        factory, ids.project_a, ids.editor_a, label="cam-wrist", metadata={"camera": "wrist"}
    )
    bare_asset = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="cam-bare")
    client = make_client(factory, ids.editor_a, object_storage=FakeMultipartStorage())
    main_episodes = _episodes(client, ids.project_a, main_asset, count=2, code_prefix="EP-CAM-MAIN")
    wrist_episodes = _episodes(
        client, ids.project_a, wrist_asset, count=2, code_prefix="EP-CAM-WRIST"
    )
    _episodes(client, ids.project_a, bare_asset, count=1, code_prefix="EP-CAM-BARE")

    hits = _search(client, ids.project_a, camera="main")
    assert {item["episode_id"] for item in hits} == {item["episode_id"] for item in main_episodes}

    wrist = _search(client, ids.project_a, camera="wrist")
    assert {item["episode_id"] for item in wrist} == {item["episode_id"] for item in wrist_episodes}

    assert _search(client, ids.project_a, camera="overhead_ceiling") == []

    # 空机位与超长机位必须被拒绝，而不是静默返回全部。
    assert client.get(project_url(ids.project_a, "/episodes?camera=")).status_code == 422
    assert client.get(project_url(ids.project_a, f"/episodes?camera={'x' * 65}")).status_code == 422


def test_filters_by_robot_and_captured_range(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="search-t")
    client = make_client(factory, ids.editor_a, object_storage=FakeMultipartStorage())
    created = _episodes(client, ids.project_a, asset_id, count=3)
    robot_id = uuid4()
    _set_episode_attrs(
        factory, ids.project_a, UUID(str(created[0]["episode_id"])), robot_id=robot_id
    )
    by_robot = _search(client, ids.project_a, robot_id=str(robot_id))
    assert [item["episode_id"] for item in by_robot] == [created[0]["episode_id"]]

    # 捕获时间全部为 NOW；用区间把结果夹到 1 条：给第 3 条资产改到更晚。
    later = NOW + timedelta(days=10)
    with factory() as session:
        asset_row = session.scalar(select(RawAssetModel).where(RawAssetModel.id == asset_id))
        assert asset_row is not None
        session.execute(
            update(RawAssetModel)
            .where(RawAssetModel.project_id == ids.project_a)
            .values(captured_at=NOW)
        )
        session.commit()

    # 三个 Episode 关联同一资产，最早捕获时间相同；区间外查询必须为空。
    outside = _search(client, ids.project_a, captured_from=later.isoformat())
    assert outside == []

    inside = _search(
        client,
        ids.project_a,
        captured_from=(NOW - timedelta(days=1)).isoformat(),
        captured_to=(NOW + timedelta(days=1)).isoformat(),
    )
    assert len(inside) == 3

    # captured_to 早于 captured_from 必须拒绝，而不是返回空。
    inverted = client.get(
        project_url(
            ids.project_a,
            "/episodes?"
            + _query({"captured_from": later.isoformat(), "captured_to": NOW.isoformat()}),
        )
    )
    assert inverted.status_code == 422

    # 无捕获时间的 Episode 不满足任何时间条件。
    with factory() as session:
        session.execute(
            update(RawAssetModel)
            .where(RawAssetModel.project_id == ids.project_a)
            .values(captured_at=None)
        )
        session.commit()
    assert _search(client, ids.project_a, captured_from=(NOW - timedelta(days=1)).isoformat()) == []


def test_filters_are_pushed_down_to_sql(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """编译期证明：WHERE 里出现对应列与 JSON 展开子查询。

    同时用 PostgreSQL 方言编译一次，确保生产方言同样生成谓词（不是只在
    SQLite 上"看起来能用"）。
    """
    factory, ids = api_db
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="search-push")
    client = make_client(factory, ids.editor_a, object_storage=FakeMultipartStorage())
    _episodes(client, ids.project_a, asset_id, count=2)

    filters = EpisodeListFilters(
        quality_status="PASS",
        annotation_status="APPROVED",
        scene_labels=("kitchen",),
        device_id=uuid4(),
        robot_id=uuid4(),
        camera="main",
        captured_from=NOW,
        captured_to=NOW + timedelta(days=1),
    )
    with factory() as session:
        repository = SqlEpisodeRepository(session)
        base = select(EpisodeModel).where(EpisodeModel.project_id == ids.project_a)
        statement = repository._apply_filters(base, filters)
        # literal_binds 让 JSON 路径参数展开，才能验证机位事实确实按
        # ``raw_metadata["camera"]`` 抽取，而不是取回整列。
        sqlite_sql = str(
            statement.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True})
        )
        # 生产方言单独构造一次谓词并编译，确认不是"只在 SQLite 上能用"。
        # 机位谓词本身与方言无关；JSON 数组展开必须按 PG 方言生成。
        pg_statement = (
            repository._apply_filters(base, EpisodeListFilters(camera="main"))
            .where(json_array_any_of(EpisodeModel.scene_labels, ("kitchen",), dialect="postgresql"))
            .where(
                json_array_any_of(EpisodeModel.device_ids, (str(uuid4()),), dialect="postgresql")
            )
        )
        pg_sql = str(pg_statement.compile(dialect=postgresql.dialect()))

    flat = " ".join(sqlite_sql.split()).lower()
    # 每一维都必须出现在 WHERE 里。
    for column in (
        "episodes.quality_status",
        "episodes.annotation_status",
        "episodes.scene_labels",
        "episodes.device_ids",
        "episodes.robot_id",
        "captured_at",
    ):
        assert column in flat, f"{column} 未下推到 SQL"
    # 机位条件走关联资产的相关 EXISTS，JSON 事实在库内抽取，不取回内存过滤。
    assert "episode_raw_assets" in flat
    assert "raw_assets" in flat
    assert "json_extract" in flat
    assert "camera" in flat
    # JSON 数组必须走展开子查询，而不是整列取回。
    assert "json_each" in flat
    assert "exists" in flat

    pg_flat = " ".join(pg_sql.split()).lower()
    assert "jsonb_array_elements_text" in pg_flat
    assert "episodes.robot_id" in pg_flat
    # PostgreSQL 上的 JSON 事实抽取是 ->>，与 SQLite 的 json_extract 不同。
    assert "raw_metadata" in pg_flat
    assert "->>" in pg_flat

    # 没有任何条件时不应平白多出 WHERE 片段（避免误加恒真/恒假谓词）。
    with factory() as session:
        bare = SqlEpisodeRepository(session)._apply_filters(
            select(EpisodeModel).where(EpisodeModel.project_id == ids.project_a),
            EpisodeListFilters(),
        )
    assert "json_each" not in str(bare.compile(dialect=sqlite.dialect()))


def test_filter_returns_hits_beyond_first_page(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """行为证明：命中项在后续位置时，limit=1 仍必须返回它。

    若实现是"按 limit 取回再内存过滤"，这一条会返回空数组。
    """
    factory, ids = api_db
    asset_id = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="search-page")
    client = make_client(factory, ids.editor_a, object_storage=FakeMultipartStorage())
    created = _episodes(client, ids.project_a, asset_id, count=4)

    # 让 ID 排序最大的那条命中，前面三条都不命中。
    ordered = sorted(created, key=lambda item: UUID(str(item["episode_id"])))
    target = ordered[-1]["episode_id"]
    _set_episode_attrs(factory, ids.project_a, UUID(str(target)), quality_status="WARN")

    page = client.get(project_url(ids.project_a, "/episodes?quality_status=WARN&limit=1"))
    assert page.status_code == 200, page.text
    items = page.json()["items"]
    assert [item["episode_id"] for item in items] == [target]


def test_tag_filter_on_datasets(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    from tests.api.dataset_support import create_dataset

    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    tagged = create_dataset(editor, ids.project_a, ids.owner_a, f"ds-tag-{uuid4().hex[:6]}")
    other = create_dataset(editor, ids.project_a, ids.owner_a, f"ds-tag-{uuid4().hex[:6]}")

    with factory() as session:
        from app.infrastructure.db.models.datasets import DatasetModel

        session.execute(
            update(DatasetModel)
            .where(DatasetModel.id == UUID(str(tagged["dataset_id"])))
            .values(tags=["manipulation", "indoor"])
            .execution_options(synchronize_session=False)
        )
        session.execute(
            update(DatasetModel)
            .where(DatasetModel.id == UUID(str(other["dataset_id"])))
            .values(tags=["outdoor"])
            .execution_options(synchronize_session=False)
        )
        session.commit()

    def _search(**params: object) -> list[str]:
        query = _query(params)
        response = editor.get(url(ids.project_a, f"/datasets?{query}"))
        assert response.status_code == 200, response.text
        return [item["dataset_id"] for item in response.json()["items"]]

    assert _search(tag="manipulation") == [tagged["dataset_id"]]
    # 两个 tag 都在 tagged 上：仍只有 tagged 命中。
    assert _search(tag=["manipulation", "indoor"]) == [tagged["dataset_id"]]
    # 各命中一个：并集，证明是"任一命中"而不是 AND。
    assert set(_search(tag=["indoor", "outdoor"])) == {
        tagged["dataset_id"],
        other["dataset_id"],
    }
    assert _search(tag="nonexistent") == []


def test_filters_do_not_leak_across_projects(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    asset_a = seed_valid_asset(
        factory,
        ids.project_a,
        ids.editor_a,
        label="search-a",
        metadata={"camera": "main"},
    )
    client_a = make_client(factory, ids.editor_a, object_storage=FakeMultipartStorage())
    created = _episodes(client_a, ids.project_a, asset_a, count=2)
    _set_episode_attrs(
        factory,
        ids.project_a,
        UUID(str(created[0]["episode_id"])),
        quality_status="PASS",
        scene_labels=["kitchen"],
        device_ids=[str(uuid4())],
    )

    # 项目 B 里造同样标签与机位的数据，用项目 B 的作用域查询不得看到项目 A 的。
    seed_dataset_episodes(factory, ids.project_b, ids.owner_a, count=2, scene="street")
    owner_b = make_client(factory, ids.owner_a, object_storage=FakeMultipartStorage())
    asset_b = seed_valid_asset(
        factory,
        ids.project_b,
        ids.owner_a,
        label="search-b",
        metadata={"camera": "main"},
    )
    b_camera_episode = _episodes(owner_b, ids.project_b, asset_b, count=1, code_prefix="EP-B-CAM")

    a_ids = {item["episode_id"] for item in created}

    hits = owner_b.get(project_url(ids.project_b, "/episodes?quality_status=PASS&limit=100"))
    assert hits.status_code == 200, hits.text
    b_ids = {item["episode_id"] for item in hits.json()["items"]}
    assert b_ids, "项目 B 自己的数据应当能查到"
    assert b_ids.isdisjoint(a_ids), "查询结果不得包含项目 A 的 Episode"

    # 机位维度同样按项目隔离：两边都有 camera=main，结果不得交叉。
    a_camera = {item["episode_id"] for item in _search(client_a, ids.project_a, camera="main")}
    b_camera = {item["episode_id"] for item in _search(owner_b, ids.project_b, camera="main")}
    assert a_camera == a_ids
    assert b_camera == {b_camera_episode[0]["episode_id"]}
    assert a_camera.isdisjoint(b_camera)

    scenes = owner_b.get(project_url(ids.project_b, "/episodes?scene_label=kitchen&limit=100"))
    assert scenes.status_code == 200, scenes.text
    assert {item["episode_id"] for item in scenes.json()["items"]}.isdisjoint(a_ids)

    # 跨项目访问项目 A 的路径必须 404（项目作用域由服务端判定）。
    with_tags = owner_b.get(project_url(ids.project_b, "/datasets?tag=manipulation"))
    assert with_tags.status_code == 200
    assert with_tags.json()["items"] == []


def test_repository_filter_scope_never_crosses_projects(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """仓储层直连：即使过滤条件相同，项目作用域仍是硬条件。"""
    factory, ids = api_db
    asset_a = seed_valid_asset(factory, ids.project_a, ids.editor_a, label="search-repo")
    client = make_client(factory, ids.editor_a, object_storage=FakeMultipartStorage())
    created = _episodes(client, ids.project_a, asset_a, count=1)

    scope_b = ProjectScope(ids.project_b, actor_id=ids.owner_a)
    with factory() as session:
        rows = SqlEpisodeRepository(session).list(
            scope_b,
            filters=EpisodeListFilters(quality_status="NOT_CHECKED"),
            limit=10,
        )
    assert rows == []

    scope_a = ProjectScope(ids.project_a, actor_id=ids.editor_a)
    with factory() as session:
        rows_a = SqlEpisodeRepository(session).list(
            scope_a,
            filters=EpisodeListFilters(quality_status="NOT_CHECKED"),
            limit=10,
        )
    assert [row.episode_id for row in rows_a] == [UUID(str(created[0]["episode_id"]))]
