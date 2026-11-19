"""阶段 5 训练来源门禁：解析同项目的训练需求/交付/外部引用。

与 `SqlDatasetGate` 同一类跨模块只读适配：只读取阶段 4 表，不写、不持有业务规则。
规则本身在 `app.domain.modelops.training_source`。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.datasets.training_enums import NON_REAL_EXECUTION_MODES, ExecutionMode
from app.domain.modelops.training_source import TrainingSourceFacts
from app.infrastructure.db.models.training import (
    ExternalTrainingReferenceModel,
    TrainingDeliveryModel,
    TrainingRequestModel,
)


class SqlTrainingSourceGate:
    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(
        self,
        project_id: UUID,
        *,
        request_id: UUID | None = None,
        delivery_id: UUID | None = None,
        external_ref_id: UUID | None = None,
    ) -> TrainingSourceFacts | None:
        if request_id is None and delivery_id is None and external_ref_id is None:
            return TrainingSourceFacts()

        if request_id is not None:
            row = self._session.scalar(
                select(TrainingRequestModel.id).where(
                    TrainingRequestModel.id == request_id,
                    TrainingRequestModel.project_id == project_id,
                )
            )
            if row is None:
                return None

        delivery_request_id: UUID | None = None
        if delivery_id is not None:
            delivery = self._session.scalar(
                select(TrainingDeliveryModel).where(
                    TrainingDeliveryModel.id == delivery_id,
                    TrainingDeliveryModel.project_id == project_id,
                )
            )
            if delivery is None:
                return None
            delivery_request_id = delivery.training_request_id

        external_ref_delivery_id: UUID | None = None
        external_ref_non_real: bool | None = None
        if external_ref_id is not None:
            reference = self._session.scalar(
                select(ExternalTrainingReferenceModel).where(
                    ExternalTrainingReferenceModel.id == external_ref_id,
                    ExternalTrainingReferenceModel.project_id == project_id,
                )
            )
            if reference is None:
                return None
            external_ref_delivery_id = reference.training_delivery_id
            # 执行方式权威定义在 datasets 域；这里只翻译成"是否非真实"。
            # 状态为 DRY_RUN 同样视为非真实，即使执行方式字段缺失也不放行。
            external_ref_non_real = (
                ExecutionMode(reference.execution_mode) in NON_REAL_EXECUTION_MODES
                or reference.external_status == "DRY_RUN"
            )

        return TrainingSourceFacts(
            request_id=request_id,
            delivery_id=delivery_id,
            delivery_request_id=delivery_request_id,
            external_ref_id=external_ref_id,
            external_ref_delivery_id=external_ref_delivery_id,
            external_ref_non_real=external_ref_non_real,
        )


__all__ = ["SqlTrainingSourceGate"]
