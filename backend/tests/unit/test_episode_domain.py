"""Episode 领域：状态机、READY 冻结、Manifest 与版本修订。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.episodes.enums import EpisodeQualityStatus, EpisodeStatus
from app.domain.episodes.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
    LineageConflictError,
    ManifestHashMismatchError,
    ManifestSchemaInvalidError,
    ResourceImmutableError,
    TimeBaseInvalidError,
)
from app.domain.episodes.lineage import LineageEdge, validate_supersedes_linear
from app.domain.episodes.manifest import compute_manifest_sha256
from app.domain.episodes.models import Episode, Segmentation

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SHA = f"sha256:{'a' * 64}"


def _time_base() -> dict[str, object]:
    return {
        "time_domain": "UTC",
        "clock_source": "NTP",
        "sync_method": "NTP",
        "sync_status": "VERIFIED",
    }


def _calibration() -> tuple[dict[str, object], ...]:
    return (
        {
            "calibration_id": str(uuid4()),
            "calibration_version": 1,
            "checksum": SHA,
            "target_type": "ROBOT",
            "target_id": str(uuid4()),
            "schema_version": "ddp.calibration/1.0.0",
        },
    )


def _episode(**overrides: object) -> Episode:
    values: dict[str, object] = {
        "episode_id": uuid4(),
        "project_id": uuid4(),
        "code": "EP-001",
        "start_time_ns": 1_000,
        "end_time_ns": 2_000,
        "time_base": _time_base(),
        "canonical_schema_version": "ddp.canonical/1.0.0-draft",
        "pipeline_id": "parser",
        "pipeline_version": "1.0.0",
        "segmentation_rule_version": "seg-manual-v1",
        "calibration_snapshot": _calibration(),
    }
    values.update(overrides)
    return Episode(**values)  # type: ignore[arg-type]


def _manifest(episode: Episode) -> dict[str, object]:
    return {
        "schema_version": "ddp.canonical/1.0.0-draft",
        "episode_id": str(episode.episode_id),
        "episode_lineage_id": str(episode.episode_lineage_id),
        "episode_version": episode.episode_version,
        "project_id": str(episode.project_id),
        "code": episode.code,
        "task_id": None,
        "batch_id": None,
        "robot_id": None,
        "device_ids": [],
        "sensor_ids": [],
        "start_time": str(episode.start_time_ns),
        "end_time": str(episode.end_time_ns),
        "duration_ns": str(episode.duration_ns),
        "time_base": dict(episode.time_base),
        "time_alignment": {"method": "NTP"},
        "calibration_snapshot": [dict(item) for item in episode.calibration_snapshot],
        "streams": [],
        "raw_asset_ids": [],
        "provenance": {"pipeline_id": episode.pipeline_id},
        "quality": {"status": "PASS", "rule_version": "q1", "issues": []},
    }


def _ready_episode() -> Episode:
    episode = _episode()
    processing = episode.start_processing(NOW)
    manifest = _manifest(processing)
    return processing.mark_ready(
        manifest_key=f"canonical/{episode.project_id}/{episode.episode_id}/1/manifest.json",
        manifest=manifest,
        frame_count=30,
        now=NOW,
    )


def test_episode_time_base_and_failure_reason_validation() -> None:
    episode = _episode()
    assert episode.duration_ns == 1_000
    assert episode.status is EpisodeStatus.PENDING

    with pytest.raises(DomainValidationError):
        _episode(start_time_ns=2_000, end_time_ns=2_000)
    with pytest.raises(TimeBaseInvalidError):
        _episode(
            time_base={
                "time_domain": "UTC",
                "clock_source": "NTP",
                "sync_method": "NTP",
                "sync_status": "UNVERIFIED",
            }
        )
    with pytest.raises(DomainValidationError):
        _episode(success=False)


def test_time_base_stays_closed_and_legacy_shape_remains_valid() -> None:
    """历史最小 ``time_base`` 仍可建 Episode；每流映射不得塞进这个封闭对象。

    N4-003 的每流 clock/offset/drift/frame index 明细放在质量证据 JSON，
    不扩 ``time_base``（契约 ``additionalProperties: false``），因此这里
    显式锁住"历史形状不变 + 未声明字段仍被拒绝"。
    """
    legacy = _time_base()
    episode = _episode(time_base=legacy)
    assert dict(episode.time_base) == legacy

    with pytest.raises(TimeBaseInvalidError):
        _episode(time_base={**legacy, "streams": [{"stream_id": "main"}]})


def test_episode_state_machine_rejects_skipping_quality() -> None:
    episode = _episode()
    with pytest.raises(InvalidStateTransitionError):
        episode.begin_quality_check(NOW)
    processing = episode.start_processing(NOW)
    assert processing.status is EpisodeStatus.PROCESSING
    with pytest.raises(InvalidStateTransitionError):
        processing.accept(NOW)


def test_ready_freezes_data_and_manifest_hash() -> None:
    episode = _episode()
    processing = episode.start_processing(NOW)
    manifest = _manifest(processing)
    ready = processing.mark_ready(
        manifest_key=f"canonical/{episode.project_id}/{episode.episode_id}/1/manifest.json",
        manifest=manifest,
        frame_count=30,
        now=NOW,
    )
    assert ready.status is EpisodeStatus.READY
    assert ready.manifest_sha256 == compute_manifest_sha256(manifest)
    ready.assert_manifest_unchanged(manifest)

    tampered = _manifest(ready)
    tampered["streams"] = [{"stream_id": "tampered"}]
    with pytest.raises(ManifestHashMismatchError):
        ready.assert_manifest_unchanged(tampered)

    from dataclasses import replace

    changed = replace(ready, start_time_ns=500)
    with pytest.raises(ResourceImmutableError):
        ready.assert_data_immutable(changed)


def test_accept_freezes_quality_and_revision_keeps_old_version() -> None:
    ready = _ready_episode()
    checking = ready.begin_quality_check(NOW)
    passed = checking.record_quality_result(EpisodeQualityStatus.PASS, NOW)
    accepted = passed.accept(NOW)
    assert accepted.status is EpisodeStatus.ACCEPTED
    with pytest.raises(DomainValidationError):
        accepted.record_quality_result(EpisodeQualityStatus.FAIL, NOW)

    new_id = uuid4()
    revised = accepted.next_revision(episode_id=new_id, code="EP-001-v2", now=NOW)
    assert revised.episode_id == new_id
    assert revised.episode_lineage_id == accepted.episode_lineage_id
    assert revised.episode_version == accepted.episode_version + 1
    assert revised.supersedes_id == accepted.episode_id
    assert revised.status is EpisodeStatus.PENDING
    assert revised.manifest_sha256 is None
    assert accepted.status is EpisodeStatus.ACCEPTED
    assert accepted.manifest_sha256 is not None

    with pytest.raises(DomainValidationError):
        _episode().next_revision(episode_id=uuid4(), code="EP-002", now=NOW)


def test_reject_is_terminal_and_requires_reason() -> None:
    ready = _ready_episode()
    checking = ready.begin_quality_check(NOW)
    with pytest.raises(DomainValidationError):
        checking.reject(reason="", now=NOW)
    rejected = checking.reject(reason="时间不同步", now=NOW)
    assert rejected.status is EpisodeStatus.REJECTED
    with pytest.raises(InvalidStateTransitionError):
        rejected.accept(NOW)
    with pytest.raises(InvalidStateTransitionError):
        rejected.invalidate(reason="无", now=NOW)


def test_segmentation_rules() -> None:
    with pytest.raises(DomainValidationError):
        Segmentation(
            method="PARSER_SEGMENT",
            rule_version="parser-v1",
            start_time_ns=1,
            end_time_ns=2,
        )
    with pytest.raises(DomainValidationError):
        Segmentation(method="MANUAL_TIME_RANGE", rule_version="seg-manual-v1")
    with pytest.raises(DomainValidationError):
        Segmentation(
            method="MANUAL_TIME_RANGE",
            rule_version="seg-manual-v1",
            start_time_ns=1,
            end_time_ns=2,
            allow_overlap=True,
        )
    with pytest.raises(DomainValidationError):
        Segmentation(method="RULE", rule_version="rule-v1", start_time_ns=1, end_time_ns=2)
    rule = Segmentation(
        method="RULE",
        rule_version="rule-v1",
        start_time_ns=1,
        end_time_ns=2,
        rule_parameters={"window": 10},
    )
    assert rule.range == (1, 2)


def test_manifest_rejects_self_hash_and_float() -> None:
    episode = _episode()
    manifest = _manifest(episode)
    with pytest.raises(ManifestSchemaInvalidError):
        compute_manifest_sha256({**manifest, "manifest_sha256": SHA})
    with pytest.raises(ManifestSchemaInvalidError):
        compute_manifest_sha256({**manifest, "drift": 1.5})


def _edge(
    *,
    project_id: object,
    from_type: str,
    from_id: object,
    to_type: str,
    to_id: object,
    relation: str = "SUPERSEDES",
) -> LineageEdge:
    return LineageEdge(
        edge_id=uuid4(),
        project_id=project_id,  # type: ignore[arg-type]
        from_node_type=from_type,
        from_node_id=from_id,  # type: ignore[arg-type]
        to_node_type=to_type,
        to_node_id=to_id,  # type: ignore[arg-type]
        relation=relation,
        metadata={},
        created_by=uuid4(),
        created_at=NOW,
    )


def test_lineage_endpoint_validation_and_supersedes_linearity() -> None:
    project_id = uuid4()
    with pytest.raises(LineageConflictError):
        _edge(
            project_id=project_id,
            from_type="RAW_ASSET",
            from_id=uuid4(),
            to_type="RAW_ASSET",
            to_id=uuid4(),
            relation="PRODUCED",
        )
    with pytest.raises(LineageConflictError):
        _edge(
            project_id=project_id,
            from_type="EPISODE",
            from_id=uuid4(),
            to_type="EPISODE",
            to_id=uuid4(),
            relation="SEGMENTED_INTO",
        )

    episode_a, episode_b = uuid4(), uuid4()
    edge = _edge(
        project_id=project_id,
        from_type="EPISODE",
        from_id=episode_b,
        to_type="EPISODE",
        to_id=episode_a,
    )
    validate_supersedes_linear([], edge)
    with pytest.raises(LineageConflictError):
        validate_supersedes_linear([edge], edge)

    cycle = _edge(
        project_id=project_id,
        from_type="EPISODE",
        from_id=episode_a,
        to_type="EPISODE",
        to_id=episode_b,
    )
    with pytest.raises(LineageConflictError):
        validate_supersedes_linear([edge], cycle)
