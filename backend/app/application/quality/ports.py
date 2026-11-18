"""质量引擎应用层端口。

只定义质量运行自身的仓储与规则源协议；检查对象、资产、标注记录、数据集
质量报告的读取一律复用**既有域的公开 Application 端口**，不复制查询实现，
也不跨模块直接读他人表。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from app.domain.identity.models import ProjectScope
from app.domain.quality.enums import QualityCategory, QualityFindingStatus, QualitySubjectType
from app.domain.quality.models import (
    QualityFinding,
    QualityFindingDraft,
    QualityRun,
)
from app.domain.quality.rules import QualityRuleDeclaration


class UnitOfWork(Protocol):
    def commit(self) -> None: ...


class QualityRunRepository(Protocol):
    """质量运行仓储：只追加，不提供更新/删除。"""

    def add(self, scope: ProjectScope, run: QualityRun) -> QualityRun: ...

    def get(self, scope: ProjectScope, run_id: UUID) -> QualityRun | None: ...

    def list_findings(
        self,
        scope: ProjectScope,
        run_id: UUID,
        *,
        category: QualityCategory | None = None,
        status: QualityFindingStatus | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[QualityFinding]: ...


class QualityRuleSource(Protocol):
    """一套既有规则的适配器。

    ``declarations`` 是规则的权威元数据；``evaluate`` 调用既有实现并把结果
    归一成 :class:`QualityFindingDraft`。规则源**不得**重写判定逻辑。
    """

    source_id: str
    declarations: tuple[QualityRuleDeclaration, ...]

    def evaluate(
        self,
        scope: ProjectScope,
        facts: QualitySubjectFactsProtocol,
    ) -> tuple[QualityFindingDraft, ...]: ...


class QualitySubjectFactsProtocol(Protocol):
    subject_type: QualitySubjectType
    subject_id: UUID


class RuleSourceRegistry(Protocol):
    def sources_for(self, subject_type: QualitySubjectType) -> tuple[QualityRuleSource, ...]: ...

    def declarations_for(
        self, subject_type: QualitySubjectType
    ) -> tuple[QualityRuleDeclaration, ...]: ...

    def rule_set_version_for(self, subject_type: QualitySubjectType) -> str: ...


class AnnotationPolicyReader(Protocol):
    """按任务读取质量策略；缺失任务返回 ``None``，由规则判为不适用。"""

    def get_task_policy(
        self, scope: ProjectScope, task_id: UUID
    ) -> Mapping[str, object] | None: ...


__all__ = [
    "AnnotationPolicyReader",
    "QualityRuleSource",
    "QualityRunRepository",
    "QualitySubjectFactsProtocol",
    "RuleSourceRegistry",
    "UnitOfWork",
]
