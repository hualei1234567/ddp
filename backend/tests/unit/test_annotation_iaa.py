"""IAA 计算：真实重叠、Span 指标与样本不足语义。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.annotation import IAAObservation, Span, compute_iaa
from app.domain.annotation.errors import IAASourceForbiddenError
from app.domain.annotation.iaa import IAAObservationSource


def _obs(episode, annotator, *, start: int, end: int) -> IAAObservation:
    return IAAObservation(
        episode_id=episode,
        annotator_id=annotator,
        label_values={"action": "PICK"},
        spans=(Span(label_key="action", start_time_ns=start, end_time_ns=end),),
    )


def test_compute_iaa_with_real_overlap_and_spans() -> None:
    annotator_a, annotator_b = uuid4(), uuid4()
    episode_1, episode_2 = uuid4(), uuid4()
    observations = [
        _obs(episode_1, annotator_a, start=0, end=100),
        _obs(episode_1, annotator_b, start=10, end=110),
        _obs(episode_2, annotator_a, start=0, end=50),
        _obs(episode_2, annotator_b, start=0, end=50),
    ]
    result = compute_iaa(observations)
    assert result.status == "SUCCEEDED"
    assert result.overlap_item_count == 2
    metrics = {metric.metric.value: metric for metric in result.metrics}
    assert metrics["SPAN_F1"].value is not None
    assert metrics["BOUNDARY_MAE"].value is not None


def test_compute_iaa_requires_real_overlap() -> None:
    result = compute_iaa([_obs(uuid4(), uuid4(), start=0, end=10)])
    assert result.status == "INSUFFICIENT_DATA"


def test_compute_iaa_rejects_gold_set_source() -> None:
    observation = IAAObservation(
        episode_id=uuid4(),
        annotator_id=uuid4(),
        label_values={"action": "PICK"},
        source=IAAObservationSource.GOLD_SET,
    )
    with pytest.raises(IAASourceForbiddenError):
        compute_iaa([observation])


def test_compute_iaa_insufficient_returns_na_metric_rows() -> None:
    """样本不足时也必须返回各指标 N/A 占位行，前端不得显示空白（D8）。"""
    result = compute_iaa([_obs(uuid4(), uuid4(), start=0, end=10)])
    assert result.status == "INSUFFICIENT_DATA"
    assert result.metrics
    metrics = {metric.metric.value for metric in result.metrics}
    assert metrics == {
        "PERCENT_AGREEMENT",
        "COHEN_KAPPA",
        "FLEISS_KAPPA",
        "KRIPPENDORFF_ALPHA",
        "SPAN_F1",
        "BOUNDARY_MAE",
    }
    assert all(metric.value is None for metric in result.metrics)
    assert all(metric.status.value == "INSUFFICIENT_DATA" for metric in result.metrics)
