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
