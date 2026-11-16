"""缺口视图的 Episode 覆盖统计（只读）。

口径与取舍：
- 统计范围用 ``EPISODE_COVERAGE_STATUSES``（READY 起、数据已冻结），而不是训练
  门禁的 ``EPISODE_COVERABLE_STATUSES``（ACCEPTED/COMPLETED）：后者会让
  ``QUALITY_STATUS`` 轴恒等于 PASS。口径差异在领域常量处已注明来由；
- 一律用 SQL 聚合（``func.count`` + ``GROUP BY``），不把行取回再来 Python 计数；
- 强制 ``project_id`` 作用域：``project_id`` 只来自服务端 ``ProjectScope``；
- 某维度值没有可用 Episode 时**不返回该行**：0 与"测不到"必须可区分
  （ADR-0013 §3.4 要求缺失显示 UNKNOWN，不得记为 0）。

两类轴（规则声明在领域层 ``CLOSED_COVERAGE_DIMENSIONS``）：

- **开放轴** SCENE / ROBOT / DEVICE / QUALITY_STATUS：Episode 缺该属性是常态
  （未打标、未绑定设备），缺值不等于 0，只返回实测到的维度值。
- **封闭轴** ACTION / LABEL / MODALITY：只有每个 Episode 都有权威来源时才给结论。
  总体完整时，调用方通过 ``wanted_keys`` 点名的、数据中缺席的维度值补一条
  ``count=0`` 的实测记录——"查过全部且都没有"是真实的 0；总体不完整时整轴不返回，
  上游据此回退人工值或 UNKNOWN。三个轴的解析链路见 ``episode_coverage_axes``。

非 PostgreSQL / SQLite 方言显式抛错，绝不静默返回空统计（空统计会被上游误读成
"覆盖为 0"）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.domain.modelops.enums import CLOSED_COVERAGE_DIMENSIONS, CoverageDimension
from app.domain.modelops.gaps import MeasuredCoverage
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.repositories._collection_sql import as_utc
from app.infrastructure.repositories._coverage_sql import (
    coverage_scope_conditions,
    expand_text_array,
)
from app.infrastructure.repositories.episode_coverage_axes import (
    AxisResult,
    ClosedCoverageAxes,
)

_CLOSED_VALUES = frozenset(item.value for item in CLOSED_COVERAGE_DIMENSIONS)

# 开放轴：维度 → 取该维度值并聚合；JSON 数组维度的展开见 ``_array_coverage``。
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
        self._closed = ClosedCoverageAxes(session)

    def coverage_by_dimension(
        self,
        scope: ProjectScope,
        *,
        dimension_types: frozenset[str] | None = None,
        wanted_keys: Iterable[tuple[str, str]] | None = None,
    ) -> list[MeasuredCoverage]:
        dialect = self._session.get_bind().dialect.name
        if dialect not in {"sqlite", "postgresql"}:
            raise RuntimeError(f"未支持的数据库方言：{dialect}")
        requested = self._resolve_dimensions(dimension_types)
        entries: list[MeasuredCoverage] = []
        closed: dict[str, AxisResult] = {}
        for dimension_type in sorted(requested):
            if dimension_type in _SCALAR_DIMENSIONS:
                entries.extend(self._scalar_coverage(scope, dimension_type))
            elif dimension_type in _ARRAY_DIMENSIONS:
                entries.extend(self._array_coverage(scope, dimension_type, dialect=dialect))
            else:
                result = self._closed.coverage(scope, dimension_type, dialect=dialect)
                closed[dimension_type] = result
                entries.extend(result.entries)
        entries.extend(
            self._zero_fill(
                requested=requested,
                closed=closed,
                wanted_keys=wanted_keys,
                present={(item.dimension_type, item.dimension_value) for item in entries},
            )
        )
        entries.sort(key=lambda item: (item.dimension_type, item.dimension_value))
        return entries

    @staticmethod
    def _resolve_dimensions(dimension_types: frozenset[str] | None) -> frozenset[str]:
        supported = frozenset(_SCALAR_DIMENSIONS) | frozenset(_ARRAY_DIMENSIONS) | _CLOSED_VALUES
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

    @staticmethod
    def _zero_fill(
        *,
        requested: frozenset[str],
        closed: dict[str, AxisResult],
        wanted_keys: Iterable[tuple[str, str]] | None,
        present: set[tuple[str, str]],
    ) -> list[MeasuredCoverage]:
        """给封闭轴里"查过且确实没有"的维度值补实测 0。

        只对调用方点名的键补；开放轴与总体不完整的封闭轴一律不补（保持 UNKNOWN）。
        """
        if not wanted_keys:
            return []
        additions: list[MeasuredCoverage] = []
        for dimension_type, dimension_value in sorted(set(wanted_keys)):
            if dimension_type not in requested or dimension_type not in _CLOSED_VALUES:
                continue
            if (dimension_type, dimension_value) in present:
                continue
            result = closed.get(dimension_type)
            if result is None or not result.complete:
                continue
            additions.append(
                MeasuredCoverage(
                    dimension_type=dimension_type,
                    dimension_value=dimension_value,
                    count=0,
                    measured_at=result.measured_at,
                )
            )
        return additions

    def _scalar_coverage(
        self,
        scope: ProjectScope,
        dimension_type: str,
    ) -> list[MeasuredCoverage]:
        column = _SCALAR_DIMENSIONS[dimension_type]
        statement = (
            select(column, func.count(EpisodeModel.id), func.max(EpisodeModel.updated_at))
            .where(*coverage_scope_conditions(scope), column.is_not(None))
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
        expanded = expand_text_array(column, dialect=dialect)
        statement = (
            select(
                expanded.c.value,
                func.count(func.distinct(EpisodeModel.id)),
                func.max(EpisodeModel.updated_at),
            )
            .select_from(EpisodeModel)
            # 表值函数是横向相关的：显式 ON TRUE 让 SQLAlchemy 不把两段 FROM
            # 判成笛卡尔积，同时保持「每个数组元素一行」的原生展开语义。
            .join(expanded, true())
            .where(*coverage_scope_conditions(scope))
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


__all__ = ["SqlEpisodeCoverageDirectory"]
