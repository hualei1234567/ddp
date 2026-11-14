"""数据源域枚举。

取值与 ``packages/contracts`` 中的 schema 一一对应；新增取值必须先改契约。
"""

from __future__ import annotations

from enum import StrEnum


class DataSourceKind(StrEnum):
    """数据源类型。当前只支持 Hugging Face，后续 Adapter 在此扩展。"""

    HUGGINGFACE = "HUGGINGFACE"


class AccessCondition(StrEnum):
    """访问条件。**访问成功不等于拥有商业使用权**（`docs/ddp/07` §2）。"""

    PUBLIC = "PUBLIC"
    GATED = "GATED"
    PRIVATE = "PRIVATE"
    UNKNOWN = "UNKNOWN"


class ImportScope(StrEnum):
    """导入范围。默认只做小样本，不默认全量下载。"""

    SAMPLE = "SAMPLE"
    SPLIT = "SPLIT"
    FULL = "FULL"


class ImportStatus(StrEnum):
    """一次导入任务的状态。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


IMPORT_TERMINAL: frozenset[ImportStatus] = frozenset(
    {ImportStatus.SUCCEEDED, ImportStatus.FAILED, ImportStatus.CANCELLED}
)
