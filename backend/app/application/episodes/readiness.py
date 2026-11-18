"""Episode 自动就绪用例：从**已落库事实**生成 Canonical Manifest 并置 READY。

**它在链路里的位置**

``ImportEpisodeMaterializer``（导入产物 → Episode 的 canonicalization 步骤，
``POST /data-source-imports/{id}/episodes`` 与任何调用它的进程都用同一条路径）
在创建 Episode 之后调用本用例。此前这一步是人工的：调用方必须自己组装
Manifest 再 ``POST /episodes/{id}/actions/ready``，平台没有任何组件会生成它，
于是「导入 → Episode → 标注 → 数据集」停在 ``PENDING``。

**职责边界（复用优先）**

- 判定与 Manifest 的**形状**在领域层（``domain/episodes/readiness.py``，纯函数）；
- Manifest 的身份校验、Hash 与 ``manifest_key`` 全部复用既有领域实现
  （``Episode.mark_ready`` / ``build_manifest_key`` / ``compute_manifest_sha256``），
  本模块**不重算**任何 Hash，也不另写状态迁移规则——迁移仍由
  ``episode_target_status`` 决定（PENDING → PROCESSING → READY 与 HTTP
  ``ready`` 动作完全一致）；
- 本模块只做编排：读事实 → 调判定 → 落库 → 写审计与事件 → 提交。

**两种结果必须可区分**

- 就绪成功：Episode ``READY``，落 ``manifest_key`` / ``manifest_sha256`` /
  ``frame_count``，清空阻塞原因；
- 就绪被拒：Episode **保持 PENDING**，把原因写进 ``readiness_blockers``
  并记录审计事件。**这不是导入失败**——导入任务仍是 ``SUCCEEDED``，
  调用方不得据此回滚导入，也不得让异常逃逸成 500。

**幂等与重试**

- 非 ``PENDING`` 的 Episode 直接返回 ``SKIPPED``（已就绪的不重复写入，
  已驳回/失效的不复活）；
- 重复物化时对已存在的 Episode 再判一次：数据补齐后可以真的就绪，
  已就绪的仍然 ``SKIPPED``；
- 并发下 ``save`` 的乐观锁会抛 ``VersionConflictError``：回滚本次半成品
  （避免把 Episode 留在 ``PROCESSING``）并作为 ``SKIPPED`` 记一条并发说明。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.application.collection.audit import record_change
from app.application.collection.ports import AuditRepository, OutboxRepository
from app.application.episodes.ports import (
    EpisodeRawAssetRepository,
    EpisodeRepository,
)
from app.application.ingestion.ports import RawAssetRepository
from app.domain.episodes.enums import EpisodeStatus
from app.domain.episodes.errors import ResourceNotFoundError, VersionConflictError
from app.domain.episodes.models import Episode, EpisodeRawAsset
from app.domain.episodes.readiness import evaluate_readiness
from app.domain.episodes.readiness_facts import EpisodeManifestFacts, EpisodeStream
from app.domain.episodes.readiness_result import (
    ReadinessBlocker,
    ReadinessDecision,
    ReadinessNotice,
)
from app.domain.episodes.validation import build_manifest_key
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.models import RawAsset

logger = logging.getLogger("ddp.application.episodes.readiness")

# R-13：事件类型必须是字面量（AST 提取脚本抓不到 f-string）。
# 审计表 reason 列宽；摘要按此截断，超长会在 PostgreSQL 上直接失败。
AUDIT_REASON_MAX = 1000

READINESS_ACTION_EVENTS: dict[str, str] = {
    "system-start": "ingestion.episode.system-start",
    "system-ready": "ingestion.episode.system-ready",
    "readiness-blocked": "ingestion.episode.readiness-blocked",
}


class ReadinessUnitOfWork(Protocol):
    """就绪用例需要的事务端口。

    比 ``application/episodes/ports.UnitOfWork`` 多一个 ``rollback``：本用例是
    **两段写入**（PENDING → PROCESSING → READY，DB 触发器只登记了这个迁移白名单），
    第二段可能因乐观锁失败；不显式回滚会把 Episode 留在 ``PROCESSING``——
    那是个没有出口的状态（只能 system-ready/system-fail/invalidate）。
    """

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class ReadinessStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class ReadinessOutcome:
    """自动就绪的结果；``SKIPPED`` 必须带 ``note`` 说明为何跳过。"""

    episode_id: UUID
    status: ReadinessStatus
    episode_status: str
    blockers: tuple[ReadinessBlocker, ...] = ()
    notices: tuple[ReadinessNotice, ...] = ()
    frame_count: int | None = None
    manifest_sha256: str | None = None
    note: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.status is ReadinessStatus.READY


class EpisodeReadinessService:
    def __init__(
        self,
        episodes: EpisodeRepository,
        episode_assets: EpisodeRawAssetRepository,
        raw_assets: RawAssetRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: ReadinessUnitOfWork,
    ) -> None:
        self._episodes = episodes
        self._episode_assets = episode_assets
        self._raw_assets = raw_assets
        self._audit = audit
        self._outbox = outbox
        self._uow = uow

    def evaluate(
        self,
        scope: ProjectScope,
        episode_id: UUID,
    ) -> ReadinessDecision:
        """只读判定：按已落库事实算能不能就绪，不写任何状态。

        HTTP ``ready`` 动作复用它做门禁——调用方提交的 Manifest 再完整，也不能
        绕过"库内事实是否足够"这一关（帧率、多机位标定、时间基准）。
        """
        episode = self._episodes.get(scope, episode_id)
        if episode is None:
            raise ResourceNotFoundError("Episode", episode_id)
        return evaluate_readiness(
            EpisodeManifestFacts.from_episode(episode),
            self._streams(scope, episode_id),
            provenance_source="episode_ready_action",
        )

    def auto_ready(
        self,
        scope: ProjectScope,
        episode_id: UUID,
        *,
        provenance_source: str,
        request_id: str | None = None,
    ) -> ReadinessOutcome:
        now = _utcnow()
        episode = self._episodes.get(scope, episode_id)
        if episode is None:
            raise ResourceNotFoundError("Episode", episode_id)

        if episode.status is not EpisodeStatus.PENDING:
            # 幂等：已就绪/已推进的 Episode 不再重复写入。
            return ReadinessOutcome(
                episode_id=episode_id,
                status=ReadinessStatus.SKIPPED,
                episode_status=episode.status.value,
                blockers=episode.readiness_blockers,
                frame_count=episode.frame_count,
                manifest_sha256=episode.manifest_sha256,
                note=f"Episode 当前状态为 {episode.status.value}，无需自动就绪",
            )

        streams = self._streams(scope, episode_id)
        decision = evaluate_readiness(
            EpisodeManifestFacts.from_episode(episode),
            streams,
            provenance_source=provenance_source,
        )

        try:
            if not decision.is_ready:
                return self._record_blocked(
                    scope,
                    episode,
                    decision_blockers=decision.blockers,
                    notices=decision.notices,
                    frame_count=decision.frame_count,
                    request_id=request_id,
                    now=now,
                )
            return self._apply_ready(
                scope,
                episode,
                manifest=decision.require_manifest(),
                frame_count=decision.require_frame_count(),
                notices=decision.notices,
                request_id=request_id,
                now=now,
            )
        except VersionConflictError:
            # 并发：另一个进程已推进同一 Episode。回滚本次可能写了一半的状态
            # （尤其 PENDING → PROCESSING），否则会留下无出口的 PROCESSING。
            self._uow.rollback()
            logger.info(
                "自动就绪遇到并发写入，跳过",
                extra={"episode_id": str(episode_id), "resource": "EPISODE"},
            )
            current = self._episodes.get(scope, episode_id)
            return ReadinessOutcome(
                episode_id=episode_id,
                status=ReadinessStatus.SKIPPED,
                episode_status="UNKNOWN" if current is None else current.status.value,
                note="并发写入：另一个进程已推进该 Episode，本次不重复写入",
            )

    def _record_blocked(
        self,
        scope: ProjectScope,
        episode: Episode,
        *,
        decision_blockers: tuple[ReadinessBlocker, ...],
        notices: tuple[ReadinessNotice, ...],
        frame_count: int | None,
        request_id: str | None,
        now: datetime,
    ) -> ReadinessOutcome:
        """登记拒绝原因；Episode 保持 PENDING，导入本身不受影响。"""
        blocked = episode.record_readiness_blocked(blockers=decision_blockers, now=now)
        saved = self._episodes.save(scope, blocked, expected_version=episode.version)
        self._record_action(
            scope,
            episode,
            saved,
            "readiness-blocked",
            request_id,
            now,
            reason=_summarize(decision_blockers),
            details={
                "blockers": [item.as_dict() for item in decision_blockers],
                "notices": [item.as_dict() for item in notices],
                "frame_count": frame_count,
            },
        )
        self._uow.commit()
        logger.warning(
            "Episode 自动就绪被拒绝",
            extra={
                "episode_id": str(episode.episode_id),
                "blockers": [str(item.code) for item in decision_blockers],
            },
        )
        return ReadinessOutcome(
            episode_id=episode.episode_id,
            status=ReadinessStatus.BLOCKED,
            episode_status=saved.status.value,
            blockers=decision_blockers,
            notices=notices,
            frame_count=frame_count,
            note=_summarize(decision_blockers),
        )

    def _apply_ready(
        self,
        scope: ProjectScope,
        episode: Episode,
        *,
        manifest: Mapping[str, object],
        frame_count: int,
        notices: tuple[ReadinessNotice, ...],
        request_id: str | None,
        now: datetime,
    ) -> ReadinessOutcome:
        """与 HTTP ``actions/ready`` 相同的两段迁移：PENDING → PROCESSING → READY。"""
        if episode.status is EpisodeStatus.PENDING:
            started = episode.start_processing(now)
            saved_start = self._episodes.save(scope, started, expected_version=episode.version)
            self._record_action(scope, episode, saved_start, "system-start", request_id, now)
            episode = saved_start
        ready = episode.mark_ready(
            manifest_key=build_manifest_key(
                episode.project_id,
                episode.episode_id,
                episode.episode_version,
            ),
            manifest=manifest,
            frame_count=frame_count,
            now=now,
        )
        saved = self._episodes.save(scope, ready, expected_version=episode.version)
        self._record_action(
            scope,
            episode,
            saved,
            "system-ready",
            request_id,
            now,
            details={
                "notices": [item.as_dict() for item in notices],
                "frame_count": saved.frame_count,
            },
        )
        self._uow.commit()
        logger.info(
            "Episode 自动就绪完成",
            extra={
                "episode_id": str(saved.episode_id),
                "manifest_sha256": saved.manifest_sha256,
                "frame_count": saved.frame_count,
            },
        )
        return ReadinessOutcome(
            episode_id=saved.episode_id,
            status=ReadinessStatus.READY,
            episode_status=saved.status.value,
            notices=notices,
            frame_count=saved.frame_count,
            manifest_sha256=saved.manifest_sha256,
        )

    def _streams(self, scope: ProjectScope, episode_id: UUID) -> tuple[EpisodeStream, ...]:
        """把 Episode-RawAsset 关联与 RawAsset 事实转成判定输入。"""
        links = self._episode_assets.list_for_episode(scope, episode_id)
        streams: list[EpisodeStream] = []
        for link in links:
            asset = self._raw_assets.get(scope, link.asset_id)
            streams.append(_stream_from(link, asset))
        return tuple(streams)

    def _record_action(
        self,
        scope: ProjectScope,
        before: Episode,
        after: Episode,
        action: str,
        request_id: str | None,
        now: datetime,
        *,
        reason: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        """审计 + outbox 同事务写入；事件类型必须是登记过的字面量。"""
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="EPISODE",
            resource_id=after.episode_id,
            action=action,
            from_status=before.status.value,
            to_status=after.status.value,
            reason=reason,
            request_id=request_id,
            details={
                "episode_lineage_id": str(after.episode_lineage_id),
                "episode_version": after.episode_version,
                **dict(details or {}),
            },
            event_type=READINESS_ACTION_EVENTS[action],
            event_payload={
                "episode_id": str(after.episode_id),
                "from_status": before.status.value,
                "to_status": after.status.value,
                "trigger": "auto_readiness",
            },
            occurred_at=now,
        )


def _stream_from(link: EpisodeRawAsset, asset: RawAsset | None) -> EpisodeStream:
    """一条流的判定事实；资产不可取时如实标记 ``usable=False``。"""
    asset_id = link.asset_id
    sha256_snapshot = link.sha256_snapshot
    size_bytes_snapshot = link.size_bytes_snapshot
    if asset is None:
        return EpisodeStream(
            asset_id=asset_id,
            sha256=sha256_snapshot,
            size_bytes=size_bytes_snapshot,
            sha256_snapshot=sha256_snapshot,
            size_bytes_snapshot=size_bytes_snapshot,
            usable=False,
        )
    metadata = asset.raw_metadata if isinstance(asset.raw_metadata, Mapping) else {}
    return EpisodeStream(
        asset_id=asset.asset_id,
        sha256=asset.sha256,
        size_bytes=asset.size_bytes,
        camera=_optional_text(metadata.get("camera")),
        object_key=asset.object_key,
        content_type=asset.content_type,
        format=str(getattr(asset.format, "value", asset.format)),
        source_fps=_optional_fps(metadata.get("source_fps")),
        sha256_snapshot=sha256_snapshot,
        size_bytes_snapshot=size_bytes_snapshot,
        usable=True,
        status=str(getattr(asset.status, "value", asset.status)),
    )


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _optional_fps(value: object) -> float | None:
    """声明帧率；非数值/布尔/非正值一律视为"未声明"（不猜测）。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    rate = float(value)
    return rate if rate > 0 else None


def _summarize(blockers: tuple[ReadinessBlocker, ...]) -> str:
    """审计 ``reason`` 列是 String(1000)，摘要必须落在列宽之内。"""
    summary = " | ".join(f"{item.code}: {item.message}" for item in blockers)
    return summary[:AUDIT_REASON_MAX]


def _utcnow() -> datetime:
    return datetime.now(UTC)
