"""模型评测用例。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.modelops.audit import record_change
from app.application.modelops.commands import NewEvaluation
from app.application.modelops.idempotency import IdempotencyGuard, validate_key
from app.application.modelops.ports import (
    AuditRepository,
    DatasetGate,
    EvaluationRepository,
    IdempotencyRepository,
    ModelPlatformGateway,
    ModelRepository,
    ModelVersionRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.identity.models import ProjectScope
from app.domain.modelops.adapters import ensure_capability
from app.domain.modelops.comparability import ComparisonSubjects, require_comparable
from app.domain.modelops.enums import (
    EvaluationStatus,
    EvaluationVerdict,
    ModelVersionStatus,
)
from app.domain.modelops.errors import (
    DomainValidationError,
    EvaluationBaselineInvalidError,
    EvaluationDatasetNotReadyError,
    EvaluationImmutableError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.modelops.evaluation import compute_verdict, parse_metrics, parse_thresholds
from app.domain.modelops.failure_cases import parse_failure_cases
from app.domain.modelops.models import Evaluation, ModelVersion, new_evaluation
from app.domain.modelops.state_machines import validate_action_name

_RESOURCE = "EVALUATION"

# R-13：事件类型用字面量映射表，禁止 f-string——AST 提取脚本抓不到 f-string，
# 新增事件会静默逃过 ``scripts/check_outbox_events.py`` 的归类校验。
# 键集合与 ``apply_action`` 的显式白名单一致（cancel / retry）。
EVALUATION_ACTION_EVENTS: dict[str, str] = {
    "cancel": "modelops.evaluation.cancel",
    "retry": "modelops.evaluation.retry",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EvaluationUseCases:
    def __init__(
        self,
        *,
        evaluations: EvaluationRepository,
        models: ModelRepository,
        versions: ModelVersionRepository,
        datasets: DatasetGate,
        gateway: ModelPlatformGateway,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._evaluations = evaluations
        self._models = models
        self._versions = versions
        self._datasets = datasets
        self._gateway = gateway
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def get(self, scope: ProjectScope, evaluation_id: UUID) -> Evaluation:
        evaluation = self._evaluations.get(scope, evaluation_id)
        if evaluation is None:
            raise ResourceNotFoundError("评测", evaluation_id)
        return evaluation

    def list(
        self,
        scope: ProjectScope,
        *,
        model_version_id: UUID | None = None,
        dataset_version_id: UUID | None = None,
        status: str | None = None,
        verdict: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Evaluation]:
        return self._evaluations.list(
            scope,
            model_version_id=model_version_id,
            dataset_version_id=dataset_version_id,
            status=status,
            verdict=verdict,
            after_id=after_id,
            limit=limit,
        )

    def _validate_baseline(
        self,
        scope: ProjectScope,
        version: ModelVersion,
        command: NewEvaluation,
    ) -> UUID | None:
        """校验基线并返回其数据集版本（无基线时为 ``None``）。

        跨数据集版本的对比**不是**无条件放开：任务语义不同时给出的百分比
        没有意义，而系统会照常算出"提升 12%"。因此要求显式声明，并在声明后
        仍校验两侧任务大类一致（判定细则见 ``domain/modelops/comparability``）。

        基线查不到一律报"不存在或跨项目"，不区分两者——不泄漏跨项目对象的
        存在性（与 ``TrainingSourceGate`` 的口径一致）。
        """
        if command.baseline_evaluation_id is None:
            if command.cross_dataset_comparison:
                # 没有基线谈不上跨数据集对比。静默忽略会让客户端以为声明生效。
                raise DomainValidationError("cross_dataset_comparison 需要同时提供基线评测")
            return None
        baseline = self._evaluations.get(scope, command.baseline_evaluation_id)
        if baseline is None:
            raise EvaluationBaselineInvalidError("基线评测不存在或跨项目")
        cross_dataset = baseline.dataset_version_id != command.dataset_version_id
        # 任务类型只在跨数据集时才查：同数据集版本走的是改造前的路径，
        # 不该因此多出两次「模型行是否可见」的依赖与查询。
        current_task_type = self._task_type_of(scope, version) if cross_dataset else None
        baseline_task_type = (
            self._task_type_of_version(scope, baseline.model_version_id) if cross_dataset else None
        )
        require_comparable(
            ComparisonSubjects(
                baseline_dataset_version_id=baseline.dataset_version_id,
                current_dataset_version_id=command.dataset_version_id,
                baseline_task_type=baseline_task_type,
                current_task_type=current_task_type,
            ),
            declared=command.cross_dataset_comparison,
        )
        return baseline.dataset_version_id

    def _task_type_of(self, scope: ProjectScope, version: ModelVersion) -> str | None:
        """模型版本的 ``task_type``；模型行不可见时返回 ``None``（不可判定）。"""
        model = self._models.get(scope, version.model_id)
        return None if model is None else model.task_type

    def _task_type_of_version(self, scope: ProjectScope, version_id: UUID) -> str | None:
        """按版本 ID 取任务类型；版本或模型不可见时返回 ``None``（不可判定）。

        跨数据集对比里基线往往属于**另一个模型**（模型升级场景），因此必须
        按基线自己的模型版本去查，不能复用当前请求的模型。
        """
        version = self._versions.get(scope, version_id)
        if version is None:
            return None
        return self._task_type_of(scope, version)

    def create(
        self,
        scope: ProjectScope,
        command: NewEvaluation,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Evaluation:
        version = self._versions.get(scope, command.model_version_id)
        if version is None or version.project_id != scope.project_id:
            raise ResourceNotFoundError("模型版本", command.model_version_id)
        if ModelVersionStatus(version.status) not in {
            ModelVersionStatus.READY,
            ModelVersionStatus.RELEASED,
        }:
            raise DomainValidationError("模型版本必须处于 READY 或 RELEASED")
        state = self._datasets.version_state(scope.project_id, command.dataset_version_id)
        if state is None:
            raise ResourceNotFoundError("数据集版本", command.dataset_version_id)
        dataset_status, validity_status = state
        if dataset_status != "FROZEN" or validity_status != "VALID":
            raise EvaluationDatasetNotReadyError("评测数据集版本必须已冻结且有效")
        baseline_dataset_version_id = self._validate_baseline(
            scope,
            version,
            command,
        )
        thresholds = parse_thresholds(command.thresholds)
        key = validate_key(idempotency_key)
        # 幂等指纹必须覆盖所有影响结果的输入。此前漏了 baseline_evaluation_id：
        # 同一幂等键先建带基线、再建不带基线，第二次会被当成重放，静默返回
        # 第一次的评测——客户端拿到的对象与请求体不符，且没有任何报错。
        body = {
            "model_version_id": str(command.model_version_id),
            "dataset_version_id": str(command.dataset_version_id),
            "thresholds": thresholds,
            "baseline_evaluation_id": (
                None
                if command.baseline_evaluation_id is None
                else str(command.baseline_evaluation_id)
            ),
            "cross_dataset_comparison": command.cross_dataset_comparison,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        evaluation = new_evaluation(
            evaluation_id=uuid4(),
            project_id=scope.project_id,
            model_version_id=command.model_version_id,
            dataset_version_id=command.dataset_version_id,
            thresholds=thresholds,
            baseline_evaluation_id=command.baseline_evaluation_id,
            baseline_dataset_version_id=baseline_dataset_version_id,
            actor_id=scope.actor_id,
            now=now,
        )
        created = self._evaluations.add(scope, evaluation)
        if command.adapter_id:
            ensure_capability(self._gateway.capabilities(command.adapter_id), "submit_evaluation")
            self._gateway.submit_evaluation(
                adapter_id=command.adapter_id,
                adapter_version="1.0.0",
                evaluation_id=created.evaluation_id,
                model_version_id=created.model_version_id,
                dataset_version_id=created.dataset_version_id,
                config=dict(command.config or {}),
            )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=created.evaluation_id,
            action="create",
            from_status=None,
            to_status=created.status,
            reason=None,
            request_id=request_id,
            details={
                "model_version_id": str(created.model_version_id),
                "dataset_version_id": str(created.dataset_version_id),
                "baseline_evaluation_id": (
                    None
                    if created.baseline_evaluation_id is None
                    else str(created.baseline_evaluation_id)
                ),
                "baseline_dataset_version_id": (
                    None
                    if created.baseline_dataset_version_id is None
                    else str(created.baseline_dataset_version_id)
                ),
                "cross_dataset_comparison": command.cross_dataset_comparison,
            },
            event_type="modelops.evaluation.created",
            event_payload={
                "evaluation_id": str(created.evaluation_id),
                "model_version_id": str(created.model_version_id),
                "dataset_version_id": str(created.dataset_version_id),
                "baseline_dataset_version_id": (
                    None
                    if created.baseline_dataset_version_id is None
                    else str(created.baseline_dataset_version_id)
                ),
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=202,
            response_ref=str(created.evaluation_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        evaluation_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Evaluation:
        action_name = validate_action_name(action, family="evaluation")
        if action_name not in {"cancel", "retry"}:
            raise DomainValidationError("评测仅支持 cancel/retry")
        evaluation = self.get(scope, evaluation_id)
        if evaluation.row_version != expected_version:
            raise VersionConflictError("评测")
        key = validate_key(idempotency_key)
        body = {"action": action_name, "expected_version": expected_version}
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        if action_name == "cancel":
            if action_name not in evaluation.allowed_actions:
                raise InvalidStateTransitionError("评测", evaluation.status, action_name)
            updated = replace(
                evaluation,
                status=EvaluationStatus.CANCELLED.value,
                error_code="cancelled_by_user",
                error_summary=reason,
                finished_at=now,
                updated_at=now,
            )
            saved = self._evaluations.save(scope, updated, expected_version=expected_version)
        else:
            if EvaluationStatus(evaluation.status) is not EvaluationStatus.FAILED:
                raise InvalidStateTransitionError("评测", evaluation.status, action_name)
            retried = new_evaluation(
                evaluation_id=uuid4(),
                project_id=evaluation.project_id,
                model_version_id=evaluation.model_version_id,
                dataset_version_id=evaluation.dataset_version_id,
                thresholds=evaluation.thresholds,
                baseline_evaluation_id=evaluation.baseline_evaluation_id,
                baseline_dataset_version_id=evaluation.baseline_dataset_version_id,
                actor_id=scope.actor_id,
                now=now,
            )
            saved = self._evaluations.add(scope, retried)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=saved.evaluation_id,
            action=action_name,
            from_status=evaluation.status,
            to_status=saved.status,
            reason=reason,
            request_id=request_id,
            details={"source_evaluation_id": str(evaluation_id)},
            event_type=EVALUATION_ACTION_EVENTS[action_name],
            event_payload={"evaluation_id": str(saved.evaluation_id)},
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=202 if action_name == "retry" else 200,
            response_ref=str(saved.evaluation_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def complete_from_callback(
        self,
        *,
        project_id: UUID,
        evaluation_id: UUID,
        metrics: dict[str, float],
        report_ref: dict[str, object] | None,
        failure_cases: list[dict[str, object]] | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
        now: datetime | None = None,
    ) -> Evaluation:
        now = now or _utcnow()
        lookup = ProjectScope(project_id=project_id, actor_id=project_id)
        evaluation = self._evaluations.get(lookup, evaluation_id)
        if evaluation is None:
            raise ResourceNotFoundError("评测", evaluation_id)
        scope = ProjectScope(project_id=project_id, actor_id=evaluation.created_by or project_id)
        if evaluation.status == EvaluationStatus.SUCCEEDED.value:
            return evaluation
        if evaluation.status in {
            EvaluationStatus.FAILED.value,
            EvaluationStatus.CANCELLED.value,
        }:
            raise EvaluationImmutableError("已终态评测不可改写")
        if error_code:
            updated = replace(
                evaluation,
                status=EvaluationStatus.FAILED.value,
                failure_cases=parse_failure_cases(failure_cases),
                error_code=error_code,
                error_summary=error_summary,
                started_at=evaluation.started_at or now,
                finished_at=now,
                updated_at=now,
            )
        else:
            parsed = parse_metrics(metrics)
            verdict = compute_verdict(parsed, evaluation.thresholds)
            updated = replace(
                evaluation,
                status=EvaluationStatus.SUCCEEDED.value,
                metrics=parsed,
                verdict=verdict,
                report_ref=report_ref,
                failure_cases=parse_failure_cases(failure_cases),
                started_at=evaluation.started_at or now,
                finished_at=now,
                updated_at=now,
            )
        saved = self._evaluations.save(scope, updated, expected_version=evaluation.row_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE,
            resource_id=saved.evaluation_id,
            action="callback",
            from_status=evaluation.status,
            to_status=saved.status,
            reason=None,
            request_id=None,
            details={"verdict": saved.verdict},
            event_type="modelops.evaluation.callback",
            event_payload={
                "evaluation_id": str(saved.evaluation_id),
                "verdict": saved.verdict,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return saved

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> Evaluation:
        if not response_ref:
            raise ResourceNotFoundError("评测", response_ref)
        try:
            return self.get(scope, UUID(response_ref))
        except ValueError as exc:
            raise ResourceNotFoundError("评测", response_ref) from exc


__all__ = ["EvaluationUseCases", "EvaluationVerdict"]
