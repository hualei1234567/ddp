"""IAA（标注者间一致性）领域计算。

只接受真实提交形成的重叠标注；黄金集观测被显式拒绝，禁止以黄金集冒充 IAA。
样本不足返回 ``INSUFFICIENT_DATA`` 且指标值为 ``None``，不以 0 代替。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from app.domain.annotation.enums import (
    IAAMetric,
    IAAObservationSource,
    IAAValueStatus,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    IAASourceForbiddenError,
)
from app.domain.annotation.validation import coerce_enum, required_uuid

DEFAULT_IOU_THRESHOLD = 0.5
DEFAULT_METRIC_DEFINITIONS_VERSION = "ddp.stage3.iaa/1"


@dataclass(frozen=True, slots=True)
class Span:
    label_key: str
    start_time_ns: int
    end_time_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.label_key, str) or not self.label_key.strip():
            raise DomainValidationError("span.label_key 必须是非空字符串")
        for name in ("start_time_ns", "end_time_ns"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise DomainValidationError(f"span.{name} 必须是整数")
        if self.start_time_ns >= self.end_time_ns:
            raise DomainValidationError("span.start_time_ns 必须小于 end_time_ns")

    @property
    def duration_ns(self) -> int:
        return self.end_time_ns - self.start_time_ns

    def iou(self, other: Span) -> float:
        if self.label_key != other.label_key:
            return 0.0
        intersection = min(self.end_time_ns, other.end_time_ns) - max(
            self.start_time_ns,
            other.start_time_ns,
        )
        if intersection <= 0:
            return 0.0
        union = self.duration_ns + other.duration_ns - intersection
        return intersection / union if union else 0.0


@dataclass(frozen=True, slots=True)
class IAAObservation:
    episode_id: UUID
    annotator_id: UUID
    label_values: Mapping[str, object] = field(default_factory=dict)
    spans: tuple[Span, ...] = ()
    source: IAAObservationSource | str = IAAObservationSource.SUBMISSION

    def __post_init__(self) -> None:
        required_uuid(self.episode_id, "observation.episode_id")
        required_uuid(self.annotator_id, "observation.annotator_id")
        if not isinstance(self.label_values, Mapping):
            raise DomainValidationError("observation.label_values 必须是对象")
        object.__setattr__(self, "label_values", dict(self.label_values))
        spans = tuple(self.spans)
        for item in spans:
            if not isinstance(item, Span):
                raise DomainValidationError("observation.spans 只能包含 Span")
        object.__setattr__(self, "spans", spans)
        object.__setattr__(
            self,
            "source",
            coerce_enum(self.source, IAAObservationSource, "observation.source"),
        )


@dataclass(frozen=True, slots=True)
class IAAMetricResult:
    metric: IAAMetric
    label_key: str
    value: float | None
    numerator: float | None
    denominator: float | None
    sample_size: int
    status: IAAValueStatus = IAAValueStatus.OK
    unit: str | None = None
    config: Mapping[str, object] | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "metric": self.metric.value,
            "label_key": self.label_key,
            "value": self.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "sample_size": self.sample_size,
            "status": self.status.value,
        }
        if self.unit is not None:
            payload["unit"] = self.unit
        if self.config is not None:
            payload["config"] = dict(self.config)
        return payload


@dataclass(frozen=True, slots=True)
class IAAComputation:
    status: str
    metric_definitions_version: str
    sample_item_count: int
    overlap_item_count: int
    annotator_count: int
    metrics: tuple[IAAMetricResult, ...]
    excluded_items: tuple[Mapping[str, object], ...] = ()

    @property
    def is_sufficient(self) -> bool:
        return self.status == "SUCCEEDED"

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "metric_definitions_version": self.metric_definitions_version,
            "sample_item_count": self.sample_item_count,
            "overlap_item_count": self.overlap_item_count,
            "annotator_count": self.annotator_count,
            "excluded_items": [dict(item) for item in self.excluded_items],
            "metrics": [item.to_payload() for item in self.metrics],
        }


def _na(
    metric: IAAMetric,
    label_key: str,
    *,
    sample_size: int = 0,
    status: IAAValueStatus = IAAValueStatus.NOT_APPLICABLE,
    unit: str | None = None,
    config: Mapping[str, object] | None = None,
) -> IAAMetricResult:
    return IAAMetricResult(
        metric=metric,
        label_key=label_key,
        value=None,
        numerator=None,
        denominator=None,
        sample_size=sample_size,
        status=status,
        unit=unit,
        config=config,
    )


def _ok(
    metric: IAAMetric,
    label_key: str,
    *,
    value: float,
    numerator: float | None,
    denominator: float | None,
    sample_size: int,
    unit: str | None = None,
    config: Mapping[str, object] | None = None,
) -> IAAMetricResult:
    return IAAMetricResult(
        metric=metric,
        label_key=label_key,
        value=value,
        numerator=numerator,
        denominator=denominator,
        sample_size=sample_size,
        status=IAAValueStatus.OK,
        unit=unit,
        config=config,
    )


def _categorical(value: object) -> str:
    """把标签值归一为可比较的类别键；复杂值使用稳定 JSON 文本。"""
    if isinstance(value, str):
        return "s:" + value
    if isinstance(value, bool):
        return "b:1" if value else "b:0"
    if isinstance(value, int | float):
        return "n:" + repr(float(value))
    if isinstance(value, Mapping | list | tuple):
        from app.domain.annotation.validation import canonical_json

        return "j:" + canonical_json(value)
    return "u:" + repr(value)


def _percent_agreement(
    units: Sequence[list[str]],
    label_key: str,
) -> IAAMetricResult:
    comparable = 0
    agreed = 0
    for ratings in units:
        for index, left in enumerate(ratings):
            for right in ratings[index + 1 :]:
                comparable += 1
                agreed += 1 if left == right else 0
    if comparable == 0:
        return _na(IAAMetric.PERCENT_AGREEMENT, label_key)
    return _ok(
        IAAMetric.PERCENT_AGREEMENT,
        label_key,
        value=agreed / comparable,
        numerator=float(agreed),
        denominator=float(comparable),
        sample_size=comparable,
    )


def _cohen_kappa(units: Sequence[list[str]], label_key: str) -> IAAMetricResult:
    pairs = [ratings for ratings in units if len(ratings) == 2]
    if not pairs:
        return _na(IAAMetric.COHEN_KAPPA, label_key)
    total = len(pairs)
    observed = sum(1 for left, right in pairs if left == right) / total
    left_counts = Counter(left for left, _ in pairs)
    right_counts = Counter(right for _, right in pairs)
    expected = sum(
        (left_counts[category] / total) * (right_counts[category] / total)
        for category in set(left_counts) | set(right_counts)
    )
    if expected >= 1:
        return _na(IAAMetric.COHEN_KAPPA, label_key, sample_size=total)
    value = (observed - expected) / (1 - expected)
    return _ok(
        IAAMetric.COHEN_KAPPA,
        label_key,
        value=value,
        numerator=observed - expected,
        denominator=1 - expected,
        sample_size=total,
    )


def _fleiss_kappa(units: Sequence[list[str]], label_key: str) -> IAAMetricResult:
    usable = [ratings for ratings in units if len(ratings) >= 2]
    if len(usable) < 2:
        return _na(IAAMetric.FLEISS_KAPPA, label_key, sample_size=len(usable))
    p_values: list[float] = []
    totals: Counter[str] = Counter()
    total_ratings = 0
    for ratings in usable:
        counts = Counter(ratings)
        n = len(ratings)
        p_values.append((sum(count * count for count in counts.values()) - n) / (n * (n - 1)))
        totals.update(counts)
        total_ratings += n
    p_bar = sum(p_values) / len(p_values)
    p_e = sum((count / total_ratings) ** 2 for count in totals.values())
    if p_e >= 1:
        return _na(IAAMetric.FLEISS_KAPPA, label_key, sample_size=len(usable))
    return _ok(
        IAAMetric.FLEISS_KAPPA,
        label_key,
        value=(p_bar - p_e) / (1 - p_e),
        numerator=p_bar - p_e,
        denominator=1 - p_e,
        sample_size=len(usable),
    )


def _krippendorff_alpha(units: Sequence[list[str]], label_key: str) -> IAAMetricResult:
    usable = [ratings for ratings in units if len(ratings) >= 2]
    if not usable:
        return _na(IAAMetric.KRIPPENDORFF_ALPHA, label_key)
    coincidence: dict[tuple[str, str], float] = defaultdict(float)
    marginals: Counter[str] = Counter()
    total = 0.0
    for ratings in usable:
        n = len(ratings)
        for index, left in enumerate(ratings):
            for other_index, right in enumerate(ratings):
                if index == other_index:
                    continue
                coincidence[(left, right)] += 1 / (n - 1)
                total += 1 / (n - 1)
        marginals.update(ratings)
    if total == 0:
        return _na(IAAMetric.KRIPPENDORFF_ALPHA, label_key)
    observed = sum(count for (left, right), count in coincidence.items() if left != right) / total
    n_total = sum(marginals.values())
    if n_total <= 1:
        return _na(IAAMetric.KRIPPENDORFF_ALPHA, label_key)
    expected = sum(
        marginals[left] * marginals[right]
        for left in marginals
        for right in marginals
        if left != right
    ) / (n_total * (n_total - 1))
    if expected == 0:
        return _na(IAAMetric.KRIPPENDORFF_ALPHA, label_key)
    return _ok(
        IAAMetric.KRIPPENDORFF_ALPHA,
        label_key,
        value=1 - observed / expected,
        numerator=observed,
        denominator=expected,
        sample_size=len(usable),
    )


def _match_spans(
    reference: Sequence[Span],
    prediction: Sequence[Span],
    threshold: float,
) -> tuple[int, float]:
    candidates: list[tuple[float, int, int]] = []
    for index, left in enumerate(reference):
        for other_index, right in enumerate(prediction):
            iou = left.iou(right)
            if iou >= threshold:
                candidates.append((iou, index, other_index))
    candidates.sort(reverse=True)
    used_left: set[int] = set()
    used_right: set[int] = set()
    matched = 0
    boundary_error = 0.0
    for _iou, index, other_index in candidates:
        if index in used_left or other_index in used_right:
            continue
        used_left.add(index)
        used_right.add(other_index)
        matched += 1
        boundary_error += abs(
            reference[index].start_time_ns - prediction[other_index].start_time_ns
        )
        boundary_error += abs(reference[index].end_time_ns - prediction[other_index].end_time_ns)
    return matched, boundary_error


def _span_metrics(
    overlap: Mapping[UUID, Mapping[UUID, IAAObservation]],
    label_key: str,
    threshold: float,
) -> tuple[IAAMetricResult, IAAMetricResult]:
    true_positive = 0
    predicted_total = 0
    reference_total = 0
    boundary_error = 0.0
    matched_total = 0
    pairs = 0
    for per_annotator in overlap.values():
        annotators = list(per_annotator)
        for index, left in enumerate(annotators):
            for right in annotators[index + 1 :]:
                reference = [
                    item for item in per_annotator[left].spans if item.label_key == label_key
                ]
                prediction = [
                    item for item in per_annotator[right].spans if item.label_key == label_key
                ]
                if not reference and not prediction:
                    continue
                pairs += 1
                matched, error = _match_spans(reference, prediction, threshold)
                true_positive += matched
                reference_total += len(reference)
                predicted_total += len(prediction)
                boundary_error += error
                matched_total += matched
                reverse_matched, reverse_error = _match_spans(prediction, reference, threshold)
                true_positive += reverse_matched
                reference_total += len(prediction)
                predicted_total += len(reference)
                boundary_error += reverse_error
                matched_total += reverse_matched
    config: Mapping[str, object] = {"iou_threshold": threshold, "annotator_pairs": pairs}
    if true_positive == 0:
        span_f1 = _na(IAAMetric.SPAN_F1, label_key, sample_size=matched_total, config=config)
    else:
        denominator = predicted_total + reference_total
        span_f1 = _ok(
            IAAMetric.SPAN_F1,
            label_key,
            value=2 * true_positive / denominator,
            numerator=2.0 * true_positive,
            denominator=float(denominator),
            sample_size=matched_total,
            config=config,
        )
    if matched_total == 0:
        boundary_mae = _na(
            IAAMetric.BOUNDARY_MAE,
            label_key,
            sample_size=0,
            unit="ns",
            config=config,
        )
    else:
        boundary_mae = _ok(
            IAAMetric.BOUNDARY_MAE,
            label_key,
            value=boundary_error / (2 * matched_total),
            numerator=boundary_error,
            denominator=float(2 * matched_total),
            sample_size=matched_total,
            unit="ns",
            config=config,
        )
    return span_f1, boundary_mae


def compute_iaa(
    observations: Sequence[IAAObservation],
    *,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    metric_definitions_version: str = DEFAULT_METRIC_DEFINITIONS_VERSION,
) -> IAAComputation:
    """计算真实重叠标注的 IAA；黄金集来源直接拒绝。"""
    if not 0 <= iou_threshold <= 1:
        raise DomainValidationError("iou_threshold 必须在 0 到 1 之间")
    for observation in observations:
        if observation.source is not IAAObservationSource.SUBMISSION:
            raise IAASourceForbiddenError("IAA 只能基于真实提交重叠计算，不得使用黄金集")

    by_item: dict[UUID, dict[UUID, IAAObservation]] = defaultdict(dict)
    annotators: set[UUID] = set()
    for observation in observations:
        annotators.add(observation.annotator_id)
        by_item[observation.episode_id][observation.annotator_id] = observation
    overlap = {
        episode_id: per_annotator
        for episode_id, per_annotator in by_item.items()
        if len(per_annotator) >= 2
    }
    sample_item_count = len(by_item)
    overlap_item_count = len(overlap)
    annotator_count = len(annotators)
    if overlap_item_count < 2 or annotator_count < 2:
        metrics = _empty_metrics(
            observations,
            iou_threshold=iou_threshold,
            status=IAAValueStatus.INSUFFICIENT_DATA,
        )
        return IAAComputation(
            status="INSUFFICIENT_DATA",
            metric_definitions_version=metric_definitions_version,
            sample_item_count=sample_item_count,
            overlap_item_count=overlap_item_count,
            annotator_count=annotator_count,
            metrics=metrics,
        )

    label_keys = sorted(
        {
            key
            for per_annotator in overlap.values()
            for observation in per_annotator.values()
            for key in observation.label_values
        }
        | {
            span.label_key
            for per_annotator in overlap.values()
            for observation in per_annotator.values()
            for span in observation.spans
        }
    )
    metrics: list[IAAMetricResult] = []
    for label_key in label_keys:
        units = [
            [
                _categorical(observation.label_values[label_key])
                for observation in per_annotator.values()
            ]
            for per_annotator in overlap.values()
            if any(label_key in observation.label_values for observation in per_annotator.values())
        ]
        units = [ratings for ratings in units if len(ratings) >= 2]
        metrics.append(_percent_agreement(units, label_key))
        metrics.append(_cohen_kappa(units, label_key))
        metrics.append(_fleiss_kappa(units, label_key))
        metrics.append(_krippendorff_alpha(units, label_key))
        span_f1, boundary_mae = _span_metrics(overlap, label_key, iou_threshold)
        metrics.append(span_f1)
        metrics.append(boundary_mae)
    return IAAComputation(
        status="SUCCEEDED",
        metric_definitions_version=metric_definitions_version,
        sample_item_count=sample_item_count,
        overlap_item_count=overlap_item_count,
        annotator_count=annotator_count,
        metrics=tuple(metrics),
    )


def _empty_metrics(
    observations: Sequence[IAAObservation],
    *,
    iou_threshold: float,
    status: IAAValueStatus,
) -> tuple[IAAMetricResult, ...]:
    # 即使没有重叠，也要按已出现的标签键返回 N/A 指标行，避免前端显示空白。
    label_keys = sorted(
        {key for observation in observations for key in observation.label_values}
        | {span.label_key for observation in observations for span in observation.spans}
    )
    metrics: list[IAAMetricResult] = []
    for label_key in label_keys:
        for metric in (
            IAAMetric.PERCENT_AGREEMENT,
            IAAMetric.COHEN_KAPPA,
            IAAMetric.FLEISS_KAPPA,
            IAAMetric.KRIPPENDORFF_ALPHA,
        ):
            metrics.append(_na(metric, label_key, status=status))
        metrics.append(
            _na(
                IAAMetric.SPAN_F1,
                label_key,
                status=status,
                config={"iou_threshold": iou_threshold},
            )
        )
        metrics.append(
            _na(
                IAAMetric.BOUNDARY_MAE,
                label_key,
                status=status,
                unit="ns",
                config={"iou_threshold": iou_threshold},
            )
        )
    return tuple(metrics)


__all__ = [
    "DEFAULT_IOU_THRESHOLD",
    "DEFAULT_METRIC_DEFINITIONS_VERSION",
    "IAAComputation",
    "IAAMetricResult",
    "IAAObservation",
    "Span",
    "compute_iaa",
]
