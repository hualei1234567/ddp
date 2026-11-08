"""样本选择纯领域逻辑：门禁、去重、分层与理由记录。

输入是 Episode 只读投影；输出是待持久化的 ``SampleEpisodeRef`` 与计数。
不访问数据库、不依赖 Web/ORM。分层策略仅支持契约中可解释的显式条件，
未知条件不会静默匹配。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app.domain.annotation.enums import (
    DedupKey,
    SampleRole,
    SampleStrategy,
)
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.sample_set import SampleEpisodeRef

_ALLOWED_STATUSES = frozenset({"ACCEPTED", "COMPLETED"})
_ALLOWED_QUALITY = frozenset({"PASS", "WARN"})
_STRATUM_KEYS = frozenset({"scene_label", "robot_id", "device_id", "sensor_id", "quality_status"})


@dataclass(frozen=True, slots=True)
class SelectionEpisode:
    episode_id: UUID
    episode_lineage_id: UUID
    episode_version: int
    status: str
    quality_status: str
    manifest_sha256: str | None
    annotation_status: str = "NOT_STARTED"
    scene_labels: tuple[str, ...] = ()
    robot_id: UUID | None = None
    device_ids: tuple[UUID, ...] = ()
    sensor_ids: tuple[UUID, ...] = ()
    captured_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SelectionResult:
    episode_refs: tuple[SampleEpisodeRef, ...]
    candidate_count: int
    excluded_count: int
    deduplicated_count: int
    exclusion_summary: Mapping[str, object] = field(default_factory=dict)


def select_episodes(
    episodes: Sequence[SelectionEpisode],
    *,
    project_id: UUID,
    sample_set_id: UUID,
    filters: Mapping[str, object],
    strata: Sequence[Mapping[str, object]],
    strategy: SampleStrategy | str,
    dedup_key: DedupKey | str,
    warn_confirmed: bool,
    now: datetime,
) -> SelectionResult:
    strategy_value = SampleStrategy(strategy)
    dedup_value = DedupKey(dedup_key)
    include_ids = _uuid_filter(filters.get("include_episode_ids"))
    exclude_ids = _uuid_filter(filters.get("exclude_episode_ids"))
    scene_filter = _string_filter(filters.get("scene_labels"))
    robot_filter = _uuid_filter(filters.get("robot_ids"))
    device_filter = _uuid_filter(filters.get("device_ids"))
    sensor_filter = _uuid_filter(filters.get("sensor_ids"))
    captured_from = _optional_datetime(filters.get("captured_from"))
    captured_to = _optional_datetime(filters.get("captured_to"))
    not_annotated = bool(filters.get("not_annotated", False))

    reasons: dict[str, int] = {}
    eligible: list[SelectionEpisode] = []
    for episode in episodes:
        reason = _ineligibility_reason(
            episode,
            warn_confirmed=warn_confirmed,
            include_ids=include_ids,
            exclude_ids=exclude_ids,
            scene_filter=scene_filter,
            robot_filter=robot_filter,
            device_filter=device_filter,
            sensor_filter=sensor_filter,
            captured_from=captured_from,
            captured_to=captured_to,
            not_annotated=not_annotated,
        )
        if reason is not None:
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        eligible.append(episode)

    deduped, duplicate_count = _deduplicate(eligible, dedup_value)
    selected, unselected_count = _apply_strata(
        deduped,
        strata=strata if strategy_value is SampleStrategy.STRATIFIED else (),
    )
    if unselected_count:
        reasons["stratum_target_reached"] = unselected_count

    refs = tuple(
        SampleEpisodeRef(
            project_id=project_id,
            sample_set_id=sample_set_id,
            episode_id=episode.episode_id,
            episode_lineage_id=episode.episode_lineage_id,
            episode_version=episode.episode_version,
            sample_role=SampleRole.NORMAL,
            selected_reason=_selected_reason(episode, stratum=stratum),
            stratum=stratum,
            time_domain="UTC",
        )
        for episode, stratum in selected
    )
    return SelectionResult(
        episode_refs=refs,
        candidate_count=len(episodes),
        excluded_count=sum(reasons.values()) - unselected_count,
        deduplicated_count=duplicate_count,
        exclusion_summary=dict(sorted(reasons.items())),
    )


def _ineligibility_reason(
    episode: SelectionEpisode,
    *,
    warn_confirmed: bool,
    include_ids: tuple[UUID, ...],
    exclude_ids: tuple[UUID, ...],
    scene_filter: tuple[str, ...],
    robot_filter: tuple[UUID, ...],
    device_filter: tuple[UUID, ...],
    sensor_filter: tuple[UUID, ...],
    captured_from: datetime | None,
    captured_to: datetime | None,
    not_annotated: bool,
) -> str | None:
    if episode.status not in _ALLOWED_STATUSES:
        return "episode_status_ineligible"
    if episode.quality_status not in _ALLOWED_QUALITY:
        return "quality_status_ineligible"
    if episode.quality_status == "WARN" and not warn_confirmed:
        return "warn_not_confirmed"
    if episode.manifest_sha256 is None:
        return "manifest_missing"
    if include_ids and episode.episode_id not in include_ids:
        return "not_in_include_list"
    if episode.episode_id in exclude_ids:
        return "excluded_by_request"
    if scene_filter and not set(scene_filter).intersection(episode.scene_labels):
        return "scene_not_matched"
    if robot_filter and episode.robot_id not in robot_filter:
        return "robot_not_matched"
    if device_filter and not set(device_filter).intersection(episode.device_ids):
        return "device_not_matched"
    if sensor_filter and not set(sensor_filter).intersection(episode.sensor_ids):
        return "sensor_not_matched"
    if captured_from is not None and (
        episode.captured_at is None or episode.captured_at < captured_from
    ):
        return "captured_before_range"
    if captured_to is not None and (
        episode.captured_at is None or episode.captured_at > captured_to
    ):
        return "captured_after_range"
    if not_annotated and episode.annotation_status != "NOT_STARTED":
        return "already_annotated"
    return None


def _deduplicate(
    episodes: Sequence[SelectionEpisode],
    dedup_key: DedupKey,
) -> tuple[list[SelectionEpisode], int]:
    if dedup_key is DedupKey.NONE:
        return sorted(episodes, key=_episode_order), 0
    keyed: dict[tuple[UUID, int] | UUID, SelectionEpisode] = {}
    ordered = sorted(episodes, key=_episode_order)
    for episode in ordered:
        key: tuple[UUID, int] | UUID
        if dedup_key is DedupKey.EPISODE_LINEAGE:
            key = episode.episode_lineage_id
        else:
            key = (episode.episode_lineage_id, episode.episode_version)
        existing = keyed.get(key)
        if existing is None:
            keyed[key] = episode
        elif episode.episode_version > existing.episode_version:
            keyed[key] = episode
    result = sorted(keyed.values(), key=_episode_order)
    return result, max(0, len(episodes) - len(result))


def _apply_strata(
    episodes: Sequence[SelectionEpisode],
    *,
    strata: Sequence[Mapping[str, object]],
) -> tuple[list[tuple[SelectionEpisode, str | None]], int]:
    if not strata:
        return [(episode, None) for episode in episodes], 0
    selected_ids: set[UUID] = set()
    selected: list[tuple[SelectionEpisode, str | None]] = []
    for stratum in strata:
        name = _stratum_name(stratum)
        target_count = _target_count(stratum)
        criteria = stratum.get("criteria", {})
        if not isinstance(criteria, Mapping):
            raise DomainValidationError("strata.criteria 必须是对象")
        unknown = set(criteria) - _STRATUM_KEYS
        if unknown:
            raise DomainValidationError("strata.criteria 包含未支持的筛选键")
        taken = 0
        for episode in episodes:
            if taken >= target_count:
                break
            if episode.episode_id in selected_ids:
                continue
            if _matches_criteria(episode, criteria):
                selected.append((episode, name))
                selected_ids.add(episode.episode_id)
                taken += 1
    return selected, max(0, len(episodes) - len(selected))


def _matches_criteria(episode: SelectionEpisode, criteria: Mapping[str, object]) -> bool:
    for key, value in criteria.items():
        if key == "scene_label":
            if not isinstance(value, str) or value not in episode.scene_labels:
                return False
        elif key == "robot_id":
            if str(episode.robot_id) != str(value):
                return False
        elif key == "device_id":
            if str(value) not in {str(item) for item in episode.device_ids}:
                return False
        elif key == "sensor_id":
            if str(value) not in {str(item) for item in episode.sensor_ids}:
                return False
        elif key == "quality_status":
            if episode.quality_status != value:
                return False
    return True


def _selected_reason(episode: SelectionEpisode, *, stratum: str | None) -> str:
    reason = "Episode 状态与质量满足标注门禁"
    if episode.quality_status == "WARN":
        reason += "；WARN 已由 AM 确认"
    if stratum:
        reason += f"；分层 {stratum}"
    return reason


def _stratum_name(stratum: Mapping[str, object]) -> str:
    name = stratum.get("name")
    if not isinstance(name, str) or not name.strip():
        raise DomainValidationError("strata.name 必须是非空字符串")
    return name.strip()


def _target_count(stratum: Mapping[str, object]) -> int:
    value = stratum.get("target_count")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainValidationError("strata.target_count 必须是非负整数")
    return value


def _episode_order(episode: SelectionEpisode) -> tuple[str, int, str]:
    return (str(episode.episode_lineage_id), episode.episode_version, str(episode.episode_id))


def _uuid_filter(value: object) -> tuple[UUID, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise DomainValidationError("筛选条件必须是 UUID 列表")
    result: list[UUID] = []
    for item in value:
        try:
            result.append(item if isinstance(item, UUID) else UUID(str(item)))
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("筛选条件包含非法 UUID") from exc
    return tuple(result)


def _string_filter(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple) or not all(isinstance(item, str) for item in value):
        raise DomainValidationError("筛选条件必须是字符串列表")
    return tuple(value)


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise DomainValidationError("时间筛选必须是 datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


__all__ = ["SelectionEpisode", "SelectionResult", "select_episodes"]
