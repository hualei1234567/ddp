"""采集任务与批次领域模型。"""

from app.domain.collection.enums import (
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
    "BATCH_TERMINAL",
    "TASK_TERMINAL",
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
    "allowed_batch_actions",
    "allowed_task_actions",
    "assert_batch_action",
    "assert_task_action",
]
