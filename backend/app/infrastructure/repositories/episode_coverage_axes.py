"""缺口视图三个「封闭轴」的 SQL 统计：ACTION / LABEL / MODALITY。

为什么单独成模块：三个轴各有一条约 3 跳的权威解析链路，与开放轴的
「单列 / 单 JSON 数组分组」不是同一类逻辑；混在一个类里会掩盖两类口径的差异，
也会把 ``episode_coverage.py`` 推过 500 行阈值。

口径（规则声明在领域层 ``CLOSED_COVERAGE_DIMENSIONS``）：
只有统计范围内**每个** Episode 都能从权威来源解析出取值时，轴才 ``complete``；
此时缺席的维度值才是真实的 0。任一 Episode 缺权威来源，整轴不给实测结论。

权威链路（全部走数据库外键，不接受客户端输入）：

- MODALITY：``episodes.sensor_ids`` → ``sensors.sensor_type``；
  ``sensor_ids`` 为空或引用了同项目里不存在的传感器 → 不可测；
- ACTION：``episodes.task_id`` → ``collection_tasks.action_spec_id`` →
  ``action_specs.actions[].code``（回退 ``name``）；任务没引用动作库、动作库为空、
  或存在取不出标识的元素 → 不可测；
- LABEL：``episodes.annotation_status='APPROVED'`` 且存在 ``APPROVED`` 标注记录
  → ``annotation_records.labels[].label_key``；未标注的 Episode 不是"没有这个
  标签"，而是"还不知道"，故使整轴不可测。

所有查询都强制服务端 ``project_id`` 作用域，跨项目引用解析不到，只会表现为
"不可测"，不会泄漏别的项目是否存在该资源。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select, true
from sqlalchemy.orm import Session

from app.domain.episodes.enums import EpisodeAnnotationStatus
from app.domain.identity.models import ProjectScope
from app.domain.modelops.enums import CoverageDimension
from app.domain.modelops.gaps import MeasuredCoverage
from app.infrastructure.db.models.annotation_results import AnnotationRecord
from app.infrastructure.db.models.collection import CollectionTask
from app.infrastructure.db.models.collection_assets import ActionSpecModel
from app.infrastructure.db.models.devices import Sensor
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.repositories._collection_sql import as_utc
from app.infrastructure.repositories._coverage_sql import (
    coverage_scope_conditions,
    element_is_object,
    expand_object_array,
    expand_text_array,
    json_array_length,
    json_string_field,
    uuid_text_from_column,
    uuid_text_from_json,
)

_APPROVED = "APPROVED"


@dataclass(frozen=True, slots=True)
class AxisResult:
    """一个封闭轴的统计结果。

    ``complete`` 表示权威来源覆盖了统计范围内的**全部** Episode；只有它为真时，
    缺席的维度值才能被解释成真实的 0，否则上游必须按不可测处理。
    """

    entries: list[MeasuredCoverage]
    complete: bool
    measured_at: datetime | None


class ClosedCoverageAxes:
    """ACTION / LABEL / MODALITY 的只读统计实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def coverage(self, scope: ProjectScope, dimension_type: str, *, dialect: str) -> AxisResult:
        if dimension_type == CoverageDimension.MODALITY.value:
            return self._modality(scope, dialect=dialect)
        if dimension_type == CoverageDimension.ACTION.value:
            return self._action(scope, dialect=dialect)
        return self._label(scope, dialect=dialect)

    def _scope_totals(self, scope: ProjectScope) -> tuple[int, datetime | None]:
        statement = select(
            func.count(EpisodeModel.id),
            func.max(EpisodeModel.updated_at),
        ).where(*coverage_scope_conditions(scope))
        total, last_change = self._session.execute(statement).one()
        return int(total), as_utc(last_change)

    def _scalar(self, statement: Any) -> int:
        return int(self._session.execute(statement).scalar_one())

    # ------------------------------------------------------------------ #
    # MODALITY
    # ------------------------------------------------------------------ #
    def _modality(self, scope: ProjectScope, *, dialect: str) -> AxisResult:
        total, last_change = self._scope_totals(scope)
        if total == 0:
            return AxisResult([], False, None)
        elements = expand_text_array(EpisodeModel.sensor_ids, dialect=dialect)
        sensor_condition = and_(
            Sensor.project_id == EpisodeModel.project_id,
            uuid_text_from_json(elements.c.value, dialect=dialect)
            == uuid_text_from_column(Sensor.id),
        )
        unresolved = self._scalar(
            select(func.count())
            .select_from(EpisodeModel)
            .join(elements, true())
            .outerjoin(Sensor, sensor_condition)
            .where(*coverage_scope_conditions(scope), Sensor.id.is_(None))
        )
        empty = self._scalar(
            select(func.count(EpisodeModel.id)).where(
                *coverage_scope_conditions(scope),
                json_array_length(EpisodeModel.sensor_ids, dialect=dialect) == 0,
            )
        )
        if empty > 0 or unresolved > 0:
            return AxisResult([], False, last_change)
        rows = self._session.execute(
            select(
                Sensor.sensor_type,
                func.count(func.distinct(EpisodeModel.id)),
                func.max(EpisodeModel.updated_at),
            )
            .select_from(EpisodeModel)
            .join(elements, true())
            .join(Sensor, sensor_condition)
            .where(*coverage_scope_conditions(scope))
            .group_by(Sensor.sensor_type)
        ).all()
        return AxisResult(
            [
                MeasuredCoverage(
                    dimension_type=CoverageDimension.MODALITY.value,
                    dimension_value=str(sensor_type),
                    count=int(count),
                    measured_at=as_utc(measured_at),
                )
                for sensor_type, count, measured_at in rows
            ],
            True,
            last_change,
        )

    # ------------------------------------------------------------------ #
    # ACTION
    # ------------------------------------------------------------------ #
    def _action_source(self, scope: ProjectScope, elements: Any) -> Any:
        return (
            select(EpisodeModel.id)
            .select_from(EpisodeModel)
            .join(
                CollectionTask,
                and_(
                    CollectionTask.project_id == EpisodeModel.project_id,
                    CollectionTask.id == EpisodeModel.task_id,
                ),
            )
            .join(
                ActionSpecModel,
                and_(
                    ActionSpecModel.project_id == CollectionTask.project_id,
                    ActionSpecModel.id == CollectionTask.action_spec_id,
                ),
            )
            .join(elements, true())
            .where(*coverage_scope_conditions(scope))
        )

    def _action(self, scope: ProjectScope, *, dialect: str) -> AxisResult:
        total, last_change = self._scope_totals(scope)
        if total == 0:
            return AxisResult([], False, None)
        elements = expand_object_array(ActionSpecModel.actions, dialect=dialect)
        is_object = element_is_object(elements, dialect=dialect)
        action_key = func.coalesce(
            json_string_field(elements.c.value, "code", dialect=dialect),
            json_string_field(elements.c.value, "name", dialect=dialect),
        )
        source = self._action_source(scope, elements)
        attributed = self._scalar(
            source.where(is_object).with_only_columns(func.count(func.distinct(EpisodeModel.id)))
        )
        non_object = self._scalar(source.where(~is_object).with_only_columns(func.count()))
        unkeyed = self._scalar(
            source.where(is_object, action_key.is_(None)).with_only_columns(func.count())
        )
        if attributed != total or non_object > 0 or unkeyed > 0:
            return AxisResult([], False, last_change)
        rows = self._session.execute(
            source.where(is_object, action_key.is_not(None))
            .with_only_columns(
                action_key,
                func.count(func.distinct(EpisodeModel.id)),
                func.max(EpisodeModel.updated_at),
            )
            .group_by(action_key)
        ).all()
        return AxisResult(
            [
                MeasuredCoverage(
                    dimension_type=CoverageDimension.ACTION.value,
                    dimension_value=str(key),
                    count=int(count),
                    measured_at=as_utc(measured_at),
                )
                for key, count, measured_at in rows
            ],
            True,
            last_change,
        )

    # ------------------------------------------------------------------ #
    # LABEL
    # ------------------------------------------------------------------ #
    def _label_source(self, scope: ProjectScope) -> Any:
        return (
            select(EpisodeModel.id)
            .select_from(EpisodeModel)
            .join(
                AnnotationRecord,
                and_(
                    AnnotationRecord.project_id == EpisodeModel.project_id,
                    AnnotationRecord.episode_id == EpisodeModel.id,
                    AnnotationRecord.status == _APPROVED,
                ),
            )
            .where(*coverage_scope_conditions(scope))
        )

    def _label(self, scope: ProjectScope, *, dialect: str) -> AxisResult:
        total, last_change = self._scope_totals(scope)
        if total == 0:
            return AxisResult([], False, None)
        source = self._label_source(scope)
        resolved = self._scalar(
            source.where(
                EpisodeModel.annotation_status == EpisodeAnnotationStatus.APPROVED.value
            ).with_only_columns(func.count(func.distinct(EpisodeModel.id)))
        )
        if resolved != total:
            return AxisResult([], False, last_change)
        elements = expand_object_array(AnnotationRecord.labels, dialect=dialect)
        is_object = element_is_object(elements, dialect=dialect)
        label_key = json_string_field(elements.c.value, "label_key", dialect=dialect)
        joined = source.join(elements, true())
        non_object = self._scalar(joined.where(~is_object).with_only_columns(func.count()))
        unkeyed = self._scalar(
            joined.where(is_object, label_key.is_(None)).with_only_columns(func.count())
        )
        if non_object > 0 or unkeyed > 0:
            return AxisResult([], False, last_change)
        rows = self._session.execute(
            joined.where(is_object, label_key.is_not(None))
            .with_only_columns(
                label_key,
                func.count(func.distinct(EpisodeModel.id)),
                func.max(EpisodeModel.updated_at),
            )
            .group_by(label_key)
        ).all()
        return AxisResult(
            [
                MeasuredCoverage(
                    dimension_type=CoverageDimension.LABEL.value,
                    dimension_value=str(key),
                    count=int(count),
                    measured_at=as_utc(measured_at),
                )
                for key, count, measured_at in rows
            ],
            True,
            last_change,
        )


__all__ = ["AxisResult", "ClosedCoverageAxes"]
