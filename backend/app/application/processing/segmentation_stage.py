"""``EPISODE_SEGMENTATION``：既有 Episode 追溯检查 + 明确边界时创建 Episode。

两类输入分开处理（V31-018）：

- 输入已关联 Episode（canonical manifest 或 Episode-RawAsset 关联）→ 保持既有
  行为：检查冻结分段规则版本是否可追溯，**不**重新切片。
- 输入是原始 RawAsset 且元数据阶段给出了明确边界（当前只有单文件 MP4）→ 创建
  Episode；创建按稳定 code 幂等，重跑不会造出第二条。
- 两者都没有 → ``SKIPPED`` 并写明"没有明确边界"。**不允许**把整个容器文件当成
  一个 Episode 来凑数。

边界推导放在 :mod:`episode_boundaries`（纯函数）；本模块只做编排与失败闭合。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.application.processing.episode_boundaries import plan_episode_boundaries
from app.application.processing.quality_stage import resolve_episode_ids
from app.application.processing.refs import raw_asset_id_from_ref
from app.application.processing.stages import StageContext, StageFailure, StageOutcome
from app.domain.episodes.segmentation_rules import SegmentationRuleRegistry
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import ProcessingStepStatus

MAX_SEGMENTATION_EPISODES = 200


@dataclass(frozen=True, slots=True)
class EpisodeBoundaryRequest:
    """一段待创建的 Episode 边界；实现方负责补齐其余服务端权威字段。"""

    raw_object_key: str
    asset_id: UUID
    start_time_ns: int
    end_time_ns: int


class EpisodeCreator(Protocol):
    """从处理运行创建 Episode 的端口。

    返回 ``(episode_id, created)``：``created=False`` 表示同 code 的 Episode
    已存在（重跑或并发），不是失败。
    """

    def create_from_boundary(
        self,
        scope: ProjectScope,
        request: EpisodeBoundaryRequest,
        *,
        run_pipeline_version: str,
    ) -> tuple[UUID, bool]: ...


class BoundaryArtifactReader(Protocol):
    """读取本运行元数据产物文档（按原始对象 key 索引）；缺失的跳过。"""

    def read_metadata_documents(
        self,
        scope: ProjectScope,
        *,
        run_id: UUID,
        source_refs: tuple[str, ...],
    ) -> Mapping[str, Mapping[str, object]]: ...


class EpisodeSegmentationHandler:
    """检查既有 Episode 的分段来源，或按明确边界创建 Episode。"""

    def __init__(
        self,
        *,
        episodes,
        episode_assets,
        rules: SegmentationRuleRegistry,
        max_episodes: int = MAX_SEGMENTATION_EPISODES,
        metadata_reader: BoundaryArtifactReader | None = None,
        creator: EpisodeCreator | None = None,
    ) -> None:
        self._episodes = episodes
        self._episode_assets = episode_assets
        self._rules = rules
        self._max_episodes = max_episodes
        # 边界推导与创建必须成对装配：只给一半会让"有边界"的输入静默退回
        # 追溯检查，看不到任何"为什么没建 Episode"的线索。
        if (metadata_reader is None) != (creator is None):
            raise ValueError("metadata_reader 与 creator 必须同时提供或同时省略")
        self._metadata_reader = metadata_reader
        self._creator = creator

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

        creation_summary, had_boundaries = self._create_from_boundaries(context)
        if episode_ids:
            trace = self._check_traceability(context, episode_ids)
            return StageOutcome(
                status=ProcessingStepStatus.SUCCEEDED.value,
                log_summary=f"{trace}；{creation_summary}" if creation_summary else trace,
            )
        if had_boundaries:
            return StageOutcome(
                status=ProcessingStepStatus.SUCCEEDED.value,
                log_summary=creation_summary or "",
            )
        return StageOutcome(
            status=ProcessingStepStatus.SKIPPED.value,
            log_summary=creation_summary
            or (
                "跳过：本次运行的输入未关联任何 Episode，且没有明确边界声明；"
                "不把容器文件整段当成一个 Episode"
            ),
        )

    def _check_traceability(self, context: StageContext, episode_ids) -> str:
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
        return (
            f"分段来源检查完成：Episode {len(episode_ids)} 个，"
            f"可追溯 {traceable} 个，不可重放 {unreplayable} 个；"
            "仅检查冻结规则版本，未执行重新切片"
        )

    def _create_from_boundaries(self, context: StageContext) -> tuple[str | None, bool]:
        """按明确边界创建 Episode。

        返回 ``(摘要, 是否存在明确边界)``。第二个值区分三种情形：没有可切分的
        RawAsset 输入 ``(None, False)``、有输入但没有明确边界 ``(原因, False)``、
        有明确边界 ``(创建统计, True)``——重跑时"已存在"仍属成功，不是跳过。
        """
        if self._metadata_reader is None or self._creator is None:
            return None, False
        documents = self._metadata_reader.read_metadata_documents(
            context.scope,
            run_id=context.run.run_id,
            source_refs=context.run.input_refs,
        )
        plan = plan_episode_boundaries(
            project_id=context.run.project_id,
            input_refs=context.run.input_refs,
            metadata_documents=documents,
        )
        if not plan.boundaries:
            if not plan.skipped:
                return None, False
            reasons = "; ".join(f"{item.raw_object_key}: {item.reason}" for item in plan.skipped)
            return f"未创建 Episode：输入没有明确边界（{reasons}）", False

        created = 0
        existing = 0
        for boundary in plan.boundaries:
            asset_id = raw_asset_id_from_ref(
                boundary.raw_object_key,
                str(context.run.project_id),
            )
            if asset_id is None:  # pragma: no cover - 推导阶段已过滤
                continue
            _, is_new = self._creator.create_from_boundary(
                context.scope,
                EpisodeBoundaryRequest(
                    raw_object_key=boundary.raw_object_key,
                    asset_id=asset_id,
                    start_time_ns=boundary.start_time_ns,
                    end_time_ns=boundary.end_time_ns,
                ),
                run_pipeline_version=context.run.pipeline_version,
            )
            if is_new:
                created += 1
            else:
                existing += 1
        skipped_note = f"，跳过 {len(plan.skipped)} 个无明确边界的输入" if plan.skipped else ""
        return (
            f"按明确边界创建 Episode：新建 {created} 个，已存在 {existing} 个{skipped_note}",
            True,
        )


__all__ = [
    "MAX_SEGMENTATION_EPISODES",
    "BoundaryArtifactReader",
    "EpisodeBoundaryRequest",
    "EpisodeCreator",
    "EpisodeSegmentationHandler",
]
