"""CALIBRATION_VALIDATION 阶段：Episode 标定快照可追溯性校验。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.processing.calibration_stage import CalibrationValidationHandler
from app.application.processing.stages import StageContext, StageFailure
from app.domain.devices.calibration import CalibrationRecord
from app.domain.episodes.models import Episode
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import ProcessingStepStatus
from app.domain.processing.models import ProcessingRun

from tests.calibration_fixtures import parameters_for

NOW = datetime(2026, 11, 21, tzinfo=UTC)
PROJECT_ID = UUID("00000000-0000-7000-8000-000000000201")
ACTOR_ID = UUID("00000000-0000-7000-8000-000000000202")
RUN_ID = UUID("00000000-0000-7000-8000-000000000203")
EPISODE_ID = UUID("00000000-0000-7000-8000-000000000204")
CALIBRATION_ID = UUID("00000000-0000-7000-8000-000000000205")
PROFILE_ID = UUID("00000000-0000-7000-8000-000000000206")
TARGET_ID = UUID("00000000-0000-7000-8000-000000000207")
VALIDATOR_ID = UUID("00000000-0000-7000-8000-000000000210")
EVIDENCE_ID = UUID("00000000-0000-7000-8000-000000000211")
CHECKSUM = "sha256:" + "a" * 64
SCHEMA_VERSION = "ddp.calibration/1.0.0"


def _snapshot(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "calibration_id": str(CALIBRATION_ID),
        "calibration_version": 3,
        "checksum": CHECKSUM,
        "target_type": "SENSOR",
        "target_id": str(TARGET_ID),
        "schema_version": SCHEMA_VERSION,
    }
    value.update(overrides)
    return value


def _episode(*, snapshot: tuple[dict[str, object], ...] | None = None) -> Episode:
    return Episode(
        episode_id=EPISODE_ID,
        project_id=PROJECT_ID,
        code="DROID-EP-000",
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
        segmentation_rule_version="episode-metadata-v1",
        calibration_snapshot=snapshot if snapshot is not None else (_snapshot(),),
    )


def _record(**overrides: object) -> CalibrationRecord:
    value: dict[str, object] = {
        "calibration_id": CALIBRATION_ID,
        "project_id": PROJECT_ID,
        "profile_id": PROFILE_ID,
        "scope": "SENSOR",
        "target_type": "SENSOR",
        "target_id": TARGET_ID,
        "calibration_type": "INTRINSICS",
        "method": "公开标定导入",
        "parameters": parameters_for("INTRINSICS"),
        "valid_from": NOW,
        "evidence_asset_ids": (),
        "checksum": CHECKSUM,
        "performed_by": ACTOR_ID,
        "calibration_version": 3,
        "profile_schema_version": SCHEMA_VERSION,
    }
    value.update(overrides)
    return CalibrationRecord(**value)  # type: ignore[arg-type]


class _Episodes:
    def __init__(self, *episodes: Episode) -> None:
        self._items = {episode.episode_id: episode for episode in episodes}

    def get(self, scope: ProjectScope, episode_id: UUID) -> Episode | None:
        episode = self._items.get(episode_id)
        if episode is None or episode.project_id != scope.project_id:
            return None
        return episode


class _Calibrations:
    def __init__(self, *records: CalibrationRecord) -> None:
        self._items = {record.calibration_id: record for record in records}

    def get(self, scope: ProjectScope, calibration_id: UUID) -> CalibrationRecord | None:
        record = self._items.get(calibration_id)
        if record is None or record.project_id != scope.project_id:
            return None
        return record


class _EpisodeAssets:
    def list_for_asset(self, scope: ProjectScope, asset_id: UUID) -> list[object]:
        return []


def _context(*episode_ids: UUID) -> StageContext:
    refs = tuple(
        f"canonical/{PROJECT_ID}/{episode_id}/1/manifest.json"
        for episode_id in (episode_ids or (EPISODE_ID,))
    )
    run = ProcessingRun(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        pipeline_version="pipeline-v1",
        input_refs=refs,
        created_at=NOW,
        updated_at=NOW,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    return StageContext(run, ProjectScope(PROJECT_ID, actor_id=ACTOR_ID), NOW)


def _handler(episode: Episode, *records: CalibrationRecord) -> CalibrationValidationHandler:
    return CalibrationValidationHandler(
        episodes=_Episodes(episode),
        episode_assets=_EpisodeAssets(),  # type: ignore[arg-type]
        calibrations=_Calibrations(*records),
    )


def test_matching_snapshot_remains_traceable_after_record_is_superseded() -> None:
    record = _record(
        status="SUPERSEDED",
        evidence_asset_ids=(EVIDENCE_ID,),
        validated_by=VALIDATOR_ID,
        validated_at=NOW,
    )

    outcome = _handler(_episode(), record)(_context())

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert "一致 1" in outcome.log_summary
    assert "缺失 0" in outcome.log_summary


def test_empty_snapshot_is_reported_as_missing_without_fabricating_calibration() -> None:
    outcome = _handler(_episode(snapshot=()))(_context())

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert "一致 0" in outcome.log_summary
    assert "缺失 1" in outcome.log_summary


def test_run_without_related_episode_is_explicitly_skipped() -> None:
    context = _context()
    context = replace(
        context,
        run=replace(context.run, input_refs=(f"raw/{PROJECT_ID}/capture.mcap",)),
    )

    outcome = _handler(_episode())(context)

    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert "未关联任何 Episode" in outcome.log_summary


def test_missing_or_cross_project_calibration_is_reported_invalid() -> None:
    foreign = _record(project_id=UUID("00000000-0000-7000-8000-000000000299"))

    outcome = _handler(_episode(), foreign)(_context())

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert "无效 1" in outcome.log_summary


@pytest.mark.parametrize(
    "record_override,expected_field",
    [
        ({"calibration_version": 4}, "calibration_version"),
        ({"checksum": "sha256:" + "b" * 64}, "checksum"),
        ({"target_id": UUID("00000000-0000-7000-8000-000000000208")}, "target_id"),
        ({"target_type": "ROBOT", "scope": "ROBOT"}, "target_type"),
        ({"profile_schema_version": "ddp.calibration/2.0.0"}, "schema_version"),
    ],
)
def test_snapshot_conflict_is_reported_invalid(
    record_override: dict[str, object], expected_field: str
) -> None:
    outcome = _handler(_episode(), _record(**record_override))(_context())

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert "无效 1" in outcome.log_summary
    assert expected_field not in outcome.log_summary


def test_episode_limit_is_fail_closed() -> None:
    second_id = UUID("00000000-0000-7000-8000-000000000209")
    handler = CalibrationValidationHandler(
        episodes=_Episodes(_episode()),
        episode_assets=_EpisodeAssets(),  # type: ignore[arg-type]
        calibrations=_Calibrations(_record()),
        max_episodes=1,
    )

    with pytest.raises(StageFailure) as captured:
        handler(_context(EPISODE_ID, second_id))

    assert captured.value.error_code == "calibration_episode_limit_exceeded"
    assert captured.value.retryable is False
