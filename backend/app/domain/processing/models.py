"""ProcessingRun 聚合根。

一个 ProcessingRun 表示"一次带版本的处理流水线执行"，承载：

- 输入引用与产物引用（只存对象存储引用/标识，不存大对象本体）；
- 七态状态机与当前阶段；
- 尝试次数、重试次数、错误码/摘要与起止时间；
- 乐观锁版本 ``row_version`` 与审计字段。

实体不可变：所有状态变化都经 :meth:`ProcessingRun.transition` 生成新实例。
领域层不依赖 Web/ORM/队列，也不实现阶段业务逻辑（N4-002）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.domain.processing.enums import (
    STAGE_SEQUENCE,
    ProcessingRunAction,
    ProcessingRunStatus,
)
from app.domain.processing.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
)
from app.domain.processing.state_machines import (
    allowed_actions,
    coerce_stage,
    first_stage,
    stage_index,
    target_status,
    validate_action_name,
)
from app.domain.processing.validation import (
    non_negative_int,
    optional_aware,
    optional_text,
    positive_int,
    required_text,
    required_uuid,
)

MAX_REFS = 1000
REF_MAX_LENGTH = 1024
PIPELINE_VERSION_MAX_LENGTH = 64
ERROR_CODE_MAX_LENGTH = 64
ERROR_SUMMARY_MAX_LENGTH = 2000
#: 收口原因与 ``audit_logs.reason`` 同宽；不新增列，原因只落在审计记录里。
STATUS_REASON_MAX_LENGTH = 1000

_STAGE_ACTIONS = frozenset(
    {
        ProcessingRunAction.WORKER_CLAIM.value,
        ProcessingRunAction.WORKER_ADVANCE.value,
        ProcessingRunAction.WORKER_SUCCEED.value,
        ProcessingRunAction.WORKER_FAIL.value,
    }
)
_OUTPUT_ACTIONS = frozenset(
    {
        ProcessingRunAction.WORKER_ADVANCE.value,
        ProcessingRunAction.WORKER_SUCCEED.value,
        ProcessingRunAction.WORKER_FAIL.value,
    }
)
_ACTIVE_STATUSES = frozenset(
    {
        ProcessingRunStatus.RUNNING.value,
        ProcessingRunStatus.SUCCEEDED.value,
        ProcessingRunStatus.FAILED.value,
    }
)
_FINISHED_STATUSES = frozenset(
    {
        ProcessingRunStatus.SUCCEEDED.value,
        ProcessingRunStatus.FAILED.value,
        ProcessingRunStatus.CANCELLED.value,
    }
)


def _coerce_status(value: object) -> str:
    try:
        return ProcessingRunStatus(value).value  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in ProcessingRunStatus)
        raise DomainValidationError(f"status 取值非法，允许值：{allowed}") from exc


def _refs(value: object, field_name: str, *, allow_empty: bool) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise DomainValidationError(f"{field_name} 必须是字符串列表")
    if not value and not allow_empty:
        raise DomainValidationError(f"{field_name} 不能为空")
    if len(value) > MAX_REFS:
        raise DomainValidationError(f"{field_name} 最多 {MAX_REFS} 项")
    result: list[str] = []
    for item in value:
        cleaned = required_text(item, field_name, max_length=REF_MAX_LENGTH)
        if cleaned not in result:
            result.append(cleaned)
    return tuple(result)


def _non_decreasing_stage(current: str | None, candidate: str) -> str:
    if current is None:
        return candidate
    if stage_index(candidate) < stage_index(current):
        raise DomainValidationError("阶段不得回退")
    return candidate


def _strictly_advancing_stage(current: str | None, candidate: str) -> str:
    if current is None:
        return candidate
    if stage_index(candidate) <= stage_index(current):
        raise DomainValidationError("advance 必须把阶段推进到下一阶段")
    return candidate


@dataclass(frozen=True, slots=True)
class ProcessingRun:
    run_id: UUID
    project_id: UUID
    pipeline_version: str
    input_refs: tuple[str, ...]
    output_refs: tuple[str, ...] = ()
    status: str = ProcessingRunStatus.CREATED.value
    stage: str | None = None
    attempt: int = 0
    retry_count: int = 0
    error_code: str | None = None
    error_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    #: 失败收口时间：``FAILED`` 上由服务端 ``system-close`` 写入；为 NULL 时
    #: 仍可按 Worker 的重试策略拉起。它不替代 ``error_*``，收口后错误原因保留。
    closed_at: datetime | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", required_uuid(self.run_id, "run_id"))
        object.__setattr__(self, "project_id", required_uuid(self.project_id, "project_id"))
        object.__setattr__(
            self,
            "pipeline_version",
            required_text(
                self.pipeline_version,
                "pipeline_version",
                max_length=PIPELINE_VERSION_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "input_refs",
            _refs(self.input_refs, "input_refs", allow_empty=False),
        )
        object.__setattr__(
            self,
            "output_refs",
            _refs(self.output_refs, "output_refs", allow_empty=True),
        )
        object.__setattr__(self, "status", _coerce_status(self.status))
        object.__setattr__(
            self,
            "stage",
            None if self.stage is None else coerce_stage(self.stage),
        )
        object.__setattr__(self, "attempt", non_negative_int(self.attempt, "attempt"))
        object.__setattr__(
            self,
            "retry_count",
            non_negative_int(self.retry_count, "retry_count"),
        )
        object.__setattr__(
            self,
            "error_code",
            optional_text(self.error_code, "error_code", max_length=ERROR_CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "error_summary",
            optional_text(
                self.error_summary,
                "error_summary",
                max_length=ERROR_SUMMARY_MAX_LENGTH,
            ),
        )
        object.__setattr__(self, "started_at", optional_aware(self.started_at, "started_at"))
        object.__setattr__(self, "finished_at", optional_aware(self.finished_at, "finished_at"))
        object.__setattr__(self, "closed_at", optional_aware(self.closed_at, "closed_at"))
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))
        object.__setattr__(self, "created_at", optional_aware(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", optional_aware(self.updated_at, "updated_at"))
        if self.closed_at is not None:
            if self.status != ProcessingRunStatus.FAILED.value:
                raise DomainValidationError("只有 FAILED 状态可以记录 closed_at")
            if self.finished_at is not None and self.closed_at < self.finished_at:
                raise DomainValidationError("closed_at 不得早于 finished_at")
        self._validate_invariants()

    def _validate_invariants(self) -> None:
        status = self.status
        if self.retry_count > self.attempt:
            raise DomainValidationError("retry_count 不得超过 attempt")
        if status == ProcessingRunStatus.FAILED.value:
            if self.error_code is None or self.error_summary is None:
                raise DomainValidationError("FAILED 必须同时记录 error_code 与 error_summary")
        elif self.error_code is not None or self.error_summary is not None:
            raise DomainValidationError("只有 FAILED 状态可以携带错误信息")
        if status in _ACTIVE_STATUSES:
            if self.stage is None:
                raise DomainValidationError(f"{status} 必须记录当前阶段")
            if self.started_at is None:
                raise DomainValidationError(f"{status} 必须有 started_at")
            if self.attempt < 1:
                raise DomainValidationError(f"{status} 的 attempt 必须至少为 1")
        if status in _FINISHED_STATUSES and self.finished_at is None:
            raise DomainValidationError(f"{status} 必须有 finished_at")
        if status not in _FINISHED_STATUSES and self.finished_at is not None:
            raise DomainValidationError(f"{status} 不得携带 finished_at")

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_actions(self.status)

    @property
    def is_terminal(self) -> bool:
        """终态：成功、取消，或保留错误的失败收口。

        ``FAILED`` 本身不是终态（Worker 仍可重试）；只有 ``closed_at`` 非空后
        才不可再操作。判定只看本实体的持久字段，不读取审计表。
        """
        if self.status in {
            ProcessingRunStatus.SUCCEEDED.value,
            ProcessingRunStatus.CANCELLED.value,
        }:
            return True
        return self.status == ProcessingRunStatus.FAILED.value and self.closed_at is not None

    @property
    def closed(self) -> bool:
        """是否已失败收口；``closed_at`` 的布尔投影，供审计/响应使用。"""
        return self.closed_at is not None

    def transition(
        self,
        action: object,
        *,
        now: datetime,
        actor_id: UUID,
        stage: object | None = None,
        output_refs: list[str] | tuple[str, ...] = (),
        error_code: str | None = None,
        error_summary: str | None = None,
        reason: str | None = None,
    ) -> ProcessingRun:
        """按显式迁移表生成新状态；非法动作/字段组合一律拒绝。"""
        action_name = validate_action_name(action)
        moment = optional_aware(now, "now")
        if moment is None:
            raise DomainValidationError("now 不能为空")
        actor = required_uuid(actor_id, "actor_id")
        if self.closed_at is not None:
            # 收口是吸收态：连 Worker 重试也不允许，避免把已放弃的失败重新拉起。
            raise InvalidStateTransitionError(self.status, action_name, detail="运行已系统收口")
        target = target_status(self.status, action_name)

        if action_name == ProcessingRunAction.WORKER_ADVANCE.value and stage is None:
            raise DomainValidationError("worker-advance 必须指定要推进到的阶段")
        if stage is not None and action_name not in _STAGE_ACTIONS:
            raise DomainValidationError(f"动作 {action_name} 不接受 stage")
        if output_refs and action_name not in _OUTPUT_ACTIONS:
            raise DomainValidationError(f"动作 {action_name} 不接受 output_refs")
        if (error_code is not None or error_summary is not None) and (
            action_name != ProcessingRunAction.WORKER_FAIL.value
        ):
            raise DomainValidationError(f"动作 {action_name} 不接受错误信息")
        if action_name == ProcessingRunAction.WORKER_FAIL.value and (
            error_code is None or error_summary is None
        ):
            raise DomainValidationError("worker-fail 必须同时提供 error_code 与 error_summary")
        reason_text = optional_text(
            reason,
            "reason",
            max_length=STATUS_REASON_MAX_LENGTH,
        )
        if action_name == ProcessingRunAction.SYSTEM_CLOSE.value:
            if reason_text is None:
                raise DomainValidationError("system-close 必须提供收口原因")
        elif reason_text is not None:
            raise DomainValidationError(f"动作 {action_name} 不接受 reason")

        new_stage = self.stage
        if stage is not None:
            candidate = coerce_stage(stage)
            if action_name == ProcessingRunAction.WORKER_ADVANCE.value:
                new_stage = _strictly_advancing_stage(self.stage, candidate)
            else:
                new_stage = _non_decreasing_stage(self.stage, candidate)

        new_outputs = self.output_refs
        if output_refs:
            merged = list(self.output_refs)
            for ref in _refs(output_refs, "output_refs", allow_empty=False):
                if ref not in merged:
                    merged.append(ref)
            new_outputs = tuple(merged)

        updates: dict[str, object] = {
            "status": target,
            "stage": new_stage,
            "output_refs": new_outputs,
            "updated_at": moment,
            "updated_by": actor,
        }

        if action_name == ProcessingRunAction.SYSTEM_CLOSE.value:
            # 自环：状态、阶段、错误与结束时间全部保持原样，只记录收口时间。
            updates["closed_at"] = moment
        elif action_name == ProcessingRunAction.WORKER_CLAIM.value:
            updates.update(
                {
                    "attempt": self.attempt + 1,
                    "started_at": self.started_at or moment,
                    "finished_at": None,
                    "error_code": None,
                    "error_summary": None,
                }
            )
            if new_stage is None:
                updates["stage"] = first_stage()
        elif action_name == ProcessingRunAction.WORKER_RETRY.value:
            updates.update(
                {
                    "retry_count": self.retry_count + 1,
                    "error_code": None,
                    "error_summary": None,
                    "finished_at": None,
                }
            )
        elif target == ProcessingRunStatus.SUCCEEDED.value:
            updates.update({"finished_at": moment, "error_code": None, "error_summary": None})
        elif target == ProcessingRunStatus.FAILED.value:
            updates.update(
                {
                    "finished_at": moment,
                    "error_code": required_text(
                        error_code,
                        "error_code",
                        max_length=ERROR_CODE_MAX_LENGTH,
                    ),
                    "error_summary": required_text(
                        error_summary,
                        "error_summary",
                        max_length=ERROR_SUMMARY_MAX_LENGTH,
                    ),
                }
            )
        elif target == ProcessingRunStatus.CANCELLED.value:
            updates.update({"finished_at": moment, "error_code": None, "error_summary": None})
        elif target == ProcessingRunStatus.QUEUED.value:
            updates.update({"finished_at": None})

        return replace(self, **updates)


__all__ = [
    "ERROR_CODE_MAX_LENGTH",
    "ERROR_SUMMARY_MAX_LENGTH",
    "MAX_REFS",
    "PIPELINE_VERSION_MAX_LENGTH",
    "REF_MAX_LENGTH",
    "STATUS_REASON_MAX_LENGTH",
    "ProcessingRun",
    "STAGE_SEQUENCE",
]
