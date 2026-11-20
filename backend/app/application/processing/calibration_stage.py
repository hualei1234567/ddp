"""``CALIBRATION_VALIDATION``：校验 Episode 标定快照可追溯且未漂移。

本阶段只验证 Episode 冻结的快照能否在当前项目找到同一条标定记录，以及业务
版本、checksum、目标与 Profile schema 版本是否一致。标定记录后续变为
``SUPERSEDED`` / ``REVOKED`` 不会否定历史 Episode 的可复现性，因此这里不把
当前状态当作快照一致性条件。

公开数据没有标定快照时如实计入“缺失”，不生成参数、不猜测型号默认值，也不把
“检查动作完成”描述成“标定有效”。下游数据集门禁继续用既有
``CALIBRATION_MISSING`` / ``CALIBRATION_INVALID`` 决定是否放行。
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from app.application.devices.ports import CalibrationRecordRepository
from app.application.episodes.ports import EpisodeRawAssetRepository, EpisodeRepository
from app.application.processing.quality_stage import resolve_episode_ids
from app.application.processing.stages import StageContext, StageFailure, StageOutcome
from app.domain.devices.calibration import CalibrationRecord
from app.domain.processing.enums import ProcessingStepStatus

MAX_CALIBRATION_EPISODES = 200


class CalibrationValidationHandler:
    """逐 Episode 验证不可变标定快照与项目内权威记录一致。"""

    def __init__(
        self,
        *,
        episodes: EpisodeRepository,
        episode_assets: EpisodeRawAssetRepository,
        calibrations: CalibrationRecordRepository,
        max_episodes: int = MAX_CALIBRATION_EPISODES,
    ) -> None:
        self._episodes = episodes
        self._episode_assets = episode_assets
        self._calibrations = calibrations
        self._max_episodes = max_episodes

    def __call__(self, context: StageContext) -> StageOutcome:
        episode_ids = resolve_episode_ids(
            context.scope,
            context.run.input_refs,
            self._episode_assets,
        )
        if len(episode_ids) > self._max_episodes:
            raise StageFailure(
                "calibration_episode_limit_exceeded",
                f"本次运行关联 {len(episode_ids)} 个 Episode，超过标定校验上限 "
                f"{self._max_episodes}",
                retryable=False,
            )
        if not episode_ids:
            return StageOutcome(
                status=ProcessingStepStatus.SKIPPED.value,
                log_summary="跳过：本次运行的输入未关联任何 Episode，无法校验标定快照",
            )

        matched = 0
        missing_episodes = 0
        invalid = 0
        for episode_id in episode_ids:
            episode = self._episodes.get(context.scope, episode_id)
            if episode is None:
                raise StageFailure(
                    "calibration_episode_not_found",
                    f"Episode 不存在或不属于当前项目：{episode_id}",
                    retryable=False,
                )
            snapshots = tuple(getattr(episode, "calibration_snapshot", ()))
            if not snapshots:
                missing_episodes += 1
                continue
            for snapshot in snapshots:
                record = self._record(context, snapshot)
                if record is None or not _snapshot_matches(snapshot, record):
                    invalid += 1
                else:
                    matched += 1

        return StageOutcome(
            status=ProcessingStepStatus.SUCCEEDED.value,
            log_summary=(
                f"标定快照校验完成：Episode {len(episode_ids)} 个，"
                f"一致 {matched} 条，无效 {invalid} 条，缺失 {missing_episodes} 个 Episode；"
                "仅验证快照可追溯性，不代表标定当前 ACTIVE"
            ),
        )

    def _record(
        self,
        context: StageContext,
        snapshot: Mapping[str, object],
    ) -> CalibrationRecord | None:
        calibration_id = UUID(str(snapshot["calibration_id"]))
        return self._calibrations.get(context.scope, calibration_id)


def _snapshot_matches(
    snapshot: Mapping[str, object],
    record: CalibrationRecord,
) -> bool:
    expected = {
        "calibration_version": record.calibration_version,
        "checksum": record.checksum,
        "target_type": record.target_type.value,
        "target_id": str(record.target_id),
        "schema_version": record.profile_schema_version,
    }
    return all(snapshot.get(field) == value for field, value in expected.items())


__all__ = ["MAX_CALIBRATION_EPISODES", "CalibrationValidationHandler"]
