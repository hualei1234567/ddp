"""训练/验证/测试划分。

- 默认 Episode 级 80/10/10，可通过策略配置；
- 以血缘/内容 Hash/场景组为最小不可拆分单元；
- 由 seed + 策略版本 + 规范化键确定，重复构建得到相同结果；
- 空测试集默认失败，只有显式 ALLOW_EMPTY_TEST 时允许；训练/验证始终非空。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.domain.datasets.canonical import sha256_text
from app.domain.datasets.enums import (
    EmptySplitPolicy,
    GroupingKey,
    RoundingRule,
    Split,
    SplitStrategy,
    StratifyKey,
)
from app.domain.datasets.errors import SplitInvalidError
from app.domain.datasets.policies import SplitPolicy
from app.domain.datasets.selection import EpisodeCandidate


class SplitStatus(StrEnum):
    OK = "OK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    EMPTY_TEST_SPLIT = "EMPTY_TEST_SPLIT"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class SplitOutcome:
    assignments: dict[UUID, Split]
    strata: dict[UUID, str | None]
    counts: dict[str, int]
    status: SplitStatus
    warnings: tuple[str, ...]
    units: tuple[tuple[UUID, ...], ...]
    rounding_rule: RoundingRule

    @property
    def train_count(self) -> int:
        return self.counts[Split.TRAIN.value]

    @property
    def validation_count(self) -> int:
        return self.counts[Split.VALIDATION.value]

    @property
    def test_count(self) -> int:
        return self.counts[Split.TEST.value]

    def counts_payload(self) -> dict[str, int]:
        return {
            "train": self.train_count,
            "validation": self.validation_count,
            "test": self.test_count,
        }


def assign_splits(candidates: tuple[EpisodeCandidate, ...], policy: SplitPolicy) -> SplitOutcome:
    if not candidates:
        return SplitOutcome({}, {}, _zero_counts(), SplitStatus.INSUFFICIENT_DATA, (), (), policy.rounding_rule)
    total_duration = sum(item.duration_ns for item in candidates)
    if len(candidates) < policy.min_item_count or total_duration < policy.min_duration_ns:
        return SplitOutcome(
            {},
            {},
            _zero_counts(),
            SplitStatus.INSUFFICIENT_DATA,
            (),
            (),
            policy.rounding_rule,
        )
    units = _build_units(candidates, policy)
    if policy.strategy is SplitStrategy.RATIO:
        counts = _ratio_counts(len(units), policy)
    else:
        counts = _fixed_counts(len(units), policy)
    if counts is None:
        return SplitOutcome(
            {},
            {},
            _zero_counts(),
            SplitStatus.INVALID,
            ("SPLIT_COUNTS_INVALID",),
            (),
            policy.rounding_rule,
        )
    train_n, validation_n, test_n = counts
    if train_n < 1 or validation_n < 1:
        return SplitOutcome(
            {},
            {},
            _zero_counts(),
            SplitStatus.INSUFFICIENT_DATA,
            ("TRAIN_OR_VALIDATION_EMPTY",),
            (),
            policy.rounding_rule,
        )
    warnings: list[str] = []
    if test_n == 0 and policy.empty_split_policy is EmptySplitPolicy.FAIL:
        return SplitOutcome(
            {},
            {},
            _zero_counts(),
            SplitStatus.EMPTY_TEST_SPLIT,
            ("EMPTY_TEST_SPLIT_REJECTED",),
            (),
            policy.rounding_rule,
        )
    if test_n == 0:
        warnings.append("EMPTY_TEST_SPLIT_ALLOWED")

    ordered_units = sorted(units, key=lambda unit: _unit_signature(unit, policy.seed))
    assignments: dict[UUID, Split] = {}
    for position, unit in enumerate(ordered_units):
        if position < train_n:
            split = Split.TRAIN
        elif position < train_n + validation_n:
            split = Split.VALIDATION
        else:
            split = Split.TEST
        for candidate in unit:
            assignments[candidate.episode_id] = split
    strata = {
        candidate.episode_id: _stratum(candidate, policy.stratify_by, warnings)
        for candidate in candidates
    }
    if any(item is None for item in strata.values()) and policy.stratify_by:
        warnings.append("STRATIFICATION_PARTIAL")
    return SplitOutcome(
        assignments=assignments,
        strata=strata,
        counts={
            Split.TRAIN.value: train_n,
            Split.VALIDATION.value: validation_n,
            Split.TEST.value: test_n,
        },
        status=SplitStatus.OK,
        warnings=tuple(dict.fromkeys(warnings)),
        units=tuple(tuple(item.episode_id for item in unit) for unit in ordered_units),
        rounding_rule=policy.rounding_rule,
    )


def _build_units(
    candidates: tuple[EpisodeCandidate, ...],
    policy: SplitPolicy,
) -> list[list[EpisodeCandidate]]:
    parent = list(range(len(candidates)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    first_by_key: dict[tuple[str, str], int] = {}
    for index, candidate in enumerate(candidates):
        for key in policy.group_by:
            signature = _group_signature(candidate, key)
            if signature is None:
                raise SplitInvalidError(f"分组键 {key.value} 缺少可用于当前样本的数据")
            composite = (key.value, signature)
            previous = first_by_key.get(composite)
            if previous is None:
                first_by_key[composite] = index
            else:
                union(previous, index)
    components: dict[int, list[EpisodeCandidate]] = {}
    for index, candidate in enumerate(candidates):
        components.setdefault(find(index), []).append(candidate)
    return [components[root] for root in sorted(components)]


def _group_signature(candidate: EpisodeCandidate, key: GroupingKey) -> str | None:
    if key is GroupingKey.EPISODE_LINEAGE:
        return str(candidate.episode_lineage_id)
    if key is GroupingKey.MANIFEST_SHA256:
        return candidate.manifest_sha256
    if key is GroupingKey.RAW_ASSET_SHA256:
        hashes = tuple(sorted(candidate.raw_asset_hashes))
        return "|".join(hashes) if hashes else None
    if key is GroupingKey.SCENE_GROUP:
        return str(candidate.scene_group_id) if candidate.scene_group_id else None
    if key is GroupingKey.SIMILARITY_GROUP:
        # P1 近似重复/相似簇尚未实现；失败关闭，绝不假装按簇分组。
        raise SplitInvalidError("SIMILARITY_GROUP 分组需要相似簇结果，当前不可用")
    raise SplitInvalidError("未知分组键")


def _unit_signature(unit: list[EpisodeCandidate], seed: int) -> str:
    anchor = min(
        unit,
        key=lambda item: (str(item.episode_lineage_id), str(item.episode_id)),
    )
    return sha256_text(f"{seed}:{anchor.episode_lineage_id}:{anchor.episode_id}")


def _ratio_counts(
    unit_count: int,
    policy: SplitPolicy,
) -> tuple[int, int, int] | None:
    ratios = (policy.train_ratio, policy.validation_ratio, policy.test_ratio)
    exact = [unit_count * ratio for ratio in ratios]
    if policy.rounding_rule is RoundingRule.FLOOR:
        counts = [int(value) for value in exact]
        counts[0] += unit_count - sum(counts)
        return counts[0], counts[1], counts[2]
    if policy.rounding_rule is RoundingRule.ROUND_HALF_UP:
        counts = [int(value + 0.5) for value in exact]
        difference = unit_count - sum(counts)
        counts[0] += difference
        if counts[0] < 0:
            return None
        return counts[0], counts[1], counts[2]
    counts = [int(value) for value in exact]
    remainder = unit_count - sum(counts)
    fractions = [value - int(value) for value in exact]
    order = sorted(range(3), key=lambda index: (-fractions[index], index))
    for index in order[:remainder]:
        counts[index] += 1
    return counts[0], counts[1], counts[2]


def _fixed_counts(unit_count: int, policy: SplitPolicy) -> tuple[int, int, int] | None:
    train = policy.train_count or 0
    validation = policy.validation_count or 0
    test = policy.test_count or 0
    if train + validation + test != unit_count:
        return None
    return train, validation, test


def _stratum(
    candidate: EpisodeCandidate,
    keys: tuple[StratifyKey, ...],
    warnings: list[str],
) -> str | None:
    if not keys:
        return None
    parts: list[str] = []
    for key in keys:
        if key is StratifyKey.SCENE:
            value = candidate.scene_labels[0] if candidate.scene_labels else None
        elif key is StratifyKey.ROBOT:
            value = str(candidate.robot_id) if candidate.robot_id else None
        elif key is StratifyKey.DEVICE:
            value = str(candidate.device_ids[0]) if candidate.device_ids else None
        elif key is StratifyKey.QUALITY_STATUS:
            value = candidate.quality_status
        else:
            value = None
            warnings.append(f"STRATIFICATION_DIMENSION_UNAVAILABLE:{key.value}")
        if value is None:
            return None
        parts.append(f"{key.value}={value}")
    return "|".join(parts)


def _zero_counts() -> dict[str, int]:
    return {
        Split.TRAIN.value: 0,
        Split.VALIDATION.value: 0,
        Split.TEST.value: 0,
    }


__all__ = ["SplitOutcome", "SplitStatus", "assign_splits"]
