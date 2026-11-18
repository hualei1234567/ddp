"""受管 ``TestClient``：全测试套件共用同一份生命周期登记。

Starlette 的 ``TestClient`` 在 ``__enter__`` 里创建 anyio 内存流与 portal；
不进入上下文时这些资源只能等 GC 收尾，偶发在**别的**用例 setup 阶段抛
``PytestUnraisableExceptionWarning``（被 ``filterwarnings=error`` 判失败）。

登记与关闭放在根 conftest 的 autouse 夹具里，覆盖 ``tests/api`` 之外的
worker / e2e 用例——只放在 ``tests/api/conftest.py`` 会让跨目录使用
``make_client`` 的客户端无人关闭，问题依旧会复现。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

_OPEN_CLIENTS: list[TestClient] = []


def managed_client(app: object) -> TestClient:
    """进入客户端上下文并登记，由根 autouse 夹具在用例结束后关闭。"""
    client = TestClient(app)  # type: ignore[arg-type]
    client.__enter__()
    _OPEN_CLIENTS.append(client)
    return client


def close_open_clients() -> None:
    """按登记顺序关闭全部受管客户端；重复调用安全。"""
    while _OPEN_CLIENTS:
        _OPEN_CLIENTS.pop().__exit__(None, None, None)


__all__ = ["close_open_clients", "managed_client"]
