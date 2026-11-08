"""训练/测试泄漏检查。

硬规则（血缘/内容 Hash/跨划分重复/相似簇）失败即 FAIL，不可豁免；
软规则（场景组/批次组/时间重叠/重复标注）默认 WARN，仅策略允许的软规则可 WAIVED；
检查缺少必要数据时返回 UNKNOWN，调用方必须失败关闭。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from uuid import UUID

from app.domain.datasets.canonical import sha256_value
from app.domain.datasets.enums import (
    HARD_LEAKAGE_RULES,
    LeakageRule,
    LeakageStatus,
)
from app.domain.datasets.errors import LeakageWaiverForbiddenError
from app.domain.datasets.policies import LeakagePolicy, PolicyRef
from app.domain.datasets.validation import (
    aware_datetime,
    optional_aware_datetime,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    sha256_value as ensure_sha,
    text_value,
)
from app.domain.datasets.enums import Split


@dataclass(frozen=True, slots=True)
class LeakageItem:
    episode_id: UUID
    episode_lineage_id: UUID
    manifest_sha256: str
    raw_asset_ids: tuple[UUID, ...]
    raw_asset_hashes: tuple[str, ...]
    split: Split | str
    annotation_submission_id: UUID
    annotation_record_digest: str
    scene_group_id: UUID | None = None
    similarity_group_id: UUID | None = None
    batch_id: UUID | None = None
    start_time_ns: int = 0
    end_time_ns: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "split", self.split if isinstance(self.split, Split) else Split(self.split))
        if not self.raw_asset_ids:
            raise ValueError("raw_asset_ids 不得为空")


@dataclass(frozen=True, slots=True)
class LeakageWaiver:
    rule: LeakageRule
    approved_by: UUID
    reason: str
    approved_at: datetime
    audit_ref: str
    impact: str | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule", self.rule if isinstance(self.rule, LeakageRule) else LeakageRule(self.rule))
        required_uuid(self.approved_by, "approved_by")
        object.__setattr__(self, "reason", text_value(self.reason, "reason", max_length=2000))
        object.__setattr__(self, "approved_at", aware_datetime(self.approved_at, "approved_at"))
        object.__setattr__(self, "audit_ref", text_value(self.audit_ref, "audit_ref", max_length=256))
        object.__setattr__(self, "impact", optional_text(self.impact, "impact", max_length=1000))
        object.__setattr__(self, "expires_at", optional_aware_datetime(self.expires_at, "expires_at"))

    def to_payload(self) -> dict[str, object]:
        return {
            "rule": self.rule.value,
            "approved_by": str(self.approved_by),
            "reason": self.reason,
            "impact": self.impact,
            "approved_at": self.approved_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "audit_ref": self.audit_ref,
        }


@dataclass(frozen=True, slots=True)
class LeakageViolation:
    rule: LeakageRule
    level: str
    split_a: Split
    split_b: Split
    object_refs: tuple[dict[str, str], ...]
    group_key: str | None = None
    evidence: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "rule": self.rule.value,
            "level": self.level,
            "split_a": self.split_a.value,
            "split_b": self.split_b.value,
            "group_key": self.group_key,
            "object_refs": list(self.object_refs),
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class LeakageReport:
    leakage_report_id: UUID
    project_id: UUID
    dataset_version_id: UUID
    policy_ref: PolicyRef
    leakage_status: LeakageStatus | str
    checked_rule_count: int
    violations: tuple[LeakageViolation, ...]
    waivers: tuple[LeakageWaiver, ...]
    checked_at: datetime
    unknown_reason: str | None = None
    source_snapshot_at: datetime | None = None
    status: str = "READY"
    report_key: str | None = None
    report_sha256: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        required_uuid(self.leakage_report_id, "leakage_report_id")
        required_uuid(self.project_id, "project_id")
        required_uuid(self.dataset_version_id, "dataset_version_id")
        if not isinstance(self.policy_ref, PolicyRef):
            raise ValueError("policy_ref 必须是 PolicyRef")
        object.__setattr__(
            self,
            "leakage_status",
            self.leakage_status if isinstance(self.leakage_status, LeakageStatus) else LeakageStatus(self.leakage_status),
        )
        object.__setattr__(self, "checked_rule_count", positive_int(self.checked_rule_count, "checked_rule_count"))
        object.__setattr__(self, "violations", tuple(self.violations))
        object.__setattr__(self, "waivers", tuple(self.waivers))
        object.__setattr__(self, "checked_at", aware_datetime(self.checked_at, "checked_at"))
        object.__setattr__(
            self,
            "unknown_reason",
            optional_text(self.unknown_reason, "unknown_reason", max_length=1000),
        )
        object.__setattr__(
            self,
            "source_snapshot_at",
            optional_aware_datetime(self.source_snapshot_at, "source_snapshot_at"),
        )
        object.__setattr__(self, "status", text_value(self.status, "status", max_length=16))
        object.__setattr__(
            self,
            "report_key",
            optional_text(self.report_key, "report_key", max_length=1024),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        digest = sha256_value(self._payload())
        if self.report_sha256 is not None and ensure_sha(self.report_sha256, "report_sha256") != digest:
            raise ValueError("report_sha256 与泄漏报告内容不一致")
        object.__setattr__(self, "report_sha256", digest)

    @property
    def hard_violation_count(self) -> int:
        return sum(1 for item in self.violations if item.level == "HARD")

    @property
    def soft_violation_count(self) -> int:
        return sum(1 for item in self.violations if item.level == "SOFT")

    def _payload(self) -> dict[str, object]:
        return {
            "leakage_report_id": str(self.leakage_report_id),
            "project_id": str(self.project_id),
            "dataset_version_id": str(self.dataset_version_id),
            "policy_ref": self.policy_ref.to_payload(),
            "leakage_status": self.leakage_status.value,
            "checked_rule_count": self.checked_rule_count,
            "violations": [item.to_payload() for item in self.violations],
            "waivers": [item.to_payload() for item in self.waivers],
            "unknown_reason": self.unknown_reason,
            "checked_at": self.checked_at.isoformat(),
            "source_snapshot_at": (
                self.source_snapshot_at.isoformat() if self.source_snapshot_at else None
            ),
        }

    def to_payload(self) -> dict[str, object]:
        payload = self._payload()
        payload["status"] = self.status
        payload["violation_count"] = len(self.violations)
        payload["hard_violation_count"] = self.hard_violation_count
        payload["soft_violation_count"] = self.soft_violation_count
        payload["report_key"] = self.report_key
        payload["report_sha256"] = self.report_sha256
        payload["version"] = self.version
        return payload


def check_leakage(
    items: tuple[LeakageItem, ...],
    policy: LeakagePolicy,
    *,
    leakage_report_id: UUID,
    project_id: UUID,
    dataset_version_id: UUID,
    checked_at: datetime,
    waivers: tuple[LeakageWaiver, ...] = (),
    source_snapshot_at: datetime | None = None,
    report_key: str | None = None,
) -> LeakageReport:
    for waiver in waivers:
        if waiver.rule in HARD_LEAKAGE_RULES:
            raise LeakageWaiverForbiddenError("硬规则不可豁免")
        if waiver.rule not in policy.waiver_allowed_rules:
            raise LeakageWaiverForbiddenError("该软规则未在策略中允许豁免")

    unknown = _precheck(items, policy)
    if unknown is not None:
        return LeakageReport(
            leakage_report_id=leakage_report_id,
            project_id=project_id,
            dataset_version_id=dataset_version_id,
            policy_ref=policy.ref,
            leakage_status=LeakageStatus.UNKNOWN,
            checked_rule_count=_checked_rule_count(policy),
            violations=(),
            waivers=waivers,
            checked_at=checked_at,
            unknown_reason=unknown,
            source_snapshot_at=source_snapshot_at,
            report_key=report_key,
        )

    violations: list[LeakageViolation] = []
    for rule in policy.hard_rules:
        violations.extend(_violations_for_rule(items, rule, level="HARD"))
    for rule in policy.soft_rules:
        if not _soft_rule_enabled(rule, policy):
            continue
        violations.extend(_violations_for_rule(items, rule, level="SOFT"))

    hard_failed = any(item.level == "HARD" for item in violations)
    soft_rules = {item.rule for item in violations if item.level == "SOFT"}
    waived_rules = {item.rule for item in waivers}
    if hard_failed:
        status = LeakageStatus.FAIL
    elif soft_rules and soft_rules <= waived_rules:
        status = LeakageStatus.WAIVED
    elif soft_rules:
        status = LeakageStatus.WARN
    else:
        status = LeakageStatus.PASS
    return LeakageReport(
        leakage_report_id=leakage_report_id,
        project_id=project_id,
        dataset_version_id=dataset_version_id,
        policy_ref=policy.ref,
        leakage_status=status,
        checked_rule_count=_checked_rule_count(policy),
        violations=tuple(violations),
        waivers=waivers,
        checked_at=checked_at,
        source_snapshot_at=source_snapshot_at,
        report_key=report_key,
    )


def _precheck(items: tuple[LeakageItem, ...], policy: LeakagePolicy) -> str | None:
    for item in items:
        if not item.episode_lineage_id:
            return "EPISODE_LINEAGE_MISSING"
        if not item.manifest_sha256:
            return "MANIFEST_SHA256_MISSING"
        if not item.raw_asset_hashes:
            return "RAW_ASSET_SHA256_MISSING"
    if LeakageRule.SIMILARITY_GROUP_CROSS_SPLIT in policy.hard_rules:
        if not policy.similarity_grouping_enabled:
            return "SIMILARITY_GROUPING_DISABLED"
        if any(item.similarity_group_id is None for item in items):
            return "SIMILARITY_GROUP_MISSING"
    if policy.scene_grouping_enabled and any(item.scene_group_id is None for item in items):
        return "SCENE_GROUP_MISSING"
    if policy.batch_grouping_enabled and any(item.batch_id is None for item in items):
        return "BATCH_GROUP_MISSING"
    return None


def _checked_rule_count(policy: LeakagePolicy) -> int:
    count = len(policy.hard_rules)
    count += sum(1 for rule in policy.soft_rules if _soft_rule_enabled(rule, policy))
    return max(count, 1)


def _soft_rule_enabled(rule: LeakageRule, policy: LeakagePolicy) -> bool:
    if rule is LeakageRule.SCENE_GROUP_CROSS_SPLIT:
        return policy.scene_grouping_enabled
    if rule is LeakageRule.BATCH_GROUP_CROSS_SPLIT:
        return policy.batch_grouping_enabled
    if rule is LeakageRule.TEMPORAL_OVERLAP_CROSS_SPLIT:
        return policy.temporal_overlap_enabled
    return True


def _violations_for_rule(
    items: tuple[LeakageItem, ...],
    rule: LeakageRule,
    *,
    level: str,
) -> list[LeakageViolation]:
    if rule in {
        LeakageRule.EPISODE_LINEAGE_CROSS_SPLIT,
        LeakageRule.MANIFEST_SHA256_CROSS_SPLIT,
        LeakageRule.RAW_ASSET_SHA256_CROSS_SPLIT,
        LeakageRule.SCENE_GROUP_CROSS_SPLIT,
        LeakageRule.SIMILARITY_GROUP_CROSS_SPLIT,
        LeakageRule.DUPLICATE_ANNOTATION,
    }:
        return _group_violations(items, rule, level=level)
    if rule is LeakageRule.BATCH_GROUP_CROSS_SPLIT:
        return _batch_violations(items, level=level)
    if rule is LeakageRule.TEMPORAL_OVERLAP_CROSS_SPLIT:
        return _temporal_violations(items, level=level)
    return []


def _group_violations(
    items: tuple[LeakageItem, ...],
    rule: LeakageRule,
    *,
    level: str,
) -> list[LeakageViolation]:
    groups: dict[str, list[LeakageItem]] = {}
    for item in items:
        value = _group_value(item, rule)
        if value is None:
            continue
        groups.setdefault(value, []).append(item)
    violations: list[LeakageViolation] = []
    for value, members in groups.items():
        split_pairs = _split_pairs(members)
        for split_a, split_b in split_pairs:
            object_refs = tuple(
                {"object_type": "EPISODE", "object_id": str(member.episode_id)}
                for member in members
                if member.split in {split_a, split_b}
            )
            violations.append(
                LeakageViolation(
                    rule=rule,
                    level=level,
                    split_a=split_a,
                    split_b=split_b,
                    group_key=value,
                    object_refs=object_refs,
                    evidence=f"{rule.value}={value}",
                )
            )
    return violations


def _group_value(item: LeakageItem, rule: LeakageRule) -> str | None:
    if rule is LeakageRule.EPISODE_LINEAGE_CROSS_SPLIT:
        return str(item.episode_lineage_id)
    if rule is LeakageRule.MANIFEST_SHA256_CROSS_SPLIT:
        return item.manifest_sha256
    if rule is LeakageRule.RAW_ASSET_SHA256_CROSS_SPLIT:
        hashes = tuple(sorted(item.raw_asset_hashes))
        return "|".join(hashes) if hashes else None
    if rule is LeakageRule.SCENE_GROUP_CROSS_SPLIT:
        return str(item.scene_group_id) if item.scene_group_id else None
    if rule is LeakageRule.SIMILARITY_GROUP_CROSS_SPLIT:
        return str(item.similarity_group_id) if item.similarity_group_id else None
    if rule is LeakageRule.DUPLICATE_ANNOTATION:
        return f"{item.annotation_submission_id}:{item.annotation_record_digest}"
    return None


def _batch_violations(
    items: tuple[LeakageItem, ...],
    *,
    level: str,
) -> list[LeakageViolation]:
    groups: dict[str, list[LeakageItem]] = {}
    for item in items:
        if item.batch_id is not None:
            groups.setdefault(str(item.batch_id), []).append(item)
    violations: list[LeakageViolation] = []
    for key, members in groups.items():
        for split_a, split_b in _split_pairs(members):
            violations.append(
                LeakageViolation(
                    rule=LeakageRule.BATCH_GROUP_CROSS_SPLIT,
                    level=level,
                    split_a=split_a,
                    split_b=split_b,
                    group_key=key,
                    object_refs=tuple(
                        {"object_type": "EPISODE", "object_id": str(member.episode_id)}
                        for member in members
                    ),
                    evidence=f"batch_id={key}",
                )
            )
    return violations


def _temporal_violations(
    items: tuple[LeakageItem, ...],
    *,
    level: str,
) -> list[LeakageViolation]:
    violations: list[LeakageViolation] = []
    by_asset: dict[UUID, list[LeakageItem]] = {}
    for item in items:
        for asset_id in item.raw_asset_ids:
            by_asset.setdefault(asset_id, []).append(item)
    for asset_id, members in by_asset.items():
        for left, right in combinations(members, 2):
            if left.split is right.split:
                continue
            if left.start_time_ns < right.end_time_ns and right.start_time_ns < left.end_time_ns:
                split_a, split_b = sorted((left.split, right.split), key=lambda item: item.value)
                violations.append(
                    LeakageViolation(
                        rule=LeakageRule.TEMPORAL_OVERLAP_CROSS_SPLIT,
                        level=level,
                        split_a=split_a,
                        split_b=split_b,
                        group_key=str(asset_id),
                        object_refs=(
                            {"object_type": "EPISODE", "object_id": str(left.episode_id)},
                            {"object_type": "EPISODE", "object_id": str(right.episode_id)},
                        ),
                        evidence=(
                            f"raw_asset={asset_id}:"
                            f"[{left.start_time_ns},{left.end_time_ns})"
                            f"[{right.start_time_ns},{right.end_time_ns})"
                        ),
                    )
                )
    return violations


def _split_pairs(members: list[LeakageItem]) -> list[tuple[Split, Split]]:
    splits = sorted({member.split for member in members}, key=lambda item: item.value)
    if len(splits) < 2:
        return []
    return list(combinations(splits, 2))


__all__ = [
    "LeakageItem",
    "LeakageReport",
    "LeakageViolation",
    "LeakageWaiver",
    "check_leakage",
]
