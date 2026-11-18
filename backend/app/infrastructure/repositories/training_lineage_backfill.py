"""存量外部训练引用的血缘补偿扫描（只读）。

**为什么单独一个仓储**：``SqlExternalReferenceRepository`` 已被训练用例占满，
本查询只服务系统补偿任务，且是"训练引用表 + 血缘边表"的反连接读取。把它塞进
任一既有仓储都会让那个文件继续膨胀；放在训练模块自己的基础设施层，既不跨模块
读别人的表（``external_training_references`` 是本模块的表，血缘边是只读公共图），
也让扫描口径只有一个权威实现。

**只读**：本类不写任何表。写边由用例通过既有
:class:`app.application.datasets.lineage.DatasetVersionLineageRecorder` 完成。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.application.datasets.training_ports import (
    DEFAULT_BATCH_LIMIT,
    MAX_BATCH_LIMIT,
    LineageBackfillCandidate,
)
from app.domain.episodes.enums import LineageNodeType, LineageRelation
from app.infrastructure.db.models.episodes import DataLineageEdgeModel
from app.infrastructure.db.models.training import ExternalTrainingReferenceModel
from app.infrastructure.repositories._collection_sql import as_utc
from app.infrastructure.repositories._dataset_sql import bounded_limit


class SqlTrainingLineageBackfillSource:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_missing_trained_on(
        self,
        *,
        after_id: UUID | None = None,
        limit: int = DEFAULT_BATCH_LIMIT,
    ) -> list[LineageBackfillCandidate]:
        """按 ``external_ref_id`` 键集分页，返回缺少 ``TRAINED_ON`` 边的引用。

        反连接条件必须与 N5-005 写边的端点、方向、关系完全一致，否则会把
        "已存在的边"误判成缺失并尝试重复写入。``project_id`` 参与连接：
        跨项目的同 ID 边不算本项目的边。
        """
        bound = bounded_limit(limit, maximum=MAX_BATCH_LIMIT)
        statement = (
            select(ExternalTrainingReferenceModel)
            .outerjoin(
                DataLineageEdgeModel,
                and_(
                    DataLineageEdgeModel.project_id == ExternalTrainingReferenceModel.project_id,
                    DataLineageEdgeModel.from_node_type == LineageNodeType.DATASET_VERSION.value,
                    DataLineageEdgeModel.from_node_id
                    == ExternalTrainingReferenceModel.dataset_version_id,
                    DataLineageEdgeModel.to_node_type == LineageNodeType.TRAINING_RUN.value,
                    DataLineageEdgeModel.to_node_id == ExternalTrainingReferenceModel.id,
                    DataLineageEdgeModel.relation == LineageRelation.TRAINED_ON.value,
                ),
            )
            .where(DataLineageEdgeModel.id.is_(None))
            .order_by(ExternalTrainingReferenceModel.id)
            .limit(bound)
        )
        if after_id is not None:
            statement = statement.where(ExternalTrainingReferenceModel.id > after_id)
        rows = self._session.scalars(statement)
        candidates: list[LineageBackfillCandidate] = []
        for row in rows:
            created_at = as_utc(row.created_at)
            if created_at is None:
                # 列声明为 NOT NULL；这里显式失败而不是补一个假时间。
                raise ValueError(f"外部训练引用 {row.id} 缺少 created_at")
            candidates.append(
                LineageBackfillCandidate(
                    project_id=row.project_id,
                    external_ref_id=row.id,
                    dataset_version_id=row.dataset_version_id,
                    training_delivery_id=row.training_delivery_id,
                    training_request_revision_id=row.training_request_revision_id,
                    adapter_id=row.adapter_id,
                    external_run_id=row.external_run_id,
                    execution_mode=row.execution_mode,
                    created_at=created_at,
                    created_by=row.created_by,
                )
            )
        return candidates


__all__ = ["MAX_BATCH_LIMIT", "SqlTrainingLineageBackfillSource"]
