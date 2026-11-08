"""ExportJob 领域单测：状态机、幂等、超时/重试/取消、PARTIAL 与产物 Hash。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
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
    assert job.allowed_actions == frozenset({"worker-start", "cancel"})

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
        "meta/ddp_divisions.json",
        "ddp/sidecar.json",
    }
    assert all(item.size_bytes == len(item.data) for item in first)

    aggregate = aggregate_sha256(first)
    assert aggregate == aggregate_sha256(second)
    manifest = build_artifact_manifest(job, first, aggregate=aggregate)
    assert manifest["artifact_count"] == 4
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
