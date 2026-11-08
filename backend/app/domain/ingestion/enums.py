"""上传会话、分片与 RawAsset 枚举。

取值以阶段 2 契约草案 ``state-machines.v1.json`` 与
``raw-asset.v1.schema.json`` 为准；未知枚举一律拒绝，不存在默认成功分支。
"""

from __future__ import annotations

from enum import StrEnum


class UploadSessionStatus(StrEnum):
    CREATED = "CREATED"
    UPLOADING = "UPLOADING"
    COMPLETING = "COMPLETING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class UploadVerificationStatus(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class RawAssetStatus(StrEnum):
    VALIDATING = "VALIDATING"
    VALID = "VALID"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"
    TOMBSTONED = "TOMBSTONED"


# 内容去重只对这些“可用态”生效；REJECTED/TOMBSTONED 不占用唯一键。
RAW_ASSET_ACTIVE_STATUSES: frozenset[str] = frozenset(
    {
        RawAssetStatus.VALID.value,
        RawAssetStatus.QUARANTINED.value,
        RawAssetStatus.ARCHIVED.value,
    }
)


class RawAssetSourceType(StrEnum):
    UPLOAD = "UPLOAD"
    DEVICE = "DEVICE"
    IMPORT = "IMPORT"


class RawAssetFormat(StrEnum):
    MCAP = "MCAP"
    ROSBAG2 = "ROSBAG2"
    HDF5 = "HDF5"
    MP4 = "MP4"
    PARQUET = "PARQUET"
    CSV = "CSV"
    JSON = "JSON"
    ZIP = "ZIP"
    UNKNOWN = "UNKNOWN"


class StorageClass(StrEnum):
    STANDARD = "STANDARD"
    INFREQUENT = "INFREQUENT"
    ARCHIVE = "ARCHIVE"


class RawAssetLinkType(StrEnum):
    UPLOAD_SESSION = "UPLOAD_SESSION"
    COLLECTION_TASK = "COLLECTION_TASK"
    COLLECTION_BATCH = "COLLECTION_BATCH"


class RawAssetLinkStatus(StrEnum):
    """使用关系状态待异步校验通过后再转为 ACTIVE。"""

    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class UploadSessionAction(StrEnum):
    BEGIN_UPLOAD = "begin-upload"
    COMPLETE = "complete"
    SYSTEM_REGISTER = "system-register"
    SYSTEM_RETRYABLE_FAILURE = "system-retryable-failure"
    SYSTEM_FAIL = "system-fail"
    ABORT = "abort"
    SYSTEM_EXPIRE = "system-expire"


class IngestionJobType(StrEnum):
    """阶段 2 接入 Job 类型；未知类型一律拒绝。"""

    VALIDATE = "VALIDATE"
    PARSE_METADATA = "PARSE_METADATA"
    SEGMENT_EPISODE = "SEGMENT_EPISODE"
    REVALIDATE = "REVALIDATE"


class IngestionJobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


class IngestionJobAction(StrEnum):
    WORKER_START = "worker-start"
    WORKER_SUCCEED = "worker-succeed"
    WORKER_FAIL = "worker-fail"
    RETRY = "retry"
    CANCEL = "cancel"
    WORKER_CANCELLED = "worker-cancelled"
