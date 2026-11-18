"""外部训练引用领域实体。

DDP 只保存外部训练平台的 run、模型与评测引用及状态摘要；外部平台是训练执行、
模型权重与训练指标的权威系统。未知/不可达不得显示为成功。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.domain.datasets.errors import DomainValidationError, InvalidStateTransitionError
from app.domain.datasets.training_enums import (
    DECLARED_EXECUTION_MODES,
    ExecutionMode,
    ExternalTrainingStatus,
)
from app.domain.datasets.training_state_machines import external_target
from app.domain.datasets.validation import (
    aware_datetime,
    coerce_enum,
    optional_aware_datetime,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    text_value,
)

_ADAPTER_MAX = 100
_STATUS_ACTIONS = {
    ExternalTrainingStatus.RUNNING: "sync",
    ExternalTrainingStatus.SUCCEEDED: "system-succeed",
    ExternalTrainingStatus.FAILED: "system-fail",
    ExternalTrainingStatus.CANCELLED: "system-cancel",
    ExternalTrainingStatus.UNKNOWN: "system-unknown",
    ExternalTrainingStatus.UNAVAILABLE: "system-unavailable",
}


def _model_ref(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DomainValidationError("model_ref 必须是对象或 null")
    unknown = set(value) - {"model_id", "model_version", "registered_at"}
    if unknown:
        raise DomainValidationError("model_ref 包含未知字段")
    result: dict[str, object] = {
        "model_id": text_value(value.get("model_id"), "model_id", max_length=200)
    }
    if value.get("model_version") is not None:
        result["model_version"] = text_value(
            value.get("model_version"), "model_version", max_length=100
        )
    if value.get("registered_at") is not None:
        registered = value.get("registered_at")
        if isinstance(registered, datetime):
            result["registered_at"] = aware_datetime(
                registered, "model_ref.registered_at"
            ).isoformat()
        else:
            text_value(registered, "model_ref.registered_at", max_length=64)
            result["registered_at"] = str(registered)
    return result


def _evaluation_ref(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DomainValidationError("evaluation_ref 必须是对象或 null")
    unknown = set(value) - {"evaluation_id", "evaluation_version", "registered_at"}
    if unknown:
        raise DomainValidationError("evaluation_ref 包含未知字段")
    result: dict[str, object] = {
        "evaluation_id": text_value(value.get("evaluation_id"), "evaluation_id", max_length=200)
    }
    if value.get("evaluation_version") is not None:
        result["evaluation_version"] = text_value(
            value.get("evaluation_version"), "evaluation_version", max_length=100
        )
    if value.get("registered_at") is not None:
        registered = value.get("registered_at")
        if isinstance(registered, datetime):
            result["registered_at"] = aware_datetime(
                registered, "evaluation_ref.registered_at"
            ).isoformat()
        else:
            text_value(registered, "evaluation_ref.registered_at", max_length=64)
            result["registered_at"] = str(registered)
    return result


# 回调带回的产物清单是元数据（对象键、sha256、字节数、类别统计），不是产物本身。
# 上限设在这里而不是靠数据库列类型：超限说明对方在往引用里塞大对象，必须
# 明确拒绝，不能悄悄落库。
_ARTIFACTS_MAX_BYTES = 32 * 1024


def _artifacts(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DomainValidationError("artifacts 必须是对象或 null")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("artifacts 必须可序列化为 JSON") from exc
    if len(encoded) > _ARTIFACTS_MAX_BYTES:
        raise DomainValidationError("artifacts 超过 32KB 上限")
    return dict(value)


def resolve_execution_mode(*, configured: object, requested: object) -> ExecutionMode:
    """确定一次训练提交的执行方式。

    规则（fail-closed，绝不猜测）：

    - Adapter 配置声明了模式：以配置为准；请求同时声明但与配置不一致时拒绝，
      防止客户端把本地/Mock 运行标成 ``REMOTE``；
    - 只有请求声明：采用请求值；
    - 两者都没有：拒绝——"不知道在哪里跑"不能被默认成真实远端训练。

    ``UNKNOWN`` 只描述无法回溯的存量记录，不得从配置或请求写入。
    """
    configured_mode = _declared_mode(configured, "Adapter 配置的 execution_mode")
    requested_mode = _declared_mode(requested, "请求的 execution_mode")
    if (
        configured_mode is not None
        and requested_mode is not None
        and configured_mode is not requested_mode
    ):
        raise DomainValidationError(
            "请求的 execution_mode 与 Adapter 配置不一致："
            f"{requested_mode.value} != {configured_mode.value}"
        )
    mode = configured_mode if configured_mode is not None else requested_mode
    if mode is None:
        raise DomainValidationError(
            "无法确定训练执行方式：Adapter 未声明 execution_mode，请求也未指定；"
            "不允许默认成 REMOTE"
        )
    return mode


def _declared_mode(value: object, field_name: str) -> ExecutionMode | None:
    if value is None:
        return None
    mode = coerce_enum(value, ExecutionMode, field_name)
    if mode not in DECLARED_EXECUTION_MODES:
        allowed = "/".join(sorted(item.value for item in DECLARED_EXECUTION_MODES))
        raise DomainValidationError(f"{field_name} 只能是 {allowed}")
    return mode


@dataclass(frozen=True, slots=True)
class ExternalTrainingReference:
    external_ref_id: UUID
    project_id: UUID
    adapter_id: str
    adapter_version: str
    training_delivery_id: UUID
    training_request_revision_id: UUID
    dataset_version_id: UUID
    external_run_id: str
    correlation_id: str
    idempotency_key: str
    submitted_at: datetime
    last_synced_at: datetime
    # 执行方式在提交时定死，之后不可修改：一次训练的"在哪里跑"是事实，
    # 不是可以随状态更新漂移的展示字段。
    execution_mode: ExecutionMode | str
    external_status: ExternalTrainingStatus | str = ExternalTrainingStatus.SUBMITTED
    external_run_url: str | None = None
    status_stale: bool = False
    model_ref: dict[str, object] | None = None
    evaluation_ref: dict[str, object] | None = None
    # 外部平台回传的产物元数据（对象键/sha256/大小）。模型版本登记需要它，
    # 此前回调只保留 sha256、丢弃内容，导致训练成功的产物无法在平台内登记。
    artifacts: dict[str, object] | None = None
    error_code: str | None = None
    error_summary: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in (
            "external_ref_id",
            "project_id",
            "training_delivery_id",
            "training_request_revision_id",
            "dataset_version_id",
        ):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "adapter_id",
            text_value(self.adapter_id, "adapter_id", max_length=_ADAPTER_MAX),
        )
        object.__setattr__(
            self,
            "adapter_version",
            text_value(self.adapter_version, "adapter_version", max_length=_ADAPTER_MAX),
        )
        object.__setattr__(
            self,
            "external_run_id",
            text_value(self.external_run_id, "external_run_id", max_length=200),
        )
        object.__setattr__(
            self,
            "external_run_url",
            optional_text(self.external_run_url, "external_run_url", max_length=4096),
        )
        object.__setattr__(
            self,
            "external_status",
            coerce_enum(self.external_status, ExternalTrainingStatus, "external_status"),
        )
        object.__setattr__(
            self,
            "execution_mode",
            coerce_enum(self.execution_mode, ExecutionMode, "execution_mode"),
        )
        object.__setattr__(
            self,
            "correlation_id",
            text_value(self.correlation_id, "correlation_id", max_length=128),
        )
        key = text_value(self.idempotency_key, "idempotency_key", max_length=128)
        if len(key) < 8:
            raise DomainValidationError("idempotency_key 长度不得小于 8")
        object.__setattr__(self, "idempotency_key", key)
        object.__setattr__(self, "submitted_at", aware_datetime(self.submitted_at, "submitted_at"))
        object.__setattr__(
            self, "last_synced_at", aware_datetime(self.last_synced_at, "last_synced_at")
        )
        object.__setattr__(self, "model_ref", _model_ref(self.model_ref))
        object.__setattr__(self, "evaluation_ref", _evaluation_ref(self.evaluation_ref))
        object.__setattr__(self, "artifacts", _artifacts(self.artifacts))
        for name, limit in (("error_code", 100), ("error_summary", 2000)):
            object.__setattr__(
                self,
                name,
                optional_text(getattr(self, name), name, max_length=limit),
            )
        for name in ("created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        for name in ("created_by", "updated_by"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))
        self._assert_invariants()

    def _assert_invariants(self) -> None:
        status = ExternalTrainingStatus(self.external_status)
        if status in {ExternalTrainingStatus.FAILED, ExternalTrainingStatus.UNAVAILABLE}:
            if self.status_stale is False and self.error_code is None:
                # 不可达/失败必须可解释，避免前端误显示健康状态。
                raise DomainValidationError("FAILED/UNAVAILABLE 引用必须记录错误码或 stale 标记")
        if status is ExternalTrainingStatus.SUCCEEDED and self.status_stale:
            raise DomainValidationError("SUCCEEDED 引用不得标记 stale")

    @property
    def effective_status(self) -> ExternalTrainingStatus:
        return ExternalTrainingStatus(self.external_status)

    def apply_status(
        self,
        new_status: object,
        *,
        now: datetime,
        actor_id: UUID,
        model_ref: object = None,
        evaluation_ref: object = None,
        artifacts: object = None,
        error_code: str | None = None,
        error_summary: str | None = None,
        stale: bool = False,
        run_url: str | None = None,
    ) -> ExternalTrainingReference:
        status = coerce_enum(new_status, ExternalTrainingStatus, "external_status")
        current = ExternalTrainingStatus(self.external_status)
        if status is current:
            target = current
        else:
            action = _STATUS_ACTIONS.get(status)
            if action is None:
                raise InvalidStateTransitionError("外部训练引用", current.value, status.value)
            target = external_target(current, action)
        if stale and target is ExternalTrainingStatus.SUCCEEDED:
            raise DomainValidationError("SUCCEEDED 引用不得标记 stale")
        return replace(
            self,
            external_status=target,
            status_stale=stale,
            last_synced_at=now,
            model_ref=_model_ref(model_ref) if model_ref is not None else self.model_ref,
            evaluation_ref=(
                _evaluation_ref(evaluation_ref)
                if evaluation_ref is not None
                else self.evaluation_ref
            ),
            artifacts=_artifacts(artifacts) if artifacts is not None else self.artifacts,
            error_code=(
                optional_text(error_code, "error_code", max_length=100)
                if error_code is not None
                else self.error_code
            ),
            error_summary=(
                optional_text(error_summary, "error_summary", max_length=2000)
                if error_summary is not None
                else self.error_summary
            ),
            external_run_url=(
                optional_text(run_url, "external_run_url", max_length=4096)
                if run_url is not None
                else self.external_run_url
            ),
            updated_at=now,
            updated_by=actor_id,
            row_version=self.row_version + 1,
        )

    def mark_unavailable(
        self,
        *,
        now: datetime,
        actor_id: UUID,
        error_code: str,
        error_summary: str,
    ) -> ExternalTrainingReference:
        return self.apply_status(
            ExternalTrainingStatus.UNAVAILABLE,
            now=now,
            actor_id=actor_id,
            error_code=error_code,
            error_summary=error_summary,
            stale=True,
        )

    def matches_idempotent_request(self, other: ExternalTrainingReference) -> bool:
        if self.adapter_id != other.adapter_id or self.adapter_version != other.adapter_version:
            return False
        if self.correlation_id != other.correlation_id:
            return False
        if self.execution_mode != other.execution_mode:
            # 同一幂等键重新声明了不同执行方式，说明不是同一次提交；必须冲突，
            # 不能把后到的 REMOTE 覆盖到已有的 MOCK 记录上。
            return False
        if self.model_ref != other.model_ref or self.evaluation_ref != other.evaluation_ref:
            return False
        return (
            self.training_delivery_id == other.training_delivery_id
            and self.training_request_revision_id == other.training_request_revision_id
            and self.dataset_version_id == other.dataset_version_id
        )


def new_external_reference(
    *,
    external_ref_id: UUID,
    project_id: UUID,
    adapter_id: str,
    adapter_version: str,
    training_delivery_id: UUID,
    training_request_revision_id: UUID,
    dataset_version_id: UUID,
    external_run_id: str,
    external_run_url: str | None,
    correlation_id: str,
    idempotency_key: str,
    execution_mode: ExecutionMode | str,
    model_ref: object,
    evaluation_ref: object,
    actor_id: UUID,
    now: datetime,
) -> ExternalTrainingReference:
    return ExternalTrainingReference(
        external_ref_id=external_ref_id,
        project_id=project_id,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        training_delivery_id=training_delivery_id,
        training_request_revision_id=training_request_revision_id,
        dataset_version_id=dataset_version_id,
        external_run_id=external_run_id,
        external_run_url=external_run_url,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        execution_mode=execution_mode,
        model_ref=_model_ref(model_ref),
        evaluation_ref=_evaluation_ref(evaluation_ref),
        external_status=ExternalTrainingStatus.SUBMITTED,
        submitted_at=now,
        last_synced_at=now,
        created_at=now,
        updated_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )


__all__ = [
    "ExternalTrainingReference",
    "new_external_reference",
    "resolve_execution_mode",
]
