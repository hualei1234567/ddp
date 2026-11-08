"""ORM 模型聚合，供 Alembic 与仓储导入。"""

from app.infrastructure.db.models.collection import (
    AuditLog,
    CollectionBatch,
    CollectionTask,
    CollectionTaskDevice,
    CollectionTaskSensor,
    IdempotencyRecord,
    OutboxEvent,
)
from app.infrastructure.db.models.devices import (
    CalibrationProfile,
    CalibrationRecord,
    CalibrationTargetRef,
    Device,
    Robot,
    Sensor,
)
from app.infrastructure.db.models.episodes import (
    DataLineageEdgeModel,
    EpisodeModel,
    EpisodeRawAssetModel,
    LineageNodeRefModel,
)
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.models.ingestion import (
    RawAssetLinkModel,
    RawAssetModel,
    UploadPartChecksumBindingModel,
    UploadPartModel,
    UploadSessionModel,
)
from app.infrastructure.db.models.jobs import IngestionJobModel

__all__ = [
    "AuditLog",
    "CalibrationProfile",
    "CalibrationRecord",
    "CalibrationTargetRef",
    "CollectionBatch",
    "CollectionTask",
    "CollectionTaskDevice",
    "CollectionTaskSensor",
    "DataLineageEdgeModel",
    "Device",
    "EpisodeModel",
    "EpisodeRawAssetModel",
    "IdempotencyRecord",
    "IngestionJobModel",
    "LineageNodeRefModel",
    "OutboxEvent",
    "Project",
    "ProjectMember",
    "RawAssetLinkModel",
    "RawAssetModel",
    "Robot",
    "Sensor",
    "UploadPartChecksumBindingModel",
    "UploadPartModel",
    "UploadSessionModel",
    "User",
]
