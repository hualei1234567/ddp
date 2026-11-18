"""导入质量规则的适配器（规则实现仍在 ``application.datasources.quality``）。

适配器只做三件事：

1. 声明这 7 条既有规则的类别/严重级别/适用对象；
2. 调既有 ``evaluate_import_quality`` 得到原始结论；
3. 把 ``passed`` / ``detail`` 归一成统一 finding。

**不复制任何判定代码**。如果既有函数新增/删除规则而适配器未同步，执行时
会因"声明与结果不一致"抛 :class:`QualityEngineError`，而不是静默漏检。
"""

from __future__ import annotations

from app.application.datasources.quality import RULE_VERSION, evaluate_import_quality
from app.application.quality.facts import QualitySubjectFacts
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

SOURCE_ID = "datasource_import"

#: 规则元数据：类别与严重级别由统一引擎裁决，规则实现本身不感知分层。
#: 全部保持 ``ERROR`` 以复刻既有导入报告的语义（``QualityReport.passed``
#: 是所有规则全通过）；统一引擎不借机改变原有门禁强度。
_RULE_META: tuple[tuple[str, QualityCategory, str], ...] = (
    ("assets_present", QualityCategory.INTEGRITY, "导入必须产出至少一条媒体资产"),
    ("asset_size_positive", QualityCategory.INTEGRITY, "每条媒体资产的大小必须为正"),
    ("asset_sha256_valid", QualityCategory.INTEGRITY, "每条媒体资产的校验和格式必须合法"),
    ("episodes_have_assets", QualityCategory.INTEGRITY, "每个片段必须至少有一条媒体资产"),
    ("main_camera_present", QualityCategory.VISUAL, "机位集合必须包含主视角 main"),
    ("timing_fields_valid", QualityCategory.TEMPORAL, "片段起点/时长/帧率字段必须为正且合法"),
    (
        "stream_sync_aligned",
        QualityCategory.TEMPORAL,
        "多机位片段的时间同步必须在容忍范围内",
    ),
)

_IMPORT_RULE_DECLARATIONS: tuple[QualityRuleDeclaration, ...] = tuple(
    QualityRuleDeclaration(
        rule_id=rule_id,
        category=category,
        version=RULE_VERSION,
        severity=QualitySeverity.ERROR,
        source=SOURCE_ID,
        description=description,
        subject_types=(QualitySubjectType.EPISODE, QualitySubjectType.DATASOURCE_IMPORT),
    )
    for rule_id, category, description in _RULE_META
)

_DECLARED_BY_ID = {item.rule_id: item for item in _IMPORT_RULE_DECLARATIONS}
_MESSAGE_MAX_LENGTH = 500


class ImportQualityRuleSource:
    """把导入质量报告接入统一引擎。"""

    source_id = SOURCE_ID
    declarations = _IMPORT_RULE_DECLARATIONS

    def evaluate(
        self,
        scope: ProjectScope,
        facts: QualitySubjectFacts,
    ) -> tuple[QualityFindingDraft, ...]:
        if not facts.assets:
            if facts.subject_type is QualitySubjectType.EPISODE:
                # Episode 可能由非导入链路产生（没有媒体资产），此时导入规则
                # 没有判定对象。如实记"不适用"，而不是伪造 7 条通过。
                return tuple(
                    QualityFindingDraft(
                        rule_id=item.rule_id,
                        rule_version=item.version,
                        category=item.category,
                        severity=item.severity,
                        status=QualityFindingStatus.NOT_APPLICABLE,
                        message="该 Episode 没有可评估的导入媒体资产",
                        evidence={"reason": "episode_has_no_import_media_assets"},
                    )
                    for item in _IMPORT_RULE_DECLARATIONS
                )
            # 导入任务本身没有任何资产是真实的质量问题，继续交给既有规则判失败。
            if facts.import_job is None:
                raise QualityEngineError("导入对象缺少导入任务事实")
            import_id = facts.subject_id
            sample_scope = facts.import_job.scope.value
        else:
            import_ids = sorted({asset.import_id for asset in facts.assets})
            if facts.subject_type is QualitySubjectType.DATASOURCE_IMPORT:
                import_id = facts.subject_id
                if import_id not in import_ids:
                    raise QualityEngineError("导入资产与检查对象不一致")
                sample_scope = (
                    facts.import_job.scope.value if facts.import_job is not None else "UNKNOWN"
                )
            else:
                # Episode 的媒体资产由导入产出；若跨多个导入，取 ID 最小的导入
                # 作为报告元数据（结论仍基于全部资产）。Episode 级规则不按导入
                # 分批，避免同一 Episode 出现多份互相矛盾的报告。
                import_id = import_ids[0]
                sample_scope = "EPISODE"

        report = evaluate_import_quality(
            project_id=scope.project_id,
            import_id=import_id,
            sample_scope=sample_scope,
            assets=list(facts.assets),
        )
        return _map_report(report.findings)


def _map_report(findings: tuple[object, ...]) -> tuple[QualityFindingDraft, ...]:
    seen: set[str] = set()
    drafts: list[QualityFindingDraft] = []
    for finding in findings:
        rule_id = str(getattr(finding, "rule_id", ""))
        declaration = _DECLARED_BY_ID.get(rule_id)
        if declaration is None:
            raise QualityEngineError(f"导入质量规则 {rule_id} 未在统一引擎声明")
        passed = getattr(finding, "passed", None)
        if not isinstance(passed, bool):
            raise QualityEngineError(f"导入质量规则 {rule_id} 未返回布尔结论")
        detail = str(getattr(finding, "detail", ""))
        seen.add(rule_id)
        drafts.append(
            QualityFindingDraft(
                rule_id=declaration.rule_id,
                rule_version=declaration.version,
                category=declaration.category,
                severity=declaration.severity,
                status=QualityFindingStatus.PASS if passed else QualityFindingStatus.FAIL,
                message=_bounded_message(detail),
                score=1.0 if passed else 0.0,
            )
        )
    missing = sorted(set(_DECLARED_BY_ID) - seen)
    if missing:
        raise QualityEngineError(f"导入质量规则声明了但未执行：{', '.join(missing)}")
    return tuple(drafts)


def _bounded_message(detail: str) -> str:
    """导入规则的历史 detail 没有长度上限；统一结论列有 500 字符约束。

    截断而不是拒绝：详情里可能列出多个长路径，但"哪些资产不合规"的结论
    本身仍然有效。完整排障信息由既有导入质量报告接口提供。
    """
    text = detail.strip()
    if not text:
        return "导入规则未提供说明"
    return text[:_MESSAGE_MAX_LENGTH]


def declarations_for(subject_type: QualitySubjectType) -> tuple[QualityRuleDeclaration, ...]:
    return tuple(item for item in _IMPORT_RULE_DECLARATIONS if item.applies_to(subject_type))


__all__ = [
    "SOURCE_ID",
    "ImportQualityRuleSource",
    "declarations_for",
]
