"""训练需求、交付、外部引用与授权应用层命令。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class NewTrainingRequest:
    code: str
    title: str
    owner_id: UUID
    priority: str
    snapshot: dict[str, object]


@dataclass(frozen=True, slots=True)
class NewTrainingRequestRevision:
    snapshot: dict[str, object]
    change_reason: str


@dataclass(frozen=True, slots=True)
class NewTrainingDelivery:
    training_request_id: UUID
    training_request_revision_id: UUID
    dataset_version_id: UUID
    export_job_id: UUID
    recipient_id: UUID
    recipient_team: str | None
    delivery_note_no: str


@dataclass(frozen=True, slots=True)
class NewExternalTrainingReference:
    adapter_id: str
    adapter_version: str
    correlation_id: str
    model_ref: dict[str, object] | None = None
    evaluation_ref: dict[str, object] | None = None
    config: dict[str, object] = field(default_factory=dict)
    # 训练代码/镜像版本与 run 级模型版本标识：由发起方声明，未知留空。
    code_version: str | None = None
    model_version: str | None = None
    # 显式执行方式（LOCAL/MOCK/DRY_RUN/REMOTE）。与 Adapter 配置同时存在时
    # 必须一致；两者都没有时提交被拒，不默认成 REMOTE。
    execution_mode: str | None = None


@dataclass(frozen=True, slots=True)
class NewDatasetGrant:
    dataset_version_id: UUID | None
    grantee_project_id: UUID
    grantee_team: str | None
    scope: tuple[str, ...]
    reason: str
    expires_at: datetime


__all__ = [
    "NewDatasetGrant",
    "NewExternalTrainingReference",
    "NewTrainingDelivery",
    "NewTrainingRequest",
    "NewTrainingRequestRevision",
]
