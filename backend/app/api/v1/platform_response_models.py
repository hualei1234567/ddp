"""平台元信息与健康检查响应模型。

两者都不属于业务域，但都必须有显式响应模型：没有 ``response_model`` 时
FastAPI 导出的是"任意对象"，客户端无法据此生成类型，契约比对也只能判定
为 ``untyped``。字段集合与 ``packages/contracts/openapi.yaml`` 的
``HealthResponse`` / ``PlatformMeta`` 一致。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """``/health`` 的固定结构；``status`` 只有 ``ok`` 一个取值。"""

    status: Literal["ok"]
    service: str
    version: str
    env: str


class PlatformMetaOut(BaseModel):
    """平台自述信息；``phase`` / ``modules`` 来自配置，不在此硬编码。"""

    name: str
    version: str
    env: str
    phase: str
    modules: list[str]


__all__ = ["HealthResponse", "PlatformMetaOut"]
