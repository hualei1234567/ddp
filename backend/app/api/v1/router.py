"""v1 路由聚合。

新模块在此挂载；路由文件只做协议适配，业务逻辑放 application/domain。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import platform

api_router = APIRouter()
api_router.include_router(platform.router)
