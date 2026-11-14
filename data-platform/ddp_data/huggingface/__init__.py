"""Hugging Face 数据源 Adapter。

只做"读元信息并给出可直接入库的规范化结果"，不做下载、不做解析。
设计目标是**可注入**：测试用 fake 客户端，生产用 ``huggingface_hub``。
"""

from ddp_data.huggingface.adapter import (
    DatasetCardInfo,
    HfAdapterError,
    HfDatasetRef,
    HuggingFaceAdapter,
    HfRepoClient,
    parse_repo_id,
    resolve_license,
)

__all__ = [
    "DatasetCardInfo",
    "HfAdapterError",
    "HfDatasetRef",
    "HfRepoClient",
    "HuggingFaceAdapter",
    "parse_repo_id",
    "resolve_license",
]
