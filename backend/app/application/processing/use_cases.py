"""ProcessingRun 用例编排。

边界：

- **客户端用例**（create / list / get / cancel）只暴露 ``allowed_actions``
  中的动作；``system-*`` / ``worker-*`` 永远不会下发到 HTTP 层。
- **Worker 用例**（enqueue / claim / advance / succeed / fail / retry）是
  服务端内部入口，只负责状态与计数的推进，**不实现八个阶段的具体业务**
  （N4-002）。任何耗时解析都必须在 Worker 进程里执行，不得占用 HTTP 线程。
- 所有读写都经 ``ProjectScope``；客户端传入的 ``project_id`` 不参与授权。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.application.audit import (
    AuditRepository,
    OutboxRepository,
    record_change,
)
from app.application.processing.commands import NewProcessingRun
from app.application.processing.ports import (
    ProcessingRunRepository,
    ProcessingStepRepository,
    UnitOfWork,
)
from app.domain.common.ids import new_id
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import (
    PUBLIC_PROCESSING_ACTIONS,
    ProcessingRunAction,
    ProcessingRunStatus,
)
from app.domain.processing.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.processing.models import ProcessingRun
from app.domain.processing.state_machines import validate_action_name
from app.domain.processing.steps import ProcessingStep

_NOT_FOUND_MESSAGE = "处理运行不存在或不属于当前项目"

#: 投递类事件：创建运行后由 ``app.workers.processing`` 消费并执行八阶段。
#: 常量写在这里而不是 Worker 里，是因为**生产点**才是语义来源；消费者
#: import 同一常量，避免两边各写一份字符串后悄然失配。
PROCESSING_RUN_REQUESTED_EVENT = "processing.run.requested"

#: 详情响应内联步骤的上限。八阶段 × 有限重试，正常远小于该值；
#: 超限时详情会截断，完整历史走 ``GET .../steps`` 分页。
DETAIL_STEP_LIMIT = 200


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ProcessingRunUseCases:
    """客户端可见用例。"""

    def __init__(
        self,
        runs: ProcessingRunRepository,
        steps: ProcessingStepRepository,
        uow: UnitOfWork,
        *,
        audit: AuditRepository,
        outbox: OutboxRepository,
    ) -> None:
        self._runs = runs
        self._steps = steps
        self._uow = uow
        self._audit = audit
        self._outbox = outbox

    def create(
        self,
        scope: ProjectScope,
        command: NewProcessingRun,
        *,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> ProcessingRun:
        moment = now or _utcnow()
        run = ProcessingRun(
            run_id=new_id(),
            project_id=scope.project_id,
            pipeline_version=command.pipeline_version,
            input_refs=command.input_refs,
            status=ProcessingRunStatus.CREATED.value,
            created_at=moment,
            updated_at=moment,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._runs.add(scope, run)
        # 投递类事件：HTTP 线程只落一行运行 + 一条 outbox，真正的八阶段
        # 处理由 Worker 异步完成。审计与事件同事务写入，任一失败整体回滚。
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="PROCESSING_RUN",
            resource_id=created.run_id,
            action="create",
            from_status=None,
            to_status=created.status,
            reason=None,
            request_id=request_id,
            details={
                "pipeline_version": created.pipeline_version,
                "input_ref_count": len(created.input_refs),
            },
            event_type=PROCESSING_RUN_REQUESTED_EVENT,
            event_payload={
                "run_id": str(created.run_id),
                "pipeline_version": created.pipeline_version,
                # Worker 据此沿用发起请求的 trace_id；附加字段。
                **({"request_id": request_id} if request_id is not None else {}),
            },
            occurred_at=moment,
            dispatch=True,
        )
        self._uow.commit()
        return created

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        stage: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ProcessingRun]:
        return self._runs.list(
            scope,
            status=status,
            stage=stage,
            after_id=after_id,
            limit=limit,
        )

    def get(self, scope: ProjectScope, run_id: UUID) -> ProcessingRun:
        run = self._runs.get(scope, run_id)
        if run is None:
            raise ResourceNotFoundError(_NOT_FOUND_MESSAGE)
        return run

    def list_steps(
        self,
        scope: ProjectScope,
        run_id: UUID,
        *,
        after_id: UUID | None = None,
        limit: int = 100,
    ) -> list[ProcessingStep]:
        """按项目作用域列出一个运行的步骤记录；运行不存在即 404。"""
        self.get(scope, run_id)
        return self._steps.list(
            scope,
            run_id=run_id,
            after_id=after_id,
            limit=limit,
        )

    def detail_steps(self, scope: ProjectScope, run_id: UUID) -> list[ProcessingStep]:
        """详情响应内联步骤（有界）；完整历史走 :meth:`list_steps` 分页。"""
        return self.list_steps(scope, run_id, limit=DETAIL_STEP_LIMIT)

    def act(
        self,
        scope: ProjectScope,
        run_id: UUID,
        action: str,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> ProcessingRun:
        """客户端动作入口：只接受 ``PUBLIC_PROCESSING_ACTIONS``。"""
        action_name = validate_action_name(action)
        if action_name not in PUBLIC_PROCESSING_ACTIONS:
            raise DomainValidationError(f"客户端不支持动作 {action_name}")
        return self._apply_client(
            scope,
            run_id,
            action_name,
            expected_version=expected_version,
            now=now or _utcnow(),
        )

    def cancel(
        self,
        scope: ProjectScope,
        run_id: UUID,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> ProcessingRun:
        return self._apply_client(
            scope,
            run_id,
            ProcessingRunAction.CANCEL.value,
            expected_version=expected_version,
            now=now or _utcnow(),
        )

    def _apply_client(
        self,
        scope: ProjectScope,
        run_id: UUID,
        action: str,
        *,
        expected_version: int,
        now: datetime,
    ) -> ProcessingRun:
        run = self.get(scope, run_id)
        if run.row_version != expected_version:
            raise VersionConflictError()
        if action not in run.allowed_actions:
            raise InvalidStateTransitionError(run.status, action)
        updated = run.transition(action, now=now, actor_id=scope.actor_id)
        saved = self._runs.save(scope, updated, expected_version=expected_version)
        self._uow.commit()
        return saved


class ProcessingWorkerUseCases:
    """服务端内部入口；不实现阶段业务，只推进状态、阶段与计数。"""

    def __init__(self, runs: ProcessingRunRepository, uow: UnitOfWork) -> None:
        self._runs = runs
        self._uow = uow

    def enqueue(
        self,
        scope: ProjectScope,
        run_id: UUID,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> ProcessingRun:
        return self._apply_internal(
            scope,
            run_id,
            ProcessingRunAction.SYSTEM_QUEUE.value,
            expected_version=expected_version,
            now=now or _utcnow(),
        )

    def claim(
        self,
        scope: ProjectScope,
        run_id: UUID,
        *,
        expected_version: int,
        stage: str | None = None,
        now: datetime | None = None,
    ) -> ProcessingRun:
        return self._apply_internal(
            scope,
            run_id,
            ProcessingRunAction.WORKER_CLAIM.value,
            expected_version=expected_version,
            now=now or _utcnow(),
            stage=stage,
        )

    def advance(
        self,
        scope: ProjectScope,
        run_id: UUID,
        stage: str,
        *,
        expected_version: int,
        output_refs: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> ProcessingRun:
        return self._apply_internal(
            scope,
            run_id,
            ProcessingRunAction.WORKER_ADVANCE.value,
            expected_version=expected_version,
            now=now or _utcnow(),
            stage=stage,
            output_refs=output_refs,
        )

    def succeed(
        self,
        scope: ProjectScope,
        run_id: UUID,
        *,
        expected_version: int,
        output_refs: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> ProcessingRun:
        return self._apply_internal(
            scope,
            run_id,
            ProcessingRunAction.WORKER_SUCCEED.value,
            expected_version=expected_version,
            now=now or _utcnow(),
            output_refs=output_refs,
        )

    def fail(
        self,
        scope: ProjectScope,
        run_id: UUID,
        *,
        expected_version: int,
        error_code: str,
        error_summary: str,
        now: datetime | None = None,
    ) -> ProcessingRun:
        return self._apply_internal(
            scope,
            run_id,
            ProcessingRunAction.WORKER_FAIL.value,
            expected_version=expected_version,
            now=now or _utcnow(),
            error_code=error_code,
            error_summary=error_summary,
        )

    def retry(
        self,
        scope: ProjectScope,
        run_id: UUID,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> ProcessingRun:
        return self._apply_internal(
            scope,
            run_id,
            ProcessingRunAction.WORKER_RETRY.value,
            expected_version=expected_version,
            now=now or _utcnow(),
        )

    def _apply_internal(
        self,
        scope: ProjectScope,
        run_id: UUID,
        action: str,
        *,
        expected_version: int,
        now: datetime,
        stage: str | None = None,
        output_refs: tuple[str, ...] = (),
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> ProcessingRun:
        run = self._runs.get(scope, run_id)
        if run is None:
            raise ResourceNotFoundError(_NOT_FOUND_MESSAGE)
        if run.row_version != expected_version:
            raise VersionConflictError()
        updated = run.transition(
            action,
            now=now,
            actor_id=scope.actor_id,
            stage=stage,
            output_refs=output_refs,
            error_code=error_code,
            error_summary=error_summary,
        )
        saved = self._runs.save(scope, updated, expected_version=expected_version)
        self._uow.commit()
        return saved


__all__ = [
    "DETAIL_STEP_LIMIT",
    "PROCESSING_RUN_REQUESTED_EVENT",
    "ProcessingRunUseCases",
    "ProcessingWorkerUseCases",
]
