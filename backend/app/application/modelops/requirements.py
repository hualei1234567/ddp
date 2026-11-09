"""数据需求闭环用例。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.modelops.audit import record_change
from app.application.modelops.commands import NewRequirement
from app.application.modelops.idempotency import IdempotencyGuard, validate_key
from app.application.modelops.ports import (
    AuditRepository,
    DatasetGate,
    EvaluationRepository,
    IdempotencyRepository,
    OutboxRepository,
    RequirementRepository,
    UnitOfWork,
)
from app.domain.identity.models import ProjectScope
from app.domain.modelops.enums import RequirementStatus
from app.domain.modelops.errors import (
    DataRequirementApprovalForbiddenError,
    DataRequirementDatasetInvalidError,
    DataRequirementNotSatisfiedError,
    DataRequirementRetestInvalidError,
    DataRequirementTerminalError,
    DomainValidationError,
    DuplicateResourceError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.modelops.models import DataRequirement, new_requirement
from app.domain.modelops.state_machines import target_status, validate_action_name
from app.domain.modelops.validation import optional_text

_RESOURCE = "DATA_REQUIREMENT"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RequirementUseCases:
    def __init__(
        self,
        *,
        requirements: RequirementRepository,
        evaluations: EvaluationRepository,
        datasets: DatasetGate,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._requirements = requirements
        self._evaluations = evaluations
        self._datasets = datasets
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, requirement_id: UUID) -> DataRequirement:
        item = self._requirements.get(scope, requirement_id)
        if item is None:
            raise ResourceNotFoundError("数据需求", requirement_id)
        return item

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        priority: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DataRequirement]:
        return self._requirements.list(
            scope, status=status, priority=priority, after_id=after_id, limit=limit
        )

    def create(
        self,
        scope: ProjectScope,
        command: NewRequirement,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DataRequirement:
        key = validate_key(idempotency_key)
        body = {"code": command.code, "title": command.title}
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        if self._requirements.get_by_code(scope, command.code.strip()) is not None:
            raise DuplicateResourceError("数据需求编码已存在")
        requirement = new_requirement(
            requirement_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            title=command.title,
            source_type=command.source_type,
            dimensions=command.dimensions,
            owner_id=command.owner_id,
            target_episode_count=command.target_episode_count,
            description=command.description,
            source_feedback_ids=command.source_feedback_ids,
            source_evaluation_ids=command.source_evaluation_ids,
            quality_thresholds=command.quality_thresholds,
            priority=command.priority,
            due_at=command.due_at,
            actor_id=scope.actor_id,
            now=now,
        )
        created = self._requirements.add(scope, requirement)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=created.requirement_id,
            action="create",
            from_status=None,
            to_status=created.status,
            reason=None,
            request_id=request_id,
            details={"code": created.code},
            event_type="modelops.data_requirement.created",
            event_payload={
                "requirement_id": str(created.requirement_id),
                "source_feedback_ids": [str(item) for item in created.source_feedback_ids],
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.requirement_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        requirement_id: UUID,
        action: str,
        *,
        expected_version: int,
        payload: dict[str, object] | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DataRequirement:
        if action == "link-retest":
            action_name = "link-retest"
        else:
            action_name = validate_action_name(action, family="requirement")
        payload = payload or {}
        reason = optional_text(payload.get("reason"), "reason", max_length=2000)
        key = validate_key(idempotency_key)
        body = {"action": action_name, "expected_version": expected_version}
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        requirement = self.get(scope, requirement_id)
        if requirement.row_version != expected_version:
            raise VersionConflictError("数据需求")
        if requirement.status in {
            RequirementStatus.CLOSED.value,
            RequirementStatus.REJECTED.value,
            RequirementStatus.CANCELLED.value,
        }:
            raise DataRequirementTerminalError("数据需求已关闭、驳回或取消")
        if action_name == "link-retest":
            if requirement.status not in {
                RequirementStatus.SATISFIED.value,
                RequirementStatus.CLOSED.value,
            }:
                raise InvalidStateTransitionError("数据需求", requirement.status, action_name)
            updated = self._link_retest(scope, requirement, payload)
        else:
            if action_name not in requirement.allowed_actions:
                raise InvalidStateTransitionError("数据需求", requirement.status, action_name)
            updated = self._apply_transition(scope, requirement, action_name, payload, reason, now)
        saved = self._requirements.save(scope, updated, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=requirement_id,
            action=action_name,
            from_status=requirement.status,
            to_status=saved.status,
            reason=reason,
            request_id=request_id,
            details={
                "linked_dataset_version_ids": [
                    str(item) for item in saved.linked_dataset_version_ids
                ],
                "retest_evaluation_ids": [str(item) for item in saved.retest_evaluation_ids],
            },
            event_type=f"modelops.data_requirement.{action_name}",
            event_payload={
                "requirement_id": str(requirement_id),
                "to_status": saved.status,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.requirement_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _apply_transition(
        self,
        scope: ProjectScope,
        requirement: DataRequirement,
        action: str,
        payload: dict[str, object],
        reason: str | None,
        now: datetime,
    ) -> DataRequirement:
        if action == "approve" and requirement.created_by == scope.actor_id:
            raise DataRequirementApprovalForbiddenError("提交人不得审批自己提交的需求")
        if action in {"reject", "cancel"} and not reason:
            raise DomainValidationError(f"{action} 必须提供原因")
        updates: dict[str, object] = {
            "status": target_status("requirement", requirement.status, action),
            "updated_at": now,
            "updated_by": scope.actor_id,
        }
        if action == "satisfy":
            linked = self._validated_dataset_versions(scope, payload)
            existing = list(requirement.linked_dataset_version_ids)
            for item in linked:
                if item not in existing:
                    existing.append(item)
            updates["linked_dataset_version_ids"] = existing
            updates["satisfied_at"] = now
            updates["satisfaction_evidence_ref"] = optional_text(
                payload.get("satisfaction_evidence_ref"),
                "satisfaction_evidence_ref",
                max_length=1024,
            )
        if action == "close" and not requirement.retest_evaluation_ids:
            raise DataRequirementNotSatisfiedError("需求关闭前必须有关联的复测评测")
        return replace(requirement, **updates)

    def _validated_dataset_versions(
        self, scope: ProjectScope, payload: dict[str, object]
    ) -> list[UUID]:
        raw = payload.get("dataset_version_ids")
        if not isinstance(raw, list) or not raw:
            raise DataRequirementNotSatisfiedError("满足需求必须关联至少一个冻结数据集版本")
        result: list[UUID] = []
        for value in raw:
            try:
                version_id = UUID(str(value))
            except ValueError as exc:
                raise DataRequirementDatasetInvalidError("dataset_version_ids 含非法 UUID") from exc
            state = self._datasets.version_state(scope.project_id, version_id)
            if state is None:
                raise DataRequirementDatasetInvalidError("数据集版本不存在或跨项目")
            status, validity = state
            if status != "FROZEN" or validity != "VALID":
                raise DataRequirementDatasetInvalidError("数据集版本必须已冻结且有效")
            if self._datasets.item_count(scope.project_id, version_id) < 1:
                raise DataRequirementNotSatisfiedError("数据集版本没有样本")
            if version_id not in result:
                result.append(version_id)
        return result

    def _link_retest(
        self,
        scope: ProjectScope,
        requirement: DataRequirement,
        payload: dict[str, object],
    ) -> DataRequirement:
        raw = payload.get("evaluation_id")
        try:
            evaluation_id = UUID(str(raw))
        except (ValueError, TypeError) as exc:
            raise DataRequirementRetestInvalidError("evaluation_id 必须是 UUID") from exc
        evaluation = self._evaluations.get(scope, evaluation_id)
        if evaluation is None:
            raise DataRequirementRetestInvalidError("复测评测不存在或跨项目")
        if (
            requirement.linked_dataset_version_ids
            and evaluation.dataset_version_id not in requirement.linked_dataset_version_ids
        ):
            raise DataRequirementRetestInvalidError("复测评测必须引用需求关联的数据集版本")
        retests = list(requirement.retest_evaluation_ids)
        if evaluation_id not in retests:
            retests.append(evaluation_id)
        return replace(
            requirement,
            retest_evaluation_ids=retests,
            updated_at=_utcnow(),
            updated_by=scope.actor_id,
        )

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> DataRequirement:
        if not response_ref:
            raise ResourceNotFoundError("数据需求", response_ref)
        try:
            return self.get(scope, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("数据需求", response_ref) from exc


__all__ = ["RequirementUseCases"]
