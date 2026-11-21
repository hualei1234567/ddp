"""EPISODE_SEGMENTATION 阶段：既有 Episode 分段来源可追溯性检查。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.processing.segmentation_stage import EpisodeSegmentationHandler
from app.application.processing.stages import StageContext, StageFailure
from app.domain.episodes.models import Episode
from app.domain.episodes.segmentation_rules import (
    EPISODE_BOUNDARY_RULE_VERSION,
    build_segmentation_rule_registry,
)
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import ProcessingStepStatus
from app.domain.processing.models import ProcessingRun

NOW = datetime(2026, 11, 21, tzinfo=UTC)
PROJECT_ID = UUID("00000000-0000-7000-8000-000000000301")
ACTOR_ID = UUID("00000000-0000-7000-8000-000000000302")
RUN_ID = UUID("00000000-0000-7000-8000-000000000303")
EPISODE_ID = UUID("00000000-0000-7000-8000-000000000304")


def _episode(
    *,
    episode_id: UUID = EPISODE_ID,
    project_id: UUID = PROJECT_ID,
    rule_version: str = EPISODE_BOUNDARY_RULE_VERSION,
) -> Episode:
    return Episode(
        episode_id=episode_id,
        project_id=project_id,
        code=f"EP-{episode_id}",
        start_time_ns=0,
        end_time_ns=1_000_000_000,
        time_base={
            "time_domain": "UTC",
            "clock_source": "NTP",
            "sync_method": "NTP",
            "sync_status": "VERIFIED",
        },
        canonical_schema_version="ddp.canonical/1.0.0-draft",
        pipeline_id="droid-public",
        pipeline_version="1.0.0",
        segmentation_rule_version=rule_version,
        calibration_snapshot=(),
    )


class _Episodes:
    def __init__(self, *episodes: Episode) -> None:
        self._items = {episode.episode_id: episode for episode in episodes}

    def get(self, scope: ProjectScope, episode_id: UUID) -> Episode | None:
        episode = self._items.get(episode_id)
        if episode is None or episode.project_id != scope.project_id:
            return None
        return episode


class _EpisodeAssets:
    def list_for_asset(self, scope: ProjectScope, asset_id: UUID) -> list[object]:
        return []


def _context(*episode_ids: UUID) -> StageContext:
    ids = episode_ids or (EPISODE_ID,)
    run = ProcessingRun(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        pipeline_version="pipeline-v1",
        input_refs=tuple(
            f"canonical/{PROJECT_ID}/{episode_id}/1/manifest.json" for episode_id in ids
        ),
        created_at=NOW,
        updated_at=NOW,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    return StageContext(run, ProjectScope(PROJECT_ID, actor_id=ACTOR_ID), NOW)


def _handler(*episodes: Episode, max_episodes: int = 200) -> EpisodeSegmentationHandler:
    return EpisodeSegmentationHandler(
        episodes=_Episodes(*episodes),
        episode_assets=_EpisodeAssets(),  # type: ignore[arg-type]
        rules=build_segmentation_rule_registry(),
        max_episodes=max_episodes,
    )


def test_registered_rule_is_reported_traceable_without_resegmenting() -> None:
    outcome = _handler(_episode())(_context())

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert "可追溯 1" in outcome.log_summary
    assert "不可重放 0" in outcome.log_summary
    assert "未执行重新切片" in outcome.log_summary
    assert outcome.output_refs == ()


def test_manual_or_legacy_rule_label_is_unverifiable_not_fabricated() -> None:
    outcome = _handler(_episode(rule_version="seg-manual-v1"))(_context())

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert "可追溯 0" in outcome.log_summary
    assert "不可重放 1" in outcome.log_summary
    assert "seg-manual-v1" not in outcome.log_summary


def test_run_without_related_episode_is_explicitly_skipped() -> None:
    context = _context()
    context = replace(
        context,
        run=replace(context.run, input_refs=(f"raw/{PROJECT_ID}/capture.mcap",)),
    )

    outcome = _handler(_episode())(context)

    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert "未关联任何 Episode" in outcome.log_summary


def test_missing_or_cross_project_episode_is_fail_closed() -> None:
    foreign = _episode(project_id=UUID("00000000-0000-7000-8000-000000000399"))

    with pytest.raises(StageFailure) as captured:
        _handler(foreign)(_context())

    assert captured.value.error_code == "segmentation_episode_not_found"
    assert captured.value.retryable is False


def test_episode_limit_is_fail_closed() -> None:
    second_id = UUID("00000000-0000-7000-8000-000000000305")

    with pytest.raises(StageFailure) as captured:
        _handler(_episode(), max_episodes=1)(_context(EPISODE_ID, second_id))

    assert captured.value.error_code == "segmentation_episode_limit_exceeded"
    assert captured.value.retryable is False
