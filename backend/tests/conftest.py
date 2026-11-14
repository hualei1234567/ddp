"""测试环境基线。

测试必须在 ``local`` 环境运行：应用启动自检会拒绝非本地环境。
这里在导入 app 之前显式设置，避免受开发机或 CI 的 DDP_ENV 影响。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["DDP_ENV"] = "local"

# data-platform 是独立的纯数据能力包（不装进 backend 的 venv），
# 测试时把仓库根的 data-platform 加入导入路径即可。
_DATA_PLATFORM = Path(__file__).resolve().parents[2] / "data-platform"
if _DATA_PLATFORM.is_dir() and str(_DATA_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_DATA_PLATFORM))
# 本地 Header 认证默认关闭；需要它的用例在自己的 Settings 中显式开启。
os.environ.pop("DDP_LOCAL_HEADER_AUTH", None)
