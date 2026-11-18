"""处理运行 HTTP 请求模型。

入参 ``extra="forbid"``：客户端不能提交 ``project_id`` / 状态 / 版本等服务端
权威字段。``If-Match`` 通过请求头传递，不在 body 里。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.domain.processing.models import MAX_REFS, PIPELINE_VERSION_MAX_LENGTH, REF_MAX_LENGTH

Ref = Annotated[str, Field(min_length=1, max_length=REF_MAX_LENGTH)]


class ProcessingRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_version: str = Field(min_length=1, max_length=PIPELINE_VERSION_MAX_LENGTH)
    input_refs: list[Ref] = Field(min_length=1, max_length=MAX_REFS)


__all__ = ["ProcessingRunCreate"]
