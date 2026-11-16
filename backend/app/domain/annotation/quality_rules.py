"""标注质量规则引擎（纯领域、无 IO）。

设计约束：

1. **规则不查库**。Episode 边界、Schema 标签定义、策略阈值一律由调用方经
   :class:`RuleContext` 传入。规则里出现仓储/会话就没法单测，也会让
   "同一份提交跑两次得到不同结论"成为可能。
2. **确定性**。同一 ``(record, context)`` 必然得到同一 ``RuleOutcome``：
   不使用随机、不使用当前时间、不依赖 dict 插入顺序（所有多值输出排序）。
3. **不静默放过**。缺上下文时返回 ``applicable=False``（"没判"），而不是
   ``passed=True``（"判过了、合规"）。二者在落库层的区别是
   ``annotation_quality_findings.passed`` 为 ``NULL`` 而非 ``TRUE``。
4. **阈值只来自 policy**。规则内不写死阈值；policy 给了非法值就报错，
   不回退默认值（回退会让配置错误变成"看起来检查过了"）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from uuid import UUID

from app.domain.annotation.enums import (
    LabelValueSource,
    QualityCheckStatus,
    QualityRuleSeverity,
)
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.schema import LabelDefinition
from app.domain.annotation.submission import AnnotationRecord
from app.domain.annotation.validation import (
    canonical_sha256,
    coerce_enum,
    normalized_text,
    optional_text,
    positive_int,
)

RULE_ID_MAX_LENGTH = 64
RULE_DESCRIPTION_MAX_LENGTH = 500
# 策略键名只在本模块定义一次，应用层与 API 层引用同一常量。
AI_CONFIDENCE_THRESHOLD_KEY = "ai_confidence_threshold"


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """单条规则对单条记录的判定结果。

    ``applicable=False`` 表示**规则没有结论**（缺边界、缺阈值、记录没有该
    维度），此时 ``passed`` 的值不具含义，调用方必须落 ``NULL`` 而不是
    ``TRUE``。``message`` 说明原因，便于人读；``evidence`` 只放可回显给客户端
    的结构化排障信息（标签键、时间边界、阈值），不放跨项目标识。
    """

    passed: bool
    message: str
    evidence: Mapping[str, object] | None = None
    applicable: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "message",
            normalized_text(
                self.message,
                "rule_outcome.message",
                max_length=RULE_DESCRIPTION_MAX_LENGTH,
            ),
        )
        if not isinstance(self.passed, bool):
            raise DomainValidationError("rule_outcome.passed 必须是布尔值")
        if not isinstance(self.applicable, bool):
            raise DomainValidationError("rule_outcome.applicable 必须是布尔值")
        if self.evidence is not None and not isinstance(self.evidence, Mapping):
            raise DomainValidationError("rule_outcome.evidence 必须是对象")


@dataclass(frozen=True, slots=True)
class RuleContext:
    """规则求值所需的全部外部输入；由应用层一次性构造后传阅。

    ``definitions`` 是提交所用 Schema 快照的标签定义；``episode_bounds`` 是
    ``episode_id -> (start_time_ns, end_time_ns)``；``policy`` 是任务
    ``quality_policy``。三者都允许为空——此时相关规则返回 ``applicable=False``，
    不会伪造合规结论。
    """

    definitions: tuple[LabelDefinition, ...] = ()
    episode_bounds: Mapping[UUID, tuple[int, int]] = field(default_factory=dict)
    policy: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "definitions", tuple(self.definitions))
        for name in ("episode_bounds", "policy"):
            if not isinstance(getattr(self, name), Mapping):
                raise DomainValidationError(f"rule_context.{name} 必须是对象")


Evaluator = Callable[[AnnotationRecord, RuleContext], RuleOutcome]


@dataclass(frozen=True, slots=True)
class QualityRule:
    """一条可执行的质量规则。

    规则以"一个 id + 一个版本 + 一个纯函数"构成：版本变化意味着判定语义变了，
    执行记录里必须能区分"当时用的是哪一版"，否则历史结论无法解释。
    """

    rule_id: str
    version: int
    severity: QualityRuleSeverity | str
    description: str
    evaluator: Evaluator

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rule_id",
            normalized_text(self.rule_id, "rule_id", max_length=RULE_ID_MAX_LENGTH),
        )
        object.__setattr__(self, "version", positive_int(self.version, "rule.version"))
        object.__setattr__(
            self,
            "severity",
            coerce_enum(self.severity, QualityRuleSeverity, "rule.severity"),
        )
        description = optional_text(
            self.description,
            "rule.description",
            max_length=RULE_DESCRIPTION_MAX_LENGTH,
        )
        if description is None:
            raise DomainValidationError("rule.description 不能为空")
        object.__setattr__(self, "description", description)
        if not callable(self.evaluator):
            raise DomainValidationError("rule.evaluator 必须是可调用对象")

    def evaluate(self, record: AnnotationRecord, context: RuleContext) -> RuleOutcome:
        if not isinstance(record, AnnotationRecord):
            raise DomainValidationError("quality rule 只能作用于 AnnotationRecord")
        if not isinstance(context, RuleContext):
            raise DomainValidationError("quality rule 需要 RuleContext")
        outcome = self.evaluator(record, context)
        if not isinstance(outcome, RuleOutcome):
            # 规则实现返回错类型属于编程错误，必须立刻失败而不是当成"通过"。
            raise DomainValidationError(f"规则 {self.rule_id} 未返回 RuleOutcome")
        return outcome

    def to_payload(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "severity": self.severity.value,
            "description": self.description,
        }


class QualityRuleRegistry:
    """实例级规则注册表。

    刻意不用模块级全局单例：全局可变注册表会让测试互相污染，也无法在同一进程
    里并存"默认规则集"与"实验规则集"。默认规则集由 :func:`default_registry`
    每次构造一份新的。
    """

    def __init__(self, rules: tuple[QualityRule, ...] = ()) -> None:
        self._rules: dict[str, QualityRule] = {}
        for rule in rules:
            self.register(rule)

    def register(self, rule: QualityRule) -> None:
        if not isinstance(rule, QualityRule):
            raise DomainValidationError("只能注册 QualityRule")
        existing = self._rules.get(rule.rule_id)
        if existing is not None and existing.version == rule.version:
            raise DomainValidationError(f"质量规则 {rule.rule_id}@{rule.version} 已注册")
        if existing is not None and rule.version < existing.version:
            # 允许注册更高版本（升级规则），拒绝退回旧版本：否则同一进程内
            # "哪个版本在生效"取决于注册顺序。
            raise DomainValidationError(
                f"质量规则 {rule.rule_id} 不能从版本 {existing.version} 回退到 {rule.version}"
            )
        self._rules[rule.rule_id] = rule

    def get(self, rule_id: str) -> QualityRule:
        rule = self._rules.get(rule_id)
        if rule is None:
            raise DomainValidationError(f"未注册的质量规则：{rule_id}")
        return rule

    def list_rules(self) -> tuple[QualityRule, ...]:
        """按 ``rule_id`` 排序，保证执行顺序与输出顺序稳定。"""
        return tuple(self._rules[key] for key in sorted(self._rules))

    def __len__(self) -> int:
        return len(self._rules)

    @property
    def version(self) -> str:
        """规则集的内容寻址版本：任何规则的新增/删除/改版本/改严重级别都会变。

        用内容摘要而不是 ``v1``/``v2`` 这类手写序号：手写序号靠人记得改，
        漏改时执行记录会声称"用的是 v1"，而 v1 的定义早已不同。
        """
        return canonical_sha256({"rules": [rule.to_payload() for rule in self.list_rules()]})


# --------------------------------------------------------------------------
# 具体规则
# --------------------------------------------------------------------------


def evaluate_required_labels(record: AnnotationRecord, context: RuleContext) -> RuleOutcome:
    """Schema 声明为 required 的标签必须出现在记录里。"""
    if not context.definitions:
        return RuleOutcome(
            passed=True,
            applicable=False,
            message="未提供 Schema 标签定义，无法判定必填标签",
        )
    present = {label.label_key for label in record.labels}
    missing = sorted(
        definition.label_key
        for definition in context.definitions
        if definition.required and definition.label_key not in present
    )
    if not missing:
        return RuleOutcome(
            passed=True,
            message=f"必填标签齐全（共 {len(context.definitions)} 项定义）",
        )
    return RuleOutcome(
        passed=False,
        message=f"缺少 {len(missing)} 个必填标签：{', '.join(missing)}",
        evidence={"missing_label_keys": missing},
    )


def evaluate_time_range_within_episode(
    record: AnnotationRecord,
    context: RuleContext,
) -> RuleOutcome:
    """记录声明的 ``time_range`` 必须落在所属 Episode 的时间边界内。

    Episode 边界由调用方传入。提交草稿时的硬校验（``submission._validate_record_bounds``）
    已经拦过一次，本规则覆盖的是**存量的、绕过创建路径写入的记录**：
    质量检查是对已落库数据的独立复核，不能假设写入路径永远正确。
    """
    if record.time_range is None:
        return RuleOutcome(
            passed=True,
            applicable=False,
            message="记录未声明 time_range，无越界判定对象",
        )
    bounds = context.episode_bounds.get(record.episode_id)
    if bounds is None:
        return RuleOutcome(
            passed=True,
            applicable=False,
            message="未提供 Episode 时间边界，无法判定时间片段是否越界",
        )
    start = int(record.time_range["start_time_ns"])  # type: ignore[arg-type]
    end = int(record.time_range["end_time_ns"])  # type: ignore[arg-type]
    episode_start, episode_end = int(bounds[0]), int(bounds[1])
    evidence = {
        "episode_start_time_ns": episode_start,
        "episode_end_time_ns": episode_end,
        "record_start_time_ns": start,
        "record_end_time_ns": end,
    }
    if start >= episode_start and end <= episode_end:
        return RuleOutcome(passed=True, message="时间片段在 Episode 边界内", evidence=evidence)
    return RuleOutcome(
        passed=False,
        message="时间片段超出 Episode 边界",
        evidence=evidence,
    )


def evaluate_ai_label_confidence(
    record: AnnotationRecord,
    context: RuleContext,
) -> RuleOutcome:
    """AI_ASSISTED 标签的置信度不得低于策略阈值，且不得缺失。

    阈值只从 ``quality_policy.ai_confidence_threshold`` 读取：任务没配就没有
    判定标准，规则返回"不适用"；配了非法值属于配置错误，抛错而不是回退默认值。
    缺置信度的 AI 标签也算不通过——"无法证明高于阈值"和"低于阈值"在质量门禁上
    同等不可接受，放过会变成用空值蒙混过关。
    """
    threshold = _confidence_threshold(context.policy)
    if threshold is None:
        return RuleOutcome(
            passed=True,
            applicable=False,
            message=f"任务未配置 {AI_CONFIDENCE_THRESHOLD_KEY}，无法判定置信度",
        )
    ai_labels = [label for label in record.labels if label.source is LabelValueSource.AI_ASSISTED]
    if not ai_labels:
        return RuleOutcome(passed=True, message="记录不含 AI_ASSISTED 标签")
    unscored = sorted(label.label_key for label in ai_labels if label.confidence is None)
    low = sorted(
        (
            label
            for label in ai_labels
            if label.confidence is not None and label.confidence < threshold
        ),
        key=lambda label: label.label_key,
    )
    if not unscored and not low:
        return RuleOutcome(
            passed=True,
            message=f"AI 标签置信度均不低于 {threshold}",
            evidence={"threshold": threshold},
        )
    evidence: dict[str, object] = {"threshold": threshold}
    if low:
        evidence["below_threshold"] = [
            {"label_key": label.label_key, "confidence": float(label.confidence or 0.0)}
            for label in low
        ]
    if unscored:
        evidence["missing_confidence"] = unscored
    parts: list[str] = []
    if low:
        parts.append(f"低于阈值 {threshold} 的标签 {len(low)} 个")
    if unscored:
        parts.append(f"缺少置信度的 AI 标签 {len(unscored)} 个")
    return RuleOutcome(passed=False, message="；".join(parts), evidence=evidence)


def evaluate_duplicate_label_keys(
    record: AnnotationRecord,
    context: RuleContext,
) -> RuleOutcome:
    """同一记录内不得出现语义重复的标签键。

    实体层只做**精确**去重（``label_key`` 去掉首尾空白后逐字比较），因此
    ``action`` 与 ``Action`` 能同时存在——两者在 Schema 里也是两个键，但在
    下游统计、导出与训练标签映射里是同一语义，会把一个标签算两遍。
    本规则按去空白 + 小写折叠后再判重，输出始终排序。
    """
    grouped: dict[str, list[str]] = {}
    for label in record.labels:
        grouped.setdefault(label.label_key.strip().casefold(), []).append(label.label_key)
    duplicates = {key: sorted(values) for key, values in grouped.items() if len(values) > 1}
    if not duplicates:
        return RuleOutcome(passed=True, message="标签键无重复")
    ordered = sorted(duplicates)
    pairs = [{"normalized_key": key, "label_keys": duplicates[key]} for key in ordered]
    return RuleOutcome(
        passed=False,
        message=f"存在 {len(pairs)} 组重复标签键：{', '.join(ordered)}",
        evidence={"duplicates": pairs},
    )


def _confidence_threshold(policy: Mapping[str, object]) -> float | None:
    """读取并校验置信度阈值；未配置返回 ``None``，配置非法抛错。"""
    raw = policy.get(AI_CONFIDENCE_THRESHOLD_KEY)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise DomainValidationError(
            f"quality_policy.{AI_CONFIDENCE_THRESHOLD_KEY} 必须是 0 到 1 之间的数字"
        )
    threshold = float(raw)
    if not 0 <= threshold <= 1:
        raise DomainValidationError(
            f"quality_policy.{AI_CONFIDENCE_THRESHOLD_KEY} 必须在 0 到 1 之间"
        )
    return threshold


DEFAULT_RULES: tuple[QualityRule, ...] = (
    QualityRule(
        rule_id="required_labels_present",
        version=1,
        severity=QualityRuleSeverity.ERROR,
        description="Schema 声明为必填的标签必须出现在标注记录中",
        evaluator=evaluate_required_labels,
    ),
    QualityRule(
        rule_id="time_range_within_episode",
        version=1,
        severity=QualityRuleSeverity.ERROR,
        description="记录的时间片段必须落在所属 Episode 的时间边界内",
        evaluator=evaluate_time_range_within_episode,
    ),
    QualityRule(
        rule_id="ai_label_confidence_low",
        version=1,
        severity=QualityRuleSeverity.WARN,
        description="AI_ASSISTED 标签的置信度必须存在且不低于任务策略阈值",
        evaluator=evaluate_ai_label_confidence,
    ),
    QualityRule(
        rule_id="duplicate_label_keys",
        version=1,
        severity=QualityRuleSeverity.ERROR,
        description="同一记录内不得出现仅大小写/空白不同的重复标签键",
        evaluator=evaluate_duplicate_label_keys,
    ),
)


def resolve_status(error_count: int) -> QualityCheckStatus:
    """由 ERROR 级不通过数决定整体结论：有任何 ERROR 即 FAILED。

    规则只在这里定义一次：``WARN`` 永远不阻断（它表达"值得人看一眼"），
    阻断只能来自 ``ERROR``。把这个判定写进每条规则会让严重级别形同注释。
    """
    if isinstance(error_count, bool) or not isinstance(error_count, int) or error_count < 0:
        raise DomainValidationError("error_count 必须是非负整数")
    return QualityCheckStatus.FAILED if error_count > 0 else QualityCheckStatus.PASSED


def default_registry() -> QualityRuleRegistry:
    """默认规则集；每次返回新实例，避免跨调用共享可变状态。"""
    return QualityRuleRegistry(DEFAULT_RULES)


__all__ = [
    "AI_CONFIDENCE_THRESHOLD_KEY",
    "DEFAULT_RULES",
    "QualityRule",
    "QualityRuleRegistry",
    "RuleContext",
    "RuleOutcome",
    "default_registry",
    "evaluate_ai_label_confidence",
    "evaluate_duplicate_label_keys",
    "evaluate_required_labels",
    "evaluate_time_range_within_episode",
    "resolve_status",
]
