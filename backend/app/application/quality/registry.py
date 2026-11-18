"""规则源注册表：把三套既有规则统一暴露为声明 + 执行入口。

注册表本身不判定任何规则；它只负责：

- 校验 ``source_id`` 与 ``(source, rule_id)`` 不重复；
- 按检查对象过滤出适用的规则声明；
- 计算适用规则集的内容 SHA（历史运行固化该值）。

每次调用返回新计算的版本，不缓存可变状态；规则升级后新运行会得到新版本，
旧运行的版本字段不会被改写（运行只追加）。
"""

from __future__ import annotations

from app.application.datasets.ports import DatasetReportRepository
from app.application.quality.ports import QualityRuleSource
from app.application.quality.sources import (
    AnnotationQualityRuleSource,
    DatasetQualityRuleSource,
    ImportQualityRuleSource,
)
from app.domain.quality.enums import QualitySubjectType
from app.domain.quality.errors import DomainValidationError
from app.domain.quality.rules import QualityRuleDeclaration, compute_rule_set_version


class QualityRuleSourceRegistry:
    def __init__(self, sources: tuple[QualityRuleSource, ...]) -> None:
        if not sources:
            raise DomainValidationError("规则源注册表不能为空")
        seen_sources: set[str] = set()
        seen_rules: set[tuple[str, str]] = set()
        for source in sources:
            if not isinstance(source.source_id, str) or not source.source_id:
                raise DomainValidationError("规则源必须有非空 source_id")
            if source.source_id in seen_sources:
                raise DomainValidationError(f"规则源重复注册：{source.source_id}")
            seen_sources.add(source.source_id)
            if not source.declarations:
                raise DomainValidationError(f"规则源 {source.source_id} 没有声明任何规则")
            for declaration in source.declarations:
                if not isinstance(declaration, QualityRuleDeclaration):
                    raise DomainValidationError("规则源声明必须是 QualityRuleDeclaration")
                key = (declaration.source, declaration.rule_id)
                if declaration.source != source.source_id:
                    raise DomainValidationError(
                        f"规则 {declaration.rule_id} 的 source 与所属规则源不一致"
                    )
                if key in seen_rules:
                    raise DomainValidationError(f"规则声明重复：{key[0]}/{key[1]}")
                seen_rules.add(key)
        self._sources = tuple(sources)

    def sources_for(self, subject_type: QualitySubjectType) -> tuple[QualityRuleSource, ...]:
        return tuple(
            source
            for source in self._sources
            if any(item.applies_to(subject_type) for item in source.declarations)
        )

    def declarations_for(
        self, subject_type: QualitySubjectType
    ) -> tuple[QualityRuleDeclaration, ...]:
        declarations: list[QualityRuleDeclaration] = []
        for source in self._sources:
            declarations.extend(
                item for item in source.declarations if item.applies_to(subject_type)
            )
        return tuple(declarations)

    def rule_set_version_for(self, subject_type: QualitySubjectType) -> str:
        return compute_rule_set_version(self.declarations_for(subject_type))


def default_registry(reports: DatasetReportRepository) -> QualityRuleSourceRegistry:
    """默认注册表：导入 / 标注 / 数据集三套规则各一个规则源。"""
    return QualityRuleSourceRegistry(
        (
            ImportQualityRuleSource(),
            AnnotationQualityRuleSource(),
            DatasetQualityRuleSource(reports),
        )
    )


__all__ = ["QualityRuleSourceRegistry", "default_registry"]
