"""平台元信息接口。

``phase`` 与 ``modules`` 来自配置（``DDP_PLATFORM_PHASE`` / ``DDP_PLATFORM_MODULES``），
不在此处硬编码：阶段推进时只改配置，避免对外元信息与实现脱节。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(prefix="/platform", tags=["platform"])


@router.get("/meta")
def platform_meta() -> dict[str, object]:
    settings = get_settings()
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "env": settings.env,
        "phase": settings.platform_phase,
        "modules": list(settings.platform_modules),
    }
