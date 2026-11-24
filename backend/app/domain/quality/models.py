"""质量运行与结论实体。

**结论**是只追加的事实：一次质量检查算出的结果不可变，"改结论"的正确做法
是重跑（新 run），而不是编辑旧 run。因此这里不引入 ``version`` /
``updated_at`` 这类会暗示"可更新"的字段。

**生命周期**（V31-030）在成功前会推进：``QualityRun`` 除了结论，还携带
``lifecycle`` / ``attempt`` / ``error_*``。状态迁移只经 ``mark_*`` 方法，
每条边在 :data:`~app.domain.quality.enums.QUALITY_RUN_TRANSITIONS` 中显式
声明；未完成的运行没有任何结论字段。

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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.quality.enums import (
    QUALITY_RUN_TRANSITIONS,
    QualityCategory,
    QualityFindingStatus,
    QualityRunLifecycle,
    QualityRunStatus,
    QualitySeverity,
    QualitySubjectType,
    coerce_category,
    coerce_finding_status,
    coerce_lifecycle,
    coerce_run_status,
    coerce_severity,
    coerce_subject_type,
)
from app.domain.quality.errors import (
    DomainValidationError,
    InvalidQualityRunTransitionError,
)
from app.domain.quality.evidence import (
    EvidenceLocation,
    parse_evidence_location,
    split_evidence_location,
)
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


def _optional_code(value: object, field_name: str, *, max_length: int = 64) -> str | None:
    """可空的技术标识：``None`` 原样保留，空串视为"没填"而拒绝。

    与 :func:`_optional_text` 的区别：后者把 ``None`` 折叠成空串，会把
    "没有错误"与"错误码写成空串"混为一谈；执行失败的错误码必须能区分。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} 必须是字符串")
    text = value.strip()
    if not text:
        raise DomainValidationError(f"{field_name} 不得为空字符串")
    if len(text) > max_length:
        raise DomainValidationError(f"{field_name} 超过 {max_length} 字符")
    return text


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


def _resolve_location(
    evidence: Mapping[str, object] | None,
    location: EvidenceLocation | Mapping[str, object] | None,
) -> tuple[Mapping[str, object] | None, EvidenceLocation | None]:
    """把定位从两处来源归一：显式字段优先，其次 ``evidence["location"]``。

    两处同时给出且不一致时直接失败：静默取其一会让"界面定位到的区间"与
    "规则声称的证据区间"分叉，那正是本批次要消除的问题。
    """
    inline_location, rest = split_evidence_location(evidence)
    explicit = parse_evidence_location(location)
    if explicit is not None and inline_location is not None:
        if explicit.to_payload() != inline_location.to_payload():
            raise DomainValidationError(
                "evidence.location 与 evidence_location 不一致，请只保留一处"
            )
    resolved = explicit if explicit is not None else inline_location
    if rest is None and evidence is not None:
        # evidence 只包含定位：其余证据为空，按"没有排障信息"处理。
        rest = None
    return rest, resolved


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
    #: 证据定位（时间区间 / 流 / 字段）；规则可把它写在 ``evidence["location"]``
    #: 里，构造期自动提升，避免同一事实两处存储。
    evidence_location: EvidenceLocation | Mapping[str, object] | None = None

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
        evidence, location = _resolve_location(self.evidence, self.evidence_location)
        object.__setattr__(self, "evidence", _normalize_evidence(evidence))
        object.__setattr__(self, "evidence_location", location)
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
    evidence_location: EvidenceLocation | Mapping[str, object] | None = None

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
            evidence_location=self.evidence_location,
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
        object.__setattr__(self, "evidence_location", draft.evidence_location)
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
            evidence_location=draft.evidence_location,
            created_at=created_at,
        )


@dataclass(frozen=True, slots=True)
class QualityRun:
    """一次质量运行：生命周期 +（成功时）结论。

    ``lifecycle`` 回答"这次运行走到哪一步"，``status`` 回答"算出来的结论是
    什么"。两者**正交**：``QUEUED`` / ``RUNNING`` / ``RETRYING`` 没有结论，
    ``FAILED`` 指**执行失败**（没有可信结论），只有 ``SUCCEEDED`` 才携带
    ``status`` / ``score`` / 各类计数与 ``category_summary``。

    ``category_summary`` 是六类聚合结果的物化快照（含 ``OVERALL``）。把聚合
    结果随运行一起落库，查询详情时不必重新扫描全部 findings；finding 明细
    仍可通过子资源分页读取。

    默认 ``lifecycle=SUCCEEDED`` 保持既有调用方语义不变：此前构造
    ``QualityRun`` 的地方都是在描述"已经算完的结论"。生命周期只经
    :meth:`mark_running` 等方法迁移，构造器不接受任意字段组合冒充迁移结果。
    """

    run_id: UUID
    project_id: UUID
    subject_type: QualitySubjectType | str
    subject_id: UUID
    rule_set_version: str
    status: QualityRunStatus | str | None
    score: float | None
    finding_count: int
    error_count: int
    warn_count: int
    category_summary: Mapping[str, Mapping[str, object]]
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    created_by: UUID
    lifecycle: QualityRunLifecycle | str = QualityRunLifecycle.SUCCEEDED
    attempt: int = 1
    max_attempts: int = 1
    error_code: str | None = None
    error_summary: str | None = None
    findings: tuple[QualityFinding, ...] = field(default=())

    def __post_init__(self) -> None:
        for name in ("run_id", "project_id", "subject_id", "created_by"):
            _uuid(getattr(self, name), name)
        object.__setattr__(self, "subject_type", coerce_subject_type(self.subject_type))
        if not is_sha256_version(self.rule_set_version):
            raise DomainValidationError("rule_set_version 必须是 sha256:<64 hex>")
        lifecycle = coerce_lifecycle(self.lifecycle)
        object.__setattr__(self, "lifecycle", lifecycle)
        for name in ("attempt", "max_attempts"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise DomainValidationError(f"{name} 必须是正整数")
        if self.attempt > self.max_attempts:
            raise DomainValidationError("attempt 不得大于 max_attempts")
        object.__setattr__(self, "created_at", _aware(self.created_at, "created_at"))
        object.__setattr__(self, "error_code", _optional_code(self.error_code, "error_code"))
        object.__setattr__(
            self,
            "error_summary",
            _optional_code(self.error_summary, "error_summary", max_length=1000),
        )
        findings = tuple(self.findings)
        for finding in findings:
            if not isinstance(finding, QualityFinding):
                raise DomainValidationError("findings 只能包含 QualityFinding")
            if finding.project_id != self.project_id or finding.run_id != self.run_id:
                raise DomainValidationError("finding 的项目或运行与所属运行不一致")
        object.__setattr__(self, "findings", findings)
        if lifecycle is QualityRunLifecycle.SUCCEEDED:
            self._validate_conclusion()
        else:
            self._validate_unfinished(lifecycle)

    # ------------------------------------------------------------------ #
    # 构造入口
    # ------------------------------------------------------------------ #

    @classmethod
    def queued(
        cls,
        *,
        run_id: UUID,
        project_id: UUID,
        subject_type: QualitySubjectType | str,
        subject_id: UUID,
        rule_set_version: str,
        created_at: datetime,
        created_by: UUID,
        max_attempts: int = 1,
        attempt: int = 1,
    ) -> QualityRun:
        """一次"待执行"的运行：没有结论、没有开始/结束时间。"""
        return cls(
            run_id=run_id,
            project_id=project_id,
            subject_type=subject_type,
            subject_id=subject_id,
            rule_set_version=rule_set_version,
            status=None,
            score=None,
            finding_count=0,
            error_count=0,
            warn_count=0,
            category_summary={},
            started_at=None,
            finished_at=None,
            created_at=created_at,
            created_by=created_by,
            lifecycle=QualityRunLifecycle.QUEUED,
            attempt=attempt,
            max_attempts=max_attempts,
        )

    # ------------------------------------------------------------------ #
    # 生命周期迁移（只走状态机，不可跳步）
    # ------------------------------------------------------------------ #

    def mark_running(self, *, now: datetime) -> QualityRun:
        self._require_target(QualityRunLifecycle.RUNNING)
        return replace(
            self,
            lifecycle=QualityRunLifecycle.RUNNING,
            started_at=self.started_at or _aware(now, "now"),
            finished_at=None,
        )

    def mark_succeeded(
        self,
        *,
        status: QualityRunStatus | str,
        score: float | None,
        finding_count: int,
        error_count: int,
        warn_count: int,
        category_summary: Mapping[str, Mapping[str, object]],
        finished_at: datetime,
        findings: tuple[QualityFinding, ...] = (),
    ) -> QualityRun:
        self._require_target(QualityRunLifecycle.SUCCEEDED)
        if self.started_at is None:
            raise DomainValidationError("未开始的运行不能直接产出结论")
        return replace(
            self,
            lifecycle=QualityRunLifecycle.SUCCEEDED,
            status=status,
            score=score,
            finding_count=finding_count,
            error_count=error_count,
            warn_count=warn_count,
            category_summary=category_summary,
            finished_at=finished_at,
            findings=findings,
            # 重投后成功：上一次执行的失败信息只是历史，不能跟着结论落库。
            error_code=None,
            error_summary=None,
        )

    def mark_failed(self, *, error_code: str, error_summary: str, now: datetime) -> QualityRun:
        self._require_target(QualityRunLifecycle.FAILED)
        if self.started_at is None:
            raise DomainValidationError("未开始的运行不能标记为执行失败")
        return replace(
            self,
            lifecycle=QualityRunLifecycle.FAILED,
            finished_at=_aware(now, "now"),
            error_code=error_code,
            error_summary=error_summary,
        )

    def schedule_retry(self, *, now: datetime) -> QualityRun:
        """把一次执行失败重投；重投次数受 ``max_attempts`` 约束。"""
        self._require_target(QualityRunLifecycle.RETRYING)
        if self.attempt >= self.max_attempts:
            raise InvalidQualityRunTransitionError(
                f"{self.lifecycle.value}（已用 {self.attempt}/{self.max_attempts} 次）",
                QualityRunLifecycle.RETRYING,
            )
        return replace(
            self,
            lifecycle=QualityRunLifecycle.RETRYING,
            attempt=self.attempt + 1,
            finished_at=None,
        )

    def mark_cancelled(self, *, now: datetime) -> QualityRun:
        self._require_target(QualityRunLifecycle.CANCELLED)
        return replace(
            self,
            lifecycle=QualityRunLifecycle.CANCELLED,
            finished_at=_aware(now, "now"),
        )

    # ------------------------------------------------------------------ #
    # 内部校验
    # ------------------------------------------------------------------ #

    def _require_target(self, target: QualityRunLifecycle) -> None:
        if target not in QUALITY_RUN_TRANSITIONS[self.lifecycle]:
            raise InvalidQualityRunTransitionError(self.lifecycle, target)

    def _validate_conclusion(self) -> None:
        if self.status is None:
            raise DomainValidationError("SUCCEEDED 的运行必须给出结论 status")
        object.__setattr__(self, "status", coerce_run_status(self.status))
        if self.error_code is not None or self.error_summary is not None:
            raise DomainValidationError("已产出结论的运行不得携带执行错误信息")
        for name in ("started_at", "finished_at"):
            value = getattr(self, name)
            if value is None:
                raise DomainValidationError(f"已产出结论的运行必须有 {name}")
            object.__setattr__(self, name, _aware(value, name))
        if self.finished_at < self.started_at:  # type: ignore[operator]
            raise DomainValidationError("finished_at 不得早于 started_at")
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

    def _validate_unfinished(self, lifecycle: QualityRunLifecycle) -> None:
        if self.status is not None or self.score is not None:
            raise DomainValidationError("未产出结论的运行不得携带 status / score")
        if self.finding_count or self.error_count or self.warn_count:
            raise DomainValidationError("未产出结论的运行不得携带计数")
        if dict(self.category_summary):
            raise DomainValidationError("未产出结论的运行不得携带类别聚合")
        if self.findings:
            raise DomainValidationError("未产出结论的运行不得携带 findings")

        for name in ("started_at", "finished_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _aware(value, name))

        settled = lifecycle in {QualityRunLifecycle.FAILED, QualityRunLifecycle.CANCELLED}
        if settled and self.finished_at is None:
            raise DomainValidationError(f"{lifecycle.value} 的运行必须有 finished_at")
        if not settled and self.finished_at is not None:
            raise DomainValidationError(f"{lifecycle.value} 的运行不得有 finished_at")

        if lifecycle is QualityRunLifecycle.QUEUED:
            if self.started_at is not None:
                raise DomainValidationError("QUEUED 的运行不得有 started_at")
            if self.error_code is not None or self.error_summary is not None:
                raise DomainValidationError("QUEUED 的运行不得携带执行错误信息")
        elif lifecycle is QualityRunLifecycle.RUNNING:
            if self.started_at is None:
                raise DomainValidationError("RUNNING 的运行必须有 started_at")
            # 重投后的执行保留上一次失败的 error_code，作为"为什么又跑一遍"的
            # 依据；产出结论时由 mark_succeeded 清空。
        elif lifecycle is QualityRunLifecycle.RETRYING:
            if self.started_at is None:
                raise DomainValidationError("RETRYING 的运行必须有 started_at")
        elif lifecycle is QualityRunLifecycle.FAILED:
            if self.started_at is None:
                raise DomainValidationError("FAILED 的运行必须有 started_at")
            if self.error_code is None or self.error_summary is None:
                raise DomainValidationError("FAILED 的运行必须记录 error_code 与 error_summary")


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
    "EvidenceLocation",
    "QualityFinding",
    "QualityFindingDraft",
    "QualityRun",
    "finding_drafts_from_iterable",
]
