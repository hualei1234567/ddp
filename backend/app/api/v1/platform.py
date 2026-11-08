"""平台元信息接口。"""

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
        "phase": "0",
        "modules": ["platform"],
    }
