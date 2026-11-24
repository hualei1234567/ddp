"""上传会话与 RawAsset 领域异常。

领域层不依赖 HTTP；API 层负责把稳定 ``code`` 映射为契约错误码与状态码。
所有失败必须携带稳定 code，未知错误不得默认成功。
"""

from __future__ import annotations

from collections.abc import Mapping


class IngestionDomainError(ValueError):
    """上传/资产域可预期错误的基类。"""

    code = "validation_failed"

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.details: Mapping[str, object] = dict(details or {})


class DomainValidationError(IngestionDomainError):
    """字段或跨字段校验失败。"""


class InvalidFilenameError(IngestionDomainError):
    code = "filename_invalid"


class FileTypeUnsupportedError(IngestionDomainError):
    code = "file_type_unsupported"


class FileTooLargeError(IngestionDomainError):
    code = "file_too_large"


class UploadSessionExpiredError(IngestionDomainError):
    code = "upload_session_expired"


class UploadIncompleteError(IngestionDomainError):
    code = "upload_incomplete"

    def __init__(self, missing_part_numbers: list[int]) -> None:
        self.missing_part_numbers = tuple(missing_part_numbers)
        super().__init__(
            "上传分片不完整",
            details={"missing_part_numbers": list(self.missing_part_numbers)},
        )


class UploadPartConflictError(IngestionDomainError):
    code = "upload_part_conflict"


class MultipartUploadNotFoundError(IngestionDomainError):
    """对象存储中已不存在该 multipart。

    并发完成时，赢家提交后 S3 的 ListParts 会返回 ``NoSuchUpload``。这不是
    存储故障，必须交由完成用例重读会话，按“幂等成功/版本冲突/分片不完整”
    处理，不能映射为 503。
    """

    code = "upload_incomplete"


class UploadSessionNotUploadingError(IngestionDomainError):
    code = "upload_session_not_uploading"


class FileSignatureMismatchError(IngestionDomainError):
    code = "file_signature_mismatch"


class HashMismatchError(IngestionDomainError):
    code = "hash_mismatch"


class SizeMismatchError(IngestionDomainError):
    code = "size_mismatch"

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            "分片总大小与声明大小不一致",
            details={"expected": expected, "actual": actual},
        )


class ObjectStorageUnavailableError(IngestionDomainError):
    code = "object_storage_unavailable"


class ObjectNotFoundError(IngestionDomainError):
    """对象 Key 不存在；属于内容校验结果，不作为可无限重试的存储故障。"""

    code = "object_missing"


class InvalidStateTransitionError(IngestionDomainError):
    code = "state_transition_invalid"

    def __init__(self, entity: str, current: object, action: object) -> None:
        self.entity = entity
        self.current = current
        self.action = action
        super().__init__(f"{entity} 当前状态 {current} 不允许动作 {action}")


class ResourceNotFoundError(IngestionDomainError):
    code = "resource_not_found"

    def __init__(self, resource: str, resource_id: object) -> None:
        self.resource = resource
        self.resource_id = resource_id
        # 不回显资源 ID，避免跨项目存在性泄漏。
        super().__init__(f"{resource}不存在或不可见")


class VersionConflictError(IngestionDomainError):
    code = "version_conflict"

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"{resource}版本冲突")


class ResourceImmutableError(IngestionDomainError):
    code = "resource_immutable"


class ConflictError(IngestionDomainError):
    code = "conflict"


class IdempotencyKeyReusedError(IngestionDomainError):
    code = "idempotency_key_reused"


class JobNotRetryableError(IngestionDomainError):
    code = "job_not_retryable"


class JobTimeoutError(IngestionDomainError):
    code = "job_timeout"


class AssetNotUsableError(IngestionDomainError):
    code = "asset_not_usable"


class RawAssetReferencedByFrozenDatasetError(IngestionDomainError):
    """被冻结数据集版本引用，禁止销毁资产内容（V51-017）。

    为什么放在 ingestion 域而不是新建的 ``retention`` 域：抛出点是
    ``RawAssetUseCases.apply_action``，其 HTTP 语义应由 ingestion 域的异常
    处理器统一裁决（``app/api/errors.py`` 的 ``_INGESTION_STATUS_BY_CODE``
    与 ``with_details=True`` 的 details 透传）。放进新域会得到 422 兜底码
    ``validation_failed``，把"状态冲突"误报成"字段校验失败"。

    为什么 409 而不是 422：请求本身合法（资产存在、动作在状态机里可达），
    拒绝原因是**当前资源状态冲突**——与 ``version_conflict`` /
    ``state_transition_invalid`` 同一语义族。``retryable=false``：重试同样
    会失败，客户端必须改用 ``archive`` 路径或先处理引用它的数据集版本。
    """

    code = "raw_asset_referenced_by_frozen_dataset"
