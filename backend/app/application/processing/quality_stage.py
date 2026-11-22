"""``QUALITY_PRE_CHECK`` 阶段：对本次运行关联的 Episode 逐个执行统一质量引擎。

**Episode 从哪来（不新增关联表）**

``ProcessingRun`` 只保存对象引用，没有 run→Episode 的关联表。本阶段只认两种
既有、可验证的关联：

1. ``canonical/<project>/<episode_id>/<version>/manifest.json``：
   Episode 自身的 Canonical manifest（``domain.episodes.validation`` 的权威格式）；
2. ``raw/<project>/YYYY/MM/DD/<raw_asset_id>/content.<ext>``：
   Episode 通过 ``episode_raw_assets`` 引用同一 RawAsset
   （``domain.ingestion.validation`` 的权威格式）。

其余输入（例如尚未切片的原始对象）不产生 Episode：阶段如实 ``SKIPPED`` 并写明
原因，不把"没检查"写成"通过"。跨项目引用在解析阶段就被丢弃（并以项目作用域
查询二次兜底），不会产生跨项目读取。

**事务与幂等**

- 先算完全部 Episode 的结论（纯读），再一次性落库；任何一个 Episode 计算失败
  都不会留下"部分已检查"的写入；
- 落库只 flush 不提交：编排器把质量结论与阶段步骤、运行推进放进同一事务。
  落库异常时本处理器主动回滚并抛可重试的 :class:`StageFailure`；
- 因此同一阶段重试不会为同一 Episode 产生互相矛盾的 ``quality_run``。

**结论落点（复用既有列，不新增迁移）**

- 每个 Episode 一条 ``quality_runs``（已有 ``rule_set_version``，历史固化）；
- 阶段结果 ``processing_run_steps.log_summary`` 记录检查数、失败数与规则集版本；
- 运行 ``output_refs`` 追加 ``quality-run/<run_id>``，可逐个回查结论。

Episode 质检结论为 ``FAILED`` 不会让流水线失败：阶段完成的是"检查"而不是
"验收"，是否放行由数据集构造/交付等下游门禁决定。只有检查本身无法完成
（数据越界、规则实现异常、落库故障）才进入失败路径。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.application.episodes.ports import EpisodeRawAssetRepository
from app.application.processing.ports import UnitOfWork
from app.application.processing.refs import manifest_episode_id, raw_asset_id_from_ref
from app.application.processing.stages import StageContext, StageFailure, StageOutcome
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import ProcessingStepStatus
from app.domain.quality.enums import QualityRunStatus
from app.domain.quality.errors import (
    DomainValidationError,
    QualityEngineError,
    ResourceNotFoundError,
)
from app.domain.quality.models import QualityRun

logger = logging.getLogger("ddp.processing.quality")

#: 单次处理运行最多自动质检的 Episode 数。运行 ``output_refs`` 上限为 1000，
#: 每个 Episode 一条 ``quality-run`` 引用；200 同时给对象存储引用留出余量，
#: 并把单阶段的读放大控制在可解释范围内。超限显式失败，不静默截断。
MAX_AUTO_QUALITY_EPISODES = 200

#: 阶段摘要里直接列出的失败 Episode 数。完整清单以 output_refs 的 quality-run
#: 引用为准，摘要保持可读且不超过步骤日志长度上限。
MAX_FAILED_EPISODES_IN_SUMMARY = 10


class EpisodeQualityGateway(Protocol):
    """阶段需要的质量引擎能力：先计算、后落库（不提交）。"""

    def prepare_episode(
        self,
        scope: ProjectScope,
        episode_id: UUID,
        *,
        now: datetime,
    ) -> QualityRun: ...

    def persist_prepared(
        self,
        scope: ProjectScope,
        runs: Sequence[QualityRun],
    ) -> None: ...


def resolve_episode_ids(
    scope: ProjectScope,
    refs: Iterable[str],
    episode_assets: EpisodeRawAssetRepository,
) -> tuple[UUID, ...]:
    """按输入顺序解析本次运行关联的 Episode，去重并保持稳定顺序。"""
    project_id = str(scope.project_id)
    ordered: list[UUID] = []
    seen: set[UUID] = set()

    def _add(episode_id: UUID) -> None:
        if episode_id not in seen:
            seen.add(episode_id)
            ordered.append(episode_id)

    for ref in refs:
        episode_id = manifest_episode_id(ref, project_id)
        if episode_id is not None:
            _add(episode_id)
            continue
        asset_id = raw_asset_id_from_ref(ref, project_id)
        if asset_id is None:
            continue
        for link in episode_assets.list_for_asset(scope, asset_id):
            _add(link.episode_id)
    return tuple(ordered)


class QualityCheckHandler:
    """对运行关联的 Episode 逐个执行统一质量引擎并落库（不提交事务）。"""

    def __init__(
        self,
        *,
        episode_assets: EpisodeRawAssetRepository,
        quality: EpisodeQualityGateway,
        uow: UnitOfWork,
        max_episodes: int = MAX_AUTO_QUALITY_EPISODES,
    ) -> None:
        self._episode_assets = episode_assets
        self._quality = quality
        self._uow = uow
        self._max_episodes = max_episodes

    def __call__(self, context: StageContext) -> StageOutcome:
        episode_ids = resolve_episode_ids(
            context.scope,
            context.run.input_refs,
            self._episode_assets,
        )
        if len(episode_ids) > self._max_episodes:
            raise StageFailure(
                "quality_episode_limit_exceeded",
                f"本次运行关联 {len(episode_ids)} 个 Episode，超过自动质检上限 "
                f"{self._max_episodes}",
                retryable=False,
            )
        if not episode_ids:
            return StageOutcome(
                status=ProcessingStepStatus.SKIPPED.value,
                log_summary=(
                    "跳过：本次运行的输入未关联任何 Episode"
                    "（仅 Canonical manifest 或已建立 episode_raw_assets 关联的 RawAsset 可解析）"
                ),
            )

        prepared: list[QualityRun] = []
        for episode_id in episode_ids:
            try:
                prepared.append(
                    self._quality.prepare_episode(
                        context.scope,
                        episode_id,
                        now=context.now,
                    )
                )
            except ResourceNotFoundError as exc:
                # 引用指向本项目之外的 Episode：按数据问题处理，不重试也不会
                # 降级成"跳过"——否则越权引用会被当成正常空结果。
                raise StageFailure(
                    "quality_episode_not_found",
                    f"Episode 不存在或不属于当前项目：{episode_id}",
                    retryable=False,
                ) from exc
            except QualityEngineError as exc:
                raise StageFailure(
                    "quality_engine_error",
                    f"质量规则执行异常：{exc}",
                    retryable=False,
                ) from exc
            except DomainValidationError as exc:
                raise StageFailure(
                    "quality_subject_invalid",
                    f"Episode {episode_id} 不满足质检输入上限或数据格式要求：{exc}",
                    retryable=False,
                ) from exc

        # 摘要先算：任何格式化异常都发生在写库之前，避免"结论已落库、阶段却
        # 报失败"的半成品。
        summary, output_refs = _summarize(prepared)
        try:
            self._quality.persist_prepared(context.scope, tuple(prepared))
        except Exception as exc:  # noqa: BLE001 - 落库边界：回滚后转可重试失败
            logger.exception(
                "质量结论落库失败",
                extra={
                    "run_id": str(context.run.run_id),
                    "episode_count": len(prepared),
                },
            )
            self._uow.rollback()
            raise StageFailure(
                "quality_persist_failed",
                "质量结论落库失败，可重试",
                retryable=True,
            ) from exc

        logger.info(
            "processing.quality.finished",
            extra={
                "run_id": str(context.run.run_id),
                "project_id": str(context.run.project_id),
                "episode_count": len(prepared),
            },
        )
        return StageOutcome(
            status=ProcessingStepStatus.SUCCEEDED.value,
            log_summary=summary,
            output_refs=output_refs,
        )


def _summarize(runs: Sequence[QualityRun]) -> tuple[str, tuple[str, ...]]:
    """生成阶段摘要与可回查的 quality-run 引用。"""
    passed = sum(1 for run in runs if run.status == QualityRunStatus.PASSED)
    failed = [run for run in runs if run.status == QualityRunStatus.FAILED]
    incomplete = sum(1 for run in runs if run.status == QualityRunStatus.INCOMPLETE)
    versions = sorted({run.rule_set_version for run in runs})
    version_text = (
        versions[0] if len(versions) == 1 else "、".join(versions) if versions else "（无）"
    )

    parts = [
        f"质检完成：检查 {len(runs)} 个 Episode"
        f"（通过 {passed}、失败 {len(failed)}、未完成 {incomplete}）",
        f"规则集 {version_text}",
    ]
    if failed:
        shown = failed[:MAX_FAILED_EPISODES_IN_SUMMARY]
        suffix = (
            f" 等 {len(failed)} 个（全部见 quality-run 引用）" if len(failed) > len(shown) else ""
        )
        parts.append("失败 Episode：" + "、".join(str(run.subject_id) for run in shown) + suffix)
    return "；".join(parts), tuple(f"quality-run/{run.run_id}" for run in runs)


__all__ = [
    "MAX_AUTO_QUALITY_EPISODES",
    "MAX_FAILED_EPISODES_IN_SUMMARY",
    "EpisodeQualityGateway",
    "QualityCheckHandler",
    "resolve_episode_ids",
]
