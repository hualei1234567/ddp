"""测试环境基线。

测试必须在 ``local`` 环境运行：应用启动自检会拒绝非本地环境。
这里在导入 app 之前显式设置，避免受开发机或 CI 的 DDP_ENV 影响。
"""

from __future__ import annotations

import os

os.environ["DDP_ENV"] = "local"
# 本地 Header 认证默认关闭；需要它的用例在自己的 Settings 中显式开启。
os.environ.pop("DDP_LOCAL_HEADER_AUTH", None)
