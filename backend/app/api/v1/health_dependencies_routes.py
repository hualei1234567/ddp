"""依赖健康检查端点（V52-036）：``GET /api/v1/health/dependencies``。

给负载均衡 / 编排系统 / 值班看板一个可自动消费的依赖判定。当前依赖只有数据库
（``SELECT 1``）；新增依赖时在 ``checks`` 下增加同构的检查项，**不新建端点**，
否则调用方要为每个依赖各写一条探活规则。

## 为什么 HTTP 状态码恒为 200

依赖故障意味着"服务进程还在、下游不可用"，此时返回 5xx 会让 LB 把本副本摘掉，
故障从"降级"放大成"整服务不可用"。判定放在响应体
``status`` / ``checks.<dependency>.status`` 里，调用方（告警规则、看板）按需消费；
409/503 留给真正的进程健康检查（``/health``）。

## 为什么不回显异常原文

同 ``/health`` 与全局错误处理器：对外响应体绝不出现 ``str(exc)``——数据库探活
异常常含连接串、主机名与 SQL。``detail`` 是固定文案，原文与堆栈进服务端日志
（``ddp.health`` logger）。排障看日志，不看探活响应。

## 为什么不在 OpenAPI 契约里

这是运维探活端点，与 ``/metrics`` 同类：不含业务数据，调用方是 LB/编排而不是
前端。契约（``packages/contracts``）变更需 System Architect 评审，本次不改，
因此 ``include_in_schema=False``——与 ``app/api/observability.py`` 的已知契约缺口
同处理；正式纳入契约时删掉该参数并补 fragment 即可。
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.infrastructure.db.session import get_session_factory

logger = logging.getLogger("ddp.health")

router = APIRouter(prefix="/health", tags=["platform"])

_OK = "ok"
_DEGRADED = "degraded"


class DatabaseCheck(BaseModel):
    """数据库依赖检查结果；``latency_ms`` 是本次探活耗时（含连接建立）。"""

    status: Literal["ok", "degraded"]
    latency_ms: int
    detail: str


class DependencyChecks(BaseModel):
    """全部依赖的检查结果；键名即依赖名，新增依赖只增字段。"""

    database: DatabaseCheck


class DependencyHealthResponse(BaseModel):
    """``/health/dependencies`` 的固定结构；``status`` 由各依赖状态聚合。"""

    status: Literal["ok", "degraded"]
    checks: DependencyChecks


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _probe_database() -> DatabaseCheck:
    """执行 ``SELECT 1``；任何异常都折叠为 ``degraded``，不向上抛。

    会话在这里自建自关（而不是走 ``Depends(get_session)``）：依赖装配本身
    （引擎/连接池创建）也可能失败，若放在依赖注入阶段抛异常，框架会直接返回
    500，违背"绝不 500"的承诺。
    """
    started = time.perf_counter()
    session: Session | None = None
    try:
        session = get_session_factory()()
        session.execute(text("SELECT 1"))
    except Exception:
        # 不是吞异常：原文与堆栈落日志，响应体只给固定文案，且状态如实降级。
        logger.warning("依赖健康检查：数据库探活失败", exc_info=True)
        status: Literal["ok", "degraded"] = _DEGRADED
        detail = "SELECT 1 失败（详见服务端日志）"
    else:
        status = _OK
        detail = "SELECT 1 成功"
    finally:
        if session is not None:
            _close_quietly(session)
    return DatabaseCheck(status=status, latency_ms=_elapsed_ms(started), detail=detail)


def _close_quietly(session: Session) -> None:
    """关闭探活会话；关闭失败同样只降级记录，不能让探活变 500。"""
    try:
        session.close()
    except Exception:
        logger.warning("依赖健康检查：探活会话关闭失败", exc_info=True)


@router.get(
    "/dependencies",
    response_model=DependencyHealthResponse,
    include_in_schema=False,
)
def dependencies_health() -> DependencyHealthResponse:
    database = _probe_database()
    checks = DependencyChecks(database=database)
    overall: Literal["ok", "degraded"] = _OK if database.status == _OK else _DEGRADED
    return DependencyHealthResponse(status=overall, checks=checks)
