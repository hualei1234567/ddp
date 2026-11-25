"""标注任务聚合根。

任务批准时冻结样本集与 Schema 快照；核心绑定字段创建后不可变。
跨对象前置条件（样本集、Schema、批次、分配）由应用层与数据库兜底校验。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.annotation.enums import (
    AnnotationTaskAction,
    AnnotationTaskStatus,
    AssignmentMode,
    TaskPriority,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    ResourceImmutableError,
    TaskNotApprovedError,
    TaskNotReadyError,
)
from app.domain.annotation.review_policy import ReviewMode, ReviewPolicy
from app.domain.annotation.skill_routing import (
    SkillRoutingError,
    SkillTag,
    normalize_skill_tags,
    skill_tags_to_json,
)
from app.domain.annotation.state_machines import (
    action_requires_reason,
    task_target,
    validate_action_name,
)
from app.domain.annotation.validation import (
    CODE_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
    NAME_MAX_LENGTH,
    REASON_MAX_LENGTH,
    aware_datetime,
    coerce_enum,
    frozen_mapping,
    non_negative_int,
    normalized_text,
    optional_aware_datetime,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    validate_sha256,
)

PROGRESS_FIELDS = (
    "total_items",
    "submitted_items",
    "approved_items",
    "rejected_items",
    "rework_items",
    "arbitration_items",
)
MISSING_SEMANTICS = frozenset({"NONE", "N/A", "UNKNOWN", "STALE", "PARTIAL"})
# SLA 逾期只对"仍在推进"的任务有意义：进入终态后即使 due_at 已过也不再算逾期。
SLA_CLOSED_STATUSES = frozenset(
    {
        AnnotationTaskStatus.COMPLETED,
        AnnotationTaskStatus.CANCELLED,
        AnnotationTaskStatus.ARCHIVED,
    }
)
_BINDING_FIELDS = ("sample_set_id", "schema_id", "schema_version", "schema_sha256")
# 审批通过前的状态：此时"未批准"是正确的拒绝理由。
_PRE_APPROVAL_STATUSES = frozenset(
    {
        AnnotationTaskStatus.DRAFT,
        AnnotationTaskStatus.PENDING_REVIEW,
    }
)


def _now() -> datetime:
    return datetime.now(UTC)


def is_overdue(task: AnnotationTask, now: datetime) -> bool:
    """返回任务在 ``now`` 时刻是否已超过 SLA 截止时间（纯函数）。

    逾期是**派生事实**，不是任务状态，因此不新增状态枚举值、也不写库：

    - 没有 ``due_at`` → 没有 SLA，永不逾期；
    - 已进入终态（完成 / 取消 / 归档）→ 历史截止时间不再构成逾期；
    - 只有 ``now`` 严格晚于 ``due_at`` 才算逾期，恰好等于截止时刻不算。

    ``now`` 必须带时区，与领域内其余时间字段保持同一语义；调用方不得传
    本地时间冒充 UTC。本函数不修改 ``task``，也不自动改派或关闭任务。
    """
    moment = aware_datetime(now, "now")
    if task.due_at is None or task.status in SLA_CLOSED_STATUSES:
        return False
    return moment > task.due_at


def _normalize_progress(value: object) -> Mapping[str, object]:
    raw = frozen_mapping(value or {}, "progress")
    progress: dict[str, object] = {}
    for name in PROGRESS_FIELDS:
        progress[name] = non_negative_int(raw.get(name, 0), f"progress.{name}")
    semantics = raw.get("missing_semantics", "NONE")
    if semantics not in MISSING_SEMANTICS:
        raise DomainValidationError("progress.missing_semantics 取值非法")
    progress["missing_semantics"] = semantics
    return frozen_mapping(progress, "progress")


def _normalize_policy(value: object, field_name: str) -> Mapping[str, object]:
    if value is None:
        return frozen_mapping({}, field_name)
    return frozen_mapping(value, field_name)


@dataclass(frozen=True, slots=True)
class AnnotationTask:
    task_id: UUID
    project_id: UUID
    code: str
    name: str
    sample_set_id: UUID
    schema_id: UUID
    schema_version: int
    schema_sha256: str
    status: AnnotationTaskStatus | str = AnnotationTaskStatus.DRAFT
    description: str | None = None
    target_item_count: int = 1
    target_episode_count: int = 1
    priority: TaskPriority | str = TaskPriority.P1
    assignment_mode: AssignmentMode | str = AssignmentMode.MANUAL
    iaa_policy: Mapping[str, object] = field(default_factory=dict)
    quality_policy: Mapping[str, object] = field(default_factory=dict)
    # 技能路由配置（V52-009）：空 = 不启用路由（既有分配逻辑 100% 不变）。
    # 存储形态仍是 JSON 标签列表，聚合根内规一成 SkillTag 元组。
    required_skills: tuple[SkillTag, ...] = ()
    progress: Mapping[str, object] = field(default_factory=dict)
    quality_summary: Mapping[str, object] = field(default_factory=dict)
    due_at: datetime | None = None
    accepted_at: datetime | None = None
    completed_at: datetime | None = None
    gold_set_id: UUID | None = None
    iaa_report_id: UUID | None = None
    status_reason: str | None = None
    blocked_reason: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("task_id", "project_id", "sample_set_id", "schema_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "name",
            normalized_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "description",
            optional_text(self.description, "description", max_length=DESCRIPTION_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, AnnotationTaskStatus, "status"),
        )
        object.__setattr__(
            self,
            "priority",
            coerce_enum(self.priority, TaskPriority, "priority"),
        )
        object.__setattr__(
            self,
            "assignment_mode",
            coerce_enum(self.assignment_mode, AssignmentMode, "assignment_mode"),
        )
        object.__setattr__(
            self,
            "schema_version",
            positive_int(self.schema_version, "schema_version"),
        )
        object.__setattr__(
            self,
            "schema_sha256",
            validate_sha256(self.schema_sha256, "schema_sha256"),
        )
        object.__setattr__(
            self,
            "target_item_count",
            positive_int(self.target_item_count, "target_item_count"),
        )
        object.__setattr__(
            self,
            "target_episode_count",
            positive_int(self.target_episode_count, "target_episode_count"),
        )
        object.__setattr__(self, "iaa_policy", _normalize_policy(self.iaa_policy, "iaa_policy"))
        # 双标策略不是第二套状态机：它只决定一致性门槛与是否自动通过，
        # 存在既有的 iaa_policy JSON 中；这里在创建/读取时校验一次。
        ReviewPolicy.from_mapping(self.iaa_policy)
        object.__setattr__(
            self,
            "quality_policy",
            _normalize_policy(self.quality_policy, "quality_policy"),
        )
        # 技能标签接受 JSON 列表或 SkillTag 序列；非法元素在领域层立即报错，
        # 不允许带着脏配置进入分配链路。
        try:
            object.__setattr__(
                self,
                "required_skills",
                normalize_skill_tags(self.required_skills),
            )
        except SkillRoutingError as exc:
            raise DomainValidationError(f"required_skills 非法：{exc}") from exc
        object.__setattr__(self, "progress", _normalize_progress(self.progress))
        object.__setattr__(
            self,
            "quality_summary",
            _normalize_policy(self.quality_summary, "quality_summary"),
        )
        object.__setattr__(self, "due_at", optional_aware_datetime(self.due_at, "due_at"))
        for name in ("accepted_at", "completed_at", "created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        for name in ("gold_set_id", "iaa_report_id"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        object.__setattr__(
            self,
            "status_reason",
            optional_text(self.status_reason, "status_reason", max_length=REASON_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "blocked_reason",
            optional_text(self.blocked_reason, "blocked_reason", max_length=REASON_MAX_LENGTH),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))

    @property
    def review_policy(self) -> ReviewPolicy:
        return ReviewPolicy.from_mapping(self.iaa_policy)

    @property
    def review_mode(self) -> ReviewMode:
        return self.review_policy.review_mode

    @property
    def is_binding_frozen(self) -> bool:
        return self.status is not AnnotationTaskStatus.DRAFT

    @property
    def skill_tags_json(self) -> list[dict[str, object]]:
        """``required_skills`` 的 JSON 落库形态（仓储写库用）。"""
        return skill_tags_to_json(self.required_skills)

    def apply_action(
        self,
        action: object,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> AnnotationTask:
        action_name = validate_action_name(action, family="task")
        target = task_target(self.status, action_name)
        occurred_at = now or _now()
        cleaned_reason = optional_text(reason, "reason", max_length=REASON_MAX_LENGTH)
        if action_requires_reason("task", action_name) and cleaned_reason is None:
            raise DomainValidationError("该动作必须填写原因")
        changes: dict[str, object] = {
            "status": target,
            "updated_at": occurred_at,
        }
        if target is AnnotationTaskStatus.IN_PROGRESS:
            changes["blocked_reason"] = None
        if target is AnnotationTaskStatus.BLOCKED:
            changes["blocked_reason"] = cleaned_reason
        if action_name in {
            AnnotationTaskAction.RETURN_DRAFT.value,
            AnnotationTaskAction.PAUSE.value,
            AnnotationTaskAction.RESUME.value,
            AnnotationTaskAction.REJECT.value,
            AnnotationTaskAction.ESCALATE.value,
            AnnotationTaskAction.RESOLVE_REJECTED.value,
            AnnotationTaskAction.REWORK.value,
            AnnotationTaskAction.CANCEL.value,
        }:
            changes["status_reason"] = cleaned_reason
        if target is AnnotationTaskStatus.ACCEPTED:
            changes["accepted_at"] = self.accepted_at or occurred_at
        if target is AnnotationTaskStatus.COMPLETED:
            changes["completed_at"] = self.completed_at or occurred_at
        return replace(self, **changes)

    def assert_binding_unchanged(self, candidate: AnnotationTask) -> None:
        changed = [
            name for name in _BINDING_FIELDS if getattr(self, name) != getattr(candidate, name)
        ]
        if changed:
            raise ResourceImmutableError("任务绑定字段创建后不可修改")

    def assert_assignable(self) -> None:
        """任务是否允许分配/开始：必须已 READY 之后再执行。

        契约 ``error-codes.v1.json`` 把这条前置条件拆成两个码：
        ``annotation_task_not_approved``（还没审批通过）与
        ``annotation_task_not_ready``（已批准但没点 ready）。两者混用会让 AM
        无法判断该去找审批人还是自己点 ready，因此按状态精确区分。
        """
        if self.status in _PRE_APPROVAL_STATUSES:
            raise TaskNotApprovedError(
                "任务未批准，不能分配或开始",
                details={"task_id": str(self.task_id), "status": self.status.value},
            )
        if self.status is AnnotationTaskStatus.APPROVED:
            raise TaskNotReadyError(
                "任务未就绪，不能分配或开始",
                details={"task_id": str(self.task_id), "status": self.status.value},
            )

    def with_progress(self, **counts: int) -> AnnotationTask:
        progress = dict(self.progress)
        for name, value in counts.items():
            if name not in PROGRESS_FIELDS:
                raise DomainValidationError("未知进度字段")
            progress[name] = non_negative_int(value, f"progress.{name}")
        return replace(self, progress=progress)


__all__ = [
    "AnnotationTask",
    "MISSING_SEMANTICS",
    "PROGRESS_FIELDS",
    "SLA_CLOSED_STATUSES",
    "is_overdue",
]
