"""数据集领域异常。

领域层不依赖 HTTP；稳定 ``code`` 对齐阶段 4 契约草案 ``error-codes.v1.json``。
未知状态、非法迁移、终态回退、泄漏未知一律显式失败，不存在默认成功分支。
"""

from __future__ import annotations


class DatasetDomainError(ValueError):
    """数据集域可预期错误的基类。"""

    code = "validation_failed"


class DomainValidationError(DatasetDomainError):
    """字段或跨字段校验失败。"""


class InvalidStateTransitionError(DatasetDomainError):
    """非法状态迁移或终态回退。"""

    code = "state_transition_invalid"

    def __init__(self, entity: str, current: object, action: object) -> None:
        self.entity = entity
        self.current = current
        self.action = action
        super().__init__(f"{entity} 当前状态 {current} 不允许动作 {action}")


class ResourceNotFoundError(DatasetDomainError):
    """资源不存在或不属于当前项目。"""

    code = "resource_not_found"

    def __init__(self, resource: str, resource_id: object) -> None:
        self.resource = resource
        self.resource_id = resource_id
        super().__init__(f"{resource}不存在或不可见")


class VersionConflictError(DatasetDomainError):
    """乐观锁版本不符。"""

    code = "version_conflict"

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"{resource}版本冲突")


class ResourceImmutableError(DatasetDomainError):
    """冻结或终态资源不允许原地修改。"""

    code = "resource_immutable"


class DuplicateResourceError(DatasetDomainError):
    """项目内业务键重复。"""

    code = "duplicate_resource"


class ConflictError(DatasetDomainError):
    """跨对象一致性冲突。"""

    code = "conflict"


class ForbiddenOperationError(DatasetDomainError):
    """职责分离或业务权限拒绝。"""

    code = "forbidden"


class IdempotencyKeyReusedError(DatasetDomainError):
    """同一幂等键对应不同请求体。"""

    code = "idempotency_key_reused"


class DatasetAccessDeniedError(DatasetDomainError):
    code = "dataset_access_denied"


class FreezeApprovalForbiddenError(DatasetDomainError):
    code = "dataset_freeze_approval_forbidden"


class DatasetNotReadyError(DatasetDomainError):
    code = "dataset_not_ready"


class DatasetVersionImmutableError(DatasetDomainError):
    code = "dataset_version_immutable"


class DatasetVersionNotFrozenError(DatasetDomainError):
    code = "dataset_version_not_frozen"


class DatasetEmptyError(DatasetDomainError):
    code = "dataset_empty"


class InsufficientDataError(DatasetDomainError):
    code = "dataset_insufficient_data"


class SplitInvalidError(DatasetDomainError):
    code = "dataset_split_invalid"


class LeakageCheckFailedError(DatasetDomainError):
    code = "dataset_leakage_check_failed"


class LeakageCheckUnknownError(DatasetDomainError):
    code = "dataset_leakage_check_unknown"


class LeakageWaiverForbiddenError(DatasetDomainError):
    code = "dataset_leakage_waiver_forbidden"


class QualityGateFailedError(DatasetDomainError):
    code = "dataset_quality_gate_failed"


class ItemIneligibleError(DatasetDomainError):
    code = "dataset_item_ineligible"


class AnnotationNotApprovedError(DatasetDomainError):
    code = "dataset_annotation_not_approved"


class AnnotationSnapshotMismatchError(DatasetDomainError):
    code = "dataset_annotation_snapshot_mismatch"


class ManifestMismatchError(DatasetDomainError):
    code = "dataset_manifest_mismatch"


class UpstreamInvalidatedError(DatasetDomainError):
    code = "dataset_upstream_invalidated"


class FreezeRequiresApprovalError(DatasetDomainError):
    code = "dataset_freeze_requires_approval"


class ArchiveBlockedError(DatasetDomainError):
    code = "dataset_archive_blocked"


class PolicyInvalidError(DatasetDomainError):
    """策略定义非法。"""

    code = "validation_failed"


class ExportFormatUnsupportedError(DatasetDomainError):
    code = "export_format_unsupported"


class ExportFormatVersionUnsupportedError(DatasetDomainError):
    code = "export_format_version_unsupported"


class ExportConfigInvalidError(DatasetDomainError):
    code = "export_config_invalid"


class ExportJobNotRetryableError(DatasetDomainError):
    code = "export_job_not_retryable"


class ExportJobCancelledError(DatasetDomainError):
    code = "export_job_cancelled"


class ExportIncompleteError(DatasetDomainError):
    code = "export_incomplete"


class ExportChecksumMismatchError(DatasetDomainError):
    code = "export_checksum_mismatch"


class ExportOutputConflictError(DatasetDomainError):
    code = "export_output_conflict"


class ExportArtifactVerifyFailedError(DatasetDomainError):
    code = "export_artifact_verify_failed"


class ExportExecutionFailedError(DatasetDomainError):
    code = "export_job_failed"


class ExportPartialError(DatasetDomainError):
    code = "export_partial"


class ExportTimeoutError(DatasetDomainError):
    code = "export_job_timeout"
