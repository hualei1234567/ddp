"""场地资源计数的只读 SQL 适配器（N9-005）。

为什么单独一个文件，而不是塞进 ``repositories/locations.py``
-------------------------------------------------------------
本适配器读的是 ``devices`` / ``sensors`` / ``collection_tasks`` 三张**别域**的
表，只做 ``COUNT(*) GROUP BY location_id``，不写任何一行、不构造别域实体。
把它放在 ``locations.py`` 会让人误以为场地仓储拥有这些表的读写权；单独成
模块可以把边界写清楚：这是一个**只读投影**，供多基地概览视图消费。

为什么分成三条 GROUP BY，而不是一条多表 LEFT JOIN
------------------------------------------------
一条 JOIN 会把设备行与任务行做笛卡尔积，``COUNT(*)`` 直接翻倍。三次聚合
各自返回 ``location_id -> count``，再在内存里按场地合并——查询条数固定为 3，
且每条都只扫本项目的、本页场地（≤100 个）的行。

项目隔离：三条查询都强制 ``project_id = scope.project_id``；``scope`` 由服务端
从成员资格解析，客户端传入的 ``project_id`` 不参与构造。传感器通过
``device_id`` 归属设备，因此只统计"挂在该场地设备上"的传感器；仅挂在机器人
（``robot_id``）上的传感器无法归因到场地，不计入任何基地。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.domain.locations.models import LocationResourceCounts
from app.infrastructure.db.models.collection import CollectionTask
from app.infrastructure.db.models.devices import Device, Sensor


class SqlSiteResourceSummaryRepository:
    """按场地聚合设备/传感器/采集任务数；只读，无写路径。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def count_by_location(
        self,
        scope: ProjectScope,
        *,
        location_ids: Sequence[UUID],
    ) -> Mapping[UUID, LocationResourceCounts]:
        # 去重且保序：同一页不会出现重复场地，但调用方传参不应导致 IN 列表膨胀。
        ids = list(dict.fromkeys(location_ids))
        if not ids:
            return {}

        device_counts = self._device_counts(scope, ids)
        sensor_counts = self._sensor_counts(scope, ids)
        task_counts = self._task_counts(scope, ids)

        # 只为真正有资源的场地建条目；没有资源的场地由用例回落到全 0，
        # 从而避免把"查询结果缺行"误当成"该场地资源为 0"的隐性约定。
        location_ids_with_rows = set(device_counts) | set(sensor_counts) | set(task_counts)
        return {
            location_id: LocationResourceCounts(
                device_count=device_counts.get(location_id, 0),
                sensor_count=sensor_counts.get(location_id, 0),
                collection_task_count=task_counts.get(location_id, 0),
            )
            for location_id in location_ids_with_rows
        }

    def _device_counts(self, scope: ProjectScope, ids: list[UUID]) -> dict[UUID, int]:
        rows = self._session.execute(
            select(Device.location_id, func.count(Device.id))
            .where(
                Device.project_id == scope.project_id,
                Device.location_id.in_(ids),
            )
            .group_by(Device.location_id)
        ).all()
        return {row[0]: int(row[1]) for row in rows}

    def _sensor_counts(self, scope: ProjectScope, ids: list[UUID]) -> dict[UUID, int]:
        # 传感器没有 location_id：只能经设备归属。设备可跨项目？不能——
        # 复合外键 + project_id 条件同时约束，跨项目行不可能混入本结果。
        rows = self._session.execute(
            select(Device.location_id, func.count(Sensor.id))
            .join(Device, Sensor.device_id == Device.id)
            .where(
                Device.project_id == scope.project_id,
                Sensor.project_id == scope.project_id,
                Device.location_id.in_(ids),
            )
            .group_by(Device.location_id)
        ).all()
        return {row[0]: int(row[1]) for row in rows}

    def _task_counts(self, scope: ProjectScope, ids: list[UUID]) -> dict[UUID, int]:
        rows = self._session.execute(
            select(CollectionTask.location_id, func.count(CollectionTask.id))
            .where(
                CollectionTask.project_id == scope.project_id,
                CollectionTask.location_id.in_(ids),
            )
            .group_by(CollectionTask.location_id)
        ).all()
        return {row[0]: int(row[1]) for row in rows}


__all__ = ["SqlSiteResourceSummaryRepository"]
