"""训练需求、交付、外部引用与跨项目授权错误。

全部继承 :class:`DatasetDomainError`，由既有数据集异常处理器统一映射为
契约错误码；领域层不依赖 HTTP。
"""

from __future__ import annotations

from app.domain.datasets.errors import DatasetDomainError


class TrainingRequestIncompleteError(DatasetDomainError):
    code = "training_request_incomplete"


class TrainingRequestImmutableError(DatasetDomainError):
    code = "training_request_immutable"


class TrainingRequestApprovalForbiddenError(DatasetDomainError):
    code = "training_request_approval_forbidden"


class TrainingRequestRevisionConflictError(DatasetDomainError):
    code = "training_request_revision_conflict"


class TrainingRequestNotApprovedError(DatasetDomainError):
    code = "training_request_not_approved"


class TrainingDeliveryNotReadyError(DatasetDomainError):
    code = "training_delivery_not_ready"


class TrainingDeliveryAcceptanceForbiddenError(DatasetDomainError):
    code = "training_delivery_acceptance_forbidden"


class TrainingDeliveryAlreadyAcceptedError(DatasetDomainError):
    code = "training_delivery_already_accepted"


class TrainingDeliveryMismatchError(DatasetDomainError):
    code = "training_delivery_mismatch"


class TrainingDeliveryRequirementUnmetError(DatasetDomainError):
    """需求快照与数据集版本核对不通过（TRN-002）。

    ``details`` 携带逐维度结论，便于交付方直接看出"差在哪个维度"；只含维度名、
    期望值与版本实际值，均为请求方本已可见的对象属性。
    """

    code = "training_delivery_requirement_unmet"


class TrainingAdapterUnavailableError(DatasetDomainError):
    code = "training_adapter_unavailable"


class TrainingAdapterUnsupportedError(DatasetDomainError):
    code = "training_adapter_unsupported"


class TrainingExternalRunConflictError(DatasetDomainError):
    code = "training_external_run_conflict"


class TrainingCallbackInvalidError(DatasetDomainError):
    code = "training_callback_invalid"


class TrainingCallbackReplayedError(DatasetDomainError):
    code = "training_callback_replayed"


class DatasetGrantForbiddenError(DatasetDomainError):
    code = "dataset_grant_forbidden"


class DatasetGrantExpiredError(DatasetDomainError):
    code = "dataset_grant_expired"


class DatasetGrantRevokedError(DatasetDomainError):
    code = "dataset_grant_revoked"
