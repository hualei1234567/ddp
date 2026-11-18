"""Episode 自动就绪判定与 Canonical Manifest 生成（领域单测，纯函数）。

锁定的行为：

1. 生成器只用**给定事实**产出 Manifest：同样的输入必然得到同样的字节；
2. Manifest 必须满足 ``MANIFEST_REQUIRED_KEYS`` 且通过领域自身的身份校验；
3. 每一条拒绝原因都必须**显式登记**：判不了不等于通过；
4. ``Episode`` 的阻塞原因字段有状态不变量（READY 之后不得残留）。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.domain.episodes.enums import EpisodeStatus
from app.domain.episodes.errors import DomainValidationError
from app.domain.episodes.manifest import (
    MANIFEST_REQUIRED_KEYS,
    assert_manifest_hash,
    compute_manifest_sha256,
    validate_manifest_identity,
)
from app.domain.episodes.models import Episode
from app.domain.episodes.readiness import evaluate_readiness
from app.domain.episodes.readiness_facts import EpisodeManifestFacts, EpisodeStream
from app.domain.episodes.readiness_result import (
    ReadinessBlocker,
    ReadinessBlockerCode,
    ReadinessNoticeCode,
)

_NS = 1_000_000_000
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
CALIBRATION = {
    "calibration_id": str(uuid4()),
    "calibration_version": 1,
    "checksum": SHA_A,
    "target_type": "ROBOT",
    "target_id": str(uuid4()),
    "schema_version": "ddp.calibration/1.0.0",
}
TIME_BASE = {
    "time_domain": "UTC",
    "clock_source": "HOST_CLOCK",
    "sync_method": "NONE",
    "sync_status": "DEGRADED",
}


def facts(**overrides: object) -> EpisodeManifestFacts:
    defaults: dict[str, object] = {
        "episode_id": uuid4(),
        "project_id": uuid4(),
        "episode_lineage_id": uuid4(),
        "episode_version": 1,
        "code": "EP-demo_001",
        "start_time_ns": 0,
        "end_time_ns": 5 * _NS,
        "time_base": dict(TIME_BASE),
        "calibration_snapshot": (dict(CALIBRATION),),
        "canonical_schema_version": "ddp.canonical/1.0.0-draft",
        "pipeline_id": "data_source_import",
        "pipeline_version": "1.0.0",
        "quality_status": "NOT_CHECKED",
    }
    defaults.update(overrides)
    return EpisodeManifestFacts(**defaults)  # type: ignore[arg-type]


def stream(**overrides: object) -> EpisodeStream:
    asset_id = uuid4()
    defaults: dict[str, object] = {
        "asset_id": asset_id,
        "sha256": SHA_A,
        "size_bytes": 1024,
        "camera": "main",
        "object_key": f"raw/{asset_id}/content.mp4",
        "content_type": "video/mp4",
        "format": "MP4",
        "source_fps": 30.0,
        "sha256_snapshot": SHA_A,
        "size_bytes_snapshot": 1024,
        "usable": True,
        "status": "VALID",
    }
    defaults.update(overrides)
    return EpisodeStream(**defaults)  # type: ignore[arg-type]


def _codes(decision: object) -> set[str]:
    return {str(item.code) for item in decision.blockers}  # type: ignore[attr-defined]


def test_manifest_contains_every_required_key_and_passes_identity_check() -> None:
    episode = facts()
    decision = evaluate_readiness(
        episode,
        (stream(camera="main"), stream(camera="wrist")),
        provenance_source="data_source_import",
    )

    assert decision.is_ready, decision.blockers
    manifest = decision.require_manifest()
    assert MANIFEST_REQUIRED_KEYS <= set(manifest)
    # 不允许把自身 Hash 写进被 Hash 的对象里。
    assert "manifest_sha256" not in manifest
    validate_manifest_identity(
        manifest,
        episode_id=episode.episode_id,
        episode_lineage_id=episode.episode_lineage_id,
        episode_version=episode.episode_version,
        project_id=episode.project_id,
        code=episode.code,
        start_time_ns=episode.start_time_ns,
        end_time_ns=episode.end_time_ns,
        duration_ns=episode.duration_ns,
    )
    assert manifest["time_alignment"] == {
        "clock_source": "HOST_CLOCK",
        "sync_method": "NONE",
        "sync_status": "DEGRADED",
    }
    assert manifest["provenance"]["source"] == "data_source_import"
    # quality 如实反映当前状态，不声称通过。
    assert manifest["quality"] == {"status": "NOT_CHECKED", "issues": []}


def test_generation_is_deterministic_and_hash_is_reproducible() -> None:
    episode = facts()
    streams = (stream(camera="wrist"), stream(camera="main"))
    first = evaluate_readiness(episode, streams, provenance_source="import")
    second = evaluate_readiness(episode, tuple(reversed(streams)), provenance_source="import")

    # 流顺序不影响结果：生成器按机位确定性排序。
    assert first.require_manifest() == second.require_manifest()
    sha = compute_manifest_sha256(first.require_manifest())
    assert sha == compute_manifest_sha256(second.require_manifest())
    assert sha.startswith("sha256:")
    assert_manifest_hash(first.require_manifest(), sha)

    tampered = dict(first.require_manifest())
    tampered["frame_count"] = int(tampered["frame_count"]) + 1  # type: ignore[arg-type]
    assert compute_manifest_sha256(tampered) != sha


def test_frame_count_uses_episode_duration_and_declared_fps() -> None:
    episode = facts(start_time_ns=0, end_time_ns=5 * _NS)
    decision = evaluate_readiness(
        episode,
        # 两台机位、同一时间轴：frame_count 不得把两路帧相加。
        (stream(camera="main", source_fps=30.0), stream(camera="wrist", source_fps=30.0)),
        provenance_source="import",
    )
    assert decision.frame_count == 150
    assert decision.require_manifest()["frame_count"] == 150


def test_streams_missing_is_a_blocker() -> None:
    decision = evaluate_readiness(facts(), (), provenance_source="import")
    assert not decision.is_ready
    assert decision.manifest is None
    assert _codes(decision) == {ReadinessBlockerCode.STREAMS_MISSING}
    with pytest.raises(DomainValidationError):
        decision.require_manifest()


def test_stream_without_camera_is_a_blocker() -> None:
    decision = evaluate_readiness(facts(), (stream(camera="  "),), provenance_source="import")
    assert not decision.is_ready
    assert ReadinessBlockerCode.STREAM_INCOMPLETE in _codes(decision)


def test_unusable_or_illegal_stream_is_a_blocker() -> None:
    decision = evaluate_readiness(
        facts(),
        (
            stream(camera="main", usable=False),
            stream(camera="wrist", sha256="not-a-hash"),
            stream(camera="front", size_bytes=0),
        ),
        provenance_source="import",
    )
    assert not decision.is_ready
    assert ReadinessBlockerCode.STREAM_INCOMPLETE in _codes(decision)


def test_duplicate_camera_is_an_incomplete_rig() -> None:
    decision = evaluate_readiness(
        facts(),
        (stream(camera="main"), stream(camera="main")),
        provenance_source="import",
    )
    assert not decision.is_ready
    blocker = next(
        item for item in decision.blockers if item.code is ReadinessBlockerCode.STREAM_INCOMPLETE
    )
    assert blocker.detail["cameras"] == ["main"]


def test_snapshot_mismatch_blocks() -> None:
    decision = evaluate_readiness(
        facts(),
        (stream(camera="main", sha256=SHA_B, sha256_snapshot=SHA_A),),
        provenance_source="import",
    )
    assert not decision.is_ready
    assert _codes(decision) == {ReadinessBlockerCode.STREAM_SNAPSHOT_MISMATCH}


@pytest.mark.parametrize("status", ["FAILED", "UNVERIFIED"])
def test_unverified_time_sync_blocks(status: str) -> None:
    """与 ``validate_time_base`` 共用同一份"未同步"口径（fail-closed）。"""
    episode = facts(time_base={**TIME_BASE, "sync_status": status})
    decision = evaluate_readiness(episode, (stream(camera="main"),), provenance_source="import")
    assert not decision.is_ready
    assert _codes(decision) == {ReadinessBlockerCode.TIME_SYNC_UNVERIFIED}
    assert status in decision.blockers[0].message


def test_invalid_time_base_blocks() -> None:
    episode = facts(time_base={"time_domain": "UTC", "clock_source": "HOST_CLOCK"})
    decision = evaluate_readiness(episode, (stream(camera="main"),), provenance_source="import")
    assert not decision.is_ready
    assert _codes(decision) == {ReadinessBlockerCode.TIME_BASE_INVALID}


def test_multi_camera_without_calibration_is_notice_not_blocker() -> None:
    """多机位缺标定：就绪放行但必须留痕，可训练性由数据集准入判断。

    2026-11-18 调整：公开第一人称数据集普遍多机位且不带标定，把它当就绪阻塞
    会让这些数据永远停在 PENDING，只能靠 HTTP ready 绕过门禁。标定是否可接受
    由数据集选择器的 ``CALIBRATION_MISSING`` / ``allow_missing_calibration``
    判定——那里才是权威口径。
    """
    episode = facts(calibration_snapshot=())
    decision = evaluate_readiness(
        episode,
        (stream(camera="main"), stream(camera="wrist")),
        provenance_source="import",
    )
    assert decision.is_ready
    assert [notice.code for notice in decision.notices] == [
        ReadinessNoticeCode.MULTI_STREAM_CALIBRATION_ABSENT
    ]


def test_single_camera_without_calibration_is_not_blocking_but_is_recorded() -> None:
    """单机位缺标定不阻塞，但必须留痕（标定准入属数据集策略，见模块文档）。"""
    episode = facts(calibration_snapshot=())
    decision = evaluate_readiness(episode, (stream(camera="main"),), provenance_source="import")
    assert decision.is_ready, decision.blockers
    assert [str(item.code) for item in decision.notices] == [
        ReadinessNoticeCode.SINGLE_STREAM_CALIBRATION_ABSENT
    ]


def test_unknown_duration_blocks() -> None:
    episode = facts(start_time_ns=7 * _NS, end_time_ns=7 * _NS)
    decision = evaluate_readiness(episode, (stream(camera="main"),), provenance_source="import")
    assert not decision.is_ready
    assert ReadinessBlockerCode.DURATION_UNKNOWN in _codes(decision)


def test_unknown_frame_rate_blocks() -> None:
    """声明帧率缺失时不能猜：平台不解码视频，帧数就是不可确定。"""
    decision = evaluate_readiness(
        facts(), (stream(camera="main", source_fps=None),), provenance_source="import"
    )
    assert not decision.is_ready
    assert _codes(decision) == {ReadinessBlockerCode.FRAME_RATE_UNKNOWN}


def test_inconsistent_frame_rate_blocks() -> None:
    decision = evaluate_readiness(
        facts(),
        (stream(camera="main", source_fps=30.0), stream(camera="wrist", source_fps=15.0)),
        provenance_source="import",
    )
    assert not decision.is_ready
    assert _codes(decision) == {ReadinessBlockerCode.FRAME_RATE_INCONSISTENT}
    assert decision.blockers[0].detail["source_fps"] == [15.0, 30.0]


def test_frame_count_rounding_to_zero_blocks() -> None:
    decision = evaluate_readiness(
        facts(start_time_ns=0, end_time_ns=1000),
        (stream(camera="main", source_fps=1.0),),
        provenance_source="import",
    )
    assert not decision.is_ready
    assert ReadinessBlockerCode.FRAME_COUNT_UNDETERMINED in _codes(decision)


def test_missing_provenance_source_is_rejected_loudly() -> None:
    with pytest.raises(DomainValidationError):
        evaluate_readiness(facts(), (stream(camera="main"),), provenance_source="  ")


def _episode(**overrides: object) -> Episode:
    defaults: dict[str, object] = {
        "episode_id": uuid4(),
        "project_id": uuid4(),
        "code": "EP-demo",
        "start_time_ns": 0,
        "end_time_ns": 5 * _NS,
        "time_base": dict(TIME_BASE),
        "canonical_schema_version": "ddp.canonical/1.0.0-draft",
        "pipeline_id": "data_source_import",
        "pipeline_version": "1.0.0",
        "segmentation_rule_version": "episode-boundary/1.0.0",
        "calibration_snapshot": (),
    }
    defaults.update(overrides)
    return Episode(**defaults)  # type: ignore[arg-type]


def test_record_readiness_blocked_keeps_pending_and_stores_reason() -> None:
    from datetime import UTC, datetime

    episode = _episode()
    blocker = ReadinessBlocker(
        ReadinessBlockerCode.FRAME_RATE_UNKNOWN,
        "机位没有声明有效帧率（source_fps），无法确定帧数",
        {"asset_ids": ["x"]},
    )
    blocked = episode.record_readiness_blocked(
        blockers=(blocker,), now=datetime(2026, 11, 17, tzinfo=UTC)
    )
    assert blocked.status is EpisodeStatus.PENDING
    assert blocked.readiness_blockers == (blocker,)
    assert blocked.readiness_blockers[0].as_dict()["code"] == "FRAME_RATE_UNKNOWN"


def test_record_readiness_blocked_requires_blockers_and_pending() -> None:
    from datetime import UTC, datetime

    episode = _episode()
    with pytest.raises(DomainValidationError):
        episode.record_readiness_blocked(blockers=(), now=datetime(2026, 11, 17, tzinfo=UTC))
    frozen = _episode(
        status=EpisodeStatus.INVALIDATED,
        failure_reason="人工作废",
    )
    with pytest.raises(DomainValidationError):
        frozen.record_readiness_blocked(
            blockers=(ReadinessBlocker(ReadinessBlockerCode.STREAMS_MISSING, "没有流"),),
            now=datetime(2026, 11, 17, tzinfo=UTC),
        )


def test_ready_episode_cannot_carry_blockers() -> None:
    from datetime import UTC, datetime

    from app.domain.episodes.validation import build_manifest_key

    episode = _episode(calibration_snapshot=(dict(CALIBRATION),))
    decision = evaluate_readiness(
        _facts_of(episode), (stream(camera="main"),), provenance_source="import"
    )
    # 迁移规则由状态机决定：PENDING → PROCESSING → READY（与用例一致）。
    processing = episode.start_processing(datetime(2026, 11, 17, tzinfo=UTC))
    ready = processing.mark_ready(
        manifest_key=build_manifest_key(processing.project_id, processing.episode_id, 1),
        manifest=decision.require_manifest(),
        frame_count=decision.require_frame_count(),
        now=datetime(2026, 11, 17, tzinfo=UTC),
    )
    assert ready.status is EpisodeStatus.READY
    assert ready.readiness_blockers == ()
    with pytest.raises(DomainValidationError):
        # 构造一个自相矛盾的实体：已 READY 却挂着阻塞原因。
        _episode(
            status=EpisodeStatus.READY,
            manifest_key=ready.manifest_key,
            manifest_sha256=ready.manifest_sha256,
            readiness_blockers=(ReadinessBlocker(ReadinessBlockerCode.STREAMS_MISSING, "没有流"),),
        )


def test_manifest_key_and_ready_clear_previous_blockers() -> None:
    """就绪会把"为什么没就绪"清空：残留原因会让问题永远存在。"""
    from datetime import UTC, datetime

    from app.domain.episodes.validation import build_manifest_key

    episode = _episode(
        calibration_snapshot=(dict(CALIBRATION),),
        readiness_blockers=(ReadinessBlocker(ReadinessBlockerCode.STREAMS_MISSING, "没有流"),),
    )
    decision = evaluate_readiness(
        _facts_of(episode), (stream(camera="main"),), provenance_source="import"
    )
    # 迁移规则由状态机决定：PENDING → PROCESSING → READY（与用例一致）。
    processing = episode.start_processing(datetime(2026, 11, 17, tzinfo=UTC))
    ready = processing.mark_ready(
        manifest_key=build_manifest_key(processing.project_id, processing.episode_id, 1),
        manifest=decision.require_manifest(),
        frame_count=decision.require_frame_count(),
        now=datetime(2026, 11, 17, tzinfo=UTC),
    )
    assert ready.readiness_blockers == ()


def _facts_of(episode: Episode) -> EpisodeManifestFacts:
    return EpisodeManifestFacts.from_episode(episode)


def test_from_episode_copies_identity_fields() -> None:
    lineage = uuid4()
    episode = _episode(episode_lineage_id=lineage, device_ids=(uuid4(),), sensor_ids=(uuid4(),))
    copied = EpisodeManifestFacts.from_episode(episode)
    assert copied.episode_id == episode.episode_id
    assert copied.episode_lineage_id == lineage
    assert copied.duration_ns == episode.duration_ns
    assert copied.device_ids == episode.device_ids
    assert isinstance(copied.episode_id, UUID)
