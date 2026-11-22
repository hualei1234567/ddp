"""N3：Episode 创建路径的切片规则校验（真实 SQLite 仓储，非 Mock 业务）。

证明三件事：
1. 未注册的 ``rule_version`` 在写库**之前**失败，不留半截 Episode 行；
2. 已注册规则能正常创建，``segmentation_rule_version`` 正确落库；
3. 注册表是实例级的——装配方注入空表，创建路径就拒绝规则切片，
   但人工切片（既有全部集成测试的路径）继续可用。
"""

from __future__ import annotations

from uuid import UUID

import pytest
from app.application.episodes.commands import CreateEpisodeCommand
from app.application.episodes.episodes import EpisodeService
from app.domain.episodes.errors import SegmentationRuleNotRegisteredError
from app.domain.episodes.models import Segmentation
from app.domain.episodes.segmentation_rules import (
    FIXED_WINDOW_RULE_VERSION,
    SegmentationRuleRegistry,
    build_segmentation_rule_registry,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.episodes import EpisodeModel
from ddp_data.canonical.v1 import SCHEMA_VERSION
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.infrastructure.episode_support import (
    build_services,
    calibration_snapshot,
    create_command,
    time_base,
)


def _scope(ids: object, project: str = "a") -> ProjectScope:
    if project == "a":
        return ProjectScope(ids.project_a, actor_id=ids.user_a)  # type: ignore[attr-defined]
    return ProjectScope(ids.project_b, actor_id=ids.user_b)  # type: ignore[attr-defined]


def _rule_command(
    asset_id: UUID,
    *,
    rule_version: str,
    parameters: dict[str, object],
) -> CreateEpisodeCommand:
    return CreateEpisodeCommand(
        raw_asset_ids=(asset_id,),
        segmentation=Segmentation(
            method="RULE",
            rule_version=rule_version,
            start_time_ns=1_000,
            end_time_ns=2_000,
            rule_parameters=parameters,
        ),
        time_base=time_base(),
        calibration_snapshot=calibration_snapshot(),
        canonical_schema_version=SCHEMA_VERSION,
        pipeline_id="parser",
        pipeline_version="1.0.0",
    )


def _episode_count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return len(session.scalars(select(EpisodeModel)).all())


def test_create_rejects_unregistered_rule_before_writing(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        episode_service, _, _ = build_services(session)
        with pytest.raises(SegmentationRuleNotRegisteredError):
            episode_service.create(
                _scope(ids),
                _rule_command(
                    ids.asset_a,  # type: ignore[attr-defined]
                    rule_version="ghost-rule/2.0.0",
                    parameters={"window_ns": 10},
                ),
            )
    # 关键断言：失败发生在写库前，不能留下半截 Episode 行。
    assert _episode_count(factory) == 0


def test_create_accepts_registered_rule(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = episode_db
    with factory() as session:
        episode_service, _, _ = build_services(session)
        episode = episode_service.create(
            _scope(ids),
            _rule_command(
                ids.asset_a,  # type: ignore[attr-defined]
                rule_version=FIXED_WINDOW_RULE_VERSION,
                parameters={"window_ns": 100},
            ),
        )
        assert episode.segmentation_rule_version == FIXED_WINDOW_RULE_VERSION
    assert _episode_count(factory) == 1


def test_injected_registry_controls_what_is_accepted(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    """注册表是实例级的：装配方注入空表，规则切片就被拒绝。"""
    factory, ids = episode_db
    with factory() as session:
        episode_service = _service_with_registry(session, SegmentationRuleRegistry())
        with pytest.raises(SegmentationRuleNotRegisteredError):
            episode_service.create(
                _scope(ids),
                _rule_command(
                    ids.asset_a,  # type: ignore[attr-defined]
                    rule_version=FIXED_WINDOW_RULE_VERSION,
                    parameters={"window_ns": 100},
                ),
            )
    assert _episode_count(factory) == 0


def test_default_assembly_uses_builtin_registry(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    """不显式注入时使用内置规则集合；显式注入空表时不得被内置表顶替。"""
    factory, ids = episode_db
    expected = build_segmentation_rule_registry().registered_versions()
    with factory() as session:
        default_service, _, _ = build_services(session)
        builtins = default_service._segmentation_rules  # noqa: SLF001 - 断言装配契约
        assert builtins.registered_versions() == expected

        empty_service = _service_with_registry(session, SegmentationRuleRegistry())
        assert len(empty_service._segmentation_rules) == 0  # noqa: SLF001


def test_manual_time_range_create_still_works(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    """回归保护：既有集成测试全部走人工切片，必须继续通过。"""
    factory, ids = episode_db
    with factory() as session:
        episode_service, _, _ = build_services(session)
        episode = episode_service.create(
            _scope(ids),
            create_command(ids.asset_a, code="EP-MANUAL-OK"),  # type: ignore[attr-defined]
        )
        assert episode.segmentation_rule_version == "seg-manual-v1"
    assert _episode_count(factory) == 1


def test_cross_project_still_isolated_for_rule_segmentation(
    episode_db: tuple[sessionmaker[Session], object],
) -> None:
    """新增校验不得动摇 project scope：跨项目资产仍然 404（不泄漏存在性）。"""
    from app.domain.episodes.errors import ResourceNotFoundError

    factory, ids = episode_db
    with factory() as session:
        episode_service, _, _ = build_services(session)
        with pytest.raises(ResourceNotFoundError):
            episode_service.create(
                _scope(ids, "a"),
                _rule_command(
                    ids.asset_b,  # type: ignore[attr-defined]
                    rule_version=FIXED_WINDOW_RULE_VERSION,
                    parameters={"window_ns": 100},
                ),
            )
    assert _episode_count(factory) == 0


def _service_with_registry(
    session: Session,
    registry: SegmentationRuleRegistry,
) -> EpisodeService:
    """按生产装配方式重建 ``EpisodeService``，只替换注册表。

    直接复用 ``tests.infrastructure.episode_support.build_services`` 的仓储组合，
    避免测试自造一套平行装配（漏掉某个仓储就会让断言失去意义）。
    """
    from app.infrastructure.repositories.collection_audit import (
        SqlAuditRepository,
        SqlOutboxRepository,
    )
    from app.infrastructure.repositories.episodes import (
        SqlEpisodeRawAssetRepository,
        SqlEpisodeRepository,
    )
    from app.infrastructure.repositories.ingestion_jobs import SqlIngestionJobRepository
    from app.infrastructure.repositories.lineage import SqlLineageRepository
    from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository

    return EpisodeService(
        SqlEpisodeRepository(session),
        SqlEpisodeRawAssetRepository(session),
        SqlLineageRepository(session),
        SqlRawAssetRepository(session),
        SqlIngestionJobRepository(session),
        SqlAuditRepository(session),
        SqlOutboxRepository(session),
        session,
        segmentation_rules=registry,
    )


def test_service_constructor_signature_is_explicit() -> None:
    """构造签名契约：注册表是可选关键字参数，既有装配点无需改动。"""
    import inspect

    signature = inspect.signature(EpisodeService.__init__)
    parameter = signature.parameters["segmentation_rules"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None
