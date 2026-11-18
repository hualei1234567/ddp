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


# 「可作为 Episode 数据流」的状态集合。
#
# episodes 与 datasets 两域各有一份同义的内联判断（历史遗留），本常量为
# **新代码**的权威引用点，不回溯改动既有实现——与
# ``episodes/state_machines.py`` 里 ``EPISODE_COVERABLE_STATUSES`` 的处理口径一致：
# 先给新代码一处可引用的事实，避免第四份副本继续增殖。
RAW_ASSET_STREAM_USABLE_STATUSES: frozenset[str] = frozenset(
    {
        RawAssetStatus.VALID.value,
        RawAssetStatus.ARCHIVED.value,
    }
)


# 内容去重只对这些“可用态”生效；REJECTED/TOMBSTONED 不占用唯一键。
RAW_ASSET_ACTIVE_STATUSES: frozenset[str] = frozenset(
    {
        RawAssetStatus.VALID.value,
        RawAssetStatus.QUARANTINED.value,
        RawAssetStatus.ARCHIVED.value,
    }
)


class RawAssetQuarantineCode(StrEnum):
    """RawAsset 隔离原因稳定码。

    前六项来自 ``raw-asset.v1.schema.json`` 草案；后三项是本任务新增的
    内容一致性校验码，契约草案尚未收录，已作为风险上报。
    """

    MALWARE_SUSPECTED = "malware_suspected"
    MALWARE_SCANNER_UNAVAILABLE = "malware_scanner_unavailable"
    SIGNATURE_AMBIGUOUS = "signature_ambiguous"
    UNSUPPORTED_FORMAT = "unsupported_format"
    METADATA_CONFLICT = "metadata_conflict"
    PARSE_FAILED = "parse_failed"
    HASH_MISMATCH = "hash_mismatch"
    SIZE_MISMATCH = "size_mismatch"
    OBJECT_MISSING = "object_missing"


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


class RawAssetAction(StrEnum):
    """原始资产的公开状态动作。

    这些动作在领域状态机里早就有边，但此前**没有任何 HTTP 入口**：被隔离的
    资产无法放行或驳回，资产也无法归档或墓碑，界面只能展示"隔离原因""墓碑
    时间"这些用户永远造不出来的状态。这里把它们收敛成唯一对外集合，
    系统内部动作（``validate-*`` / ``system-fail``）不在此列。
    """

    RELEASE_QUARANTINE = "release-quarantine-or-revalidate"
    REJECT_QUARANTINED = "reject-quarantined"
    POST_HOC_QUARANTINE = "post-hoc-quarantine"
    ARCHIVE = "archive"
    TOMBSTONE = "tombstone"


PUBLIC_RAW_ASSET_ACTIONS: frozenset[str] = frozenset(action.value for action in RawAssetAction)


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
