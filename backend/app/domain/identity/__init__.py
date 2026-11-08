"""身份与项目权限领域。"""

from app.domain.identity.errors import CrossProjectAccessError
from app.domain.identity.models import (
    ROLE_PERMISSIONS,
    PlatformRole,
    Principal,
    ProjectAction,
    ProjectContext,
    ProjectRole,
    ProjectScope,
)

__all__ = [
    "ROLE_PERMISSIONS",
    "CrossProjectAccessError",
    "PlatformRole",
    "Principal",
    "ProjectAction",
    "ProjectContext",
    "ProjectRole",
    "ProjectScope",
]
