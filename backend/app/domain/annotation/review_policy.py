"""双标与盲审策略。

策略存放在任务既有的 ``iaa_policy`` JSON 列中，不新增表或列：

- ``review_mode``：``SINGLE``（默认）或 ``DOUBLE``；
- ``agreement_threshold``：一致性阈值（0–1，默认 0.8）；
- ``agreement_metric``：判定指标，默认 ``PERCENT_AGREEMENT``；
- ``auto_approve``：达标后是否自动通过提交（默认关闭，必须显式开启）；
- ``blind_review``：盲审开关（默认关闭）；开启后，标注人之外的人读取
  该任务的提交/标注记录时，服务端隐藏标签值。

这里只做**策略解析与校验**，不实现任何 IAA 指标；指标计算唯一来源是
:mod:`app.domain.annotation.iaa`。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.domain.annotation.enums import IAAMetric
from app.domain.annotation.errors import DomainValidationError

DEFAULT_AGREEMENT_THRESHOLD = 0.8
# 可参与双标门槛判定的指标；黄金集指标（GOLD_ACCURACY）不在此列。
SUPPORTED_CONSISTENCY_METRICS = frozenset(
    {
        IAAMetric.PERCENT_AGREEMENT,
        IAAMetric.COHEN_KAPPA,
        IAAMetric.FLEISS_KAPPA,
        IAAMetric.KRIPPENDORFF_ALPHA,
        IAAMetric.SPAN_F1,
    }
)


class ReviewMode(StrEnum):
    SINGLE = "SINGLE"
    DOUBLE = "DOUBLE"


@dataclass(frozen=True, slots=True)
class ReviewPolicy:
    """从任务 ``iaa_policy`` 解析出的双标策略。"""

    review_mode: ReviewMode = ReviewMode.SINGLE
    agreement_threshold: float = DEFAULT_AGREEMENT_THRESHOLD
    agreement_metric: IAAMetric = IAAMetric.PERCENT_AGREEMENT
    auto_approve: bool = False
    blind_review: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> ReviewPolicy:
        policy = value or {}
        if not isinstance(policy, Mapping):
            raise DomainValidationError("iaa_policy 必须是对象")

        raw_mode = policy.get("review_mode", ReviewMode.SINGLE.value)
        if isinstance(raw_mode, ReviewMode):
            mode = raw_mode
        else:
            try:
                mode = ReviewMode(str(raw_mode).strip().upper())
            except ValueError as exc:
                raise DomainValidationError(
                    "iaa_policy.review_mode 必须是 SINGLE 或 DOUBLE"
                ) from exc

        raw_threshold = policy.get("agreement_threshold", DEFAULT_AGREEMENT_THRESHOLD)
        if raw_threshold is None:
            threshold = DEFAULT_AGREEMENT_THRESHOLD
        else:
            if (
                not isinstance(raw_threshold, int | float)
                or isinstance(raw_threshold, bool)
                or not 0 <= float(raw_threshold) <= 1
            ):
                raise DomainValidationError("iaa_policy.agreement_threshold 必须在 0 到 1 之间")
            threshold = float(raw_threshold)

        raw_metric = policy.get("agreement_metric", IAAMetric.PERCENT_AGREEMENT.value)
        if isinstance(raw_metric, IAAMetric):
            metric = raw_metric
        else:
            try:
                metric = IAAMetric(str(raw_metric))
            except ValueError as exc:
                raise DomainValidationError("iaa_policy.agreement_metric 取值非法") from exc
        if metric not in SUPPORTED_CONSISTENCY_METRICS:
            raise DomainValidationError("iaa_policy.agreement_metric 不支持用于双标一致性判定")

        raw_auto = policy.get("auto_approve", False)
        if not isinstance(raw_auto, bool):
            raise DomainValidationError("iaa_policy.auto_approve 必须是布尔值")
        if raw_auto and mode is not ReviewMode.DOUBLE:
            raise DomainValidationError("auto_approve 仅适用于 review_mode=DOUBLE 的任务")

        raw_blind = policy.get("blind_review", False)
        if not isinstance(raw_blind, bool):
            raise DomainValidationError("iaa_policy.blind_review 必须是布尔值")

        return cls(
            review_mode=mode,
            agreement_threshold=threshold,
            agreement_metric=metric,
            auto_approve=raw_auto,
            blind_review=raw_blind,
        )

    @property
    def is_double(self) -> bool:
        return self.review_mode is ReviewMode.DOUBLE

    def hides_labels_from(self, *, actor_id: UUID, annotator_id: UUID) -> bool:
        """读取某条记录时是否隐藏标签值。

        规则：任务开启盲审且读取人不是标注人时隐藏；标注人读取自己的记录
        始终可见。规则只在这里定义一次，API 与用例不得各写一份判断。
        """
        return self.blind_review and actor_id != annotator_id


def validate_review_policy(value: Mapping[str, object] | None) -> None:
    """任务创建/修改时的策略校验入口。"""
    ReviewPolicy.from_mapping(value)


__all__ = [
    "DEFAULT_AGREEMENT_THRESHOLD",
    "ReviewMode",
    "ReviewPolicy",
    "SUPPORTED_CONSISTENCY_METRICS",
    "validate_review_policy",
]
