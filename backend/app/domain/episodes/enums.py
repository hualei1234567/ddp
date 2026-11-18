"""Episode、切片与血缘枚举。

取值严格对应 ``episode.v1.schema.json``、``lineage-edge.v1.schema.json``
与 ``state-machines.v1.json``；未知值一律拒绝。
"""

from __future__ import annotations

from enum import StrEnum


class EpisodeStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    QUALITY_CHECKING = "QUALITY_CHECKING"
    ACCEPTED = "ACCEPTED"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"


class EpisodeQualityStatus(StrEnum):
    NOT_CHECKED = "NOT_CHECKED"
    PASS = "PASS"  # noqa: S105 - 枚举值不是凭据
    WARN = "WARN"
    FAIL = "FAIL"


class EpisodeAnnotationStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SegmentationMethod(StrEnum):
    MANUAL_TIME_RANGE = "MANUAL_TIME_RANGE"
    RULE = "RULE"
    PARSER_SEGMENT = "PARSER_SEGMENT"


class LineageNodeType(StrEnum):
    """血缘图节点类型。

    ``DATA_SOURCE`` / ``DATA_SOURCE_IMPORT`` 是**只读投影节点**：数据源与导入
    任务的权威事实在 ``data_sources`` / ``data_source_imports`` 表里，血缘图
    不另存一份（否则两处会漂移）。遍历时由
    :class:`app.application.episodes.provenance.SourceProvenanceResolver`
    按 ``data_source_media_assets`` 的既有外键即时展开成边，因此这两个取值
    **不会**出现在 ``lineage_node_refs`` / ``data_lineage_edges`` 里。
    """

    DATA_SOURCE = "DATA_SOURCE"
    DATA_SOURCE_IMPORT = "DATA_SOURCE_IMPORT"
    RAW_ASSET = "RAW_ASSET"
    UPLOAD_SESSION = "UPLOAD_SESSION"
    COLLECTION_TASK = "COLLECTION_TASK"
    COLLECTION_BATCH = "COLLECTION_BATCH"
    INGESTION_JOB = "INGESTION_JOB"
    EPISODE = "EPISODE"
    QUALITY_RUN = "QUALITY_RUN"
    ANNOTATION = "ANNOTATION"
    DATASET_VERSION = "DATASET_VERSION"
    TRAINING_RUN = "TRAINING_RUN"


class LineageRelation(StrEnum):
    UPLOADED_BY = "UPLOADED_BY"
    PRODUCED_BY = "PRODUCED_BY"
    USED_AS_INPUT = "USED_AS_INPUT"
    PRODUCED = "PRODUCED"
    SEGMENTED_INTO = "SEGMENTED_INTO"
    SUPERSEDES = "SUPERSEDES"
    INVALIDATES = "INVALIDATES"
    EVALUATED_BY = "EVALUATED_BY"
    ANNOTATED_BY = "ANNOTATED_BY"
    INCLUDED_IN = "INCLUDED_IN"
    # DatasetVersion → TrainingRun：某次训练基于哪个数据集版本（ADR-0036）。
    TRAINED_ON = "TRAINED_ON"
