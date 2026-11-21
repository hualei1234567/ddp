"""固定 revision 的公开 ROS Bag 样例复验（默认跳过，不以 Mock 冒充）。

设置 ``DDP_TEST_REAL_ROSBAG`` 指向 foxglove/mcap 的
``noetic-multitopic-none.bag`` 后执行。来源、许可、SHA256 与观察事实见
``backend/tests/fixtures/public/rosbag/facts.json``。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from ddp_data.rosbag import read_rosbag_structure

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "public" / "rosbag"
_FACTS = json.loads((_FIXTURE_DIR / "facts.json").read_text())
_REAL_PATH = os.environ.get("DDP_TEST_REAL_ROSBAG")
pytestmark = pytest.mark.skipif(
    not _REAL_PATH or not Path(_REAL_PATH).is_file(),
    reason="需要 DDP_TEST_REAL_ROSBAG 指向固定公开 noetic-multitopic-none.bag",
)


def test_real_public_rosbag_matches_pinned_hash() -> None:
    assert _REAL_PATH is not None
    digest = hashlib.sha256(Path(_REAL_PATH).read_bytes()).hexdigest()
    assert digest == _FACTS["source"]["file_sha256"]
    assert Path(_REAL_PATH).stat().st_size == _FACTS["source"]["file_size_bytes"]


def test_real_public_rosbag_structure_matches_recorded_facts() -> None:
    assert _REAL_PATH is not None
    actual = read_rosbag_structure(_REAL_PATH).as_metadata()
    expected = _FACTS["observed_structure"]

    for key in (
        "format",
        "topic_count",
        "message_count",
        "start_time_ns",
        "end_time_ns",
        "duration_ns",
        "topics",
    ):
        assert actual[key] == expected[key]


def test_real_public_rosbag_processing_stage_emits_bounded_reference() -> None:
    """固定公开 ROS1 bag 经 Stage 后只输出索引结构，不携带消息体。"""
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.application.processing.metadata_stage import (
        FormatParsingHandler,
        MetadataExtractionHandler,
    )
    from app.application.processing.stages import StageContext
    from app.domain.identity.models import ProjectScope
    from app.domain.processing.enums import ProcessingStage, ProcessingStepStatus
    from app.domain.processing.models import ProcessingRun

    from tests.unit.processing_stage_support import FakeArtifactStore, FakeObjectReader

    assert _REAL_PATH is not None
    project_id = uuid4()
    run_id = uuid4()
    ref = f"raw/{project_id}/2026/11/21/{uuid4()}/content.db3"
    artifacts = FakeArtifactStore()

    def context(*, output_refs: tuple[str, ...] = ()) -> StageContext:
        return StageContext(
            run=ProcessingRun(
                run_id=run_id,
                project_id=project_id,
                pipeline_version="pipeline-v1",
                input_refs=(ref,),
                output_refs=output_refs,
                status="RUNNING",
                stage=ProcessingStage.METADATA_EXTRACTION.value,
                attempt=1,
                started_at=datetime(2026, 11, 21, tzinfo=UTC),
            ),
            scope=ProjectScope(project_id=project_id, actor_id=uuid4()),
            now=datetime(2026, 11, 21, tzinfo=UTC),
        )

    metadata_outcome = MetadataExtractionHandler(
        FakeObjectReader({ref: Path(_REAL_PATH).read_bytes()}),
        artifacts,
    )(context())

    assert metadata_outcome.status == ProcessingStepStatus.SUCCEEDED.value
    encoded = artifacts.read_bytes(metadata_outcome.output_refs[0], max_bytes=256 * 1024)
    document = json.loads(encoded)
    expected = _FACTS["observed_structure"]
    assert document["format"] == "ROSBAG2"
    assert document["metadata"]["format"] == "rosbag1"
    assert document["metadata"]["topic_count"] == expected["topic_count"]
    assert document["metadata"]["message_count"] == expected["message_count"]
    assert document["metadata"]["details_truncated"] is False
    assert document["metadata"]["omitted_topic_count"] == 0
    assert b"world" not in encoded

    parsing_outcome = FormatParsingHandler(artifacts)(
        context(output_refs=metadata_outcome.output_refs)
    )
    reference = json.loads(
        artifacts.read_bytes(parsing_outcome.output_refs[0], max_bytes=256 * 1024)
    )
    assert reference["canonical_episode"] is False
    assert reference["kind"] == "ROSBAG2_STAGE_ARTIFACT_REFERENCE"
