"""供应商与到货验收领域异常。领域层不依赖 HTTP。"""

from __future__ import annotations


class SupplierDomainError(ValueError):
    """供应商域错误基类；``code`` 由 API 层映射为 HTTP 语义。"""

    code = "validation_failed"


class DomainValidationError(SupplierDomainError):
    pass


class InvalidStateTransitionError(SupplierDomainError):
    code = "state_transition_invalid"

    def __init__(self, entity: str, current: object, action: object) -> None:
        self.entity = entity
        self.current = current
        self.action = action
        super().__init__(f"{entity} 当前状态 {current} 不允许动作 {action}")


class ResourceNotFoundError(SupplierDomainError):
    code = "resource_not_found"


class VersionConflictError(SupplierDomainError):
    code = "version_conflict"


class DuplicateResourceError(SupplierDomainError):
    code = "duplicate_resource"


class SupplierNotActiveError(SupplierDomainError):
    code = "supplier_not_active"


class SupplierTerminalError(SupplierDomainError):
    code = "supplier_terminal"


class BatchNotScreeningError(SupplierDomainError):
    code = "batch_not_screening"


class BatchReviewIncompleteError(SupplierDomainError):
    code = "batch_review_incomplete"


class BatchAlreadySettledError(SupplierDomainError):
    code = "batch_already_settled"


class SegmentTerminalError(SupplierDomainError):
    code = "segment_terminal"


class SegmentDecisionForbiddenError(SupplierDomainError):
    code = "segment_decision_forbidden"


class SegmentDecisionReasonRequiredError(SupplierDomainError):
    code = "decision_reason_required"


class SegmentRawAssetMissingError(SupplierDomainError):
    code = "segment_raw_asset_missing"


class LedgerImmutableError(SupplierDomainError):
    code = "ledger_immutable"
