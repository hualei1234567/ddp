"""P0 精确去重：Episode 血缘、Manifest Hash 与 RawAsset 内容 Hash。

去重以确定的保留规则执行，重复组使用稳定 UUID，保证同一输入可复算。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5

from app.domain.datasets.enums import DedupKey, ExclusionCode, RetainRule
from app.domain.datasets.errors import DomainValidationError, PolicyInvalidError
from app.domain.datasets.policies import DedupPolicy
from app.domain.datasets.selection import EpisodeCandidate, ExcludedCandidate

_NAMESPACE = UUID("6f8d4f1e-0f1a-4f0e-9e9f-2f6d1b7c1a31")
_CODE_BY_KEY = {
    DedupKey.EPISODE_LINEAGE: ExclusionCode.DUPLICATE_EPISODE_LINEAGE,
    DedupKey.MANIFEST_SHA256: ExclusionCode.DUPLICATE_MANIFEST_SHA256,
    DedupKey.RAW_ASSET_SHA256: ExclusionCode.DUPLICATE_RAW_ASSET_SHA256,
}


@dataclass(frozen=True, slots=True)
class DedupGroup:
    group_id: UUID
    key_type: DedupKey
    key_value: str
    retained_episode_id: UUID
    excluded_episode_ids: tuple[UUID, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "group_id": str(self.group_id),
            "key_type": self.key_type.value,
            "key_value": self.key_value,
            "retained_episode_id": str(self.retained_episode_id),
            "excluded_episode_ids": [str(item) for item in self.excluded_episode_ids],
        }


@dataclass(frozen=True, slots=True)
class DedupOutcome:
    selected: tuple[EpisodeCandidate, ...]
    excluded: tuple[ExcludedCandidate, ...]
    groups: tuple[DedupGroup, ...]
    duplicated_count: int
    dedup_group_by_episode: dict[UUID, UUID]

    def summary(self) -> dict[str, object]:
        return {
            "exact_group_count": len(self.groups),
            "excluded_duplicate_count": self.duplicated_count,
            "groups": [item.to_payload() for item in self.groups],
        }


def deduplicate(
    candidates: tuple[EpisodeCandidate, ...],
    policy: DedupPolicy,
) -> DedupOutcome:
    if policy.retain_rule is RetainRule.MANUAL:
        raise PolicyInvalidError("MANUAL 去重规则需要逐组人工决定，不能自动构建")
    if not candidates:
        return DedupOutcome((), (), (), 0, {})
    parent = list(range(len(candidates)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    first_by_key: dict[tuple[DedupKey, str], int] = {}
    duplicate_key_by_root: dict[int, tuple[DedupKey, str]] = {}
    for index, candidate in enumerate(candidates):
        for key in policy.exact_keys:
            signature = _signature(candidate, key)
            if signature is None:
                continue
            composite = (key, signature)
            previous = first_by_key.get(composite)
            if previous is None:
                first_by_key[composite] = index
                continue
            union(previous, index)
            root = find(index)
            duplicate_key_by_root.setdefault(root, composite)

    components: dict[int, list[int]] = {}
    for index in range(len(candidates)):
        components.setdefault(find(index), []).append(index)

    selected: list[EpisodeCandidate] = []
    excluded: list[ExcludedCandidate] = []
    groups: list[DedupGroup] = []
    group_by_episode: dict[UUID, UUID] = {}
    for root in sorted(components):
        indices = components[root]
        members = [candidates[index] for index in indices]
        retained = _retain(members, policy.retain_rule)
        if len(members) == 1:
            selected.append(retained)
            continue
        key = duplicate_key_by_root.get(root) or _first_duplicate_key(members, policy)
        group_id = uuid5(
            _NAMESPACE,
            f"dedup:{key[0].value}:{key[1]}:{retained.episode_id}",
        )
        excluded_members = [item for item in members if item.episode_id != retained.episode_id]
        groups.append(
            DedupGroup(
                group_id=group_id,
                key_type=key[0],
                key_value=key[1],
                retained_episode_id=retained.episode_id,
                excluded_episode_ids=tuple(item.episode_id for item in excluded_members),
            )
        )
        group_by_episode[retained.episode_id] = group_id
        selected.append(retained)
        code = _CODE_BY_KEY[key[0]]
        for item in excluded_members:
            group_by_episode[item.episode_id] = group_id
            excluded.append(
                ExcludedCandidate(
                    item.episode_id,
                    code,
                    f"与 {retained.episode_id} 重复，保留规则 {policy.retain_rule.value}",
                    detail=key[1],
                )
            )
    selected.sort(key=lambda item: str(item.episode_id))
    excluded.sort(key=lambda item: str(item.episode_id))
    return DedupOutcome(
        selected=tuple(selected),
        excluded=tuple(excluded),
        groups=tuple(groups),
        duplicated_count=len(excluded),
        dedup_group_by_episode=group_by_episode,
    )


def _signature(candidate: EpisodeCandidate, key: DedupKey) -> str | None:
    if key is DedupKey.EPISODE_LINEAGE:
        return str(candidate.episode_lineage_id)
    if key is DedupKey.MANIFEST_SHA256:
        return candidate.manifest_sha256
    if key is DedupKey.RAW_ASSET_SHA256:
        hashes = tuple(sorted(candidate.raw_asset_hashes))
        return "|".join(hashes) if hashes else None
    raise DomainValidationError("未知去重键")


def _first_duplicate_key(
    members: list[EpisodeCandidate],
    policy: DedupPolicy,
) -> tuple[DedupKey, str]:
    for key in policy.exact_keys:
        seen: dict[str, int] = {}
        for candidate in members:
            signature = _signature(candidate, key)
            if signature is None:
                continue
            seen[signature] = seen.get(signature, 0) + 1
        duplicate = next((item for item, count in seen.items() if count > 1), None)
        if duplicate is not None:
            return key, duplicate
    return policy.exact_keys[0], "unknown"


def _retain(members: list[EpisodeCandidate], rule: RetainRule) -> EpisodeCandidate:
    if rule is RetainRule.NEWEST_ACCEPTED:
        return max(members, key=_newest_key)
    if rule is RetainRule.HIGHEST_QUALITY:
        return max(members, key=_quality_key)
    if rule is RetainRule.EARLIEST:
        return min(members, key=_earliest_key)
    raise PolicyInvalidError("MANUAL 去重规则需要逐组人工决定")


def _newest_key(candidate: EpisodeCandidate) -> tuple[datetime, int, str]:
    return (
        candidate.updated_at or datetime.min.replace(tzinfo=UTC),
        candidate.episode_version,
        str(candidate.episode_id),
    )


def _quality_key(candidate: EpisodeCandidate) -> tuple[int, datetime, int, str]:
    rank = {"PASS": 2, "WARN": 1}.get(candidate.quality_status, 0)
    return (
        rank,
        candidate.updated_at or datetime.min.replace(tzinfo=UTC),
        candidate.episode_version,
        str(candidate.episode_id),
    )


def _earliest_key(candidate: EpisodeCandidate) -> tuple[datetime, str]:
    return (
        candidate.updated_at or datetime.max.replace(tzinfo=UTC),
        str(candidate.episode_id),
    )


__all__ = ["DedupGroup", "DedupOutcome", "deduplicate"]
