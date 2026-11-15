"""数据集领域异常。

领域层不依赖 HTTP；稳定 ``code`` 对齐阶段 4 契约草案 ``error-codes.v1.json``。
未知状态、非法迁移、终态回退、泄漏未知一律显式失败，不存在默认成功分支。
"""

from __future__ import annotations

from collections.abc import Mapping


class DatasetDomainError(ValueError):
    """数据集域可预期错误的基类。

    ``details`` 是**可在安全前提下回显给客户端**的结构化排障信息
    （例如不合格条目的引用、冲突路径）。是否透传由 API 层按 code 白名单
    决定（见 ``app/api/errors.py`` 的 ``details_codes``），领域层只负责
    如实携带，不判断安全性——与设备域 G-19 的口径一致。
    """

    code = "validation_failed"

    def __init__(
        self,
        message: str = "",
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.details: Mapping[str, object] | None = details


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
    """无权访问数据集或数据集产物（契约：无访问/下载/导出权限）。

    与 ``forbidden`` 的区别：``forbidden`` 表示"不是本项目成员或角色不够"，
    本码表示"是项目上下文内合法主体，但对这个数据集缺少授权"——例如项目内
    角色可读但该数据集范围未授权，或跨项目主体持过期/范围不符的 Grant。
    未授权访问默认应表现为 404（不泄漏存在性），只有"存在性已知的范围内拒绝"
    才用本码。
    """

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
    """入选条目不合格（Episode/标注/Manifest/标定/时间同步/用途）。

    契约语义是**逐项资格判定**：选择门禁把不合格候选排除并记录排除码，
    只有当"调用方显式指名了具体条目、而这些条目全部不合格"时才升级为本错误
    （见 ``pipeline.require_eligible_selection``）。details 携带不合格引用。
    """

    code = "dataset_item_ineligible"


class AnnotationNotApprovedError(DatasetDomainError):
    """引用了未批准标注、草稿、驳回或返工中的标注。

    选择门禁已按 ``ANNOTATION_NOT_APPROVED`` 排除；本错误用于"显式指名条目"
    路径下的确定性拒绝，details 携带被拒条目的 Episode 与提交引用。
    """

    code = "dataset_annotation_not_approved"


class AnnotationSnapshotMismatchError(DatasetDomainError):
    """标注/Schema 版本快照与任务冻结不一致。

    AC-DS-29 原文：「任务冻结 Schema/标注版本；DatasetItem 引用不同 Schema
    版本或不存在的提交」→「快照不一致拒绝」。因此判定点是**DatasetItem 的
    Schema 快照必须来自已批准提交的冻结快照**，不是任意 Schema 版本差异。
    """

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
    """输出路径/产物冲突；不得静默覆盖成功产物。

    契约原文：「输出路径/产物冲突；不得静默覆盖成功产物」。判定点是
    **同一输出前缀下已存在成功产物**（`SUCCEEDED` 的 Job 已写该前缀），
    此时 retry/新 Job 不得覆盖。仅"前缀恰好相同但尚无成功产物"不构成冲突——
    那属于正常重试（``ExportJob.retry`` 复用同一前缀）。
    """

    code = "export_output_conflict"


class ExportArtifactVerifyFailedError(DatasetDomainError):
    code = "export_artifact_verify_failed"


class ExportExecutionFailedError(DatasetDomainError):
    code = "export_job_failed"


class ExportPartialError(DatasetDomainError):
    code = "export_partial"


class ExportTimeoutError(DatasetDomainError):
    code = "export_job_timeout"
