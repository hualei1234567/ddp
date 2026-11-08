"""ORM 模型聚合，供 Alembic 与仓储导入。"""

from app.infrastructure.db.models.annotation_catalog import (
    AnnotationSampleEpisode,
    AnnotationSampleSet,
    AnnotationSchema,
)
from app.infrastructure.db.models.annotation_quality import (
    GoldEvaluation,
    GoldExposure,
    GoldSet,
    IAAReport,
    QualitySample,
)
from app.infrastructure.db.models.annotation_results import (
    AnnotationArbitration,
    AnnotationRecord,
    AnnotationReview,
    AnnotationSubmission,
)
from app.infrastructure.db.models.annotation_work import (
    AnnotationAssignment,
    AnnotationAssignmentEvent,
    AnnotationBatch,
    AnnotationTask,
)
from app.infrastructure.db.models.collection import (
    AuditLog,
    CollectionBatch,
    CollectionTask,
    CollectionTaskDevice,
    CollectionTaskSensor,
    IdempotencyRecord,
    OutboxEvent,
)
from app.infrastructure.db.models.dataset_policies import (
    DatasetDedupPolicyModel,
    DatasetLeakagePolicyModel,
    DatasetQualityPolicyModel,
    DatasetSplitPolicyModel,
)
from app.infrastructure.db.models.dataset_reports import (
    DatasetLeakageReportModel,
    DatasetQualityReportModel,
)
from app.infrastructure.db.models.datasets import (
    DatasetItemModel,
    DatasetModel,
    DatasetVersionModel,
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
    "AnnotationArbitration",
    "AnnotationAssignment",
    "AnnotationAssignmentEvent",
    "AnnotationBatch",
    "AnnotationRecord",
    "AnnotationReview",
    "AnnotationSampleEpisode",
    "AnnotationSampleSet",
    "AnnotationSchema",
    "AnnotationSubmission",
    "AnnotationTask",
    "AuditLog",
    "CalibrationProfile",
    "CalibrationRecord",
    "CalibrationTargetRef",
    "CollectionBatch",
    "CollectionTask",
    "CollectionTaskDevice",
    "CollectionTaskSensor",
    "DataLineageEdgeModel",
    "DatasetDedupPolicyModel",
    "DatasetItemModel",
    "DatasetLeakagePolicyModel",
    "DatasetLeakageReportModel",
    "DatasetModel",
    "DatasetQualityPolicyModel",
    "DatasetQualityReportModel",
    "DatasetSplitPolicyModel",
    "DatasetVersionModel",
    "Device",
    "EpisodeModel",
    "EpisodeRawAssetModel",
    "GoldEvaluation",
    "GoldExposure",
    "GoldSet",
    "IAAReport",
    "IdempotencyRecord",
    "IngestionJobModel",
    "LineageNodeRefModel",
    "OutboxEvent",
    "Project",
    "ProjectMember",
    "QualitySample",
    "RawAssetLinkModel",
    "RawAssetModel",
    "Robot",
    "Sensor",
    "UploadPartChecksumBindingModel",
    "UploadPartModel",
    "UploadSessionModel",
    "User",
]
