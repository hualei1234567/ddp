"""写操作 → 受约束资源类型的**唯一权威映射**。

为什么必须集中在一处
--------------------
"创建 Episode 要不要检查 EPISODES 配额"如果散落在各域用例里，新增一条写路径
时极容易漏检；而漏检的表现是"配额看起来配了，实际拦不住"——比没配更糟。
本模块把映射声明成不可变表，应用层只按操作名查表，路由层只引用操作名。

为什么放在领域层
----------------
这是业务规则（哪种写入消耗哪种资源），不是 HTTP 适配，也不是数据库细节。
领域层只依赖枚举，不依赖 FastAPI / SQLAlchemy。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.domain.governance.enums import ResourceKind
from app.domain.governance.errors import DomainValidationError

# 操作名常量：调用点必须引用常量，禁止在各路由里手写字符串。
# 刻意不再维护第二个操作名枚举：取值集合由 OPERATION_RESOURCE_KIND 一处定义。
EPISODE_CREATE = "episode.create"
DATASET_VERSION_CREATE = "dataset.version.create"
INGESTION_JOB_CREATE = "ingestion_job.create"
INFERENCE_TASK_CREATE = "inference_task.create"
OBJECT_STORAGE_UPLOAD = "object_storage.upload"

OPERATION_RESOURCE_KIND: Mapping[str, ResourceKind] = MappingProxyType(
    {
        # 一个 Episode 记录消耗一个 Episode 名额。
        EPISODE_CREATE: ResourceKind.EPISODES,
        # 一个 DatasetVersion 消耗一个版本名额。
        DATASET_VERSION_CREATE: ResourceKind.DATASET_VERSIONS,
        # 一次接入任务消耗一个任务名额（重试不新建任务，见接入用例）。
        INGESTION_JOB_CREATE: ResourceKind.INGESTION_JOBS,
        # 一次推理任务消耗一个任务名额。
        INFERENCE_TASK_CREATE: ResourceKind.INFERENCE_TASKS,
        # 新建上传会话按**声明字节数**预扣对象存储配额；完成上传后
        # raw_assets.size_bytes 成为权威用量。
        OBJECT_STORAGE_UPLOAD: ResourceKind.OBJECT_STORAGE_BYTES,
    }
)

# 以字节为计量的资源类型：判定时请求量是"本次声明字节数"，不是 1。
BYTE_BASED_RESOURCE_KINDS = frozenset({ResourceKind.OBJECT_STORAGE_BYTES})


def resource_kind_for(operation: str) -> ResourceKind:
    """按写操作取受约束资源类型；未登记的操作直接拒绝。

    刻意不做"未知操作默认放行"或"默认按某个资源扣"：两者都会让新写路径
    静默逃过配额。未登记说明调用方漏了登记，应在开发期暴露。
    """
    try:
        return OPERATION_RESOURCE_KIND[operation]
    except KeyError as exc:
        raise DomainValidationError("写操作未登记到配额约束表") from exc


def is_byte_based(resource_kind: ResourceKind) -> bool:
    return resource_kind in BYTE_BASED_RESOURCE_KINDS


__all__ = [
    "BYTE_BASED_RESOURCE_KINDS",
    "DATASET_VERSION_CREATE",
    "EPISODE_CREATE",
    "INFERENCE_TASK_CREATE",
    "INGESTION_JOB_CREATE",
    "OBJECT_STORAGE_UPLOAD",
    "OPERATION_RESOURCE_KIND",
    "is_byte_based",
    "resource_kind_for",
]
