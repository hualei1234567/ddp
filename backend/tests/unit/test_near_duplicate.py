"""近重复检测：按元数据信号判断疑似同一段拍摄。

**能力边界必须说清楚**：这是元数据近似，不是视觉查重。三条同时满足才判近重复
（时间重叠率 / 时长接近 / 场景标签一致），宁可漏报也不误杀——误杀会把真正
不同的样本踢出数据集。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.datasets.errors import DomainValidationError
from app.domain.datasets.near_duplicate import (
    NearDuplicateMethod,
    detect_near_duplicates,
)
from app.domain.datasets.selection import EpisodeCandidate

_SEC = 1_000_000_000
_SHA = "sha256:" + "a" * 64


def _cand(
    index: int,
    *,
    start_s: float = 0.0,
    duration_s: float = 10.0,
    scenes: tuple[str, ...] = ("kitchen",),
    lineage: UUID | None = None,
) -> EpisodeCandidate:
    start = int(start_s * _SEC)
    end = start + int(duration_s * _SEC)
    return EpisodeCandidate(
        episode_id=UUID(int=index + 1),
        episode_lineage_id=lineage or UUID(int=1000 + index),
        episode_version=1,
        code=f"ep-{index}",
        status="ACCEPTED",
        quality_status="PASS",
        annotation_status="APPROVED",
        manifest_sha256=_SHA,
        scene_labels=scenes,
        robot_id=None,
        device_ids=(),
        batch_id=None,
        task_id=None,
        start_time_ns=start,
        end_time_ns=end,
        duration_ns=end - start,
        captured_at=datetime(2026, 11, 1, tzinfo=UTC),
        updated_at=datetime(2026, 11, 1, tzinfo=UTC),
        time_sync_status="VERIFIED",
        calibration_valid=True,
        calibration_issue=None,
        annotation_submission_id=UUID(int=9000),
        annotation_version=1,
        annotation_record_digest=_SHA,
        annotation_submission_status="APPROVED",
        schema_id=UUID(int=9100),
        schema_version=1,
        schema_sha256=_SHA,
        raw_asset_ids=(UUID(int=2000 + index),),
        raw_asset_hashes=(_SHA,),
        raw_asset_statuses=("VALID",),
    )


def test_identical_windows_same_scene_are_near_duplicates() -> None:
    outcome = detect_near_duplicates((_cand(0), _cand(1)))
    assert outcome.found is True
    assert outcome.cluster_count == 1
    assert len(outcome.pairs) == 1


def test_non_overlapping_windows_are_not_duplicates() -> None:
    outcome = detect_near_duplicates((_cand(0), _cand(1, start_s=100.0)))
    assert outcome.found is False


def test_different_scene_labels_are_not_duplicates() -> None:
    """场景不同就不判——没有依据时不做断言。"""
    outcome = detect_near_duplicates(
        (_cand(0, scenes=("kitchen",)), _cand(1, scenes=("warehouse",)))
    )
    assert outcome.found is False


def test_missing_scene_labels_do_not_participate() -> None:
    outcome = detect_near_duplicates((_cand(0, scenes=()), _cand(1, scenes=())))
    assert outcome.found is False


def test_duration_mismatch_prevents_duplicate() -> None:
    """时间重叠但时长差太大——可能是不同长度的两次拍摄。"""
    outcome = detect_near_duplicates((_cand(0, duration_s=10.0), _cand(1, duration_s=20.0)))
    assert outcome.found is False


def test_same_lineage_is_left_to_exact_dedup() -> None:
    """同一血缘是精确重复，不是近重复。"""
    shared = uuid4()
    outcome = detect_near_duplicates((_cand(0, lineage=shared), _cand(1, lineage=shared)))
    assert outcome.found is False


def test_cluster_of_three() -> None:
    outcome = detect_near_duplicates((_cand(0), _cand(1), _cand(2)))
    assert outcome.cluster_count == 1
    assert len(outcome.clusters[0]) == 3


def test_result_is_order_independent() -> None:
    forward = detect_near_duplicates((_cand(0), _cand(1), _cand(2, start_s=100.0)))
    backward = detect_near_duplicates((_cand(2, start_s=100.0), _cand(1), _cand(0)))
    assert forward.summary() == backward.summary()


def test_group_id_is_stable() -> None:
    first = detect_near_duplicates((_cand(0), _cand(1))).pairs[0].group_id
    second = detect_near_duplicates((_cand(0), _cand(1))).pairs[0].group_id
    assert first == second


def test_unsupported_method_fails_loudly() -> None:
    """未实现的方法必须显式拒绝，不静默降级成"没发现重复"。"""
    with pytest.raises(DomainValidationError) as excinfo:
        detect_near_duplicates((_cand(0),), method="PERCEPTUAL_HASH_V1")
    assert "未实现" in str(excinfo.value)


def test_invalid_thresholds_are_rejected() -> None:
    with pytest.raises(DomainValidationError):
        detect_near_duplicates((_cand(0),), overlap_ratio=0.0)
    with pytest.raises(DomainValidationError):
        detect_near_duplicates((_cand(0),), duration_tolerance=1.5)


def test_higher_threshold_is_stricter() -> None:
    pair = (
        _cand(0, start_s=0.0, duration_s=10.0),
        _cand(1, start_s=5.0, duration_s=10.0),
    )
    # 重叠 5s / 较短 10s = 0.5
    assert detect_near_duplicates(pair, overlap_ratio=0.4).found is True
    assert detect_near_duplicates(pair, overlap_ratio=0.9).found is False


def test_default_method_is_temporal_overlap() -> None:
    assert NearDuplicateMethod.TEMPORAL_OVERLAP in NearDuplicateMethod.SUPPORTED
