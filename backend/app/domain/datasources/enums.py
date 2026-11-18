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


# 客户端视角的终态：这三种状态下不再接受任何**客户端**动作。
#
# 注意 FAILED 在这里，但**不是**系统层面的绝对终态：``attempt < max_attempts``
# 时 Worker 可以走内部 ``retry`` 把它退回 PENDING 重新领取。契约
# ``ImportAction`` 描述的是客户端动作，所以「终态之后任何动作返回 409」
# 对客户端依然成立；系统重试不在客户端的动作集合里。
IMPORT_TERMINAL: frozenset[ImportStatus] = frozenset(
    {ImportStatus.SUCCEEDED, ImportStatus.FAILED, ImportStatus.CANCELLED}
)


class ImportStage(StrEnum):
    """导入执行阶段（可观测性，不参与状态机）。

    ``NULL`` 表示"未测量"：迁移 0055 之前的存量导入没有阶段信息，不能把
    "没记录"伪装成"已完成 0%"。新导入从 ``DOWNLOAD`` 开始写真实阶段。
    """

    DOWNLOAD = "DOWNLOAD"
    VALIDATE = "VALIDATE"
    PARSE = "PARSE"
    INGEST = "INGEST"
    COMPLETE = "COMPLETE"


class ImportLogLevel(StrEnum):
    """步骤日志等级。只用于运行期排障，不是业务状态。"""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
