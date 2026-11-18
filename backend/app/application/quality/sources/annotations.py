"""标注质量规则的适配器（规则实现仍在 ``domain.annotation.quality_rules``）。

标注规则本身是纯函数，且明确要求"缺上下文返回不适用"。统一引擎只需为每条
记录组装既有 :class:`RuleContext`，然后调用既有规则对象；不复制任何判定。

适用对象：``EPISODE``。一个 Episode 可能包含多份提交/多条记录，finding 的
``subject_ref`` 记录 ``annotation_id``，便于按记录回查。
"""

from __future__ import annotations

from uuid import UUID

from app.application.quality.facts import QualitySubjectFacts
from app.domain.annotation.quality_rules import (
    QualityRule,
    RuleContext,
    RuleOutcome,
    default_registry,
)
from app.domain.identity.models import ProjectScope
from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualitySeverity,
    QualitySubjectType,
)
from app.domain.quality.errors import QualityEngineError
from app.domain.quality.models import QualityFindingDraft
from app.domain.quality.rules import QualityRuleDeclaration

SOURCE_ID = "annotation_quality"

_ANNOTATION_RULES = default_registry().list_rules()

_ANNOTATION_RULE_DECLARATIONS: tuple[QualityRuleDeclaration, ...] = tuple(
    QualityRuleDeclaration(
        rule_id=rule.rule_id,
        category=QualityCategory.ANNOTATION,
        version=rule.version,
        severity=QualitySeverity(rule.severity.value),
        source=SOURCE_ID,
        description=rule.description,
        subject_types=(QualitySubjectType.EPISODE,),
    )
    for rule in _ANNOTATION_RULES
)


class AnnotationQualityRuleSource:
    """把标注质量规则接入统一引擎，按 Episode 聚合全部记录。"""

    source_id = SOURCE_ID
    declarations = _ANNOTATION_RULE_DECLARATIONS

    def evaluate(
        self,
        scope: ProjectScope,
        facts: QualitySubjectFacts,
    ) -> tuple[QualityFindingDraft, ...]:
        if facts.subject_type is not QualitySubjectType.EPISODE:
            raise QualityEngineError("标注质量规则只适用于 Episode")
        if not facts.annotation_records:
            return tuple(
                QualityFindingDraft(
                    rule_id=item.rule_id,
                    rule_version=item.version,
                    category=item.category,
                    severity=item.severity,
                    status=QualityFindingStatus.NOT_APPLICABLE,
                    message="该 Episode 没有标注记录",
                    evidence={"reason": "episode_has_no_annotation_records"},
                )
                for item in _ANNOTATION_RULE_DECLARATIONS
            )

        drafts: list[QualityFindingDraft] = []
        for record in facts.annotation_records:
            schema = facts.schemas.get(record.schema_id)
            bounds = facts.episode_bounds.get(record.episode_id)
            context = RuleContext(
                definitions=tuple(schema.labels) if schema is not None else (),
                episode_bounds=({record.episode_id: bounds} if bounds is not None else {}),
                policy=facts.task_policies.get(record.task_id, {}),
            )
            for rule in _ANNOTATION_RULES:
                outcome = rule.evaluate(record, context)
                drafts.append(_draft_from_outcome(rule, record.annotation_id, outcome))
        return tuple(drafts)


def _draft_from_outcome(
    rule: QualityRule,
    annotation_id: UUID,
    outcome: RuleOutcome,
) -> QualityFindingDraft:
    if not isinstance(outcome, RuleOutcome):
        raise QualityEngineError("标注质量规则未返回 RuleOutcome")
    if not outcome.applicable:
        status = QualityFindingStatus.NOT_APPLICABLE
        score = None
    elif outcome.passed:
        status = QualityFindingStatus.PASS
        score = 1.0
    else:
        status = QualityFindingStatus.FAIL
        score = 0.0
    return QualityFindingDraft(
        rule_id=rule.rule_id,
        rule_version=str(rule.version),
        category=QualityCategory.ANNOTATION,
        severity=QualitySeverity(rule.severity.value),
        status=status,
        message=outcome.message,
        evidence=outcome.evidence,
        score=score,
        subject_ref=str(annotation_id),
    )


def declarations_for(subject_type: QualitySubjectType) -> tuple[QualityRuleDeclaration, ...]:
    return tuple(item for item in _ANNOTATION_RULE_DECLARATIONS if item.applies_to(subject_type))


__all__ = [
    "SOURCE_ID",
    "AnnotationQualityRuleSource",
    "declarations_for",
]
