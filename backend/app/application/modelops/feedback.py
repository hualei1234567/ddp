"""模型反馈与数据缺口聚合用例。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.application.modelops.audit import record_change
from app.application.modelops.commands import NewFeedback
from app.application.modelops.gap_assembly import build_gap_view
from app.application.modelops.idempotency import IdempotencyGuard, validate_key
from app.application.modelops.ports import (
    AuditRepository,
    EpisodeCoverageDirectory,
    EvaluationRepository,
    FeedbackRepository,
    GapEvidenceDirectory,
    IdempotencyRepository,
    InferenceTaskRepository,
    ModelVersionRepository,
    OutboxRepository,
    RequirementRepository,
    UnitOfWork,
)
from app.domain.identity.models import ProjectScope
from app.domain.modelops.enums import CoverageDimension, FeedbackStatus, RequirementStatus
from app.domain.modelops.errors import (
    DomainValidationError,
    FeedbackAlreadyConvertedError,
    FeedbackSourceInvalidError,
    FeedbackTerminalError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.modelops.gaps import GapSummary, MeasuredCoverage
from app.domain.modelops.models import ModelFeedback, new_feedback, new_requirement
from app.domain.modelops.state_machines import target_status, validate_action_name
from app.domain.modelops.validation import coerce_enum, optional_aware_datetime, optional_text

_RESOURCE = "MODEL_FEEDBACK"

# 需求侧证据与既有 ``requirement_ids`` 共用一次有界查询；到顶时证据侧显式标记
# 截断（见 ``gap_summary``），不把"没扫到"读成"没有需求"。
REQUIREMENT_SCAN_LIMIT = 100

# R-13：事件类型用字面量映射表，禁止 f-string——AST 提取脚本抓不到 f-string，
# 新增事件会静默逃过 ``scripts/check_outbox_events.py`` 的归类校验。
# 键集合 = ``domain/modelops/state_machines`` 的 feedback 动作并集。
FEEDBACK_ACTION_EVENTS: dict[str, str] = {
    "triage": "modelops.feedback.triage",
    "dismiss": "modelops.feedback.dismiss",
    "create-requirement": "modelops.feedback.create-requirement",
    "resolve": "modelops.feedback.resolve",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FeedbackUseCases:
    def __init__(
        self,
        *,
        feedback: FeedbackRepository,
        requirements: RequirementRepository,
        versions: ModelVersionRepository,
        evaluations: EvaluationRepository,
        tasks: InferenceTaskRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
        coverage: EpisodeCoverageDirectory | None = None,
        coverage_stale_after: timedelta | None = None,
        evidence: GapEvidenceDirectory | None = None,
    ) -> None:
        self._feedback = feedback
        self._requirements = requirements
        self._versions = versions
        self._evaluations = evaluations
        self._tasks = tasks
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow
        # 覆盖端口与新鲜度阈值都可选注入：未注入时缺口视图退化为"仅人工值"，
        # 与改造前行为一致，不破坏既有调用点与既有测试。
        self._coverage = coverage
        self._coverage_stale_after = coverage_stale_after
        # 证据端口同样可选：未注入时只统计人工反馈与需求的来源，不查询其他来源。
        self._evidence = evidence

    def get(self, scope: ProjectScope, feedback_id: UUID) -> ModelFeedback:
        item = self._feedback.get(scope, feedback_id)
        if item is None:
            raise ResourceNotFoundError("模型反馈", feedback_id)
        return item

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        issue_type: str | None = None,
        model_version_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ModelFeedback]:
        return self._feedback.list(
            scope,
            status=status,
            issue_type=issue_type,
            model_version_id=model_version_id,
            after_id=after_id,
            limit=limit,
        )

    def create(
        self,
        scope: ProjectScope,
        command: NewFeedback,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> ModelFeedback:
        version = self._versions.get(scope, command.model_version_id)
        if version is None or version.project_id != scope.project_id:
            raise ResourceNotFoundError("模型版本", command.model_version_id)
        if command.evaluation_id is not None:
            evaluation = self._evaluations.get(scope, command.evaluation_id)
            if evaluation is None or evaluation.model_version_id != command.model_version_id:
                raise FeedbackSourceInvalidError("评测不存在、跨项目或与模型版本不一致")
        if command.inference_task_id is not None:
            task = self._tasks.get(scope, command.inference_task_id)
            if task is None or task.model_version_id != command.model_version_id:
                raise FeedbackSourceInvalidError("推理任务不存在、跨项目或与模型版本不一致")
        key = validate_key(idempotency_key)
        body = {
            "model_version_id": str(command.model_version_id),
            "issue_type": command.issue_type,
            "severity": command.severity,
            "description": command.description,
            "dimensions": [item.to_payload() for item in command.dimensions],
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        item = new_feedback(
            feedback_id=uuid4(),
            project_id=scope.project_id,
            model_version_id=command.model_version_id,
            issue_type=command.issue_type,
            severity=command.severity,
            description=command.description,
            dimensions=command.dimensions,
            evaluation_id=command.evaluation_id,
            inference_task_id=command.inference_task_id,
            evidence_refs=command.evidence_refs,
            actor_id=scope.actor_id,
            now=now,
        )
        created = self._feedback.add(scope, item)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=created.feedback_id,
            action="create",
            from_status=None,
            to_status=created.status,
            reason=None,
            request_id=request_id,
            details={"model_version_id": str(created.model_version_id)},
            event_type="modelops.feedback.created",
            event_payload={"feedback_id": str(created.feedback_id)},
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.feedback_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        feedback_id: UUID,
        action: str,
        *,
        expected_version: int,
        payload: dict[str, object] | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> ModelFeedback:
        action_name = validate_action_name(action, family="feedback")
        payload = payload or {}
        reason = optional_text(payload.get("reason"), "reason", max_length=2000)
        key = validate_key(idempotency_key)
        body = {"action": action_name, "expected_version": expected_version}
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        item = self.get(scope, feedback_id)
        if item.row_version != expected_version:
            raise VersionConflictError("模型反馈")
        if item.status in {FeedbackStatus.RESOLVED.value, FeedbackStatus.DISMISSED.value}:
            raise FeedbackTerminalError("反馈已关闭或已忽略")
        if action_name == "create-requirement":
            if item.converted_requirement_id is not None:
                raise FeedbackAlreadyConvertedError("反馈已转为数据需求")
            self._create_requirement(scope, item, payload, now=now, request_id=request_id)
            item = self.get(scope, feedback_id)
            self._idempotency.store(
                scope,
                key,
                method=method,
                path=path,
                body=body,
                response_status=201,
                response_ref=str(item.feedback_id),
                now=now,
            )
            self._uow.commit()
            return item
        if action_name not in item.allowed_actions:
            raise InvalidStateTransitionError("模型反馈", item.status, action_name)
        if action_name == "dismiss" and not reason:
            raise DomainValidationError("dismiss 必须提供原因")
        if action_name == "resolve":
            if item.converted_requirement_id is None:
                raise DomainValidationError("反馈尚未转需求，不能置为 RESOLVED")
            requirement = self._requirements.get(scope, item.converted_requirement_id)
            if requirement is None or requirement.status not in {
                RequirementStatus.SATISFIED.value,
                RequirementStatus.CLOSED.value,
            }:
                raise DomainValidationError("关联需求尚未满足，不能关闭反馈")
        target = target_status("feedback", item.status, action_name)
        updated = replace(
            item,
            status=target,
            resolution_note=optional_text(
                payload.get("resolution_note"), "resolution_note", max_length=2000
            )
            or item.resolution_note,
            updated_at=now,
        )
        saved = self._feedback.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=feedback_id,
            action=action_name,
            from_status=item.status,
            to_status=saved.status,
            reason=reason,
            request_id=request_id,
            details={},
            event_type=FEEDBACK_ACTION_EVENTS[action_name],
            event_payload={"feedback_id": str(feedback_id), "to_status": saved.status},
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.feedback_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _create_requirement(
        self,
        scope: ProjectScope,
        feedback: ModelFeedback,
        payload: dict[str, object],
        *,
        now: datetime,
        request_id: str | None,
    ) -> None:
        code = optional_text(payload.get("requirement_code"), "requirement_code", max_length=64)
        if code is None:
            code = f"DR-{feedback.feedback_id.hex[:8].upper()}"
        title = optional_text(payload.get("requirement_title"), "requirement_title", max_length=200)
        if title is None:
            title = f"模型问题补数：{feedback.description[:60]}"
        if self._requirements.get_by_code(scope, code) is not None:
            raise DomainValidationError("数据需求编码已存在")
        target_count = payload.get("target_episode_count")
        if target_count is None:
            target_count = 1
        priority = payload.get("priority")
        requirement = new_requirement(
            requirement_id=uuid4(),
            project_id=scope.project_id,
            code=code,
            title=title,
            source_type="MODEL_FEEDBACK",
            dimensions=feedback.dimensions,
            owner_id=feedback.created_by or scope.actor_id,
            target_episode_count=int(target_count),  # type: ignore[arg-type]
            description=feedback.description,
            source_feedback_ids=[feedback.feedback_id],
            source_evaluation_ids=([feedback.evaluation_id] if feedback.evaluation_id else []),
            quality_thresholds={},
            priority=str(priority or "P1"),
            due_at=optional_aware_datetime(payload.get("due_at"), "due_at"),
            actor_id=scope.actor_id,
            now=now,
        )
        created = self._requirements.add(scope, requirement)
        updated = replace(
            feedback,
            status=FeedbackStatus.REQUIREMENT_CREATED.value,
            converted_requirement_id=created.requirement_id,
            updated_at=now,
        )
        self._feedback.save(scope, updated, expected_version=feedback.row_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="DATA_REQUIREMENT",
            resource_id=created.requirement_id,
            action="create-from-feedback",
            from_status=None,
            to_status=created.status,
            reason=None,
            request_id=request_id,
            details={"feedback_id": str(feedback.feedback_id)},
            event_type="modelops.data_requirement.created",
            event_payload={
                "requirement_id": str(created.requirement_id),
                "feedback_id": str(feedback.feedback_id),
            },
            occurred_at=now,
        )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=feedback.feedback_id,
            action="create-requirement",
            from_status=feedback.status,
            to_status=updated.status,
            reason=None,
            request_id=request_id,
            details={"requirement_id": str(created.requirement_id)},
            event_type="modelops.feedback.create-requirement",
            event_payload={
                "feedback_id": str(feedback.feedback_id),
                "requirement_id": str(created.requirement_id),
            },
            occurred_at=now,
        )

    def gap_summary(
        self,
        scope: ProjectScope,
        *,
        dimension_type: str | None = None,
    ) -> GapSummary:
        """数据缺口视图：反馈侧聚合 + 真实 Episode 覆盖 + 多来源证据。

        ``current_count`` 优先用实测覆盖；实测不可得的维度保留反馈里的人工值并
        标记来源；``freshness`` 由实测数据的最近更新时间与可配阈值计算，
        不再硬编码。``sources`` 只挂到既有维度键上，挂不上的进 ``unmapped``，
        既不影响覆盖口径，也不会凭空造维度。
        """
        feedback_items = self._feedback.list_open_dimensions(scope, dimension_type=dimension_type)
        requirements = self._requirements.list(scope, limit=REQUIREMENT_SCAN_LIMIT)
        measured = self._measured_coverage(
            scope,
            dimension_type=dimension_type,
            feedback_items=feedback_items,
        )
        facts = self._evidence.evidence(scope) if self._evidence is not None else None
        return build_gap_view(
            feedback_items=feedback_items,
            requirements=requirements,
            measured=measured,
            evidence_facts=facts,
            dimension_type=dimension_type,
            now=_utcnow(),
            stale_after=self._coverage_stale_after,
            requirement_scan_limit=REQUIREMENT_SCAN_LIMIT,
        )

    def _measured_coverage(
        self,
        scope: ProjectScope,
        *,
        dimension_type: str | None,
        feedback_items: list[ModelFeedback],
    ) -> list[MeasuredCoverage]:
        """取真实覆盖；未装配覆盖端口时返回空列表（等价于只有人工值）。

        ``wanted_keys`` 把反馈里出现的维度键交给覆盖端口：封闭轴
        （ACTION/LABEL/MODALITY）在数据总体完整时可以据此给出 ``count=0`` 的
        实测结论；开放轴忽略该参数，保持"缺值不报 0"的既有口径。
        """
        if self._coverage is None:
            return []
        wanted_keys = frozenset(
            (dimension.dimension_type, dimension.dimension_value)
            for item in feedback_items
            for dimension in item.dimensions
        )
        if dimension_type is not None:
            try:
                wanted = frozenset({CoverageDimension(dimension_type).value})
            except ValueError:
                # 契约外的维度值（拼写错误等）：不查询，回退人工值路径。
                return []
            return list(
                self._coverage.coverage_by_dimension(
                    scope,
                    dimension_types=wanted,
                    wanted_keys=wanted_keys,
                )
            )
        return list(self._coverage.coverage_by_dimension(scope, wanted_keys=wanted_keys))

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> ModelFeedback:
        if not response_ref:
            raise ResourceNotFoundError("模型反馈", response_ref)
        try:
            return self.get(scope, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("模型反馈", response_ref) from exc


__all__ = ["FeedbackUseCases", "coerce_enum"]
