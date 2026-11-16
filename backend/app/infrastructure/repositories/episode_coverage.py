"""缺口视图的 Episode 覆盖统计（只读）。

口径与取舍：
- 统计范围用 ``EPISODE_COVERAGE_STATUSES``（READY 起、数据已冻结），而不是训练
  门禁的 ``EPISODE_COVERABLE_STATUSES``（ACCEPTED/COMPLETED）：后者会让
  ``QUALITY_STATUS`` 轴恒等于 PASS。口径差异在领域常量处已注明来由；
- 一律用 SQL 聚合（``func.count`` + ``GROUP BY``），不把行取回再来 Python 计数；
- 强制 ``project_id`` 作用域：``project_id`` 只来自服务端 ``ProjectScope``；
- 某维度值没有可用 Episode 时**不返回该行**：0 与"测不到"必须可区分
  （ADR-0013 §3.4 要求缺失显示 UNKNOWN，不得记为 0）。

JSON 数组维度（``scene_labels`` / ``device_ids``）的跨方言取舍：SQLite 与 PostgreSQL
的「把 JSON 数组展开成多行」是两套不同语法——

- SQLite 3.38+ 内置表值函数 ``json_each(x)``，产出 ``(key, value, ...)``；
- PostgreSQL 用 ``jsonb_array_elements_text(x)``，产出单列 ``value``。

二者都由数据库原生完成展开，语义一致（每个数组元素一行），因此这里按会话方言选择
表值函数，而不是为了迁就 SQLite 写「取回后在 Python 里解析 JSON」——后者会把全表读
进内存、在数据量上来后直接变成性能事故。非 PostgreSQL / SQLite 方言显式抛错，
绝不静默返回空统计（空统计会被上游误读成"覆盖为 0"）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.domain.episodes.state_machines import EPISODE_COVERAGE_STATUSES
from app.domain.identity.models import ProjectScope
from app.domain.modelops.enums import CoverageDimension
from app.domain.modelops.gaps import MeasuredCoverage
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.repositories._collection_sql import as_utc

_COVERAGE_STATUSES = tuple(sorted(item.value for item in EPISODE_COVERAGE_STATUSES))

# 维度 → 取该维度值并聚合；``json_array`` 标记哪些维度要从 JSON 数组横向展开。
_SCALAR_DIMENSIONS: dict[str, Any] = {
    CoverageDimension.ROBOT.value: EpisodeModel.robot_id,
    CoverageDimension.QUALITY_STATUS.value: EpisodeModel.quality_status,
}
_ARRAY_DIMENSIONS: dict[str, Any] = {
    CoverageDimension.SCENE.value: EpisodeModel.scene_labels,
    CoverageDimension.DEVICE.value: EpisodeModel.device_ids,
}


class SqlEpisodeCoverageDirectory:
    """按维度统计项目内可覆盖 Episode 的只读仓储实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def coverage_by_dimension(
        self,
        scope: ProjectScope,
        *,
        dimension_types: frozenset[str] | None = None,
    ) -> list[MeasuredCoverage]:
        dialect = self._session.get_bind().dialect.name
        if dialect not in {"sqlite", "postgresql"}:
            raise RuntimeError(f"未支持的数据库方言：{dialect}")
        wanted = self._resolve_dimensions(dimension_types)
        entries: list[MeasuredCoverage] = []
        for dimension_type in sorted(wanted):
            if dimension_type in _SCALAR_DIMENSIONS:
                entries.extend(self._scalar_coverage(scope, dimension_type))
            else:
                entries.extend(self._array_coverage(scope, dimension_type, dialect=dialect))
        return entries

    @staticmethod
    def _resolve_dimensions(dimension_types: frozenset[str] | None) -> frozenset[str]:
        supported = frozenset(_SCALAR_DIMENSIONS) | frozenset(_ARRAY_DIMENSIONS)
        if dimension_types is None:
            return supported
        unknown = {
            item
            for item in dimension_types
            if item not in {member.value for member in CoverageDimension}
        }
        if unknown:
            # 未知维度不能静默忽略：否则调用方会以为"统计过但结果是空"。
            raise ValueError(f"不支持的覆盖维度：{', '.join(sorted(unknown))}")
        return frozenset(dimension_types) & supported

    def _base_where(self, scope: ProjectScope) -> tuple[Any, ...]:
        return (
            EpisodeModel.project_id == scope.project_id,
            EpisodeModel.status.in_(_COVERAGE_STATUSES),
        )

    def _scalar_coverage(
        self,
        scope: ProjectScope,
        dimension_type: str,
    ) -> list[MeasuredCoverage]:
        column = _SCALAR_DIMENSIONS[dimension_type]
        statement = (
            select(column, func.count(EpisodeModel.id), func.max(EpisodeModel.updated_at))
            .where(*self._base_where(scope), column.is_not(None))
            .group_by(column)
        )
        return [
            MeasuredCoverage(
                dimension_type=dimension_type,
                dimension_value=str(value),
                count=int(count),
                measured_at=as_utc(measured_at),
            )
            for value, count, measured_at in self._session.execute(statement)
        ]

    def _array_coverage(
        self,
        scope: ProjectScope,
        dimension_type: str,
        *,
        dialect: str,
    ) -> list[MeasuredCoverage]:
        column = _ARRAY_DIMENSIONS[dimension_type]
        expanded = self._expand_array(column, dialect=dialect)
        statement = (
            select(
                expanded.c.value,
                func.count(func.distinct(EpisodeModel.id)),
                func.max(EpisodeModel.updated_at),
            )
            .select_from(EpisodeModel)
            # json_each / jsonb_array_elements_text 是横向相关的表值函数：
            # 显式 ON TRUE 让 SQLAlchemy 不把两段 FROM 判成笛卡尔积，
            # 同时保持「每个数组元素一行」的原生展开语义。
            .join(expanded, true())
            .where(*self._base_where(scope))
            .group_by(expanded.c.value)
        )
        return [
            MeasuredCoverage(
                dimension_type=dimension_type,
                dimension_value=str(value),
                count=int(count),
                measured_at=as_utc(measured_at),
            )
            for value, count, measured_at in self._session.execute(statement)
        ]

    @staticmethod
    def _expand_array(column: Any, *, dialect: str) -> Any:
        if dialect == "postgresql":
            return (
                func.jsonb_array_elements_text(column)
                .table_valued("value", joins_implicitly=True)
                .alias("coverage_value")
            )
        return (
            func.json_each(column)
            .table_valued("value", joins_implicitly=True)
            .alias("coverage_value")
        )


__all__ = ["SqlEpisodeCoverageDirectory"]
