"""质量运行与结论实体。

两张表/两个实体都**只追加**：一次质量检查是不可变的事实记录，"改结论"的
正确做法是重跑（新 run），而不是编辑旧 run。因此这里不引入 ``version`` /
``updated_at`` 这类会暗示"可更新"的字段。

口径：

- ``rule_set_version`` 在运行开始时固化，规则升级不回写历史；
- 每条 finding 同时记录 ``rule_version``，用于解释"当时用的是哪一版规则"；
- ``NOT_APPLICABLE`` / ``ERROR`` 的 ``score`` 必须为 ``NULL``——"没判"不能
  被平均成 1.0 或 0.0；
- ``subject_ref`` 用于区分同一规则作用于多个对象（例如一个 Episode 下的
  多条标注记录），空字符串表示对象级单条结论。
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualityRunStatus,
    QualitySeverity,
    QualitySubjectType,
    coerce_category,
    coerce_finding_status,
    coerce_run_status,
    coerce_severity,
    coerce_subject_type,
)
from app.domain.quality.errors import DomainValidationError
from app.domain.quality.rules import is_sha256_version

RULE_ID_MAX_LENGTH = 64
RULE_VERSION_MAX_LENGTH = 100
MESSAGE_MAX_LENGTH = 500
SUBJECT_REF_MAX_LENGTH = 128
EVIDENCE_MAX_BYTES = 4000


def _required_text(value: object, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} 必须是字符串")
    text = value.strip()
    if not text:
        raise DomainValidationError(f"{field_name} 不能为空")
    if len(text) > max_length:
        raise DomainValidationError(f"{field_name} 超过 {max_length} 字符")
    return text


def _optional_text(value: object, field_name: str, *, max_length: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} 必须是字符串")
    text = value.strip()
    if len(text) > max_length:
        raise DomainValidationError(f"{field_name} 超过 {max_length} 字符")
    return text


def _aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{field_name} 必须是时间")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} 必须带时区")
    return value.astimezone(UTC)


def _uuid(value: object, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise DomainValidationError(f"{field_name} 必须是 UUID")
    return value


def _normalize_evidence(evidence: Mapping[str, object] | None) -> dict[str, object] | None:
    if evidence is None:
        return None
    if not isinstance(evidence, Mapping):
        raise DomainValidationError("evidence 必须是 JSON 对象")
    if not evidence:
        return None
    normalized = dict(evidence)
    # 这里只做"可序列化 + 有界"，不要求 RFC 8785：evidence 不参与任何 Hash，
    # 而纳秒时间戳（约 1.8e18）超出 JCS 的 ±2^53 精确表示范围。用 canonical_json
    # 会把合法的时序证据判成非法，逼调用方丢精度或改类型。
    try:
        serialized = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("evidence 必须是可序列化的 JSON 对象") from exc
    if len(serialized.encode("utf-8")) > EVIDENCE_MAX_BYTES:
        raise DomainValidationError(f"evidence 序列化后超过 {EVIDENCE_MAX_BYTES} 字节")
    return normalized


def _normalize_score(status: QualityFindingStatus, score: float | None) -> float | None:
    if status in {QualityFindingStatus.NOT_APPLICABLE, QualityFindingStatus.ERROR}:
        if score is not None:
            raise DomainValidationError(f"{status.value} 结论不得携带 score")
        return None
    if score is None:
        raise DomainValidationError(f"{status.value} 结论必须携带 score")
    if isinstance(score, bool) or not isinstance(score, int | float):
        raise DomainValidationError("score 必须是数字")
    value = float(score)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise DomainValidationError("score 必须在 0 到 1 之间")
    return round(value, 4)


@dataclass(frozen=True, slots=True)
class QualityFindingDraft:
    """规则源产出的未落库结论。

    ``finding_id`` / ``run_id`` 由应用层在事务内生成，规则源不接触数据库。
    """

    rule_id: str
    rule_version: str
    category: QualityCategory | str
    severity: QualitySeverity | str
    status: QualityFindingStatus | str
    message: str
    evidence: Mapping[str, object] | None = None
    score: float | None = None
    subject_ref: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rule_id",
            _required_text(self.rule_id, "rule_id", max_length=RULE_ID_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "rule_version",
            _required_text(
                str(self.rule_version),
                "rule_version",
                max_length=RULE_VERSION_MAX_LENGTH,
            ),
        )
        category = coerce_category(self.category)
        if category is QualityCategory.OVERALL:
            raise DomainValidationError("OVERALL 是派生类别，规则不得直接产出")
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "severity", coerce_severity(self.severity))
        status = coerce_finding_status(self.status)
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "message",
            _required_text(self.message, "message", max_length=MESSAGE_MAX_LENGTH),
        )
        object.__setattr__(self, "evidence", _normalize_evidence(self.evidence))
        object.__setattr__(self, "score", _normalize_score(status, self.score))
        object.__setattr__(
            self,
            "subject_ref",
            _optional_text(self.subject_ref, "subject_ref", max_length=SUBJECT_REF_MAX_LENGTH),
        )


@dataclass(frozen=True, slots=True)
class QualityFinding:
    """一条已落库的质量结论（不可变）。"""

    finding_id: UUID
    project_id: UUID
    run_id: UUID
    rule_id: str
    rule_version: str
    category: QualityCategory | str
    severity: QualitySeverity | str
    status: QualityFindingStatus | str
    message: str
    created_at: datetime
    evidence: Mapping[str, object] | None = None
    score: float | None = None
    subject_ref: str = ""

    def __post_init__(self) -> None:
        for name in ("finding_id", "project_id", "run_id"):
            _uuid(getattr(self, name), name)
        draft = QualityFindingDraft(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            category=self.category,
            severity=self.severity,
            status=self.status,
            message=self.message,
            evidence=self.evidence,
            score=self.score,
            subject_ref=self.subject_ref,
        )
        object.__setattr__(self, "rule_id", draft.rule_id)
        object.__setattr__(self, "rule_version", draft.rule_version)
        object.__setattr__(self, "category", draft.category)
        object.__setattr__(self, "severity", draft.severity)
        object.__setattr__(self, "status", draft.status)
        object.__setattr__(self, "message", draft.message)
        object.__setattr__(self, "evidence", draft.evidence)
        object.__setattr__(self, "score", draft.score)
        object.__setattr__(self, "subject_ref", draft.subject_ref)
        object.__setattr__(self, "created_at", _aware(self.created_at, "created_at"))

    @classmethod
    def from_draft(
        cls,
        *,
        finding_id: UUID,
        project_id: UUID,
        run_id: UUID,
        draft: QualityFindingDraft,
        created_at: datetime,
    ) -> QualityFinding:
        return cls(
            finding_id=finding_id,
            project_id=project_id,
            run_id=run_id,
            rule_id=draft.rule_id,
            rule_version=draft.rule_version,
            category=draft.category,
            severity=draft.severity,
            status=draft.status,
            message=draft.message,
            evidence=draft.evidence,
            score=draft.score,
            subject_ref=draft.subject_ref,
            created_at=created_at,
        )


@dataclass(frozen=True, slots=True)
class QualityRun:
    """一次质量运行。

    ``category_summary`` 是六类聚合结果的物化快照（含 ``OVERALL``）。把聚合
    结果随运行一起落库，查询详情时不必重新扫描全部 findings；finding 明细
    仍可通过子资源分页读取。
    """

    run_id: UUID
    project_id: UUID
    subject_type: QualitySubjectType | str
    subject_id: UUID
    rule_set_version: str
    status: QualityRunStatus | str
    score: float | None
    finding_count: int
    error_count: int
    warn_count: int
    category_summary: Mapping[str, Mapping[str, object]]
    started_at: datetime
    finished_at: datetime
    created_at: datetime
    created_by: UUID
    findings: tuple[QualityFinding, ...] = field(default=())

    def __post_init__(self) -> None:
        for name in ("run_id", "project_id", "subject_id", "created_by"):
            _uuid(getattr(self, name), name)
        object.__setattr__(self, "subject_type", coerce_subject_type(self.subject_type))
        if not is_sha256_version(self.rule_set_version):
            raise DomainValidationError("rule_set_version 必须是 sha256:<64 hex>")
        object.__setattr__(self, "status", coerce_run_status(self.status))
        for name in ("finding_count", "error_count", "warn_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise DomainValidationError(f"{name} 必须是非负整数")
            if value > self.finding_count:
                raise DomainValidationError(f"{name} 不得大于 finding_count")
        if self.score is not None:
            if isinstance(self.score, bool) or not isinstance(self.score, int | float):
                raise DomainValidationError("score 必须是数字")
            value = float(self.score)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise DomainValidationError("score 必须在 0 到 1 之间")
            object.__setattr__(self, "score", round(value, 4))
        summary = {str(key): dict(value) for key, value in dict(self.category_summary).items()}
        missing = [item.value for item in QualityCategory if item.value not in summary]
        if missing:
            raise DomainValidationError(f"category_summary 缺少类别：{'、'.join(missing)}")
        object.__setattr__(self, "category_summary", summary)
        object.__setattr__(self, "started_at", _aware(self.started_at, "started_at"))
        object.__setattr__(self, "finished_at", _aware(self.finished_at, "finished_at"))
        object.__setattr__(self, "created_at", _aware(self.created_at, "created_at"))
        if self.finished_at < self.started_at:
            raise DomainValidationError("finished_at 不得早于 started_at")
        findings = tuple(self.findings)
        for finding in findings:
            if not isinstance(finding, QualityFinding):
                raise DomainValidationError("findings 只能包含 QualityFinding")
            if finding.project_id != self.project_id or finding.run_id != self.run_id:
                raise DomainValidationError("finding 的项目或运行与所属运行不一致")
        object.__setattr__(self, "findings", findings)


def finding_drafts_from_iterable(
    drafts: Iterable[QualityFindingDraft],
) -> tuple[QualityFindingDraft, ...]:
    """类型收窄辅助：调用方传入错误的元素类型时立即失败。"""
    result: list[QualityFindingDraft] = []
    for draft in drafts:
        if not isinstance(draft, QualityFindingDraft):
            raise DomainValidationError("规则源必须返回 QualityFindingDraft")
        result.append(draft)
    return tuple(result)


__all__ = [
    "EVIDENCE_MAX_BYTES",
    "QualityFinding",
    "QualityFindingDraft",
    "QualityRun",
    "finding_drafts_from_iterable",
]
