"""阶段 5 状态机：显式迁移表；未知状态/动作一律拒绝。"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.modelops.enums import (
    EvaluationStatus,
    FeedbackStatus,
    InferenceServiceStatus,
    InferenceTaskStatus,
    ModelStatus,
    ModelVersionStatus,
    RequirementStatus,
)
from app.domain.modelops.errors import DomainValidationError, InvalidStateTransitionError

_Rules = Mapping[str, Mapping[str, str]]

_MODEL: _Rules = MappingProxyType(
    {
        "DRAFT": {"activate": "ACTIVE"},
        "ACTIVE": {"deprecate": "DEPRECATED", "archive": "ARCHIVED"},
        "DEPRECATED": {"restore": "ACTIVE", "archive": "ARCHIVED"},
        "ARCHIVED": {},
    }
)

_VERSION: _Rules = MappingProxyType(
    {
        "DRAFT": {"validate": "VALIDATING"},
        "VALIDATING": {
            "system-validate-pass": "READY",
            "system-validate-fail": "VALIDATION_FAILED",
        },
        "VALIDATION_FAILED": {"return-draft": "DRAFT"},
        "READY": {"publish": "RELEASED"},
        "RELEASED": {"deprecate": "DEPRECATED", "archive": "ARCHIVED"},
        "DEPRECATED": {"archive": "ARCHIVED"},
        "ARCHIVED": {},
    }
)

_SERVICE: _Rules = MappingProxyType(
    {
        "PENDING": {"deploy": "DEPLOYING"},
        "DEPLOYING": {"activate": "ACTIVE", "system-deploy-fail": "FAILED"},
        "FAILED": {"reset": "PENDING"},
        "ACTIVE": {"stop": "STOPPED"},
        "STOPPED": {"deploy": "DEPLOYING", "retire": "RETIRED"},
        "RETIRED": {},
    }
)

_TASK: _Rules = MappingProxyType(
    {
        "QUEUED": {"system-start": "RUNNING", "cancel": "CANCELLED"},
        "RUNNING": {
            "system-succeed": "SUCCEEDED",
            "system-fail": "FAILED",
            "cancel": "CANCELLED",
        },
        "SUCCEEDED": {},
        "FAILED": {},
        "CANCELLED": {},
    }
)

_EVALUATION: _Rules = MappingProxyType(
    {
        "QUEUED": {"system-start": "RUNNING", "cancel": "CANCELLED"},
        "RUNNING": {
            "system-succeed": "SUCCEEDED",
            "system-fail": "FAILED",
            "cancel": "CANCELLED",
        },
        "FAILED": {},
        "SUCCEEDED": {},
        "CANCELLED": {},
    }
)

_FEEDBACK: _Rules = MappingProxyType(
    {
        "OPEN": {
            "triage": "TRIAGED",
            "dismiss": "DISMISSED",
            "create-requirement": "REQUIREMENT_CREATED",
        },
        "TRIAGED": {
            "dismiss": "DISMISSED",
            "create-requirement": "REQUIREMENT_CREATED",
        },
        "REQUIREMENT_CREATED": {"resolve": "RESOLVED"},
        "RESOLVED": {},
        "DISMISSED": {},
    }
)

_REQUIREMENT: _Rules = MappingProxyType(
    {
        "DRAFT": {"submit": "SUBMITTED", "cancel": "CANCELLED"},
        "SUBMITTED": {
            "approve": "APPROVED",
            "reject": "REJECTED",
            "cancel": "CANCELLED",
        },
        "APPROVED": {
            "plan": "PLANNED",
            "satisfy": "SATISFIED",
            "cancel": "CANCELLED",
        },
        "PLANNED": {"start": "IN_PROGRESS", "cancel": "CANCELLED"},
        "IN_PROGRESS": {"satisfy": "SATISFIED", "cancel": "CANCELLED"},
        "SATISFIED": {"close": "CLOSED"},
        "CLOSED": {},
        "REJECTED": {},
        "CANCELLED": {},
    }
)

_FAMILIES: Mapping[str, _Rules] = MappingProxyType(
    {
        "model": _MODEL,
        "model-version": _VERSION,
        "inference-service": _SERVICE,
        "inference-task": _TASK,
        "evaluation": _EVALUATION,
        "feedback": _FEEDBACK,
        "requirement": _REQUIREMENT,
    }
)


def _family(name: str) -> _Rules:
    rules = _FAMILIES.get(name)
    if rules is None:
        raise DomainValidationError(f"未知状态机 {name}")
    return rules


def target_status(family: str, current: str, action: str) -> str:
    rules = _family(family)
    transitions = rules.get(str(current))
    if transitions is None:
        raise InvalidStateTransitionError(family, current, action)
    target = transitions.get(action)
    if target is None:
        raise InvalidStateTransitionError(family, current, action)
    return target


def allowed_actions(family: str, current: str) -> frozenset[str]:
    transitions = _family(family).get(str(current), {})
    return frozenset(action for action in transitions if not action.startswith("system-"))


def validate_action_name(action: object, *, family: str) -> str:
    if not isinstance(action, str) or action.strip() == "":
        raise DomainValidationError("action 必须是非空字符串")
    value = action.strip()
    known = {name for transitions in _family(family).values() for name in transitions}
    if value not in known:
        raise DomainValidationError("未知状态动作")
    return value


def apply_action(family: str, current: str, action: str) -> str:
    return target_status(family, current, action)


__all__ = [
    "allowed_actions",
    "apply_action",
    "target_status",
    "validate_action_name",
    "EvaluationStatus",
    "FeedbackStatus",
    "InferenceServiceStatus",
    "InferenceTaskStatus",
    "ModelStatus",
    "ModelVersionStatus",
    "RequirementStatus",
]
