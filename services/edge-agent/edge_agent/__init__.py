"""DDP Edge Agent 本地核心库。

只实现与服务端 API 无关的本地能力；与服务端的交互全部经
``edge_agent.upload.gateway.ServerGateway`` 等 Protocol 抽象。
子包的 ``__init__`` 刻意保持为空，避免循环导入。
"""

__version__ = "0.1.0"
