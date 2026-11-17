"""数据源链投影的仓储/用例测试：方向、项目隔离与来源状态判定。

用真实 SQLite + 真实仓储，不 Mock SQL；数据源、导入任务与媒体资产按
生产表结构写入，这样投影查询（三表 JOIN）本身也被覆盖。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.domain.episodes.provenance import RawAssetSourceStatus
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.datasources import (
    DataSourceImportModel,
    DataSourceModel,
    MediaAssetModel,
)
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.repositories.datasource_provenance import (
    SqlSourceProvenanceRepository,
)
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from tests.infrastructure.episode_support import build_services

_DATASOURCE_NOW = datetime(2026, 11, 8, 12, 30, tzinfo=UTC)
_IMPORT_NOW = datetime(2026, 11, 8, 12, 40, tzinfo=UTC)
_MEDIA_NOW = datetime(2026, 11, 8, 12, 50, tzinfo=UTC)
_REVISION = "b" * 40


def seed_data_source(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    actor_id: UUID,
    code: str = "hf-human1m",
) -> tuple[UUID, UUID]:
    """写入数据源 + 一个 SUCCEEDED 导入任务，返回 (source_id, import_id)。"""
    source_id, import_id = uuid4(), uuid4()
    with factory() as session:
        session.add(
            DataSourceModel(
                id=source_id,
                project_id=project_id,
                code=f"{code}-{source_id.hex[:6]}",
                name="Human-1M 预览",
                kind="HUGGINGFACE",
                repo_id="nvidia/PhysicalAI-Human-1M-100K",
                revision=_REVISION,
                license="CC-BY-4.0",
                access_condition="PUBLIC",
                is_gated=False,
                metadata_json={},
                created_by=actor_id,
                updated_by=actor_id,
                created_at=_DATASOURCE_NOW,
                updated_at=_DATASOURCE_NOW,
            )
        )
        session.flush()
        session.add(
            DataSourceImportModel(
                id=import_id,
                project_id=project_id,
                data_source_id=source_id,
                status="SUCCEEDED",
                scope="SAMPLE",
                tool_name="huggingface_hub",
                tool_version="0.27.1",
                sample_limit=3,
                imported_file_count=2,
                created_by=actor_id,
                updated_by=actor_id,
                created_at=_IMPORT_NOW,
                updated_at=_IMPORT_NOW,
            )
        )
        session.commit()
    return source_id, import_id


def seed_import_asset(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    actor_id: UUID,
    label: str,
    import_id: UUID | None = None,
    source_id: UUID | None = None,
) -> UUID:
    """写入一条 ``source_type=IMPORT`` 的 RawAsset；给了导入任务就再写一行媒体资产。"""
    asset_id = uuid4()
    with factory() as session:
        session.add(
            RawAssetModel(
                id=asset_id,
                project_id=project_id,
                code=f"RAW-{label}-{asset_id.hex[:8]}",
                source_type="IMPORT",
                original_filename=f"{label}.mp4",
                object_key=f"raw/{project_id}/2026/11/08/{asset_id}/content.mp4",
                size_bytes=2048,
                content_type="video/mp4",
                format="MP4",
                sha256=f"sha256:{asset_id.hex}{uuid4().hex}"[:71],
                status="VALIDATING",
                ingested_at=_MEDIA_NOW,
                storage_class="STANDARD",
                quarantine_codes=[],
                raw_metadata={"origin": "data_source_import"},
                uploaded_by=actor_id,
                version=1,
                created_at=_MEDIA_NOW,
                updated_at=_MEDIA_NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        # 与生产写入一致：先 VALIDATING 再转 VALID（初始状态受触发器约束）。
        session.execute(
            update(RawAssetModel)
            .where(RawAssetModel.id == asset_id)
            .values(status="VALID")
            .execution_options(synchronize_session=False)
        )
        if import_id is not None and source_id is not None:
            session.add(
                MediaAssetModel(
                    id=uuid4(),
                    project_id=project_id,
                    data_source_id=source_id,
                    import_id=import_id,
                    raw_asset_id=asset_id,
                    episode_key=f"episode-{label}",
                    path=f"videos/{label}_main.mp4",
                    camera="main",
                    object_key=f"raw/{project_id}/2026/11/08/{asset_id}/content.mp4",
                    size_bytes=2048,
                    sha256=f"sha256:{'c' * 64}",
                    content_type="video/mp4",
                    task_name="pick",
                    source_fps=30.0,
                    created_by=actor_id,
                    updated_by=actor_id,
                    created_at=_MEDIA_NOW,
                    updated_at=_MEDIA_NOW,
                    version=1,
                )
            )
        session.commit()
    return asset_id


def test_reverse_from_raw_asset_reaches_import_and_data_source(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    source_id, import_id = seed_data_source(factory, project_id=ids.project_a, actor_id=ids.user_a)
    asset_id = seed_import_asset(
        factory,
        project_id=ids.project_a,
        actor_id=ids.user_a,
        label="imported",
        import_id=import_id,
        source_id=source_id,
    )
    scope = ProjectScope(ids.project_a, actor_id=ids.user_a)

    with factory() as session:
        _episodes, _jobs, lineage = build_services(session)
        depth_one = lineage.trace(
            scope,
            node_type=LineageNodeType.RAW_ASSET,
            node_id=asset_id,
            direction="backward",
            depth=1,
            require_node=False,
        )
        depth_two = lineage.trace(
            scope,
            node_type=LineageNodeType.RAW_ASSET,
            node_id=asset_id,
            direction="backward",
            depth=2,
            require_node=False,
        )

    assert [
        (edge.from_node_type, edge.to_node_type, edge.relation) for edge in depth_one.edges
    ] == [(LineageNodeType.DATA_SOURCE_IMPORT, LineageNodeType.RAW_ASSET, LineageRelation.PRODUCED)]
    assert depth_one.edges[0].from_node_id == import_id
    assert depth_one.edges[0].metadata["derived"] is True
    assert depth_one.edges[0].metadata["derived_from"] == "data_source_media_assets"
    assert depth_one.edges[0].metadata["camera"] == "main"

    # 深度 2 才跨到数据源：层的语义与持久化边一致，不额外"跳级"。
    assert [
        (edge.from_node_type, edge.to_node_type, edge.relation) for edge in depth_two.edges
    ] == [
        (LineageNodeType.DATA_SOURCE_IMPORT, LineageNodeType.RAW_ASSET, LineageRelation.PRODUCED),
        (LineageNodeType.DATA_SOURCE, LineageNodeType.DATA_SOURCE_IMPORT, LineageRelation.PRODUCED),
    ]
    assert depth_two.edges[1].to_node_id == import_id
    assert depth_two.edges[1].metadata["data_source"]["repo_id"] == (
        "nvidia/PhysicalAI-Human-1M-100K"
    )
    assert depth_two.edges[1].metadata["import"]["status"] == "SUCCEEDED"

    assert [origin.status for origin in depth_two.raw_asset_sources] == [
        RawAssetSourceStatus.RESOLVED
    ]
    origin = depth_two.raw_asset_sources[0]
    assert origin.raw_asset_id == asset_id
    assert [link.data_source_id for link in origin.links] == [source_id]
    assert [link.import_id for link in origin.links] == [import_id]


def test_forward_from_data_source_reaches_assets(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    source_id, import_id = seed_data_source(factory, project_id=ids.project_a, actor_id=ids.user_a)
    asset_id = seed_import_asset(
        factory,
        project_id=ids.project_a,
        actor_id=ids.user_a,
        label="forward",
        import_id=import_id,
        source_id=source_id,
    )
    scope = ProjectScope(ids.project_a, actor_id=ids.user_a)

    with factory() as session:
        _episodes, _jobs, lineage = build_services(session)
        one = lineage.trace(
            scope,
            node_type=LineageNodeType.DATA_SOURCE,
            node_id=source_id,
            direction="forward",
            depth=1,
            require_node=False,
        )
        two = lineage.trace(
            scope,
            node_type=LineageNodeType.DATA_SOURCE,
            node_id=source_id,
            direction="forward",
            depth=2,
            require_node=False,
        )

    assert [(edge.from_node_id, edge.to_node_id) for edge in one.edges] == [(source_id, import_id)]
    assert [(edge.from_node_type, edge.to_node_type) for edge in two.edges] == [
        (LineageNodeType.DATA_SOURCE, LineageNodeType.DATA_SOURCE_IMPORT),
        (LineageNodeType.DATA_SOURCE_IMPORT, LineageNodeType.RAW_ASSET),
    ]
    assert two.edges[1].to_node_id == asset_id
    # 正向能查到它产出的资产：这条就是验收要求的"从数据源查到哪些资产"。
    assert {origin.raw_asset_id for origin in two.raw_asset_sources} == {asset_id}


def test_forward_from_data_source_reaches_import_without_assets(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    """导入任务还没产出媒体文件时，数据源仍要能查到这次导入（左连接语义）。"""
    factory, ids = episode_db
    source_id, import_id = seed_data_source(factory, project_id=ids.project_a, actor_id=ids.user_a)
    scope = ProjectScope(ids.project_a, actor_id=ids.user_a)

    with factory() as session:
        _episodes, _jobs, lineage = build_services(session)
        trace = lineage.trace(
            scope,
            node_type=LineageNodeType.DATA_SOURCE,
            node_id=source_id,
            direction="forward",
            depth=2,
            require_node=False,
        )

    assert [(edge.from_node_id, edge.to_node_id) for edge in trace.edges] == [
        (source_id, import_id)
    ]
    assert trace.raw_asset_sources == []


def test_upload_asset_has_no_external_source(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    """手工上传的资产不是"缺失来源"，而是明确的无外部数据源。"""
    factory, ids = episode_db
    scope = ProjectScope(ids.project_a, actor_id=ids.user_a)

    with factory() as session:
        _episodes, _jobs, lineage = build_services(session)
        trace = lineage.trace(
            scope,
            node_type=LineageNodeType.RAW_ASSET,
            node_id=ids.asset_a,
            direction="backward",
            depth=2,
            require_node=False,
        )

    assert trace.edges == []
    assert [(origin.raw_asset_id, origin.status) for origin in trace.raw_asset_sources] == [
        (ids.asset_a, RawAssetSourceStatus.NO_EXTERNAL_SOURCE)
    ]


def test_import_asset_without_media_link_is_reported_missing(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    asset_id = seed_import_asset(
        factory, project_id=ids.project_a, actor_id=ids.user_a, label="orphan"
    )
    scope = ProjectScope(ids.project_a, actor_id=ids.user_a)

    with factory() as session:
        _episodes, _jobs, lineage = build_services(session)
        trace = lineage.trace(
            scope,
            node_type=LineageNodeType.RAW_ASSET,
            node_id=asset_id,
            direction="backward",
            depth=2,
            require_node=False,
        )

    assert trace.edges == []
    assert [origin.status for origin in trace.raw_asset_sources] == [
        RawAssetSourceStatus.SOURCE_LINK_MISSING
    ]


def test_data_source_chain_is_project_scoped(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    """B 项目的数据源/导入/媒体资产在 A 项目作用域下不可见。"""
    factory, ids = episode_db
    source_id, import_id = seed_data_source(factory, project_id=ids.project_b, actor_id=ids.user_b)
    asset_id = seed_import_asset(
        factory,
        project_id=ids.project_b,
        actor_id=ids.user_b,
        label="cross",
        import_id=import_id,
        source_id=source_id,
    )
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)

    with factory() as session:
        _episodes, _jobs, lineage = build_services(session)
        forward = lineage.trace(
            scope_a,
            node_type=LineageNodeType.DATA_SOURCE,
            node_id=source_id,
            direction="forward",
            depth=2,
            require_node=False,
        )
        origins = lineage.trace(
            scope_a,
            node_type=LineageNodeType.RAW_ASSET,
            node_id=asset_id,
            direction="backward",
            depth=2,
            require_node=False,
        )

    assert forward.edges == []
    assert forward.raw_asset_sources == []
    # 资产不属于 A 项目：既不泄露来源，也不能说成"无外部数据源"。
    assert [origin.status for origin in origins.raw_asset_sources] == [
        RawAssetSourceStatus.UNKNOWN_ASSET
    ]


def test_repository_limit_bounds_projection_rows(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    source_id, import_id = seed_data_source(factory, project_id=ids.project_a, actor_id=ids.user_a)
    for index in range(3):
        seed_import_asset(
            factory,
            project_id=ids.project_a,
            actor_id=ids.user_a,
            label=f"multi-{index}",
            import_id=import_id,
            source_id=source_id,
        )
    scope = ProjectScope(ids.project_a, actor_id=ids.user_a)

    with factory() as session:
        repository = SqlSourceProvenanceRepository(session)
        rows = repository.list_links_by_data_sources(scope, [source_id], limit=2)

    assert len(rows) == 2
    assert {row.import_id for row in rows} == {import_id}


def test_projected_root_requires_authoritative_check(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    """投影节点不能按节点登记表校验：误用必须报错，而不是返回空图。"""
    from app.domain.episodes.errors import DomainValidationError

    factory, ids = episode_db
    scope = ProjectScope(ids.project_a, actor_id=ids.user_a)
    with factory() as session:
        _episodes, _jobs, lineage = build_services(session)
        with pytest.raises(DomainValidationError):
            lineage.trace(
                scope,
                node_type=LineageNodeType.DATA_SOURCE,
                node_id=uuid4(),
                direction="forward",
                depth=1,
                require_node=True,
            )


def test_projection_rejects_two_filter_dimensions(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    """两种过滤维度同时传入说明方向搞错了，必须报错而不是静默取其一。"""
    from app.application.episodes.provenance import SourceProvenanceResolver
    from app.domain.episodes.errors import DomainValidationError
    from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository

    factory, ids = episode_db
    scope = ProjectScope(ids.project_a, actor_id=ids.user_a)
    with factory() as session:
        resolver = SourceProvenanceResolver(
            SqlSourceProvenanceRepository(session), SqlRawAssetRepository(session)
        )
        with pytest.raises(DomainValidationError):
            resolver.source_to_import_edges(scope, source_ids=[uuid4()], import_ids=[uuid4()])
        with pytest.raises(DomainValidationError):
            resolver.import_to_asset_edges(scope, import_ids=[uuid4()], asset_ids=[uuid4()])
