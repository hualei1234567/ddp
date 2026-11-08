"""TrainingRequest 与不可变 Revision 领域实体。

规则：
- revision 快照由服务端规范化并计算 ``revision_sha256``；
- 批准后的 revision 内容不得原地修改，变更必须创建新 revision；
- 提交人不得审批自己的需求；
- 未知状态/动作、终态回退一律显式失败。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import UUID

from app.domain.datasets.canonical import canonical_json, plain, sha256_value
from app.domain.datasets.enums import ExportFormat
from app.domain.datasets.errors import (
    DomainValidationError,
    ExportFormatUnsupportedError,
    ExportFormatVersionUnsupportedError,
)
from app.domain.datasets.training_enums import (
    RevisionStatus,
    TrainingPriority,
    TrainingRequestStatus,
    TrainingType,
)
from app.domain.datasets.training_errors import (
    TrainingRequestApprovalForbiddenError,
    TrainingRequestImmutableError,
    TrainingRequestIncompleteError,
)
from app.domain.datasets.training_state_machines import (
    allowed_request_actions,
    allowed_revision_actions,
    request_target,
    revision_target,
)
from app.domain.datasets.validation import (
    aware_datetime,
    coerce_enum,
    non_negative_int,
    optional_aware_datetime,
    optional_sha256,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    sha256_value as ensure_sha,
    text_value,
)

EXPORT_FORMAT_VERSION_LE_ROBOT = "ddp.lerobot/1.0.0"
_MAX_SNAPSHOT_BYTES = 256 * 1024
_FORBIDDEN_THRESHOLD_KEYS = frozenset(
    {"token", "secret", "api_key", "apikey", "password", "credential", "access_key"}
)


def _text_list(
    value: object,
    field_name: str,
    *,
    max_length: int,
    min_items: int = 0,
) -> list[str]:
    if value is None:
        value = []
    if not isinstance(value, list | tuple):
        raise TrainingRequestIncompleteError(f"{field_name} 必须是数组")
    items: list[str] = []
    for item in value:
        cleaned = text_value(item, field_name, max_length=max_length)
        if cleaned not in items:
            items.append(cleaned)
    if len(items) < min_items:
        raise TrainingRequestIncompleteError(f"{field_name} 至少需要 {min_items} 项")
    return items


def _schema_versions(value: object) -> list[dict[str, object]]:
    if value is None:
        value = []
    if not isinstance(value, list | tuple):
        raise TrainingRequestIncompleteError("required_schema_versions 必须是数组")
    result: list[dict[str, object]] = []
    seen: set[tuple[UUID, int]] = set()
    for item in value:
        if not isinstance(item, dict):
            raise TrainingRequestIncompleteError("required_schema_versions 条目必须是对象")
        schema_id = required_uuid(item.get("schema_id"), "schema_id")
        version = positive_int(item.get("schema_version"), "schema_version")
        key = (schema_id, version)
        if key in seen:
            continue
        seen.add(key)
        result.append({"schema_id": schema_id, "schema_version": version})
    return result


def _split_requirements(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TrainingRequestIncompleteError("split_requirements 必须是对象或 null")
    unknown = set(value) - {"train_ratio", "validation_ratio", "test_ratio", "group_by"}
    if unknown:
        raise TrainingRequestIncompleteError("split_requirements 包含未知字段")
    result: dict[str, object] = {}
    ratios: list[float] = []
    for name in ("train_ratio", "validation_ratio", "test_ratio"):
        raw = value.get(name)
        if raw is None:
            continue
        if not isinstance(raw, int | float) or isinstance(raw, bool):
            raise TrainingRequestIncompleteError(f"{name} 必须是数字")
        ratio = float(raw)
        if not 0 <= ratio <= 1:
            raise TrainingRequestIncompleteError(f"{name} 必须在 0 到 1 之间")
        result[name] = ratio
        ratios.append(ratio)
    if not ratios:
        raise TrainingRequestIncompleteError("split_requirements 至少需要一个划分比例")
    if abs(sum(ratios) - 1.0) > 1e-6:
        raise TrainingRequestIncompleteError("划分比例之和必须为 1")
    if "group_by" in value:
        result["group_by"] = _text_list(
            value.get("group_by"),
            "split_requirements.group_by",
            max_length=100,
        )
    return result


def _due_at(value: object) -> datetime:
    """接受带时区 datetime 或 ISO-8601 字符串；无时区一律拒绝。"""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise TrainingRequestIncompleteError("delivery_due_at 必须是 ISO-8601 时间") from exc
    return aware_datetime(value, "delivery_due_at")


def _quality_thresholds(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not value:
        raise TrainingRequestIncompleteError("quality_thresholds 至少需要一个阈值")
    lowered = {str(key).lower() for key in value}
    if lowered & _FORBIDDEN_THRESHOLD_KEYS:
        raise TrainingRequestIncompleteError("quality_thresholds 不得包含凭据类字段")
    try:
        normalized = plain(dict(value))
        sha256_value(normalized)
    except (TypeError, ValueError) as exc:
        raise TrainingRequestIncompleteError("quality_thresholds 必须是可规范化 JSON") from exc
    if not isinstance(normalized, dict):
        raise TrainingRequestIncompleteError("quality_thresholds 必须是对象")
    return normalized


def normalize_requirement_snapshot(value: object) -> dict[str, object]:
    """校验并规范化需求快照；缺项或非法值一律拒绝。"""
    if not isinstance(value, dict):
        raise TrainingRequestIncompleteError("snapshot 必须是对象")
    allowed = {
        "business_goal",
        "model_type",
        "training_type",
        "data_modalities",
        "scene_targets",
        "action_targets",
        "robot_targets",
        "device_targets",
        "required_labels",
        "required_schema_versions",
        "target_episode_count",
        "target_duration_ns",
        "quality_thresholds",
        "split_requirements",
        "leakage_policy_id",
        "format",
        "format_version",
        "delivery_due_at",
        "acceptance_method",
        "priority",
    }
    missing = allowed - set(value)
    if missing:
        raise TrainingRequestIncompleteError(
            "需求快照缺少字段：" + ", ".join(sorted(missing))
        )
    unknown = set(value) - allowed
    if unknown:
        raise TrainingRequestIncompleteError(
            "需求快照包含未知字段：" + ", ".join(sorted(unknown))
        )

    episode_target = value.get("target_episode_count")
    duration_target = value.get("target_duration_ns")
    if episode_target is None and duration_target is None:
        raise TrainingRequestIncompleteError("目标样本数与目标时长至少填写一个")
    if episode_target is not None:
        episode_target = positive_int(episode_target, "target_episode_count")
    if duration_target is not None:
        duration_target = positive_int(duration_target, "target_duration_ns")

    if value.get("format") != ExportFormat.LE_ROBOT.value:
        raise ExportFormatUnsupportedError("仅支持 LE_ROBOT 训练数据格式")
    if value.get("format_version") != EXPORT_FORMAT_VERSION_LE_ROBOT:
        raise ExportFormatVersionUnsupportedError(
            f"仅支持格式版本 {EXPORT_FORMAT_VERSION_LE_ROBOT}"
        )

    snapshot: dict[str, object] = {
        "business_goal": text_value(
            value.get("business_goal"), "business_goal", max_length=2000
        ),
        "model_type": text_value(value.get("model_type"), "model_type", max_length=100),
        "training_type": coerce_enum(
            value.get("training_type"), TrainingType, "training_type"
        ).value,
        "data_modalities": _text_list(
            value.get("data_modalities"),
            "data_modalities",
            max_length=64,
            min_items=1,
        ),
        "scene_targets": _text_list(value.get("scene_targets"), "scene_targets", max_length=100),
        "action_targets": _text_list(
            value.get("action_targets"), "action_targets", max_length=100
        ),
        "robot_targets": _text_list(value.get("robot_targets"), "robot_targets", max_length=100),
        "device_targets": _text_list(
            value.get("device_targets"), "device_targets", max_length=100
        ),
        "required_labels": _text_list(
            value.get("required_labels"), "required_labels", max_length=100
        ),
        "required_schema_versions": _schema_versions(value.get("required_schema_versions")),
        "target_episode_count": episode_target,
        "target_duration_ns": duration_target,
        "quality_thresholds": _quality_thresholds(value.get("quality_thresholds")),
        "split_requirements": _split_requirements(value.get("split_requirements")),
        "leakage_policy_id": optional_uuid(
            value.get("leakage_policy_id"), "leakage_policy_id"
        ),
        "format": ExportFormat.LE_ROBOT.value,
        "format_version": EXPORT_FORMAT_VERSION_LE_ROBOT,
        "delivery_due_at": _due_at(value.get("delivery_due_at")).isoformat(),
        "acceptance_method": text_value(
            value.get("acceptance_method"), "acceptance_method", max_length=500
        ),
        "priority": coerce_enum(
            value.get("priority"), TrainingPriority, "priority"
        ).value,
    }
    if len(canonical_json(snapshot).encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
        raise TrainingRequestIncompleteError("需求快照过大")
    return snapshot


@dataclass(frozen=True, slots=True)
class TrainingRequestRevision:
    revision_id: UUID
    project_id: UUID
    training_request_id: UUID
    revision_no: int
    snapshot: dict[str, object]
    status: RevisionStatus | str = RevisionStatus.DRAFT
    change_reason: str | None = None
    supersedes_revision_id: UUID | None = None
    submitted_by: UUID | None = None
    submitted_at: datetime | None = None
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    rejected_by: UUID | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    revision_sha256: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    created_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("revision_id", "project_id", "training_request_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "revision_no",
            positive_int(self.revision_no, "revision_no"),
        )
        normalized = normalize_requirement_snapshot(self.snapshot)
        object.__setattr__(self, "snapshot", normalized)
        digest = sha256_value(normalized)
        if (
            self.revision_sha256 is not None
            and ensure_sha(self.revision_sha256, "revision_sha256") != digest
        ):
            raise TrainingRequestImmutableError("revision_sha256 与快照内容不一致")
        object.__setattr__(self, "revision_sha256", digest)
        object.__setattr__(self, "status", coerce_enum(self.status, RevisionStatus, "status"))
        object.__setattr__(
            self,
            "change_reason",
            optional_text(self.change_reason, "change_reason", max_length=2000),
        )
        for name in (
            "supersedes_revision_id",
            "submitted_by",
            "reviewed_by",
            "approved_by",
            "rejected_by",
            "created_by",
        ):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        for name in (
            "submitted_at",
            "reviewed_at",
            "approved_at",
            "rejected_at",
            "created_at",
        ):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        object.__setattr__(
            self,
            "rejection_reason",
            optional_text(self.rejection_reason, "rejection_reason", max_length=2000),
        )
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))
        self._assert_invariants()

    def _assert_invariants(self) -> None:
        status = RevisionStatus(self.status)
        if status in {RevisionStatus.SUBMITTED, RevisionStatus.PENDING_REVIEW}:
            if self.submitted_by is None or self.submitted_at is None:
                raise DomainValidationError("提交后的 revision 必须记录提交人/时间")
        if status is RevisionStatus.APPROVED:
            if self.approved_by is None or self.approved_at is None:
                raise DomainValidationError("APPROVED revision 必须记录审批人/时间")
            if self.submitted_by is not None and self.approved_by == self.submitted_by:
                raise TrainingRequestApprovalForbiddenError("提交人不得审批自己的需求")
        if status is RevisionStatus.REJECTED:
            if self.rejected_by is None or self.rejected_at is None:
                raise DomainValidationError("REJECTED revision 必须记录驳回人/时间")
            if self.rejection_reason is None:
                raise DomainValidationError("REJECTED revision 必须记录驳回原因")

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_revision_actions(RevisionStatus(self.status))

    def with_content(
        self,
        snapshot: object,
        *,
        change_reason: str | None,
        actor_id: UUID,
        now: datetime,
    ) -> TrainingRequestRevision:
        if self.status is not RevisionStatus.DRAFT:
            raise TrainingRequestImmutableError("只有 DRAFT revision 可以原地修改")
        normalized = normalize_requirement_snapshot(snapshot)
        return replace(
            self,
            snapshot=normalized,
            revision_sha256=sha256_value(normalized),
            change_reason=(
                optional_text(change_reason, "change_reason", max_length=2000)
                if change_reason is not None
                else self.change_reason
            ),
            row_version=self.row_version + 1,
        )

    def submit(self, *, actor_id: UUID, now: datetime) -> TrainingRequestRevision:
        target = revision_target(RevisionStatus(self.status), "submit")
        return replace(
            self,
            status=target,
            submitted_by=actor_id,
            submitted_at=now,
            row_version=self.row_version + 1,
        )

    def start_review(self, *, now: datetime) -> TrainingRequestRevision:
        target = revision_target(RevisionStatus(self.status), "system-review-start")
        return replace(self, status=target, reviewed_at=now, row_version=self.row_version + 1)

    def approve(self, *, actor_id: UUID, now: datetime) -> TrainingRequestRevision:
        if self.submitted_by is not None and actor_id == self.submitted_by:
            raise TrainingRequestApprovalForbiddenError("提交人不得审批自己的需求")
        target = revision_target(RevisionStatus(self.status), "approve")
        return replace(
            self,
            status=target,
            approved_by=actor_id,
            approved_at=now,
            row_version=self.row_version + 1,
        )

    def reject(
        self,
        *,
        actor_id: UUID,
        now: datetime,
        reason: str,
    ) -> TrainingRequestRevision:
        if self.submitted_by is not None and actor_id == self.submitted_by:
            raise TrainingRequestApprovalForbiddenError("提交人不得驳回自己的需求")
        target = revision_target(RevisionStatus(self.status), "reject")
        return replace(
            self,
            status=target,
            rejected_by=actor_id,
            rejected_at=now,
            rejection_reason=text_value(reason, "rejection_reason", max_length=2000),
            row_version=self.row_version + 1,
        )

    def supersede(self) -> TrainingRequestRevision:
        target = revision_target(RevisionStatus(self.status), "system-supersede")
        return replace(self, status=target, row_version=self.row_version + 1)


@dataclass(frozen=True, slots=True)
class TrainingRequest:
    training_request_id: UUID
    project_id: UUID
    code: str
    title: str
    priority: TrainingPriority | str
    owner_id: UUID
    status: TrainingRequestStatus | str = TrainingRequestStatus.DRAFT
    current_revision_id: UUID | None = None
    current_revision_no: int | None = None
    blocked_reason: str | None = None
    blocked_from_status: str | None = None
    cancel_reason: str | None = None
    closed_at: datetime | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("training_request_id", "project_id", "owner_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(self, "code", text_value(self.code, "code", max_length=64))
        object.__setattr__(self, "title", text_value(self.title, "title", max_length=200))
        object.__setattr__(
            self,
            "priority",
            coerce_enum(self.priority, TrainingPriority, "priority"),
        )
        object.__setattr__(
            self, "status", coerce_enum(self.status, TrainingRequestStatus, "status")
        )
        object.__setattr__(
            self,
            "current_revision_id",
            optional_uuid(self.current_revision_id, "current_revision_id"),
        )
        if self.current_revision_no is not None:
            object.__setattr__(
                self,
                "current_revision_no",
                positive_int(self.current_revision_no, "current_revision_no"),
            )
        for name, limit in (
            ("blocked_reason", 2000),
            ("blocked_from_status", 64),
            ("cancel_reason", 2000),
        ):
            object.__setattr__(
                self,
                name,
                optional_text(getattr(self, name), name, max_length=limit),
            )
        object.__setattr__(
            self, "closed_at", optional_aware_datetime(self.closed_at, "closed_at")
        )
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))
        for name in ("created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        for name in ("created_by", "updated_by"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        if self.status is TrainingRequestStatus.CLOSED and self.closed_at is None:
            raise DomainValidationError("CLOSED 需求必须记录关闭时间")
        if self.status is TrainingRequestStatus.BLOCKED and self.blocked_reason is None:
            raise DomainValidationError("BLOCKED 需求必须记录阻塞原因")
        if self.status is TrainingRequestStatus.CANCELLED and self.cancel_reason is None:
            raise DomainValidationError("CANCELLED 需求必须记录取消原因")

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_request_actions(TrainingRequestStatus(self.status))

    def with_revision(
        self,
        revision: TrainingRequestRevision,
        *,
        now: datetime,
        actor_id: UUID,
    ) -> TrainingRequest:
        status = TrainingRequestStatus(self.status)
        if status in {TrainingRequestStatus.CLOSED, TrainingRequestStatus.CANCELLED}:
            raise TrainingRequestImmutableError("终态需求不能创建新 revision")
        if status is not TrainingRequestStatus.DRAFT:
            status = request_target(status, "new-revision")
        return replace(
            self,
            status=status,
            current_revision_id=revision.revision_id,
            current_revision_no=revision.revision_no,
            priority=revision.snapshot["priority"],
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def apply_action(
        self,
        action: str,
        *,
        now: datetime,
        actor_id: UUID,
        reason: str | None = None,
        restore_status: str | None = None,
    ) -> TrainingRequest:
        current = TrainingRequestStatus(self.status)
        if action == "unblock":
            if reason is None or not reason.strip():
                raise DomainValidationError("恢复需求必须填写原因")
            target_value = restore_status or self.blocked_from_status
            if target_value is None:
                raise DomainValidationError("恢复需求必须指定目标状态")
            try:
                target = TrainingRequestStatus(target_value)
            except ValueError as exc:
                raise DomainValidationError("恢复目标状态非法") from exc
            if target not in {
                TrainingRequestStatus.DRAFT,
                TrainingRequestStatus.APPROVED,
                TrainingRequestStatus.IN_DELIVERY,
                TrainingRequestStatus.DELIVERED,
                TrainingRequestStatus.REVISION_REQUIRED,
            }:
                raise DomainValidationError("恢复目标状态不允许")
        else:
            target = request_target(current, action)

        changes: dict[str, object] = {
            "status": target,
            "updated_at": now,
            "updated_by": actor_id,
            "row_version": self.row_version + 1,
        }
        if action == "block":
            changes.update(
                blocked_reason=text_value(reason, "blocked_reason", max_length=2000),
                blocked_from_status=current.value,
            )
        elif action == "unblock":
            changes.update(blocked_reason=None, blocked_from_status=None)
        elif action == "cancel":
            changes.update(
                cancel_reason=text_value(reason, "cancel_reason", max_length=2000),
            )
        elif action == "close":
            changes.update(closed_at=now)
        return replace(self, **changes)

    def _simple_transition(
        self,
        action: str,
        *,
        now: datetime,
        actor_id: UUID,
    ) -> TrainingRequest:
        target = request_target(TrainingRequestStatus(self.status), action)
        return replace(
            self,
            status=target,
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def submit_revision(self, *, now: datetime, actor_id: UUID) -> TrainingRequest:
        current = TrainingRequestStatus(self.status)
        submitted = request_target(current, "submit-revision")
        review = request_target(submitted, "system-review-start")
        return replace(
            self,
            status=review,
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def approve_revision(self, *, now: datetime, actor_id: UUID) -> TrainingRequest:
        return self._simple_transition("approve", now=now, actor_id=actor_id)

    def reject_revision(self, *, now: datetime, actor_id: UUID) -> TrainingRequest:
        return self._simple_transition("reject", now=now, actor_id=actor_id)

    def start_delivery(self, *, now: datetime, actor_id: UUID) -> TrainingRequest:
        return self._simple_transition("start-delivery", now=now, actor_id=actor_id)

    def mark_delivered(self, *, now: datetime, actor_id: UUID) -> TrainingRequest:
        return self._simple_transition("mark-delivered", now=now, actor_id=actor_id)

    def accept_delivery(self, *, now: datetime, actor_id: UUID) -> TrainingRequest:
        return self._simple_transition("accept-delivery", now=now, actor_id=actor_id)

    def require_revision(self, *, now: datetime, actor_id: UUID) -> TrainingRequest:
        return self._simple_transition("require-revision", now=now, actor_id=actor_id)


def new_training_request(
    *,
    training_request_id: UUID,
    revision_id: UUID,
    project_id: UUID,
    code: str,
    title: str,
    owner_id: UUID,
    snapshot: object,
    actor_id: UUID,
    now: datetime,
) -> tuple[TrainingRequest, TrainingRequestRevision]:
    normalized = normalize_requirement_snapshot(snapshot)
    request = TrainingRequest(
        training_request_id=training_request_id,
        project_id=project_id,
        code=code,
        title=title,
        priority=normalized["priority"],
        owner_id=owner_id,
        status=TrainingRequestStatus.DRAFT,
        current_revision_id=revision_id,
        current_revision_no=1,
        created_at=now,
        updated_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )
    revision = TrainingRequestRevision(
        revision_id=revision_id,
        project_id=project_id,
        training_request_id=training_request_id,
        revision_no=1,
        snapshot=normalized,
        status=RevisionStatus.DRAFT,
        change_reason=None,
        created_at=now,
        created_by=actor_id,
    )
    return request, revision


def new_revision(
    *,
    revision_id: UUID,
    request: TrainingRequest,
    previous: TrainingRequestRevision,
    snapshot: object,
    change_reason: str,
    actor_id: UUID,
    now: datetime,
) -> TrainingRequestRevision:
    reason = text_value(change_reason, "change_reason", max_length=2000)
    if previous.revision_no != request.current_revision_no:
        raise DomainValidationError("只能基于当前 revision 创建新 revision")
    return TrainingRequestRevision(
        revision_id=revision_id,
        project_id=request.project_id,
        training_request_id=request.training_request_id,
        revision_no=previous.revision_no + 1,
        snapshot=snapshot,
        status=RevisionStatus.DRAFT,
        change_reason=reason,
        supersedes_revision_id=previous.revision_id,
        created_at=now,
        created_by=actor_id,
    )


__all__ = [
    "EXPORT_FORMAT_VERSION_LE_ROBOT",
    "TrainingRequest",
    "TrainingRequestRevision",
    "new_revision",
    "new_training_request",
    "normalize_requirement_snapshot",
]
