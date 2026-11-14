"""数据源域：Hugging Face 等外部数据集的登记与治理。

只表达"我们登记了哪个数据源、在什么许可与访问条件下、导入了多少"，
不承载解析与处理——那些属于 ``data-platform/`` 的纯数据能力。
"""

from app.domain.datasources.errors import (
    DataSourceDomainError,
    DomainValidationError,
    DuplicateResourceError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.datasources.models import DataSource, DataSourceImport
from app.domain.datasources.state_machines import (
    allowed_import_actions,
    import_target_status,
)

__all__ = [
    "DataSource",
    "DataSourceDomainError",
    "DataSourceImport",
    "DomainValidationError",
    "DuplicateResourceError",
    "InvalidStateTransitionError",
    "ResourceNotFoundError",
    "VersionConflictError",
    "allowed_import_actions",
    "import_target_status",
]
