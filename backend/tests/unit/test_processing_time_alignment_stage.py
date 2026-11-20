"""TIME_ALIGNMENT 阶段：严格 manifest 输入与同步分析产物。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.application.processing.ports import ObjectInspectionError
from app.application.processing.stages import StageContext, StageFailure
from app.application.processing.time_alignment_stage import (
    TIME_ALIGNMENT_REPORT_SCHEMA_VERSION,
    TimeAlignmentHandler,
    time_alignment_report_key,
)
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import ProcessingStepStatus
from app.domain.processing.models import ProcessingRun

from tests.unit.processing_stage_support import FakeArtifactStore, FakeObjectReader

NOW = datetime(2026, 11, 20, tzinfo=UTC)
PROJECT_ID = UUID("00000000-0000-7000-8000-000000000101")
ACTOR_ID = UUID("00000000-0000-7000-8000-000000000102")
RUN_ID = UUID("00000000-0000-7000-8000-000000000103")
MANIFEST_REF = f"canonical/{PROJECT_ID}/time-alignment/droid-episode-0.json"


def _manifest(*, groups: list[dict[str, object]] | None = None) -> bytes:
    document: dict[str, object] = {
        "schema_version": "ddp.processing.time-alignment-input/1.0.0",
        "time_unit": "ns",
        "groups": groups
        if groups is not None
        else [
            {
                "group_key": "lerobot/droid_100:episode:0",
                "streams": [
                    {
                        "stream_id": "exterior",
                        "start_time_ns": "0",
                        "end_time_ns": "11066667000",
                        "frame_rate": {"num": 15, "den": 1},
                        "declared_frame_count": 166,
                        "time_domain": "UNKNOWN",
                        "clock_source": "UNKNOWN",
                    },
                    {
                        "stream_id": "wrist",
                        "start_time_ns": "0",
                        "end_time_ns": "11066667000",
                        "frame_rate": {"num": 15, "den": 1},
                        "declared_frame_count": 166,
                        "time_domain": "UNKNOWN",
                        "clock_source": "UNKNOWN",
                    },
                ],
            }
        ],
    }
    return json.dumps(document).encode("utf-8")


def _context(*refs: str) -> StageContext:
    run = ProcessingRun(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        pipeline_version="pipeline-v1",
        input_refs=refs or (f"raw/{PROJECT_ID}/capture.mcap",),
        created_at=NOW,
        updated_at=NOW,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    return StageContext(
        run=run,
        scope=ProjectScope(PROJECT_ID, actor_id=ACTOR_ID),
        now=NOW,
    )


def test_valid_manifest_writes_group_analysis_artifact() -> None:
    reader = FakeObjectReader({MANIFEST_REF: _manifest()})
    artifacts = FakeArtifactStore()

    outcome = TimeAlignmentHandler(reader, artifacts)(_context(MANIFEST_REF))

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert outcome.output_refs == (time_alignment_report_key(PROJECT_ID, RUN_ID),)
    report = json.loads(artifacts.read_bytes(outcome.output_refs[0], max_bytes=256 * 1024))
    assert report["schema_version"] == TIME_ALIGNMENT_REPORT_SCHEMA_VERSION
    assert report["source_manifest_ref"] == MANIFEST_REF
    assert report["groups"][0]["group_key"] == "lerobot/droid_100:episode:0"
    assert report["groups"][0]["analysis"]["status"] == "ALIGNED"
    corrections = [
        stream["correction"]
        for stream in report["groups"][0]["analysis"]["streams"]
        if stream["correction"] is not None
    ]
    assert corrections
    assert all(correction["applied"] is False for correction in corrections)


def test_missing_manifest_is_explicitly_skipped() -> None:
    outcome = TimeAlignmentHandler(FakeObjectReader(), FakeArtifactStore())(
        _context(f"raw/{PROJECT_ID}/capture.mcap")
    )

    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert "manifest" in outcome.log_summary
    assert outcome.output_refs == ()


def test_empty_groups_are_explicitly_skipped() -> None:
    outcome = TimeAlignmentHandler(
        FakeObjectReader({MANIFEST_REF: _manifest(groups=[])}), FakeArtifactStore()
    )(_context(MANIFEST_REF))

    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert "groups" in outcome.log_summary


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        json.dumps(
            {
                "schema_version": "ddp.processing.time-alignment-input/9.9.9",
                "time_unit": "ns",
                "groups": [],
            }
        ).encode("utf-8"),
    ],
)
def test_invalid_manifest_fails_permanently(payload: bytes) -> None:
    handler = TimeAlignmentHandler(FakeObjectReader({MANIFEST_REF: payload}), FakeArtifactStore())

    with pytest.raises(StageFailure) as captured:
        handler(_context(MANIFEST_REF))

    assert captured.value.error_code == "time_alignment_artifact_invalid"
    assert captured.value.retryable is False


def test_multiple_manifests_fail_instead_of_guessing_order() -> None:
    second = f"canonical/{PROJECT_ID}/time-alignment/droid-episode-1.json"
    handler = TimeAlignmentHandler(
        FakeObjectReader({MANIFEST_REF: _manifest(), second: _manifest()}),
        FakeArtifactStore(),
    )

    with pytest.raises(StageFailure) as captured:
        handler(_context(MANIFEST_REF, second))

    assert captured.value.error_code == "time_alignment_manifest_ambiguous"
    assert captured.value.retryable is False


def test_storage_failure_is_retryable() -> None:
    class UnavailableReader(FakeObjectReader):
        def object_size(self, object_key: str) -> int | None:
            raise ObjectInspectionError("storage unavailable")

    with pytest.raises(StageFailure) as captured:
        TimeAlignmentHandler(UnavailableReader(), FakeArtifactStore())(_context(MANIFEST_REF))

    assert captured.value.error_code == "object_storage_unavailable"
    assert captured.value.retryable is True
