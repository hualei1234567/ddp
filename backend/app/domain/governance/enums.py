"""经营治理的稳定枚举（N9 规模化运营）。

只放"有真实消费方"的取值：

- :class:`ResourceKind` 的每一项都对应一条可计量、可设上限的核心资源，
  并由 ``policy.py`` 的权威映射绑定到具体写操作；
- :class:`QuotaStatus` 是报表对外呈现的**唯一**状态口径，避免调用方各自
  用 ``used >= limit`` 重新推断（重推断必然分叉出 ``==`` 与 ``>`` 的差异）。

刻意不预留 ``CPU_SECONDS`` / ``BANDWIDTH_BYTES`` 之类当前没有权威取数来源的
资源类型。加一个测不出来的配额，只会让报表永远显示 ``UNMEASURED``。
"""

from __future__ import annotations

from enum import StrEnum


class ResourceKind(StrEnum):
    """可计量的项目级核心资源。"""

    # 对象存储占用字节数（来源：raw_assets.size_bytes 的权威落库值）。
    OBJECT_STORAGE_BYTES = "OBJECT_STORAGE_BYTES"
    # Episode 记录数（来源：episodes 表真实计数）。
    EPISODES = "EPISODES"
    # DatasetVersion 记录数（来源：dataset_versions 表真实计数）。
    DATASET_VERSIONS = "DATASET_VERSIONS"
    # 接入任务数（来源：ingestion_jobs 表真实计数）。
    INGESTION_JOBS = "INGESTION_JOBS"
    # 推理任务数（来源：inference_tasks 表真实计数）。
    INFERENCE_TASKS = "INFERENCE_TASKS"


# 报表固定顺序：枚举声明顺序即对外条目顺序，避免"某项目少配一个配额就少一行"。
RESOURCE_KIND_ORDER: tuple[ResourceKind, ...] = tuple(ResourceKind)


class QuotaStatus(StrEnum):
    """单条资源用量相对配额的状态。

    ``UNMEASURED`` 是本模块的"三态原则"落点：没有可信统计时它是
    ``UNMEASURED``，**不是** ``OK``，更不是用 ``used=0`` 冒充。
    """

    UNMEASURED = "UNMEASURED"
    UNLIMITED = "UNLIMITED"
    OK = "OK"
    AT_LIMIT = "AT_LIMIT"
    OVER_LIMIT = "OVER_LIMIT"


__all__ = ["RESOURCE_KIND_ORDER", "QuotaStatus", "ResourceKind"]
