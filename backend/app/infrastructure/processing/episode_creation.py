"""把处理运行推导出的边界落成 Episode（V31-018 的 ``EpisodeCreator`` 实现）。

**为什么调用 ``EpisodeService.create`` 而不是直接写仓储**：创建 Episode 不只是
插一行——还要落 ``episode_raw_assets`` 快照、登记血缘、写审计与 outbox 事件、
校验时间基准与资产状态。这些规则只允许存在一处权威实现（应用用例），在这里
复制一遍迟早漂移。

**幂等靠稳定 code，不靠幂等键**：``code`` 由 ``(asset_id, start_time_ns)``
确定性生成，重跑先 ``get_by_code`` 命中即返回"已存在"。并发下另一个 Worker
可能刚插入同 code，``create`` 抛 ``ConflictError``；此时重读一次按已存在处理，
重读仍为空才向上抛——不把真实冲突当成幂等成功。

**时间基准是 DEGRADED**：MP4 探测给出的是文件内相对时长，没有墙钟与同步证据。
按 ``domain.episodes.validation`` 的口径，DEGRADED 允许建 Episode、但自动就绪
会因"时间同步未验证"被挡住——这正是"先如实登记、再由门禁决定能否进数据集"。
"""

from __future__ import annotations

from uuid import UUID

from app.application.episodes import EpisodeService
from app.application.episodes.commands import CreateEpisodeCommand
from app.application.episodes.ports import EpisodeRepository
from app.application.processing.segmentation_stage import EpisodeBoundaryRequest
from app.application.processing.stages import StageFailure
from app.domain.episodes.enums import SegmentationMethod
from app.domain.episodes.errors import ConflictError, DomainValidationError
from app.domain.episodes.models import Segmentation
from app.domain.episodes.segmentation_rules import EPISODE_BOUNDARY_RULE_VERSION
from app.domain.identity.models import ProjectScope

#: 处理运行产出的 Episode 统一记这个 pipeline_id；具体版本取运行自身的版本，
#: 数据集与血缘据此回答"这条 Episode 是哪次处理跑出来的"。
PROCESSING_PIPELINE_ID = "processing"

#: 边界来自元数据阶段对容器的解析（``probe_mp4`` 的 duration），不是人工输入。
BOUNDARY_SOURCE = "PARSER"

#: 处理链路建出的 Episode 时间基准：文件内相对时长，无墙钟同步证据。
UNVERIFIED_TIME_BASE: dict[str, object] = {
    "time_domain": "UNKNOWN",
    "clock_source": "UNKNOWN",
    "sync_method": "NONE",
    "sync_status": "DEGRADED",
}


def stable_episode_code(asset_id: UUID, start_time_ns: int) -> str:
    """``(asset_id, start_time_ns)`` 的确定性 code，重跑必然得到同一个。"""
    return f"EP-{asset_id.hex[:16]}-{start_time_ns}"


class ProcessingEpisodeCreator:
    """``EpisodeCreator`` 端口的生产实现。"""

    def __init__(
        self,
        *,
        service: EpisodeService,
        episodes: EpisodeRepository,
        canonical_schema_version: str,
    ) -> None:
        self._service = service
        self._episodes = episodes
        self._canonical_schema_version = canonical_schema_version

    def create_from_boundary(
        self,
        scope: ProjectScope,
        request: EpisodeBoundaryRequest,
        *,
        run_pipeline_version: str,
    ) -> tuple[UUID, bool]:
        code = stable_episode_code(request.asset_id, request.start_time_ns)
        existing = self._episodes.get_by_code(scope, code)
        if existing is not None:
            return existing.episode_id, False

        command = CreateEpisodeCommand(
            raw_asset_ids=(request.asset_id,),
            segmentation=Segmentation(
                method=SegmentationMethod.RULE,
                rule_version=EPISODE_BOUNDARY_RULE_VERSION,
                start_time_ns=request.start_time_ns,
                end_time_ns=request.end_time_ns,
                rule_parameters={"boundary_source": BOUNDARY_SOURCE},
            ),
            time_base=dict(UNVERIFIED_TIME_BASE),
            calibration_snapshot=(),
            canonical_schema_version=self._canonical_schema_version,
            pipeline_id=PROCESSING_PIPELINE_ID,
            pipeline_version=run_pipeline_version,
            code=code,
        )
        try:
            created = self._service.create(scope, command)
        except ConflictError as exc:
            # 并发重跑：另一 Worker 已按同 code 建好。重读确认真的是同一条
            # Episode，避免把"code 冲突但内容不同"的异常当成幂等成功。
            raced = self._episodes.get_by_code(scope, code)
            if raced is None:
                raise StageFailure(
                    "segmentation_episode_conflict",
                    f"Episode code 冲突且无法按 code 回读：{code}",
                    retryable=False,
                ) from exc
            return raced.episode_id, False
        except DomainValidationError as exc:
            # 资产状态不可用、规则参数非法、时间基准非法等：数据问题，重试不会变好。
            raise StageFailure(
                "segmentation_episode_invalid",
                f"无法按边界创建 Episode：{exc}",
                retryable=False,
            ) from exc
        return created.episode_id, True


__all__ = [
    "BOUNDARY_SOURCE",
    "PROCESSING_PIPELINE_ID",
    "UNVERIFIED_TIME_BASE",
    "ProcessingEpisodeCreator",
    "stable_episode_code",
]
