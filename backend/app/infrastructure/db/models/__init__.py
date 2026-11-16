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
from app.infrastructure.db.models.collection_assets import (
    ActionSpecModel,
    CollectionPlanModel,
    CollectionSopModel,
    ScenarioModel,
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
from app.infrastructure.db.models.export_jobs import ExportJobModel
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.models.ingestion import (
    RawAssetLinkModel,
    RawAssetModel,
    UploadPartChecksumBindingModel,
    UploadPartModel,
    UploadSessionModel,
)
from app.infrastructure.db.models.jobs import IngestionJobModel
from app.infrastructure.db.models.suppliers import (
    AcceptanceLedgerModel,
    DeliveryBatchModel,
    InboundSegmentModel,
    SupplierModel,
)
from app.infrastructure.db.models.training import (
    DatasetGrantModel,
    ExternalTrainingReferenceModel,
    TrainingAdapterCallbackModel,
    TrainingDeliveryModel,
    TrainingRequestModel,
    TrainingRequestRevisionModel,
)

__all__ = [
    "AcceptanceLedgerModel",
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
    "ActionSpecModel",
    "CollectionBatch",
    "CollectionPlanModel",
    "CollectionSopModel",
    "CollectionTask",
    "ScenarioModel",
    "CollectionTaskDevice",
    "CollectionTaskSensor",
    "DataLineageEdgeModel",
    "DatasetDedupPolicyModel",
    "DatasetGrantModel",
    "DatasetItemModel",
    "DatasetLeakagePolicyModel",
    "DatasetLeakageReportModel",
    "DatasetModel",
    "DatasetQualityPolicyModel",
    "DatasetQualityReportModel",
    "DatasetSplitPolicyModel",
    "DatasetVersionModel",
    "DeliveryBatchModel",
    "Device",
    "EpisodeModel",
    "EpisodeRawAssetModel",
    "ExportJobModel",
    "ExternalTrainingReferenceModel",
    "GoldEvaluation",
    "GoldExposure",
    "GoldSet",
    "IAAReport",
    "IdempotencyRecord",
    "InboundSegmentModel",
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
    "SupplierModel",
    "TrainingAdapterCallbackModel",
    "TrainingDeliveryModel",
    "TrainingRequestModel",
    "TrainingRequestRevisionModel",
    "UploadPartChecksumBindingModel",
    "UploadPartModel",
    "UploadSessionModel",
    "User",
]

from app.infrastructure.db.models import modelops  # noqa: F401,E402
from app.infrastructure.db.models.datasources import (  # noqa: E402
    DataReviewModel,
    DataSourceImportModel,
    DataSourceModel,
    MediaAssetModel,
)

__all__ += [
    "DataReviewModel",
    "DataSourceImportModel",
    "DataSourceModel",
    "MediaAssetModel",
]
