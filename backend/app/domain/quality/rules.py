"""规则声明与规则集版本。

设计约束：

1. **声明与实现分离**。规则实现仍在既有三套模块里（导入、标注、数据集），
   本模块只描述"这条规则属于哪一层、版本多少、严重级别是什么、适用于
   哪些检查对象"。声明是规则集的元数据，不是第二套判定逻辑。
2. **版本内容寻址**。规则集版本 = 全部适用声明的内容 SHA，复用标注域
   ``QualityRuleSet.version`` 的做法。手写 ``v1``/``v2`` 靠人记得改，
   漏改时历史执行记录会声称"用的是 v1"，而 v1 的定义早已不同。
3. **历史不可覆盖**。运行记录落库时固化 ``rule_set_version`` 与每条
   finding 的 ``rule_version``；后续规则升级不会回写历史结论。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domain.common.canonical_json import CanonicalJSONError, sha256_value
from app.domain.quality.enums import (
    RULE_CATEGORIES,
    QualityCategory,
    QualitySeverity,
    QualitySubjectType,
    coerce_category,
    coerce_severity,
    coerce_subject_type,
)
from app.domain.quality.errors import DomainValidationError

RULE_ID_MAX_LENGTH = 64
RULE_VERSION_MAX_LENGTH = 100
RULE_SOURCE_MAX_LENGTH = 64
RULE_DESCRIPTION_MAX_LENGTH = 500
SHA256_PREFIX = "sha256:"
SHA256_HEX_LENGTH = 64


def _required_text(value: object, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} 必须是字符串")
    text = value.strip()
    if not text:
        raise DomainValidationError(f"{field_name} 不能为空")
    if len(text) > max_length:
        raise DomainValidationError(f"{field_name} 超过 {max_length} 字符")
    return text


@dataclass(frozen=True, slots=True)
class QualityRuleDeclaration:
    """一条规则的权威声明。

    ``version`` 统一按字符串保存：导入规则用语义化版本（``1.1.0``）、标注
    规则用整数递增版本、数据集规则用生成器版本（``dataset-quality/1.0.0``）。
    三套既有版本体系各有含义，强行归一成整数会丢失可读性；字符串足以
    保证"历史记录能区分当时用的是哪一版"。
    """

    rule_id: str
    category: QualityCategory | str
    version: str | int
    severity: QualitySeverity | str
    source: str
    description: str
    subject_types: frozenset[QualitySubjectType | str] | tuple[QualitySubjectType | str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rule_id",
            _required_text(self.rule_id, "rule_id", max_length=RULE_ID_MAX_LENGTH),
        )
        category = coerce_category(self.category)
        if category is QualityCategory.OVERALL:
            raise DomainValidationError("OVERALL 是派生类别，规则不得直接声明为该类别")
        object.__setattr__(self, "category", category)
        if self.version is None:
            raise DomainValidationError("rule.version 不能为空")
        object.__setattr__(
            self,
            "version",
            _required_text(str(self.version), "rule.version", max_length=RULE_VERSION_MAX_LENGTH),
        )
        object.__setattr__(self, "severity", coerce_severity(self.severity))
        object.__setattr__(
            self,
            "source",
            _required_text(self.source, "rule.source", max_length=RULE_SOURCE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "description",
            _required_text(
                self.description,
                "rule.description",
                max_length=RULE_DESCRIPTION_MAX_LENGTH,
            ),
        )
        raw_subjects = self.subject_types
        if not raw_subjects:
            raise DomainValidationError("rule.subject_types 不能为空")
        subjects = frozenset(coerce_subject_type(item) for item in raw_subjects)
        object.__setattr__(self, "subject_types", subjects)

    @property
    def category_value(self) -> str:
        return self.category.value  # type: ignore[union-attr]

    @property
    def severity_value(self) -> str:
        return self.severity.value  # type: ignore[union-attr]

    def applies_to(self, subject_type: QualitySubjectType) -> bool:
        return subject_type in self.subject_types  # type: ignore[operator]

    def to_payload(self) -> dict[str, object]:
        """参与规则集版本计算的稳定载荷（键序由规范化器统一处理）。"""
        return {
            "category": self.category_value,
            "description": self.description,
            "rule_id": self.rule_id,
            "severity": self.severity_value,
            "source": self.source,
            "subject_types": sorted(item.value for item in self.subject_types),  # type: ignore[union-attr]
            "version": self.version,
        }


def compute_rule_set_version(
    declarations: Iterable[QualityRuleDeclaration],
) -> str:
    """对一组声明计算内容 SHA（``sha256:<64 hex>``）。

    重复 ``(source, rule_id)`` 直接失败：同一来源里出现两条同 ID 规则时，
    "哪条在生效"会取决于迭代顺序，历史版本也无法解释。
    """
    items = tuple(declarations)
    seen: set[tuple[str, str]] = set()
    payload: list[dict[str, object]] = []
    for declaration in sorted(items, key=lambda item: (item.source, item.rule_id)):
        if not isinstance(declaration, QualityRuleDeclaration):
            raise DomainValidationError("规则集只能包含 QualityRuleDeclaration")
        key = (declaration.source, declaration.rule_id)
        if key in seen:
            raise DomainValidationError(f"规则声明重复：{declaration.source}/{declaration.rule_id}")
        seen.add(key)
        payload.append(declaration.to_payload())
    try:
        return sha256_value({"rules": payload})
    except CanonicalJSONError as exc:
        raise DomainValidationError("规则声明无法规范化") from exc


def is_sha256_version(value: str) -> bool:
    """判断字符串是否为 ``sha256:<64 hex>`` 形态。"""
    if not isinstance(value, str) or not value.startswith(SHA256_PREFIX):
        return False
    body = value[len(SHA256_PREFIX) :]
    return len(body) == SHA256_HEX_LENGTH and all(ch in "0123456789abcdef" for ch in body)


def declarations_by_rule_id(
    declarations: Iterable[QualityRuleDeclaration],
) -> Mapping[str, QualityRuleDeclaration]:
    """按 ``(source, rule_id)`` 建立索引时用 ``source/rule_id`` 作为键。"""
    result: dict[str, QualityRuleDeclaration] = {}
    for declaration in declarations:
        key = f"{declaration.source}/{declaration.rule_id}"
        if key in result:
            raise DomainValidationError(f"规则声明重复：{key}")
        result[key] = declaration
    return result


@dataclass(frozen=True, slots=True)
class RuleSet:
    """一组规则声明的不可变快照。"""

    declarations: tuple[QualityRuleDeclaration, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "declarations", tuple(self.declarations))
        # 构造即校验重复与规范化，避免非法规则集被带到执行阶段。
        compute_rule_set_version(self.declarations)

    @property
    def version(self) -> str:
        return compute_rule_set_version(self.declarations)

    def for_subject(self, subject_type: QualitySubjectType) -> RuleSet:
        return RuleSet(tuple(item for item in self.declarations if item.applies_to(subject_type)))

    def for_categories(self, categories: Iterable[QualityCategory]) -> RuleSet:
        selected = frozenset(coerce_category(item) for item in categories)
        if QualityCategory.OVERALL in selected:
            raise DomainValidationError("OVERALL 是派生类别，不能用于筛选规则")
        return RuleSet(tuple(item for item in self.declarations if item.category in selected))

    def __len__(self) -> int:
        return len(self.declarations)


__all__ = [
    "RULE_CATEGORIES",
    "RuleSet",
    "QualityRuleDeclaration",
    "compute_rule_set_version",
    "declarations_by_rule_id",
    "is_sha256_version",
]
