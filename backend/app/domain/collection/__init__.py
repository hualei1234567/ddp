"""采集任务与批次领域模型。"""

from app.domain.collection.asset_state_machines import (
    ASSET_TERMINAL,
    allowed_asset_actions,
    assert_asset_action,
)
from app.domain.collection.enums import (
    CollectionAssetAction,
    CollectionAssetStatus,
    CollectionBatchAction,
    CollectionBatchStatus,
    CollectionTaskAction,
    CollectionTaskStatus,
    TaskPriority,
)
from app.domain.collection.errors import (
    CollectionDomainError,
    ConflictError,
    DomainValidationError,
    DuplicateResourceError,
    ForbiddenOperationError,
    IdempotencyKeyReusedError,
    InvalidStateTransitionError,
    ResourceImmutableError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.collection.models import CollectionBatch, CollectionTask
from app.domain.collection.state_machines import (
    BATCH_TERMINAL,
    TASK_TERMINAL,
    allowed_batch_actions,
    allowed_task_actions,
    assert_batch_action,
    assert_task_action,
)

__all__ = [
    "ASSET_TERMINAL",
    "BATCH_TERMINAL",
    "TASK_TERMINAL",
    "CollectionAssetAction",
    "CollectionAssetStatus",
    "CollectionBatch",
    "CollectionBatchAction",
    "CollectionBatchStatus",
    "CollectionDomainError",
    "CollectionTask",
    "CollectionTaskAction",
    "CollectionTaskStatus",
    "ConflictError",
    "DomainValidationError",
    "DuplicateResourceError",
    "ForbiddenOperationError",
    "IdempotencyKeyReusedError",
    "InvalidStateTransitionError",
    "ResourceImmutableError",
    "ResourceNotFoundError",
    "TaskPriority",
    "VersionConflictError",
    "allowed_asset_actions",
    "allowed_batch_actions",
    "allowed_task_actions",
    "assert_asset_action",
    "assert_batch_action",
    "assert_task_action",
]
