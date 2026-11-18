"""Episode 候选目录：把阶段 2/3 权威数据投影为选择候选。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.domain.datasets.selection import EpisodeCandidate, SelectionFilters
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_results import (
    AnnotationRecord,
    AnnotationSubmission,
)
from app.infrastructure.db.models.episodes import EpisodeModel, EpisodeRawAssetModel
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.repositories._dataset_sql import as_utc

_APPROVED_SUBMISSION_STATUSES = ("APPROVED", "RESOLVED_APPROVED")
_INVALID_CALIBRATION_STATUSES = frozenset(
    {"REVOKED", "EXPIRED", "INVALID", "INVALIDATED", "REJECTED", "FAILED"}
)


def load_episode_assets(
    session: Session,
    scope: ProjectScope,
    episode_ids: Sequence[UUID],
) -> dict[UUID, list[tuple[EpisodeRawAssetModel, RawAssetModel]]]:
    """一次查询取回多个 Episode 的原始资产关联与资产行，按 Episode 分组。

    "Episode 指向哪些真实文件"这件事只在这里定义一次：选择候选与导出媒体索引
    都复用它，避免两边各写一份 join 后出现口径差异。调用方必须在服务端传入
    可信的 ``scope``——项目过滤在此强制，不依赖调用方自己拼条件。
    """
    if not episode_ids:
        return {}
    rows = session.execute(
        select(EpisodeRawAssetModel, RawAssetModel)
        .join(
            RawAssetModel,
            and_(
                RawAssetModel.project_id == EpisodeRawAssetModel.project_id,
                RawAssetModel.id == EpisodeRawAssetModel.asset_id,
            ),
        )
        .where(
            EpisodeRawAssetModel.project_id == scope.project_id,
            EpisodeRawAssetModel.episode_id.in_(list(episode_ids)),
        )
        .order_by(EpisodeRawAssetModel.episode_id, EpisodeRawAssetModel.asset_id)
    )
    grouped: dict[UUID, list[tuple[EpisodeRawAssetModel, RawAssetModel]]] = defaultdict(list)
    for link, asset in rows:
        grouped[link.episode_id].append((link, asset))
    return grouped


class SqlEpisodeCandidateDirectory:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_candidates(
        self,
        scope: ProjectScope,
        filters: SelectionFilters,
    ) -> tuple[EpisodeCandidate, ...]:
        statement = select(EpisodeModel).where(EpisodeModel.project_id == scope.project_id)
        if filters.episode_ids:
            statement = statement.where(EpisodeModel.id.in_(filters.episode_ids))
        episodes = list(self._session.scalars(statement.order_by(EpisodeModel.id)))
        if not episodes:
            return ()
        episode_ids = [item.id for item in episodes]
        assets = self._load_assets(scope, episode_ids)
        annotations = self._load_approved_annotations(scope, episode_ids)
        now = datetime.now(UTC)
        return tuple(
            self._build_candidate(
                episode,
                assets.get(episode.id, []),
                annotations.get(episode.id),
                now=now,
            )
            for episode in episodes
        )

    def _load_assets(
        self,
        scope: ProjectScope,
        episode_ids: list[UUID],
    ) -> dict[UUID, list[tuple[EpisodeRawAssetModel, RawAssetModel]]]:
        return load_episode_assets(self._session, scope, episode_ids)

    def _load_approved_annotations(
        self,
        scope: ProjectScope,
        episode_ids: list[UUID],
    ) -> dict[UUID, tuple[AnnotationRecord, AnnotationSubmission]]:
        rows = self._session.execute(
            select(AnnotationRecord, AnnotationSubmission)
            .join(
                AnnotationSubmission,
                and_(
                    AnnotationSubmission.project_id == AnnotationRecord.project_id,
                    AnnotationSubmission.id == AnnotationRecord.submission_id,
                ),
            )
            .where(
                AnnotationRecord.project_id == scope.project_id,
                AnnotationRecord.episode_id.in_(episode_ids),
                AnnotationRecord.status == "APPROVED",
                AnnotationSubmission.status.in_(_APPROVED_SUBMISSION_STATUSES),
            )
            .order_by(
                AnnotationRecord.episode_id,
                AnnotationRecord.annotation_version.desc(),
                AnnotationRecord.id.desc(),
            )
        )
        result: dict[UUID, tuple[AnnotationRecord, AnnotationSubmission]] = {}
        for record, submission in rows:
            result.setdefault(record.episode_id, (record, submission))
        return result

    def _build_candidate(
        self,
        episode: EpisodeModel,
        assets: list[tuple[EpisodeRawAssetModel, RawAssetModel]],
        annotation: tuple[AnnotationRecord, AnnotationSubmission] | None,
        *,
        now: datetime,
    ) -> EpisodeCandidate:
        raw_asset_ids = tuple(link.asset_id for link, _asset in assets)
        hashes: list[str] = []
        statuses: list[str] = []
        captured: list[datetime] = []
        total_bytes = 0
        for link, asset in assets:
            hashes.append(link.sha256_snapshot)
            if link.sha256_snapshot != asset.sha256:
                statuses.append("HASH_CHANGED")
            else:
                statuses.append(asset.status)
            captured_at = as_utc(asset.captured_at)
            if captured_at is not None:
                captured.append(captured_at)
            total_bytes += link.size_bytes_snapshot
        calibration_valid, calibration_issue, calibration_missing = _calibration_state(
            episode.calibration_snapshot,
            now=now,
        )
        time_base = episode.time_base or {}
        sync_status = time_base.get("sync_status")
        record, submission = annotation if annotation is not None else (None, None)
        return EpisodeCandidate(
            episode_id=episode.id,
            episode_lineage_id=episode.episode_lineage_id,
            episode_version=episode.episode_version,
            code=episode.code,
            status=episode.status,
            quality_status=episode.quality_status,
            annotation_status=episode.annotation_status,
            manifest_sha256=episode.manifest_sha256,
            scene_labels=tuple(episode.scene_labels or ()),
            robot_id=episode.robot_id,
            device_ids=tuple(UUID(item) for item in (episode.device_ids or [])),
            batch_id=episode.batch_id,
            task_id=episode.task_id,
            start_time_ns=episode.start_time_ns,
            end_time_ns=episode.end_time_ns,
            duration_ns=episode.duration_ns,
            captured_at=min(captured) if captured else None,
            updated_at=as_utc(episode.updated_at),
            time_sync_status=sync_status if isinstance(sync_status, str) else None,
            calibration_valid=calibration_valid,
            calibration_issue=calibration_issue,
            calibration_missing=calibration_missing,
            annotation_submission_id=record.submission_id if record is not None else None,
            annotation_version=record.annotation_version if record is not None else None,
            annotation_record_digest=record.record_sha256 if record is not None else None,
            annotation_submission_status=submission.status if submission is not None else None,
            schema_id=record.schema_id if record is not None else None,
            schema_version=record.schema_version if record is not None else None,
            schema_sha256=submission.schema_sha256 if submission is not None else None,
            submission_schema_id=submission.schema_id if submission is not None else None,
            submission_schema_version=(
                submission.schema_version if submission is not None else None
            ),
            raw_asset_ids=raw_asset_ids,
            raw_asset_hashes=tuple(hashes),
            raw_asset_statuses=tuple(statuses),
            total_bytes=total_bytes,
        )


def _calibration_state(
    snapshot: object,
    *,
    now: datetime,
) -> tuple[bool, str | None, bool]:
    """返回 ``(是否有效, 问题描述, 是否缺失)``。

    第三个返回值区分"**没有**标定"与"有标定但无效"：前者是数据本身的
    属性（公开数据集常常不带标定），后者是质量问题。数据集侧据此产出
    ``CALIBRATION_MISSING`` / ``CALIBRATION_INVALID`` 两个不同排除码，
    否则两个码会塌缩成一个，调用方无法判断该不该去补标定。
    """
    if not isinstance(snapshot, list) or not snapshot:
        return False, "缺少有效标定快照", True
    for item in snapshot:
        if not isinstance(item, dict):
            return False, "标定快照结构非法", False
        status = item.get("status")
        if isinstance(status, str) and status.upper() in _INVALID_CALIBRATION_STATUSES:
            return False, f"标定状态 {status} 不可用", False
        valid_until = item.get("valid_until") or item.get("expires_at")
        if valid_until is not None:
            try:
                expiry = (
                    valid_until
                    if isinstance(valid_until, datetime)
                    else datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
                )
            except ValueError:
                return False, "标定有效期格式非法", False
            if expiry.tzinfo is None or expiry.utcoffset() is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry <= now:
                return False, "标定已过期", False
    return True, None, False


class SqlDatasetUpstreamDirectory:
    """数据集版本的上游指纹读取（只读）：Episode 状态与 Manifest Hash。

    有效性巡检只需要"这个 Episode 现在是什么状态、Manifest 是否变了"，
    不读业务实体，避免把整条 Episode 聚合拉进巡检循环。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def episode_states(
        self,
        scope: ProjectScope,
        episode_ids: list[UUID],
    ) -> dict[UUID, tuple[str, str | None, int]]:
        if not episode_ids:
            return {}
        rows = self._session.execute(
            select(
                EpisodeModel.id,
                EpisodeModel.status,
                EpisodeModel.manifest_sha256,
                EpisodeModel.episode_version,
            ).where(
                EpisodeModel.project_id == scope.project_id,
                EpisodeModel.id.in_(episode_ids),
            )
        ).all()
        return {row[0]: (str(row[1]), row[2], int(row[3])) for row in rows}

    def episode_manifest_keys(
        self,
        scope: ProjectScope,
        episode_ids: list[UUID],
    ) -> dict[UUID, str | None]:
        """Episode 当前指向的 Canonical Manifest 对象键（可能未就绪为 None）。

        来源可达性探测只需要这一列；调用方必须先用 ``episode_states`` 排除
        已失效/已删除的 Episode，再把可探测的键交给对象存储做 HEAD。
        """
        if not episode_ids:
            return {}
        rows = self._session.execute(
            select(EpisodeModel.id, EpisodeModel.manifest_key).where(
                EpisodeModel.project_id == scope.project_id,
                EpisodeModel.id.in_(episode_ids),
            )
        ).all()
        return {row[0]: row[1] for row in rows}

    def episode_raw_asset_keys(
        self,
        scope: ProjectScope,
        episode_ids: list[UUID],
    ) -> dict[UUID, tuple[str, ...]]:
        """Episode 通过 ``episode_raw_assets`` 关联的原始资产对象键（批量）。

        复用 :func:`load_episode_assets`——"Episode 指向哪些真实文件"只在
        那里定义一次，避免巡检另写一份 join 后和选择/导出产生口径差异。
        一次查询取回全部 Episode 的关联，不做逐 Episode 查询；项目过滤在
        SQL 里强制，不依赖调用方拼条件。没有关联的 Episode 不出现（"没有
        引用"不是"对象缺失"）。
        """
        grouped = load_episode_assets(self._session, scope, episode_ids)
        return {
            episode_id: tuple(sorted({asset.object_key for _link, asset in rows}))
            for episode_id, rows in grouped.items()
        }
