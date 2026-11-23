"""Edge Agent 域异常。

``code`` 全部取自既有契约错误码（``not_authenticated`` / ``forbidden`` /
``resource_not_found`` / ``state_transition_invalid`` / ``conflict`` /
``validation_failed``），API 层只需新增一张查表，不引入新错误码。
异常消息不含任何凭据或客户端输入。
"""

from __future__ import annotations


class EdgeAgentDomainError(ValueError):
    """Edge Agent 域可预期错误的基类。"""

    code = "validation_failed"


class DomainValidationError(EdgeAgentDomainError):
    """字段或跨字段校验失败。"""


class ResourceNotFoundError(EdgeAgentDomainError):
    code = "resource_not_found"

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"{resource}不存在或不可见")


class AgentAuthenticationError(EdgeAgentDomainError):
    """Agent token / 配对凭证无效（含过期、已使用、已撤销）。

    对外统一措辞：不区分原因，避免成为凭据状态的探测口。
    """

    code = "not_authenticated"


class AgentScopeError(EdgeAgentDomainError):
    """合法 Agent token 访问了不属于自己的资源。"""

    code = "forbidden"


class StateTransitionError(EdgeAgentDomainError):
    """当前状态不允许该操作（撤销终态、Lease 已过期/已释放等）。"""

    code = "state_transition_invalid"


class LeaseConflictError(EdgeAgentDomainError):
    """同一 CaptureSession 已有其他 Agent 持有 active Lease。"""

    code = "conflict"


__all__ = [
    "AgentAuthenticationError",
    "AgentScopeError",
    "DomainValidationError",
    "EdgeAgentDomainError",
    "LeaseConflictError",
    "ResourceNotFoundError",
    "StateTransitionError",
]
