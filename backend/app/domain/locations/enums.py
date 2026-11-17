"""场地的类型枚举（N9 多基地）。

为什么需要 ``kind``：一个"基地"与基地内的"实验室/房间"在采集安排上不是同一
层级的东西——地址、进出门禁、可排期时段只对实体场地成立。用自由文本
（``name``）表达会导致"北京基地/北京基地-3楼/BJ-3F"三种写法混用，下游无法
按层级汇总。取值刻意只保留三个业务上确实存在、且当前有消费方（列表过滤）的
类型，不预留 ``WAREHOUSE`` / ``OUTDOOR`` 之类尚无需求的取值。

刻意**不含** ``status`` 枚举：本阶段没有任何读取方按"启用/停用"过滤场地
（采集任务、设备、机器人都只校验"存在且同项目"）。加一个无人使用、也无人
能改的状态列，只会重复 COL-002 素材 ``ACTIVE``/``ARCHIVED`` 至今无法到达的
那种债。场地停用需求出现时，再连同状态动作与过滤一起加。
"""

from __future__ import annotations

from enum import StrEnum


class LocationKind(StrEnum):
    """场地的物理层级；默认 ``SITE``（一个独立基地/园区）。"""

    SITE = "SITE"
    ROOM = "ROOM"
    LAB = "LAB"


DEFAULT_LOCATION_KIND = LocationKind.SITE

__all__ = ["DEFAULT_LOCATION_KIND", "LocationKind"]
