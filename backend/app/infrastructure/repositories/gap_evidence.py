"""数据缺口的多来源证据统计（N7-004，只读）。

口径
----
本模块只从**真实落库事实**取证据，不读配置里的目标值、不推断缺口：

- ``FAILURE_CASE`` ← ``evaluations.failure_cases``（N7-003 回调写入的结构化失败
  明细）。只取有界的一批评测（按创建时间倒序），到顶时在 ``truncated_sources``
  里如实声明；单条用例的 ``suggested_gap`` 是提示值，原样交给领域层匹配；
- ``LOW_SCORE`` ← ``evaluations.verdict='FAIL'`` 的条数。整场低分没有维度语义，
  因此作为项目级"未映射"证据，不硬塞进某个场景/动作；
- ``ANNOTATION_CONFLICT`` ← ``annotation_submissions.status='ESCALATED'`` 的提交。
  按提交内 ``annotation_records.labels[].label_key`` 聚合到 ``LABEL`` 轴；取不出
  标签键的提交计入"未映射"，不会被静默丢弃；
- 未满足需求（``REQUIREMENT``）由应用层用已加载的需求行生成，不再重复查库。

所有查询强制服务端 ``ProjectScope`` 的 ``project_id``，不使用客户端入参。
聚合走 SQL（``COUNT``/``GROUP BY``），不把明细行整批取回内存。

方言：与既有覆盖统计保持一致，只支持 SQLite / PostgreSQL；其他方言显式报错，
绝不静默返回空结果（空结果会被上游误读成"没有证据 = 缺口为 0"）。
"""

from __future__ import annotations

import logging

from sqlalchemy import and_, func, select, true
from sqlalchemy.orm import Session

from app.domain.annotation.enums import AnnotationSubmissionStatus
from app.domain.identity.models import ProjectScope
from app.domain.modelops.enums import EvaluationVerdict, GapDimensionType
from app.domain.modelops.errors import DomainValidationError
from app.domain.modelops.failure_cases import parse_failure_cases
from app.domain.modelops.gap_candidates import FailureCaseFact, FailureCaseScan
from app.domain.modelops.gap_evidence import (
    GapEvidenceFacts,
    GapEvidenceRecord,
    GapEvidenceSource,
    GapUnmappedReason,
)
from app.infrastructure.db.models.annotation_results import (
    AnnotationRecord,
    AnnotationSubmission,
)
from app.infrastructure.db.models.modelops import EvaluationRow
from app.infrastructure.repositories._coverage_sql import (
    element_is_object,
    expand_object_array,
    json_array_length,
    json_string_field,
)

logger = logging.getLogger(__name__)

# 资源边界（不是业务阈值）：failure_cases 每条评测最多 200 条，取 200 场评测
# 已足以覆盖近期失败面；继续放大会把 HTTP 线程拖进无界扫描。
MAX_FAILURE_CASE_EVALUATIONS = 200


class SqlGapEvidenceDirectory:
    """缺口多来源证据的只读仓储实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def evidence(self, scope: ProjectScope) -> GapEvidenceFacts:
        dialect = self._dialect()
        records: list[GapEvidenceRecord] = []
        truncated: list[str] = []
        failure_records, failure_truncated = self._failure_cases(scope)
        records.extend(failure_records)
        if failure_truncated:
            truncated.append(GapEvidenceSource.FAILURE_CASE.value)
        records.extend(self._low_score(scope))
        records.extend(self._annotation_conflicts(scope, dialect=dialect))
        return GapEvidenceFacts(records=tuple(records), truncated_sources=tuple(truncated))

    def _dialect(self) -> str:
        dialect = self._session.get_bind().dialect.name
        if dialect not in {"sqlite", "postgresql"}:
            raise RuntimeError(f"未支持的数据库方言：{dialect}")
        return dialect

    # ------------------------------------------------------------------ #
    # 评测失败用例
    # ------------------------------------------------------------------ #
    def failure_case_scan(self, scope: ProjectScope) -> FailureCaseScan:
        """读取当前项目近期评测的失败明细（有界、只读）。

        N7-004 的证据统计与 N7-005 的候选缺口共用这一查询；返回的是**原始
        事实**，聚合与归因由领域层完成。解析失败的存量行显式计数，不静默丢弃。
        """
        statement = (
            select(EvaluationRow.id, EvaluationRow.failure_cases)
            .where(
                EvaluationRow.project_id == scope.project_id,
                json_array_length(EvaluationRow.failure_cases, dialect=self._dialect()) > 0,
            )
            .order_by(EvaluationRow.created_at.desc(), EvaluationRow.id.desc())
            .limit(MAX_FAILURE_CASE_EVALUATIONS + 1)
        )
        rows = self._session.execute(statement).all()
        truncated = len(rows) > MAX_FAILURE_CASE_EVALUATIONS
        facts: list[FailureCaseFact] = []
        invalid = 0
        for evaluation_id, payload in rows[:MAX_FAILURE_CASE_EVALUATIONS]:
            try:
                cases = parse_failure_cases(payload)
            except DomainValidationError:
                # 写入时已校验；存量行若损坏，必须显式计数，不能静默丢弃。
                logger.warning("数据缺口证据：评测失败用例解析失败 project_id=%s", scope.project_id)
                invalid += 1
                continue
            facts.extend(FailureCaseFact(evaluation_id=evaluation_id, case=case) for case in cases)
        return FailureCaseScan(
            facts=tuple(facts),
            evaluations_scanned=min(len(rows), MAX_FAILURE_CASE_EVALUATIONS),
            invalid_evaluation_count=invalid,
            truncated=truncated,
        )

    def _failure_cases(self, scope: ProjectScope) -> tuple[list[GapEvidenceRecord], bool]:
        scan = self.failure_case_scan(scope)
        by_value: dict[str, int] = {}
        without_value = 0
        for fact in scan.facts:
            case = fact.case
            if case.suggested_gap is None:
                without_value += 1
                continue
            by_value[case.suggested_gap] = by_value.get(case.suggested_gap, 0) + 1
        records = [
            GapEvidenceRecord(
                source=GapEvidenceSource.FAILURE_CASE.value,
                count=count,
                dimension_value=value,
            )
            for value, count in sorted(by_value.items())
        ]
        if without_value:
            records.append(
                GapEvidenceRecord(
                    source=GapEvidenceSource.FAILURE_CASE.value,
                    count=without_value,
                    reason=GapUnmappedReason.NO_DIMENSION.value,
                )
            )
        if scan.invalid_evaluation_count:
            records.append(
                GapEvidenceRecord(
                    source=GapEvidenceSource.FAILURE_CASE.value,
                    count=scan.invalid_evaluation_count,
                    reason=GapUnmappedReason.INVALID_EVIDENCE.value,
                )
            )
        return records, scan.truncated

    # ------------------------------------------------------------------ #
    # 评测低分
    # ------------------------------------------------------------------ #
    def _low_score(self, scope: ProjectScope) -> list[GapEvidenceRecord]:
        count = self._session.scalar(
            select(func.count(EvaluationRow.id)).where(
                EvaluationRow.project_id == scope.project_id,
                EvaluationRow.verdict == EvaluationVerdict.FAIL.value,
            )
        )
        if not count:
            return []
        return [
            GapEvidenceRecord(
                source=GapEvidenceSource.LOW_SCORE.value,
                count=int(count),
                reason=GapUnmappedReason.NO_DIMENSION.value,
            )
        ]

    # ------------------------------------------------------------------ #
    # 标注分歧
    # ------------------------------------------------------------------ #
    def _annotation_conflicts(
        self, scope: ProjectScope, *, dialect: str
    ) -> list[GapEvidenceRecord]:
        status = AnnotationSubmissionStatus.ESCALATED.value
        total = self._session.scalar(
            select(func.count(AnnotationSubmission.id)).where(
                AnnotationSubmission.project_id == scope.project_id,
                AnnotationSubmission.status == status,
            )
        )
        if not total:
            return []
        elements = expand_object_array(AnnotationRecord.labels, dialect=dialect)
        label_key = json_string_field(elements.c.value, "label_key", dialect=dialect)
        joined = (
            select(AnnotationSubmission.id)
            .select_from(AnnotationSubmission)
            .join(
                AnnotationRecord,
                and_(
                    AnnotationRecord.project_id == AnnotationSubmission.project_id,
                    AnnotationRecord.submission_id == AnnotationSubmission.id,
                ),
            )
            .join(elements, true())
            .where(
                AnnotationSubmission.project_id == scope.project_id,
                AnnotationSubmission.status == status,
                element_is_object(elements, dialect=dialect),
                label_key.is_not(None),
            )
        )
        rows = self._session.execute(
            joined.with_only_columns(
                label_key,
                func.count(func.distinct(AnnotationSubmission.id)),
            ).group_by(label_key)
        ).all()
        with_labels = self._session.scalar(
            joined.with_only_columns(func.count(func.distinct(AnnotationSubmission.id)))
        )
        records = [
            GapEvidenceRecord(
                source=GapEvidenceSource.ANNOTATION_CONFLICT.value,
                count=int(count),
                dimension_type=GapDimensionType.LABEL.value,
                dimension_value=str(key),
            )
            for key, count in rows
        ]
        missing = int(total) - int(with_labels or 0)
        if missing > 0:
            records.append(
                GapEvidenceRecord(
                    source=GapEvidenceSource.ANNOTATION_CONFLICT.value,
                    count=missing,
                    reason=GapUnmappedReason.NO_DIMENSION.value,
                )
            )
        return records


__all__ = ["MAX_FAILURE_CASE_EVALUATIONS", "SqlGapEvidenceDirectory"]
