"""把一次导入的产物materialize成 Canonical Episode。

**为什么需要这一层**

导入链路产出的是「一条 MediaAsset = 一个机位的一段视频」，而 Canonical 层的
样本单元是 Episode（一个演示片段的全部机位）。两者之间此前没有任何桥：
``episodes`` 表恒为 0 行，标注、质检、数据集、训练全部无米下锅。

本模块只做**编排**，不复制任何业务规则：

- 分组：按 ``MediaAsset.episode_key`` 聚合（导入解析器已按 ``example_id``
  分组，MediaAsset 上保留了这个键）；
- 切片：每个 Episode 的时间范围取「该组全部机位的最早起点 → 最晚终点」，
  单位纳秒；机位各自的 ``excerpt_*`` 是秒，转换在此做；
- 真正的 Episode 创建、血缘边、幂等、审计、事件，全部复用
  ``EpisodeService.create``——它已经有 8 个协作者与完整校验，重写一遍就是
  第二套权威规则（CLAUDE.md「禁止复制粘贴业务规则」）。

**创建之后立刻尝试自动就绪**

Episode 创建完并不等于可用：进入 ``READY`` 需要 Canonical Manifest，而此前平台
没有任何组件生成它（验收文档 §6.2 记为 ``MISSING``），整条链停在 ``PENDING`` 等
人工补一步。现在本模块在每条 Episode 创建成功后调用
``EpisodeReadinessService.auto_ready``：成功则落 Manifest 与 ``frame_count``；
被拒则 Episode 保持 ``PENDING`` 并把原因写进 ``readiness_blockers``，
**导入本身仍然成功**——「导入完成」与「Episode 就绪」是两个结果，不能混为一谈。

判定的规则不在本层（见 ``domain/episodes/readiness.py``），本层只负责在正确的
时机调用它，并保证失败不把导入一起拖垮、重试不产生重复就绪。

**如实标注的限制**

导入的公开数据集通常**不含标定信息**（HF Human-1M 的 preview/metadata.jsonl
只有视频与任务字段）。因此这里传空 ``calibration_snapshot``，由数据集侧的
``CALIBRATION_MISSING`` 排除码在准入时如实反映——而不是在 Episode 上伪造
一份假的标定快照。同理 ``time_base`` 取 ``DEGRADED`` + ``NONE``：数据集本身
没有任何时钟同步声明，声称 VERIFIED 是造假；``DEGRADED`` 是"可用但未验证"，
数据集准入时要求显式确认原因（``selection.py`` 的 ``allow_degraded_time_sync``）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from app.application.datasources.ports import (
    DataSourceImportRepository,
    MediaAssetRepository,
)
from app.application.episodes.commands import CreateEpisodeCommand
from app.application.episodes.episodes import EpisodeService
from app.application.episodes.ports import EpisodeRepository
from app.application.episodes.readiness import (
    EpisodeReadinessService,
    ReadinessOutcome,
    ReadinessStatus,
)
from app.domain.datasources.errors import DomainValidationError
from app.domain.datasources.media import MediaAsset
from app.domain.episodes.errors import ConflictError as EpisodeConflictError
from app.domain.episodes.models import Segmentation
from app.domain.episodes.segmentation_rules import EPISODE_BOUNDARY_RULE_VERSION
from app.domain.identity.models import ProjectScope

# 导入链路的时间基准：数据集没有声明时钟与同步方式，如实填 DEGRADED + NONE。
# 断言 VERIFIED 会让数据集准入跳过"确认降级原因"，等于把未验证的时间当可信。
IMPORT_TIME_BASE: Mapping[str, object] = {
    "time_domain": "UTC",
    "clock_source": "HOST_CLOCK",
    "sync_method": "NONE",
    "sync_status": "DEGRADED",
}

IMPORT_CANONICAL_SCHEMA_VERSION = "ddp.canonical/1.0.0-draft"
IMPORT_PIPELINE_ID = "data_source_import"
IMPORT_PIPELINE_VERSION = "1.0.0"
# 复用 N3 的 episode-boundary 规则，不另立一套编号。
#
# 导入链路的边界并不是"我们切出来的"，而是解析器从 preview/metadata.jsonl
# 的 example_id 分组读出来的——语义上正是 ``episode-boundary`` 的
# ``boundary_source=PARSER``。此前这里写的是自定义版本号
# ``import-episode-key/1.0.0``，该版本从未在注册表里登记，导入生成 Episode
# 全线报 ``SegmentationRuleNotRegisteredError``。与其把没实现的自定义规则
# 塞进注册表占位，不如如实复用它本来就在做的事。
IMPORT_SEGMENTATION_RULE_VERSION = EPISODE_BOUNDARY_RULE_VERSION
IMPORT_SEGMENTATION_BOUNDARY_SOURCE = "PARSER"

_NS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class MaterializedEpisode:
    episode_id: UUID
    episode_key: str
    raw_asset_ids: tuple[UUID, ...]
    # 创建后立刻尝试的自动就绪结果；``None`` 只在调用方显式不装配就绪服务时出现
    # （生产路径不允许——构造函数要求该服务）。
    readiness: ReadinessOutcome | None = None


@dataclass(frozen=True, slots=True)
class MaterializeResult:
    import_id: UUID
    created: tuple[MaterializedEpisode, ...]
    skipped_episode_keys: tuple[str, ...]
    # 已存在的 Episode 也重试一次就绪（数据补齐后可以真的就绪），结果记在这里。
    refreshed: tuple[MaterializedEpisode, ...] = ()

    @property
    def episode_count(self) -> int:
        return len(self.created)

    @property
    def ready_count(self) -> int:
        return sum(
            1
            for item in (*self.created, *self.refreshed)
            if item.readiness is not None and item.readiness.status is ReadinessStatus.READY
        )

    @property
    def blocked_count(self) -> int:
        return sum(
            1
            for item in (*self.created, *self.refreshed)
            if item.readiness is not None and item.readiness.status is ReadinessStatus.BLOCKED
        )


class ImportEpisodeMaterializer:
    """按 episode_key 把导入产物聚合成 Episode。

    幂等：同一 ``episode_key`` 的 Episode 用**确定性 code**
    （``EP-{episode_key}``），``EpisodeService.create`` 遇到同 code 会抛
    ``ConflictError``，本层捕获后计为"已存在、跳过"。这样重复执行不会造出
    两份 Episode，也不需要额外的幂等表。
    """

    def __init__(
        self,
        imports: DataSourceImportRepository,
        assets: MediaAssetRepository,
        episodes: EpisodeService,
        episode_repository: EpisodeRepository,
        readiness: EpisodeReadinessService,
    ) -> None:
        self._imports = imports
        self._assets = assets
        self._episodes = episodes
        self._episode_repo = episode_repository
        # 必填而不是可选：漏接会让"自动就绪"静默失效，退回人工补 Manifest——
        # 那正是本次要修的断点，不能用一个默认值把它藏回去。
        self._readiness = readiness

    def materialize(
        self,
        scope: ProjectScope,
        import_id: UUID,
        *,
        scene_labels: tuple[str, ...] = (),
    ) -> MaterializeResult:
        job = self._imports.get(scope, import_id)
        if job is None:
            raise DomainValidationError("导入任务不存在或不属于当前项目")

        grouped = _group_by_episode(self._assets.list_by_import(scope, import_id, limit=1000))
        if not grouped:
            raise DomainValidationError("该导入没有任何媒体资产，无法生成 Episode")

        created: list[MaterializedEpisode] = []
        refreshed: list[MaterializedEpisode] = []
        skipped: list[str] = []
        for episode_key in sorted(grouped):
            members = grouped[episode_key]
            command = _command_for(
                episode_key,
                members,
                scene_labels=scene_labels,
                collection_task_id=job.collection_task_id,
            )
            # 幂等判重必须**在创建之前**：EpisodeService.create 还带一层
            # "同一 RawAsset 的时间区间不得被重复占用"检查，它会先于 code
            # 唯一性触发，抛 EpisodeOverlapConflictError。靠捕获异常判重会
            # 把"重复物化"误报成"时间区间冲突"。
            existing = self._episode_repo.get_by_code(scope, command.code)
            if existing is not None:
                skipped.append(episode_key)
                # 重试语义：Episode 已存在不代表它就绪（上一次可能因事实不全被拒）。
                # 再判一次是幂等的：已就绪的返回 SKIPPED，不会被重复写。
                refreshed.append(
                    MaterializedEpisode(
                        episode_id=existing.episode_id,
                        episode_key=episode_key,
                        raw_asset_ids=command.raw_asset_ids,
                        readiness=self._auto_ready(scope, existing.episode_id),
                    )
                )
                continue
            try:
                episode = self._episodes.create(scope, command)
            except EpisodeConflictError:
                # 并发下的兜底：另一个进程刚插入同 code。
                skipped.append(episode_key)
                continue
            created.append(
                MaterializedEpisode(
                    episode_id=episode.episode_id,
                    episode_key=episode_key,
                    raw_asset_ids=command.raw_asset_ids,
                    readiness=self._auto_ready(scope, episode.episode_id),
                )
            )
        return MaterializeResult(
            import_id=import_id,
            created=tuple(created),
            skipped_episode_keys=tuple(skipped),
            refreshed=tuple(refreshed),
        )

    def _auto_ready(
        self,
        scope: ProjectScope,
        episode_id: UUID,
    ) -> ReadinessOutcome:
        """尝试自动就绪；被拒是**正常结果**，不得让它变成导入失败。

        只有"判不了"（判定层登记的阻塞项）会走到 ``BLOCKED``；真正的异常
        （数据库错误、领域校验缺陷）照常抛出——那是缺陷，不能吞。
        并发写入由用例内部处理（回滚并返回 ``SKIPPED``）。
        """
        return self._readiness.auto_ready(
            scope,
            episode_id,
            provenance_source=IMPORT_PIPELINE_ID,
        )


def _group_by_episode(assets: list[MediaAsset]) -> dict[str, list[MediaAsset]]:
    grouped: dict[str, list[MediaAsset]] = {}
    for asset in assets:
        if asset.raw_asset_id is None:
            # 历史导入产物没有 RawAsset，不能用于创建 Episode（Episode 强制
            # 引用 RawAsset 且要求状态 VALID）。如实跳过而不是伪造引用。
            continue
        grouped.setdefault(asset.episode_key, []).append(asset)
    return grouped


def _command_for(
    episode_key: str,
    members: list[MediaAsset],
    *,
    scene_labels: tuple[str, ...],
    collection_task_id: UUID | None = None,
) -> CreateEpisodeCommand:
    raw_asset_ids = _unique([asset.raw_asset_id for asset in members])
    start_ns, end_ns = _time_range(members)
    first = members[0]
    return CreateEpisodeCommand(
        # 物化出的 Episode 继承导入的采集任务归属：命令本来就支持 task_id，
        # 只是这条链路一直没传，于是按任务统计永远查不到导入来的数据。
        task_id=collection_task_id,
        raw_asset_ids=raw_asset_ids,
        segmentation=Segmentation(
            method="RULE",
            rule_version=IMPORT_SEGMENTATION_RULE_VERSION,
            start_time_ns=start_ns,
            end_time_ns=end_ns,
            # 只传该规则 schema 允许的字段：episode-boundary 是
            # additionalProperties=False，多传会被参数校验拒绝。episode_key
            # 已由 Episode 的 code 承载，不需要塞进规则参数。
            rule_parameters={
                "boundary_source": IMPORT_SEGMENTATION_BOUNDARY_SOURCE,
            },
        ),
        time_base=dict(IMPORT_TIME_BASE),
        # 空快照：数据集本身不含标定信息，不伪造（详见模块文档）。
        calibration_snapshot=(),
        canonical_schema_version=IMPORT_CANONICAL_SCHEMA_VERSION,
        pipeline_id=IMPORT_PIPELINE_ID,
        pipeline_version=IMPORT_PIPELINE_VERSION,
        code=f"EP-{episode_key}"[:64],
        scene_labels=scene_labels,
        # instruction 是数据集的自然语言任务描述；导入时已写进
        # RawAsset.raw_metadata，MediaAsset 上没有该列，这里退到 task_name。
        language_instruction=first.task_name or None,
    )


def _time_range(members: list[MediaAsset]) -> tuple[int, int]:
    """取该组全部机位的并集区间（纳秒）。

    机位各自带 ``excerpt_start_seconds`` / ``excerpt_seconds``；缺失时按 0 起点
    与 1 秒兜底——Episode 要求 ``start < end``，而公开数据集不保证每行都有
    这两个字段。兜底值不写入任何"看起来精确"的字段，只是让区间合法。
    """
    starts: list[int] = []
    ends: list[int] = []
    for asset in members:
        start_seconds = asset.excerpt_start_seconds or 0.0
        duration_seconds = asset.excerpt_seconds or 1.0
        starts.append(int(start_seconds * _NS))
        ends.append(int((start_seconds + duration_seconds) * _NS))
    return min(starts), max(ends)


def _unique(values: list[UUID | None]) -> tuple[UUID, ...]:
    seen: dict[UUID, None] = {}
    for value in values:
        if value is not None:
            seen[value] = None
    return tuple(seen)
