"""阶段 5 应用层端口与记录类型。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.application.idempotency import (
    IdempotencyRecord as _IdempotencyRecord,
)
from app.application.idempotency import (
    IdempotencyRepository as _IdempotencyRepository,
)
from app.domain.identity.models import ProjectScope
from app.domain.modelops.gap_candidates import FailureCaseScan
from app.domain.modelops.gap_evidence import GapEvidenceFacts
from app.domain.modelops.gaps import MeasuredCoverage
from app.domain.modelops.models import (
    DataRequirement,
    Evaluation,
    InferenceService,
    InferenceTask,
    Model,
    ModelFeedback,
    ModelVersion,
)
from app.domain.modelops.training_source import TrainingSourceFacts


class UnitOfWork(Protocol):
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class AuditRecord(Protocol):
    audit_id: UUID
    project_id: UUID
    actor_user_id: UUID | None
    actor_role: str | None
    resource_type: str
    resource_id: UUID
    action: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    request_id: str | None
    result: str
    details: dict[str, object]
    occurred_at: datetime


class OutboxEvent(Protocol):
    event_id: UUID
    project_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, object]
    occurred_at: datetime


class AuditRepository(Protocol):
    def record(self, scope: ProjectScope, record: AuditRecord) -> None: ...


class OutboxRepository(Protocol):
    def enqueue(self, scope: ProjectScope, event: OutboxEvent) -> None: ...


# 幂等记录类型与仓储协议由 application 顶层统一声明，避免四个域各写一份。
# 这里重新导出，保持本域既有的 ``from app.application.modelops.ports import
# IdempotencyRecord`` 调用点不变。
IdempotencyRecord = _IdempotencyRecord
IdempotencyRepository = _IdempotencyRepository


class ModelRepository(Protocol):
    def add(self, scope: ProjectScope, model: Model) -> Model: ...
    def get(self, scope: ProjectScope, model_id: UUID) -> Model | None: ...
    def get_by_code(self, scope: ProjectScope, code: str) -> Model | None: ...
    def save(self, scope: ProjectScope, model: Model, *, expected_version: int) -> Model: ...
    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        task_type: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Model]: ...


class ModelVersionRepository(Protocol):
    def add(self, scope: ProjectScope, version: ModelVersion) -> ModelVersion: ...
    def get(self, scope: ProjectScope, version_id: UUID) -> ModelVersion | None: ...
    def save(
        self, scope: ProjectScope, version: ModelVersion, *, expected_version: int
    ) -> ModelVersion: ...
    def list(
        self,
        scope: ProjectScope,
        model_id: UUID,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ModelVersion]: ...
    def next_version_no(self, scope: ProjectScope, model_id: UUID) -> int: ...


class EvaluationRepository(Protocol):
    def add(self, scope: ProjectScope, evaluation: Evaluation) -> Evaluation: ...
    def get(self, scope: ProjectScope, evaluation_id: UUID) -> Evaluation | None: ...
    def save(
        self, scope: ProjectScope, evaluation: Evaluation, *, expected_version: int
    ) -> Evaluation: ...
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
    ) -> list[Evaluation]: ...


class InferenceServiceRepository(Protocol):
    def add(self, scope: ProjectScope, service: InferenceService) -> InferenceService: ...
    def get(self, scope: ProjectScope, service_id: UUID) -> InferenceService | None: ...
    def save(
        self, scope: ProjectScope, service: InferenceService, *, expected_version: int
    ) -> InferenceService: ...
    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[InferenceService]: ...
    def count_active_for_version(self, scope: ProjectScope, model_version_id: UUID) -> int: ...


class InferenceTaskRepository(Protocol):
    def add(self, scope: ProjectScope, task: InferenceTask) -> InferenceTask: ...
    def get(self, scope: ProjectScope, task_id: UUID) -> InferenceTask | None: ...
    def save(
        self, scope: ProjectScope, task: InferenceTask, *, expected_version: int
    ) -> InferenceTask: ...
    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        model_version_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[InferenceTask]: ...


class FeedbackRepository(Protocol):
    def add(self, scope: ProjectScope, feedback: ModelFeedback) -> ModelFeedback: ...
    def get(self, scope: ProjectScope, feedback_id: UUID) -> ModelFeedback | None: ...
    def save(
        self, scope: ProjectScope, feedback: ModelFeedback, *, expected_version: int
    ) -> ModelFeedback: ...
    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        issue_type: str | None = None,
        model_version_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ModelFeedback]: ...
    def list_open_dimensions(
        self, scope: ProjectScope, *, dimension_type: str | None = None
    ) -> list[ModelFeedback]: ...


class RequirementRepository(Protocol):
    def add(self, scope: ProjectScope, requirement: DataRequirement) -> DataRequirement: ...
    def get(self, scope: ProjectScope, requirement_id: UUID) -> DataRequirement | None: ...
    def get_by_code(self, scope: ProjectScope, code: str) -> DataRequirement | None: ...
    def save(
        self, scope: ProjectScope, requirement: DataRequirement, *, expected_version: int
    ) -> DataRequirement: ...
    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        priority: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DataRequirement]: ...
    def list_by_feedback(self, scope: ProjectScope, feedback_id: UUID) -> list[DataRequirement]: ...


class DatasetGate(Protocol):
    """阶段 4 数据集门禁：冻结/有效性检查。"""

    def version_state(self, project_id: UUID, version_id: UUID) -> tuple[str, str] | None: ...

    def item_count(self, project_id: UUID, version_id: UUID) -> int: ...


class EpisodeCoverageDirectory(Protocol):
    """缺口视图的真实覆盖来源：按维度统计项目内可用 Episode 的只读端口。

    **开放轴**（SCENE/ROBOT/DEVICE/QUALITY_STATUS）只返回「实测到」的维度值；
    某维度值一个 Episode 都没有时**不出现**在结果里（``current_count`` 的 0 与
    "测不到"必须区分，见 ADR-0013 §3.4）。

    **封闭轴**（ACTION/LABEL/MODALITY，见 ``CLOSED_COVERAGE_DIMENSIONS``）语义不同：
    该轴的数据总体完整时，可以断言"缺席即 0"。调用方通过 ``wanted_keys`` 声明
    它需要结论的 ``(dimension_type, dimension_value)``，实现可以在总体完整时为
    缺席的键补一条 ``count=0`` 的实测记录；总体不完整时不补（上游据此回退人工值
    或 UNKNOWN），绝不用部分总体的计数冒充权威结论。

    实现必须以服务端 ``ProjectScope`` 为准强制 ``project_id`` 隔离。
    返回类型直接复用领域值对象 :class:`MeasuredCoverage`，不另立一份记录类型。
    """

    def coverage_by_dimension(
        self,
        scope: ProjectScope,
        *,
        dimension_types: frozenset[str] | None = None,
        wanted_keys: Iterable[tuple[str, str]] | None = None,
    ) -> list[MeasuredCoverage]: ...


class GapEvidenceDirectory(Protocol):
    """缺口视图的多来源证据端口（N7-004）：只读、项目内、有界。

    实现负责从评测失败用例、低分评测、标注分歧与未满足需求等**真实事实**中
    取出证据，但**不做归因**：返回的 :class:`GapEvidenceFacts` 保留原始维度或
    提示值，由领域函数 ``attribute_gap_evidence`` 决定挂到哪个既有维度键。

    实现必须以服务端 ``ProjectScope`` 强制 ``project_id`` 隔离；扫描必须有界，
    触达上限时在 ``truncated_sources`` 里如实声明，绝不静默截断。
    """

    def evidence(self, scope: ProjectScope) -> GapEvidenceFacts: ...


class GapCandidateDirectory(Protocol):
    """候选缺口的失败事实端口（N7-005）：只读、项目内、有界。

    返回当前项目近期评测的 ``failure_cases`` 明细（已由领域层解析校验）与扫描
    边界信息；**不做聚合、不做归因**。聚合与匹配由
    ``app.domain.modelops.gap_candidates.build_gap_candidates`` 决定。

    实现必须以服务端 ``ProjectScope`` 强制 ``project_id`` 隔离；扫描触达上限时
    在 ``FailureCaseScan.truncated`` 里如实声明，绝不静默截断。
    """

    def failure_case_scan(self, scope: ProjectScope) -> FailureCaseScan: ...


class TrainingSourceGate(Protocol):
    """阶段 4 训练来源门禁：解析同项目的训练需求/交付/外部引用。

    三个引用各自查不到或跨项目时返回 ``None``；应用层据此抛
    ``model_training_source_invalid``（不区分「不存在」与「跨项目」，不泄漏存在性）。
    """

    def resolve(
        self,
        project_id: UUID,
        *,
        request_id: UUID | None = None,
        delivery_id: UUID | None = None,
        external_ref_id: UUID | None = None,
    ) -> TrainingSourceFacts | None: ...


class ArtifactInspector(Protocol):
    def object_size(self, object_key: str) -> int | None: ...

    def object_sha256(self, object_key: str) -> str | None:
        """返回对象的 `sha256:<hex>`；对象不存在返回 None。"""


class ModelPlatformGateway(Protocol):
    def capabilities(self, adapter_id: str) -> frozenset[str]: ...

    def submit_evaluation(
        self,
        *,
        adapter_id: str,
        adapter_version: str,
        evaluation_id: UUID,
        model_version_id: UUID,
        dataset_version_id: UUID,
        config: dict[str, object],
    ) -> None: ...

    def submit_inference(
        self,
        *,
        adapter_id: str,
        adapter_version: str,
        task_id: UUID,
        model_version_id: UUID,
        service_id: UUID | None,
        dataset_version_id: UUID | None,
        episode_ids: list[UUID],
        config: dict[str, object],
    ) -> None: ...


class CallbackRepository(Protocol):
    def get_by_event(self, adapter_id: str, event_id: str) -> CallbackRecord | None: ...
    def get_by_nonce(self, adapter_id: str, nonce: str) -> CallbackRecord | None: ...
    def add(self, record: CallbackRecord) -> None: ...
    def prune_before(self, *, received_before: datetime) -> None: ...


class CallbackRecord(Protocol):
    callback_id: UUID
    adapter_id: str
    event_id: str
    nonce: str
    payload_sha256: str
    received_at: datetime


__all__ = [
    "ArtifactInspector",
    "AuditRecord",
    "AuditRepository",
    "CallbackRecord",
    "CallbackRepository",
    "DatasetGate",
    "DataRequirement",
    "EpisodeCoverageDirectory",
    "EvaluationRepository",
    "FeedbackRepository",
    "GapEvidenceDirectory",
    "IdempotencyRecord",
    "IdempotencyRepository",
    "InferenceServiceRepository",
    "InferenceTaskRepository",
    "MeasuredCoverage",
    "ModelPlatformGateway",
    "ModelRepository",
    "ModelVersionRepository",
    "OutboxEvent",
    "OutboxRepository",
    "RequirementRepository",
    "TrainingSourceFacts",
    "TrainingSourceGate",
    "UnitOfWork",
]
