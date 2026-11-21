"""ROS Bag 接入 Processing Metadata Stage 的专项回归。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.application.processing.metadata_stage import (
    METADATA_SCHEMA_VERSION,
    FormatParsingHandler,
    MetadataExtractionHandler,
    metadata_artifact_key,
)
from app.application.processing.stages import StageContext, StageFailure
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import ProcessingStage, ProcessingStepStatus
from app.domain.processing.models import ProcessingRun

from tests.unit.processing_stage_support import FakeArtifactStore, FakeObjectReader

NOW = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
_PROJECT_ID = uuid4()
_RUN_ID = uuid4()
_SCOPE = ProjectScope(project_id=_PROJECT_ID, actor_id=uuid4())


def _context(*, output_refs: tuple[str, ...] = ()) -> StageContext:
    ref = f"raw/{_PROJECT_ID}/2026/11/21/{uuid4()}/content.db3"
    run = ProcessingRun(
        run_id=_RUN_ID,
        project_id=_PROJECT_ID,
        pipeline_version="pipeline-v1",
        input_refs=(ref,),
        output_refs=output_refs,
        status="RUNNING",
        stage=ProcessingStage.METADATA_EXTRACTION.value,
        attempt=1,
        started_at=NOW,
    )
    return StageContext(run=run, scope=_SCOPE, now=NOW)


def _context_for(ref: str, *, output_refs: tuple[str, ...] = ()) -> StageContext:
    context = _context(output_refs=output_refs)
    run = ProcessingRun(
        run_id=context.run.run_id,
        project_id=context.run.project_id,
        pipeline_version=context.run.pipeline_version,
        input_refs=(ref,),
        output_refs=output_refs,
        status=context.run.status,
        stage=context.run.stage,
        attempt=context.run.attempt,
        started_at=context.run.started_at,
    )
    return StageContext(run=run, scope=context.scope, now=context.now)


def _handler(reader: FakeObjectReader, artifacts: FakeArtifactStore) -> MetadataExtractionHandler:
    return MetadataExtractionHandler(reader, artifacts, max_object_bytes=4 * 1024 * 1024)


def _metadata_document(*, source_ref: str) -> bytes:
    return json.dumps(
        {
            "schema_version": METADATA_SCHEMA_VERSION,
            "source_ref": source_ref,
            "format": "ROSBAG2",
            "adapter_id": "rosbag.structure_readonly",
            "adapter_version": "1.0.0",
            "object_size_bytes": 5482,
            "metadata": {"format": "rosbag1", "topic_count": 2, "message_count": 3},
        }
    ).encode()


def _build_rosbag1(path: Path) -> bytes:
    from rosbags.rosbag1 import Writer
    from rosbags.typesys import Stores, get_typestore

    typestore = get_typestore(Stores.ROS1_NOETIC)
    message_type = typestore.types["std_msgs/msg/String"]
    with Writer(path) as writer:
        connection = writer.add_connection(
            "/camera/front",
            message_type.__msgtype__,
            typestore=typestore,
        )
        for timestamp in (2_000_000_000, 2_500_000_000):
            payload = typestore.serialize_ros1(
                message_type(data=f"body-{timestamp}"),
                message_type.__msgtype__,
            )
            writer.write(connection, timestamp, payload)
    return path.read_bytes()


def test_extracts_rosbag1_index_without_reading_message_bodies(tmp_path: Path) -> None:
    ref = f"raw/{_PROJECT_ID}/2026/11/21/{uuid4()}/content.db3"
    reader = FakeObjectReader({ref: _build_rosbag1(tmp_path / "sample.bag")})
    artifacts = FakeArtifactStore()

    outcome = _handler(reader, artifacts)(_context_for(ref))

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    document = json.loads(artifacts.read_bytes(outcome.output_refs[0], max_bytes=256 * 1024))
    assert document["format"] == "ROSBAG2"
    assert document["adapter_id"] == "rosbag.structure_readonly"
    assert document["metadata"]["format"] == "rosbag1"
    assert document["metadata"]["topic_count"] == 1
    assert document["metadata"]["message_count"] == 2
    assert document["metadata"]["topics"][0]["topic"] == "/camera/front"
    assert "body-2000000000" not in json.dumps(document)


def test_corrupt_rosbag1_is_permanent_failure() -> None:
    ref = f"raw/{_PROJECT_ID}/2026/11/21/{uuid4()}/content.db3"
    reader = FakeObjectReader({ref: b"#ROSBAG V2.0\n" + b"corrupt"})

    with pytest.raises(StageFailure) as excinfo:
        _handler(reader, FakeArtifactStore())(_context_for(ref))

    assert excinfo.value.error_code == "metadata_probe_failed"
    assert excinfo.value.retryable is False


def test_rosbag_metadata_limit_is_permanent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ddp_data import rosbag

    def fail_probe(path: Path) -> object:
        raise rosbag.RosbagMetadataLimitError("limit")

    monkeypatch.setattr(rosbag, "read_rosbag_structure", fail_probe)
    ref = f"raw/{_PROJECT_ID}/2026/11/21/{uuid4()}/content.db3"
    reader = FakeObjectReader({ref: b"#ROSBAG V2.0\n" + b"0" * 64})

    with pytest.raises(StageFailure) as excinfo:
        _handler(reader, FakeArtifactStore())(_context_for(ref))

    assert excinfo.value.error_code == "metadata_limit_exceeded"
    assert excinfo.value.retryable is False


def test_rosbag_sdk_unavailable_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    from ddp_data import rosbag

    def fail_probe(path: Path) -> object:
        raise rosbag.RosbagUnavailableError("unavailable")

    monkeypatch.setattr(rosbag, "read_rosbag_structure", fail_probe)
    ref = f"raw/{_PROJECT_ID}/2026/11/21/{uuid4()}/content.db3"
    reader = FakeObjectReader({ref: b"#ROSBAG V2.0\n" + b"0" * 64})

    with pytest.raises(StageFailure) as excinfo:
        _handler(reader, FakeArtifactStore())(_context_for(ref))

    assert excinfo.value.error_code == "metadata_probe_unavailable"
    assert excinfo.value.retryable is True


def test_standalone_rosbag2_sqlite_is_explicit_failure() -> None:
    ref = f"raw/{_PROJECT_ID}/2026/11/21/{uuid4()}/content.db3"
    reader = FakeObjectReader({ref: b"SQLite format 3\x00" + b"0" * 64})

    with pytest.raises(StageFailure) as excinfo:
        _handler(reader, FakeArtifactStore())(_context_for(ref))

    assert excinfo.value.error_code == "metadata_probe_failed"
    assert excinfo.value.retryable is False


def test_format_parsing_keeps_rosbag_as_stage_reference_without_fake_episode() -> None:
    source_ref = f"raw/{_PROJECT_ID}/2026/11/21/{uuid4()}/content.db3"
    metadata_ref = metadata_artifact_key(_PROJECT_ID, _RUN_ID, 0, "ROSBAG2")
    artifacts = FakeArtifactStore()
    artifacts.put_bytes(metadata_ref, _metadata_document(source_ref=source_ref))

    outcome = FormatParsingHandler(artifacts)(_context_for(source_ref, output_refs=(metadata_ref,)))

    document = json.loads(artifacts.read_bytes(outcome.output_refs[0], max_bytes=256 * 1024))
    assert document["kind"] == "ROSBAG2_STAGE_ARTIFACT_REFERENCE"
    assert document["canonical_episode"] is False
    assert "episode" not in document


def test_rosbag_stage_payload_bounds_topic_details_without_hiding_totals() -> None:
    from app.application.processing.rosbag_metadata import (
        MAX_ROSBAG_PAYLOAD_BYTES,
        rosbag_stage_payload,
    )
    from ddp_data.rosbag import RosbagStructure, RosbagTopicSummary

    topics = tuple(
        RosbagTopicSummary(
            topic=f"/camera/{index:04d}/" + "x" * 2000,
            msgtype="pkg/msg/Frame" + "y" * 2000,
            message_count=1,
        )
        for index in range(100)
    )
    structure = RosbagStructure(
        adapter_id="rosbag.structure_readonly",
        adapter_version="1.0.0",
        format="rosbag1",
        topic_count=len(topics),
        message_count=len(topics),
        start_time_ns=1,
        end_time_ns=100,
        duration_ns=99,
        topics=topics,
    )

    payload = rosbag_stage_payload(structure)
    encoded = json.dumps(payload, ensure_ascii=False).encode()

    assert len(encoded) <= MAX_ROSBAG_PAYLOAD_BYTES
    assert payload["topic_count"] == 100
    assert payload["details_truncated"] is True
    assert payload["omitted_topic_count"] == 100 - len(payload["topics"])
    assert 0 < len(payload["topics"]) < 100


def test_rosbag_stage_payload_caps_topic_count_with_exact_omissions() -> None:
    from app.application.processing.rosbag_metadata import rosbag_stage_payload
    from ddp_data.rosbag import RosbagStructure, RosbagTopicSummary

    topics = tuple(
        RosbagTopicSummary(
            topic=f"/topic/{index:04d}",
            msgtype="pkg/msg/Value",
            message_count=1,
        )
        for index in range(300)
    )
    payload = rosbag_stage_payload(
        RosbagStructure(
            adapter_id="rosbag.structure_readonly",
            adapter_version="1.0.0",
            format="rosbag1",
            topic_count=300,
            message_count=300,
            start_time_ns=1,
            end_time_ns=300,
            duration_ns=299,
            topics=topics,
        )
    )

    assert len(payload["topics"]) == 256
    assert payload["omitted_topic_count"] == 44
    assert payload["details_truncated"] is True
