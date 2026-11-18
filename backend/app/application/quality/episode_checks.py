"""Episode 批量质检入口：供处理流水线 QUALITY 阶段复用同一质量引擎。

本模块**不新增任何规则**，只是把 :class:`QualityRunUseCases` 的
"计算"与"落库"两步暴露给处理流水线：

- 阶段先对本次运行关联的每个 Episode 调用 :meth:`prepare_episode`（只读事实、
  纯内存计算），任何一个 Episode 失败都不会留下半成品；
- 全部计算成功后调用 :meth:`persist_prepared` 一次性落库（只 flush，不提交），
  由编排器把质量结论与阶段推进放进同一事务提交。

这样重试 QUALITY 阶段时：
- 计算阶段失败 → 没有写入，重试从头开始；
- 落库失败 → 编排器回滚整个事务，重试从头开始；
- 崩溃在提交前 → 事务未提交，重试不会产生重复/互相矛盾的 quality_run。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from app.application.quality.commands import NewQualityRun
from app.application.quality.use_cases import QualityRunUseCases
from app.domain.identity.models import ProjectScope
from app.domain.quality.enums import QualitySubjectType
from app.domain.quality.models import QualityRun


class EpisodeQualityChecks:
    """把统一质量引擎收敛成 Episode 粒度的两步接口。"""

    def __init__(self, runs: QualityRunUseCases) -> None:
        self._runs = runs

    def prepare_episode(
        self,
        scope: ProjectScope,
        episode_id: UUID,
        *,
        now: datetime,
    ) -> QualityRun:
        """计算单个 Episode 的质量结论；不写库、不提交。"""
        return self._runs.prepare(
            scope,
            NewQualityRun(
                subject_type=QualitySubjectType.EPISODE,
                subject_id=episode_id,
            ),
            now=now,
        )

    def persist_prepared(
        self,
        scope: ProjectScope,
        runs: Sequence[QualityRun],
    ) -> None:
        """落库已计算的结论；只 flush，事务提交由调用方负责。"""
        for run in runs:
            self._runs.persist(scope, run)


__all__ = ["EpisodeQualityChecks"]
