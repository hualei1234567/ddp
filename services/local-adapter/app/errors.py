"""Adapter 统一错误类型。

所有可预期的失败都带稳定的 ``code``，回调里原样上报，便于 DDP 与运维定位；
未预期异常由任务层收敛为 ``adapter_internal_error``，不把堆栈写进回调。
"""

from __future__ import annotations


class AdapterError(RuntimeError):
    def __init__(self, code: str, summary: str) -> None:
        super().__init__(summary)
        self.code = code
        self.summary = summary


class ConfigurationError(AdapterError):
    def __init__(self, summary: str) -> None:
        super().__init__("adapter_configuration_invalid", summary)


class StorageError(AdapterError):
    pass


class DatasetError(AdapterError):
    pass


class BlockedError(AdapterError):
    """数据形态不足以执行；不得伪造成功结果。"""


class JobCancelledError(AdapterError):
    def __init__(self, summary: str = "任务已取消") -> None:
        super().__init__("job_cancelled", summary)


class JobTimedOutError(AdapterError):
    def __init__(self, summary: str = "任务超过允许时长") -> None:
        super().__init__("run_timeout", summary)


class ModelArtifactError(AdapterError):
    pass


class FrameExtractionError(AdapterError):
    pass


__all__ = [
    "AdapterError",
    "BlockedError",
    "ConfigurationError",
    "DatasetError",
    "FrameExtractionError",
    "JobCancelledError",
    "JobTimedOutError",
    "ModelArtifactError",
    "StorageError",
]
