"""Episode/切片/血缘领域异常。

领域层不依赖 HTTP；API 层负责稳定 ``code`` 到状态码的映射。
跨项目资源统一使用 ``resource_not_found``，不泄漏存在性。
"""

from __future__ import annotations

from collections.abc import Mapping


class EpisodeDomainError(ValueError):
    code = "validation_failed"

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.details: Mapping[str, object] = dict(details or {})


class DomainValidationError(EpisodeDomainError):
    pass


class InvalidStateTransitionError(EpisodeDomainError):
    code = "state_transition_invalid"

    def __init__(self, entity: str, current: object, action: object) -> None:
        self.entity = entity
        self.current = current
        self.action = action
        super().__init__(f"{entity} 当前状态 {current} 不允许动作 {action}")


class ResourceImmutableError(EpisodeDomainError):
    code = "resource_immutable"


class ResourceNotFoundError(EpisodeDomainError):
    code = "resource_not_found"

    def __init__(self, resource: str, resource_id: object) -> None:
        self.resource = resource
        self.resource_id = resource_id
        super().__init__(f"{resource}不存在或不可见")


class VersionConflictError(EpisodeDomainError):
    code = "version_conflict"

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"{resource}版本冲突")


class EpisodeOverlapConflictError(EpisodeDomainError):
    code = "episode_overlap_conflict"


class ManifestHashMismatchError(EpisodeDomainError):
    code = "manifest_hash_mismatch"


class ManifestSchemaInvalidError(EpisodeDomainError):
    code = "manifest_schema_invalid"


class CalibrationNotActiveError(EpisodeDomainError):
    code = "calibration_not_active"


class TimeBaseInvalidError(EpisodeDomainError):
    code = "episode_time_base_invalid"


class LineageConflictError(EpisodeDomainError):
    code = "lineage_conflict"


class ConflictError(EpisodeDomainError):
    code = "conflict"
