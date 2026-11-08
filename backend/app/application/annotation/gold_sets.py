"""黄金集用例：隐藏注入、答案访问控制、轮换与准确率评估。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.commands import NewGoldSet
from app.application.annotation.idempotency import IdempotencyGuard, validate_key
from app.application.annotation.ports import (
    AnnotationRecordRepository,
    AnnotationSchemaRepository,
    AnnotationSubmissionRepository,
    AnnotationTaskRepository,
    AuditRepository,
    GoldEvaluationRecord,
    GoldExposure,
    GoldSetAccessPolicy,
    GoldSetRepository,
    IdempotencyRepository,
    MemberDirectory,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.annotation.enums import (
    AnnotationSchemaStatus,
    GoldRotationPolicy,
    GoldSetAction,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    GoldSetAccessDeniedError,
    GoldSetExposureForbiddenError,
    GoldSetNotActiveError,
    ResourceNotFoundError,
    SchemaNotPublishedError,
    VersionConflictError,
)
from app.domain.annotation.gold_set import (
    ExpectedLabel,
    GoldItem,
    GoldSet,
    evaluate_gold_set,
)
from app.domain.annotation.schema import compute_schema_sha256
from app.domain.annotation.state_machines import (
    allowed_gold_set_actions,
    assert_gold_set_action,
    validate_action_name,
)
from app.domain.identity.models import ProjectScope

_RESOURCE_TYPE = "GOLD_SET"


class GoldSetUseCases:
    def __init__(
        self,
        gold_sets: GoldSetRepository,
        schemas: AnnotationSchemaRepository,
        submissions: AnnotationSubmissionRepository,
        records: AnnotationRecordRepository,
        tasks: AnnotationTaskRepository,
        members: MemberDirectory,
        access_policy: GoldSetAccessPolicy,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._gold_sets = gold_sets
        self._schemas = schemas
        self._submissions = submissions
        self._records = records
        self._tasks = tasks
        self._members = members
        self._access = access_policy
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, gold_set_id: UUID) -> GoldSet:
        gold_set = self._gold_sets.get(scope, gold_set_id)
        if gold_set is None:
            raise ResourceNotFoundError("黄金集", gold_set_id)
        return gold_set

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[GoldSet]:
        return self._gold_sets.list(scope, status=status, after_id=after_id, limit=limit)

    def allowed_actions(self, scope: ProjectScope, gold_set_id: UUID) -> frozenset[str]:
        return allowed_gold_set_actions(self.get(scope, gold_set_id).status)

    def create(
        self,
        scope: ProjectScope,
        command: NewGoldSet,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> GoldSet:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self.get(scope, _as_uuid(replay.response_ref, "黄金集"))
        schema = self._schemas.get(scope, command.schema_id)
        if schema is None:
            raise ResourceNotFoundError("标注 Schema", command.schema_id)
        if schema.status is not AnnotationSchemaStatus.PUBLISHED:
            raise SchemaNotPublishedError("黄金集只能引用已发布 Schema")
        items = _build_items(command.items)
        gold_set = GoldSet(
            gold_set_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            schema_id=schema.schema_id,
            schema_version=schema.schema_version,
            schema_sha256=compute_schema_sha256(schema),
            items=items,
            exposure_policy=command.exposure_policy,
            max_exposure_per_annotator=command.max_exposure_per_annotator,
            rotation_policy=command.rotation_policy,
            usage_policy=command.usage_policy,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._gold_sets.add(scope, gold_set)
        self._record(scope, created, "create", None, created.status.value, None, request_id, now)
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.gold_set_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        gold_set_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str = "",
        request_id: str | None = None,
    ) -> GoldSet:
        key = validate_key(idempotency_key)
        action_name = validate_action_name(action, family="gold_set")
        body: dict[str, object] = {
            "action": action_name,
            "expected_version": expected_version,
            "reason": reason,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self.get(scope, _as_uuid(replay.response_ref, "黄金集"))
        current = self.get(scope, gold_set_id)
        if current.version != expected_version:
            raise VersionConflictError("黄金集")
        assert_gold_set_action(current.status, action_name)
        if action_name == GoldSetAction.ACTIVATE.value and current.created_by == scope.actor_id:
            raise GoldSetAccessDeniedError("黄金集创建人不得激活自己的黄金集")
        if action_name == GoldSetAction.ACTIVATE.value:
            schema = self._schemas.get(scope, current.schema_id)
            if schema is None:
                raise ResourceNotFoundError("标注 Schema", current.schema_id)
            current.assert_schema_compatible(schema)
        updated = replace(
            current.apply_action(action_name, reason=reason, now=now),
            updated_by=scope.actor_id,
        )
        saved = self._gold_sets.save(scope, updated, expected_version=expected_version)
        self._record(
            scope,
            saved,
            action_name,
            current.status.value,
            saved.status.value,
            reason,
            request_id,
            now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.gold_set_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def redacted_view(self, scope: ProjectScope, gold_set_id: UUID) -> Mapping[str, object]:
        """标注员可见视图：永不包含期望答案。"""
        return self.get(scope, gold_set_id).redacted_view()

    def answer_view(
        self,
        scope: ProjectScope,
        gold_set_id: UUID,
        *,
        request_id: str | None = None,
    ) -> Mapping[str, object]:
        gold_set = self.get(scope, gold_set_id)
        if not self._access.can_view_answers(scope, gold_set_id):
            raise GoldSetAccessDeniedError("无权查看黄金集期望答案")
        now = _utcnow()
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=gold_set_id,
            action="view-answers",
            from_status=gold_set.status.value,
            to_status=gold_set.status.value,
            reason=None,
            request_id=request_id,
            details={"answer_access": True},
            event_type="annotation.gold_set.answers_accessed",
            event_payload={
                "gold_set_id": str(gold_set_id),
                "actor_id": str(scope.actor_id),
            },
            occurred_at=now,
        )
        self._uow.commit()
        return gold_set.answer_view()

    def inject(
        self,
        scope: ProjectScope,
        gold_set_id: UUID,
        *,
        task_id: UUID,
        annotator_id: UUID,
        item_ids: tuple[UUID, ...],
        request_id: str | None = None,
    ) -> Mapping[str, object]:
        gold_set = self.get(scope, gold_set_id)
        if not gold_set.is_active:
            raise GoldSetNotActiveError("黄金集未激活，不能注入")
        if annotator_id == scope.actor_id:
            raise GoldSetExposureForbiddenError("标注员不得自行选择黄金项")
        if not self._members.user_is_member(scope, annotator_id):
            raise ResourceNotFoundError("项目成员", annotator_id)
        task = self._tasks.get(scope, task_id)
        if task is None:
            raise ResourceNotFoundError("标注任务", task_id)
        by_id = {item.gold_item_id: item for item in gold_set.items}
        if not item_ids:
            raise DomainValidationError("至少选择一个黄金项")
        if len(set(item_ids)) != len(item_ids):
            raise DomainValidationError("黄金项不得重复选择")
        now = _utcnow()
        exposures: list[GoldExposure] = []
        for item_id in item_ids:
            item = by_id.get(item_id)
            if item is None or not item.active:
                raise ResourceNotFoundError("黄金项", item_id)
            count = self._gold_sets.count_exposures(
                scope,
                gold_set_id=gold_set_id,
                gold_item_id=item_id,
                annotator_id=annotator_id,
            )
            if count >= gold_set.max_exposure_per_annotator:
                raise GoldSetExposureForbiddenError("黄金项曝光次数超过上限")
            if gold_set.rotation_policy is GoldRotationPolicy.PER_TASK:
                per_task = self._gold_sets.count_exposures(
                    scope,
                    gold_set_id=gold_set_id,
                    gold_item_id=item_id,
                    annotator_id=annotator_id,
                    task_id=task_id,
                )
                if per_task:
                    raise GoldSetExposureForbiddenError("该任务已向标注员轮换过该黄金项")
            exposures.append(
                GoldExposure(
                    exposure_id=uuid4(),
                    project_id=scope.project_id,
                    gold_set_id=gold_set_id,
                    gold_item_id=item_id,
                    annotator_id=annotator_id,
                    task_id=task_id,
                    rotation_marker=f"{gold_set.rotation_policy.value}:{count + 1}",
                    exposed_at=now,
                )
            )
        self._gold_sets.add_exposures(scope, tuple(exposures))
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=gold_set_id,
            action="inject",
            from_status=gold_set.status.value,
            to_status=gold_set.status.value,
            reason=None,
            request_id=request_id,
            details={
                "task_id": str(task_id),
                "annotator_id": str(annotator_id),
                "item_count": len(exposures),
                "exposure_ids": [str(item.exposure_id) for item in exposures],
            },
            event_type="annotation.gold_set.injected",
            event_payload={
                "gold_set_id": str(gold_set_id),
                "task_id": str(task_id),
                "annotator_id": str(annotator_id),
                "item_count": len(exposures),
            },
            occurred_at=now,
        )
        self._uow.commit()
        selected = tuple(by_id[item_id] for item_id in item_ids)
        return {
            "gold_set_id": str(gold_set_id),
            "items": [item.redacted_payload() for item in selected],
            "exposure_ids": [str(item.exposure_id) for item in exposures],
        }

    def evaluate(
        self,
        scope: ProjectScope,
        gold_set_id: UUID,
        *,
        task_id: UUID,
        submission_id: UUID,
        request_id: str | None = None,
    ) -> GoldEvaluationRecord:
        gold_set = self.get(scope, gold_set_id)
        submission = self._submissions.get(scope, submission_id)
        if submission is None or submission.task_id != task_id:
            raise ResourceNotFoundError("标注提交", submission_id)
        actual: dict[UUID, dict[str, object]] = {}
        for record in self._records.list_for_submission(scope, submission_id):
            values = actual.setdefault(record.episode_id, {})
            for label in record.labels:
                values[label.label_key] = label.value
        evaluation = evaluate_gold_set(gold_set, actual)
        now = _utcnow()
        stored = self._gold_sets.add_evaluation(
            scope,
            GoldEvaluationRecord(
                evaluation_id=uuid4(),
                project_id=scope.project_id,
                gold_set_id=gold_set_id,
                task_id=task_id,
                submission_id=submission_id,
                evaluation=evaluation,
                computed_at=now,
            ),
        )
        task = self._tasks.get(scope, task_id)
        if task is not None and task.gold_set_id != gold_set_id:
            self._tasks.save(
                scope,
                replace(task, gold_set_id=gold_set_id, updated_at=now, updated_by=scope.actor_id),
                expected_version=task.version,
            )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=gold_set_id,
            action="evaluate",
            from_status=gold_set.status.value,
            to_status=gold_set.status.value,
            reason=None,
            request_id=request_id,
            details={
                "task_id": str(task_id),
                "submission_id": str(submission_id),
                "evaluated_count": evaluation.evaluated_count,
                "passed_count": evaluation.passed_count,
                "accuracy": evaluation.accuracy,
            },
            event_type="annotation.gold_set.evaluated",
            event_payload={
                "gold_set_id": str(gold_set_id),
                "task_id": str(task_id),
                "submission_id": str(submission_id),
                "evaluated_count": evaluation.evaluated_count,
                "passed_count": evaluation.passed_count,
                "accuracy": evaluation.accuracy,
                "status": evaluation.status,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return stored

    def list_exposures(
        self,
        scope: ProjectScope,
        gold_set_id: UUID,
        *,
        annotator_id: UUID,
    ) -> list[GoldExposure]:
        return self._gold_sets.list_exposures(
            scope,
            gold_set_id=gold_set_id,
            annotator_id=annotator_id,
        )

    def _record(
        self,
        scope: ProjectScope,
        gold_set: GoldSet,
        action: str,
        from_status: str | None,
        to_status: str,
        reason: str | None,
        request_id: str | None,
        now: datetime,
    ) -> None:
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=gold_set.gold_set_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            request_id=request_id,
            details={"blind": gold_set.blind, "item_count": len(gold_set.items)},
            event_type="annotation.gold_set.status_changed",
            event_payload={
                "gold_set_id": str(gold_set.gold_set_id),
                "from_status": from_status,
                "to_status": to_status,
                "action": action,
                "version": gold_set.version,
            },
            occurred_at=now,
        )


def _build_items(payload: tuple[Mapping[str, object], ...]) -> tuple[GoldItem, ...]:
    if not payload:
        raise DomainValidationError("黄金集至少需要一个黄金项")
    items: list[GoldItem] = []
    for raw in payload:
        expected = raw.get("expected_labels") or ()
        if not isinstance(expected, list | tuple):
            raise DomainValidationError("expected_labels 必须是列表")
        items.append(
            GoldItem(
                gold_item_id=_as_uuid(raw.get("gold_item_id") or uuid4(), "gold_item_id"),
                episode_id=_as_uuid(raw.get("episode_id"), "episode_id"),
                expected_labels=tuple(
                    ExpectedLabel(**item) if isinstance(item, Mapping) else item
                    for item in expected
                ),
                difficulty=raw.get("difficulty", "MEDIUM"),
                weight=raw.get("weight", 1.0),
                active=raw.get("active", True),
                episode_version=raw.get("episode_version"),
                time_range=raw.get("time_range"),
            )
        )
    return tuple(items)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_uuid(value: object, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise DomainValidationError(f"{field_name} 不是合法 UUID") from exc
    raise DomainValidationError(f"{field_name} 必须是 UUID")


__all__ = ["GoldSetUseCases"]
