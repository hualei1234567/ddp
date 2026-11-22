"""处理运行 HTTP 请求模型。

入参 ``extra="forbid"``：客户端不能提交 ``project_id`` / 状态 / 版本等服务端
权威字段。``If-Match`` 通过请求头传递，不在 body 里。

``raw_asset_id`` 与 ``input_refs`` 二选一（V31-015）：前者让客户端只给资产 ID，
对象存储 key 由服务端从资产记录解析；后者保留给非 RawAsset 的裸引用。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.processing.models import MAX_REFS, PIPELINE_VERSION_MAX_LENGTH, REF_MAX_LENGTH

Ref = Annotated[str, Field(min_length=1, max_length=REF_MAX_LENGTH)]


class ProcessingRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_version: str = Field(min_length=1, max_length=PIPELINE_VERSION_MAX_LENGTH)
    input_refs: list[Ref] = Field(default_factory=list, max_length=MAX_REFS)
    raw_asset_id: UUID | None = None

    @model_validator(mode="after")
    def _exactly_one_input_source(self) -> ProcessingRunCreate:
        """二选一：同时给或都不给都是调用方错误。

        静默取其一会让调用方以为另一个生效了——例如同时给了资产 ID 与裸引用，
        却只用了裸引用，资产状态校验就被悄悄跳过。
        """
        has_refs = len(self.input_refs) > 0
        if self.raw_asset_id is not None and has_refs:
            raise ValueError("raw_asset_id 与 input_refs 只能二选一")
        if self.raw_asset_id is None and not has_refs:
            raise ValueError("必须提供 raw_asset_id 或 input_refs")
        return self


__all__ = ["ProcessingRunCreate"]
