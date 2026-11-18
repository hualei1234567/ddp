"""ExportJob 领域单测：状态机、幂等、超时/重试/取消、PARTIAL 与产物 Hash。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.domain.datasets.canonical import sha256_text
from app.domain.datasets.enums import (
    DatasetVersionStatus,
    ExportJobStatus,
    ExportVerificationStatus,
    Split,
    ValidityStatus,
)
from app.domain.datasets.errors import (
    ExportConfigInvalidError,
    ExportFormatUnsupportedError,
    ExportFormatVersionUnsupportedError,
    ExportJobNotRetryableError,
    ExportTimeoutError,
    InvalidStateTransitionError,
)
from app.domain.datasets.export_jobs import (
    ExportJob,
    new_export_job,
)
from app.domain.datasets.exporting import (
    EpisodeExportSource,
    EpisodeMediaRef,
    ExportArtifactSpec,
    aggregate_sha256,
    build_artifact_manifest,
    build_export_artifacts,
)
from app.domain.datasets.models import DatasetItem, DatasetVersion
from app.domain.datasets.policies import PolicyRef

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
PROJECT = UUID(int=1)


def _ref(seed: str) -> PolicyRef:
    return PolicyRef(
        policy_id=UUID(int=int(seed, 16)),
        version=1,
        policy_sha256=SHA,
    )


def make_job(**overrides: object) -> ExportJob:
    values: dict[str, object] = {
        "export_job_id": UUID(int=10),
        "project_id": PROJECT,
        "dataset_version_id": UUID(int=20),
        "format": "LE_ROBOT",
        "format_version": "ddp.lerobot/1.0.0",
        "exporter_id": "ddp.lerobot-metadata",
        "exporter_version": "1.0.0",
        "mapping_version": "ddp.lerobot/1.0.0",
        "config": {"chunk_size": 8, "include_sidecar": True},
        "idempotency_key": "export-key-0001",
        "requested_by": UUID(int=30),
        "output_prefix": "exports/p/20/10/",
        "now": NOW,
        "timeout_seconds": 60,
        "max_attempts": 3,
    }
    values.update(overrides)
    return new_export_job(**values)  # type: ignore[arg-type]


def make_version() -> DatasetVersion:
    return DatasetVersion(
        dataset_version_id=UUID(int=20),
        project_id=PROJECT,
        dataset_id=UUID(int=21),
        dataset_lineage_id=UUID(int=22),
        dataset_version=1,
        selection_rule_version="dataset-selection/1",
        selection_filters={},
        selection_snapshot_at=NOW,
        dedup_policy_ref=_ref("31"),
        split_policy_ref=_ref("32"),
        leakage_policy_ref=_ref("33"),
        quality_policy_ref=_ref("34"),
        status=DatasetVersionStatus.FROZEN,
        validity_status=ValidityStatus.VALID,
        item_count=3,
        train_count=1,
        validation_count=1,
        test_count=1,
        episode_version_digest=SHA,
        manifest_sha256=SHA_B,
        quality_report_sha256=SHA_C,
        leakage_report_sha256=SHA,
        frozen_at=NOW,
        frozen_by=UUID(int=30),
        freeze_approval_id=UUID(int=40),
        created_at=NOW,
        updated_at=NOW,
        created_by=UUID(int=30),
        updated_by=UUID(int=30),
    )


def make_item(index: int, split: Split) -> DatasetItem:
    return DatasetItem(
        dataset_item_id=UUID(int=100 + index),
        project_id=PROJECT,
        dataset_version_id=UUID(int=20),
        episode_id=UUID(int=200 + index),
        episode_version=1,
        episode_lineage_id=UUID(int=300 + index),
        manifest_sha256=SHA,
        raw_asset_ids=(UUID(int=400 + index),),
        annotation_submission_id=UUID(int=500 + index),
        annotation_version=1,
        annotation_record_digest=SHA_B,
        schema_id=UUID(int=600 + index),
        schema_version=1,
        schema_sha256=SHA_C,
        split=split,
        created_at=NOW,
    )


def test_export_job_lifecycle_retry_cancel_and_partial() -> None:
    job = make_job()
    assert job.status is ExportJobStatus.QUEUED
    assert job.attempt == 0
    assert job.config_sha256.startswith("sha256:")
    assert job.verification_status is ExportVerificationStatus.UNVERIFIED
    # 对外动作只含用户能执行的：worker-* 是后台推进用的系统动作。
    assert job.allowed_actions == frozenset({"cancel"})

    started = job.start(NOW + timedelta(seconds=1))
    assert started.status is ExportJobStatus.RUNNING
    assert started.attempt == 1
    assert started.started_at is not None

    failed = started.fail(
        now=NOW + timedelta(seconds=2),
        error_code="export_job_failed",
        error_summary="对象存储不可用",
    )
    assert failed.status is ExportJobStatus.FAILED
    assert failed.can_retry
    assert failed.allowed_actions == frozenset({"retry"})

    retried = failed.retry(NOW + timedelta(seconds=3))
    assert retried.status is ExportJobStatus.QUEUED
    assert retried.attempt == 2
    assert retried.error_code is None
    assert retried.started_at is None

    second = retried.start(NOW + timedelta(seconds=4))
    partial = second.partial(
        now=NOW + timedelta(seconds=5),
        error_code="export_partial",
        error_summary="部分产物写入失败",
        artifact_count=2,
        total_bytes=100,
    )
    assert partial.status is ExportJobStatus.PARTIAL
    assert partial.is_terminal
    assert partial.allowed_actions == frozenset()
    with pytest.raises(InvalidStateTransitionError):
        partial.retry(NOW + timedelta(seconds=6))
    with pytest.raises(InvalidStateTransitionError):
        partial.succeed(
            now=NOW + timedelta(seconds=6),
            artifact_count=4,
            total_bytes=200,
            artifact_manifest_key="exports/manifest.json",
            artifact_manifest_sha256=SHA,
            aggregate_sha256=SHA_B,
        )


def test_export_job_max_attempts_cancel_and_timeout() -> None:
    job = make_job(max_attempts=1)
    running = job.start(NOW)
    failed = running.fail(
        now=NOW + timedelta(seconds=1),
        error_code="export_job_failed",
        error_summary="失败",
    )
    assert not failed.can_retry
    with pytest.raises(ExportJobNotRetryableError):
        failed.retry(NOW + timedelta(seconds=2))

    cancelled = make_job().cancel(NOW + timedelta(seconds=1))
    assert cancelled.status is ExportJobStatus.CANCELLED
    assert cancelled.cancel_requested_at is not None
    assert cancelled.finished_at is not None

    running_cancel = make_job().start(NOW).cancel(NOW + timedelta(seconds=1))
    assert running_cancel.status is ExportJobStatus.CANCELLED

    running_job = make_job().start(NOW)
    with pytest.raises(ExportTimeoutError):
        running_job.fail_timeout(NOW + timedelta(seconds=30))
    timed_out = running_job.fail_timeout(NOW + timedelta(seconds=61))
    assert timed_out.status is ExportJobStatus.FAILED
    assert timed_out.error_code == "export_job_timeout"


def test_export_idempotency_and_format_validation() -> None:
    first = make_job()
    same = make_job(export_job_id=UUID(int=11), output_prefix="exports/p/20/11/")
    assert first.matches_idempotent_request(same)
    different = make_job(config={"chunk_size": 16})
    assert not first.matches_idempotent_request(different)

    with pytest.raises(ExportFormatUnsupportedError):
        make_job(format="PARQUET")
    with pytest.raises(ExportFormatVersionUnsupportedError):
        make_job(format_version="ddp.lerobot/9.9.9")
    with pytest.raises(ExportConfigInvalidError):
        make_job(config={"api_token": "should-not-be-stored"})
    with pytest.raises(InvalidStateTransitionError):
        make_job().succeed(
            now=NOW,
            artifact_count=1,
            total_bytes=1,
            artifact_manifest_key="m",
            artifact_manifest_sha256=SHA,
            aggregate_sha256=SHA,
        )


def test_lerobot_metadata_artifacts_are_deterministic_and_hashed() -> None:
    job = make_job()
    version = make_version()
    items = (
        make_item(2, Split.TEST),
        make_item(0, Split.TRAIN),
        make_item(1, Split.VALIDATION),
    )
    first = build_export_artifacts(job, version, items, created_at=NOW)
    second = build_export_artifacts(job, version, reversed(items), created_at=NOW)
    assert [item.path for item in first] == [item.path for item in second]
    assert [item.sha256 for item in first] == [item.sha256 for item in second]
    assert all(item.sha256.startswith("sha256:") for item in first)
    assert {item.path for item in first} == {
        "meta/info.json",
        "meta/episodes.jsonl",
        "meta/media.jsonl",
        "data/episodes.jsonl",
        "meta/ddp_divisions.json",
        "ddp/sidecar.json",
    }
    assert all(item.size_bytes == len(item.data) for item in first)

    aggregate = aggregate_sha256(first)
    assert aggregate == aggregate_sha256(second)
    manifest = build_artifact_manifest(job, first, aggregate=aggregate)
    assert manifest["artifact_count"] == 6
    assert manifest["total_bytes"] == sum(item.size_bytes for item in first)
    assert manifest["aggregate_sha256"] == aggregate
    assert manifest["verification_status"] == "UNVERIFIED"
    entries = manifest["artifacts"]
    assert isinstance(entries, list)
    assert {entry["path"] for entry in entries} == {item.path for item in first}

    divisions = next(item for item in first if item.path.endswith("divisions.json"))
    assert b'"train"' in divisions.data
    assert b'"validation"' in divisions.data
    assert b'"test"' in divisions.data
    sidecar = next(item for item in first if item.path == "ddp/sidecar.json")
    assert b'"frame_level_mapping":false' in sidecar.data
    assert b'"verification_status":"UNVERIFIED"' in sidecar.data


# ---- 训练输入索引：meta/media.jsonl 与 data/episodes.jsonl ----


def _label(key: str, value: object) -> dict[str, object]:
    return {
        "label_key": key,
        "value": value,
        "source": "HUMAN",
        "label_status": "CONFIRMED",
    }


def _media(
    index: int,
    role: str,
    *,
    asset_index: int | None = None,
    duration_ns: int | None = 10_000,
) -> EpisodeMediaRef:
    return EpisodeMediaRef(
        episode_id=UUID(int=200 + index),
        role=role,
        object_key=f"raw/p/2026/11/17/{index}/{role}.mp4",
        sha256=SHA,
        size_bytes=1024 + index,
        duration_ns=duration_ns,
        asset_id=UUID(int=400 + (index if asset_index is None else asset_index)),
        content_type="video/mp4",
    )


def _source_records(artifacts: tuple[ExportArtifactSpec, ...]) -> dict[str, list[dict]]:
    return {
        artifact.path: [
            json.loads(line) for line in artifact.data.decode("utf-8").splitlines() if line
        ]
        for artifact in artifacts
    }


def test_media_index_and_training_table_carry_real_locations_and_labels() -> None:
    job = make_job()
    version = make_version()
    items = (make_item(0, Split.TRAIN), make_item(1, Split.TEST))
    sources = (
        EpisodeExportSource(
            episode_id=UUID(int=200),
            duration_ns=10_000,
            success=False,
            labels=(
                _label("episode_outcome", "success"),
                _label("action_segment", "grasp"),
                _label("quality_score", 0.86),
                _label("scene_notes", "台面反光"),
            ),
            media=(
                _media(0, "wrist"),
                _media(0, "main"),
                # 不在条目冻结资产内的媒体不得混进产物。
                _media(0, "leaked", asset_index=9),
            ),
            annotation_submission_id=UUID(int=500),
            annotation_version=3,
        ),
        # 该 Episode 只有成功标记，没有任何已批准标签。
        EpisodeExportSource(episode_id=UUID(int=201), duration_ns=20_000, success=True),
    )

    artifacts = build_export_artifacts(job, version, items, created_at=NOW, sources=sources)
    rows = _source_records(artifacts)

    media_rows = rows["meta/media.jsonl"]
    assert [(row["episode_id"], row["role"]) for row in media_rows] == [
        (str(UUID(int=200)), "main"),
        (str(UUID(int=200)), "wrist"),
    ]
    first_media = media_rows[0]
    assert first_media["object_key"] == "raw/p/2026/11/17/0/main.mp4"
    assert first_media["sha256"] == SHA
    assert first_media["size_bytes"] == 1024
    assert first_media["duration_ns"] == 10_000
    assert first_media["content_type"] == "video/mp4"
    assert first_media["asset_id"] == str(UUID(int=400))

    training = rows["data/episodes.jsonl"]
    assert len(training) == 2
    labelled = training[0]
    assert labelled["episode_id"] == str(UUID(int=200))
    assert labelled["split"] == Split.TRAIN.value
    assert labelled["action"] == "grasp"
    assert labelled["success"] is True  # 已批准标签优先于 Episode 自身的 success=False
    assert labelled["quality_score"] == 0.86
    assert labelled["duration_ns"] == 10_000
    assert labelled["media_keys"] == [
        "raw/p/2026/11/17/0/main.mp4",
        "raw/p/2026/11/17/0/wrist.mp4",
    ]
    assert labelled["annotation_submission_id"] == str(UUID(int=500))
    assert labelled["annotation_version"] == 3
    assert [item["label_key"] for item in labelled["labels"]] == [
        "episode_outcome",
        "action_segment",
        "quality_score",
        "scene_notes",
    ]

    unlabelled = training[1]
    assert unlabelled["action"] is None
    assert unlabelled["quality_score"] is None
    assert unlabelled["labels"] == []
    assert unlabelled["media_keys"] == []
    assert unlabelled["annotation_submission_id"] is None
    # 没有标注时退回 Episode 自身的事实列，不编造标签。
    assert unlabelled["success"] is True
    assert unlabelled["duration_ns"] == 20_000


def test_training_table_is_empty_but_valid_without_sources() -> None:
    job = make_job()
    version = make_version()
    items = (make_item(0, Split.TRAIN),)

    artifacts = build_export_artifacts(job, version, items, created_at=NOW)
    rows = _source_records(artifacts)
    assert rows["meta/media.jsonl"] == []
    training = rows["data/episodes.jsonl"]
    assert len(training) == 1
    assert training[0]["success"] is None
    assert training[0]["action"] is None
    assert training[0]["quality_score"] is None
    assert training[0]["duration_ns"] is None
    assert training[0]["media_keys"] == []
    assert training[0]["labels"] == []

    media = next(item for item in artifacts if item.path == "meta/media.jsonl")
    assert media.size_bytes == 0
    assert media.sha256 == sha256_text("")


def test_ambiguous_multi_segment_action_is_left_empty() -> None:
    job = make_job()
    version = make_version()
    items = (make_item(0, Split.TRAIN),)
    sources = (
        EpisodeExportSource(
            episode_id=UUID(int=200),
            labels=(
                _label("action_segment", "grasp"),
                _label("action_segment", "place"),
                _label("quality_score", True),
            ),
        ),
    )

    training = _source_records(
        build_export_artifacts(job, version, items, created_at=NOW, sources=sources)
    )["data/episodes.jsonl"]
    # 多段动作不猜"第一条"，完整标签仍在 labels 里。
    assert training[0]["action"] is None
    assert [
        item["value"] for item in training[0]["labels"] if item["label_key"] == "action_segment"
    ] == [
        "grasp",
        "place",
    ]
    # 布尔值不得冒充质量评分。
    assert training[0]["quality_score"] is None


def test_new_artifacts_change_aggregate_and_manifest_entries() -> None:
    job = make_job()
    version = make_version()
    items = (make_item(0, Split.TRAIN),)
    baseline = build_export_artifacts(job, version, items, created_at=NOW)
    labelled = build_export_artifacts(
        job,
        version,
        items,
        created_at=NOW,
        sources=(
            EpisodeExportSource(
                episode_id=UUID(int=200),
                labels=(_label("episode_outcome", "success"),),
            ),
        ),
    )
    assert aggregate_sha256(baseline) != aggregate_sha256(labelled)

    manifest = build_artifact_manifest(
        job,
        labelled,
        aggregate=aggregate_sha256(labelled),
    )
    entries = {entry["path"]: entry for entry in manifest["artifacts"]}
    assert entries["meta/media.jsonl"]["content_type"] == "application/x-ndjson"
    assert entries["data/episodes.jsonl"]["content_type"] == "application/x-ndjson"
    for path in ("meta/media.jsonl", "data/episodes.jsonl"):
        artifact = next(item for item in labelled if item.path == path)
        assert entries[path]["sha256"] == artifact.sha256
        assert entries[path]["size_bytes"] == artifact.size_bytes


def test_media_ref_rejects_bad_location_and_hash() -> None:
    with pytest.raises(ValueError):
        EpisodeMediaRef(
            episode_id=UUID(int=200),
            role="",
            object_key="raw/a.mp4",
            sha256=SHA,
            size_bytes=1,
        )
    with pytest.raises(ValueError):
        EpisodeMediaRef(
            episode_id=UUID(int=200),
            role="main",
            object_key="raw/a.mp4",
            sha256="not-a-sha",
            size_bytes=1,
        )
    with pytest.raises(ValueError):
        EpisodeMediaRef(
            episode_id=UUID(int=200),
            role="main",
            object_key="raw/a.mp4",
            sha256=SHA,
            size_bytes=0,
        )
    with pytest.raises(ValueError):
        EpisodeMediaRef(
            episode_id=UUID(int=200),
            role="main",
            object_key="../escape.mp4",
            sha256=SHA,
            size_bytes=1,
        )
