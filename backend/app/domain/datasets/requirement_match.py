"""训练需求快照与数据集版本事实的匹配规则（TRN-002）。

背景
----
交付用例原先只校验「revision 已批准」「版本 FROZEN」「export job 匹配」，
**没有**把评审通过的 ``training_request_revisions.snapshot`` 与真正要交付的
数据集版本比对——「交付的是不是需求要的东西」全靠人工。本模块补上这层断言。

三条硬规则
----------
1. **纯函数、无 IO、确定性**：同一 ``(snapshot, facts, policy)`` 必须得到完全
   相同的报告；报告落库后可按同样输入复核。
2. **三态结论**：``PASSED`` / ``FAILED`` / ``UNVERIFIABLE``。``UNVERIFIABLE``
   表示「版本上没有可核对的事实」，它**不是**通过。二者合并的写法等于用
   「没检查」冒充「检查通过」，因此这里保留独立状态，并由策略位分别处置。
3. **阈值不硬编码**：业务阈值只来自需求快照；划分比例容差由部署配置经
   ``MatchPolicy`` 显式传入。本模块登记的只是「阈值键 → 版本事实」的对应关系。
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.domain.datasets.canonical import plain
from app.domain.datasets.errors import DomainValidationError


class MatchStatus(StrEnum):
    """单个维度的核对结论。``UNVERIFIABLE`` 明确不等于 ``PASSED``。"""

    PASSED = "PASSED"
    FAILED = "FAILED"
    UNVERIFIABLE = "UNVERIFIABLE"


class MatchDimension(StrEnum):
    """可核对的维度名（与需求快照字段同名，便于客户端与审计定位）。"""

    TARGET_EPISODE_COUNT = "target_episode_count"
    TARGET_DURATION_NS = "target_duration_ns"
    SCENE_TARGETS = "scene_targets"
    ACTION_TARGETS = "action_targets"
    DATA_MODALITIES = "data_modalities"
    QUALITY_THRESHOLDS = "quality_thresholds"
    REQUIRED_SCHEMA_VERSIONS = "required_schema_versions"
    SPLIT_REQUIREMENTS = "split_requirements"
    FORMAT = "format"


class EnforcementPolicy(StrEnum):
    """核对结论的处置方式：``BLOCK`` 拒绝交付，``WARN`` 记录后放行。"""

    BLOCK = "BLOCK"
    WARN = "WARN"


@dataclass(frozen=True, slots=True)
class MatchPolicy:
    """核对策略；由部署配置注入，不在领域层写死处置方式与容差。"""

    on_failed: EnforcementPolicy | str
    on_unverifiable: EnforcementPolicy | str
    split_ratio_tolerance: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "on_failed", _policy("on_failed", self.on_failed))
        object.__setattr__(
            self, "on_unverifiable", _policy("on_unverifiable", self.on_unverifiable)
        )
        tolerance = self.split_ratio_tolerance
        if (
            not isinstance(tolerance, int | float)
            or isinstance(tolerance, bool)
            or not 0 <= float(tolerance) <= 1
        ):
            raise DomainValidationError("split_ratio_tolerance 必须在 0 到 1 之间")
        object.__setattr__(self, "split_ratio_tolerance", float(tolerance))


def _policy(field_name: str, value: object) -> EnforcementPolicy:
    try:
        return EnforcementPolicy(value)  # type: ignore[arg-type]
    except ValueError as exc:
        raise DomainValidationError(f"{field_name} 取值非法") from exc


@dataclass(frozen=True, slots=True)
class VersionFacts:
    """数据集版本上可核对的事实。

    每个字段取 ``None`` 表示「该信息未投影到版本/产物上」，核对结论只能是
    ``UNVERIFIABLE``。绝不把 ``None`` 当成 0 或空集合。
    """

    selected_count: int | None = None
    total_duration_ns: int | None = None
    split_counts: Mapping[str, int] | None = None
    scene_labels: frozenset[str] | None = None
    # label_key -> 该 key 下出现过的标量取值（已字符串化）
    annotation_labels: Mapping[str, frozenset[str]] | None = None
    annotation_schema_versions: frozenset[tuple[str, int]] | None = None
    data_modalities: frozenset[str] | None = None
    quality_metrics: Mapping[str, object] | None = None
    format: str | None = None
    format_version: str | None = None


@dataclass(frozen=True, slots=True)
class DimensionResult:
    dimension: MatchDimension
    status: MatchStatus
    expected: object
    actual: object
    detail: str

    @property
    def passed(self) -> bool:
        return self.status is MatchStatus.PASSED

    def to_payload(self) -> dict[str, object]:
        return {
            "dimension": self.dimension.value,
            "status": self.status.value,
            "expected": plain(self.expected),
            "actual": plain(self.actual),
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class MatchReport:
    """需求与版本的核对报告；``blocked`` 完全由 ``MatchPolicy`` 决定。"""

    results: tuple[DimensionResult, ...]
    policy: MatchPolicy

    @property
    def failed(self) -> tuple[DimensionResult, ...]:
        return tuple(item for item in self.results if item.status is MatchStatus.FAILED)

    @property
    def unverifiable(self) -> tuple[DimensionResult, ...]:
        return tuple(item for item in self.results if item.status is MatchStatus.UNVERIFIABLE)

    @property
    def status(self) -> MatchStatus:
        if self.failed:
            return MatchStatus.FAILED
        if self.unverifiable:
            return MatchStatus.UNVERIFIABLE
        return MatchStatus.PASSED

    @property
    def blocking_dimensions(self) -> tuple[DimensionResult, ...]:
        blocked: list[DimensionResult] = []
        if self.policy.on_failed is EnforcementPolicy.BLOCK:
            blocked.extend(self.failed)
        if self.policy.on_unverifiable is EnforcementPolicy.BLOCK:
            blocked.extend(self.unverifiable)
        return tuple(blocked)

    @property
    def blocked(self) -> bool:
        return bool(self.blocking_dimensions)

    def to_payload(self) -> dict[str, object]:
        """JSON 安全的稳定载荷；维度按固定顺序排列，可逐字节比较。"""
        return {
            "status": self.status.value,
            "blocked": self.blocked,
            "policy": {
                "on_failed": self.policy.on_failed.value,
                "on_unverifiable": self.policy.on_unverifiable.value,
                "split_ratio_tolerance": self.policy.split_ratio_tolerance,
            },
            "failed_dimensions": [item.dimension.value for item in self.failed],
            "unverifiable_dimensions": [item.dimension.value for item in self.unverifiable],
            "dimensions": [item.to_payload() for item in self.results],
        }


# 「质量阈值键 → 版本质量事实」登记表。这里**没有**阈值本身：阈值只存在于需求
# 快照。未登记的自定义阈值（如业务方自定的 min_quality_score）一律判
# UNVERIFIABLE——不能因为不认识就当通过。
_QUALITY_THRESHOLD_METRICS: Mapping[str, Callable[[Mapping[str, object]], object]] = {
    "min_selected_count": lambda metrics: metrics.get("selected_count"),
    "max_duplicated_count": lambda metrics: metrics.get("duplicated_count"),
    "min_total_duration_ns": lambda metrics: metrics.get("total_duration_ns"),
    "max_blocking_issue_count": lambda metrics: len(metrics.get("blocking_issues") or ()),
    "max_warning_count": lambda metrics: len(metrics.get("warnings") or ()),
}

_COMPARATORS: Mapping[str, Callable[[Any, Any], bool]] = {
    "min_": operator.ge,
    "max_": operator.le,
}

_SPLIT_KEYS: tuple[str, ...] = ("train_ratio", "validation_ratio", "test_ratio")
# 需求快照的 ``<split>_ratio`` 与 ``DatasetVersion`` 的 ``<split>_count`` 是两套
# 键名。核对时必须显式映射，否则 ``split_counts["train_ratio"]`` 恒为 0，
# 会把「划分正确」误判成「划分完全不符」。
_SPLIT_COUNT_KEYS: Mapping[str, str] = {
    "train_ratio": "train",
    "validation_ratio": "validation",
    "test_ratio": "test",
}


def evaluate_requirement_match(
    snapshot: Mapping[str, object] | object,
    facts: VersionFacts,
    policy: MatchPolicy,
) -> MatchReport:
    """逐维度核对需求快照与版本事实，返回确定性报告。"""
    values: Mapping[str, object] = snapshot if isinstance(snapshot, Mapping) else {}
    results = (
        _check_episode_count(values, facts),
        _check_duration(values, facts),
        _check_scene_targets(values, facts),
        _check_action_targets(values, facts),
        _check_modalities(values, facts),
        _check_quality_thresholds(values, facts),
        _check_schema_versions(values, facts),
        _check_split(values, facts, policy),
        _check_format(values, facts),
    )
    return MatchReport(results=results, policy=policy)


def _result(
    dimension: MatchDimension,
    status: MatchStatus,
    expected: object,
    actual: object,
    detail: str,
) -> DimensionResult:
    return DimensionResult(
        dimension=dimension,
        status=status,
        expected=expected,
        actual=actual,
        detail=detail,
    )


def _text_list(value: object) -> list[str]:
    """快照字段缺失或类型异常时返回空列表，绝不抛异常。"""
    if not isinstance(value, list | tuple):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in items:
            items.append(item.strip())
    return items


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _check_episode_count(values: Mapping[str, object], facts: VersionFacts) -> DimensionResult:
    dimension = MatchDimension.TARGET_EPISODE_COUNT
    expected = values.get("target_episode_count")
    if expected is None:
        return _result(
            dimension, MatchStatus.PASSED, None, facts.selected_count, "需求未指定目标样本数"
        )
    if not _is_number(expected):
        return _result(
            dimension,
            MatchStatus.UNVERIFIABLE,
            expected,
            facts.selected_count,
            "目标样本数取值非法",
        )
    if facts.selected_count is None:
        return _result(dimension, MatchStatus.UNVERIFIABLE, expected, None, "版本未提供入选样本数")
    status = MatchStatus.PASSED if facts.selected_count >= expected else MatchStatus.FAILED
    return _result(
        dimension,
        status,
        expected,
        facts.selected_count,
        f"版本入选 {facts.selected_count} 条，需求要求至少 {expected} 条",
    )


def _check_duration(values: Mapping[str, object], facts: VersionFacts) -> DimensionResult:
    dimension = MatchDimension.TARGET_DURATION_NS
    expected = values.get("target_duration_ns")
    if expected is None:
        return _result(
            dimension, MatchStatus.PASSED, None, facts.total_duration_ns, "需求未指定目标时长"
        )
    if not _is_number(expected):
        return _result(
            dimension,
            MatchStatus.UNVERIFIABLE,
            expected,
            facts.total_duration_ns,
            "目标时长取值非法",
        )
    if facts.total_duration_ns is None:
        return _result(dimension, MatchStatus.UNVERIFIABLE, expected, None, "版本未提供累计时长")
    status = MatchStatus.PASSED if facts.total_duration_ns >= expected else MatchStatus.FAILED
    return _result(
        dimension,
        status,
        expected,
        facts.total_duration_ns,
        f"版本累计时长 {facts.total_duration_ns} ns，需求要求至少 {expected} ns",
    )


def _check_scene_targets(values: Mapping[str, object], facts: VersionFacts) -> DimensionResult:
    dimension = MatchDimension.SCENE_TARGETS
    targets = _text_list(values.get("scene_targets"))
    if not targets:
        return _result(dimension, MatchStatus.PASSED, [], None, "需求未指定场景目标")
    if facts.scene_labels is None:
        return _result(
            dimension, MatchStatus.UNVERIFIABLE, targets, None, "版本未投影场景标签，无法核对"
        )
    actual = sorted(facts.scene_labels)
    missing = [item for item in targets if item not in facts.scene_labels]
    if missing:
        return _result(
            dimension,
            MatchStatus.FAILED,
            targets,
            actual,
            "版本内缺少场景标签：" + "、".join(missing),
        )
    return _result(
        dimension, MatchStatus.PASSED, targets, actual, f"场景目标 {len(targets)} 项均在版本内出现"
    )


def _check_action_targets(values: Mapping[str, object], facts: VersionFacts) -> DimensionResult:
    dimension = MatchDimension.ACTION_TARGETS
    targets = _text_list(values.get("action_targets"))
    if not targets:
        return _result(dimension, MatchStatus.PASSED, [], None, "需求未指定动作目标")
    labels = facts.annotation_labels
    if labels is None:
        return _result(
            dimension, MatchStatus.UNVERIFIABLE, targets, None, "版本未投影标注标签，无法核对"
        )
    observed: set[str] = set()
    for key, label_values in labels.items():
        observed.add(key.lower())
        observed.update(item.lower() for item in label_values)
    if not observed:
        # 「版本内一条标签都没有」是信息缺失，不是「核对不通过」。
        return _result(
            dimension, MatchStatus.UNVERIFIABLE, targets, [], "版本内标注没有任何标签取值，无法核对"
        )
    missing = [item for item in targets if item.lower() not in observed]
    actual = sorted(observed)
    if missing:
        return _result(
            dimension,
            MatchStatus.FAILED,
            targets,
            actual,
            "标注中未出现动作目标：" + "、".join(missing) + "（按标签 key/value 近似核对）",
        )
    return _result(
        dimension,
        MatchStatus.PASSED,
        targets,
        actual,
        "动作目标均出现在标注标签中（按标签 key/value 近似核对，非语义等价）",
    )


def _check_modalities(values: Mapping[str, object], facts: VersionFacts) -> DimensionResult:
    dimension = MatchDimension.DATA_MODALITIES
    targets = _text_list(values.get("data_modalities"))
    if not targets:
        return _result(dimension, MatchStatus.PASSED, [], None, "需求未指定数据模态")
    if facts.data_modalities is None:
        return _result(
            dimension,
            MatchStatus.UNVERIFIABLE,
            targets,
            None,
            "版本与导出产物均未声明数据模态，无法核对",
        )
    actual = sorted(facts.data_modalities)
    missing = [item for item in targets if item not in facts.data_modalities]
    if missing:
        return _result(
            dimension, MatchStatus.FAILED, targets, actual, "缺少数据模态：" + "、".join(missing)
        )
    return _result(dimension, MatchStatus.PASSED, targets, actual, "数据模态均已覆盖")


def _check_quality_thresholds(values: Mapping[str, object], facts: VersionFacts) -> DimensionResult:
    dimension = MatchDimension.QUALITY_THRESHOLDS
    thresholds = values.get("quality_thresholds")
    if not isinstance(thresholds, Mapping) or not thresholds:
        return _result(dimension, MatchStatus.PASSED, {}, None, "需求未指定质量阈值")
    if facts.quality_metrics is None:
        return _result(
            dimension,
            MatchStatus.UNVERIFIABLE,
            dict(thresholds),
            None,
            "版本没有质量报告事实，无法核对质量阈值",
        )
    details: dict[str, object] = {}
    failed: list[str] = []
    unknown: list[str] = []
    for key in sorted(str(item) for item in thresholds):
        expected = thresholds[key]
        extractor = _QUALITY_THRESHOLD_METRICS.get(key)
        comparator = next(
            (fn for prefix, fn in _COMPARATORS.items() if key.startswith(prefix)), None
        )
        actual = None if extractor is None else extractor(facts.quality_metrics)
        if (
            extractor is None
            or comparator is None
            or not _is_number(actual)
            or not _is_number(expected)
        ):
            details[key] = {
                "expected": plain(expected),
                "actual": plain(actual),
                "status": "UNVERIFIABLE",
            }
            unknown.append(key)
            continue
        passed = comparator(actual, expected)
        details[key] = {
            "expected": plain(expected),
            "actual": plain(actual),
            "status": "PASSED" if passed else "FAILED",
        }
        if not passed:
            failed.append(key)
    if failed:
        return _result(
            dimension,
            MatchStatus.FAILED,
            dict(thresholds),
            details,
            "质量阈值未满足：" + "、".join(failed),
        )
    if unknown:
        return _result(
            dimension,
            MatchStatus.UNVERIFIABLE,
            dict(thresholds),
            details,
            "以下阈值没有可核对的版本事实：" + "、".join(unknown),
        )
    return _result(dimension, MatchStatus.PASSED, dict(thresholds), details, "质量阈值全部满足")


def _check_schema_versions(values: Mapping[str, object], facts: VersionFacts) -> DimensionResult:
    dimension = MatchDimension.REQUIRED_SCHEMA_VERSIONS
    required = values.get("required_schema_versions")
    if not required:
        return _result(dimension, MatchStatus.PASSED, [], None, "需求未指定标注 Schema 版本")
    wanted: list[tuple[str, int]] = []
    if not isinstance(required, list | tuple):
        return _result(
            dimension, MatchStatus.UNVERIFIABLE, plain(required), None, "Schema 版本要求结构非法"
        )
    for item in required:
        version = item.get("schema_version") if isinstance(item, Mapping) else None
        if not isinstance(version, int) or isinstance(version, bool):
            return _result(
                dimension,
                MatchStatus.UNVERIFIABLE,
                plain(required),
                None,
                "Schema 版本要求条目结构非法",
            )
        wanted.append((str(item.get("schema_id")), version))
    expected = [{"schema_id": sid, "schema_version": ver} for sid, ver in wanted]
    if facts.annotation_schema_versions is None:
        return _result(
            dimension,
            MatchStatus.UNVERIFIABLE,
            expected,
            None,
            "版本未投影标注 Schema 版本，无法核对",
        )
    actual = [
        {"schema_id": sid, "schema_version": ver}
        for sid, ver in sorted(facts.annotation_schema_versions)
    ]
    missing = [
        item
        for (sid, ver), item in zip(wanted, expected, strict=True)
        if (sid, ver) not in facts.annotation_schema_versions
    ]
    if missing:
        return _result(
            dimension, MatchStatus.FAILED, expected, actual, "版本内缺少需求的标注 Schema 版本"
        )
    return _result(dimension, MatchStatus.PASSED, expected, actual, "标注 Schema 版本匹配")


def _check_split(
    values: Mapping[str, object], facts: VersionFacts, policy: MatchPolicy
) -> DimensionResult:
    dimension = MatchDimension.SPLIT_REQUIREMENTS
    requirements = values.get("split_requirements")
    if not isinstance(requirements, Mapping) or not requirements:
        return _result(dimension, MatchStatus.PASSED, {}, None, "需求未指定划分要求")
    wanted = {
        key: float(requirements[key]) for key in _SPLIT_KEYS if _is_number(requirements.get(key))
    }
    if not wanted:
        return _result(
            dimension, MatchStatus.PASSED, plain(requirements), None, "需求未指定划分比例"
        )
    if facts.split_counts is None:
        return _result(
            dimension, MatchStatus.UNVERIFIABLE, wanted, None, "版本未提供划分计数，无法核对"
        )
    total = sum(int(count) for count in facts.split_counts.values())
    if total <= 0:
        return _result(
            dimension,
            MatchStatus.UNVERIFIABLE,
            wanted,
            dict(facts.split_counts),
            "版本划分为空，无法核对",
        )
    tolerance = policy.split_ratio_tolerance
    diffs: dict[str, object] = {}
    failed: list[str] = []
    for key in _SPLIT_KEYS:
        if key not in wanted:
            continue
        # 需求用 ``train_ratio``，版本计数用 ``train``——两者键名不同源，必须经
        # ``_SPLIT_COUNT_KEYS`` 显式映射；直接拿需求键查计数会静默取到 0。
        count = int(facts.split_counts.get(_SPLIT_COUNT_KEYS[key], 0))
        realized = count / total
        diff = abs(realized - wanted[key])
        diffs[key] = {"expected": wanted[key], "actual": round(realized, 6), "diff": round(diff, 6)}
        if diff > tolerance:
            failed.append(key)
    if failed:
        return _result(
            dimension,
            MatchStatus.FAILED,
            wanted,
            diffs,
            f"划分比例超出容差 {tolerance}：" + "、".join(failed),
        )
    return _result(dimension, MatchStatus.PASSED, wanted, diffs, f"划分比例均在容差 {tolerance} 内")


def _check_format(values: Mapping[str, object], facts: VersionFacts) -> DimensionResult:
    dimension = MatchDimension.FORMAT
    expected = {"format": values.get("format"), "format_version": values.get("format_version")}
    if expected["format"] is None and expected["format_version"] is None:
        return _result(dimension, MatchStatus.PASSED, {}, None, "需求未指定交付格式")
    actual = {"format": facts.format, "format_version": facts.format_version}
    if facts.format is None or facts.format_version is None:
        return _result(
            dimension,
            MatchStatus.UNVERIFIABLE,
            expected,
            actual,
            "导出任务未提供格式声明，无法核对",
        )
    mismatched = [name for name in ("format", "format_version") if expected[name] != actual[name]]
    if mismatched:
        return _result(
            dimension,
            MatchStatus.FAILED,
            expected,
            actual,
            "导出格式与需求不一致：" + "、".join(mismatched),
        )
    return _result(dimension, MatchStatus.PASSED, expected, actual, "导出格式与需求一致")


__all__ = [
    "DimensionResult",
    "EnforcementPolicy",
    "MatchDimension",
    "MatchPolicy",
    "MatchReport",
    "MatchStatus",
    "VersionFacts",
    "evaluate_requirement_match",
]
