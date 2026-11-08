"""数据集策略与报告 SQLAlchemy 仓储。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.datasets.enums import (
    DedupKey,
    EmptySplitPolicy,
    GroupingKey,
    LeakageRule,
    LeakageStatus,
    NearDuplicateEnforcement,
    RetainRule,
    RoundingRule,
    Split,
    SplitStrategy,
    StratifyKey,
)
from app.domain.datasets.errors import DomainValidationError
from app.domain.datasets.leakage import LeakageReport, LeakageViolation, LeakageWaiver
from app.domain.datasets.policies import (
    DedupPolicy,
    LeakagePolicy,
    PolicyRef,
    QualityPolicy,
    SplitPolicy,
)
from app.domain.datasets.quality import QualityReport
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.dataset_policies import (
    DatasetDedupPolicyModel,
    DatasetLeakagePolicyModel,
    DatasetQualityPolicyModel,
    DatasetSplitPolicyModel,
)
from app.infrastructure.db.models.dataset_reports import (
    DatasetLeakageReportModel,
    DatasetQualityReportModel,
)
from app.infrastructure.repositories._dataset_sql import (
    as_utc,
    bounded_limit,
    flush_or_duplicate,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _next_version(
    session: Session,
    model: type,
    scope: ProjectScope,
    dataset_id: UUID,
) -> int:
    current = session.scalar(
        select(func.max(model.version)).where(
            model.project_id == scope.project_id,
            model.dataset_id == dataset_id,
        )
    )
    return int(current or 0) + 1


class SqlDatasetPolicyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- 去重策略 ----
    def add_dedup(self, scope: ProjectScope, policy: DedupPolicy) -> DedupPolicy:
        scope.ensure(policy.project_id)
        row = DatasetDedupPolicyModel(
            id=policy.dedup_policy_id,
            project_id=policy.project_id,
            dataset_id=policy.dataset_id,
            name=policy.name,
            version=policy.version,
            exact_keys=[item.value for item in policy.exact_keys],
            near_duplicate_enabled=policy.near_duplicate_enabled,
            near_duplicate_enforcement=policy.near_duplicate_enforcement.value,
            similarity_method=policy.similarity_method,
            similarity_threshold=policy.similarity_threshold,
            similarity_min_cluster_size=policy.similarity_min_cluster_size,
            grouping_keys=list(policy.grouping_keys),
            retain_rule=policy.retain_rule.value,
            policy_sha256=policy.policy_sha256 or "",
            created_at=policy.created_at or _now(),
            created_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session, "策略版本重复")
        return policy

    def get_dedup(self, scope: ProjectScope, policy_id: UUID) -> DedupPolicy | None:
        row = self._session.scalar(
            select(DatasetDedupPolicyModel).where(
                DatasetDedupPolicyModel.id == policy_id,
                DatasetDedupPolicyModel.project_id == scope.project_id,
            )
        )
        return self._dedup_from_row(row) if row is not None else None

    def get_dedup_version(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        policy_id: UUID,
        version: int,
    ) -> DedupPolicy | None:
        row = self._session.scalar(
            select(DatasetDedupPolicyModel).where(
                DatasetDedupPolicyModel.id == policy_id,
                DatasetDedupPolicyModel.project_id == scope.project_id,
                DatasetDedupPolicyModel.dataset_id == dataset_id,
                DatasetDedupPolicyModel.version == version,
            )
        )
        return self._dedup_from_row(row) if row is not None else None

    def list_dedup(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        limit: int = 20,
    ) -> list[DedupPolicy]:
        limit = bounded_limit(limit, maximum=101)
        rows = self._session.scalars(
            select(DatasetDedupPolicyModel)
            .where(
                DatasetDedupPolicyModel.project_id == scope.project_id,
                DatasetDedupPolicyModel.dataset_id == dataset_id,
            )
            .order_by(DatasetDedupPolicyModel.version.desc(), DatasetDedupPolicyModel.id)
            .limit(limit)
        )
        return [self._dedup_from_row(row) for row in rows]

    def next_dedup_version(self, scope: ProjectScope, dataset_id: UUID) -> int:
        return _next_version(self._session, DatasetDedupPolicyModel, scope, dataset_id)

    # ---- 划分策略 ----
    def add_split(self, scope: ProjectScope, policy: SplitPolicy) -> SplitPolicy:
        scope.ensure(policy.project_id)
        row = DatasetSplitPolicyModel(
            id=policy.split_policy_id,
            project_id=policy.project_id,
            dataset_id=policy.dataset_id,
            name=policy.name,
            version=policy.version,
            strategy=policy.strategy.value,
            train_ratio=policy.train_ratio,
            validation_ratio=policy.validation_ratio,
            test_ratio=policy.test_ratio,
            train_count=policy.train_count,
            validation_count=policy.validation_count,
            test_count=policy.test_count,
            seed=policy.seed,
            stratify_by=[item.value for item in policy.stratify_by],
            group_by=[item.value for item in policy.group_by],
            empty_split_policy=policy.empty_split_policy.value,
            min_item_count=policy.min_item_count,
            min_duration_ns=policy.min_duration_ns,
            rounding_rule=policy.rounding_rule.value,
            policy_sha256=policy.policy_sha256 or "",
            created_at=policy.created_at or _now(),
            created_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session, "策略版本重复")
        return policy

    def get_split(self, scope: ProjectScope, policy_id: UUID) -> SplitPolicy | None:
        row = self._session.scalar(
            select(DatasetSplitPolicyModel).where(
                DatasetSplitPolicyModel.id == policy_id,
                DatasetSplitPolicyModel.project_id == scope.project_id,
            )
        )
        return self._split_from_row(row) if row is not None else None

    def get_split_version(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        policy_id: UUID,
        version: int,
    ) -> SplitPolicy | None:
        row = self._session.scalar(
            select(DatasetSplitPolicyModel).where(
                DatasetSplitPolicyModel.id == policy_id,
                DatasetSplitPolicyModel.project_id == scope.project_id,
                DatasetSplitPolicyModel.dataset_id == dataset_id,
                DatasetSplitPolicyModel.version == version,
            )
        )
        return self._split_from_row(row) if row is not None else None

    def list_split(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        limit: int = 20,
    ) -> list[SplitPolicy]:
        limit = bounded_limit(limit, maximum=101)
        rows = self._session.scalars(
            select(DatasetSplitPolicyModel)
            .where(
                DatasetSplitPolicyModel.project_id == scope.project_id,
                DatasetSplitPolicyModel.dataset_id == dataset_id,
            )
            .order_by(DatasetSplitPolicyModel.version.desc(), DatasetSplitPolicyModel.id)
            .limit(limit)
        )
        return [self._split_from_row(row) for row in rows]

    def next_split_version(self, scope: ProjectScope, dataset_id: UUID) -> int:
        return _next_version(self._session, DatasetSplitPolicyModel, scope, dataset_id)

    # ---- 泄漏策略 ----
    def add_leakage(self, scope: ProjectScope, policy: LeakagePolicy) -> LeakagePolicy:
        scope.ensure(policy.project_id)
        row = DatasetLeakagePolicyModel(
            id=policy.leakage_policy_id,
            project_id=policy.project_id,
            dataset_id=policy.dataset_id,
            name=policy.name,
            version=policy.version,
            hard_rules=[item.value for item in policy.hard_rules],
            soft_rules=[item.value for item in policy.soft_rules],
            waiver_allowed_rules=[item.value for item in policy.waiver_allowed_rules],
            similarity_grouping_enabled=policy.similarity_grouping_enabled,
            scene_grouping_enabled=policy.scene_grouping_enabled,
            batch_grouping_enabled=policy.batch_grouping_enabled,
            temporal_overlap_enabled=policy.temporal_overlap_enabled,
            policy_sha256=policy.policy_sha256 or "",
            created_at=policy.created_at or _now(),
            created_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session, "策略版本重复")
        return policy

    def get_leakage(self, scope: ProjectScope, policy_id: UUID) -> LeakagePolicy | None:
        row = self._session.scalar(
            select(DatasetLeakagePolicyModel).where(
                DatasetLeakagePolicyModel.id == policy_id,
                DatasetLeakagePolicyModel.project_id == scope.project_id,
            )
        )
        return self._leakage_from_row(row) if row is not None else None

    def get_leakage_version(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        policy_id: UUID,
        version: int,
    ) -> LeakagePolicy | None:
        row = self._session.scalar(
            select(DatasetLeakagePolicyModel).where(
                DatasetLeakagePolicyModel.id == policy_id,
                DatasetLeakagePolicyModel.project_id == scope.project_id,
                DatasetLeakagePolicyModel.dataset_id == dataset_id,
                DatasetLeakagePolicyModel.version == version,
            )
        )
        return self._leakage_from_row(row) if row is not None else None

    def list_leakage(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        limit: int = 20,
    ) -> list[LeakagePolicy]:
        limit = bounded_limit(limit, maximum=101)
        rows = self._session.scalars(
            select(DatasetLeakagePolicyModel)
            .where(
                DatasetLeakagePolicyModel.project_id == scope.project_id,
                DatasetLeakagePolicyModel.dataset_id == dataset_id,
            )
            .order_by(DatasetLeakagePolicyModel.version.desc(), DatasetLeakagePolicyModel.id)
            .limit(limit)
        )
        return [self._leakage_from_row(row) for row in rows]

    def next_leakage_version(self, scope: ProjectScope, dataset_id: UUID) -> int:
        return _next_version(self._session, DatasetLeakagePolicyModel, scope, dataset_id)

    # ---- 质量策略 ----
    def add_quality(self, scope: ProjectScope, policy: QualityPolicy) -> QualityPolicy:
        scope.ensure(policy.project_id)
        row = DatasetQualityPolicyModel(
            id=policy.quality_policy_id,
            project_id=policy.project_id,
            dataset_id=policy.dataset_id,
            name=policy.name,
            version=policy.version,
            min_selected_count=policy.min_selected_count,
            allow_warn_quality=policy.allow_warn_quality,
            require_leakage_pass=policy.require_leakage_pass,
            policy_sha256=policy.policy_sha256 or "",
            created_at=policy.created_at or _now(),
            created_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session, "策略版本重复")
        return policy

    def get_quality(self, scope: ProjectScope, policy_id: UUID) -> QualityPolicy | None:
        row = self._session.scalar(
            select(DatasetQualityPolicyModel).where(
                DatasetQualityPolicyModel.id == policy_id,
                DatasetQualityPolicyModel.project_id == scope.project_id,
            )
        )
        return self._quality_from_row(row) if row is not None else None

    def get_quality_version(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        policy_id: UUID,
        version: int,
    ) -> QualityPolicy | None:
        row = self._session.scalar(
            select(DatasetQualityPolicyModel).where(
                DatasetQualityPolicyModel.id == policy_id,
                DatasetQualityPolicyModel.project_id == scope.project_id,
                DatasetQualityPolicyModel.dataset_id == dataset_id,
                DatasetQualityPolicyModel.version == version,
            )
        )
        return self._quality_from_row(row) if row is not None else None

    def list_quality(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        limit: int = 20,
    ) -> list[QualityPolicy]:
        limit = bounded_limit(limit, maximum=101)
        rows = self._session.scalars(
            select(DatasetQualityPolicyModel)
            .where(
                DatasetQualityPolicyModel.project_id == scope.project_id,
                DatasetQualityPolicyModel.dataset_id == dataset_id,
            )
            .order_by(DatasetQualityPolicyModel.version.desc(), DatasetQualityPolicyModel.id)
            .limit(limit)
        )
        return [self._quality_from_row(row) for row in rows]

    def next_quality_version(self, scope: ProjectScope, dataset_id: UUID) -> int:
        return _next_version(self._session, DatasetQualityPolicyModel, scope, dataset_id)

    def _dedup_from_row(self, row: DatasetDedupPolicyModel) -> DedupPolicy:
        return DedupPolicy(
            dedup_policy_id=row.id,
            project_id=row.project_id,
            dataset_id=row.dataset_id,
            name=row.name,
            version=row.version,
            exact_keys=tuple(DedupKey(item) for item in (row.exact_keys or [])),
            near_duplicate_enabled=row.near_duplicate_enabled,
            near_duplicate_enforcement=NearDuplicateEnforcement(row.near_duplicate_enforcement),
            similarity_method=row.similarity_method,
            similarity_threshold=row.similarity_threshold,
            similarity_min_cluster_size=row.similarity_min_cluster_size,
            grouping_keys=tuple(row.grouping_keys or ()),
            retain_rule=RetainRule(row.retain_rule),
            policy_sha256=row.policy_sha256,
            created_at=as_utc(row.created_at),
            created_by=row.created_by,
        )

    def _split_from_row(self, row: DatasetSplitPolicyModel) -> SplitPolicy:
        return SplitPolicy(
            split_policy_id=row.id,
            project_id=row.project_id,
            dataset_id=row.dataset_id,
            name=row.name,
            version=row.version,
            strategy=SplitStrategy(row.strategy),
            train_ratio=float(row.train_ratio),
            validation_ratio=float(row.validation_ratio),
            test_ratio=float(row.test_ratio),
            train_count=row.train_count,
            validation_count=row.validation_count,
            test_count=row.test_count,
            seed=row.seed,
            stratify_by=tuple(StratifyKey(item) for item in (row.stratify_by or [])),
            group_by=tuple(GroupingKey(item) for item in (row.group_by or [])),
            empty_split_policy=EmptySplitPolicy(row.empty_split_policy),
            min_item_count=row.min_item_count,
            min_duration_ns=row.min_duration_ns,
            rounding_rule=RoundingRule(row.rounding_rule),
            policy_sha256=row.policy_sha256,
            created_at=as_utc(row.created_at),
            created_by=row.created_by,
        )

    def _leakage_from_row(self, row: DatasetLeakagePolicyModel) -> LeakagePolicy:
        return LeakagePolicy(
            leakage_policy_id=row.id,
            project_id=row.project_id,
            dataset_id=row.dataset_id,
            name=row.name,
            version=row.version,
            hard_rules=tuple(LeakageRule(item) for item in (row.hard_rules or [])),
            soft_rules=tuple(LeakageRule(item) for item in (row.soft_rules or [])),
            waiver_allowed_rules=tuple(
                LeakageRule(item) for item in (row.waiver_allowed_rules or [])
            ),
            similarity_grouping_enabled=row.similarity_grouping_enabled,
            scene_grouping_enabled=row.scene_grouping_enabled,
            batch_grouping_enabled=row.batch_grouping_enabled,
            temporal_overlap_enabled=row.temporal_overlap_enabled,
            policy_sha256=row.policy_sha256,
            created_at=as_utc(row.created_at),
            created_by=row.created_by,
        )

    def _quality_from_row(self, row: DatasetQualityPolicyModel) -> QualityPolicy:
        return QualityPolicy(
            quality_policy_id=row.id,
            project_id=row.project_id,
            dataset_id=row.dataset_id,
            name=row.name,
            version=row.version,
            min_selected_count=row.min_selected_count,
            allow_warn_quality=row.allow_warn_quality,
            require_leakage_pass=row.require_leakage_pass,
            policy_sha256=row.policy_sha256,
            created_at=as_utc(row.created_at),
            created_by=row.created_by,
        )


class SqlDatasetReportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_leakage(self, scope: ProjectScope, report: LeakageReport) -> LeakageReport:
        scope.ensure(report.project_id)
        self._session.add(
            DatasetLeakageReportModel(
                id=report.leakage_report_id,
                project_id=report.project_id,
                dataset_version_id=report.dataset_version_id,
                status=report.status,
                leakage_status=report.leakage_status.value,
                policy_id=report.policy_ref.policy_id,
                policy_version=report.policy_ref.version,
                policy_sha256=report.policy_ref.policy_sha256,
                checked_rule_count=report.checked_rule_count,
                violation_count=len(report.violations),
                hard_violation_count=report.hard_violation_count,
                soft_violation_count=report.soft_violation_count,
                violations=[item.to_payload() for item in report.violations],
                waivers=[item.to_payload() for item in report.waivers],
                unknown_reason=report.unknown_reason,
                checked_at=report.checked_at,
                source_snapshot_at=report.source_snapshot_at,
                report_key=report.report_key,
                report_sha256=report.report_sha256 or "",
                version=report.version,
            )
        )
        flush_or_duplicate(self._session, "泄漏报告重复")
        return report

    def latest_leakage(
        self,
        scope: ProjectScope,
        dataset_version_id: UUID,
    ) -> LeakageReport | None:
        row = self._session.scalar(
            select(DatasetLeakageReportModel)
            .where(
                DatasetLeakageReportModel.project_id == scope.project_id,
                DatasetLeakageReportModel.dataset_version_id == dataset_version_id,
            )
            .order_by(
                DatasetLeakageReportModel.checked_at.desc(), DatasetLeakageReportModel.id.desc()
            )
            .limit(1)
        )
        return self._leakage_from_row(row) if row is not None else None

    def add_quality(self, scope: ProjectScope, report: QualityReport) -> QualityReport:
        scope.ensure(report.project_id)
        self._session.add(
            DatasetQualityReportModel(
                id=report.quality_report_id,
                project_id=report.project_id,
                dataset_version_id=report.dataset_version_id,
                status=report.status.value,
                quality_policy_id=report.quality_policy_ref.policy_id,
                quality_policy_version=report.quality_policy_ref.version,
                quality_policy_sha256=report.quality_policy_ref.policy_sha256,
                candidate_count=report.candidate_count,
                selected_count=report.selected_count,
                excluded_count=report.excluded_count,
                duplicated_count=report.duplicated_count,
                split_counts=dict(report.split_counts),
                distributions=dict(report.distributions),
                total_duration_ns=report.total_duration_ns,
                total_bytes=report.total_bytes,
                coverage=dict(report.coverage),
                dedup_summary=dict(report.dedup_summary),
                leakage_summary=dict(report.leakage_summary),
                warnings=list(report.warnings),
                blocking_issues=list(report.blocking_issues),
                source_snapshot_at=report.source_snapshot_at,
                generator_version=report.generator_version,
                report_key=report.report_key,
                report_sha256=report.report_sha256 or "",
                created_at=report.created_at,
                version=report.version,
            )
        )
        flush_or_duplicate(self._session, "质量报告重复")
        return report

    def latest_quality(
        self,
        scope: ProjectScope,
        dataset_version_id: UUID,
    ) -> QualityReport | None:
        row = self._session.scalar(
            select(DatasetQualityReportModel)
            .where(
                DatasetQualityReportModel.project_id == scope.project_id,
                DatasetQualityReportModel.dataset_version_id == dataset_version_id,
            )
            .order_by(
                DatasetQualityReportModel.created_at.desc(), DatasetQualityReportModel.id.desc()
            )
            .limit(1)
        )
        return self._quality_from_row(row) if row is not None else None

    def _leakage_from_row(self, row: DatasetLeakageReportModel) -> LeakageReport:
        violations = tuple(
            LeakageViolation(
                rule=LeakageRule(item["rule"]),
                level=str(item["level"]),
                split_a=Split(item["split_a"]),
                split_b=Split(item["split_b"]),
                group_key=item.get("group_key"),  # type: ignore[arg-type]
                object_refs=tuple(dict(ref) for ref in item.get("object_refs", [])),
                evidence=item.get("evidence"),  # type: ignore[arg-type]
            )
            for item in (row.violations or [])
        )
        waivers = tuple(
            LeakageWaiver(
                rule=LeakageRule(item["rule"]),
                approved_by=UUID(item["approved_by"]),
                reason=item["reason"],
                impact=item.get("impact"),
                approved_at=_parse_dt(item["approved_at"]),
                expires_at=(_parse_dt(item["expires_at"]) if item.get("expires_at") else None),
                audit_ref=item["audit_ref"],
            )
            for item in (row.waivers or [])
        )
        return LeakageReport(
            leakage_report_id=row.id,
            project_id=row.project_id,
            dataset_version_id=row.dataset_version_id,
            policy_ref=PolicyRef(row.policy_id, row.policy_version, row.policy_sha256),
            leakage_status=LeakageStatus(row.leakage_status),
            checked_rule_count=row.checked_rule_count,
            violations=violations,
            waivers=waivers,
            checked_at=as_utc(row.checked_at),  # type: ignore[arg-type]
            unknown_reason=row.unknown_reason,
            source_snapshot_at=as_utc(row.source_snapshot_at),
            status=row.status,
            report_key=row.report_key,
            report_sha256=row.report_sha256,
            version=row.version,
        )

    def _quality_from_row(self, row: DatasetQualityReportModel) -> QualityReport:
        return QualityReport(
            quality_report_id=row.id,
            project_id=row.project_id,
            dataset_version_id=row.dataset_version_id,
            quality_policy_ref=PolicyRef(
                row.quality_policy_id,
                row.quality_policy_version,
                row.quality_policy_sha256,
            ),
            candidate_count=row.candidate_count,
            selected_count=row.selected_count,
            excluded_count=row.excluded_count,
            duplicated_count=row.duplicated_count,
            split_counts=dict(row.split_counts),
            distributions={key: dict(value) for key, value in (row.distributions or {}).items()},
            total_duration_ns=row.total_duration_ns,
            total_bytes=row.total_bytes,
            coverage=dict(row.coverage),
            dedup_summary=dict(row.dedup_summary),
            leakage_summary=dict(row.leakage_summary),
            warnings=tuple(row.warnings or ()),
            blocking_issues=tuple(row.blocking_issues or ()),
            source_snapshot_at=as_utc(row.source_snapshot_at),  # type: ignore[arg-type]
            created_at=as_utc(row.created_at),  # type: ignore[arg-type]
            generator_version=row.generator_version,
            status=row.status,
            report_key=row.report_key,
            report_sha256=row.report_sha256,
            version=row.version,
        )


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DomainValidationError("报告中时间必须带时区")
    return parsed
