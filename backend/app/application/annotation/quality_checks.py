"""质量检查用例：对一份标注提交执行可版本化的质量规则。

职责边界：
- 规则**判定**在领域层（``domain.annotation.quality_rules``），本模块不写规则；
- 本模块只做编排：取上下文（Schema 定义、Episode 边界、任务策略）→ 跑规则 →
  构造带不变式校验的执行记录 → 落库 + 审计 + outbox → 提交事务。

为什么同步执行而不进 Worker：一次检查是 O(记录数 × 规则数) 的纯内存计算，
输入是已经在事务里读到的记录，没有大文件 IO。塞进队列只会换来"结果不确定何时
可见"和一套额外的幂等/重试语义。若将来单提交规模大到毫秒级不可接受，应改为
分片任务并复用同一规则引擎——引擎本身已经无 IO、可并行。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.ports import (
    AnnotationRecordRepository,
    AnnotationSchemaRepository,
    AnnotationSubmissionRepository,
    AnnotationTaskRepository,
    AuditRepository,
    EpisodeDirectory,
    OutboxRepository,
    QualityCheckRepository,
    UnitOfWork,
)
from app.domain.annotation.errors import (
    ResourceNotFoundError,
    SubmissionNotSubmittedError,
    SubmissionSchemaMismatchError,
)
from app.domain.annotation.quality_check import (
    QualityCheckRun,
    QualityEvaluation,
    QualityFinding,
    summarize_findings,
)
from app.domain.annotation.quality_rules import (
    QualityRuleRegistry,
    RuleContext,
    default_registry,
    resolve_status,
)
from app.domain.annotation.schema import AnnotationSchema, compute_schema_sha256
from app.domain.annotation.submission import AnnotationRecord, AnnotationSubmission
from app.domain.identity.models import ProjectScope

_RESOURCE_TYPE = "ANNOTATION_QUALITY_RUN"
_EVENT_TYPE = "annotation.quality_run.completed"


class QualityCheckUseCases:
    def __init__(
        self,
        checks: QualityCheckRepository,
        submissions: AnnotationSubmissionRepository,
        records: AnnotationRecordRepository,
        tasks: AnnotationTaskRepository,
        schemas: AnnotationSchemaRepository,
        episodes: EpisodeDirectory,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
        registry: QualityRuleRegistry | None = None,
    ) -> None:
        self._checks = checks
        self._submissions = submissions
        self._records = records
        self._tasks = tasks
        self._schemas = schemas
        self._episodes = episodes
        self._audit = audit
        self._outbox = outbox
        self._uow = uow
        self._registry = registry or default_registry()

    @property
    def rule_set_version(self) -> str:
        return self._registry.version

    def get(self, scope: ProjectScope, run_id: UUID) -> QualityCheckRun:
        run = self._checks.get(scope, run_id)
        if run is None:
            raise ResourceNotFoundError("质量检查记录", run_id)
        return run

    def list_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
        *,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[QualityCheckRun]:
        # 先确认提交可见，否则跨项目/不存在的提交会得到空列表而非 404，
        # 客户端无法区分"没检查过"和"这个提交不是你的"。
        self._require_submission(scope, submission_id)
        return self._checks.list_for_submission(
            scope,
            submission_id=submission_id,
            after_id=after_id,
            limit=limit,
        )

    def run(
        self,
        scope: ProjectScope,
        submission_id: UUID,
        *,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> QualityCheckRun:
        started_at = now or datetime.now(UTC)
        submission = self._require_submission(scope, submission_id)
        if submission.is_editable:
            # 草稿还在改，任何结论都会立刻过期。判定直接复用领域实体的
            # ``is_editable``，不在应用层重新列举"哪些状态算草稿"。
            raise SubmissionNotSubmittedError("草稿提交不参与质量检查，请先提交")
        records = tuple(self._records.list_for_submission(scope, submission_id))
        schema = self._resolve_schema(scope, submission)
        context = self._build_context(scope, submission, records, schema)

        findings = _evaluate(self._registry, records, context)
        error_count, warn_count = summarize_findings(findings)
        finished_at = datetime.now(UTC)
        if finished_at < started_at:
            # 显式注入的 now 可能晚于真实时钟；用真实值兜底会让 finished_at
            # 早于 started_at，落库 CHECK 直接失败且原因难查。
            finished_at = started_at
        evaluation = QualityEvaluation(
            findings=findings,
            error_count=error_count,
            warn_count=warn_count,
            status=resolve_status(error_count),
        )
        run = QualityCheckRun(
            run_id=uuid4(),
            project_id=scope.project_id,
            submission_id=submission_id,
            rule_set_version=self._registry.version,
            status=evaluation.status,
            error_count=evaluation.error_count,
            warn_count=evaluation.warn_count,
            started_at=started_at,
            finished_at=finished_at,
            findings=evaluation.findings,
            created_at=started_at,
            created_by=scope.actor_id,
        )
        created = self._checks.add(scope, run)
        self._record(scope, created, request_id=request_id, occurred_at=finished_at)
        self._uow.commit()
        return created

    def _require_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> AnnotationSubmission:
        submission = self._submissions.get(scope, submission_id)
        if submission is None:
            raise ResourceNotFoundError("标注提交", submission_id)
        return submission

    def _resolve_schema(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
    ) -> AnnotationSchema:
        schema = self._schemas.get(scope, submission.schema_id)
        if schema is None:
            raise ResourceNotFoundError("标注 Schema", submission.schema_id)
        if (
            schema.schema_version != submission.schema_version
            or compute_schema_sha256(schema) != submission.schema_sha256
        ):
            # 提交绑定的快照与库里当前 Schema 不一致：不能拿"当前定义"去判
            # "当时的提交"，否则规则结论会对不上提交本身。
            raise SubmissionSchemaMismatchError("提交 Schema 快照与 Schema 当前快照不一致")
        return schema

    def _build_context(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
        records: tuple[AnnotationRecord, ...],
        schema: AnnotationSchema,
    ) -> RuleContext:
        episode_ids = tuple(sorted({record.episode_id for record in records}))
        bounds = self._episodes.episode_bounds(scope, episode_ids=episode_ids)
        policy = self._task_policy(scope, submission.task_id)
        return RuleContext(
            definitions=tuple(schema.labels),
            episode_bounds=dict(bounds),
            policy=policy,
        )

    def _task_policy(self, scope: ProjectScope, task_id: UUID) -> Mapping[str, object]:
        task = self._tasks.get(scope, task_id)
        if task is None:
            raise ResourceNotFoundError("标注任务", task_id)
        return dict(task.quality_policy)

    def _record(
        self,
        scope: ProjectScope,
        run: QualityCheckRun,
        *,
        request_id: str | None,
        occurred_at: datetime,
    ) -> None:
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=run.run_id,
            action="run",
            from_status=None,
            to_status=run.status.value,
            reason=None,
            request_id=request_id,
            details={
                "submission_id": str(run.submission_id),
                "rule_set_version": run.rule_set_version,
                "error_count": run.error_count,
                "warn_count": run.warn_count,
                "finding_count": len(run.findings),
            },
            event_type=_EVENT_TYPE,
            event_payload={
                "run_id": str(run.run_id),
                "submission_id": str(run.submission_id),
                "rule_set_version": run.rule_set_version,
                "status": run.status.value,
                "error_count": run.error_count,
                "warn_count": run.warn_count,
            },
            occurred_at=occurred_at,
        )


def _evaluate(
    registry: QualityRuleRegistry,
    records: tuple[AnnotationRecord, ...],
    context: RuleContext,
) -> tuple[QualityFinding, ...]:
    findings: list[QualityFinding] = []
    for record in records:
        for rule in registry.list_rules():
            outcome = rule.evaluate(record, context)
            findings.append(
                QualityFinding(
                    annotation_id=record.annotation_id,
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    severity=rule.severity,
                    # 不适用 → NULL，而不是 True：把"没判"记成"合规"等于伪造证据。
                    passed=None if not outcome.applicable else outcome.passed,
                    message=outcome.message,
                )
            )
    return tuple(findings)


__all__ = ["QualityCheckUseCases"]
