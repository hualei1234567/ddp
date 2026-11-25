"""DDP 纯数据能力包。

不依赖 FastAPI / SQLAlchemy / 业务领域模型；可单独测试与复用。
"""

from ddp_data.capability import (
    BUILTIN_FORMAT_CAPABILITY_MATRIX,
    CapabilityValidationError,
    FormatCapabilityLevel,
    FormatCapabilityProfile,
)

__all__ = [
    "BUILTIN_FORMAT_CAPABILITY_MATRIX",
    "CapabilityValidationError",
    "FormatCapabilityLevel",
    "FormatCapabilityProfile",
    "__version__",
]

__version__ = "0.1.0"
