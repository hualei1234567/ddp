"""平台元信息接口。

``phase`` 与 ``modules`` 来自配置（``DDP_PLATFORM_PHASE`` / ``DDP_PLATFORM_MODULES``），
不在此处硬编码：阶段推进时只改配置，避免对外元信息与实现脱节。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.platform_response_models import PlatformMetaOut
from app.core.config import get_settings

router = APIRouter(prefix="/platform", tags=["platform"])


@router.get("/meta", response_model=PlatformMetaOut)
def platform_meta() -> PlatformMetaOut:
    settings = get_settings()
    return PlatformMetaOut(
        name=settings.app_name,
        version=settings.app_version,
        env=settings.env,
        phase=settings.platform_phase,
        modules=list(settings.platform_modules),
    )
