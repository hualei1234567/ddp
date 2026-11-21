"""``EPISODE_SEGMENTATION``：检查既有 Episode 的分段来源可追溯性。

ProcessingRun 当前只保存对象引用；关联到 Episode 时，Episode 已由导入或创建
链路完成切分。本阶段不创建第二套 Episode，也不拿冻结的起止时间反向伪造切分
输入，而是检查 ``segmentation_rule_version`` 是否能在权威规则注册表中解析。

Episode 只冻结规则版本，规则参数目前仅存在于创建审计，无法从 Episode 本身完整
重放。因此未注册标签（包括既有 ``MANUAL_TIME_RANGE`` 标签）计为“不可重放”，
不误报成切分失败；阶段成功只表示追溯检查完成，明确不代表执行了重新切片。
"""

from __future__ import annotations

from app.application.episodes.ports import EpisodeRawAssetRepository, EpisodeRepository
from app.application.processing.quality_stage import resolve_episode_ids
from app.application.processing.stages import StageContext, StageFailure, StageOutcome
from app.domain.episodes.segmentation_rules import SegmentationRuleRegistry
from app.domain.processing.enums import ProcessingStepStatus

MAX_SEGMENTATION_EPISODES = 200


class EpisodeSegmentationHandler:
    """检查运行关联 Episode 的冻结分段规则版本是否可追溯。"""

    def __init__(
        self,
        *,
        episodes: EpisodeRepository,
        episode_assets: EpisodeRawAssetRepository,
        rules: SegmentationRuleRegistry,
        max_episodes: int = MAX_SEGMENTATION_EPISODES,
    ) -> None:
        self._episodes = episodes
        self._episode_assets = episode_assets
        self._rules = rules
        self._max_episodes = max_episodes

    def __call__(self, context: StageContext) -> StageOutcome:
        episode_ids = resolve_episode_ids(
            context.scope,
            context.run.input_refs,
            self._episode_assets,
        )
        if len(episode_ids) > self._max_episodes:
            raise StageFailure(
                "segmentation_episode_limit_exceeded",
                f"本次运行关联 {len(episode_ids)} 个 Episode，超过分段追溯上限 "
                f"{self._max_episodes}",
                retryable=False,
            )
        if not episode_ids:
            return StageOutcome(
                status=ProcessingStepStatus.SKIPPED.value,
                log_summary="跳过：本次运行的输入未关联任何 Episode，无法检查分段来源",
            )

        traceable = 0
        unreplayable = 0
        for episode_id in episode_ids:
            episode = self._episodes.get(context.scope, episode_id)
            if episode is None:
                raise StageFailure(
                    "segmentation_episode_not_found",
                    f"Episode 不存在或不属于当前项目：{episode_id}",
                    retryable=False,
                )
            if self._rules.has(episode.segmentation_rule_version):
                traceable += 1
            else:
                unreplayable += 1

        return StageOutcome(
            status=ProcessingStepStatus.SUCCEEDED.value,
            log_summary=(
                f"分段来源检查完成：Episode {len(episode_ids)} 个，"
                f"可追溯 {traceable} 个，不可重放 {unreplayable} 个；"
                "仅检查冻结规则版本，未执行重新切片"
            ),
        )


__all__ = ["MAX_SEGMENTATION_EPISODES", "EpisodeSegmentationHandler"]
