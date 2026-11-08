"""标注路由共享类型与请求辅助。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Path, Request

from app.api.deps import get_project_scope, get_write_scope
from app.api.v1.preconditions import require_expected_version
from app.domain.identity.models import ProjectScope

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]
ActionName = Annotated[str, Path(min_length=1, max_length=64)]


def request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None
