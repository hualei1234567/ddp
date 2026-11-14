"""阶段 5 领域异常。领域层不依赖 HTTP。"""

from __future__ import annotations


class ModelOpsDomainError(ValueError):
    code = "validation_failed"


class DomainValidationError(ModelOpsDomainError):
    pass


class IdempotencyKeyReusedError(DomainValidationError):
    """同一幂等键对应不同请求体。"""

    code = "idempotency_key_reused"


class InvalidStateTransitionError(ModelOpsDomainError):
    code = "state_transition_invalid"

    def __init__(self, entity: str, current: object, action: object) -> None:
        self.entity = entity
        self.current = current
        self.action = action
        super().__init__(f"{entity} 当前状态 {current} 不允许动作 {action}")


class ResourceNotFoundError(ModelOpsDomainError):
    code = "resource_not_found"

    def __init__(self, resource: str, resource_id: object) -> None:
        self.resource = resource
        self.resource_id = resource_id
        super().__init__(f"{resource}不存在或不可见")


class VersionConflictError(ModelOpsDomainError):
    code = "version_conflict"


class DuplicateResourceError(ModelOpsDomainError):
    code = "duplicate_resource"


class ModelNotActiveError(ModelOpsDomainError):
    code = "model_not_active"


class ModelVersionImmutableError(ModelOpsDomainError):
    code = "model_version_immutable"


class ModelVersionInUseError(ModelOpsDomainError):
    code = "model_version_in_use"


class ModelReleaseApprovalForbiddenError(ModelOpsDomainError):
    code = "model_release_approval_forbidden"


class ModelArtifactInvalidError(ModelOpsDomainError):
    code = "model_artifact_invalid"


class ModelArtifactChecksumMismatchError(ModelOpsDomainError):
    code = "model_artifact_checksum_mismatch"


class ModelRuntimeInvalidError(ModelOpsDomainError):
    code = "model_runtime_invalid"


class ModelTrainingSourceInvalidError(ModelOpsDomainError):
    code = "model_training_source_invalid"


class EvaluationDatasetNotReadyError(ModelOpsDomainError):
    code = "evaluation_dataset_not_ready"


class EvaluationImmutableError(ModelOpsDomainError):
    code = "evaluation_immutable"


class EvaluationMetricsInvalidError(ModelOpsDomainError):
    code = "evaluation_metrics_invalid"


class EvaluationBaselineInvalidError(ModelOpsDomainError):
    code = "evaluation_baseline_invalid"


class InferenceServiceUnavailableError(ModelOpsDomainError):
    code = "inference_service_unavailable"


class InferenceServiceNotActiveError(ModelOpsDomainError):
    code = "inference_service_not_active"


class InferenceTaskInputInvalidError(ModelOpsDomainError):
    code = "inference_task_input_invalid"


class InferenceTaskResultInvalidError(ModelOpsDomainError):
    code = "inference_task_result_invalid"


class FeedbackDimensionsInvalidError(ModelOpsDomainError):
    code = "feedback_dimensions_invalid"


class FeedbackSourceInvalidError(ModelOpsDomainError):
    code = "feedback_source_invalid"


class FeedbackAlreadyConvertedError(ModelOpsDomainError):
    code = "feedback_already_converted"


class FeedbackTerminalError(ModelOpsDomainError):
    code = "feedback_terminal"


class DataRequirementApprovalForbiddenError(ModelOpsDomainError):
    code = "data_requirement_approval_forbidden"


class DataRequirementNotSatisfiedError(ModelOpsDomainError):
    code = "data_requirement_not_satisfied"


class DataRequirementDatasetInvalidError(ModelOpsDomainError):
    code = "data_requirement_dataset_invalid"


class DataRequirementRetestInvalidError(ModelOpsDomainError):
    code = "data_requirement_retest_invalid"


class DataRequirementTerminalError(ModelOpsDomainError):
    code = "data_requirement_terminal"


class ModelAdapterUnavailableError(ModelOpsDomainError):
    code = "model_adapter_unavailable"


class ModelAdapterUnsupportedError(ModelOpsDomainError):
    code = "model_adapter_unsupported"


class ModelCallbackInvalidError(ModelOpsDomainError):
    code = "model_callback_invalid"


class ModelCallbackReplayedError(ModelOpsDomainError):
    code = "model_callback_replayed"
