"""``METADATA_EXTRACTION`` / ``FORMAT_PARSING`` 阶段（DDP-V21-N4-002 增量）。

覆盖：正常（MP4/Parquet/MCAP 各一）、未知格式、损坏文件、超限、脱敏、
空输入显式 SKIPPED、跨项目引用拒绝、产物可回查、FORMAT_PARSING 不伪造
Episode 且缺元数据时显式 SKIPPED。

真实解析器（ffprobe / PyArrow / mcap SDK）在此被真实调用；ffprobe 通过
``ddp_data.media.mp4`` 的既有测试夹具替换为可控子进程输出，不依赖 CI 安装
FFmpeg。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.application.processing.metadata_stage import (
    METADATA_SCHEMA_VERSION,
    REFERENCE_SCHEMA_VERSION,
    FormatParsingHandler,
    MetadataExtractionHandler,
    canonical_artifact_key,
    metadata_artifact_key,
)
from app.application.processing.stages import StageContext, StageFailure
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import ProcessingStage, ProcessingStepStatus
from app.domain.processing.models import ProcessingRun

from tests.unit.processing_stage_support import FakeArtifactStore, FakeObjectReader

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
_PROJECT_ID = uuid4()
_RUN_ID = uuid4()
_SCOPE = ProjectScope(project_id=_PROJECT_ID, actor_id=uuid4())

_FTYP_HEADER = b"\x00\x00\x00\x18ftypmp42"
_PROBE_JSON = json.dumps(
    {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30000/1001",
                "r_frame_rate": "30/1",
                "duration": "12.345",
                "nb_frames": "370",
            }
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "12.345",
            "size": "999",
        },
    }
).encode()

_PARQUET_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "parquet"


def _run(input_refs: tuple[str, ...], *, output_refs: tuple[str, ...] = ()) -> ProcessingRun:
    return ProcessingRun(
        run_id=_RUN_ID,
        project_id=_PROJECT_ID,
        pipeline_version="pipeline-v1",
        input_refs=input_refs,
        output_refs=output_refs,
        status="RUNNING",
        stage=ProcessingStage.METADATA_EXTRACTION.value,
        attempt=1,
        started_at=NOW,
    )


def _context(
    input_refs: tuple[str, ...] | None = None,
    *,
    output_refs: tuple[str, ...] = (),
) -> StageContext:
    # 领域层要求 input_refs 非空；未显式传入时给一个占位引用，仅用于
    # FORMAT_PARSING 这类只依赖 output_refs 的用例。
    refs = input_refs if input_refs is not None else (f"raw/{_PROJECT_ID}/placeholder",)
    return StageContext(
        run=_run(refs, output_refs=output_refs),
        scope=_SCOPE,
        now=NOW,
    )


def _mp4_bytes() -> bytes:
    return _FTYP_HEADER + b"\x00" * 256


@pytest.fixture(autouse=True)
def _resolve_ffprobe_to_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 ffprobe 解析为可执行路径，具体输出由各用例替换 ``subprocess.run``。"""
    from ddp_data.media import mp4 as mp4_module

    monkeypatch.setattr(
        mp4_module.shutil,
        "which",
        lambda name: sys.executable if name == "ffprobe" else None,
    )


def _patch_ffprobe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: bytes = _PROBE_JSON,
    returncode: int = 0,
) -> None:
    from ddp_data.media import mp4 as mp4_module

    monkeypatch.setattr(
        mp4_module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["ffprobe"], returncode=returncode, stdout=stdout, stderr=b""
        ),
    )


def _handler(
    reader: FakeObjectReader,
    artifacts: FakeArtifactStore,
    *,
    max_object_bytes: int = 1024 * 1024,
    max_artifact_bytes: int = 256 * 1024,
    max_objects: int = 200,
) -> MetadataExtractionHandler:
    return MetadataExtractionHandler(
        reader,
        artifacts,
        max_object_bytes=max_object_bytes,
        max_artifact_bytes=max_artifact_bytes,
        max_objects=max_objects,
    )


# ---------------------------------------------------------------- 正常路径


def test_extracts_mp4_metadata_and_writes_bounded_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ffprobe(monkeypatch)
    ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.mp4"
    reader = FakeObjectReader({ref: _mp4_bytes()})
    artifacts = FakeArtifactStore()

    outcome = _handler(reader, artifacts)(_context((ref,)))

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    key = metadata_artifact_key(_PROJECT_ID, _RUN_ID, 0, "MP4")
    assert outcome.output_refs == (key,)
    document = json.loads(artifacts.read_bytes(key, max_bytes=256 * 1024))
    assert document["schema_version"] == METADATA_SCHEMA_VERSION
    assert document["format"] == "MP4"
    assert document["source_ref"] == ref
    assert document["metadata"]["width"] == 1280
    assert document["metadata"]["duration_ms"] == 12345
    # 产物不得包含临时目录绝对路径或 ffprobe 原始输出（adapter_id 是稳定标识）。
    serialized = json.dumps(document, ensure_ascii=False)
    assert tempfile.gettempdir() not in serialized
    assert "Traceback" not in serialized
    assert "stderr" not in serialized


def test_extracts_parquet_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _PARQUET_FIXTURES / "lerobot_v21_data.parquet"
    ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.parquet"
    reader = FakeObjectReader({ref: source.read_bytes()})
    artifacts = FakeArtifactStore()

    outcome = _handler(reader, artifacts)(_context((ref,)))

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    document = json.loads(artifacts.read_bytes(outcome.output_refs[0], max_bytes=256 * 1024))
    assert document["format"] == "PARQUET"
    assert document["metadata"]["layout"] == "lerobot_tabular"
    assert document["metadata"]["num_rows"] > 0
    assert "episode_index" in document["metadata"]["top_level_fields"]


def test_extracts_mcap_metadata(tmp_path: Path) -> None:
    from mcap.writer import CompressionType, Writer

    path = tmp_path / "sample.mcap"
    with path.open("wb") as stream:
        writer = Writer(stream, compression=CompressionType.NONE)
        writer.start(profile="robot/teleop", library="ddp-test")
        schema_id = writer.register_schema(
            name="pkg/CameraFrame", encoding="jsonschema", data=b'{"type":"object"}'
        )
        channel_id = writer.register_channel(
            topic="/camera/front", message_encoding="json", schema_id=schema_id
        )
        writer.add_message(
            channel_id=channel_id,
            log_time=1,
            publish_time=1,
            data=b"{}",
        )
        writer.finish()

    ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.mcap"
    reader = FakeObjectReader({ref: path.read_bytes()})
    artifacts = FakeArtifactStore()

    outcome = _handler(reader, artifacts)(_context((ref,)))

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    document = json.loads(artifacts.read_bytes(outcome.output_refs[0], max_bytes=256 * 1024))
    assert document["format"] == "MCAP"
    assert document["metadata"]["channel_count"] == 1
    assert document["metadata"]["channels"][0]["topic"] == "/camera/front"


def test_all_unsupported_formats_are_skipped_with_reason() -> None:
    ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.bin"
    reader = FakeObjectReader({ref: b"\x00\x01\x02\x03 not a known container"})

    outcome = _handler(reader, FakeArtifactStore())(_context((ref,)))

    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert "跳过" in outcome.log_summary
    assert "MP4/Parquet/MCAP" in outcome.log_summary


# ---------------------------------------------------------------- 未知格式


def test_unknown_format_is_skipped_with_reason_not_success() -> None:
    """无法识别格式时显式 SKIPPED 并写原因，绝不默认成功。"""
    ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.bin"
    reader = FakeObjectReader({ref: b"\x00\x01\x02\x03 not a known container"})

    outcome = _handler(reader, FakeArtifactStore())(_context((ref,)))

    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert "UNKNOWN" in outcome.log_summary
    assert outcome.output_refs == ()


def test_mixed_unknown_format_fails_whole_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ffprobe(monkeypatch)
    mp4_ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.mp4"
    bin_ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.bin"
    reader = FakeObjectReader({mp4_ref: _mp4_bytes(), bin_ref: b"\x00\x01\x02\x03"})
    artifacts = FakeArtifactStore()

    with pytest.raises(StageFailure) as excinfo:
        _handler(reader, artifacts)(_context((mp4_ref, bin_ref)))

    assert excinfo.value.error_code == "metadata_format_unsupported"
    # 先算完再写：失败时不得留下任何产物。
    assert artifacts.keys() == []


# ---------------------------------------------------------------- 损坏文件


def test_corrupt_mp4_is_permanent_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ffprobe(monkeypatch, returncode=1)
    ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.mp4"
    reader = FakeObjectReader({ref: _mp4_bytes()})

    with pytest.raises(StageFailure) as excinfo:
        _handler(reader, FakeArtifactStore())(_context((ref,)))

    assert excinfo.value.error_code == "metadata_probe_failed"
    assert excinfo.value.retryable is False


def test_corrupt_parquet_is_permanent_failure() -> None:
    ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.parquet"
    # 首尾魔数合法但 footer 长度与内容不符：解析器必须显式失败。
    reader = FakeObjectReader({ref: b"PAR1" + b"\x00" * 4 + b"\xff\xff\xff\x7fPAR1"})

    with pytest.raises(StageFailure) as excinfo:
        _handler(reader, FakeArtifactStore())(_context((ref,)))

    assert excinfo.value.error_code in {
        "metadata_probe_failed",
        "metadata_limit_exceeded",
    }
    assert excinfo.value.retryable is False


def test_truncated_mcap_is_permanent_failure() -> None:
    ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.mcap"
    reader = FakeObjectReader({ref: b"\x89MCAP0\r\n" + b"\x00" * 32})

    with pytest.raises(StageFailure) as excinfo:
        _handler(reader, FakeArtifactStore())(_context((ref,)))

    assert excinfo.value.error_code == "metadata_probe_failed"
    assert excinfo.value.retryable is False


# ---------------------------------------------------------------- 超限


def test_object_exceeding_limit_is_rejected_before_download() -> None:
    ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.mp4"
    reader = FakeObjectReader({ref: b"\x00" * 4096})

    with pytest.raises(StageFailure) as excinfo:
        _handler(reader, FakeArtifactStore(), max_object_bytes=1024)(_context((ref,)))

    assert excinfo.value.error_code == "object_exceeds_limit"
    assert excinfo.value.retryable is False
    # 超限在下载前判定：不得把对象复制到临时盘。
    assert reader.download_calls == []


def test_too_many_objects_is_rejected() -> None:
    refs = tuple(f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.mp4" for _ in range(3))
    reader = FakeObjectReader({ref: _mp4_bytes() for ref in refs})

    with pytest.raises(StageFailure) as excinfo:
        _handler(reader, FakeArtifactStore(), max_objects=2)(_context(refs))

    assert excinfo.value.error_code == "metadata_object_limit_exceeded"
    assert excinfo.value.retryable is False


def test_empty_object_is_rejected() -> None:
    ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.mp4"
    reader = FakeObjectReader({ref: b""})

    with pytest.raises(StageFailure) as excinfo:
        _handler(reader, FakeArtifactStore())(_context((ref,)))

    assert excinfo.value.error_code == "object_empty"
    assert excinfo.value.retryable is False


# ---------------------------------------------------------------- 隔离与脱敏


def test_cross_project_reference_is_rejected_without_storage_access() -> None:
    other = uuid4()
    ref = f"raw/{other}/2026/11/19/{uuid4()}/content.mp4"
    reader = FakeObjectReader({ref: _mp4_bytes()})

    with pytest.raises(StageFailure) as excinfo:
        _handler(reader, FakeArtifactStore())(_context((ref,)))

    assert excinfo.value.error_code == "input_ref_not_project_scoped"
    assert excinfo.value.retryable is False
    assert reader.size_calls == []


def test_missing_object_is_permanent_failure() -> None:
    ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.mp4"

    with pytest.raises(StageFailure) as excinfo:
        _handler(FakeObjectReader(), FakeArtifactStore())(_context((ref,)))

    assert excinfo.value.error_code == "object_missing"
    assert excinfo.value.retryable is False


# ---------------------------------------------------------------- FORMAT_PARSING


def _metadata_document(
    *,
    source_ref: str,
    raw_format: str,
    metadata: dict[str, object],
) -> bytes:
    return json.dumps(
        {
            "schema_version": METADATA_SCHEMA_VERSION,
            "source_ref": source_ref,
            "format": raw_format,
            "adapter_id": "test",
            "adapter_version": "1.0.0",
            "object_size_bytes": 1024,
            "metadata": metadata,
        },
        ensure_ascii=False,
    ).encode()


def test_format_parsing_projects_mp4_to_canonical_video_stream() -> None:
    source_ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.mp4"
    metadata_ref = metadata_artifact_key(_PROJECT_ID, _RUN_ID, 0, "MP4")
    artifacts = FakeArtifactStore()
    artifacts.put_bytes(
        metadata_ref,
        _metadata_document(
            source_ref=source_ref,
            raw_format="MP4",
            metadata={
                "duration_ms": 12345,
                "fps": 29.97,
                "width": 1280,
                "height": 720,
                "frame_count": 370,
                "video_codec": "h264",
            },
        ),
    )

    outcome = FormatParsingHandler(artifacts)(_context((source_ref,), output_refs=(metadata_ref,)))

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    key = canonical_artifact_key(_PROJECT_ID, _RUN_ID, 0, kind="video-stream")
    assert outcome.output_refs == (key,)
    document = json.loads(artifacts.read_bytes(key, max_bytes=256 * 1024))
    assert document["schema_version"] == "ddp.canonical/1.0.0"
    assert document["duration_ns"] == "12345000000"
    assert document["width"] == 1280
    assert document["storage_reference"] == source_ref
    # 孤立 MP4 无法提供的字段必须显式登记缺失，而不是补默认值。
    assert "camera_name" in document["missing_fields"]
    assert "checksum" in document["missing_fields"]


def test_format_parsing_keeps_parquet_as_stage_reference_without_fake_episode() -> None:
    source_ref = f"raw/{_PROJECT_ID}/2026/11/19/{uuid4()}/content.parquet"
    metadata_ref = metadata_artifact_key(_PROJECT_ID, _RUN_ID, 0, "PARQUET")
    artifacts = FakeArtifactStore()
    artifacts.put_bytes(
        metadata_ref,
        _metadata_document(
            source_ref=source_ref,
            raw_format="PARQUET",
            metadata={"num_rows": 10, "layout": "generic_table"},
        ),
    )

    outcome = FormatParsingHandler(artifacts)(_context((source_ref,), output_refs=(metadata_ref,)))

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    document = json.loads(artifacts.read_bytes(outcome.output_refs[0], max_bytes=256 * 1024))
    assert document["schema_version"] == REFERENCE_SCHEMA_VERSION
    assert document["canonical_episode"] is False
    assert "episode" not in document
    assert "未伪造 Episode" in outcome.log_summary


def test_format_parsing_without_metadata_is_skipped() -> None:
    outcome = FormatParsingHandler(FakeArtifactStore())(_context(output_refs=()))

    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert "METADATA_EXTRACTION" in outcome.log_summary


def test_format_parsing_rejects_corrupt_artifact() -> None:
    metadata_ref = metadata_artifact_key(_PROJECT_ID, _RUN_ID, 0, "MP4")
    artifacts = FakeArtifactStore()
    artifacts.put_bytes(metadata_ref, b"not json")

    with pytest.raises(StageFailure) as excinfo:
        FormatParsingHandler(artifacts)(_context(output_refs=(metadata_ref,)))

    assert excinfo.value.error_code == "metadata_artifact_invalid"
    assert excinfo.value.retryable is False


def test_format_parsing_missing_artifact_is_retryable() -> None:
    metadata_ref = metadata_artifact_key(_PROJECT_ID, _RUN_ID, 0, "MP4")

    with pytest.raises(StageFailure) as excinfo:
        FormatParsingHandler(FakeArtifactStore())(_context(output_refs=(metadata_ref,)))

    assert excinfo.value.error_code == "metadata_artifact_missing"
    assert excinfo.value.retryable is True


def test_format_parsing_ignores_other_runs_artifacts() -> None:
    other_run = uuid4()
    foreign = metadata_artifact_key(_PROJECT_ID, other_run, 0, "MP4")
    artifacts = FakeArtifactStore()
    artifacts.put_bytes(foreign, b"{}")

    outcome = FormatParsingHandler(artifacts)(_context(output_refs=(foreign,)))

    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert artifacts.read_calls == []


# ---------------------------------------------------------------- 引用格式


def test_artifact_keys_are_project_and_run_scoped() -> None:
    project_id: UUID = _PROJECT_ID
    run_id: UUID = _RUN_ID
    assert metadata_artifact_key(project_id, run_id, 3, "MCAP") == (
        f"processing-run/{project_id}/{run_id}/metadata/0003-mcap.json"
    )
    assert canonical_artifact_key(project_id, run_id, 7, kind="reference") == (
        f"processing-run/{project_id}/{run_id}/canonical/0007-reference.json"
    )
